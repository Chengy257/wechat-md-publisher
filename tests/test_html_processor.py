"""HTML processor tests: sanitization, CSS inlining, list flattening, footnotes, image discovery."""

from pathlib import Path

from wechat_publish.html_processor import (
    convert_links_to_footnotes,
    discover_images,
    inline_css,
    make_wechat_compatible,
    sanitize_html_fragment,
)

_SAMPLE_CSS = """
.wechat-content { font-size: 16px; color: #333; }
.wechat-content h1 { font-size: 24px; font-weight: 700; }
.wechat-content h2 { font-size: 20px; }
.wechat-content p { margin: 0 0 14px; }
.wechat-content strong { font-weight: 700; color: #14532d; }
.wechat-content code { background: #f3f4f6; padding: 1px 4px; }
.wechat-content pre { background: #f8fafc; padding: 14px; }
.wechat-content pre code { background: transparent; padding: 0; }
.wechat-content table { border-collapse: collapse; }
.wechat-content th { font-weight: 700; }
.wechat-content td { padding: 8px; }
.footnote-ref { color: #1a5c3f; font-size: 0.8em; }
.footnotes { margin-top: 24px; font-size: 13px; }
.footnote-url { color: #576b95; }
.list-item { margin: 0 0 10px; }
.list-marker { color: #1a5c3f; font-weight: 700; }
"""


# ── Sanitization ────────────────────────────────────────────────

class TestSanitizeHtmlFragment:
    def test_removes_script(self):
        html = '<p>Hello</p><script>alert("x")</script>'
        result = sanitize_html_fragment(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "Hello" in result

    def test_removes_iframe(self):
        html = '<p>Text</p><iframe src="evil.com"></iframe>'
        result = sanitize_html_fragment(html)
        assert "<iframe" not in result

    def test_removes_style_tag(self):
        html = '<style>body{color:red}</style><p>Text</p>'
        result = sanitize_html_fragment(html)
        assert "<style>" not in result

    def test_removes_form_elements(self):
        html = '<form><input type="text"><button>Go</button></form><p>Text</p>'
        result = sanitize_html_fragment(html)
        assert "<form" not in result
        assert "<input" not in result
        assert "<button" not in result

    def test_removes_link_tag(self):
        html = '<link rel="stylesheet" href="evil.css"><p>Text</p>'
        result = sanitize_html_fragment(html)
        assert "<link" not in result

    def test_removes_event_handlers(self):
        html = '<p onclick="evil()">Text</p>'
        result = sanitize_html_fragment(html)
        assert "onclick" not in result
        assert "Text" in result

    def test_preserves_content(self):
        html = "<h1>Title</h1><p>Paragraph</p><blockquote>Quote</blockquote>"
        result = sanitize_html_fragment(html)
        assert "<h1>" in result
        assert "<p>" in result
        assert "<blockquote>" in result


# ── CSS inlining ────────────────────────────────────────────────

class TestInlineCss:
    def test_applies_h1_style(self):
        html = "<h1>Title</h1>"
        result = inline_css(html, _SAMPLE_CSS)
        assert "font-size" in result
        assert "24px" in result

    def test_applies_multiple_tags(self):
        html = "<h2>Sub</h2><p>Text</p>"
        result = inline_css(html, _SAMPLE_CSS)
        assert "20px" in result
        assert "margin" in result

    def test_wraps_in_container(self):
        html = "<p>Text</p>"
        result = inline_css(html, _SAMPLE_CSS)
        assert "wechat-content" in result

    def test_no_css_returns_html(self):
        html = "<p>Text</p>"
        result = inline_css(html, "")
        assert "Text" in result

    def test_inline_code_style_does_not_apply_inside_pre(self):
        html = "<pre><code>print('hi')</code></pre><p><code>x=1</code></p>"
        result = inline_css(html, _SAMPLE_CSS)
        # Inline code should have background, pre code should not
        assert "background" in result

    def test_table_styling(self):
        html = "<table><tr><th>H</th></tr><tr><td>D</td></tr></table>"
        result = inline_css(html, _SAMPLE_CSS)
        assert "border-collapse" in result
        assert "font-weight" in result
        assert "padding" in result

    def test_strong_styled(self):
        html = "<p><strong>Bold</strong></p>"
        result = inline_css(html, _SAMPLE_CSS)
        assert "14532d" in result


# ── Image discovery ─────────────────────────────────────────────

class TestDiscoverImages:
    def test_finds_local_image(self):
        html = '<p><img src="images/fig1.png"></p>'
        refs = discover_images(html, Path("/base"))
        assert len(refs) == 1
        assert refs[0].original_src == "images/fig1.png"
        assert refs[0].is_remote is False
        assert refs[0].resolved_path is not None

    def test_finds_remote_image(self):
        html = '<img src="https://cdn.example.com/img.png">'
        refs = discover_images(html, Path("/base"))
        assert len(refs) == 1
        assert refs[0].is_remote is True
        assert refs[0].resolved_path is None

    def test_deduplicates_same_src(self):
        html = '<img src="fig.png"><p>Text</p><img src="fig.png">'
        refs = discover_images(html, Path("/base"))
        assert len(refs) == 1

    def test_multiple_images(self):
        html = '<img src="a.png"><img src="b.jpg">'
        refs = discover_images(html, Path("/base"))
        assert len(refs) == 2

    def test_no_images(self):
        html = "<p>Just text</p>"
        refs = discover_images(html, Path("/base"))
        assert refs == []

    def test_skips_images_in_code_blocks(self):
        html = '<pre><code>&lt;img src="code_img.png"&gt;</code></pre><img src="real.png">'
        refs = discover_images(html, Path("/base"))
        assert len(refs) == 1
        assert refs[0].original_src == "real.png"

    def test_empty_src_skipped(self):
        html = '<img src=""><img src="real.png">'
        refs = discover_images(html, Path("/base"))
        assert len(refs) == 1

    def test_absolute_path_inside_base_is_allowed(self, tmp_path: Path):
        target = tmp_path / "assets" / "fig.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\x89PNG")
        html = f'<img src="{target.as_posix()}">'
        refs = discover_images(html, tmp_path)
        assert len(refs) == 1
        assert refs[0].resolved_path == target.resolve()

    def test_absolute_path_outside_base_is_rejected(self, tmp_path: Path):
        html = '<img src="/etc/secret.png">'
        refs = discover_images(html, tmp_path)
        assert refs[0].resolved_path is None

    def test_relative_path_escaping_base_is_rejected(self, tmp_path: Path):
        html = '<img src="../outside.png">'
        refs = discover_images(html, tmp_path)
        assert refs[0].resolved_path is None

    def test_allowed_roots_include_build_dir(self, tmp_path: Path):
        build_mermaid = tmp_path / "build" / "mermaid" / "mermaid_0.png"
        build_mermaid.parent.mkdir(parents=True)
        build_mermaid.write_bytes(b"\x89PNG")
        html = '<img src="../build/mermaid/mermaid_0.png">'
        refs = discover_images(
            html, tmp_path / "input",
            allowed_roots=[tmp_path / "input", tmp_path / "build"],
        )
        assert refs[0].resolved_path == build_mermaid.resolve()


