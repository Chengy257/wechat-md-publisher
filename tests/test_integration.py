"""End-to-end integration tests: full draft flow against mocked WeChat APIs."""

import hashlib
import json
from pathlib import Path

import pytest
import responses
from conftest import make_png

from wechat_publish.cli import main
from wechat_publish.http import request_with_retry
from wechat_publish.token import get_access_token

_WX = "https://api.weixin.qq.com"


def _token_cache_path(tmp_project: Path) -> Path:
    """Account-scoped token cache path for the test appid."""
    key = hashlib.sha256(b"wx_test_appid").hexdigest()[:12]
    return tmp_project / ".wechat_publish" / "accounts" / key / "token.json"


def _setup_article(tmp_project: Path) -> None:
    """Article with one body image plus a cover image on disk."""
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

        # An immutable snapshot was persisted under a unique directory
        snapshots = sorted(
            (tmp_project / ".wechat_publish" / "posts").glob("*/state.json")
        )
        assert len(snapshots) == 1
        state = json.loads(snapshots[0].read_text(encoding="utf-8"))
        assert state["draft_media_id"] == "DRAFT_MEDIA_ID_123456"
        snapshot_dir = snapshots[0].parent
        assert (snapshot_dir / "final.wechat.html").exists()
        assert (snapshot_dir / "source.md").exists()

        # Token was cached for reuse (account-scoped, bound to the appid)
        token_cache = json.loads(
            _token_cache_path(tmp_project).read_text(encoding="utf-8")
        )
        assert token_cache["access_token"] == "TK" * 30
        assert token_cache["appid"] == "wx_test_appid"

    @responses.activate
    def test_expired_token_midrun_recovers(self, tmp_project: Path, monkeypatch):
        _setup_article(tmp_project)
        # Pre-seed an account-scoped token cache that is valid but will be rejected
        cache = _token_cache_path(tmp_project)
        cache.parent.mkdir(parents=True)
        cache.write_text(
            json.dumps({
                "appid": "wx_test_appid",
                "access_token": "STALE" * 8,
                "expires_at": 2**31,
            }),
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
    @staticmethod
    def _write_cache(cache: Path, token_value: str) -> None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({
                "appid": "appid",
                "access_token": token_value,
                "expires_at": 2**31,
            }),
            encoding="utf-8",
        )

    @responses.activate
    def test_valid_cache_used_without_request(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        self._write_cache(cache, "CACHED" * 8)
        token = get_access_token("appid", "secret", cache)
        assert token.value == "CACHED" * 8
        assert len(responses.calls) == 0

    @responses.activate
    def test_force_refresh_bypasses_valid_cache(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        self._write_cache(cache, "CACHED" * 8)
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

    def test_render_accepts_new_bundled_themes(self, tmp_project: Path):
        """fancy / nb / filling are registered as --theme choices and inline."""
        article = tmp_project / "input" / "article.md"
        article.write_text(
            "# Heading\n\n> quote\n\nSee [link](https://example.com).\n\n"
            "```python\nprint('hi')\n```\n",
            encoding="utf-8",
        )
        identity = {"fancy": "#0969da", "nb": "#5b6cff", "filling": "#c0392b"}
        for theme, color in identity.items():
            rc = main([
                "render", "--md", "input/article.md",
                "--theme", theme,
                "--out", "build/w.html", "--preview-out", "build/p.html",
            ])
            assert rc == 0, theme
            html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
            assert "style=" in html  # theme CSS inlined
            assert color in html  # theme identity color survives inlining


class TestLayoutPaletteCLI:
    """--layout/--palette on render/draft/inspect (WU-B matrix unit)."""

    _ARTICLE = (
        '---\ntitle: "T"\n---\n\n# Head\n\n**bold identity**\n\n'
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\ndef f(x):\n    return x\n```\n"
    )

    def test_render_layout_default_palette_nb(self, tmp_project: Path):
        (tmp_project / "input" / "article.md").write_text(self._ARTICLE, encoding="utf-8")
        rc = main([
            "render", "--md", "input/article.md",
            "--layout", "default", "--palette", "nb",
            "--out", "build/w.html", "--preview-out", "build/p.html",
        ])
        assert rc == 0
        html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
        assert "#5b6cff" in html  # nb identity color survives inlining
        assert "color:#ff7b72" in html  # github-dark keyword (code_scheme=github-dark)

    def test_render_style_beats_layout_palette(self, tmp_project: Path):
        (tmp_project / "input" / "article.md").write_text(self._ARTICLE, encoding="utf-8")
        style = tmp_project / "config" / "custom.css"
        style.write_text("h1 { color: #123abc; }", encoding="utf-8")
        rc = main([
            "render", "--md", "input/article.md",
            "--style", "config/custom.css",
            "--layout", "default", "--palette", "nb",
            "--out", "build/w.html", "--preview-out", "build/p.html",
        ])
        assert rc == 0
        html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
        assert "#123abc" in html  # the --style file was inlined
        assert "#5b6cff" not in html  # the palette was not applied
        # palette=None -> pygments falls back to the light default
        assert "color:#ff7b72" not in html
        assert "color:#007020" in html  # friendly keyword color

    def test_render_rejects_unknown_palette_choice(self, tmp_project: Path, capsys):
        with pytest.raises(SystemExit):
            main([
                "render", "--md", "input/article.md",
                "--palette", "nope",
                "--out", "build/w.html", "--preview-out", "build/p.html",
            ])
        assert "invalid choice" in capsys.readouterr().err

    def test_render_project_palette_overrides_builtin(self, tmp_project: Path):
        """A project config/palettes/nb.json overrides the builtin palette."""
        (tmp_project / "input" / "article.md").write_text(self._ARTICLE, encoding="utf-8")
        pdir = tmp_project / "config" / "palettes"
        pdir.mkdir(parents=True)
        nb = pdir / "nb.json"
        nb.write_text(
            json.dumps({
                "text": "#111111", "muted": "#111111", "link": "#111111",
                "h1_color": "#111111", "h2_color": "#fff", "h2_accent": "#111111",
                "h3_color": "#111111", "blockquote_border": "#111111",
                "blockquote_bg": "#f5f5f5", "code_inline_bg": "#f5f5f5",
                "code_inline_color": "#111111", "code_bg": "#f6f8fa",
                "code_border": "#eee", "bar_bg": "#f0f0f0", "bar_border": "#eee",
                "bar_text": "#111111", "copy_btn_border": "#ddd",
                "copy_btn_bg": "#fff", "copy_btn_color": "#111111",
                "table_border": "#eee", "th_bg": "#f5f5f5",
                "row_alt_bg": "#fafafa", "hr_color": "#eee", "radius": "6px",
                "code_scheme": "friendly",
                # extra tokens used by base/common partials
                "strong_color": "#0e0e0e", "em_color": "#111111",
                "list_marker_color": "#111111", "footnote_ref_color": "#111111",
                "footnote_url_color": "#576b95", "codeblock_margin": "1.2em 0",
                "bar_font": "Menlo, Consolas, monospace",
                "root_font": "-apple-system, sans-serif",
                "root_letter_spacing": "0.05em",
            }),
            encoding="utf-8",
        )
        rc = main([
            "render", "--md", "input/article.md",
            "--palette", "nb",
            "--out", "build/w.html", "--preview-out", "build/p.html",
        ])
        assert rc == 0
        html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
        assert "#0e0e0e" in html  # project override's identity color
        assert "#5b6cff" not in html  # builtin nb colors are gone
        assert "color:#ff7b72" not in html  # project palette is friendly
        assert "color:#007020" in html

    def test_publish_yaml_unknown_palette_fails_closed(self, tmp_project: Path, capsys):
        """An invalid publish.yaml palette bypasses argparse choices and is
        still rejected by the resolve_selection fail-closed path."""
        (tmp_project / "config" / "publish.yaml").write_text(
            "palette: ghost-palette\n", encoding="utf-8"
        )
        rc = main([
            "render", "--md", "input/article.md",
            "--out", "build/w.html", "--preview-out", "build/p.html",
        ])
        assert rc == 1
        assert "unknown palette 'ghost-palette'" in capsys.readouterr().err

    def test_publish_yaml_layout_palette_defaults_and_cli_override(self, tmp_project: Path):
        """publish.yaml layout/palette act as defaults; explicit CLI wins."""
        (tmp_project / "input" / "article.md").write_text(self._ARTICLE, encoding="utf-8")
        (tmp_project / "config" / "publish.yaml").write_text(
            "layout: default\npalette: nb\n", encoding="utf-8"
        )
        args = [
            "render", "--md", "input/article.md",
            "--out", "build/w.html", "--preview-out", "build/p.html",
        ]
        rc = main(args)
        assert rc == 0
        html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
        assert "#5b6cff" in html  # config default palette applied

        # CLI flag overrides the config default
        rc = main(args + ["--palette", "filling"])
        assert rc == 0
        html = (tmp_project / "build" / "w.html").read_text(encoding="utf-8")
        assert "#c0392b" in html  # filling identity color
        assert "#5b6cff" not in html

    def test_draft_dry_run_and_inspect_accept_layout_palette(self, tmp_project: Path, capsys):
        (tmp_project / "input" / "article.md").write_text(self._ARTICLE, encoding="utf-8")
        rc = main(["draft", "--md", "input/article.md", "--dry-run", "--palette", "nb"])
        assert rc == 0
        assert "DRY RUN" in capsys.readouterr().out

        rc = main(["inspect", "--md", "input/article.md", "--layout", "default"])
        assert rc == 0
        assert "Resolved Metadata" in capsys.readouterr().out
