"""Markdown rendering tests: front matter, HTML output, preview shell."""

import re
from pathlib import Path

import pytest

from wechat_publish import theme_engine
from wechat_publish.config import BUILTIN_THEMES, load_preset_css
from wechat_publish.html_processor import process_article_html
from wechat_publish.render import (
    parse_front_matter,
    pygments_style_for_palette,
    pygments_style_for_theme,
    render_article,
    render_markdown_to_html,
)

# ── Front matter parsing ────────────────────────────────────────

class TestParseFrontMatter:
    def test_extracts_yaml_front_matter(self):
        md = '---\ntitle: "Test"\nauthor: Cy257\n---\nBody text.'
        meta, body = parse_front_matter(md)
        assert meta["title"] == "Test"
        assert meta["author"] == "Cy257"
        assert "Body text." in body

    def test_no_front_matter(self):
        md = "Just a paragraph."
        meta, body = parse_front_matter(md)
        assert meta == {}
        assert body == "Just a paragraph."

    def test_empty_front_matter(self):
        md = "---\n---\nBody here."
        meta, body = parse_front_matter(md)
        assert meta == {}
        assert "Body here." in body

    def test_invalid_yaml_returns_empty(self):
        md = "---\n: invalid: [yaml: }}}\n---\nBody."
        meta, body = parse_front_matter(md)
        assert isinstance(meta, dict)
        assert "Body." in body

    def test_front_matter_with_list(self):
        md = '---\ntitle: "Test"\ntags:\n  - Python\n  - CLI\n---\nBody.'
        meta, body = parse_front_matter(md)
        assert meta["title"] == "Test"
        assert meta["tags"] == ["Python", "CLI"]

    def test_chinese_front_matter(self):
        md = '---\ntitle: "中文标题"\nauthor: "程宇"\n---\n中文正文。'
        meta, body = parse_front_matter(md)
        assert meta["title"] == "中文标题"
        assert "中文正文。" in body


# ── Markdown to HTML ────────────────────────────────────────────

class TestRenderMarkdownToHtml:
    def test_heading(self):
        html = render_markdown_to_html("# Hello")
        assert "<h1>" in html
        assert "Hello" in html

    def test_paragraph(self):
        html = render_markdown_to_html("A paragraph.")
        assert "<p>" in html
        assert "A paragraph." in html

    def test_code_block(self):
        html = render_markdown_to_html("```python\nprint('hi')\n```")
        assert "<code" in html
        assert "print" in html

    def test_code_highlight_uses_light_theme_colors(self):
        html = render_markdown_to_html("```bash\ncd project\n```")
        assert "#F8F8F2" not in html

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = render_markdown_to_html(md)
        assert "<table>" in html
        assert "<th" in html or "<td" in html

    def test_blockquote(self):
        html = render_markdown_to_html("> A quote")
        assert "<blockquote>" in html

    def test_image(self):
        html = render_markdown_to_html("![alt](fig.png)")
        assert "<img" in html
        assert 'src="fig.png"' in html

    def test_list(self):
        html = render_markdown_to_html("- item1\n- item2")
        assert "<ul>" in html or "<li>" in html

    def test_more_marker_removed(self):
        """<!--more--> should be handled at render_article level."""
        md = "Before\n\n<!--more-->\n\nAfter"
        html = render_markdown_to_html(md)
        # At the raw render level, the comment may be preserved or stripped;
        # the article-level render_article function handles removal
        assert "Before" in html
        assert "After" in html


# ── Full article render ─────────────────────────────────────────