# ── WeChat compatibility transforms ────────────────────────────

class TestMakeWechatCompatible:
    def test_code_block_uses_explicit_breaks(self):
        html = "<pre><code>line1\n  line2\nline3</code></pre>"
        result = make_wechat_compatible(html)
        assert "<br" in result
        assert "line1" in result

    def test_code_block_transform_is_idempotent(self):
        html = "<pre><code>line1\nline2\nline3</code></pre>"
        once = make_wechat_compatible(html)
        twice = make_wechat_compatible(once)
        assert twice.count("<br") == 2

    def test_unordered_list_is_flattened_for_wechat(self):
        html = "<ul><li><strong>A4 双页排版</strong>：内容自动压缩至刚好 2 页</li></ul>"
        result = make_wechat_compatible(html)
        assert "<ul" not in result
        assert "<li" not in result
        assert "•" in result
        assert "list-item" in result
        assert "list-marker" in result


# ── Link-to-footnote conversion ────────────────────────────────

class TestConvertLinksToFootnotes:
    def test_single_link(self):
        html = '<p>See <a href="https://example.com">this link</a> for details.</p>'
        result = convert_links_to_footnotes(html)
        assert "<a " not in result
        assert "this link" in result
        assert "footnote-ref" in result
        assert "[1]" in result
        assert "https://example.com" in result
        assert "footnotes" in result

    def test_multiple_links_increment_counter(self):
        html = '<p><a href="https://a.com">First</a> and <a href="https://b.com">Second</a></p>'
        result = convert_links_to_footnotes(html)
        assert "[1]" in result
        assert "[2]" in result

    def test_no_links_returns_unchanged(self):
        html = "<p>No links here.</p>"
        result = convert_links_to_footnotes(html)
        assert result == html

    def test_anchor_links_skipped(self):
        html = '<p><a href="#section1">Jump</a> to section.</p>'
        result = convert_links_to_footnotes(html)
        assert '<a href="#section1"' in result

    def test_empty_href_skipped(self):
        html = '<p><a href="">Empty</a> link.</p>'
        result = convert_links_to_footnotes(html)
        assert '<a href=""' in result

    def test_nested_tags_in_link(self):
        html = '<p><a href="https://example.com"><strong>Bold link</strong></a></p>'
        result = convert_links_to_footnotes(html)
        assert "Bold link" in result
        assert "footnote-ref" in result

    def test_idempotent(self):
        html = '<p><a href="https://example.com">Link</a></p>'
        once = convert_links_to_footnotes(html)
        twice = convert_links_to_footnotes(once)
        assert twice.count("[1]") == once.count("[1]")

    def test_uses_css_classes(self):
        html = '<p><a href="https://example.com">Link</a></p>'
        result = convert_links_to_footnotes(html)
        assert 'class="footnote-ref"' in result
        assert 'class="footnotes"' in result
        assert 'class="footnote-url"' in result


class TestCodeBlockWhitespacePreservesMarkup:
    def test_pygments_spans_survive_normalization(self):
        html = (
            '<pre><code class="language-python">'
            '<span style="color: #007020">print</span>'
            '<span style="color: #4070A0">"hi"</span>\n'
            "</code></pre>"
        )
        result = make_wechat_compatible(html)
        assert 'style="color: #007020"' in result
        assert 'style="color: #4070A0"' in result
        assert "<br" in result

    def test_indentation_becomes_nbsp(self):
        html = "<pre><code>line1\n    line2</code></pre>"
        result = make_wechat_compatible(html)
        assert "\u00a0\u00a0\u00a0\u00a0line2" in result
        assert "<br" in result

    def test_mermaid_block_keeps_newlines(self):
        html = '<pre><code class="language-mermaid">graph TD\nA-->B</code></pre>'
        result = make_wechat_compatible(html)
        assert "\n" in result
        assert "<br" not in result
