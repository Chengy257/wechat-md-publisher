"""Regression tests for the P0 correctness fixes.

Covers:
- front matter no longer leaking into the draft body (cmd_draft)
- duplicated row-alt class in _convert_headings (copy-paste bug)
- environment variables outside the credential whitelist being visible
- mermaid <img src> resolvable relative to the markdown directory
- cover/body image size limits (10 MB cover, 1 MB body)
- cover failure chain aborting instead of crashing with a traceback
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from wechat_publish.cli import main
from wechat_publish.config import load_env_values
from wechat_publish.html_processor import make_wechat_compatible
from wechat_publish.images import validate_body_image, validate_cover_image

# ── cmd_draft front matter handling ─────────────────────────────

def _write_article(path: Path, front_matter: str, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    if front_matter:
        text += f"---\n{front_matter}\n---\n\n"
    text += body
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def cli_env(monkeypatch):
    monkeypatch.setenv("WECHAT_APPID", "wx_test_appid")
    monkeypatch.setenv("WECHAT_APPSECRET", "test_secret")
    yield


class TestFrontMatterNotInDraftBody:
    def test_yaml_stays_out_of_rendered_body(self, tmp_project: Path, capsys):
        _write_article(
            tmp_project / "input" / "article.md",
            'title: "测试文章"\nauthor: "Cy257"\n',
            "# Hello\n\nParagraph text.\n",
        )
        rc = main(["draft", "--md", "input/article.md", "--dry-run"])
        assert rc == 0

        wechat_html = (tmp_project / "build" / "article.wechat.html").read_text(
            encoding="utf-8"
        )
        assert "title:" not in wechat_html
        assert "author:" not in wechat_html
        assert "测试文章" not in wechat_html
        assert "Hello" in wechat_html

    def test_first_line_kept_when_body_contains_horizontal_rule(
        self, tmp_project: Path
    ):
        _write_article(
            tmp_project / "input" / "article.md",
            "",
            "First line stays.\n\n---\n\nAfter the rule.\n",
        )
        rc = main(
            ["draft", "--md", "input/article.md", "--dry-run", "--title", "T"]
        )
        assert rc == 0

        wechat_html = (tmp_project / "build" / "article.wechat.html").read_text(
            encoding="utf-8"
        )
        assert "First line stays." in wechat_html
        assert "After the rule." in wechat_html

    def test_more_marker_removed(self, tmp_project: Path):
        _write_article(
            tmp_project / "input" / "article.md",
            'title: "T"\n',
            "Intro.\n\n<!--more-->\n\nRest.\n",
        )
        rc = main(["draft", "--md", "input/article.md", "--dry-run"])
        assert rc == 0

        wechat_html = (tmp_project / "build" / "article.wechat.html").read_text(
            encoding="utf-8"
        )
        assert "<!--more-->" not in wechat_html


# ── html_processor table/heading transforms ─────────────────────

class TestTableAndHeadingCompat:
    def test_no_duplicated_row_alt_class(self):
        html = (
            "<table><thead><tr><th>H</th></tr></thead>"
            "<tbody><tr><td>a</td></tr><tr><td>b</td></tr></tbody></table>"
        )
        result = make_wechat_compatible(html)
        assert "row-alt row-alt" not in result
        assert result.count("row-alt") == 1

    def test_h3_converted_to_styled_paragraph(self):
        result = make_wechat_compatible("<h3>Section</h3>")
        assert "<h3>" not in result
        assert 'class="h3-like"' in result
        assert "Section" in result


# ── environment variable merging ────────────────────────────────

class TestEnvValuesMerge:
    def test_non_whitelisted_env_visible(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key-from-shell")
        merged = load_env_values(tmp_path)
        assert merged["GEMINI_API_KEY"] == "key-from-shell"

    def test_custom_env_name_visible(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_KEY", "custom-value")
        merged = load_env_values(tmp_path)
        assert merged["MY_CUSTOM_KEY"] == "custom-value"

    def test_os_environ_overrides_dotenv(self, tmp_path: Path, monkeypatch):
        (tmp_path / ".env").write_text("WECHAT_AUTHOR=dotenv_author\n", encoding="utf-8")
        monkeypatch.setenv("WECHAT_AUTHOR", "shell_author")
        merged = load_env_values(tmp_path)
        assert merged["WECHAT_AUTHOR"] == "shell_author"

    def test_dotenv_only_key_visible(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("AI_API_KEY", raising=False)
        (tmp_path / ".env").write_text("AI_API_KEY=dotenv_key\n", encoding="utf-8")
        merged = load_env_values(tmp_path)
        assert merged["AI_API_KEY"] == "dotenv_key"


# ── mermaid src resolution ──────────────────────────────────────

class TestMermaidSrcRelative:
    @patch("wechat_publish.mermaid.render_mermaid_mmdc")
    def test_src_resolves_relative_to_markdown_dir(
        self, mock_render, tmp_path: Path
    ):
        from wechat_publish.mermaid import _diagram_filename, replace_mermaid_blocks

        build_mermaid = tmp_path / "build" / "mermaid"
        md_dir = tmp_path / "input"
        img_path = build_mermaid / _diagram_filename("graph TD\n  A-->B")
        mock_render.side_effect = lambda src, path: path.write_bytes(b"\x89PNG")

        html = '<pre><code class="language-mermaid">graph TD\n  A--&gt;B</code></pre>'
        result = replace_mermaid_blocks(
            html, build_mermaid, engine="mmdc", src_base_dir=md_dir
        )

        src = result.split('src="')[1].split('"')[0]
        assert (md_dir / src).resolve() == img_path.resolve()


# ── image size limits ───────────────────────────────────────────

class TestImageSizeLimits:
    def test_cover_accepts_up_to_10mb(self, tmp_path: Path):
        cover = tmp_path / "cover.png"
        cover.write_bytes(b"\x89PNG" + b"\x00" * (2 * 1024 * 1024))
        validate_cover_image(cover)  # should not raise

    def test_body_image_rejects_over_1mb(self, tmp_path: Path):
        img = tmp_path / "big.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * (2 * 1024 * 1024))
        with pytest.raises(ValueError, match="too large"):
            validate_body_image(img)

    def test_cover_rejects_over_10mb(self, tmp_path: Path):
        cover = tmp_path / "huge.png"
        cover.write_bytes(b"\x89PNG" + b"\x00" * (11 * 1024 * 1024))
        with pytest.raises(ValueError, match="too large"):
            validate_cover_image(cover)


# ── cover failure chain ─────────────────────────────────────────

class TestCoverFailureChain:
    def test_missing_cover_aborts_cleanly(self, tmp_project: Path, capsys):
        with patch("wechat_publish.cli.get_access_token") as mock_token:
            from wechat_publish.token import AccessToken
            mock_token.return_value = AccessToken(value="t" * 64, expires_at=2**31)
            rc = main(["draft", "--md", "input/article.md"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "Cover image not found" in err

    def test_ai_cover_generation_failure_aborts(
        self, tmp_project: Path, monkeypatch, capsys
    ):
        monkeypatch.setenv("GEMINI_API_KEY", "fake_key")
        with patch("wechat_publish.cli.get_access_token") as mock_token, patch(
            "wechat_publish.ai_cover.generate_cover_image",
            side_effect=RuntimeError("boom"),
        ):
            from wechat_publish.token import AccessToken
            mock_token.return_value = AccessToken(value="t" * 64, expires_at=2**31)
            rc = main(["draft", "--md", "input/article.md", "--ai-cover"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "AI cover generation failed" in err

    def test_ai_cover_without_key_aborts(self, tmp_project: Path, monkeypatch, capsys):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with patch("wechat_publish.cli.get_access_token") as mock_token:
            from wechat_publish.token import AccessToken
            mock_token.return_value = AccessToken(value="t" * 64, expires_at=2**31)
            rc = main(["draft", "--md", "input/article.md", "--ai-cover"])

        assert rc == 1
        err = capsys.readouterr().err
        assert "no API key" in err
