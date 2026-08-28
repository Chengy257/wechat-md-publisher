"""Tests for phase-2 hardening: retries, validation, autofill, atomic writes."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wechat_publish.cli import _autofill_front_matter, _run_with_token_retry, main
from wechat_publish.draft import DraftArticle, validate_draft_article
from wechat_publish.errors import WeChatAPIError, WeChatErrorDetail
from wechat_publish.http import json_response
from wechat_publish.images import compress_cover, process_images
from wechat_publish.state import PostState, load_json_mapping, save_post_state, save_json_mapping
from wechat_publish.token import AccessToken


# ── draft field validation ──────────────────────────────────────

class TestValidateDraftArticle:
    def _article(self, **overrides):
        fields = dict(
            title="T",
            author="Cy",
            digest="d",
            content="<p>hello</p>",
            thumb_media_id="MEDIA",
        )
        fields.update(overrides)
        return DraftArticle(**fields)

    def test_valid_article_passes(self):
        validate_draft_article(self._article())

    def test_empty_title_rejected(self):
        with pytest.raises(ValueError, match="title must not be empty"):
            validate_draft_article(self._article(title=""))

    def test_title_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            validate_draft_article(self._article(title="字" * 65))

    def test_author_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            validate_draft_article(self._article(author="一二三四五六七八九"))

    def test_digest_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            validate_draft_article(self._article(digest="x" * 121))

    def test_content_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            validate_draft_article(self._article(content="<p>" + "x" * 20_000))

    def test_missing_thumb_media_id_rejected(self):
        with pytest.raises(ValueError, match="thumb_media_id"):
            validate_draft_article(self._article(thumb_media_id=""))


# ── token retry helper ──────────────────────────────────────────

def _api_error(errcode: int) -> WeChatAPIError:
    return WeChatAPIError(
        WeChatErrorDetail(operation="op", errcode=errcode, errmsg="e", hint="h")
    )


class TestRunWithTokenRetry:
    def test_success_passthrough(self):
        token = AccessToken("t" * 64, 2**31)
        assert _run_with_token_retry(token, "a", "s", Path("x"), lambda tv: ("ok", tv)) == (
            "ok",
            "t" * 64,
        )

    @patch("wechat_publish.cli.get_access_token")
    def test_expired_token_refreshes_once(self, mock_refresh):
        mock_refresh.return_value = AccessToken("fresh" * 8, 2**31)
        token = AccessToken("stale" * 8, 2**31)
        calls = []

        def fn(tv):
            calls.append(tv)
            if tv.startswith("stale"):
                raise _api_error(42001)
            return "done"

        result = _run_with_token_retry(token, "a", "s", Path("tok.json"), fn)
        assert result == "done"
        assert calls == ["stale" * 8, "fresh" * 8]
        mock_refresh.assert_called_once()

    @patch("wechat_publish.cli.time.sleep", lambda s: None)
    def test_busy_error_retries_then_succeeds(self):
        token = AccessToken("t" * 64, 2**31)
        attempts = []

        def fn(tv):
            attempts.append(tv)
            if len(attempts) < 3:
                raise _api_error(45009)
            return "ok"

        assert _run_with_token_retry(token, "a", "s", Path("tok.json"), fn) == "ok"
        assert len(attempts) == 3

    def test_fatal_error_raises_immediately(self):
        token = AccessToken("t" * 64, 2**31)
        with pytest.raises(WeChatAPIError) as exc_info:
            _run_with_token_retry(
                token, "a", "s", Path("tok.json"),
                lambda tv: (_ for _ in ()).throw(_api_error(40007)),
            )
        assert exc_info.value.detail.errcode == 40007


# ── JSON response parsing ───────────────────────────────────────

class TestJsonResponse:
    def test_non_json_raises_clean_error(self):
        class FakeResp:
            status_code = 502
            text = "<html>Bad Gateway</html>"

            def json(self):
                raise ValueError("Expecting value")

        with pytest.raises(WeChatAPIError, match="non-JSON"):
            json_response(FakeResp(), "add_draft")

    def test_non_dict_raises_clean_error(self):
        class FakeResp:
            status_code = 200
            text = "[1, 2]"

            def json(self):
                return [1, 2]

        with pytest.raises(WeChatAPIError, match="unexpected response type"):
            json_response(FakeResp(), "add_draft")


# ── front matter autofill ───────────────────────────────────────

class TestAutofillFrontMatter:
    def test_generates_block_from_first_heading(self, tmp_path: Path):
        md = tmp_path / "a.md"
        text = "# My Great Post\n\nBody.\n"
        md.write_text(text, encoding="utf-8")
        result = _autofill_front_matter(md, text, None, "Cy257")
        assert result.startswith("---\ntitle: \"My Great Post\"\ndate: \"")
        assert 'author: "Cy257"' in result
        assert "# My Great Post" in result

    def test_leaves_existing_front_matter_alone(self, tmp_path: Path):
        md = tmp_path / "a.md"
        text = '---\ntitle: "Already"\n---\n\nBody.\n'
        assert _autofill_front_matter(md, text, None, "Cy") == text

    def test_cli_title_wins(self, tmp_path: Path):
        md = tmp_path / "a.md"
        text = "# Heading Title\n\nBody.\n"
        md.write_text(text, encoding="utf-8")
        result = _autofill_front_matter(md, text, "CLI Title", "")
        assert 'title: "CLI Title"' in result

    def test_quotes_are_escaped(self, tmp_path: Path):
        md = tmp_path / "a.md"
        text = '# Weird "quoted" title\n\nBody.\n'
        md.write_text(text, encoding="utf-8")
        result = _autofill_front_matter(md, text, None, "")
        assert 'title: "Weird \\"quoted\\" title"' in result

    def test_end_to_end_writes_file_and_dry_run_succeeds(
        self, tmp_project: Path
    ):
        md = tmp_project / "input" / "article.md"
        md.write_text("# Generated Title\n\nSome body text.\n", encoding="utf-8")

        rc = main(
            ["draft", "--md", "input/article.md", "--dry-run",
             "--autofill-front-matter"]
        )
        assert rc == 0
        content = md.read_text(encoding="utf-8")
        assert content.startswith("---\ntitle: \"Generated Title\"\n")
        # Second run is idempotent: no duplicate block
        rc2 = main(
            ["draft", "--md", "input/article.md", "--dry-run",
             "--autofill-front-matter"]
        )
        assert rc2 == 0
        assert content == md.read_text(encoding="utf-8")


# ── cover compression ───────────────────────────────────────────

class TestCompressCover:
    def test_small_cover_untouched(self, tmp_path: Path):
        cover = tmp_path / "cover.png"
        cover.write_bytes(b"\x89PNG" + b"\x00" * 100)
        assert compress_cover(cover) == cover

    def test_large_cover_compressed_to_jpg(self, tmp_path: Path):
        import os

        from PIL import Image

        cover = tmp_path / "cover.png"
        noise = os.urandom(1200 * 510 * 3)
        Image.frombytes("RGB", (1200, 510), noise).save(cover, "PNG")
        assert cover.stat().st_size > 1024 * 1024

        result = compress_cover(cover)
        assert result.suffix == ".jpg"
        assert result.exists()
        assert result.stat().st_size < cover.stat().st_size
        with Image.open(result) as img:
            assert img.width == 900  # scaled down, aspect ratio kept

    def test_missing_pillow_returns_original(self, tmp_path: Path, monkeypatch):
        import sys
        import types

        cover = tmp_path / "cover.png"
        cover.write_bytes(b"\x89PNG" + b"\x00" * (2 * 1024 * 1024))

        class _NoPIL:
            def __getattr__(self, name):
                raise ImportError("PIL not available")

        monkeypatch.setitem(sys.modules, "PIL", _NoPIL())
        assert compress_cover(cover) == cover


# ── image upload failure policy ─────────────────────────────────

class TestProcessImagesFailurePolicy:
    def _html_with_image(self):
        return '<p><img src="missing.png"></p>'

    def test_failure_aborts_by_default(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Failed to upload image"):
            process_images(
                "token", self._html_with_image(),
                [type("R", (), {"original_src": "missing.png",
                                "resolved_path": tmp_path / "nope.png",
                                "is_remote": False})()],
                tmp_path,
            )

    def test_allow_missing_keeps_original_src(self, tmp_path: Path):
        result = process_images(
            "token", self._html_with_image(),
            [type("R", (), {"original_src": "missing.png",
                            "resolved_path": tmp_path / "nope.png",
                            "is_remote": False})()],
            tmp_path, allow_missing=True,
        )
        assert 'src="missing.png"' in result


# ── atomic writes ───────────────────────────────────────────────

class TestAtomicWrites:
    def test_overwrite_and_no_temp_files(self, tmp_path: Path):
        target = tmp_path / "cache.json"
        save_json_mapping(target, {"a": 1})
        save_json_mapping(target, {"a": 2, "b": 3})
        assert load_json_mapping(target) == {"a": 2, "b": 3}
        leftovers = [p for p in tmp_path.iterdir() if p.name != "cache.json"]
        assert leftovers == []

    def test_save_post_state_writes_valid_json(self, tmp_path: Path):
        state = PostState(
            title="原子写 Test",
            source_markdown=Path("input/a.md"),
            wechat_html=Path("build/a.html"),
        )
        path = save_post_state(tmp_path / "posts", state)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["title"] == "原子写 Test"


# ── dry-run must not touch the network ──────────────────────────

class TestDryRunNoNetwork:
    def test_dry_run_with_ai_flags_makes_no_requests(
        self, tmp_project: Path, monkeypatch
    ):
        def _no_network(*a, **kw):
            raise AssertionError("network call attempted during dry-run")

        monkeypatch.setattr("wechat_publish.ai_summary.generate_digest", _no_network)
        monkeypatch.setattr("wechat_publish.ai_cover.generate_cover_image", _no_network)
        monkeypatch.setattr("requests.request", _no_network)
        monkeypatch.setattr("requests.post", _no_network)
        monkeypatch.setattr("requests.get", _no_network)

        rc = main(
            ["draft", "--md", "input/article.md", "--dry-run",
             "--ai-summary", "--ai-cover"]
        )
        assert rc == 0
