"""Markdown rendering for WeChat-compatible HTML fragments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, TextLexer

from .html_processor import process_article_html


@dataclass(frozen=True)
class RenderedArticle:
    """Rendered article output paths and HTML content."""

    preview_html: str
    wechat_html: str
    preview_path: Path
    wechat_path: Path


def parse_front_matter(markdown_text: str) -> tuple[Mapping[str, object], str]:
    """Split YAML front matter from Markdown body.

    Returns (front_matter_dict, body_text).
    If no front matter is found, returns ({}, full_text).
    """
    text = markdown_text.strip()
    if not text.startswith("---"):
        return {}, markdown_text

    match = re.match(r"^---\s*\n(.*?\n)---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, markdown_text

    yaml_str = match.group(1)
    body = match.group(2)

    try:
        meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        meta = {}

    if not isinstance(meta, dict):
        meta = {}

    return meta, body


def _make_pygments_formatter() -> HtmlFormatter:
    """Create a Pygments HTML formatter with inline styles for WeChat."""
    return HtmlFormatter(
        nowrap=True,
        noclasses=True,
        style="friendly",
    )


def _pygments_highlight(code: str, lang: str, attrs: str) -> str:
    """Highlight code using Pygments with inline color styles.

    The ``attrs`` argument is part of markdown-it's highlight callback
    contract but is intentionally unused here.
    """
    try:
        lexer = get_lexer_by_name(lang or "text")
    except Exception:
        lexer = TextLexer()
    formatter = _make_pygments_formatter()
    return highlight(code, lexer, formatter)


def render_markdown_to_html(markdown_text: str) -> str:
    """Render Markdown to raw HTML with Pygments syntax highlighting."""
    md = (
        MarkdownIt("commonmark", {"html": True, "highlight": _pygments_highlight})
        .enable("table")
        .enable("strikethrough")
    )
    html = md.render(markdown_text)
    return html


def _wrap_preview(body_html: str, title: str = "") -> str:
    """Wrap body HTML in a minimal HTML document for local browser preview."""
    title_tag = f"<title>{_esc(title)}</title>" if title else "<title>Preview</title>"
    heading = f'<h1 class="preview-title">{_esc(title)}</h1>\n' if title else ""
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"{title_tag}\n"
        "<style>\n"
        "body { margin: 0; background: #f3f6f8; "
        "font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif; "
        "font-size: 16px; line-height: 1.75; color: #1f2937; }\n"
        ".preview-page { max-width: 720px; margin: 24px auto; padding: 28px 18px; "
        "background: #fff; box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08); }\n"
        ".preview-title { font-size: 28px; line-height: 1.35; margin: 0 0 22px; "
        "font-weight: 800; color: #111827; }\n"
        "@media (max-width: 760px) { .preview-page { margin: 0; padding: 22px 14px; box-shadow: none; } }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<main class=\"preview-page\">\n"
        f"{heading}"
        f"{body_html}\n"
        "</main>\n"
        "</body>\n"
        "</html>"
    )


def _esc(text: str) -> str:
    """Minimal HTML escape for title."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_article(
    markdown_path: Path,
    theme_css: str = "",
    *,
    build_dir: Path | None = None,
    preview_path: Path | None = None,
    wechat_path: Path | None = None,
) -> RenderedArticle:
    """Render a Markdown file into preview and WeChat outputs."""
    markdown_text = markdown_path.read_text(encoding="utf-8")

    front_matter, body = parse_front_matter(markdown_text)

    # Remove <!--more--> marker
    body = body.replace("<!--more-->", "")

    raw_html = render_markdown_to_html(body)

    # Sanitize and adapt for WeChat always; CSS inlining only when a theme
    # is loaded (an empty stylesheet must never skip the safety pipeline).
    raw_html = process_article_html(raw_html, theme_css)

    # Build output paths
    if build_dir is None:
        build_dir = Path("build")
    build_dir.mkdir(parents=True, exist_ok=True)

    if preview_path is None:
        preview_path = build_dir / "article.preview.html"
    if wechat_path is None:
        wechat_path = build_dir / "article.wechat.html"

    title = str(front_matter.get("title", ""))

    # Preview: wrapped in minimal HTML shell
    preview_html = _wrap_preview(raw_html, title)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(preview_html, encoding="utf-8")

    # WeChat: same processed fragment
    wechat_html = raw_html
    wechat_path.parent.mkdir(parents=True, exist_ok=True)
    wechat_path.write_text(wechat_html, encoding="utf-8")

    return RenderedArticle(
        preview_html=preview_html,
        wechat_html=wechat_html,
        preview_path=preview_path,
        wechat_path=wechat_path,
    )
