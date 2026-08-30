"""Configuration tests: YAML loading, precedence, front matter, credentials."""

import json
from pathlib import Path

import pytest

from wechat_publish.config import (
    load_preset_css,
    load_publish_config,
    load_theme_css,
    normalize_string_or_list,
    resolve_config,
    resolve_credentials,
    resolve_style_path,
    resolve_theme_css,
)
from wechat_publish.theme_engine import (
    list_palettes,
    load_palette,
    render_css,
    resolve_selection,
)

# ── YAML loading ────────────────────────────────────────────────

class TestLoadPublishConfig:
    def test_loads_valid_yaml(self, tmp_path: Path):
        cfg = tmp_path / "publish.yaml"
        cfg.write_text("default_author: 'Cy257'\ndefault_mode: 'draft'\n")
        result = load_publish_config(cfg)
        assert result["default_author"] == "Cy257"
        assert result["default_mode"] == "draft"

    def test_returns_empty_for_missing_file(self, tmp_path: Path, capsys):
        result = load_publish_config(tmp_path / "nonexistent.yaml")
        assert result == {}
        stderr = capsys.readouterr().err
        assert "[INFO] no publish config at" in stderr
        assert "built-in defaults" in stderr

    def test_does_not_fall_back_to_example(self, tmp_path: Path, capsys):
        """The example file is documentation-only and never read at runtime."""
        example = tmp_path / "publish.example.yaml"
        example.write_text("default_author: 'ExampleAuthor'\n")
        missing = tmp_path / "publish.yaml"
        result = load_publish_config(missing)
        assert result == {}
        assert "default_author" not in result
        stderr = capsys.readouterr().err
        assert "[INFO] no publish config at" in stderr

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


# ── String-or-list normalization ────────────────────────────────

class TestNormalizeStringOrList:
    def test_string_wrapped_in_list(self):
        assert normalize_string_or_list("WECHAT_APP_ID", field="wechat.appid_env") == [
            "WECHAT_APP_ID"
        ]

    def test_list_passthrough(self):
        assert normalize_string_or_list(["A", "B"], field="f") == ["A", "B"]

    def test_tuple_converted_to_list(self):
        assert normalize_string_or_list(("A", "B"), field="f") == ["A", "B"]

    def test_default_tuple_style_values(self):
        assert normalize_string_or_list(
            ("WECHAT_APPID", "WECHAT_APP_ID"), field="wechat.appid_env"
        ) == ["WECHAT_APPID", "WECHAT_APP_ID"]

    def test_int_rejected(self):
        with pytest.raises(ValueError, match="appid_env"):
            normalize_string_or_list(123, field="wechat.appid_env")

    def test_nested_list_rejected(self):
        with pytest.raises(ValueError, match="appid_env"):
            normalize_string_or_list(["A", ["B"]], field="wechat.appid_env")

    def test_empty_string_in_list_rejected(self):
        with pytest.raises(ValueError, match="f"):
            normalize_string_or_list(["A", ""], field="f")

    def test_none_rejected(self):
        with pytest.raises(ValueError, match="must be a string"):
            normalize_string_or_list(None, field="f")


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

    def test_string_env_hint_is_normalized(self):
        """A user writing `appid_env: WECHAT_APP_ID` (plain string) works."""
        pub_cfg = {"wechat": {"appid_env": "WECHAT_APP_ID"}}
        env = {"WECHAT_APP_ID": "str_hint_id"}
        appid, _ = resolve_credentials(pub_cfg, env)
        assert appid == "str_hint_id"

    def test_string_appsecret_env_hint(self):
        pub_cfg = {"wechat": {"appsecret_env": "CUSTOM_SECRET"}}
        env = {"CUSTOM_SECRET": "custom"}
        _, secret = resolve_credentials(pub_cfg, env)
        assert secret == "custom"

    def test_int_env_hint_raises_clean_error(self):
        with pytest.raises(ValueError, match="appid_env"):
            resolve_credentials({"wechat": {"appid_env": 123}}, {})

    def test_nested_list_env_hint_raises_clean_error(self):
        with pytest.raises(ValueError, match="appsecret_env"):
            resolve_credentials(
                {"wechat": {"appsecret_env": ["A", ["B"]]}}, {}
            )

    def test_string_author_env_hint(self):
        cfg = resolve_config(
            cli_values={},
            front_matter={},
            publish_config={"wechat": {"author_env": "MY_AUTHOR"}},
            env={"MY_AUTHOR": "EnvAuthor"},
        )
        assert cfg.article.author == "EnvAuthor"

    def test_int_author_env_hint_raises(self):
        with pytest.raises(ValueError, match="author_env"):
            resolve_config(
                cli_values={},
                front_matter={},
                publish_config={"wechat": {"author_env": 42}},
                env={},
            )


