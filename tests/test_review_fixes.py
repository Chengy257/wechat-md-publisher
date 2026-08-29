"""Regression tests for the review-fix batch (token retry path, download
suffix sniffing, dead-code removal, error hint, non-dict token cache).
"""

import hashlib
import io
from pathlib import Path

import PIL.Image
import pytest
import responses
from conftest import make_png

import wechat_publish.cli as cli_module
from wechat_publish.cli import main
from wechat_publish.errors import hint_for_error
from wechat_publish.images import _resolve_image_to_file, validate_body_image
from wechat_publish.token import load_cached_token

_WX = "https://api.weixin.qq.com"


# ── fix 1: draft/add 40001 refresh writes to the account-scoped cache ──

class TestTokenRetryUsesAccountScopedCache:
    @responses.activate
    def test_mid_run_refresh_targets_account_scoped_token_cache(
        self, tmp_project: Path, monkeypatch
    ):
        make_png(tmp_project / "input" / "cover.png")
        article = tmp_project / "input" / "article.md"
        article.write_text('---\ntitle: "T"\n---\n\nBody.\n', encoding="utf-8")

        # Full publish flow where draft/add rejects the first token once
        # (40001), forcing a mid-run token refresh before succeeding.
        responses.add(
            responses.GET, f"{_WX}/cgi-bin/token",
            json={"access_token": "TK" * 30, "expires_in": 7200},
        )
        responses.add(
            responses.POST, f"{_WX}/cgi-bin/material/add_material",
            json={"media_id": "COVER_MEDIA_ID_123456", "url": "https://mmbiz/cover"},
        )
        responses.add(
            responses.POST, f"{_WX}/cgi-bin/draft/add",
            json={"errcode": 40001, "errmsg": "invalid credential, access_token expired"},
        )
        responses.add(
            responses.GET, f"{_WX}/cgi-bin/token",
            json={"access_token": "TK2" * 30, "expires_in": 7200},
        )
        responses.add(
            responses.POST, f"{_WX}/cgi-bin/draft/add",
            json={"media_id": "DRAFT_MEDIA_ID_123456"},
        )

        refresh_calls: list[Path] = []
        original = cli_module.get_access_token

        def recording_get_access_token(appid, appsecret, cache_path, force_refresh=False):
            if force_refresh:
                refresh_calls.append(Path(cache_path))
            return original(appid, appsecret, cache_path, force_refresh=force_refresh)

        monkeypatch.setattr(cli_module, "get_access_token", recording_get_access_token)

        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 0

        # The forced refresh must have gone through the account-scoped path
        state_dir = tmp_project / ".wechat_publish"
        expected = (
            state_dir / "accounts"
            / hashlib.sha256(b"wx_test_appid").hexdigest()[:12] / "token.json"
        )
        assert refresh_calls == [expected]
        assert expected.exists()
        # ... and never into the legacy non-isolated location
        assert not (state_dir / "token.json").exists()


# ── fix 2: remote downloads sniff the real format and fix the suffix ──

class _FakeDownloadResponse:
    def __init__(self, payload: bytes, content_type: str):
        self.status_code = 200
        self.headers = {"Content-Type": content_type}
        self._payload = payload

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self._payload


def _patch_http(monkeypatch, payload: bytes, content_type: str) -> None:
    resp = _FakeDownloadResponse(payload, content_type)

    def fake_request(method, url, timeout=None, **kwargs):
        return resp

    monkeypatch.setattr("wechat_publish.http.requests.request", fake_request)

    # Keep URL validation offline: resolve cdn.example to a public IP.
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("wechat_publish.images.socket.getaddrinfo", fake_getaddrinfo)


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    PIL.Image.new("RGB", (8, 8), "blue").save(buf, "JPEG")
    return buf.getvalue()


def _gif_bytes() -> bytes:
    buf = io.BytesIO()
    PIL.Image.new("RGB", (8, 8), "red").save(buf, "GIF")
    return buf.getvalue()


class TestRemoteDownloadSuffixSniffing:
    def test_extensionless_url_serving_jpeg_gets_jpg_suffix(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_http(monkeypatch, _jpeg_bytes(), "image/jpeg")

        path, is_temp = _resolve_image_to_file(
            "https://cdn.example/img?sig=abc", None, True
        )
        assert is_temp
        try:
            # Sniffed JPEG replaced the guessed .png suffix
            assert path.suffix == ".jpg"
            assert path.read_bytes().startswith(b"\xff\xd8\xff")
            # The same-family validator now accepts the file
            validate_body_image(path)
        finally:
            path.unlink(missing_ok=True)

    def test_non_body_family_format_is_left_and_rejected(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_http(monkeypatch, _gif_bytes(), "image/gif")

        path, is_temp = _resolve_image_to_file(
            "https://cdn.example/img", None, True
        )
        assert is_temp
        try:
            # GIF is not a body-image family: suffix stays .png (guessed)
            # and validation rejects the mismatch with a clear error.
            assert path.suffix == ".png"
            with pytest.raises(ValueError, match="文件内容与图片格式不符"):
                validate_body_image(path)
        finally:
            path.unlink(missing_ok=True)


# ── fix 3: dead _project_dir removed ────────────────────────────

class TestProjectDirRemoved:
    def test_dead_project_dir_helper_is_gone(self):
        assert not hasattr(cli_module, "_project_dir")
        assert hasattr(cli_module, "_discover_project_dir")


# ── fix 4: access_token hint points at the account-scoped layout ──

class TestTokenHintPath:
    def test_hint_references_account_scoped_token_json(self):
        hint = hint_for_error("add_draft", 40003, "invalid access_token")
        assert ".wechat_publish/accounts/<account-key>/token.json" in hint
        assert ".wechat_publish/token.json" not in hint


# ── fix 5: non-dict JSON token cache is a cache miss, not a crash ──

class TestNonDictTokenCache:
    def test_list_json_cache_returns_none(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        cache.write_text("[1, 2]", encoding="utf-8")
        assert load_cached_token(cache) is None
        assert load_cached_token(cache, expected_appid="appid") is None
