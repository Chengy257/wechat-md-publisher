"""Markdown rendering tests: front matter, HTML output, preview shell."""

from pathlib import Path

from wechat_publish.render import parse_front_matter, render_article, render_markdown_to_html

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