# ── Packaging guard ─────────────────────────────────────────────

class TestPackagingDependencies:
    """Guard: runtime imports are declared; dev extra covers the test suite."""

    def _pyproject_text(self) -> str:
        root = Path(__file__).resolve().parent.parent
        return (root / "pyproject.toml").read_text(encoding="utf-8")

    def test_runtime_deps_declare_pygments_and_cssutils(self):
        text = self._pyproject_text()
        assert "Pygments>=2.13" in text
        assert "cssutils>=2.9" in text

    def test_dev_extra_declares_pillow(self):
        text = self._pyproject_text()
        dev_start = text.index("dev = [")
        dev_block = text[dev_start:text.index("]", dev_start)]
        assert "Pillow>=10.0" in dev_block


# ── Theme resolution ─────────────────────────────────────────────

class TestResolveStylePath:
    def test_style_arg_takes_priority(self, tmp_path: Path):
        custom = tmp_path / "custom.css"
        custom.write_text("h1 { color: red; }")
        result = resolve_style_path(style_arg=custom, theme_arg="default", project_dir=tmp_path)
        assert result == custom

    def test_theme_loads_builtin(self):
        # Theme presets are engine-rendered: resolve_style_path yields None
        # and the CSS comes from load_preset_css.
        result = resolve_style_path(style_arg=None, theme_arg="default", project_dir=Path("/tmp"))
        assert result is None
        css = load_preset_css("default")
        assert ".wechat-content" in css

    def test_theme_elegant(self):
        assert resolve_style_path(style_arg=None, theme_arg="elegant", project_dir=Path("/tmp")) is None
        assert "#3498db" in load_preset_css("elegant")

    def test_theme_simple(self):
        assert resolve_style_path(style_arg=None, theme_arg="simple", project_dir=Path("/tmp")) is None
        assert "#2c3e50" in load_preset_css("simple")

    def test_theme_tech(self):
        assert resolve_style_path(style_arg=None, theme_arg="tech", project_dir=Path("/tmp")) is None
        assert "#0d1117" in load_preset_css("tech")

    def test_project_style_css_wins_over_builtin(self, tmp_path: Path):
        project_style = tmp_path / "config" / "style.css"
        project_style.parent.mkdir(parents=True)
        project_style.write_text("h1 { color: red; }")
        result = resolve_style_path(style_arg=None, theme_arg=None, project_dir=tmp_path)
        assert result == project_style

    def test_no_theme_no_project_style_uses_engine_default(self, tmp_path: Path):
        # No --style/--theme and no project style sheet: resolve_style_path
        # yields None and resolve_theme_css falls back to the engine default.
        result = resolve_style_path(style_arg=None, theme_arg=None, project_dir=tmp_path)
        assert result is None
        css = resolve_theme_css(style_arg=None, theme_arg=None, project_dir=tmp_path)
        assert ".wechat-content" in css


# ── 配色 × 版式: resolve_selection / project palettes ────────────


def _minimal_palette(**overrides: str) -> dict[str, str]:
    """A palette dict passing load_palette validation (test extras allowed)."""
    from wechat_publish import theme_engine

    palette = {key: "#000" for key in theme_engine.PALETTE_REQUIRED_KEYS}
    palette["code_scheme"] = "friendly"
    palette.update(overrides)
    return palette


