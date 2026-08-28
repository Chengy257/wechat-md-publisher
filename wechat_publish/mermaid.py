"""Mermaid diagram detection and rendering for WeChat articles."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests as http_requests
from bs4 import BeautifulSoup

# Rendered diagram outputs are hard-capped
_MAX_RENDERED_BYTES = 10 * 1024 * 1024


def render_mermaid_mmdc(mermaid_code: str, output_path: Path) -> Path:
    """Render a mermaid diagram to PNG using the mmdc CLI.

    Raises FileNotFoundError if mmdc is not installed.
    """
    mmdc = shutil.which("mmdc")
    if mmdc is None:
        raise FileNotFoundError(
            "mmdc (mermaid-cli) is not installed. "
            "Install with: npm install -g @mermaid-js/mermaid-cli, "
            "or use --mermaid-engine api."
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mmd", delete=False, encoding="utf-8"
    ) as f:
        f.write(mermaid_code)
        mmd_path = Path(f.name)

    # npm installs .cmd shims on Windows; CreateProcess cannot execute them
    # directly, so wrap in cmd.exe when needed.
    if sys.platform == "win32" and mmdc.lower().endswith((".cmd", ".bat")):
        command = ["cmd", "/c", mmdc]
    else:
        command = [mmdc]

    try:
        result = subprocess.run(
            command
            + ["-i", str(mmd_path), "-o", str(output_path), "-b", "white"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mmdc failed: {result.stderr.strip()}")
        if not output_path.exists():
            raise RuntimeError("mmdc produced no output file")
        return output_path
    finally:
        mmd_path.unlink(missing_ok=True)


def render_mermaid_api(mermaid_code: str, output_path: Path) -> Path:
    """Render a mermaid diagram using the mermaid.ink online API.

    Note: the diagram source is sent to a third-party service.
    """
    encoded = (
        base64.urlsafe_b64encode(mermaid_code.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    url = f"https://mermaid.ink/img/{encoded}"

    resp = http_requests.get(url, timeout=30)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise RuntimeError(
            f"mermaid.ink returned non-image content ({content_type or 'unknown'}); "
            f"the diagram source may be invalid"
        )
    if len(resp.content) > _MAX_RENDERED_BYTES:
        raise RuntimeError("mermaid.ink returned an oversized image")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(resp.content)
    return output_path


def _diagram_filename(mermaid_src: str) -> str:
    """Stable, content-addressed filename (identical diagrams reuse the PNG)."""
    digest = hashlib.sha256(mermaid_src.encode("utf-8")).hexdigest()[:12]
    return f"mermaid_{digest}.png"


def replace_mermaid_blocks(
    html: str,
    output_dir: Path,
    engine: str = "mmdc",
    src_base_dir: Path | None = None,
) -> str:
    """Replace mermaid code blocks with rendered PNG images.

    Args:
        html: HTML content with potential mermaid blocks.
        output_dir: Directory to save rendered PNG files.
        engine: Rendering engine - "mmdc" or "api".
        src_base_dir: Directory the generated ``<img src>`` should resolve
            against (typically the markdown file's directory). When omitted,
            the src falls back to the output file's own path.

    Returns:
        HTML with mermaid blocks replaced by <img> tags.
    """
    soup = BeautifulSoup(html, "html.parser")
    render_fn = render_mermaid_mmdc if engine == "mmdc" else render_mermaid_api
    output_dir.mkdir(parents=True, exist_ok=True)

    for pre in list(soup.find_all("pre")):
        code = pre.find("code", class_="language-mermaid")
        if code is None:
            continue

        mermaid_src = code.get_text()
        if not mermaid_src.strip():
            continue

        img_path = output_dir / _diagram_filename(mermaid_src)
        try:
            if not img_path.exists():
                render_fn(mermaid_src, img_path)
            img_tag = soup.new_tag(
                "img",
                attrs={
                    "src": _image_src_for(img_path, src_base_dir),
                    "alt": "Mermaid diagram",
                },
            )
            pre.replace_with(img_tag)
        except Exception as e:
            print(f"[WARN] Failed to render mermaid block: {e}")
            # Leave the block as-is

    return str(soup)


def _image_src_for(img_path: Path, src_base_dir: Path | None) -> str:
    """Return the img src so that ``src_base_dir / src`` resolves to img_path."""
    if src_base_dir is None:
        return str(img_path)
    try:
        return Path(os.path.relpath(img_path, src_base_dir)).as_posix()
    except ValueError:
        # Different drives (Windows): fall back to the absolute path
        return str(img_path)
