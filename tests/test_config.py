"""Configuration tests: YAML loading, precedence, front matter, credentials."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from wechat_publish.config import (
    ArticleMetadata,
    PublisherConfig,
    load_publish_config,
    load_theme_css,
    resolve_config,
    resolve_credentials,
    resolve_style_path,
)


# ── YAML loading ────────────────────────────────────────────────

class TestLoadPublishConfig:
    def test_loads_valid_yaml(self, tmp_path: Path):
        cfg = tmp_path / "publish.yaml"
        cfg.write_text("default_author: 'Cy257'\ndefault_mode: 'draft'\n")
        result = load_publish_config(cfg)
        assert result["default_author"] == "Cy257"
        assert result["default_mode"] == "draft"

    def test_returns_empty_for_missing_file(self, tmp_path: Path):
        result = load_publish_config(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_returns_empty_for_non_dict(self, tmp_path: Path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("- item1\n- item2\n")
        result = load_publish_config(cfg)
        assert result == {}


class TestLoadThemeCss:
    def test_loads_css_file(self, tmp_path: Path):
        css = tmp_path / "theme.css"
        css.write_text(".wechat-content h1 { font-size: 24px; }")
        result = load_theme_css(css)
        assert "font-size: 24px" in result

    def test_returns_empty_for_missing(self, tmp_path: Path):
        assert load_theme_css(tmp_path / "nope.css") == ""


# ── Precedence resolution ───────────────────────────────────────

class TestResolveConfig:
    def _make_config(self, **overrides):
        base = {
            "default_author": "YamlAuthor",
            "default_mode": "draft",
            "article": {"cover": "yaml_cover.png"},
            "paths": {"build_dir": "build", "state_dir": ".state"},
        }
        base.update(overrides)
        return base

    def test_cli_overrides_all(self):
        cfg = resolve_config(
            cli_values={"title": "CLI Title", "author": "CLI Author", "digest": "CLI Digest"},
            front_matter={"title": "FM Title", "author": "FM Author"},
            publish_config=self._make_config(),
            env={"WECHAT_DEFAULT_AUTHOR": "EnvAuthor"},
        )
        assert cfg.article.title == "CLI Title"
        assert cfg.article.author == "CLI Author"
        assert cfg.article.digest == "CLI Digest"

    def test_front_matter_overrides_yaml(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={"title": "FM Title", "author": "FM Author"},
            publish_config=self._make_config(),
            env={},
        )
        assert cfg.article.title == "FM Title"
        assert cfg.article.author == "FM Author"

    def test_yaml_overrides_env(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={},
            publish_config=self._make_config(),
            env={"WECHAT_DEFAULT_AUTHOR": "EnvAuthor"},
        )
        assert cfg.article.author == "YamlAuthor"

    def test_env_fallback(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={},
            publish_config={"article": {}, "paths": {}},
            env={"WECHAT_DEFAULT_AUTHOR": "EnvAuthor"},
        )
        assert cfg.article.author == "EnvAuthor"

    def test_digest_from_summary(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={"summary": "This is the summary"},
            publish_config={"article": {}, "paths": {}},
            env={},
        )
        assert cfg.article.digest == "This is the summary"

    def test_cover_from_cli(self):
        cfg = resolve_config(
            cli_values={"cover": "cli_cover.png"},
            front_matter={},
            publish_config=self._make_config(),
            env={},
        )
        assert cfg.article.cover == Path("cli_cover.png")

    def test_cover_from_yaml(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={},
            publish_config=self._make_config(),
            env={},
        )
        assert cfg.article.cover == Path("yaml_cover.png")

    def test_default_cover(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={},
            publish_config={"article": {}, "paths": {}},
            env={},
        )
        assert cfg.article.cover == Path("input/cover.png")


# ── Credentials ─────────────────────────────────────────────────

class TestResolveCredentials:
    def test_from_env(self):
        pub_cfg = {}
        env = {"WECHAT_APPID": "my_appid", "WECHAT_APPSECRET": "my_secret"}
        appid, secret = resolve_credentials(pub_cfg, env)
        assert appid == "my_appid"
        assert secret == "my_secret"

    def test_alternative_env_names(self):
        pub_cfg = {}
        env = {"WECHAT_APP_ID": "alt_id", "WECHAT_APP_SECRET": "alt_secret"}
        appid, secret = resolve_credentials(pub_cfg, env)
        assert appid == "alt_id"
        assert secret == "alt_secret"

    def test_config_env_hints(self):
        pub_cfg = {
            "wechat": {
                "appid_env": ["CUSTOM_APPID"],
                "appsecret_env": ["CUSTOM_SECRET"],
            }
        }
        env = {"CUSTOM_APPID": "custom_id", "CUSTOM_SECRET": "custom_secret"}
        appid, secret = resolve_credentials(pub_cfg, env)
        assert appid == "custom_id"
        assert secret == "custom_secret"

    def test_missing_returns_empty(self):
        appid, secret = resolve_credentials({}, {})
        assert appid == ""
        assert secret == ""


# ── Theme resolution ─────────────────────────────────────────────

class TestResolveStylePath:
    def test_style_arg_takes_priority(self, tmp_path: Path):
        custom = tmp_path / "custom.css"
        custom.write_text("h1 { color: red; }")
        result = resolve_style_path(style_arg=custom, theme_arg="default", project_dir=tmp_path)
        assert result == custom

    def test_theme_loads_builtin(self):
        result = resolve_style_path(style_arg=None, theme_arg="default", project_dir=Path("/tmp"))
        assert result.name == "default.css"
        assert "styles" in str(result)

    def test_theme_elegant(self):
        result = resolve_style_path(style_arg=None, theme_arg="elegant", project_dir=Path("/tmp"))
        assert result.name == "elegant.css"

    def test_theme_simple(self):
        result = resolve_style_path(style_arg=None, theme_arg="simple", project_dir=Path("/tmp"))
        assert result.name == "simple.css"

    def test_theme_tech(self):
        result = resolve_style_path(style_arg=None, theme_arg="tech", project_dir=Path("/tmp"))
        assert result.name == "tech.css"

    def test_unknown_theme_falls_back(self, tmp_path: Path):
        result = resolve_style_path(style_arg=None, theme_arg=None, project_dir=tmp_path)
        assert result == tmp_path / "config" / "style.css"

    def test_none_args_fall_back(self, tmp_path: Path):
        result = resolve_style_path(style_arg=None, theme_arg=None, project_dir=tmp_path)
        assert result == tmp_path / "config" / "style.css"
