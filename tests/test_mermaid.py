"""Mermaid diagram detection and rendering tests."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from wechat_publish.mermaid import detect_mermaid_blocks, replace_mermaid_blocks


class TestDetectMermaidBlocks:
    def test_finds_mermaid_block(self):
        html = '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
        blocks = detect_mermaid_blocks(html)
        assert len(blocks) == 1
        assert "graph TD" in blocks[0]

    def test_multiple_blocks(self):
        html = (
            '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
            '<pre><code class="language-mermaid">sequenceDiagram\n  A-&gt;&gt;B</code></pre>'
        )
        blocks = detect_mermaid_blocks(html)
        assert len(blocks) == 2

    def test_no_mermaid(self):
        html = '<pre><code class="language-python">print("hi")</code></pre>'
        blocks = detect_mermaid_blocks(html)
        assert blocks == []

    def test_empty_html(self):
        assert detect_mermaid_blocks("<p>text</p>") == []


class TestReplaceMermaidBlocks:
    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_replaces_block_with_img(self, mock_render, tmp_path: Path):
        mock_render.return_value = tmp_path / "mermaid_0.png"
        (tmp_path / "mermaid_0.png").write_bytes(b"\x89PNG")

        html = '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
        result = replace_mermaid_blocks(html, tmp_path, engine="mmdc")
        assert "<img" in result
        assert "mermaid_0.png" in result
        assert "<pre>" not in result

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_preserves_non_mermaid_blocks(self, mock_render, tmp_path: Path):
        html = (
            '<pre><code class="language-python">print("hi")</code></pre>'
            '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
        )
        mock_render.return_value = tmp_path / "mermaid_0.png"
        (tmp_path / "mermaid_0.png").write_bytes(b"\x89PNG")

        result = replace_mermaid_blocks(html, tmp_path, engine="mmdc")
        assert 'class="language-python"' in result
        assert "<img" in result

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_render_failure_preserves_block(self, mock_render, tmp_path: Path):
        mock_render.side_effect = RuntimeError("mmdc failed")
        html = '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
        result = replace_mermaid_blocks(html, tmp_path, engine="mmdc")
        assert "<pre>" in result
        assert "language-mermaid" in result

    @patch("wechat_publish.mermaid.render_mermaid_api")
    def test_api_engine(self, mock_render, tmp_path: Path):
        mock_render.return_value = tmp_path / "mermaid_0.png"
        (tmp_path / "mermaid_0.png").write_bytes(b"\x89PNG")

        html = '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
        result = replace_mermaid_blocks(html, tmp_path, engine="api")
        mock_render.assert_called_once()
        assert "<img" in result

    def test_no_mermaid_blocks_returns_unchanged(self, tmp_path: Path):
        html = "<p>Just text</p>"
        result = replace_mermaid_blocks(html, tmp_path)
        assert result == html
