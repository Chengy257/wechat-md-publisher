"""Theme engine: token-based preset CSS rendering (base template + partials).

Replaces the eight per-theme CSS files with:

- ``themes/base.css``      — the structural skeleton shared verbatim by all
  builtin themes, with ``$token`` placeholders for colors/metrics.
- ``themes/partials/*.css`` — the cross-theme structural variants (heading
  shapes, quote shapes, dark pre, ...), selected per preset.
- ``palettes/<name>.json``  — the palette values substituted into the tokens.

CSS variables are deliberately NOT used: premailer does not evaluate them and
WeChat's support is unreliable. Substitution happens at build time via
``string.Template`` (CSS braces make ``str.format`` unusable).

Ordering red line: ``THEME_PRESETS`` partial sequences (and the base/partial
concatenation order) reproduce the rule order of the retired theme files.
premailer inlines matched declarations in CSS order, so any order change
would alter the inlined ``style`` attribute text and break the golden
byte-equality baseline (``tests/test_theme_golden.py``).

Extension points:

- project-level presets can be registered by adding entries to
  ``config.THEME_PRESETS`` (partials sequence + palette name); this unit
  ships only the eight builtin presets.
- project-level palettes: ``<project>/config/palettes/<name>.json`` overrides
  a builtin palette of the same name (fail-closed validation identical to the
  builtin ones).
- layouts (``--layout``): structural variants orthogonal to the palette,
  registered in ``BUILTIN_LAYOUTS``. Only ``default`` is implemented in this
  unit; the other names are placeholders that fail closed with an explicit
  "版式文件未实现" error until their partials land.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from string import Template

_PACKAGE_DIR = Path(__file__).resolve().parent
_THEMES_DIR = _PACKAGE_DIR / "themes"
_PARTIALS_DIR = _THEMES_DIR / "partials"
_PALETTES_DIR = _PACKAGE_DIR / "palettes"
#: Layout-specific partials live here (created by the layout unit; the
#: directory may not exist yet — every non-default layout fails closed
#: before any file under it is read).
_LAYOUTS_DIR = _THEMES_DIR / "layouts"

#: Keys every palette must define (fail-closed: ``load_palette`` raises
#: ``ValueError`` listing missing keys). Additional tokens used only by
#: specific structural partials are validated at substitution time.
PALETTE_REQUIRED_KEYS = (
    "text",
    "muted",
    "link",
    "h1_color",
    "h2_color",
    "h2_accent",
    "h3_color",
    "blockquote_border",
    "blockquote_bg",
    "code_inline_bg",
    "code_inline_color",
    "code_bg",
    "code_border",
    "bar_bg",
    "bar_border",
    "bar_text",
    "copy_btn_border",
    "copy_btn_bg",
    "copy_btn_color",
    "table_border",
    "th_bg",
    "row_alt_bg",
    "hr_color",
    "radius",
    "code_scheme",
)

#: Pygments palettes that pair with a palette's code background. Dark code
#: backgrounds use "github-dark"; light ones use "friendly".
VALID_CODE_SCHEMES = ("friendly", "github-dark")

#: Structural partials shared by every layout: the code block pre/code rules
#: were marked "全部主题" in the retired theme files and must be present for
#: any layout so the WeChat-safe scroll-carrier contract on ``pre code``
#: (display:block / overflow-x:auto / touch scrolling / white-space:pre)
#: holds. They are read from ``themes/partials`` like preset partials.
_LAYOUT_COMMON_PARTIALS = ("codeblock-pre", "codeblock-pre-code")

#: Layout registry: layout name -> metadata. ``partials`` is the
#: layout-specific partial sequence (read from ``themes/layouts``; WIP for
#: every non-default layout, hence the empty lists). ``implemented=False``
#: marks a placeholder that fails closed with a "版式文件未实现" error until
#: its partials land. Layouts are orthogonal to palettes: any layout pairs
#: with any palette via :func:`render_css`.
BUILTIN_LAYOUTS: dict[str, dict[str, object]] = {
    "default": {"partials": (), "ornaments": False},
    "serif": {"partials": (), "ornaments": True, "implemented": False},
    "terminal": {"partials": (), "ornaments": True, "implemented": False},
    "card": {"partials": (), "ornaments": True, "implemented": False},
    "classic": {"partials": (), "ornaments": True, "implemented": False},
}


@cache
def _load_template(path: Path) -> Template:
    """Load a CSS template file (cached)."""
    return Template(path.read_text(encoding="utf-8"))


def _read_palette_json(name: str, path: Path) -> object:
    """Read and parse a palette JSON file (fail-closed on invalid JSON)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"palette '{name}' is not valid JSON: {exc}") from exc