class TestRenderArticle:
    def test_renders_both_outputs(self, tmp_path: Path):
        md_file = tmp_path / "article.md"
        md_file.write_text(
            '---\ntitle: "Test Article"\n---\n# Hello\n\nParagraph.\n',
            encoding="utf-8",
        )
        result = render_article(md_file, build_dir=tmp_path / "build")
        assert result.preview_path.exists()
        assert result.wechat_path.exists()
        assert "<h1>" in result.wechat_html
        assert "<!doctype" in result.preview_html

    def test_preview_has_html_shell(self, tmp_path: Path):
        md_file = tmp_path / "article.md"
        md_file.write_text("# Title\n\nText.\n", encoding="utf-8")
        result = render_article(md_file, build_dir=tmp_path / "build")
        assert "<html" in result.preview_html
        assert "<head>" in result.preview_html
        assert "</body>" in result.preview_html

    def test_wechat_html_is_fragment(self, tmp_path: Path):
        md_file = tmp_path / "article.md"
        md_file.write_text("# Title\n\nText.\n", encoding="utf-8")
        result = render_article(md_file, build_dir=tmp_path / "build")
        assert "<html" not in result.wechat_html
        assert "<head>" not in result.wechat_html

    def test_preview_shell_includes_codeblock_dots_and_copy_button(self, tmp_path: Path):
        md_file = tmp_path / "article.md"
        md_file.write_text("```python\nprint('hi')\n```\n", encoding="utf-8")
        result = render_article(md_file, build_dir=tmp_path / "build")
        # Body structure matches the WeChat output (mac-style bar + dots).
        assert 'class="codeblock-bar"' in result.preview_html
        assert 'class="codeblock-dot dot-red"' in result.preview_html
        assert '<span class="copy-btn">复制代码</span>' in result.preview_html
        # Preview shell styles the dots, positions the copy button like the
        # themes do (right: 12px), and moves the lang label out of the way.
        assert ".codeblock-bar .codeblock-dot" in result.preview_html
        assert ".copy-btn { position: absolute; right: 12px; top: 5px;" in result.preview_html
        assert ".codeblock-bar .codeblock-lang { position: absolute; right: 88px;" in result.preview_html
        # The copy script binds the existing span instead of skipping it.
        assert "bar.querySelector('.copy-btn')" in result.preview_html
        assert "'已复制'" in result.preview_html
        assert "'复制失败'" in result.preview_html
        assert "'复制代码'" in result.preview_html
        # The WeChat output itself stays JavaScript-free.
        assert "<script" not in result.wechat_html

    def test_more_marker_removed(self, tmp_path: Path):
        md_file = tmp_path / "article.md"
        md_file.write_text("Before\n\n<!--more-->\n\nAfter\n", encoding="utf-8")
        result = render_article(md_file, build_dir=tmp_path / "build")
        assert "more" not in result.wechat_html

    def test_chinese_content(self, tmp_path: Path):
        md_file = tmp_path / "article.md"
        md_file.write_text("# 中文标题\n\n中文正文内容。\n", encoding="utf-8")
        result = render_article(md_file, build_dir=tmp_path / "build")
        assert "中文标题" in result.wechat_html
        assert "中文正文内容" in result.wechat_html


class TestPygmentsStyleSelection:
    def test_dark_code_themes_use_a_dark_palette(self):
        from wechat_publish.render import pygments_style_for_theme

        assert pygments_style_for_theme("tech") == "github-dark"
        assert pygments_style_for_theme("elegant") == "github-dark"
        assert pygments_style_for_theme("lapis") == "github-dark"
        assert pygments_style_for_theme("nb") == "github-dark"
        assert pygments_style_for_theme("default") == "friendly"
        assert pygments_style_for_theme("simple") == "friendly"
        assert pygments_style_for_theme("fancy") == "friendly"
        assert pygments_style_for_theme("filling") == "friendly"
        assert pygments_style_for_theme(None) == "friendly"
        assert pygments_style_for_theme("unknown-theme") == "friendly"

    def test_pygments_style_changes_token_colors(self):
        code = "```python\nif True:\n    print('hi')\n```\n"
        light = render_markdown_to_html(code)
        dark = render_markdown_to_html(code, pygments_style="github-dark")
        assert light != dark
        assert 'style="color:' in dark
        # Both keep the code structure intact
        assert "<pre><code" in light and "<pre><code" in dark


