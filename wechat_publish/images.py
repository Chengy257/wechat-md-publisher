"""Image resolution, validation, caching, and upload for WeChat articles."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from .errors import check_wechat_response
from .http import RetryPolicy, json_response, request_with_retry, require_field
from .state import load_json_mapping, save_json_mapping

API_BASE = "https://api.weixin.qq.com"

# Conservative defaults: jpg/png under 1 MB for body images (media/uploadimg);
# permanent material (material/add_material) allows up to 10 MB for covers.
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
_MAX_BODY_IMAGE_BYTES = 1 * 1024 * 1024  # 1 MB
_MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB

# Remote download guards
_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
_DOWNLOAD_CHUNK = 64 * 1024


@dataclass(frozen=True)
class UploadedCover:
    """Permanent material upload result for a cover image."""

    media_id: str
    url: str | None = None


@dataclass(frozen=True)
class UploadedBodyImage:
    """Article body image upload result."""

    url: str


def sha256_file(path: Path) -> str:
    """Return a file sha256 digest for cache keys."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_cover_image(path: Path) -> None:
    """Validate a cover image before permanent material upload."""
    if not path.exists():
        raise FileNotFoundError(f"Cover image not found: {path}")
    if not path.is_file():
        raise ValueError(f"Cover path is not a file: {path}")
    _validate_image(path, _MAX_COVER_BYTES)


def validate_body_image(path: Path) -> None:
    """Validate a body image before media/uploadimg upload."""
    if not path.exists():
        raise FileNotFoundError(f"Body image not found: {path}")
    if not path.is_file():
        raise ValueError(f"Body image path is not a file: {path}")
    _validate_image(path, _MAX_BODY_IMAGE_BYTES)


def _validate_image(path: Path, max_bytes: int) -> None:
    """Common image validation."""
    ext = path.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Image format '{ext}' not allowed. "
            f"Supported: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"Image too large: {size / 1024:.1f} KB (max {max_bytes / 1024:.0f} KB): {path}"
        )


def _resolve_image_to_file(ref_src: str, resolved_path: Path | None, is_remote: bool) -> tuple[Path, bool]:
    """Resolve an image reference to a local file path.

    Downloads remote images to a temp file. Returns (path, is_temp); the
    caller must delete the file when ``is_temp`` is True.
    """
    if not is_remote:
        if resolved_path is None:
            raise FileNotFoundError(
                f"Image path is outside the allowed directories "
                f"(markdown directory and build directory): {ref_src}"
            )
        if resolved_path.exists():
            return resolved_path, False
        raise FileNotFoundError(f"Local image not found: {resolved_path}")

    # Remote image: stream-download with a hard size cap
    resp = request_with_retry(
        "GET", ref_src, operation="download_image", timeout=60, stream=True
    )
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if content_type and not content_type.lower().startswith("image/"):
        raise ValueError(
            f"Remote image URL returned non-image Content-Type "
            f"'{content_type}': {ref_src}"
        )

    suffix = _url_to_suffix(ref_src)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK):
            downloaded += len(chunk)
            if downloaded > _MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"Remote image exceeds {_MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB "
                    f"download limit: {ref_src}"
                )
            tmp.write(chunk)
        tmp.close()
        return Path(tmp.name), True
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise


def _url_to_suffix(url: str) -> str:
    """Extract file extension from URL, default to .png."""
    from urllib.parse import urlparse

    path = urlparse(url).path
    if "." in path.split("/")[-1]:
        ext = "." + path.split("/")[-1].rsplit(".", 1)[1].lower()
        if ext in _ALLOWED_EXTENSIONS:
            return ext
    return ".png"


def _post_multipart(
    operation: str,
    url: str,
    file_field: dict,
) -> dict:
    """POST a multipart file upload to a WeChat endpoint and parse the reply.

    Uses the UPLOAD retry policy: only ConnectTimeout and 5xx are retried;
    an uncertain failure (ReadTimeout / generic ConnectionError) raises
    AmbiguousRequestError so the upload is never blindly replayed.
    """
    resp = request_with_retry(
        "POST", url, operation=operation, files=file_field, timeout=60,
        policy=RetryPolicy.UPLOAD,
    )
    data = json_response(resp, operation)
    check_wechat_response(operation, data)
    return data


def compress_cover(
    path: Path,
    max_bytes: int = _MAX_BODY_IMAGE_BYTES,
    max_width: int = 900,
) -> Path:
    """Re-encode an oversized cover image to shrink it before upload.

    Writes a JPEG copy (quality ladder, aspect ratio kept, capped to
    *max_width*) when the file exceeds *max_bytes*. Requires the optional
    Pillow dependency; returns the original path unchanged when Pillow is
    missing or the image already fits.
    """
    if path.stat().st_size <= max_bytes:
        return path
    try:
        from PIL import Image
    except ImportError:
        print(
            "[WARN] Pillow is not installed; cover left uncompressed "
            "(pip install Pillow, or install this package with the "
            "'cover-compress' extra)."
        )
        return path

    img = Image.open(path)
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize(
            (max_width, max(1, round(img.height * ratio))), Image.LANCZOS
        )
    img = img.convert("RGB")

    out = path.with_name(f"{path.stem}.compressed.jpg")
    for quality in (85, 70, 55, 40):
        img.save(out, "JPEG", quality=quality)
        if out.stat().st_size <= max_bytes:
            break
    print(
        f"[INFO] cover compressed: {path.name} "
        f"({path.stat().st_size / 1024:.0f} KB -> {out.stat().st_size / 1024:.0f} KB) "
        f"-> {out.name}"
    )
    return out