def _validate_palette(name: str, data: object) -> dict[str, str]:
    """Validate a parsed palette mapping (fail-closed)."""
    if not isinstance(data, dict):
        raise ValueError(f"palette '{name}' must be a JSON object")

    missing = [key for key in PALETTE_REQUIRED_KEYS if key not in data]
    if missing:
        raise ValueError(
            f"palette '{name}' is missing required key(s): {', '.join(missing)}"
        )

    scheme = data["code_scheme"]
    if scheme not in VALID_CODE_SCHEMES:
        raise ValueError(
            f"palette '{name}' has invalid code_scheme {scheme!r} "
            f"(expected one of: {', '.join(VALID_CODE_SCHEMES)})"
        )
    return data


def load_palette(
    name: str, *, project_dir: Path | None = None
) -> dict[str, str]:
    """Load and validate ``palettes/<name>.json`` (fail-closed).

    When *project_dir* is given, ``<project>/config/palettes/<name>.json``
    takes precedence over the builtin palette of the same name (project
    palettes are validated with the identical fail-closed rules).

    Raises ``ValueError`` when the palette file is unknown (builtin and
    project candidates listed), when any ``PALETTE_REQUIRED_KEYS`` key is
    missing, or when ``code_scheme`` is not one of ``VALID_CODE_SCHEMES``.
    Missing non-required tokens are caught at substitution time by
    :func:`render_preset_css` / :func:`render_css`.

    The returned dict carries a ``_source`` marker: ``"builtin"`` or
    ``"project"``.
    """
    project_path = (
        Path(project_dir) / "config" / "palettes" / f"{name}.json"
        if project_dir is not None
        else None
    )
    if project_path is not None and project_path.exists():
        data = _validate_palette(name, _read_palette_json(name, project_path))
        data["_source"] = "project"
        return data

    path = _PALETTES_DIR / f"{name}.json"
    if not path.exists():
        candidates = list_palettes(project_dir)
        available = ", ".join(candidates) if candidates else "(none)"
        hint = f"; also looked for {project_path}" if project_path else ""
        raise ValueError(
            f"unknown palette '{name}' (no file at {path}{hint}); "
            f"available: {available}"
        )
    data = _validate_palette(name, _read_palette_json(name, path))
    data["_source"] = "builtin"
    return data


def list_palettes(project_dir: Path | None = None) -> list[str]:
    """All palette names usable for a run (sorted).

    Builtin ``palettes/*.json`` names plus, when *project_dir* is given, the
    stems of ``<project>/config/palettes/*.json`` (project palettes may
    override builtin names).
    """
    names = {path.stem for path in _PALETTES_DIR.glob("*.json")}
    if project_dir is not None:
        project_palettes = Path(project_dir) / "config" / "palettes"
        if project_palettes.is_dir():
            names |= {path.stem for path in project_palettes.glob("*.json")}
    return sorted(names)


def list_layouts() -> list[str]:
    """All registered layout names (sorted)."""
    return sorted(BUILTIN_LAYOUTS)


