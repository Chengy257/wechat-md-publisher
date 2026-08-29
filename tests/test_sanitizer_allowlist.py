"""Allowlist-based sanitizer semantics (nh3) for WeChat article HTML."""

from wechat_publish.html_processor import process_article_html, sanitize_html_fragment
from wechat_publish.render import render_markdown_to_html

# ── representative markdown sample survives the allowlist ──────

_MARKDOWN_SAMPLE = """\
# Heading 1

## Heading 2

A paragraph with **bold**, *italic*, ~~struck~~ text.

> A quote block.

```python
print("hello")
```

```mermaid
graph TD
  A-->B
```

1. first
2. second

- bullet

| A | B |
|---|---|
| 1 | 2 |

![figure](fig1.png)

[external](https://example.com/page) and [anchor](#section-1) links.
"""


class TestMarkdownSurvivesAllowlist:
    def test_full_markdown_sample(self):
        raw = render_markdown_to_html(_MARKDOWN_SAMPLE)
        result = sanitize_html_fragment(raw)

        # Headings
        assert "<h1>" in result and "<h2>" in result
        # Inline styles (markdown-it renders ~~struck~~ as <s>)
        assert "<strong>" in result and "<em>" in result and "<s>" in result
        # Quote, code, list
        assert "<blockquote>" in result
        assert "<pre><code" in result
        assert "<ol>" in result and "<li>" in result and "<ul>" in result
        # Table family
        assert "<table>" in result and "<th" in result and "<td" in result
        # Image with relative src preserved (image discovery depends on it)
        assert 'src="fig1.png"' in result
        assert 'alt="figure"' in result
        # Links: http and #anchor both survive
        assert 'href="https://example.com/page"' in result
        assert 'href="#section-1"' in result

    def test_pygments_inline_styles_survive(self):
        raw = render_markdown_to_html("```python\nprint('hi')\n```")
        result = sanitize_html_fragment(raw)
        assert "<span" in result
        assert 'style="color:' in result

    def test_mermaid_code_class_survives(self):
        raw = render_markdown_to_html("```mermaid\ngraph TD\n```\n")
        result = sanitize_html_fragment(raw)
        assert 'class="language-mermaid"' in result

    def test_class_and_style_attributes_globally_allowed(self):
        result = sanitize_html_fragment(
            '<p class="lead" style="color:#333">x</p>'
            '<span class="k" style="font-weight:700">y</span>'
        )
        assert 'class="lead"' in result and "color:#333" in result
        assert 'class="k"' in result and "font-weight" in result

    def test_table_cell_span_attributes_survive(self):
        result = sanitize_html_fragment(
            '<table><tr><th colspan="2">H</th></tr>'
            '<tr><td colspan="1" rowspan="1">D</td></tr></table>'
        )
        assert "colspan" in result
        assert "rowspan" in result

    def test_process_article_pipeline_end_to_end(self):
        raw = render_markdown_to_html(_MARKDOWN_SAMPLE)
        result = process_article_html(raw, "")
        assert 'src="fig1.png"' in result
        assert "list-item" in result  # _flatten_lists product survives
        assert "footnote-ref" in result  # footnote conversion ran
        assert "row-alt" in result  # table striping ran


# ── unsafe / unsupported constructs are removed ────────────────

class TestUnsafeContentRemoved:
    def test_script_tag_and_content_removed(self):
        result = sanitize_html_fragment('<p>ok</p><script>alert("x")</script>')
        assert "<script" not in result
        assert "alert" not in result
        assert "ok" in result

    def test_style_tag_and_content_removed(self):
        result = sanitize_html_fragment('<style>body{color:red}</style><p>ok</p>')
        assert "<style" not in result
        assert "color:red" not in result

    def test_iframe_removed(self):
        result = sanitize_html_fragment('<p>ok</p><iframe src="https://evil.example"></iframe>')
        assert "<iframe" not in result

    def test_object_embed_removed(self):
        result = sanitize_html_fragment(
            '<p>ok</p><object data="x"></object><embed src="y"><video src="z"></video>'
        )
        assert "<object" not in result
        assert "<embed" not in result
        assert "<video" not in result

    def test_event_handler_attributes_removed(self):
        result = sanitize_html_fragment(
            '<p onclick="evil()" onmouseover="evil2()" onerror="evil3()">ok</p>'
        )
        assert "onclick" not in result
        assert "onmouseover" not in result
        assert "onerror" not in result
        assert "ok" in result

    def test_javascript_href_removed(self):
        result = sanitize_html_fragment('<a href="javascript:alert(1)">click</a>')
        assert "javascript:" not in result
        assert "click" in result  # link text is kept

    def test_javascript_img_src_removed(self):
        result = sanitize_html_fragment('<img src="javascript:alert(1)" alt="x">')
        assert "javascript:" not in result
        assert 'alt="x"' in result

    def test_data_uri_img_src_removed(self):
        result = sanitize_html_fragment(
            '<img src="data:text/html;base64,PHNjcmlwdD4=" alt="x">'
        )
        assert "data:" not in result

    def test_unknown_tags_dropped(self):
        result = sanitize_html_fragment('<p>ok</p><custom-tag>inner</custom-tag>')
        assert "<custom-tag" not in result

    def test_form_elements_dropped(self):
        result = sanitize_html_fragment(
            '<form action="/x"><input name="a"><button>go</button></form><p>ok</p>'
        )
        assert "<form" not in result
        assert "<input" not in result
        assert "<button" not in result

    def test_unsupported_css_properties_still_stripped(self):
        result = sanitize_html_fragment(
            '<p style="color:#333; zoom:2; word-break:all">x</p>'
        )
        assert "zoom" not in result
        assert "word-break" not in result
        assert "color:#333" in result or "color: #333" in result
