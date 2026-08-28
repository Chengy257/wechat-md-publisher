"""Image resolution, validation, caching, and upload for WeChat articles."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests as http_requests

from .errors import check_wechat_response
from .state import load_json_mapping, save_json_mapping

API_BASE = "https://api.weixin.qq.com"

# Conservative defaults: jpg/png under 1 MB for body images (media/uploadimg);
# permanent material (material/add_material) allows up to 10 MB for covers.
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
_MAX_BODY_IMAGE_BYTES = 1 * 1024 * 1024  # 1 MB
_MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB


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


def _resolve_image_to_file(ref_src: str, resolved_path: Path | None, is_remote: bool) -> Path:
    """Resolve an image reference to a local file path.

    Downloads remote images to a temp file.
    """
    if not is_remote and resolved_path is not None:
        if resolved_path.exists():
            return resolved_path
        raise FileNotFoundError(f"Local image not found: {resolved_path}")

    # Remote image: download
    resp = http_requests.get(ref_src, timeout=60)
    resp.raise_for_status()

    suffix = _url_to_suffix(ref_src)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(resp.content)
    tmp.close()
    return Path(tmp.name)


def _url_to_suffix(url: str) -> str:
    """Extract file extension from URL, default to .png."""
    from urllib.parse import urlparse

    path = urlparse(url).path
    if "." in path.split("/")[-1]:
        ext = "." + path.split("/")[-1].rsplit(".", 1)[1].lower()
        if ext in _ALLOWED_EXTENSIONS:
            return ext
    return ".png"


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
        resp = http_requests.post(url, files={"media": f}, timeout=60)
    data = resp.json()
    check_wechat_response("upload_cover_image", data)

    result = UploadedCover(media_id=data["media_id"], url=data.get("url"))
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
        resp = http_requests.post(url, files={"media": f}, timeout=60)
    data = resp.json()
    check_wechat_response("upload_body_image", data)

    result = UploadedBodyImage(url=data["url"])
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
) -> str:
    """Upload all body images and replace src in HTML with WeChat URLs.

    Returns modified HTML with all image src replaced.
    """
    from .html_processor import ImageReference
    from bs4 import BeautifulSoup

    if not image_refs:
        return html

    soup = BeautifulSoup(html, "html.parser")
    src_map: dict[str, str] = {}  # original_src -> wechat_url

    for ref in image_refs:
        try:
            local_path = _resolve_image_to_file(ref.original_src, ref.resolved_path, ref.is_remote)
            result = upload_body_image(access_token, local_path, cache_path)
            src_map[ref.original_src] = result.url
        except Exception as e:
            print(f"[WARN] failed to upload image {ref.original_src}: {e}")
            # Keep original src on failure
            continue

    # Replace src attributes
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src in src_map:
            img["src"] = src_map[src]

    return str(soup)
