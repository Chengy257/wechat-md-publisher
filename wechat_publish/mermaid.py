"""Mermaid diagram detection and rendering for WeChat articles."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup, Tag


def detect_mermaid_blocks(html: str) -> list[str]:
    """Extract mermaid source code from rendered HTML.

    Looks for <pre><code class="language-mermaid">...</code></pre> blocks.
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks: list[str] = []
    for code in soup.find_all("code", class_="language-mermaid"):
        blocks.append(code.get_text())
    return blocks


def render_mermaid_mmdc(mermaid_code: str, output_path: Path) -> Path:
    """Render a mermaid diagram to PNG using mmdc CLI.

    Raises FileNotFoundError if mmdc is not installed.
    """
    # Write mermaid source to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mmd", delete=False, encoding="utf-8"
    ) as f:
        f.write(mermaid_code)
        mmd_path = Path(f.name)

    try:
        result = subprocess.run(
            ["mmdc", "-i", str(mmd_path), "-o", str(output_path), "-b", "white"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mmdc failed: {result.stderr}")
        if not output_path.exists():
            raise RuntimeError("mmdc produced no output file")
        return output_path
    except FileNotFoundError:
        raise FileNotFoundError(
            "mmdc (mermaid-cli) is not installed. "
            "Install with: npm install -g @mermaid-js/mermaid-cli"
        )
    finally:
        mmd_path.unlink(missing_ok=True)


def render_mermaid_api(mermaid_code: str, output_path: Path) -> Path:
    """Render a mermaid diagram using the mermaid.ink online API."""
    import base64
    import requests

    encoded = base64.urlsafe_b64encode(mermaid_code.encode("utf-8")).decode("ascii")
    url = f"https://mermaid.ink/img/{encoded}"

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(resp.content)
    return output_path


def replace_mermaid_blocks(
    html: str,
    output_dir: Path,
    engine: str = "mmdc",
) -> str:
    """Replace mermaid code blocks with rendered PNG images.

    Args:
        html: HTML content with potential mermaid blocks.
        output_dir: Directory to save rendered PNG files.
        engine: Rendering engine - "mmdc" or "api".

    Returns:
        HTML with mermaid blocks replaced by <img> tags.
    """
    soup = BeautifulSoup(html, "html.parser")
    render_fn = render_mermaid_mmdc if engine == "mmdc" else render_mermaid_api
    output_dir.mkdir(parents=True, exist_ok=True)

    for index, pre in enumerate(list(soup.find_all("pre"))):
        code = pre.find("code", class_="language-mermaid")
        if code is None:
            continue

        mermaid_src = code.get_text()
        if not mermaid_src.strip():
            continue

        img_path = output_dir / f"mermaid_{index}.png"
        try:
            render_fn(mermaid_src, img_path)
            img_tag = soup.new_tag(
                "img",
                attrs={
                    "src": str(img_path),
                    "alt": f"Mermaid diagram {index + 1}",
                },
            )
            pre.replace_with(img_tag)
        except Exception as e:
            print(f"[WARN] Failed to render mermaid block {index}: {e}")
            # Leave the block as-is

    return str(soup)