class TestPygmentsStyleForPalette:
    """palette["code_scheme"] drives the pygments style (WU-B refactor)."""

    def test_none_or_empty_palette_yields_light_default(self):
        assert pygments_style_for_palette(None) == "friendly"
        assert pygments_style_for_palette({}) == "friendly"

    def test_code_scheme_mapping(self):
        assert pygments_style_for_palette({"code_scheme": "friendly"}) == "friendly"
        assert (
            pygments_style_for_palette({"code_scheme": "github-dark"})
            == "github-dark"
        )

    def test_invalid_code_scheme_fails_closed(self):
        with pytest.raises(ValueError, match="code_scheme"):
            pygments_style_for_palette({"code_scheme": "monokai"})

    def test_builtin_palettes_reproduce_legacy_dark_set(self):
        # The palette-driven mapping must match the retired
        # render._DARK_CODE_THEMES result for every builtin theme.
        legacy_dark = {"elegant", "lapis", "tech", "nb"}
        for name in sorted(BUILTIN_THEMES):
            palette = theme_engine.load_palette(name)
            expected = "github-dark" if name in legacy_dark else "friendly"
            assert pygments_style_for_palette(palette) == expected, name


# ── Bundled theme smoke: fancy / nb / filling ────────────────────

_THEME_IDENTITY_COLORS = {
    "fancy": "#0969da",
    "nb": "#5b6cff",
    "filling": "#c0392b",
}

_SMOKED_THEMES = ("fancy", "nb", "filling")


def _bundled_theme_css(theme: str) -> str:
    css = load_preset_css(theme)
    assert css, f"theme preset '{theme}' must render non-empty CSS"
    return css


class TestNewThemeSmoke:
    """fancy / nb / filling must survive the full inline pipeline."""

    @pytest.mark.parametrize("theme", _SMOKED_THEMES)
    def test_theme_registered_and_bundled(self, theme):
        from wechat_publish.config import BUILTIN_THEMES

        assert theme in BUILTIN_THEMES
        _bundled_theme_css(theme)

    @pytest.mark.parametrize("theme", _SMOKED_THEMES)
    def test_theme_inlines_identity_and_structure(self, theme):
        md = (
            "# Heading 1\n\n## Heading 2\n\n> a quote\n\n- item\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "```python\nprint('hi')\n```\n"
        )
        raw = render_markdown_to_html(
            md, pygments_style=pygments_style_for_theme(theme)
        )
        html = process_article_html(raw, _bundled_theme_css(theme))
        # Theme identity color survives inlining.
        assert _THEME_IDENTITY_COLORS[theme] in html
        # Code element carries the wu5 scroll contract verbatim.
        pre_start = html.find("<pre")
        code_seg = html[pre_start: html.find("</code>", pre_start)]
        assert "display:block" in code_seg
        assert "overflow-x:auto" in code_seg
        assert "white-space:pre" in code_seg
        assert "-webkit-overflow-scrolling:touch" in code_seg
        # Table scroll wrapper got the touch-scroll style inlined.
        scroll_start = html.find('class="table-scroll"')
        assert scroll_start != -1
        scroll_seg = html[scroll_start: html.find("<table", scroll_start)]
        assert "-webkit-overflow-scrolling:touch" in scroll_seg
        # Copy button is an absolutely positioned span in the body.
        copy_start = html.find('class="copy-btn"')
        assert copy_start != -1
        tag_start = html.rfind("<span", 0, copy_start)
        tag_end = html.find(">", copy_start)
        btn_style = re.search(r'style="([^"]*)"', html[tag_start:tag_end])
        assert btn_style is not None
        assert "position:absolute" in btn_style.group(1)


# ── New layout smoke (serif / terminal / card / classic) ─────────


class TestNewLayoutSmoke:
    """The four non-default layouts must survive the full inline pipeline."""

    @pytest.mark.parametrize("layout", ["card", "classic", "serif", "terminal"])
    def test_layout_inlines_identity_on_root(self, layout):
        css = theme_engine.render_css(layout, "default")
        md = (
            "# Heading 1\n\n## Heading 2\n\n> a quote\n\n- item\n\n"
            "```python\nprint('hi')\n```\n"
        )
        raw = render_markdown_to_html(md)
        html = process_article_html(raw, css)
        assert "style=" in html, layout
        root_start = html.find('class="wechat-content"')
        assert root_start != -1, layout
        root_tag = html[html.rfind("<section", 0, root_start):html.find(">", root_start)]
        expected_font = {
            "card": None,  # card keeps a neutral sans stack; no identity font
            "classic": "Kaiti",
            "serif": "Georgia",
            "terminal": None,
        }[layout]
        if expected_font:
            assert expected_font.lower() in root_tag.lower(), layout
