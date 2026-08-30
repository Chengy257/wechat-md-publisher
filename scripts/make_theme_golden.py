"""Generate / verify the golden wechat-HTML sha256 baseline for all builtin themes.

The golden baseline freezes the byte-exact WeChat HTML output of the full
rendering pipeline (markdown -> raw HTML -> sanitize/compat/footnotes -> CSS
inline) for every ``--theme`` preset, using the representative sample at
``tests/fixtures/golden_sample.md``.

Modes:
- default (write): render every builtin theme and write sha256 hashes to
  ``tests/fixtures/theme_golden.json`` (keyed by theme name).
- ``--check``: read-only; recompute hashes and compare against the fixture.
  Exits non-zero on any mismatch.

The script intentionally resolves theme CSS through the same entry point the
CLI uses (``config.resolve_theme_css``). It therefore keeps working unchanged
before AND after the theme engine refactor: before the refactor it exercises
the bundled CSS files, afterwards it exercises the generated preset CSS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wechat_publish.config import BUILTIN_THEMES, resolve_theme_css  # noqa: E402
from wechat_publish.html_processor import process_article_html  # noqa: E402
from wechat_publish.render import (  # noqa: E402
    pygments_style_for_theme,
    render_markdown_to_html,
)

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "theme_golden.json"
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "golden_sample.md"


def render_theme_hash(theme: str, sample_markdown: str) -> str:
    """Render the sample through the full pipeline and return the sha256 hex."""
    theme_css = resolve_theme_css(
        style_arg=None, theme_arg=theme, project_dir=ROOT
    )
    if not theme_css:
        raise RuntimeError(f"theme '{theme}' resolved to empty CSS")
    raw_html = render_markdown_to_html(
        sample_markdown, pygments_style=pygments_style_for_theme(theme)
    )
    wechat_html = process_article_html(raw_html, theme_css)
    return hashlib.sha256(wechat_html.encode("utf-8")).hexdigest()


def compute_all() -> dict[str, str]:
    sample = SAMPLE_PATH.read_text(encoding="utf-8")
    return {theme: render_theme_hash(theme, sample) for theme in sorted(BUILTIN_THEMES)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="read-only: verify current output against the fixture",
    )
    args = parser.parse_args(argv)

    hashes = compute_all()

    if args.check:
        if not FIXTURE_PATH.exists():
            print(f"[ERROR] fixture missing: {FIXTURE_PATH}", file=sys.stderr)
            return 2
        expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        failures = [
            theme
            for theme in sorted(hashes)
            if expected.get(theme) != hashes[theme]
        ]
        for theme in sorted(hashes):
            status = "OK " if expected.get(theme) == hashes[theme] else "FAIL"
            print(f"  [{status}] {theme}: {hashes[theme]}")
        if failures:
            print(
                f"[ERROR] golden mismatch for {len(failures)}/{len(hashes)} "
                f"theme(s): {', '.join(failures)}",
                file=sys.stderr,
            )
            return 1
        print(f"[OK] {len(hashes)}/{len(hashes)} themes match the golden fixture")
        return 0

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for theme in sorted(hashes):
        print(f"  {theme}: {hashes[theme]}")
    print(f"[OK] wrote {len(hashes)} hashes to {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
