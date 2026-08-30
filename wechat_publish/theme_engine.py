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

Extension point: project-level presets can be registered by adding entries
to ``config.THEME_PRESETS`` (partials sequence + palette name); this unit
ships only the eight builtin presets.
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

#: Pygments palettes that pair with a theme's code background. Kept in sync
#: with ``render.py::_DARK_CODE_THEMES`` (dark code backgrounds use
#: "github-dark"; light ones use "friendly").
VALID_CODE_SCHEMES = ("friendly", "github-dark")


@cache
def _load_template(path: Path) -> Template:
    """Load a CSS template file (cached)."""
    return Template(path.read_text(encoding="utf-8"))


def load_palette(name: str) -> dict[str, str]:
    """Load and validate ``palettes/<name>.json`` (fail-closed).

    Raises ``ValueError`` when the palette file is unknown, when any
    ``PALETTE_REQUIRED_KEYS`` key is missing, or when ``code_scheme`` is not
    one of ``VALID_CODE_SCHEMES``. Missing non-required tokens are caught at
    substitution time by :func:`render_preset_css`.
    """
    path = _PALETTES_DIR / f"{name}.json"
    if not path.exists():
        raise ValueError(f"unknown palette '{name}' (no file at {path})")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"palette '{name}' is not valid JSON: {exc}") from exc
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
