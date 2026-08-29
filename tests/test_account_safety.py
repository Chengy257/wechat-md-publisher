"""Tests for v0.1.1 account safety: state isolation, offline dry-run, preflight.

Covers:
- account-scoped token/image/cover caches (switching appids never reuses state)
- token.json appid binding (mismatched/legacy caches are cache misses)
- quarantine of legacy pre-v0.1.1 state files
- fully offline --dry-run (no credentials, no AI keys, no network)
- preflight validation before any network request
- deprecation warnings for explicit paths.* cache keys
"""

import hashlib
import json
from pathlib import Path

import pytest
import responses
from conftest import make_png

from wechat_publish.cli import main
from wechat_publish.config import account_key, account_scoped_paths, resolve_config
from wechat_publish.draft import validate_publish_preflight
from wechat_publish.images import upload_body_image, upload_cover_image
from wechat_publish.state import quarantine_legacy_state
from wechat_publish.token import get_access_token, load_cached_token

_WX = "https://api.weixin.qq.com"


def _account_dir(state_dir: Path, appid: str) -> Path:
    key = hashlib.sha256(appid.encode("utf-8")).hexdigest()[:12]
    return state_dir / "accounts" / key


def _mock_wechat_endpoints() -> None:
    responses.add(
        responses.GET, f"{_WX}/cgi-bin/token",
        json={"access_token": "TK" * 30, "expires_in": 7200},
    )
    responses.add(
        responses.POST, f"{_WX}/cgi-bin/material/add_material",
        json={"media_id": "COVER_MEDIA_ID_123456", "url": "https://mmbiz/cover"},
    )
    responses.add(
        responses.POST, f"{_WX}/cgi-bin/media/uploadimg",
        json={"url": "https://mmbiz.qpic.cn/fig1.png"},
    )
    responses.add(
        responses.POST, f"{_WX}/cgi-bin/draft/add",
        json={"media_id": "DRAFT_MEDIA_ID_123456"},
    )


# ── account_scoped_paths shape ──────────────────────────────────

class TestAccountScopedPaths:
    def test_key_is_sha256_prefix(self):
        assert account_key("appid") == hashlib.sha256(b"appid").hexdigest()[:12]

    def test_paths_live_under_accounts_dir(self, tmp_path: Path):
        token, image, cover = account_scoped_paths(tmp_path, "appid_a")
        expected = tmp_path / "accounts" / account_key("appid_a")
        assert token == expected / "token.json"
        assert image == expected / "image_cache.json"
        assert cover == expected / "cover_cache.json"

    def test_different_appids_get_different_dirs(self, tmp_path: Path):
        a = account_scoped_paths(tmp_path, "appid_a")
        b = account_scoped_paths(tmp_path, "appid_b")
        assert a[0].parent != b[0].parent


# ── account isolation over the real cache APIs ──────────────────

