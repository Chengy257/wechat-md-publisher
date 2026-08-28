"""Mermaid rendering and replacement tests."""

from pathlib import Path
from unittest.mock import patch

import pytest

from wechat_publish.mermaid import (
    _diagram_filename,
    render_mermaid_mmdc,
    replace_mermaid_blocks,
)

_MERMAID_HTML = '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
_MERMAID_SRC = "graph TD\n  A-->B"  # get_text() decodes &gt;


class TestReplaceMermaidBlocks:
    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_replaces_block_with_img(self, mock_render, tmp_path: Path):
        out_dir = tmp_path / "build" / "mermaid"
        mock_render.side_effect = lambda src, path: path.write_bytes(b"\x89PNG")

        result = replace_mermaid_blocks(_MERMAID_HTML, out_dir, engine="mmdc")
        assert "<img" in result
        assert "mermaid_" in result
        assert ".png" in result
        assert "<pre>" not in result

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_src_resolves_relative_to_markdown_dir(self, mock_render, tmp_path: Path):
        out_dir = tmp_path / "build" / "mermaid"
        md_dir = tmp_path / "input"
        mock_render.side_effect = lambda src, path: path.write_bytes(b"\x89PNG")

        result = replace_mermaid_blocks(
            _MERMAID_HTML, out_dir, engine="mmdc", src_base_dir=md_dir
        )
        src = result.split('src="')[1].split('"')[0]
        expected = out_dir / _diagram_filename(_MERMAID_SRC)
        assert (md_dir / src).resolve() == expected.resolve()

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_cache_hit_skips_render(self, mock_render, tmp_path: Path):
        out_dir = tmp_path / "build" / "mermaid"
        out_dir.mkdir(parents=True)
        cached = out_dir / _diagram_filename(_MERMAID_SRC)
        cached.write_bytes(b"\x89PNG")

        result = replace_mermaid_blocks(_MERMAID_HTML, out_dir, engine="mmdc")
        mock_render.assert_not_called()
        assert "<img" in result

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_identical_diagrams_share_one_file(self, mock_render, tmp_path: Path):
        out_dir = tmp_path / "build" / "mermaid"
        mock_render.side_effect = lambda src, path: path.write_bytes(b"\x89PNG")

        html = _MERMAID_HTML + _MERMAID_HTML
        result = replace_mermaid_blocks(html, out_dir, engine="mmdc")
        assert result.count("<img") == 2
        assert len(list(out_dir.glob("mermaid_*.png"))) == 1

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_preserves_non_mermaid_blocks(self, mock_render, tmp_path: Path):
        html = (
            '<pre><code class="language-python">print("hi")</code></pre>'
            + _MERMAID_HTML
        )
        mock_render.side_effect = lambda src, path: path.write_bytes(b"\x89PNG")

        result = replace_mermaid_blocks(html, tmp_path, engine="mmdc")
        assert 'class="language-python"' in result
        assert "<img" in result

    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_render_failure_preserves_block(self, mock_render, tmp_path: Path):
        mock_render.side_effect = RuntimeError("mmdc failed")
        result = replace_mermaid_blocks(_MERMAID_HTML, tmp_path, engine="mmdc")
        assert "<pre>" in result
        assert "language-mermaid" in result

    @patch("wechat_publish.mermaid.render_mermaid_api")
    def test_api_engine(self, mock_render, tmp_path: Path):
        mock_render.side_effect = lambda src, path: path.write_bytes(b"\x89PNG")
        result = replace_mermaid_blocks(_MERMAID_HTML, tmp_path, engine="api")
        mock_render.assert_called_once()
        assert "<img" in result

    def test_no_mermaid_blocks_returns_unchanged(self, tmp_path: Path):
        html = "<p>Just text</p>"
        result = replace_mermaid_blocks(html, tmp_path)
        assert result == html


class TestMmdcCommandResolution:
    def test_missing_mmdc_raises_friendly_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("wechat_publish.mermaid.shutil.which", lambda name: None)
        with pytest.raises(FileNotFoundError, match="mermaid-cli"):
            render_mermaid_mmdc("graph TD\nA>B", tmp_path / "out.png")

    def test_windows_cmd_shim_wrapped_in_cmd(self, tmp_path: Path, monkeypatch):
        calls = []

        class FakeCompleted:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_bytes(b"\x89PNG")
            return FakeCompleted()

        monkeypatch.setattr(
            "wechat_publish.mermaid.shutil.which",
            lambda name: "C:\\npm\\mmdc.cmd",
        )
        monkeypatch.setattr(
            "wechat_publish.mermaid.sys.platform", "win32"
        )
        monkeypatch.setattr(
            "wechat_publish.mermaid.subprocess.run", fake_run
        )
        render_mermaid_mmdc("graph TD\nA>B", tmp_path / "out.png")
        assert calls[0][:3] == ["cmd", "/c", "C:\\npm\\mmdc.cmd"]


