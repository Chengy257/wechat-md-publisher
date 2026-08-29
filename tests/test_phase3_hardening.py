"""Phase 3 hardening tests: retry-by-idempotency, field validation,
remote-success/local-failure reporting, publish-stage ordering, AI cover."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import requests
import responses
from conftest import make_png

from wechat_publish.ai_cover import _DEFAULT_MODEL, generate_cover_image
from wechat_publish.cli import main
from wechat_publish.errors import AmbiguousRequestError, WeChatAPIError
from wechat_publish.http import RetryPolicy, request_with_retry, require_field

_WX = "https://api.weixin.qq.com"

# A real 4x2 PNG (Pillow-decodable), needed because AI covers are verified
# with Pillow before being written to disk.
_VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAACCAIAAADwyuo0AAAAFElEQVR4nGM8ISfHAANMcBYD"
    "AwMAGVgBCNdbWuMAAAAASUVORK5CYII="
)
# Valid base64 that Pillow cannot decode (used for the verify-failure test).
_CORRUPT_IMAGE_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50).decode()


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _mock_http_request(monkeypatch, outcomes: list) -> dict:
    """Replace http.requests.request with a scripted fake.

    *outcomes* is a list of exceptions (to raise) or int status codes (to
    return); the last entry repeats once exhausted. Returns the call counter.
    """
    calls = {"n": 0}

    def fake_request(method: str, url: str, **kwargs: Any):
        calls["n"] += 1
        outcome = outcomes[min(calls["n"] - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(outcome)

    monkeypatch.setattr("wechat_publish.http.requests.request", fake_request)
    monkeypatch.setattr("wechat_publish.http.time.sleep", lambda s: None)
    return calls


# ── Retry policies ─────────────────────────────────────────────


class TestSafePolicy:
    def test_read_timeout_is_retried_for_safe_get(self, monkeypatch):
        calls = _mock_http_request(
            monkeypatch, [requests.exceptions.ReadTimeout("late"), 200]
        )
        resp = request_with_retry("GET", "https://x/img", operation="probe")
        assert resp.status_code == 200
        assert calls["n"] == 2

    def test_safe_is_the_default(self):
        import inspect

        sig = inspect.signature(request_with_retry)
        assert sig.parameters["policy"].default is RetryPolicy.SAFE


class TestUploadPolicy:
    def test_5xx_is_retried_then_succeeds(self, monkeypatch):
        calls = _mock_http_request(monkeypatch, [502, 502, 200])
        resp = request_with_retry(
            "POST", "https://x/upload", operation="upload", policy=RetryPolicy.UPLOAD
        )
        assert resp.status_code == 200
        assert calls["n"] == 3

    def test_read_timeout_raises_ambiguous_without_replay(self, monkeypatch):
        calls = _mock_http_request(
            monkeypatch, [requests.exceptions.ReadTimeout("late")]
        )
        with pytest.raises(AmbiguousRequestError, match="outcome is uncertain"):
            request_with_retry(
                "POST", "https://x/upload", operation="upload",
                policy=RetryPolicy.UPLOAD,
            )
        assert calls["n"] == 1

    def test_generic_connection_error_raises_ambiguous(self, monkeypatch):
        calls = _mock_http_request(
            monkeypatch, [requests.exceptions.ConnectionError("reset")]
        )
        with pytest.raises(AmbiguousRequestError):
            request_with_retry(
                "POST", "https://x/upload", operation="upload",
                policy=RetryPolicy.UPLOAD,
            )
        assert calls["n"] == 1


class TestNonIdempotentPolicy:
    def test_connect_timeout_is_retried_once_then_succeeds(self, monkeypatch):
        calls = _mock_http_request(
            monkeypatch, [requests.exceptions.ConnectTimeout("no conn"), 200]
        )
        resp = request_with_retry(
            "POST", "https://x/draft", operation="add_draft",
            policy=RetryPolicy.NON_IDEMPOTENT,
        )
        assert resp.status_code == 200
        assert calls["n"] == 2

    def test_read_timeout_never_sends_second_post(self, monkeypatch):
        calls = _mock_http_request(
            monkeypatch, [requests.exceptions.ReadTimeout("late")]
        )
        with pytest.raises(AmbiguousRequestError) as excinfo:
            request_with_retry(
                "POST", "https://x/draft", operation="add_draft",
                policy=RetryPolicy.NON_IDEMPOTENT,
            )
        assert calls["n"] == 1  # absolutely no blind replay
        msg = str(excinfo.value)
        assert "outcome is uncertain" in msg
        assert "draft list" in msg

    def test_5xx_raises_ambiguous_without_replay(self, monkeypatch):
        calls = _mock_http_request(monkeypatch, [500])
        with pytest.raises(AmbiguousRequestError) as excinfo:
            request_with_retry(
                "POST", "https://x/draft", operation="add_draft",
                policy=RetryPolicy.NON_IDEMPOTENT,
            )
        assert calls["n"] == 1
        msg = str(excinfo.value)
        assert "outcome is uncertain" in msg
        assert "draft list" in msg


# ── Field validation (require_field) ───────────────────────────


class TestRequireField:
    def test_missing_access_token_raises_wechat_api_error(self):
        with pytest.raises(WeChatAPIError) as excinfo:
            require_field({"errcode": -1}, "access_token", "get_access_token")
        assert not isinstance(excinfo.value, KeyError)
        assert "get_access_token" in str(excinfo.value)
        assert "access_token" in str(excinfo.value)

    def test_missing_cover_media_id_raises_wechat_api_error(self):
        with pytest.raises(WeChatAPIError) as excinfo:
            require_field({"url": "https://mmbiz/x"}, "media_id", "upload_cover_image")
        assert not isinstance(excinfo.value, KeyError)
        assert "upload_cover_image" in str(excinfo.value)
        assert "media_id" in str(excinfo.value)

    def test_missing_body_url_raises_wechat_api_error(self):
        with pytest.raises(WeChatAPIError) as excinfo:
            require_field({"media_id": "M"}, "url", "upload_body_image")
        assert not isinstance(excinfo.value, KeyError)
        assert "upload_body_image" in str(excinfo.value)
        assert "url" in str(excinfo.value)

    def test_none_value_also_rejected(self):
        with pytest.raises(WeChatAPIError):
            require_field({"media_id": None}, "media_id", "add_draft")

    def test_present_value_returned(self):
        assert require_field({"media_id": "M"}, "media_id", "add_draft") == "M"


# ── Full-flow helpers ──────────────────────────────────────────


def _token_cache_path(tmp_project: Path) -> Path:
    key = hashlib.sha256(b"wx_test_appid").hexdigest()[:12]
    return tmp_project / ".wechat_publish" / "accounts" / key / "token.json"


def _setup_article(tmp_project: Path) -> None:
    make_png(tmp_project / "input" / "fig1.png")
    make_png(tmp_project / "input" / "cover.png")
    article = tmp_project / "input" / "article.md"
    article.write_text(
        '---\ntitle: "集成测试文章"\nauthor: "Cy257"\n---\n\n'
        "# Heading\n\n![fig](fig1.png)\n\nBody text.\n",
        encoding="utf-8",
    )


def _mock_wechat_endpoints() -> None:
    responses.add(
        responses.GET,
        f"{_WX}/cgi-bin/token",
        json={"access_token": "TK" * 30, "expires_in": 7200},
    )
    responses.add(
        responses.POST,
        f"{_WX}/cgi-bin/material/add_material",
        json={"media_id": "COVER_MEDIA_ID_123456", "url": "https://mmbiz/cover"},
    )
    responses.add(
        responses.POST,
        f"{_WX}/cgi-bin/media/uploadimg",
        json={"url": "https://mmbiz.qpic.cn/fig1.png"},
    )
    responses.add(
        responses.POST,
        f"{_WX}/cgi-bin/draft/add",
        json={"media_id": "DRAFT_MEDIA_ID_123456"},
    )


# ── Remote success / local failure ─────────────────────────────


class TestRemoteSuccessLocalFailure:
    @responses.activate
    def test_save_post_snapshot_failure_reports_media_id(
        self, tmp_project: Path, monkeypatch, capsys
    ):
        _setup_article(tmp_project)
        _mock_wechat_endpoints()

        import wechat_publish.cli as cli

        add_draft_calls = {"n": 0}
        real_add_draft = cli.add_draft

        def counting_add_draft(tv, article):
            add_draft_calls["n"] += 1
            return real_add_draft(tv, article)

        monkeypatch.setattr(cli, "add_draft", counting_add_draft)

        def failing_save_post_snapshot(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(cli, "save_post_snapshot", failing_save_post_snapshot)

        rc = main(["draft", "--md", "input/article.md"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "DRAFT_MEDIA_ID_123456" in err
        assert "Do NOT blindly rerun" in err
        assert add_draft_calls["n"] == 1  # add_draft ran exactly once
        # No snapshot was persisted
        assert list((tmp_project / ".wechat_publish" / "posts").glob("*/state.json")) == []

    @responses.activate
    def test_token_cache_written_before_failure(
        self, tmp_project: Path, monkeypatch
    ):
        """Sanity: the failure path leaves no partial posts, token cache intact."""
        _setup_article(tmp_project)
        _mock_wechat_endpoints()

        import wechat_publish.cli as cli

        monkeypatch.setattr(
            cli, "save_post_snapshot",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")),
        )
        assert main(["draft", "--md", "input/article.md"]) != 0
        cache = json.loads(_token_cache_path(tmp_project).read_text(encoding="utf-8"))
        assert cache["access_token"] == "TK" * 30


# ── Publish-stage ordering ─────────────────────────────────────


class TestPublishStageOrdering:
    @responses.activate
    def test_final_html_written_before_add_draft(self, tmp_project: Path, monkeypatch):
        _setup_article(tmp_project)
        _mock_wechat_endpoints()

        import wechat_publish.cli as cli

        wechat_path = tmp_project / "build" / "article.wechat.html"

        real_add_draft = cli.add_draft

        def order_checking_add_draft(tv, article):
            assert wechat_path.exists(), "final HTML must exist before draft/add"
            content = wechat_path.read_text(encoding="utf-8")
            assert "https://mmbiz.qpic.cn/fig1.png" in content
            return real_add_draft(tv, article)

        monkeypatch.setattr(cli, "add_draft", order_checking_add_draft)

        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 0


# ── AI cover ───────────────────────────────────────────────────


def _mock_gemini_response(mock_post, b64_data: str, mime: str | None = None):
    mock_resp = mock_post.return_value
    mock_resp.raise_for_status.return_value = None
    inline: dict[str, Any] = {"data": b64_data}
    if mime is not None:
        inline["mimeType"] = mime
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"inlineData": inline}]}}]
    }


class TestAiCoverHardening:
    def test_default_model_updated(self):
        assert _DEFAULT_MODEL == "gemini-2.5-flash-image"

    def test_payload_contains_image_config(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "wechat_publish.ai_cover._verify_image_bytes", lambda b: None
        )
        with patch("wechat_publish.ai_cover.requests.post") as mock_post:
            _mock_gemini_response(mock_post, _CORRUPT_IMAGE_B64)
            generate_cover_image("T", tmp_path / "c.png", "https://api.example.com", "k")
            payload = json.loads(mock_post.call_args.kwargs["data"])
            gen = payload["generationConfig"]
            assert gen["imageConfig"]["aspectRatio"] == "21:9"

    def test_bad_base64_raises(self, tmp_path: Path):
        with patch("wechat_publish.ai_cover.requests.post") as mock_post:
            _mock_gemini_response(mock_post, "!!!not-base64!!!")
            with pytest.raises(ValueError, match="invalid base64"):
                generate_cover_image(
                    "T", tmp_path / "c.png", "https://api.example.com", "k"
                )

    def test_non_image_mime_raises(self, tmp_path: Path):
        with patch("wechat_publish.ai_cover.requests.post") as mock_post:
            _mock_gemini_response(mock_post, _CORRUPT_IMAGE_B64, mime="text/plain")
            with pytest.raises(ValueError, match="non-image"):
                generate_cover_image(
                    "T", tmp_path / "c.png", "https://api.example.com", "k"
                )

    def test_pillow_verify_failure_raises(self, tmp_path: Path):
        # Valid base64, plausible PNG header, but not a decodable image.
        with patch("wechat_publish.ai_cover.requests.post") as mock_post:
            _mock_gemini_response(mock_post, _CORRUPT_IMAGE_B64, mime="image/png")
            with pytest.raises(ValueError, match="invalid image data"):
                generate_cover_image(
                    "T", tmp_path / "c.png", "https://api.example.com", "k"
                )

    def test_success_writes_valid_png(self, tmp_path: Path):
        b64 = base64.b64encode(_VALID_PNG).decode()
        with patch("wechat_publish.ai_cover.requests.post") as mock_post:
            _mock_gemini_response(mock_post, b64, mime="image/png")
            out = tmp_path / "c.png"
            result = generate_cover_image("T", out, "https://api.example.com", "k")
            assert result == out
            assert out.read_bytes() == _VALID_PNG

    @responses.activate
    def test_ai_cover_always_compressed_without_flag(
        self, tmp_project: Path, monkeypatch
    ):
        _setup_article(tmp_project)
        # Point the front-matter cover at a missing file so --ai-cover kicks in.
        article = tmp_project / "input" / "article.md"
        article.write_text(
            '---\ntitle: "集成测试文章"\nauthor: "Cy257"\n'
            'cover: "missing_cover.png"\n---\n\n'
            "# Heading\n\n![fig](fig1.png)\n\nBody text.\n",
            encoding="utf-8",
        )
        _mock_wechat_endpoints()
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

        import wechat_publish.ai_cover as ai_cover
        import wechat_publish.cli as cli

        def fake_generate(title, output_path, api_url, api_key, model="", prompt=""):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(_VALID_PNG)
            return output_path

        monkeypatch.setattr(ai_cover, "generate_cover_image", fake_generate)
        compress_calls = {"n": 0}
        real_compress = cli.compress_cover

        def counting_compress(path):
            compress_calls["n"] += 1
            return real_compress(path)

        monkeypatch.setattr(cli, "compress_cover", counting_compress)

        rc = main(["draft", "--md", "input/article.md", "--ai-cover"])
        assert rc == 0
        assert compress_calls["n"] >= 1  # compressed even without --compress-cover