class TestResolveSelection:
    """Unified --style > --layout/--palette > --theme > default resolution."""

    def test_style_file_wins_and_palette_is_none(self, tmp_path: Path):
        style = tmp_path / "custom.css"
        style.write_text("/* custom */ h1 { color: red; }", encoding="utf-8")
        css, palette = resolve_selection(
            style_arg=style, layout_arg="default", palette_arg="nb",
            theme_arg="nb", project_dir=tmp_path,
        )
        assert css == "/* custom */ h1 { color: red; }"
        assert palette is None  # code background unknown -> pygments friendly

    def test_layout_palette_pair_renders_via_engine(self, tmp_path: Path):
        css, palette = resolve_selection(
            style_arg=None, layout_arg="default", palette_arg="nb",
            theme_arg=None, project_dir=tmp_path,
        )
        assert css == render_css("default", "nb")
        assert palette is not None
        assert palette["_source"] == "builtin"
        assert palette["code_scheme"] == "github-dark"

    def test_omitted_side_of_the_pair_defaults(self, tmp_path: Path):
        css, palette = resolve_selection(
            style_arg=None, layout_arg=None, palette_arg="lapis",
            theme_arg=None, project_dir=tmp_path,
        )
        assert css == render_css("default", "lapis")
        assert palette is not None and palette["code_scheme"] == "github-dark"

    def test_theme_preset_path_uses_preset_palette(self):
        css, palette = resolve_selection(
            style_arg=None, layout_arg=None, palette_arg=None,
            theme_arg="lapis", project_dir=None,
        )
        assert css == load_preset_css("lapis")
        assert palette is not None
        assert palette["code_scheme"] == "github-dark"

    def test_no_args_falls_back_to_engine_default(self, tmp_path: Path):
        css, palette = resolve_selection(
            style_arg=None, layout_arg=None, palette_arg=None,
            theme_arg=None, project_dir=tmp_path,
        )
        assert css == load_preset_css("default")
        assert palette is not None and palette["_source"] == "builtin"

    def test_no_args_project_style_sheet_still_wins(self, tmp_path: Path):
        project_style = tmp_path / "config" / "style.css"
        project_style.parent.mkdir(parents=True)
        project_style.write_text("/* project style */", encoding="utf-8")
        css, palette = resolve_selection(
            style_arg=None, layout_arg=None, palette_arg=None,
            theme_arg=None, project_dir=tmp_path,
        )
        assert css == "/* project style */"
        assert palette is None

    def test_unknown_theme_fails_closed(self):
        with pytest.raises(ValueError, match="unknown theme preset 'nope'"):
            resolve_selection(
                style_arg=None, layout_arg=None, palette_arg=None,
                theme_arg="nope", project_dir=None,
            )

    def test_unknown_layout_fails_closed(self):
        with pytest.raises(ValueError, match="unknown layout 'nope'"):
            resolve_selection(
                style_arg=None, layout_arg="nope", palette_arg="default",
                theme_arg=None, project_dir=None,
            )

    def test_unknown_palette_fails_closed(self):
        with pytest.raises(ValueError, match="unknown palette 'nope'"):
            resolve_selection(
                style_arg=None, layout_arg=None, palette_arg="nope",
                theme_arg=None, project_dir=None,
            )

    def test_all_registered_layouts_resolve(self):
        from wechat_publish.theme_engine import BUILTIN_LAYOUTS

        # All five builtin layouts are implemented; each resolves through the
        # engine path (the old placeholder "版式文件未实现" fail-closed branch
        # is kept in render_css for any future unimplemented layout).
        for name in BUILTIN_LAYOUTS:
            css, palette = resolve_selection(
                style_arg=None, layout_arg=name, palette_arg="default",
                theme_arg=None, project_dir=None,
            )
            assert css.strip(), name
            assert palette is not None and palette["_source"] == "builtin", name


class TestProjectPalettes:
    """<project>/config/palettes/*.json discovery and same-name override."""

    def _write_project_palette(self, project_dir: Path, name: str, data: dict) -> Path:
        pdir = project_dir / "config" / "palettes"
        pdir.mkdir(parents=True, exist_ok=True)
        path = pdir / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_project_palette_overrides_builtin_same_name(self, tmp_path: Path):
        from wechat_publish import theme_engine

        builtin_path = theme_engine._PALETTES_DIR / "nb.json"
        data = json.loads(builtin_path.read_text(encoding="utf-8"))
        data["strong_color"] = "#123456"
        self._write_project_palette(tmp_path, "nb", data)

        palette = load_palette("nb", project_dir=tmp_path)
        assert palette["strong_color"] == "#123456"
        assert palette["_source"] == "project"
        # The builtin palette is untouched.
        assert load_palette("nb")["_source"] == "builtin"
        assert load_palette("nb")["strong_color"] != "#123456"

    def test_project_extra_palette_is_discovered(self, tmp_path: Path):
        self._write_project_palette(
            tmp_path, "corp", _minimal_palette(strong_color="#abcdef")
        )
        assert "corp" in list_palettes(tmp_path)
        assert "corp" not in list_palettes()
        palette = load_palette("corp", project_dir=tmp_path)
        assert palette["_source"] == "project"

    def test_project_palette_missing_key_fails_closed(self, tmp_path: Path):
        self._write_project_palette(tmp_path, "broken", {"text": "#000"})
        with pytest.raises(ValueError, match="missing required key"):
            load_palette("broken", project_dir=tmp_path)

    def test_list_layouts_covers_registry(self):
        from wechat_publish.theme_engine import BUILTIN_LAYOUTS, list_layouts

        assert list_layouts() == sorted(BUILTIN_LAYOUTS)
        assert set(BUILTIN_LAYOUTS) == {
            "default", "serif", "terminal", "card", "classic",
        }