def upload_cover_image(
    access_token: str,
    path: Path,
    cache_path: Path | None = None,
) -> UploadedCover:
    """Upload a cover image through material/add_material.

    Uses cover cache (by sha256) to avoid duplicate uploads.
    """
    validate_cover_image(path)
    file_hash = sha256_file(path)

    # Check cache
    if cache_path is not None:
        cache = load_json_mapping(cache_path)
        cached_entry = cache.get(file_hash)
        if isinstance(cached_entry, dict) and "media_id" in cached_entry:
            print(f"[INFO] cover cache hit: {path.name} -> media_id={cached_entry['media_id']}")
            return UploadedCover(
                media_id=cached_entry["media_id"],
                url=cached_entry.get("url"),
            )

    url = f"{API_BASE}/cgi-bin/material/add_material?access_token={access_token}&type=image"
    with open(path, "rb") as f:
        data = _post_multipart("upload_cover_image", url, {"media": f})

    result = UploadedCover(
        media_id=require_field(data, "media_id", "upload_cover_image"),
        url=data.get("url"),
    )
    print(f"[INFO] uploaded cover: {path.name} -> media_id={result.media_id[:6]}...")

    # Update cache
    if cache_path is not None:
        cache = dict(load_json_mapping(cache_path))
        cache[file_hash] = {"media_id": result.media_id, "url": result.url}
        save_json_mapping(cache_path, cache)

    return result


def upload_body_image(
    access_token: str,
    path: Path,
    cache_path: Path | None = None,
) -> UploadedBodyImage:
    """Upload an article body image through media/uploadimg.

    Uses image cache (by sha256) to avoid duplicate uploads.
    """
    validate_body_image(path)
    file_hash = sha256_file(path)

    # Check cache
    if cache_path is not None:
        cache = load_json_mapping(cache_path)
        cached_url = cache.get(file_hash)
        if isinstance(cached_url, str) and cached_url:
            print(f"[INFO] image cache hit: {path.name}")
            return UploadedBodyImage(url=cached_url)

    url = f"{API_BASE}/cgi-bin/media/uploadimg?access_token={access_token}"
    with open(path, "rb") as f:
        data = _post_multipart("upload_body_image", url, {"media": f})

    result = UploadedBodyImage(url=require_field(data, "url", "upload_body_image"))
    print(f"[INFO] uploaded image: {path.name} -> {result.url[:40]}...")

    # Update cache
    if cache_path is not None:
        cache = dict(load_json_mapping(cache_path))
        cache[file_hash] = result.url
        save_json_mapping(cache_path, cache)

    return result


def process_images(
    access_token: str,
    html: str,
    image_refs: list,  # list[ImageReference]
    base_dir: Path,
    cache_path: Path | None = None,
    allow_missing: bool = False,
) -> str:
    """Upload all body images and replace src in HTML with WeChat URLs.

    By default any failed upload aborts the run (raising RuntimeError) so a
    draft never ships with broken local image paths. With
    ``allow_missing=True`` failures only warn and the original src is kept.

    Returns modified HTML with all image src replaced.
    """
    if not image_refs:
        return html

    soup = BeautifulSoup(html, "html.parser")
    src_map: dict[str, str] = {}  # original_src -> wechat_url
    failures: list[str] = []

    for ref in image_refs:
        tmp_file: Path | None = None
        try:
            local_path, is_temp = _resolve_image_to_file(
                ref.original_src, ref.resolved_path, ref.is_remote
            )
            if is_temp:
                tmp_file = local_path
            result = upload_body_image(access_token, local_path, cache_path)
            src_map[ref.original_src] = result.url
        except Exception as e:
            failures.append(f"{ref.original_src}: {e}")
            if not allow_missing:
                raise RuntimeError(
                    f"Failed to upload image {ref.original_src}: {e}\n"
                    f"Fix the image (or pass --allow-missing-images to skip it)."
                ) from e
            print(f"[WARN] failed to upload image {ref.original_src}: {e}")
        finally:
            if tmp_file is not None:
                tmp_file.unlink(missing_ok=True)

    if failures:
        print(f"[WARN] {len(failures)} image(s) could not be uploaded:")
        for failure in failures:
            print(f"       {failure}")

    # Replace src attributes
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src in src_map:
            img["src"] = src_map[src]

    return str(soup)
