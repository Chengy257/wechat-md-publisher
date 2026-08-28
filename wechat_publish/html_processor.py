"""HTML cleanup, CSS inlining, and asset discovery for WeChat articles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from premailer import transform as premailer_transform

import logging

import cssutils
cssutils.log.setLevel(logging.CRITICAL)


@dataclass(frozen=True)
class ImageReference:
    """An image reference discovered in rendered HTML."""

    original_src: str
    resolved_path: Path | None
    is_remote: bool


# Tags to strip entirely from WeChat HTML
_STRIP_TAGS = {"script", "iframe", "style", "link", "form", "input", "button", "meta", "head"}

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


def sanitize_html_fragment(html: str) -> str:
    """Remove unsupported tags and unsafe content from a WeChat HTML fragment."""
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in soup.find_all(True):
        attrs_to_remove = [
            attr for attr in tag.attrs
            if attr.startswith("on") or attr == "onclick"
        ]
        for attr in attrs_to_remove:
            del tag[attr]

        # Remove unsupported CSS properties from inline styles
        if tag.has_attr("style"):
            cleaned = _strip_unsupported_css(tag["style"])
            if cleaned:
                tag["style"] = cleaned
            else:
                del tag["style"]

    return str(soup)


def make_wechat_compatible(html: str) -> str:
    """Apply compatibility transforms for the WeChat rich-text backend.

    Converts fragile structures (code block whitespace, native lists) into
    explicit inline content that WeChat preserves reliably.
    """
    soup = BeautifulSoup(html, "html.parser")
    _normalize_code_blocks(soup)
    _flatten_lists(soup)
    _stripe_tables(soup)
    _convert_headings(soup)
    return str(soup)


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
    """Convert code block newlines/spaces into explicit HTML."""
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if code is None:
            continue
        if code.find("br") is not None:
            continue

        text = code.get_text()
        code.clear()

        lines = text.splitlines()
        for index, line in enumerate(lines):
            if index:
                code.append(soup.new_tag("br"))
            leading_spaces = len(line) - len(line.lstrip(" "))
            if leading_spaces:
                code.append(NavigableString(" " * leading_spaces))
            code.append(NavigableString(line[leading_spaces:]))


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
    """Add alternating row class to tables (premailer cannot inline nth-child)."""
    for table in soup.find_all("table"):
        for i, tr in enumerate(table.find_all("tr")):
            if tr.find_parent("thead"):
                continue
            if i % 2 == 1:
                existing = tr.get("class", [])
                tr["class"] = existing + ["row-alt"]


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
    for num, url in footnotes:
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


def discover_images(html: str, base_dir: Path) -> list[ImageReference]:
    """Discover image references from an HTML fragment.

    Returns a list of ImageReference objects with resolved paths.
    Skips images inside <pre> and <code> blocks.
    """
    soup = BeautifulSoup(html, "html.parser")

    code_imgs: set = set()
    for parent_tag in soup.find_all(["pre", "code"]):
        for img in parent_tag.find_all("img"):
            code_imgs.add(id(img))

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
            if src.startswith("/"):
                resolved = Path(src)
            else:
                resolved = (base_dir / src).resolve()

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