class TestAccountIsolation:
    @responses.activate
    def test_switching_appid_never_reuses_account_state(self, tmp_path: Path):
        state_dir = tmp_path / ".wechat_publish"
        cover = make_png(tmp_path / "cover.png")
        fig = make_png(tmp_path / "fig1.png")
        _mock_wechat_endpoints()

        # Account A: token + cover + body image caches under A's namespace
        token_path_a, image_path_a, cover_path_a = account_scoped_paths(
            state_dir, "appid_a"
        )
        token_a = get_access_token("appid_a", "secret_a", token_path_a)
        upload_cover_image(token_a.value, cover, cover_path_a)
        upload_body_image(token_a.value, fig, image_path_a)

        assert token_path_a.exists()
        assert cover_path_a.exists()
        assert image_path_a.exists()
        uploads_so_far = len(responses.calls)

        # Account B: completely separate namespace
        token_path_b, image_path_b, cover_path_b = account_scoped_paths(
            state_dir, "appid_b"
        )
        assert token_path_b.parent != token_path_a.parent

        # B cannot read A's token even when pointed at A's cache path
        assert load_cached_token(token_path_a, expected_appid="appid_b") is None
        assert load_cached_token(token_path_a, expected_appid="appid_a") is not None

        # B's token fetch actually goes to the network (no reuse of A's token)
        token_b = get_access_token("appid_b", "secret_b", token_path_b)
        assert token_b.value == token_a.value  # same mocked upstream value
        assert len(responses.calls) == uploads_so_far + 1  # a new token request

        # B re-uploading the same cover does NOT hit A's cover cache:
        # a real add_material request must be issued again.
        upload_cover_image(token_b.value, cover, cover_path_b)
        assert len(responses.calls) == uploads_so_far + 2

        # Both account namespaces exist on disk with their own state files
        assert (state_dir / "accounts").is_dir()
        assert token_path_a.parent.is_dir()
        assert token_path_b.parent.is_dir()
        assert sorted(p.name for p in (state_dir / "accounts").iterdir()) == sorted(
            [account_key("appid_a"), account_key("appid_b")]
        )
        # Each namespace keeps its own cover cache file
        assert cover_path_a.exists() and cover_path_b.exists()
        assert cover_path_a.parent != cover_path_b.parent

    @responses.activate
    def test_token_cache_binds_appid_and_ignores_mismatch(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        _mock_wechat_endpoints()

        token_a = get_access_token("appid_a", "secret", cache)
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert data["appid"] == "appid_a"
        assert data["access_token"] == token_a.value

        # Same appid: cache hit, no network
        assert get_access_token("appid_a", "secret", cache).value == token_a.value
        assert len(responses.calls) == 1

        # Different appid on the same path: cache is ignored, token re-acquired
        token_b = get_access_token("appid_b", "secret", cache)
        assert len(responses.calls) == 2
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert data["appid"] == "appid_b"
        assert token_b.value == token_a.value

    def test_legacy_cache_without_appid_is_a_cache_miss(self, tmp_path: Path):
        cache = tmp_path / "token.json"
        cache.write_text(
            json.dumps({"access_token": "LEGACY" * 8, "expires_at": 2**31}),
            encoding="utf-8",
        )
        assert load_cached_token(cache, expected_appid="appid_a") is None
        # Without an expected appid the raw load still works (unit-level)
        assert load_cached_token(cache).value == "LEGACY" * 8


# ── legacy state quarantine ─────────────────────────────────────

class TestQuarantineLegacyState:
    def test_moves_legacy_files_without_reading_them(self, tmp_path: Path):
        legacy_token = b'{"access_token": "OLD", "expires_at": 1}'
        legacy_image = b'{"abc": "https://old"}'
        legacy_cover = b'{"def": "MEDIA_OLD"}'
        (tmp_path / "token.json").write_bytes(legacy_token)
        (tmp_path / "image_cache.json").write_bytes(legacy_image)
        (tmp_path / "cover_cache.json").write_bytes(legacy_cover)

        quarantine_legacy_state(tmp_path)

        legacy_dir = tmp_path / "legacy"
        assert (legacy_dir / "token.json").read_bytes() == legacy_token
        assert (legacy_dir / "image_cache.json").read_bytes() == legacy_image
        assert (legacy_dir / "cover_cache.json").read_bytes() == legacy_cover
        assert not (tmp_path / "token.json").exists()
        assert not (tmp_path / "image_cache.json").exists()
        assert not (tmp_path / "cover_cache.json").exists()

    def test_idempotent_rerun_is_safe(self, tmp_path: Path):
        (tmp_path / "token.json").write_text("{}", encoding="utf-8")
        quarantine_legacy_state(tmp_path)
        quarantine_legacy_state(tmp_path)  # second run: nothing left to move
        assert (tmp_path / "legacy" / "token.json").exists()

    def test_noop_without_legacy_files(self, tmp_path: Path, capsys):
        quarantine_legacy_state(tmp_path)
        assert not (tmp_path / "legacy").exists()
        assert "legacy" not in capsys.readouterr().out

    def test_warns_when_files_moved(self, tmp_path: Path, capsys):
        (tmp_path / "token.json").write_text("{}", encoding="utf-8")
        quarantine_legacy_state(tmp_path)
        assert "[WARN] legacy pre-v0.1.1 state moved to" in capsys.readouterr().out

    @responses.activate
    def test_publish_quarantines_legacy_state_before_token(self, tmp_project: Path):
        state_dir = tmp_project / ".wechat_publish"
        state_dir.mkdir()
        (state_dir / "token.json").write_text(
            json.dumps({"access_token": "OLD", "expires_at": 1}), encoding="utf-8"
        )
        make_png(tmp_project / "input" / "cover.png")
        article = tmp_project / "input" / "article.md"
        article.write_text(
            '---\ntitle: "T"\n---\n\nBody.\n', encoding="utf-8"
        )
        _mock_wechat_endpoints()

        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 0
        # Legacy file was moved aside, not trusted or reused
        assert (state_dir / "legacy" / "token.json").exists()
        assert not (state_dir / "token.json").exists()
        # A fresh token was fetched into the account-scoped path
        token_path, _, _ = account_scoped_paths(state_dir, "wx_test_appid")
        assert token_path.exists()


# ── fully offline dry-run ────────────────────────────────────────

@pytest.fixture
def offline_project(tmp_path: Path, monkeypatch) -> Path:
    """A project with zero credentials and network/AI calls wired to fail."""
    (tmp_path / "config").mkdir()
    article = tmp_path / "input" / "article.md"
    article.parent.mkdir(parents=True)
    article.write_text(
        '---\ntitle: "离线文章"\nauthor: "Cy257"\n---\n\n# Hello\n\nBody.\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for var in (
        "WECHAT_APPID", "WECHAT_APP_ID",
        "WECHAT_APPSECRET", "WECHAT_APP_SECRET",
        "AI_API_KEY", "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    def _no_network(*args, **kwargs):
        raise AssertionError("network/remote call attempted during dry-run")

    monkeypatch.setattr("requests.request", _no_network)
    monkeypatch.setattr("requests.post", _no_network)
    monkeypatch.setattr("requests.get", _no_network)
    monkeypatch.setattr("wechat_publish.token.request_access_token", _no_network)
    monkeypatch.setattr("wechat_publish.images.upload_cover_image", _no_network)
    monkeypatch.setattr("wechat_publish.images.upload_body_image", _no_network)
    monkeypatch.setattr("wechat_publish.draft.add_draft", _no_network)
    monkeypatch.setattr("wechat_publish.ai_summary.generate_digest", _no_network)
    monkeypatch.setattr("wechat_publish.ai_cover.generate_cover_image", _no_network)
    return tmp_path


class TestOfflineDryRun:
    def test_dry_run_without_credentials_returns_zero(
        self, offline_project: Path, capsys
    ):
        rc = main(["draft", "--md", "input/article.md", "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== DRY RUN ===" in out
        assert "appid:    ***" in out

    def test_dry_run_with_ai_flags_makes_zero_calls(
        self, offline_project: Path, capsys
    ):
        rc = main([
            "draft", "--md", "input/article.md", "--dry-run",
            "--ai-summary", "--ai-cover",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(AI digest would be generated on a real run)" in out
        assert "(AI cover would be generated on a real run)" in out

    def test_dry_run_with_mermaid_api_engine_makes_zero_requests(
        self, offline_project: Path, capsys
    ):
        article = offline_project / "input" / "article.md"
        article.write_text(
            '---\ntitle: "Mermaid 离线"\n---\n\n'
            "```mermaid\ngraph TD\n  A --> B\n```\n\nBody.\n",
            encoding="utf-8",
        )
        rc = main([
            "draft", "--md", "input/article.md", "--dry-run",
            "--mermaid", "--mermaid-engine", "api",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "mermaid rendering skipped (would run on a real publish)" in out

    def test_dry_run_does_not_create_state_dir(self, offline_project: Path):
        rc = main(["draft", "--md", "input/article.md", "--dry-run"])
        assert rc == 0
        assert not (offline_project / ".wechat_publish").exists()


# ── preflight validation before any network request ─────────────

class TestPreflightBeforeNetwork:
    def test_title_too_long_fails_before_any_upload(
        self, offline_project: Path, monkeypatch, capsys
    ):
        monkeypatch.setenv("WECHAT_APPID", "wx_test_appid")
        monkeypatch.setenv("WECHAT_APPSECRET", "test_secret")
        rc = main([
            "draft", "--md", "input/article.md", "--title", "标" * 65,
        ])
        assert rc == 1
        assert "title too long" in capsys.readouterr().err

    def test_digest_too_long_fails_before_any_upload(
        self, offline_project: Path, monkeypatch, capsys
    ):
        monkeypatch.setenv("WECHAT_APPID", "wx_test_appid")
        monkeypatch.setenv("WECHAT_APPSECRET", "test_secret")
        rc = main([
            "draft", "--md", "input/article.md", "--digest", "d" * 121,
        ])
        assert rc == 1
        assert "Digest too long" in capsys.readouterr().err

    def test_missing_cover_fails_before_any_upload(
        self, offline_project: Path, monkeypatch, capsys
    ):
        monkeypatch.setenv("WECHAT_APPID", "wx_test_appid")
        monkeypatch.setenv("WECHAT_APPSECRET", "test_secret")
        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 1
        assert "Cover image not found" in capsys.readouterr().err

    def test_missing_cover_passes_preflight_with_ai_cover(
        self, offline_project: Path, monkeypatch, capsys
    ):
        # With --ai-cover the cover is generated later; preflight must not
        # demand an existing cover file (and dry-run prints the AI note).
        rc = main([
            "draft", "--md", "input/article.md", "--dry-run", "--ai-cover",
        ])
        assert rc == 0
        assert "(AI cover would be generated on a real run)" in capsys.readouterr().out


class TestValidatePublishPreflight:
    def _check(self, **overrides):
        fields = dict(
            title="T",
            author="Cy",
            digest="d",
            need_open_comment=1,
            only_fans_can_comment=0,
            content_source_url="",
            content_html="x" * 100,
            cover_path=None,
        )
        fields.update(overrides)
        validate_publish_preflight(**fields)

    def test_valid_inputs_pass(self, tmp_path: Path):
        cover = tmp_path / "cover.png"
        cover.write_bytes(b"\x89PNG")
        self._check(cover_path=cover)

    def test_empty_title_rejected(self):
        with pytest.raises(ValueError, match="title must not be empty"):
            self._check(title="")

    def test_title_over_limit_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            self._check(title="x" * 65)

    def test_author_over_limit_rejected(self):
        with pytest.raises(ValueError, match="Author name too long"):
            self._check(author="一" * 9)

    def test_digest_over_limit_rejected(self):
        with pytest.raises(ValueError, match="Digest too long"):
            self._check(digest="d" * 121)

    def test_comment_flag_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="need_open_comment"):
            self._check(need_open_comment=2)
        with pytest.raises(ValueError, match="only_fans_can_comment"):
            self._check(only_fans_can_comment=-1)

    def test_source_url_scheme_rejected(self):
        with pytest.raises(ValueError, match="http"):
            self._check(content_source_url="ftp://example.com")
        with pytest.raises(ValueError, match="http"):
            self._check(content_source_url="example.com/post")

    def test_html_over_documented_limit_warns_but_passes(self, capsys):
        self._check(content_html="x" * 20_001)
        assert "documented 20k-char draft limit" in capsys.readouterr().out

    def test_html_over_byte_hard_limit_rejected(self):
        with pytest.raises(ValueError, match="content too large"):
            self._check(content_html="x" * 1_000_001)

    def test_missing_cover_file_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Cover image not found"):
            self._check(cover_path=tmp_path / "nope.png")


# ── deprecated explicit cache path keys ─────────────────────────

class TestDeprecatedPathsKeys:
    def test_deprecation_warning_and_old_field_parsing(self, capsys):
        cfg = resolve_config(
            cli_values={},
            front_matter={},
            publish_config={
                "paths": {"state_dir": ".st", "token_cache": "custom/token.json"},
            },
            env={},
        )
        err = capsys.readouterr().err
        assert "[WARN] paths.token_cache is deprecated since v0.1.1" in err
        assert "account-scoped" in err
        # The field is still parsed the old way (unused by the publish stage)
        assert cfg.token_cache == Path("custom/token.json")

    @responses.activate
    def test_publish_uses_account_scoped_paths_not_explicit_ones(
        self, tmp_project: Path, capsys
    ):
        (tmp_project / "config" / "publish.yaml").write_text(
            "paths:\n"
            "  token_cache: custom/token.json\n"
            "  image_cache: custom/image_cache.json\n"
            "  cover_cache: custom/cover_cache.json\n",
            encoding="utf-8",
        )
        make_png(tmp_project / "input" / "cover.png")
        article = tmp_project / "input" / "article.md"
        article.write_text('---\ntitle: "T"\n---\n\nBody.\n', encoding="utf-8")
        _mock_wechat_endpoints()

        rc = main(["draft", "--md", "input/article.md"])
        assert rc == 0

        err = capsys.readouterr().err
        for key in ("token_cache", "image_cache", "cover_cache"):
            assert f"paths.{key} is deprecated since v0.1.1" in err

        state_dir = tmp_project / ".wechat_publish"
        token_path, image_path, cover_path = account_scoped_paths(
            state_dir, "wx_test_appid"
        )
        assert token_path.exists()
        assert cover_path.exists()
        # The explicitly configured legacy locations were never used
        assert not (tmp_project / "custom" / "token.json").exists()