def render_css(
    layout: str, palette_name: str, *, project_dir: Path | None = None
) -> str:
    """Render the CSS for a layout x palette combination.

    Concatenates the rendered base template, the layout-common structural
    partials (``_LAYOUT_COMMON_PARTIALS``), then the layout-specific partials
    from ``BUILTIN_LAYOUTS`` (read from ``themes/layouts``). The default
    layout has an empty layout-specific sequence, so it renders as
    base + common partials.

    Fail-closed: unknown layout or palette names, placeholder layouts
    ("版式文件未实现"), and missing partial files all raise ``ValueError``.
    """
    entry = BUILTIN_LAYOUTS.get(layout)
    if entry is None:
        raise ValueError(
            f"unknown layout '{layout}' "
            f"(registered: {', '.join(sorted(BUILTIN_LAYOUTS))})"
        )
    if not entry.get("implemented", True):
        raise ValueError(
            f"版式 '{layout}' 的版式文件未实现"
            f"（当前仅 'default' 可用，其余版式的 CSS 文件尚未落地）"
        )
    palette = load_palette(palette_name, project_dir=project_dir)

    parts = [_substitute(_load_template(_THEMES_DIR / "base.css"), palette_name, palette)]
    for partial_name in _LAYOUT_COMMON_PARTIALS:
        path = _PARTIALS_DIR / f"{partial_name}.css"
        if not path.exists():
            raise ValueError(
                f"layout '{layout}' references missing shared partial "
                f"'{partial_name}'"
            )
        parts.append(_substitute(_load_template(path), palette_name, palette))
    for partial_name in entry.get("partials", ()):
        path = _LAYOUTS_DIR / f"{partial_name}.css"
        if not path.exists():
            raise ValueError(
                f"版式 '{layout}' 的版式文件未实现（缺少 {path}）"
            )
        parts.append(_substitute(_load_template(path), palette_name, palette))
    return "\n".join(parts)


def resolve_selection(
    style_arg: Path | str | None,
    layout_arg: str | None = None,
    palette_arg: str | None = None,
    theme_arg: str | None = None,
    project_dir: Path | None = None,
) -> tuple[str, dict[str, str] | None]:
    """Unified resolution of the CSS text and the palette metadata for a run.

    Priority: ``--style`` (file, read verbatim; palette unknown -> ``None``,
    pygments falls back to the light default) > ``--layout``/``--palette``
    (either given triggers the engine path; the omitted one defaults) >
    ``--theme`` preset (palette from ``THEME_PRESETS``) > no-argument
    fallback: project ``config/style.css`` (palette ``None``) or the engine
    ``default`` preset x ``default`` palette, so a default run always gets
    styled, sanitized output.

    Returns ``(css, palette)`` where *palette* is the resolved palette dict
    (with ``_source``) or ``None`` when the CSS came from a file whose code
    background is unknown.
    """
    from .config import THEME_PRESETS, load_theme_css  # lazy: config owns registries

    if style_arg is not None:
        return load_theme_css(Path(style_arg)), None

    if layout_arg or palette_arg:
        layout = layout_arg or "default"
        palette_name = palette_arg or "default"
        css = render_css(layout, palette_name, project_dir=project_dir)
        return css, load_palette(palette_name, project_dir=project_dir)

    if theme_arg:
        if theme_arg not in THEME_PRESETS:
            raise ValueError(
                f"unknown theme preset '{theme_arg}' "
                f"(registered: {', '.join(sorted(THEME_PRESETS))})"
            )
        return render_preset_css(theme_arg), load_palette(THEME_PRESETS[theme_arg][1])

    if project_dir is not None:
        project_style = Path(project_dir) / "config" / "style.css"
        if project_style.exists():
            return load_theme_css(project_style), None

    return render_preset_css("default"), load_palette("default")


def _substitute(template: Template, palette_name: str, palette: dict[str, str]) -> str:
    """Substitute tokens, failing closed with palette/key context."""
    try:
        return template.substitute(palette)
    except KeyError as exc:
        missing = exc.args[0]
        raise ValueError(
            f"palette '{palette_name}' is missing required key(s): {missing}"
        ) from exc


def render_preset_css(name: str) -> str:
    """Render the CSS for a registered preset.

    Looks up ``config.THEME_PRESETS[name]`` = (partials sequence, palette
    name), then concatenates the rendered base template followed by each
    partial in sequence order. The order is load-bearing (see module docstring).
    """
    from .config import THEME_PRESETS  # lazy: config owns the registration

    if name not in THEME_PRESETS:
        raise ValueError(
            f"unknown theme preset '{name}' "
            f"(registered: {', '.join(sorted(THEME_PRESETS))})"
        )
    partial_names, palette_name = THEME_PRESETS[name]
    palette = load_palette(palette_name)

    parts = [_substitute(_load_template(_THEMES_DIR / "base.css"), palette_name, palette)]
    for partial_name in partial_names:
        path = _PARTIALS_DIR / f"{partial_name}.css"
        if not path.exists():
            raise ValueError(
                f"preset '{name}' references missing partial '{partial_name}'"
            )
        parts.append(_substitute(_load_template(path), palette_name, palette))
    return "\n".join(parts)
