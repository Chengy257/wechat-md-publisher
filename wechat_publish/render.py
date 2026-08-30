"""Markdown rendering for WeChat-compatible HTML fragments."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name

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


_DEFAULT_CODE_STYLE = "friendly"

# Themes whose code blocks have a dark background pair with a dark pygments
# palette: tokens rendered with the light "friendly" palette are barely
# readable on dark backgrounds.
_DARK_CODE_THEMES = {"elegant", "lapis", "tech", "nb"}
_DARK_CODE_STYLE = "github-dark"


def pygments_style_for_theme(theme: str | None) -> str:
    """Return the pygments palette matching a bundled theme's code background."""
    if (theme or "").lower() in _DARK_CODE_THEMES:
        return _DARK_CODE_STYLE
    return _DEFAULT_CODE_STYLE


def _make_pygments_formatter(style_name: str = _DEFAULT_CODE_STYLE) -> HtmlFormatter:
    """Create a Pygments HTML formatter with inline styles for WeChat."""
    from pygments.styles import get_style_by_name

    try:
        style = get_style_by_name(style_name)
    except ValueError:
        style = get_style_by_name(_DEFAULT_CODE_STYLE)
    return HtmlFormatter(nowrap=True, noclasses=True, style=style)


def _build_highlight(style_name: str):
    """Build a markdown-it highlight callback bound to one pygments palette."""
    formatter = _make_pygments_formatter(style_name)

    def _pygments_highlight(code: str, lang: str, attrs: str) -> str:
        """Highlight code using Pygments with inline color styles.

        The ``attrs`` argument is part of markdown-it's highlight callback
        contract but is intentionally unused here. The output is compacted
        because pygments markup is verbose: whitespace-only spans are
        dropped, adjacent same-style spans merged, and style declarations
        compacted -- content near the WeChat 20k-char limit needs the
        savings.
        """
        try:
            lexer = get_lexer_by_name(lang or "text")
        except Exception:
            lexer = TextLexer()
        return _compact_highlight_spans(highlight(code, lexer, formatter))

    return _pygments_highlight


_SPAN_RE = re.compile(r'<span style="([^"]*)">([^<]*)</span>')
_ADJACENT_SPAN_RE = re.compile(
    r'<span style="([^"]*)">([^<]*)</span><span style="\1">([^<]*)</span>'
)


def _compact_highlight_spans(html: str) -> str:
    """Minimize pygments span markup while keeping every visible style."""
    html = _SPAN_RE.sub(
        lambda m: m.group(2) if not m.group(2).strip() else
        f'<span style="{m.group(1).replace(": ", ":").lower()}">{m.group(2)}</span>',
        html,
    )
    previous = None
    while previous != html:
        previous = html
        html = _ADJACENT_SPAN_RE.sub(
            lambda m: f'<span style="{m.group(1)}">{m.group(2)}{m.group(3)}</span>',
            html,
        )
    return html


def render_markdown_to_html(
    markdown_text: str, *, pygments_style: str = _DEFAULT_CODE_STYLE
) -> str:
    """Render Markdown to raw HTML with Pygments syntax highlighting."""
    md = (
        MarkdownIt(
            "commonmark", {"html": True, "highlight": _build_highlight(pygments_style)}
        )
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
        ".codeblock-bar { position: relative; }\n"
        # Fallback dot styling so the mac-style dots are visible in the
        # preview even when no theme CSS was inlined; with a theme the
        # inlined styles match these declarations.
        ".codeblock-bar .codeblock-dot { display: inline-block; width: 12px; "
        "height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: middle; "
        "font-size: 12px; line-height: 1; }\n"
        ".codeblock-bar .dot-red { background-color: #ff5f56; }\n"
        ".codeblock-bar .dot-yellow { background-color: #ffbd2e; }\n"
        ".codeblock-bar .dot-green { background-color: #27c93f; }\n"
        # The .codeblock-lang label carries an inlined position (right: 88px)
        # inside the article body; the copy button sits at right: 12px.
        ".codeblock-bar .codeblock-lang { position: absolute; right: 88px; top: 8px; }\n"
        ".copy-btn { position: absolute; right: 12px; top: 5px; "
        "padding: 1px 10px; font-size: 12px; line-height: 1.6; border: 1px solid #d0d7de; "
        "border-radius: 6px; background: #fff; color: #57606a; cursor: pointer; font-family: inherit; }\n"
        ".copy-btn.copied { color: #1a7f37; border-color: #1a7f37; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<main class=\"preview-page\">\n"
        f"{heading}"
        f"{body_html}\n"
        "</main>\n"
        f"{_PREVIEW_COPY_SCRIPT}\n"
        "</body>\n"
        "</html>"
    )


# Local preview only: the WeChat article body stays JavaScript-free, but the
# browser preview can wire a clipboard handler onto each code block's bar.
# The article body already carries a static span.copy-btn (WeChat shows it as
# a styled label), so prefer binding that; only fall back to creating a
# button when the bar has none (e.g. bare HTML pasted into the preview).
_PREVIEW_COPY_SCRIPT = (
    "<script>\n"
    "(function () {\n"
    "  document.querySelectorAll('.codeblock-bar').forEach(function (bar) {\n"
    "    var container = bar.parentElement;\n"
    "    var pre = container ? container.querySelector(':scope > pre') : null;\n"
    "    var code = pre ? pre.querySelector('code') : null;\n"
    "    var btn = bar.querySelector('.copy-btn');\n"
    "    if (!btn) {\n"
    "      btn = document.createElement('button');\n"
    "      btn.type = 'button';\n"
    "      btn.className = 'copy-btn';\n"
    "      btn.textContent = '复制代码';\n"
    "      bar.appendChild(btn);\n"
    "    }\n"
    "    if (!code) { return; }\n"
    "    btn.addEventListener('click', function () {\n"
    "      navigator.clipboard.writeText(code.innerText).then(function () {\n"
    "        btn.textContent = '已复制';\n"
    "        btn.classList.add('copied');\n"
    "        setTimeout(function () {\n"
    "          btn.textContent = '复制代码';\n"
    "          btn.classList.remove('copied');\n"
    "        }, 1500);\n"
    "      }).catch(function () {\n"
    "        btn.textContent = '复制失败';\n"
    "        setTimeout(function () { btn.textContent = '复制代码'; }, 1500);\n"
    "      });\n"
    "    });\n"
    "  });\n"
    "})();\n"
    "</script>"
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
    pygments_style: str = _DEFAULT_CODE_STYLE,
) -> RenderedArticle:
    """Render a Markdown file into preview and WeChat outputs."""
    markdown_text = markdown_path.read_text(encoding="utf-8")

    front_matter, body = parse_front_matter(markdown_text)

    # Remove <!--more--> marker
    body = body.replace("<!--more-->", "")

    raw_html = render_markdown_to_html(body, pygments_style=pygments_style)

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
