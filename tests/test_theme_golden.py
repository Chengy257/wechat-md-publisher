"""Golden baseline + theme engine unit tests.

The golden baseline freezes the byte-exact WeChat HTML output of the full
pipeline for every ``--theme`` preset on the representative sample
``tests/fixtures/golden_sample.md``. It was generated on the clean tree
BEFORE the theme-engine refactor (against the retired per-theme CSS files)
via ``scripts/make_theme_golden.py``; these tests prove the engine renders
byte-identical output.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from wechat_publish import theme_engine
from wechat_publish.config import (
    BUILTIN_THEMES,
    THEME_PRESETS,
    load_preset_css,
    resolve_theme_css,
)
from wechat_publish.html_processor import process_article_html
from wechat_publish.render import (
    pygments_style_for_palette,
    pygments_style_for_theme,
    render_markdown_to_html,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "theme_golden.json"
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "golden_sample.md"


def _render_theme_hash(theme: str) -> str:
    """Render the golden sample through the engine path and hash the result."""
    theme_css = resolve_theme_css(style_arg=None, theme_arg=theme, project_dir=ROOT)
    assert theme_css, f"theme '{theme}' must resolve to non-empty CSS"
    raw_html = render_markdown_to_html(
        SAMPLE_PATH.read_text(encoding="utf-8"),
        pygments_style=pygments_style_for_theme(theme),
    )
    wechat_html = process_article_html(raw_html, theme_css)
    return hashlib.sha256(wechat_html.encode("utf-8")).hexdigest()


# ── Golden baseline (8/8 byte-equality) ──────────────────────────


class TestGoldenBaseline:
    @pytest.mark.parametrize("theme", sorted(BUILTIN_THEMES))
    def test_preset_renders_golden_wechat_html(self, theme: str):
        expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))[theme]
        assert _render_theme_hash(theme) == expected

    def test_fixture_covers_exactly_the_builtin_themes(self):
        expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert set(expected) == set(BUILTIN_THEMES)
        assert len(expected) == 8


def _full_palette(**extra: str) -> dict[str, str]:
    """A palette that passes load_palette validation, with test extras."""
    palette = {key: "#000" for key in theme_engine.PALETTE_REQUIRED_KEYS}
    palette["code_scheme"] = "friendly"
    palette.update(extra)
    return palette


# ── load_palette ─────────────────────────────────────────────────


class TestLoadPalette:
    def test_builtin_palettes_are_valid(self):
        for theme in sorted(BUILTIN_THEMES):
            palette = theme_engine.load_palette(theme)
            for key in theme_engine.PALETTE_REQUIRED_KEYS:
                assert key in palette, f"{theme}: missing {key}"
            assert palette["code_scheme"] in theme_engine.VALID_CODE_SCHEMES

    def test_unknown_palette_fails_closed(self):
        with pytest.raises(ValueError, match="unknown palette 'nope'"):
            theme_engine.load_palette("nope")

    def test_missing_required_key_fails_closed(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(theme_engine, "_PALETTES_DIR", tmp_path)
        (tmp_path / "broken.json").write_text(
            json.dumps({"text": "#000"}), encoding="utf-8"
        )
        with pytest.raises(ValueError) as excinfo:
            theme_engine.load_palette("broken")
        message = str(excinfo.value)
        assert "broken" in message
        assert "link" in message  # a required key absent from the file

    def test_invalid_code_scheme_fails_closed(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(theme_engine, "_PALETTES_DIR", tmp_path)
        palette = {key: "#000" for key in theme_engine.PALETTE_REQUIRED_KEYS}
        palette["code_scheme"] = "monokai"
        (tmp_path / "weird.json").write_text(json.dumps(palette), encoding="utf-8")
        with pytest.raises(ValueError, match="code_scheme"):
            theme_engine.load_palette("weird")

    def test_invalid_json_fails_closed(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(theme_engine, "_PALETTES_DIR", tmp_path)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            theme_engine.load_palette("bad")


# ── render_preset_css ────────────────────────────────────────────


@pytest.fixture()
def engine_sandbox(tmp_path: Path, monkeypatch):
    """Isolate the engine's template directories behind a tmp sandbox.

    Yields a helper to register presets and write templates; the engine's
    template cache is cleared around each test.
    """
    themes_dir = tmp_path / "themes"
    partials_dir = themes_dir / "partials"
    palettes_dir = tmp_path / "palettes"
    partials_dir.mkdir(parents=True)
    palettes_dir.mkdir()

    monkeypatch.setattr(theme_engine, "_THEMES_DIR", themes_dir)
    monkeypatch.setattr(theme_engine, "_PARTIALS_DIR", partials_dir)
    monkeypatch.setattr(theme_engine, "_PALETTES_DIR", palettes_dir)
    theme_engine._load_template.cache_clear()
    yield themes_dir, partials_dir, palettes_dir
    theme_engine._load_template.cache_clear()


class TestRenderPresetCss:
    def test_unknown_preset_fails_closed(self):
        with pytest.raises(ValueError, match="unknown theme preset 'nope'"):
            theme_engine.render_preset_css("nope")

    def test_partials_concatenate_in_sequence_order(
        self, engine_sandbox, monkeypatch
    ):
        themes_dir, partials_dir, palettes_dir = engine_sandbox
        (themes_dir / "base.css").write_text("BASE-$a\n", encoding="utf-8")
        (partials_dir / "one.css").write_text("ONE-$b\n", encoding="utf-8")
        (partials_dir / "two.css").write_text("TWO-$c\n", encoding="utf-8")
        (palettes_dir / "px.json").write_text(
            json.dumps(_full_palette(a="1", b="2", c="3")), encoding="utf-8"
        )
        monkeypatch.setitem(
            THEME_PRESETS, "ordered", (("one", "two"), "px")
        )
        css = theme_engine.render_preset_css("ordered")
        assert css == "BASE-1\n\nONE-2\n\nTWO-3\n"

    def test_builtin_presets_render_base_then_partials_in_order(self):
        base_template = theme_engine._load_template(
            theme_engine._THEMES_DIR / "base.css"
        )
        for theme in sorted(BUILTIN_THEMES):
            css = load_preset_css(theme)
            palette = theme_engine.load_palette(theme)
            expected_base = theme_engine._substitute(base_template, theme, palette)
            assert css.startswith(expected_base.strip()), theme
            for partial_name in THEME_PRESETS[theme][0]:
                partial_template = theme_engine._load_template(
                    theme_engine._PARTIALS_DIR / f"{partial_name}.css"
                )
                text = theme_engine._substitute(partial_template, theme, palette)
                assert text.strip() in css, (theme, partial_name)

    def test_missing_partial_fails_closed(self, engine_sandbox, monkeypatch):
        themes_dir, partials_dir, palettes_dir = engine_sandbox
        (themes_dir / "base.css").write_text("BASE\n", encoding="utf-8")
        (palettes_dir / "px.json").write_text(
            json.dumps(_full_palette()), encoding="utf-8"
        )
        monkeypatch.setitem(
            THEME_PRESETS, "broken", (("ghost",), "px")
        )
        with pytest.raises(ValueError, match="missing partial 'ghost'"):
            theme_engine.render_preset_css("broken")

    def test_missing_substitution_token_fails_closed(
        self, engine_sandbox, monkeypatch
    ):
        themes_dir, partials_dir, palettes_dir = engine_sandbox
        (themes_dir / "base.css").write_text(
            "color: $text; border: $nonexistent;\n", encoding="utf-8"
        )
        (palettes_dir / "px.json").write_text(
            json.dumps(_full_palette(text="#000")), encoding="utf-8"
        )
        monkeypatch.setitem(THEME_PRESETS, "incomplete", ((), "px"))
        with pytest.raises(ValueError) as excinfo:
            theme_engine.render_preset_css("incomplete")
        assert "px" in str(excinfo.value)
        assert "nonexistent" in str(excinfo.value)

    def test_builtin_presets_use_builtin_palettes(self):
        for theme, (_, palette_name) in THEME_PRESETS.items():
            assert theme in BUILTIN_THEMES
            assert palette_name in BUILTIN_THEMES
            theme_engine.load_palette(palette_name)  # must not raise

    def test_codeblock_scroll_contract_is_verbatim(self):
        # The WeChat-safe code block structure must survive tokenization
        # byte-for-byte in every preset.
        contract = (
            "display: block;\n  overflow-x: auto;\n"
            "  -webkit-overflow-scrolling: touch;\n  white-space: pre;"
        )
        for theme in sorted(BUILTIN_THEMES):
            assert contract in load_preset_css(theme), theme


# ── CLI resolution semantics ─────────────────────────────────────


class TestResolveThemeCssRouting:
    def test_style_file_is_read_verbatim(self, tmp_path: Path):
        style = tmp_path / "custom.css"
        style.write_text("/* my style */ h1 { color: red; }", encoding="utf-8")
        css = resolve_theme_css(
            style_arg=style, theme_arg="default", project_dir=tmp_path
        )
        assert css == "/* my style */ h1 { color: red; }"

    def test_project_style_beats_engine_default(self, tmp_path: Path):
        project_style = tmp_path / "config" / "style.css"
        project_style.parent.mkdir(parents=True)
        project_style.write_text("/* project style */", encoding="utf-8")
        css = resolve_theme_css(
            style_arg=None, theme_arg=None, project_dir=tmp_path
        )
        assert css == "/* project style */"

    def test_missing_style_file_yields_empty_css(self, tmp_path: Path):
        css = resolve_theme_css(
            style_arg=tmp_path / "nope.css", theme_arg=None, project_dir=tmp_path
        )
        assert css == ""


# ── 配色 × 版式 matrix smoke (5 layouts x 8 palettes) ────────────


def _pygments_token_color(style_name: str) -> str:
    """A representative token color of a pygments style (lowercase hex)."""
    from pygments import token
    from pygments.styles import get_style_by_name

    style = get_style_by_name(style_name)
    for ttype in (
        token.Token.Keyword,
        token.Token.Name.Function,
        token.Token.Name.Builtin,
        token.Token.Comment,
    ):
        color = style.style_for_token(ttype)["color"]
        if color:
            return color.lower()
    raise AssertionError(f"pygments style '{style_name}' has no token color")


def _inlined_style(html: str, anchor: str, open_tag: str) -> str | None:
    """The inlined ``style`` attribute of the tag whose body contains *anchor*.

    *anchor* is matched inside the opening tag (e.g. ``class="copy-btn"``);
    *open_tag* is the literal tag opening the element (e.g. ``<span``), used
    to locate the start of the tag that carries the attribute.
    """
    anchor_at = html.find(anchor)
    if anchor_at == -1:
        return None
    tag_start = html.rfind(open_tag, 0, anchor_at)
    if tag_start == -1:
        return None
    tag_end = html.find(">", anchor_at)
    if tag_end == -1:
        return None
    # premailer switches the style attribute to single quotes when the value
    # itself contains double quotes (e.g. font stacks); the backreference
    # accepts either quote style and tolerates the opposite quote inside.
    match = re.search(r"style=([\"'])(.*?)\1", html[tag_start:tag_end + 1], re.DOTALL)
    return match.group(2) if match else None


class TestLayoutPaletteMatrix:
    """5 版式 × 8 色板的全组合冒烟。

    Every combination is rendered through ``theme_engine.render_css`` (the
    CLI's ``--layout``/``--palette`` path) and must satisfy the WeChat-safe
    contracts (scroll carrier, copy button, pygments scheme) plus a
    per-layout identity assertion proving the layout stylesheet actually
    took effect after inlining.
    """

    def _assert_layout_identity(self, layout: str, html: str):
        if layout == "serif":
            root_style = _inlined_style(html, 'class="wechat-content"', "<section")
            assert root_style is not None, layout
            assert "georgia" in root_style.lower(), layout
        elif layout == "terminal":
            pre_start = html.find("<pre")
            assert pre_start != -1, layout
            pre_end = html.find(">", pre_start)
            pre_style = re.search(r"style=([\"'])(.*?)\1", html[pre_start:pre_end + 1], re.DOTALL)
            assert pre_style is not None, layout
            # 全局直角：border-radius 的值必须是纯 0（而非 "0 0 8px 8px"）。
            assert re.search(r'border-radius:0(?:"|;|$)', pre_style.group(2)), (
                pre_style.group(2)
            )
        elif layout == "card":
            bq_start = html.find("<blockquote")
            assert bq_start != -1, layout
            bq_end = html.find(">", bq_start)
            bq_style = re.search(r"style=([\"'])(.*?)\1", html[bq_start:bq_end + 1], re.DOTALL)
            assert bq_style is not None, layout
            assert "background" in bq_style.group(2), layout
            assert "border-radius" in bq_style.group(2), layout
        elif layout == "classic":
            root_style = _inlined_style(html, 'class="wechat-content"', "<section")
            assert root_style is not None, layout
            assert "kaiti" in root_style.lower(), layout
        # "default" carries no identity of its own beyond the shared base.

    @pytest.mark.parametrize("layout", sorted(theme_engine.BUILTIN_LAYOUTS))
    @pytest.mark.parametrize("palette_name", sorted(BUILTIN_THEMES))
    def test_layout_x_palette_smoke(self, layout: str, palette_name: str):
        palette = theme_engine.load_palette(palette_name)
        css = theme_engine.render_css(layout, palette_name)
        assert css.strip(), (layout, palette_name)

        raw = render_markdown_to_html(
            SAMPLE_PATH.read_text(encoding="utf-8"),
            pygments_style=pygments_style_for_palette(palette),
        )
        html = process_article_html(raw, css)

        # Palette identity color (strong emphasis) survives inlining.
        assert palette["strong_color"] in html, (layout, palette_name)

        # Code element carries the WeChat-safe scroll contract verbatim.
        pre_start = html.find("<pre")
        assert pre_start != -1, (layout, palette_name)
        code_seg = html[pre_start: html.find("</code>", pre_start)]
        assert "display:block" in code_seg, (layout, palette_name)
        assert "overflow-x:auto" in code_seg, (layout, palette_name)
        assert "-webkit-overflow-scrolling:touch" in code_seg, (layout, palette_name)
        assert "white-space:pre" in code_seg, (layout, palette_name)

        # Table scroll wrapper got the touch-scroll style inlined.
        scroll_start = html.find('class="table-scroll"')
        assert scroll_start != -1, (layout, palette_name)
        scroll_seg = html[scroll_start: html.find("<table", scroll_start)]
        assert "-webkit-overflow-scrolling:touch" in scroll_seg, (layout, palette_name)

        # Copy button is an absolutely positioned span in the body.
        copy_start = html.find('class="copy-btn"')
        assert copy_start != -1, (layout, palette_name)
        tag_start = html.rfind("<span", 0, copy_start)
        tag_end = html.find(">", copy_start)
        btn_style = re.search(r'style="([^"]*)"', html[tag_start:tag_end])
        assert btn_style is not None, (layout, palette_name)
        assert "position:absolute" in btn_style.group(1), (layout, palette_name)

        # code_scheme drives the pygments style: the mapped palette's
        # representative token color appears in the highlighted code.
        token_color = _pygments_token_color(palette["code_scheme"])
        assert f"color:#{token_color}" in html, (layout, palette_name, token_color)

        # The layout stylesheet actually took effect after inlining.
        self._assert_layout_identity(layout, html)


class TestBuiltinLayoutRegistry:
    """Registry invariants for the four landed layouts."""

    def test_every_registered_layout_is_implemented(self):
        for name, entry in theme_engine.BUILTIN_LAYOUTS.items():
            assert entry.get("implemented", True) is True, name
            assert theme_engine.render_css(name, "default").strip(), name

    def test_only_classic_declares_ornaments(self):
        for name, entry in theme_engine.BUILTIN_LAYOUTS.items():
            assert bool(entry.get("ornaments")) == (name == "classic"), name

    @pytest.mark.parametrize("name", ["serif", "terminal", "card", "classic"])
    def test_layout_owns_its_stylesheet(self, name: str):
        assert theme_engine.BUILTIN_LAYOUTS[name]["partials"] == (name,), name
        css = theme_engine.render_css(name, "default")
        expected_marker = {
            "serif": "Georgia",
            "terminal": "monospace",
            "card": "border-radius: 12px",
            "classic": "KaiTi",
        }[name]
        assert expected_marker in css, name

    def test_layout_css_never_hardcodes_hex_colors(self):
        for name in ("serif", "terminal", "card", "classic"):
            template = theme_engine._load_template(
                theme_engine._LAYOUTS_DIR / f"{name}.css"
            ).template
            assert "#3e3e3e" not in template and "#07c160" not in template, name
