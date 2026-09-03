"""HTML cleanup, CSS inlining, and asset discovery for WeChat articles."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import cssutils
import nh3
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from premailer import transform as premailer_transform

cssutils.log.setLevel(logging.CRITICAL)


@dataclass(frozen=True)
class ImageReference:
    """An image reference discovered in rendered HTML."""

    original_src: str
    resolved_path: Path | None
    is_remote: bool


# Allowlist for WeChat article HTML: everything not listed is dropped.
# Covers the full markdown-it output surface (headings, inline styles,
# tables, code blocks with pygments spans, mermaid code fences) plus the
# transform products of this module (figure/figcaption, section, sup/sub).
_ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "s", "del", "sub", "sup",
    "span", "section", "blockquote", "pre", "code", "br", "hr",
    "img", "a", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption",
}

# class/style are allowed on every tag (theme CSS + pygments inline styles
# + the class hooks used by _flatten_lists/_convert_headings/_stripe_tables
# and mermaid.py's `pre > code.language-mermaid` selector).
_BASE_ATTRS = {"class", "style"}
_TAG_ATTRS: dict[str, set[str]] = {
    "img": {"src", "alt", "title"},
    "a": {"href", "title"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
_NH3_ATTRIBUTES = {"*": _BASE_ATTRS, **_TAG_ATTRS}

# Tags whose *content* is dropped along with the tag itself.
_CLEAN_CONTENT_TAGS = {"script", "style"}

_UNSUPPORTED_CSS_PROPS = {"zoom", "word-break", "word-wrap"}


def _strip_unsupported_css(style_str: str) -> str:
    """Remove CSS properties that premailer or WeChat cannot handle."""
    declarations = style_str.split(";")
    kept = []
    for decl in declarations:
        decl = decl.strip()
        if not decl:
            continue
        prop = decl.split(":")[0].strip().lower()
        if prop not in _UNSUPPORTED_CSS_PROPS:
            kept.append(decl)
    return "; ".join(kept)


def process_article_html(html: str, theme_css: str = "") -> str:
    """Run the full WeChat HTML processing pipeline (single entry point).

    sanitize → WeChat compatibility → link footnotes → CSS inline. The
    safety steps always run; CSS inlining is skipped only when no theme
    stylesheet is provided.
    """
    html = sanitize_html_fragment(html)
    html = make_wechat_compatible(html)
    html = convert_links_to_footnotes(html)
    if theme_css:
        html = inline_css(html, theme_css)
    return html


# Text ornaments injected for the classic layout. Real text characters only
# (WeChat has no pseudo-elements/JS); both classes are styled by
# themes/layouts/classic.css and survive the nh3 allowlist (section/span +
# class are whitelisted).
_ORN_DIVIDER_TEXT = "✦ ❖ ✦"
_ORN_END_TEXT = "❦"


def apply_layout_ornaments(html: str, *, layout: str) -> str:
    """Inject layout-specific text decorations into rendered article HTML.

    Must run BEFORE ``process_article_html`` (i.e. before sanitize and CSS
    inlining) so the injected sections are sanitized with the article and
    get the layout stylesheet inlined onto them. Currently only the
    ``classic`` layout carries ornaments:

    - every ``<hr>`` becomes ``<section class="orn-divider">✦ ❖ ✦</section>``
      (a text divider instead of a rule line);
    - a single ``<section class="orn-end">❦</section>`` is appended at the
      very end of the fragment.

    Idempotent: a repeated call on already-decorated HTML is a no-op (no
    ``<hr>`` left to convert, and the end marker is appended only when
    absent). Non-ornamented layouts return the input unchanged.
    """
    if layout != "classic":
        return html

    soup = BeautifulSoup(html, "html.parser")

    for hr in soup.find_all("hr"):
        divider = soup.new_tag("section", attrs={"class": "orn-divider"})
        divider.string = _ORN_DIVIDER_TEXT
        hr.replace_with(divider)

    if soup.find("section", class_="orn-end") is None:
        end = soup.new_tag("section", attrs={"class": "orn-end"})
        end.string = _ORN_END_TEXT
        soup.append(end)

    return str(soup)


def sanitize_html_fragment(html: str) -> str:
    """Remove unsafe/unsupported HTML constructs for normal article authoring.

    Uses an nh3 (ammonia) allowlist: only known-safe tags and attributes
    survive, script/style contents are dropped, event handlers and
    ``javascript:`` URLs are removed, and URL schemes are restricted to
    http/https (relative URLs and #anchors pass through). The surviving
    fragment then goes through a BeautifulSoup pass that keeps the on*
    attribute cleanup as defense in depth and strips CSS properties
    premailer/WeChat cannot handle.

    This hardens the normal authoring pipeline (markdown-it output plus
    trusted raw HTML); it does not claim to make arbitrary untrusted HTML
    safe for every embedding context.
    """
    cleaned = nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        attributes=_NH3_ATTRIBUTES,
        url_schemes={"http", "https"},
    )

    soup = BeautifulSoup(cleaned, "html.parser")
    for tag in soup.find_all(True):
        # Defense in depth: nh3 already drops event handlers.
        attrs_to_remove = [attr for attr in tag.attrs if attr.startswith("on")]
        for attr in attrs_to_remove:
            del tag[attr]

        # Remove unsupported CSS properties from inline styles
        if tag.has_attr("style"):
            cleaned_style = _strip_unsupported_css(tag["style"])
            if cleaned_style:
                tag["style"] = cleaned_style
            else:
                del tag["style"]

    return str(soup)


def make_wechat_compatible(html: str) -> str:
    """Apply compatibility transforms for the WeChat rich-text backend.

    Converts fragile structures (code block whitespace, native lists) into
    explicit inline content that WeChat preserves reliably. Tables are
    wrapped in horizontally scrollable sections (a wide table cannot fit a
    phone-width WeChat article view), every non-mermaid code block gets a
    mac-style bar (decorative dots plus a language label when a language
    marker is present), and per-cell text alignment is normalized to the
    theme's left alignment.
    """
    soup = BeautifulSoup(html, "html.parser")
    _normalize_code_blocks(soup)
    _decorate_code_blocks(soup)
    _flatten_lists(soup)
    _wrap_scrollable_tables(soup)
    _normalize_table_alignment(soup)
    _stripe_tables(soup)
    _convert_headings(soup)
    return str(soup)


def _decorate_code_blocks(soup: BeautifulSoup) -> None:
    """Wrap every non-mermaid ``pre > code`` into a mac-style code block.

    Each ``pre`` that contains a ``code`` child and is not already inside a
    ``.codeblock`` wrapper (idempotency) is wrapped into
    ``section.codeblock`` with a ``section.codeblock-bar`` placed before the
    pre. The bar carries three decorative dots (real ``span`` elements padded
    with a non-breaking space — WeChat has no pseudo-elements and clears
    empty nodes) plus a ``span.codeblock-lang`` label only when the code
    declares a non-mermaid ``language-X`` class, and always ends with a
    ``span.copy-btn`` holding two nested ``span.copy-icon`` squares that
    draw a copy glyph with CSS borders (the WeChat body has no JS, no SVG
    and no pseudo-elements): the span is static in the WeChat body, while
    the local preview wires it up as a real copy button.
    Mermaid blocks are left untouched so the mermaid renderer can re-read
    their newlines verbatim.
    """
    prefix = "language-"
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if code is None:
            continue
        if "language-mermaid" in (code.get("class") or []):
            continue
        parent = pre.parent
        if parent is not None and "codeblock" in (parent.get("class") or []):
            continue
        lang = next(
            (
                cls[len(prefix):]
                for cls in (code.get("class") or [])
                if cls.startswith(prefix)
            ),
            None,
        )
        wrapper = soup.new_tag("section", attrs={"class": "codeblock"})
        bar = soup.new_tag("section", attrs={"class": "codeblock-bar"})
        for dot_class in ("dot-red", "dot-yellow", "dot-green"):
            dot = soup.new_tag("span", attrs={"class": f"codeblock-dot {dot_class}"})
            # WeChat's editor clears empty nodes: pad each dot with a
            # non-breaking space so the span survives the round trip.
            dot.append(NavigableString("\u00a0"))
            bar.append(dot)
        if lang:
            label = soup.new_tag("span", attrs={"class": "codeblock-lang"})
            label.string = lang
            bar.append(label)
        copy_btn = soup.new_tag("span", attrs={"class": "copy-btn"})
        # The copy glyph is two overlapping squares drawn with CSS borders
        # (back square first so the front one paints on top). Like the dots,
        # each span is padded with a non-breaking space so WeChat's editor
        # does not clear it.
        for icon_class in ("copy-icon-back", "copy-icon-front"):
            icon = soup.new_tag("span", attrs={"class": f"copy-icon {icon_class}"})
            icon.append(NavigableString("\u00a0"))
            copy_btn.append(icon)
        bar.append(copy_btn)
        pre.replace_with(wrapper)
        wrapper.append(bar)
        wrapper.append(pre)


def _wrap_scrollable_tables(soup: BeautifulSoup) -> None:
    """Wrap every table in a ``section.table-scroll`` for horizontal scrolling.

    Tables whose parent is already a ``.table-scroll`` section are skipped,
    so repeated invocations never nest wrappers.
    """
    for table in soup.find_all("table"):
        parent = table.parent
        if parent is not None and "table-scroll" in (parent.get("class") or []):
            continue
        wrapper = soup.new_tag("section", attrs={"class": "table-scroll"})
        table.replace_with(wrapper)
        wrapper.append(table)


def _normalize_table_alignment(soup: BeautifulSoup) -> None:
    """Strip inline ``text-align`` from table cells so theme CSS wins.

    markdown-it turns ``|---:|`` alignment columns into per-cell inline
    ``text-align`` declarations, which survive premailer inlining and
    override the theme's left alignment. Other style declarations on the
    same cell are preserved; the style attribute is dropped when empty.
    """
    for cell in soup.find_all(["th", "td"]):
        style = cell.get("style")
        if not style:
            continue
        kept = [
            decl.strip()
            for decl in style.split(";")
            if decl.strip()
            and decl.strip().split(":", 1)[0].strip().lower() != "text-align"
        ]
        if kept:
            cell["style"] = "; ".join(kept)
        else:
            del cell["style"]


def inline_css(html: str, css: str) -> str:
    """Inline CSS rules into HTML element style attributes using premailer.

    Wraps the HTML fragment in a ``.wechat-content`` container, injects the
    CSS as a ``<style>`` block, and lets premailer resolve all selectors.
    """
    # Wrap in container for descendant selectors like .wechat-content h1
    fragment = f'<section class="wechat-content">{html}</section>'

    # premailer expects a full HTML document
    full_doc = (
        "<html><head><style>\n"
        f"{css}\n"
        "</style></head><body>"
        f"{fragment}"
        "</body></html>"
    )

    result = premailer_transform(full_doc, remove_classes=False, strip_important=False)

    # Extract body content
    soup = BeautifulSoup(result, "html.parser")
    body = soup.find("body")
    if body:
        return body.decode_contents()
    return str(soup)


def _normalize_code_blocks(soup: BeautifulSoup) -> None:
    """Convert code block whitespace into WeChat-safe explicit markup.

    Newlines become ``<br>`` and indentation runs become non-breaking
    spaces (WeChat collapses plain whitespace), while nested inline
    markup such as pygments ``<span>`` tokens is preserved. Mermaid
    blocks are left untouched: their newlines are re-read verbatim by
    the mermaid renderer.
    """
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if code is None:
            continue
        if code.find("br") is not None:
            continue
        if "language-mermaid" in (code.get("class") or []):
            continue
        _normalize_code_whitespace(code, soup)


def _normalize_code_whitespace(container: Tag, soup: BeautifulSoup) -> None:
    """Recursively replace text newlines with <br> and indentation runs
    with no-break spaces, preserving nested tags."""
    rebuilt = False
    replacements: list = []
    for child in list(container.contents):
        if isinstance(child, NavigableString):
            text = str(child)
            if "\n" not in text and not text.startswith(" ") and "  " not in text:
                replacements.append(child)
                continue
            rebuilt = True
            for index, segment in enumerate(text.split("\n")):
                if index:
                    replacements.append(soup.new_tag("br"))
                stripped = segment.lstrip(" ")
                indent = len(segment) - len(stripped)
                if indent:
                    replacements.append(NavigableString("\u00a0" * indent))
                if stripped:
                    replacements.append(NavigableString(stripped))
        else:
            _normalize_code_whitespace(child, soup)
            replacements.append(child)
    if rebuilt:
        container.clear()
        for item in replacements:
            container.append(item)


def _stripe_tables(soup: BeautifulSoup) -> None:
    """Add alternating row class to tables (premailer cannot inline nth-child)."""
    for table in soup.find_all("table"):
        for i, tr in enumerate(table.find_all("tr")):
            if tr.find_parent("thead"):
                continue
            if i % 2 == 1:
                existing = tr.get("class", [])
                tr["class"] = existing + ["row-alt"]


def _convert_headings(soup: BeautifulSoup) -> None:
    """Convert h3+ to <p class="h3-like"> for WeChat compatibility.

    WeChat's editor strips styles from <h3> and lower heading tags.
    Converting to styled <p> ensures WeChat preserves the formatting.
    """
    for tag in list(soup.find_all(["h3", "h4", "h5", "h6"])):
        tag.name = "p"
        existing = tag.get("class", [])
        tag["class"] = existing + ["h3-like"]


def _flatten_lists(soup: BeautifulSoup) -> None:
    """Replace native lists with paragraph-like rows for WeChat compatibility.

    WeChat's editor converts ``display:inline-block`` to ``display:block``,
    which breaks list layout. This emits markers as plain-text prefixes
    inside ``<p class="list-item">`` elements with ``<span class="list-marker">``
    — CSS classes are styled by the theme.
    """
    for list_tag in list(soup.find_all(["ul", "ol"])):
        replacement_nodes: list[Tag] = []
        ordered = list_tag.name == "ol"

        li_items = list_tag.find_all("li", recursive=False)

        for index, li in enumerate(li_items, start=1):
            row = soup.new_tag("p", attrs={"class": "list-item"})
            marker_text = f"{index}." if ordered else "•"

            marker = soup.new_tag("span", attrs={"class": "list-marker"})
            marker.string = marker_text
            row.append(marker)
            row.append(NavigableString(" "))

            for child in list(li.contents):
                if isinstance(child, Tag) and child.name == "p":
                    for inner in list(child.contents):
                        row.append(inner.extract() if isinstance(inner, Tag) else inner)
                else:
                    row.append(child.extract() if isinstance(child, Tag) else child)
            replacement_nodes.append(row)

        if replacement_nodes:
            for node in reversed(replacement_nodes):
                list_tag.insert_after(node)
        list_tag.decompose()


def convert_links_to_footnotes(html: str) -> str:
    """Convert hyperlinks to numbered footnotes for WeChat compatibility.

    WeChat strips external hyperlinks. This replaces ``<a>`` tags with the
    link text plus a ``<sup class="footnote-ref">`` reference, and appends a
    ``<section class="footnotes">`` at the end. Styling is handled by CSS.
    """
    soup = BeautifulSoup(html, "html.parser")

    if soup.find("section", class_="footnotes"):
        return html

    footnotes: list[tuple[int, str]] = []
    counter = 0

    for a_tag in list(soup.find_all("a")):
        href = a_tag.get("href", "").strip()
        if not href or href.startswith("#"):
            continue

        counter += 1
        link_text = a_tag.get_text()

        sup = soup.new_tag("sup", attrs={"class": "footnote-ref"})
        sup.string = f"[{counter}]"

        a_tag.replace_with(link_text, " ", sup)
        footnotes.append((counter, href))

    if not footnotes:
        return str(soup)

    section = soup.new_tag("section", attrs={"class": "footnotes"})
    ol = soup.new_tag("ol")
    for _num, url in footnotes:
        li = soup.new_tag("li")
        url_span = soup.new_tag("span", attrs={"class": "footnote-url"})
        url_span.string = url
        li.append(url_span)
        ol.append(li)

    section.append(ol)

    # Append to container if present, otherwise to root
    container = soup.find("section", class_="wechat-content")
    if container:
        container.append(section)
    else:
        soup.append(section)

    return str(soup)


def discover_images(
    html: str,
    base_dir: Path,
    allowed_roots: Sequence[Path] | None = None,
) -> list[ImageReference]:
    """Discover image references from an HTML fragment.

    Returns a list of ImageReference objects with resolved paths.
    Skips images inside <pre> and <code> blocks.

    Local image paths must resolve inside one of *allowed_roots* (by default
    the *base_dir*). A path escaping every root yields a reference with
    ``resolved_path=None`` so the upload step fails loudly instead of
    shipping arbitrary local files into the WeChat material library.
    """
    soup = BeautifulSoup(html, "html.parser")

    code_imgs: set = set()
    for parent_tag in soup.find_all(["pre", "code"]):
        for img in parent_tag.find_all("img"):
            code_imgs.add(id(img))

    roots = [Path(root).resolve() for root in (allowed_roots or [base_dir])]

    refs: list[ImageReference] = []
    seen_srcs: set[str] = set()

    for img_tag in soup.find_all("img"):
        if id(img_tag) in code_imgs:
            continue

        src = img_tag.get("src", "").strip()
        if not src or src in seen_srcs:
            continue
        seen_srcs.add(src)

        is_remote = _is_remote_url(src)
        resolved: Path | None = None

        if not is_remote:
            candidate = Path(src)
            if not candidate.is_absolute():
                candidate = base_dir / src
            candidate = candidate.resolve()
            if any(candidate.is_relative_to(root) for root in roots):
                resolved = candidate
            else:
                print(
                    f"[WARN] image path escapes allowed directories, "
                    f"it will not be uploaded: {src}"
                )

        refs.append(
            ImageReference(
                original_src=src,
                resolved_path=resolved,
                is_remote=is_remote,
            )
        )

    return refs


def _is_remote_url(url: str) -> bool:
    """Check if a URL is a remote (http/https) URL."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https")
