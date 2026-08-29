"""WeChat API payload and error handling tests with mocked HTTP."""

import json
import time
from pathlib import Path

import pytest
from conftest import make_png

from wechat_publish.draft import DraftArticle, build_draft_payload
from wechat_publish.errors import (
    WeChatAPIError,
    check_wechat_response,
    hint_for_error,
)
from wechat_publish.images import sha256_file, validate_body_image, validate_cover_image
from wechat_publish.state import (
    ensure_state_dirs,
    load_json_mapping,
    save_json_mapping,
    save_post_snapshot,
)
from wechat_publish.token import load_cached_token, mask_token

# ── Draft payload ───────────────────────────────────────────────

class TestBuildDraftPayload:
    def test_basic_payload(self):
        article = DraftArticle(
            title="测试标题",
            author="Cy257",
            digest="摘要",
            content="<p>HTML content</p>",
            thumb_media_id="MEDIA_123",
        )
        payload = build_draft_payload(article)
        assert "articles" in payload
        assert len(payload["articles"]) == 1
        art = payload["articles"][0]
        assert art["title"] == "测试标题"
        assert art["thumb_media_id"] == "MEDIA_123"
        assert art["need_open_comment"] == 1

    def test_payload_with_source_url(self):
        article = DraftArticle(
            title="Title",
            author="Author",
            digest="Digest",
            content="Content",
            thumb_media_id="MID",
            content_source_url="https://example.com",
        )
        payload = build_draft_payload(article)
        assert payload["articles"][0]["content_source_url"] == "https://example.com"

    def test_chinese_content_preserved(self):
        article = DraftArticle(
            title="中文标题",
            author="中文作者",
            digest="中文摘要",
            content="<section><h1>中文</h1></section>",
            thumb_media_id="MID",
        )
        payload = build_draft_payload(article)
        encoded = json.dumps(payload, ensure_ascii=False)
        assert "中文标题" in encoded
        assert "中文摘要" in encoded


# ── Error handling ──────────────────────────────────────────────

class TestCheckWechatResponse:
    def test_success_no_errcode(self):
        # Should not raise
        check_wechat_response("test", {"access_token": "abc", "expires_in": 7200})

    def test_success_errcode_zero(self):
        check_wechat_response("test", {"errcode": 0, "errmsg": "ok"})

    def test_raises_on_error(self):
        with pytest.raises(WeChatAPIError) as exc_info:
            check_wechat_response("test", {"errcode": 40001, "errmsg": "invalid credential"})
        assert exc_info.value.detail.errcode == 40001
        assert "invalid credential" in str(exc_info.value)

    def test_error_contains_hint(self):
        with pytest.raises(WeChatAPIError) as exc_info:
            check_wechat_response("get_access_token", {"errcode": 40001, "errmsg": "invalid credential"})
        assert exc_info.value.detail.hint != ""

    def test_ip_error_hint(self):
        with pytest.raises(WeChatAPIError) as exc_info:
            check_wechat_response("test", {"errcode": 87009, "errmsg": "invalid ip"})
        assert "IP" in exc_info.value.detail.hint or "白名单" in exc_info.value.detail.hint


class TestHintForError:
    def test_known_errcode(self):
        hint = hint_for_error("test", 40007, "invalid media_id")
        assert "thumb_media_id" in hint or "material/add_material" in hint

    def test_unknown_errcode(self):
        hint = hint_for_error("test", 99999, "unknown")
        assert "未知" in hint or "99999" in hint

    def test_operation_specific_hint(self):
        hint = hint_for_error("upload_cover_image", 40009, "image error")
        assert hint != ""


# ── Token ───────────────────────────────────────────────────────

class TestLoadCachedToken:
    def test_loads_valid_token(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        expires_at = int(time.time()) + 6000
        cache.write_text(json.dumps({"access_token": "ABC123", "expires_at": expires_at}))
        token = load_cached_token(cache)
        assert token is not None
        assert token.value == "ABC123"
        assert token.expires_at == expires_at

    def test_returns_none_for_missing(self, tmp_path: Path):
        token = load_cached_token(tmp_path / "missing.json")
        assert token is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        cache.write_text("not json")
        token = load_cached_token(cache)
        assert token is None

    def test_returns_none_for_empty_token(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        cache.write_text(json.dumps({"access_token": "", "expires_at": 9999999}))
        token = load_cached_token(cache)
        assert token is None


class TestMaskToken:
    def test_masks_long_token(self):
        result = mask_token("abcdefghijklmnop", 4)
        assert result == "abcd...mnop"

    def test_short_token(self):
        result = mask_token("abc", 4)
        assert result == "***"


# ── Image validation ────────────────────────────────────────────

class TestImageValidation:
    def test_valid_image(self, tmp_path: Path):
        img = make_png(tmp_path / "test.png")
        validate_body_image(img)  # Should not raise

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            validate_cover_image(tmp_path / "missing.png")

    def test_invalid_extension(self, tmp_path: Path):
        img = tmp_path / "test.exe"
        img.write_bytes(b"\x00" * 100)
        with pytest.raises(ValueError, match="not allowed"):
            validate_body_image(img)

    def test_too_large(self, tmp_path: Path):
        img = tmp_path / "big.png"
        # Write a file larger than 1MB
        img.write_bytes(b"\x00" * (1024 * 1024 + 1))
        with pytest.raises(ValueError, match="too large"):
            validate_body_image(img)


class TestSha256File:
    def test_consistent_hash(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        h1 = sha256_file(f)
        h2 = sha256_file(f)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex digest length

    def test_different_files_different_hash(self, tmp_path: Path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content a")
        f2.write_bytes(b"content b")
        assert sha256_file(f1) != sha256_file(f2)


# ── State persistence ───────────────────────────────────────────

class TestStatePersistence:
    def test_ensure_state_dirs(self, tmp_path: Path):
        state_dir = tmp_path / "state"
        ensure_state_dirs(state_dir)
        assert state_dir.is_dir()
        assert (state_dir / "posts").is_dir()

    def test_save_and_load_json(self, tmp_path: Path):
        path = tmp_path / "test.json"
        data = {"key": "value", "count": 42}
        save_json_mapping(path, data)
        loaded = load_json_mapping(path)
        assert loaded["key"] == "value"
        assert loaded["count"] == 42

    def test_load_missing_returns_empty(self, tmp_path: Path):
        result = load_json_mapping(tmp_path / "missing.json")
        assert result == {}

    def test_load_invalid_json_returns_empty(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json{{{")
        result = load_json_mapping(path)
        assert result == {}

    def test_save_post_snapshot(self, tmp_path: Path):
        posts_dir = tmp_path / "posts"
        source = tmp_path / "input" / "test.md"
        source.parent.mkdir()
        source.write_text("# test", encoding="utf-8")
        path = save_post_snapshot(
            posts_dir,
            title="Test Article",
            appid_hash="abc123def456",
            draft_media_id="MEDIA_123",
            source_markdown_path=source,
            final_html="<p>test</p>",
        )
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["title"] == "Test Article"
        assert data["draft_media_id"] == "MEDIA_123"

    def test_save_post_snapshot_no_media_id(self, tmp_path: Path):
        posts_dir = tmp_path / "posts"
        source = tmp_path / "input" / "test.md"
        source.parent.mkdir()
        source.write_text("# test", encoding="utf-8")
        path = save_post_snapshot(
            posts_dir,
            title="Draft Only",
            appid_hash="abc123def456",
            draft_media_id=None,
            source_markdown_path=source,
            final_html="<p>test</p>",
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "draft_media_id" not in data
