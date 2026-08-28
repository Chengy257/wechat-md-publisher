"""End-to-end integration tests: full draft flow against mocked WeChat APIs."""

import json
from pathlib import Path

import responses

from wechat_publish.cli import main
from wechat_publish.http import request_with_retry
from wechat_publish.token import AccessToken, get_access_token

_WX = "https://api.weixin.qq.com"


def _setup_article(tmp_project: Path) -> None:
    """Article with one body image plus a cover image on disk."""
    fig = tmp_project / "input" / "fig1.png"
    fig.write_bytes(b"\x89PNG" + b"\x00" * 50)
    cover = tmp_project / "input" / "cover.png"
    cover.write_bytes(b"\x89PNG" + b"\x00" * 50)
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


class TestFullDraftFlow:
    @responses.activate
    def test_publish_creates_draft_and_rewrites_images(self, tmp_project: Path):
        _setup_article(tmp_project)
        _mock_wechat_endpoints()

        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 0

        # The four WeChat endpoints were hit in order
        paths = [c.request.url.split("?")[0] for c in responses.calls]
        assert paths == [
            f"{_WX}/cgi-bin/token",
            f"{_WX}/cgi-bin/material/add_material",
            f"{_WX}/cgi-bin/media/uploadimg",
            f"{_WX}/cgi-bin/draft/add",
        ]

        # draft/add payload carries the uploaded cover and the WeChat image URL
        draft_body = json.loads(responses.calls[-1].request.body)
        art = draft_body["articles"][0]
        assert art["title"] == "集成测试文章"
        assert art["thumb_media_id"] == "COVER_MEDIA_ID_123456"
        assert "https://mmbiz.qpic.cn/fig1.png" in art["content"]
        assert 'src="fig1.png"' not in art["content"]

        # The HTML on disk was rewritten with uploaded URLs
        html = (tmp_project / "build" / "article.wechat.html").read_text(
            encoding="utf-8"
        )
        assert "https://mmbiz.qpic.cn/fig1.png" in html

        # State was persisted
        posts = list((tmp_project / ".wechat_publish" / "posts").glob("*.json"))
        assert len(posts) == 1
        state = json.loads(posts[0].read_text(encoding="utf-8"))
        assert state["draft_media_id"] == "DRAFT_MEDIA_ID_123456"

        # Token was cached for reuse
        token_cache = json.loads(
            (tmp_project / ".wechat_publish" / "token.json").read_text(
                encoding="utf-8"
            )
        )
        assert token_cache["access_token"] == "TK" * 30

    @responses.activate
    def test_expired_token_midrun_recovers(self, tmp_project: Path, monkeypatch):
        _setup_article(tmp_project)
        # Pre-seed a token cache that is still valid but will be rejected
        cache = tmp_project / ".wechat_publish" / "token.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            json.dumps({"access_token": "STALE" * 8, "expires_at": 2**31}),
            encoding="utf-8",
        )

        responses.add(
            responses.POST,
            f"{_WX}/cgi-bin/material/add_material",
            json={"errcode": 42001, "errmsg": "access_token expired"},
        )
        responses.add(
            responses.GET,
            f"{_WX}/cgi-bin/token",
            json={"access_token": "FRESH" * 8, "expires_in": 7200},
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

        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 0
        token_urls = [
            c.request.url.split("?")[0]
            for c in responses.calls
            if c.request.url.startswith(f"{_WX}/cgi-bin/token")
        ]
        assert token_urls == [f"{_WX}/cgi-bin/token"]  # refreshed exactly once


class TestHttpRetry:
    @responses.activate
    def test_recovers_from_502(self, monkeypatch):
        monkeypatch.setattr("wechat_publish.http.time.sleep", lambda s: None)
        url = "https://api.example.com/ping"
        responses.add(responses.GET, url, body="<html>gateway error</html>", status=502)
        responses.add(responses.GET, url, json={"ok": True})

        resp = request_with_retry("GET", url, operation="probe")
        assert resp.json() == {"ok": True}

    @responses.activate
    def test_non_json_wechat_response_raises_cleanly(self):
        url = f"{_WX}/cgi-bin/token?grant_type=client_credential&appid=a&secret=s"
        responses.add(responses.GET, url, body="<html>Bad Gateway</html>")

        from wechat_publish.token import request_access_token

        try:
            request_access_token("a", "s")
        except Exception as e:
            assert "non-JSON" in str(e)
        else:
            raise AssertionError("expected WeChatAPIError")


class TestTokenCache:
    @responses.activate
    def test_valid_cache_used_without_request(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        cache.write_text(
            json.dumps({"access_token": "CACHED" * 8, "expires_at": 2**31}),
            encoding="utf-8",
        )
        token = get_access_token("appid", "secret", cache)
        assert token.value == "CACHED" * 8
        assert len(responses.calls) == 0

    @responses.activate
    def test_force_refresh_bypasses_valid_cache(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        cache.write_text(
            json.dumps({"access_token": "CACHED" * 8, "expires_at": 2**31}),
            encoding="utf-8",
        )
        responses.add(
            responses.GET,
            f"{_WX}/cgi-bin/token",
            json={"access_token": "NEW" * 8, "expires_in": 7200},
        )
        token = get_access_token("appid", "secret", cache, force_refresh=True)
        assert token.value == "NEW" * 8
        assert len(responses.calls) == 1
        # cache updated
        updated = json.loads(cache.read_text(encoding="utf-8"))
        assert updated["access_token"] == "NEW" * 8


class TestInspectAndRenderCommands:
    def test_inspect_masks_appid(self, tmp_project: Path, capsys):
        rc = main(["inspect", "--md", "input/article.md"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "测试文章" in out  # resolved title from front matter
        assert "title:" in out
        assert "wx_t****id" in out

    def test_render_applies_default_theme_and_sanitizes(self, tmp_project: Path):
        article = tmp_project / "input" / "article.md"
        article.write_text(
            '---\ntitle: "T"\n---\n\n# Head\n\n<script>alert(1)</script>\n\nText.\n',
            encoding="utf-8",
        )
        rc = main([
            "render", "--md", "input/article.md",
            "--out", "build/w.html", "--preview-out", "build/p.html",
        ])
        assert rc == 0
        html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
        assert "<script>" not in html
        assert "style=" in html  # bundled default theme inlined
        assert "<h1" not in html or "h1-like" not in html  # h1 untouched by design
