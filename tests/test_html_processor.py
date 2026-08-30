"""HTML processor tests: sanitization, CSS inlining, list flattening, footnotes, image discovery."""

import re
from pathlib import Path

import wechat_publish.html_processor as html_processor_module
from wechat_publish.html_processor import (
    convert_links_to_footnotes,
    discover_images,
    inline_css,
    make_wechat_compatible,
    process_article_html,
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


class TestTableScroll:
    def _table(self, cols, rows):
        head = "".join(f"<th>列{i}</th>" for i in range(cols))
        body = "".join(
            "<tr>" + "".join(f"<td>r{r}c{c}</td>" for c in range(cols)) + "</tr>"
            for r in range(rows)
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def test_table_wrapped_in_single_scroll_section(self):
        result = make_wechat_compatible(self._table(6, 3))
        assert "<table" in result
        assert result.count('class="table-scroll"') == 1
        wrapper, _, rest = result.partition('<section class="table-scroll">')
        assert "<table" in rest

    def test_scroll_wrapper_idempotent(self):
        html = self._table(3, 2)
        once = make_wechat_compatible(html)
        twice = make_wechat_compatible(once)
        assert twice.count('class="table-scroll"') == 1
        assert 'class="table-scroll"><section class="table-scroll"' not in twice

    def test_cell_alignment_stripped_other_styles_kept(self):
        html = (
            "<table><tr>"
            '<th style="text-align:right;color:red">H</th>'
            '<td style="text-align:center">D</td>'
            '<td style="padding:2px">X</td>'
            "</tr></table>"
        )
        result = make_wechat_compatible(html)
        assert 'style="color:red"' in result
        assert "text-align" not in result
        assert 'style="padding:2px"' in result

    def test_convert_wide_tables_removed(self):
        assert not hasattr(html_processor_module, "_convert_wide_tables")
        assert not hasattr(html_processor_module, "_WIDE_TABLE_MIN_COLUMNS")


class TestCodeBlockDecoration:
    def test_language_block_gets_bar_with_lang(self):
        html = '<pre><code class="language-bash">echo hi</code></pre>'
        result = make_wechat_compatible(html)
        assert 'class="codeblock"' in result
        assert 'class="codeblock-bar"' in result
        assert '<span class="codeblock-lang">bash</span>' in result
        bar_pos = result.find("codeblock-bar")
        pre_pos = result.find("<pre>")
        assert 0 < bar_pos < pre_pos

    def test_bar_contains_three_non_empty_dots(self):
        html = '<pre><code class="language-python">x = 1</code></pre>'
        result = make_wechat_compatible(html)
        # WeChat clears empty nodes: each dot carries a non-breaking space.
        assert '<span class="codeblock-dot dot-red">\u00a0</span>' in result
        assert '<span class="codeblock-dot dot-yellow">\u00a0</span>' in result
        assert '<span class="codeblock-dot dot-green">\u00a0</span>' in result

    def test_bar_ends_with_copy_button(self):
        html = '<pre><code class="language-python">x = 1</code></pre>'
        result = make_wechat_compatible(html)
        assert '<span class="copy-btn">复制代码</span>' in result
        # The copy button comes after the lang label (or the dots) in the bar.
        assert result.find("copy-btn") < result.find("<pre>")

    def test_unmarked_block_is_wrapped_without_lang(self):
        html = "<pre><code>plain code</code></pre>"
        result = make_wechat_compatible(html)
        assert 'class="codeblock"' in result
        assert 'class="codeblock-bar"' in result
        assert "codeblock-lang" not in result
        # The copy button is always present, even without a language label.
        assert '<span class="copy-btn">复制代码</span>' in result

    def test_mermaid_block_not_decorated(self):
        html = '<pre><code class="language-mermaid">graph TD\nA-->B</code></pre>'
        result = make_wechat_compatible(html)
        assert "codeblock" not in result
        assert "\n" in result  # mermaid newlines untouched

    def test_decoration_idempotent(self):
        html = '<pre><code class="language-python">x = 1</code></pre>'
        once = make_wechat_compatible(html)
        twice = make_wechat_compatible(once)
        assert twice.count('class="codeblock"') == 1
        assert twice.count('class="codeblock-bar"') == 1
        assert twice.count('class="copy-btn"') == 1

    def test_unmarked_decoration_idempotent(self):
        html = "<pre><code>plain code</code></pre>"
        once = make_wechat_compatible(html)
        twice = make_wechat_compatible(once)
        assert twice.count('class="codeblock"') == 1
        assert twice.count("codeblock-dot") == 3
        assert twice.count('class="copy-btn"') == 1


# ── CSS inlining of the code block scroll carrier ──────────────


_CODEBLOCK_CSS = """
.wechat-content code { font-family: Menlo, Consolas, monospace; font-size: 14px; padding: 3px 5px; }
.wechat-content pre { background: #f6f8fa; padding: 16px; }
.wechat-content pre code { background: transparent; padding: 0; }
.wechat-content .codeblock { margin: 1.2em 0; border-radius: 8px; }
.wechat-content .codeblock-bar { position: relative; background: #eef1f4; padding: 4px 12px; font-size: 12px; }
.wechat-content .codeblock-dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; }
.wechat-content .dot-red { background-color: #ff5f56; }
.wechat-content .dot-yellow { background-color: #ffbd2e; }
.wechat-content .dot-green { background-color: #27c93f; }
.wechat-content .codeblock-lang { position: absolute; right: 88px; top: 8px; font-size: 12px; }
.wechat-content .copy-btn { position: absolute; right: 12px; top: 5px; padding: 1px 10px; font-size: 12px; line-height: 1.6; border: 1px solid #d0d7de; border-radius: 6px; background: #fff; color: #57606a; }
.wechat-content .codeblock pre { margin: 0; border-radius: 0 0 8px 8px; }
.wechat-content .codeblock pre code {
  display: block;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  white-space: pre;
  background: transparent;
  padding: 0;
  line-height: 1.6;
}
.wechat-content .table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  max-width: 100%;
}
.wechat-content th { white-space: nowrap; }
"""


class TestCodeBlockScrollInlining:
    # premailer compacts inlined declarations ("display:block", not "display: block").
    def test_block_code_inlines_scroll_carrier(self):
        html = (
            '<section class="codeblock">'
            '<section class="codeblock-bar"></section>'
            "<pre><code>x = 1</code></pre>"
            "</section>"
        )
        result = inline_css(html, _CODEBLOCK_CSS)
        assert "display:block" in result
        assert "overflow-x:auto" in result
        assert "white-space:pre" in result
        assert "-webkit-overflow-scrolling:touch" in result

    def test_inline_code_not_affected_by_scroll_rules(self):
        html = "<p>run <code>pip install x</code> first</p>"
        result = inline_css(html, _CODEBLOCK_CSS)
        # The paragraph-level inline code must stay inline: no block/scroll props.
        assert "display:block" not in result
        assert "overflow-x" not in result
        assert "-webkit-overflow-scrolling" not in result

    def test_table_scroll_inlines_touch_scrolling(self):
        html = (
            '<section class="table-scroll">'
            "<table><thead><tr><th>H</th></tr></thead></table>"
            "</section>"
        )
        result = inline_css(html, _CODEBLOCK_CSS)
        assert "overflow-x:auto" in result
        assert "-webkit-overflow-scrolling:touch" in result

    def test_end_to_end_pipeline_inlines_scroll_styles(self):
        raw = (
            "<p>inline <code>x=1</code> code</p>"
            "<pre><code>plain</code></pre>"
            "<pre><code class=\"language-python\">print('hi')</code></pre>"
            "<table><thead><tr><th>H</th></tr></thead></table>"
        )
        result = process_article_html(raw, _CODEBLOCK_CSS)
        # Block code gets the scroll carrier inlined.
        pre_start = result.find("<pre")
        pre_seg = result[pre_start: result.find("</code>", pre_start)]
        assert "display:block" in pre_seg
        assert "overflow-x:auto" in pre_seg
        assert "white-space:pre" in pre_seg
        assert "-webkit-overflow-scrolling:touch" in pre_seg
        # Paragraph inline code stays inline: no block/scroll props.
        p_seg = result[result.find("inline <code"):]
        p_seg = p_seg[: p_seg.find("</code>")]
        assert p_seg.startswith("inline <code")
        assert "display:block" not in p_seg
        assert "overflow-x" not in p_seg
        assert "-webkit-overflow-scrolling" not in p_seg
        # table-scroll wrapper got the touch-scroll style inlined.
        scroll_start = result.find('class="table-scroll"')
        scroll_seg = result[scroll_start: result.find("<table", scroll_start)]
        assert "-webkit-overflow-scrolling:touch" in scroll_seg
        # The copy button is a statically positioned span in the WeChat body.
        copy_start = result.find('class="copy-btn"')
        assert copy_start != -1
        tag_start = result.rfind("<span", 0, copy_start)
        tag_end = result.find(">", copy_start)
        btn_style = re.search(r'style="([^"]*)"', result[tag_start:tag_end])
        assert btn_style is not None
        assert "position:absolute" in btn_style.group(1)
