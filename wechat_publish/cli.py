"""Command line entry points for the WeChat publisher CLI."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import yaml

from .config import (
    BUILTIN_THEMES,
    ArticleMetadata,
    PublisherConfig,
    account_key,
    account_scoped_paths,
    load_env_values,
    load_publish_config,
    resolve_config,
    resolve_credentials,
)
from .draft import DraftArticle, add_draft, build_draft_payload, validate_publish_preflight
from .errors import RemoteDraftCreatedLocalStateFailed, WeChatAPIError
from .html_processor import apply_layout_ornaments, discover_images, process_article_html
from .images import compress_cover, process_images, upload_cover_image
from .render import (
    _wrap_preview,
    parse_front_matter,
    pygments_style_for_palette,
    render_markdown_to_html,
)
from .state import (
    ensure_state_dirs,
    quarantine_legacy_state,
    save_post_snapshot,
    write_text_atomic,
)
from .theme_engine import BUILTIN_LAYOUTS, list_layouts, list_palettes, resolve_selection
from .token import get_access_token, mask_appid, mask_token

_T = TypeVar("_T")

# Default --config value; used to detect whether --config was passed explicitly.
_DEFAULT_CONFIG = Path("config/publish.yaml")

# pyproject.toml name marker used to recognize this project's root directory.
_PROJECT_NAME_MARKER = 'name = "wechat-md-publisher"'


class _Abort(RuntimeError):
    """Internal: abort the current command with a user-facing message."""


def _palette_choices() -> list[str]:
    """Palette names for argparse ``choices`` (builtin + current project's).

    Discovery uses the project directory implied by the current working
    directory at parser-build time; ``resolve_selection`` re-validates the
    final name fail-closed against the actual project directory.
    """
    try:
        return list_palettes(_discover_project_dir(Path.cwd()))
    except OSError:
        return list_palettes()


def _add_theme_args(p: argparse.ArgumentParser) -> None:
    """Attach the theme/style selection flags shared by the render commands."""
    p.add_argument("--style", type=Path, default=None,
                   help="Path to CSS file (highest priority; overrides "
                        "--theme/--layout/--palette)")
    p.add_argument("--theme", default=None, choices=sorted(BUILTIN_THEMES),
                   help="Built-in theme preset (layout + palette bundle)")
    p.add_argument("--layout", default=None, choices=list_layouts(),
                   help="Built-in layout (structure; independent of colors); "
                        "pairs with --palette")
    p.add_argument("--palette", default=None, choices=_palette_choices(),
                   help="Color palette: built-in name or a project "
                        "config/palettes/*.json (same-name overrides builtin)")


def _discover_project_dir(start: Path) -> Path:
    """Walk upward from *start* looking for project-root markers.

    A directory counts as the project root when it contains
    ``config/publish.yaml``, ``config/publish.example.yaml`` or ``.git/``;
    a ``pyproject.toml`` only counts when its ``name`` is
    ``wechat-md-publisher`` (so arbitrary Python package directories are
    not mistaken for a project). At most 8 levels (including *start*)
    are examined; the fallback is *start* itself — never site-packages.
    """
    current = start.resolve()
    for _ in range(8):
        if (
            (current / "config" / "publish.yaml").exists()
            or (current / "config" / "publish.example.yaml").exists()
            or (current / ".git").exists()
        ):
            return current
        pyproject = current / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if _PROJECT_NAME_MARKER in text:
                return current
        if current.parent == current:
            break
        current = current.parent
    return start.resolve()


def _resolve_project_dir(args: argparse.Namespace) -> Path:
    """Resolve the project root directory for the current command.

    Priority: explicit ``--project-dir`` > the directory implied by an
    explicit ``--config`` > upward discovery from the cwd > the cwd.
    """
    explicit = getattr(args, "project_dir", None)
    if explicit is not None:
        return Path(explicit).resolve()

    config_arg = getattr(args, "config", None)
    if config_arg is not None and Path(config_arg) != _DEFAULT_CONFIG:
        config_path = Path(config_arg).resolve()
        if config_path.parent.name == "config":
            return config_path.parent.parent
        return config_path.parent

    return _discover_project_dir(Path.cwd())


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="wechat-publish",
        description="Render Markdown articles into WeChat Official Account drafts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- render ---
    render_p = subparsers.add_parser("render", help="Render Markdown to WeChat HTML.")
    render_p.add_argument("--project-dir", type=Path, default=None,
                          help="Project root directory (config/, .env and output "
                               "anchors); default: auto-discovered from the cwd")
    render_p.add_argument("--md", type=Path, default=Path("input/article.md"),
                          help="Path to Markdown article (default: input/article.md)")
    render_p.add_argument("--out", type=Path, default=Path("build/article.wechat.html"),
                          help="Output WeChat HTML path")
    render_p.add_argument("--preview-out", type=Path, default=Path("build/article.preview.html"),
                          help="Output preview HTML path")
    _add_theme_args(render_p)

    # --- draft ---
    draft_p = subparsers.add_parser("draft", help="Create a WeChat draft from Markdown.")
    draft_p.add_argument("--project-dir", type=Path, default=None,
                         help="Project root directory (config/, .env and output "
                              "anchors); default: auto-discovered from the cwd")
    draft_p.add_argument("--md", type=Path, default=Path("input/article.md"),
                         help="Path to Markdown article")
    draft_p.add_argument("--dry-run", action="store_true",
                         help="Show planned operations without contacting WeChat")
    draft_p.add_argument("--title", help="Override article title")
    draft_p.add_argument("--author", help="Override article author")
    draft_p.add_argument("--digest", help="Override article digest")
    draft_p.add_argument("--cover", type=Path, help="Override cover image path")
    draft_p.add_argument("--config", type=Path, default=Path("config/publish.yaml"),
                         help="Path to publish config YAML")
    _add_theme_args(draft_p)
    draft_p.add_argument("--ai-summary", action="store_true",
                         help="Generate article digest via AI when not specified")
    draft_p.add_argument("--ai-cover", action="store_true",
                         help="Generate cover image via AI when no cover is available")
    draft_p.add_argument("--mermaid", action="store_true",
                         help="Render mermaid diagrams to PNG images")
    draft_p.add_argument("--mermaid-engine", default="mmdc",
                         choices=["mmdc", "api"],
                         help="Mermaid rendering engine (default: mmdc)")
    draft_p.add_argument("--autofill-front-matter", action="store_true",
                         help="Generate front matter (title/date/author) and write it "
                              "back to the Markdown file when it has none")
    draft_p.add_argument("--compress-cover", action="store_true",
                         help="Re-encode covers larger than 1 MB as JPEG "
                              "(requires the optional Pillow dependency)")
    draft_p.add_argument("--allow-missing-images", action="store_true",
                         help="Skip images that fail to upload instead of aborting "
                              "the draft creation")

    # --- inspect ---
    inspect_p = subparsers.add_parser("inspect", help="Inspect resolved metadata and assets.")
    inspect_p.add_argument("--project-dir", type=Path, default=None,
                           help="Project root directory (config/, .env and output "
                                "anchors); default: auto-discovered from the cwd")
    inspect_p.add_argument("--md", type=Path, default=Path("input/article.md"),
                           help="Path to Markdown article")
    inspect_p.add_argument("--config", type=Path, default=Path("config/publish.yaml"),
                           help="Path to publish config YAML")
    inspect_p.add_argument("--title", help="Override article title")
    inspect_p.add_argument("--author", help="Override article author")
    inspect_p.add_argument("--digest", help="Override article digest")
    inspect_p.add_argument("--cover", type=Path, help="Override cover image path")
    _add_theme_args(inspect_p)

    return parser


def _resolve_cli_values(args: argparse.Namespace) -> dict[str, Any]:
    """Extract CLI override values from parsed args."""
    values: dict[str, Any] = {}
    for key in ("title", "author", "digest", "cover"):
        val = getattr(args, key, None)
        if val is not None:
            values[key] = str(val)
    return values


def _load_theme_config_defaults(path: Path) -> Mapping[str, Any]:
    """Quietly read a publish.yaml for theme defaults (missing file -> ``{}``).

    Used by the render command, which historically ignores publish.yaml
    entirely except for the optional ``layout``/``palette`` default keys; a
    broken YAML is treated as absent instead of aborting a pure preview run.
    """
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _apply_theme_config_defaults(
    args: argparse.Namespace, pub_cfg: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """Merge --layout/--palette CLI flags with publish.yaml defaults.

    publish.yaml ``layout``/``palette`` act as defaults; explicit CLI flags
    win. Missing keys (CLI or config) resolve to ``None`` so the default
    behavior is unchanged.
    """
    layout = getattr(args, "layout", None)
    if layout is None:
        cfg_layout = pub_cfg.get("layout")
        layout = cfg_layout if isinstance(cfg_layout, str) and cfg_layout else None
    palette = getattr(args, "palette", None)
    if palette is None:
        cfg_palette = pub_cfg.get("palette")
        palette = cfg_palette if isinstance(cfg_palette, str) and cfg_palette else None
    return layout, palette


def _resolve_theme_assets(
    args: argparse.Namespace, project: Path, pub_cfg: Mapping[str, Any]
) -> tuple[str, Mapping[str, str] | None]:
    """Resolve (theme CSS, palette) for a render/draft/inspect run.

    Resolution chain: ``--style`` file > ``--layout``/``--palette`` >
    ``--theme`` preset > publish.yaml ``layout``/``palette`` defaults are
    applied underneath the CLI flags > project ``config/style.css`` >
    engine ``default`` preset x ``default`` palette.
    """
    layout, palette_name = _apply_theme_config_defaults(args, pub_cfg)
    return resolve_selection(
        style_arg=args.style,
        layout_arg=layout,
        palette_arg=palette_name,
        theme_arg=getattr(args, "theme", None),
        project_dir=project,
    )


def _ornament_layout(
    args: argparse.Namespace, pub_cfg: Mapping[str, Any]
) -> str | None:
    """Return the layout whose HTML ornaments should be injected, or ``None``.

    Ornaments run only on the engine path (``--layout``/``--palette`` or a
    publish.yaml ``layout`` default): ``--style`` and ``--theme`` bypass the
    engine and never get decorations. Whether a layout decorates at all is
    decided by its ``ornaments`` flag in ``theme_engine.BUILTIN_LAYOUTS``.
    """
    if getattr(args, "style", None) is not None or getattr(args, "theme", None):
        return None
    layout, _ = _apply_theme_config_defaults(args, pub_cfg)
    if not layout:
        return None
    entry = BUILTIN_LAYOUTS.get(layout)
    if entry is not None and entry.get("ornaments"):
        return layout
    return None


def _run_with_token_retry(
    token: Any,
    appid: str,
    appsecret: str,
    token_cache: Path,
    fn: Callable[[str], _T],
) -> _T:
    """Run ``fn(token_value)`` retrying transient failures.

    Recovers once from a token rejected mid-run (40001/42001) by forcing a
    refresh, and retries busy/rate-limit errors (-1/45009/45064) with a
    short backoff. Any other error propagates immediately.
    """
    refreshed = False
    for attempt in range(3):
        try:
            return fn(token.value)
        except WeChatAPIError as e:
            errcode = e.detail.errcode
            if errcode in (40001, 42001) and not refreshed:
                refreshed = True
                print("[INFO] access_token rejected mid-run; refreshing and retrying ...")
                token = get_access_token(
                    appid, appsecret, token_cache, force_refresh=True
                )
                continue
            if errcode in (-1, 45009, 45064) and attempt < 2:
                wait = 3 * (attempt + 1)
                print(f"[WARN] WeChat API busy (errcode={errcode}); retrying in {wait}s ...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("token retry loop did not converge")  # pragma: no cover


def _yaml_quote(value: str) -> str:
    """Quote a string for YAML double-quoted style."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _autofill_front_matter(
    md_path: Path, text: str, cli_title: str | None, author: str
) -> str:
    """Prepend a generated front matter block when the article has none.

    Idempotent: any article already starting with a ``---`` block (even an
    unparseable one) is left untouched. The title comes from the CLI, the
    first Markdown heading, or the file stem; the date from the file mtime.
    """
    if text.lstrip().startswith("---"):
        return text

    title = cli_title
    if not title:
        for line in text.splitlines():
            heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if heading:
                title = heading.group(1)
                break
    if not title:
        title = md_path.stem

    date = datetime.fromtimestamp(md_path.stat().st_mtime).strftime("%Y-%m-%d")
    lines = ["---", f"title: {_yaml_quote(title)}", f'date: "{date}"']
    if author:
        lines.append(f"author: {_yaml_quote(author)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + text


# ── render command ──────────────────────────────────────────────

def cmd_render(args: argparse.Namespace) -> int:
    """Render Markdown to preview and WeChat HTML.

    Mirrors ``render.render_article`` step by step but keeps the
    layout-ornaments injection (``apply_layout_ornaments``) between markdown
    rendering and WeChat processing, which ``render_article`` (whose module
    is outside this feature's ownership) does not support.
    """
    md_path = args.md
    if not md_path.exists():
        print(f"[ERROR] Markdown file not found: {md_path}", file=sys.stderr)
        return 1

    project = _resolve_project_dir(args)
    pub_cfg = _load_theme_config_defaults(project / _DEFAULT_CONFIG)
    theme_css, palette = _resolve_theme_assets(args, project, pub_cfg)

    markdown_text = md_path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(markdown_text)

    # Remove <!--more--> marker
    body = body.replace("<!--more-->", "")

    raw_html = render_markdown_to_html(
        body, pygments_style=pygments_style_for_palette(palette)
    )

    # Layout decorations (classic divider/end markers) are injected before
    # sanitize/compat/inlining so they survive nh3 and get the layout CSS
    # inlined onto them.
    ornament_layout = _ornament_layout(args, pub_cfg)
    if ornament_layout:
        raw_html = apply_layout_ornaments(raw_html, layout=ornament_layout)

    wechat_html = process_article_html(raw_html, theme_css)

    preview_path = args.preview_out
    wechat_path = args.out
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    wechat_path.parent.mkdir(parents=True, exist_ok=True)

    title = str(front_matter.get("title", ""))
    preview_path.write_text(_wrap_preview(wechat_html, title), encoding="utf-8")
    wechat_path.write_text(wechat_html, encoding="utf-8")

    print(f"[OK] preview: {preview_path}")
    print(f"[OK] wechat:  {wechat_path}")
    return 0


# ── inspect command ─────────────────────────────────────────────

def cmd_inspect(args: argparse.Namespace) -> int:
    """Inspect resolved metadata and assets (offline; writes no files)."""
    try:
        # Same render path as draft, but nothing is written to disk and no
        # mermaid rendering runs (inspect has no --mermaid flag).
        stage = _render_stage(args, write_outputs=False)
    except _Abort as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    article = stage.article
    print("=== Resolved Metadata ===")
    print(f"  title:    {article.title or '(empty)'}")
    print(f"  author:   {article.author or '(empty)'}")
    print(f"  digest:   {article.digest or '(empty)'}")
    print(f"  cover:    {article.cover}")

    print(f"\n=== Images ({len(stage.images)}) ===")
    for img in stage.images:
        location = "remote" if img.is_remote else f"local: {img.resolved_path}"
        print(f"  {img.original_src}  ({location})")

    # Show credentials status (masked)
    appid, _ = resolve_credentials(stage.pub_cfg, stage.env)
    if appid:
        print("\n=== Credentials ===")
        print(f"  appid: {mask_appid(appid)}")
    else:
        print("\n=== Credentials ===")
        print("  [WARN] No WECHAT_APPID found in environment")

    return 0


# ── draft command ───────────────────────────────────────────────

@dataclass(frozen=True)
class _DraftStage:
    """Result of the local (network-free) draft rendering stage."""

    config: PublisherConfig
    article: ArticleMetadata
    wechat_html: str
    body: str
    images: list
    md_path: Path
    project: Path
    preview_path: Path
    wechat_path: Path
    pub_cfg: Mapping[str, Any]
    env: Mapping[str, str | None]


def _render_stage(args: argparse.Namespace, write_outputs: bool = True) -> _DraftStage:
    """Load, render and adapt the article locally; no network is touched.

    With ``write_outputs=False`` (the inspect path) nothing is persisted:
    no build/ directory is created and mermaid rendering is skipped.
    """
    md_path = args.md
    if not md_path.exists():
        raise _Abort(f"Markdown file not found: {md_path}")

    project = _resolve_project_dir(args)
    pub_cfg = load_publish_config(project / args.config)
    theme_css, palette = _resolve_theme_assets(args, project, pub_cfg)
    env = load_env_values(project)
    cli_values = _resolve_cli_values(args)

    # Optionally generate and write back front matter for articles that
    # have none (replaces the preprocessing of the retired publish.sh).
    if getattr(args, "autofill_front_matter", False):
        raw_text = md_path.read_text(encoding="utf-8")
        pre_cfg = resolve_config(
            cli_values=cli_values,
            front_matter={},
            publish_config=pub_cfg,
            env=env,
        )
        filled = _autofill_front_matter(
            md_path, raw_text, args.title, pre_cfg.article.author
        )
        if filled != raw_text:
            md_path.write_text(filled, encoding="utf-8")
            print(f"[INFO] front matter generated and written back: {md_path}")

    text = md_path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(text)
    body = body.replace("<!--more-->", "")

    config = resolve_config(
        cli_values=cli_values,
        front_matter=front_matter,
        publish_config=pub_cfg,
        env=env,
        project_dir=project,
    )

    article = config.article
    if write_outputs and not article.title:
        raise _Abort(
            "Article title is required. Use --title, "
            "--autofill-front-matter, or add front matter."
        )

    raw_html = render_markdown_to_html(
        body, pygments_style=pygments_style_for_palette(palette)
    )

    # Layout decorations (classic divider/end markers) before sanitize and
    # inlining — same ordering as the render command.
    ornament_layout = _ornament_layout(args, pub_cfg)
    if ornament_layout:
        raw_html = apply_layout_ornaments(raw_html, layout=ornament_layout)

    build_dir = config.build_dir
    preview_path = build_dir / "article.preview.html"
    wechat_path = build_dir / "article.wechat.html"

    wechat_html = process_article_html(raw_html, theme_css)

    if write_outputs:
        build_dir.mkdir(parents=True, exist_ok=True)

        if getattr(args, "mermaid", False) and not getattr(args, "dry_run", False):
            from .mermaid import replace_mermaid_blocks
            mermaid_dir = build_dir / "mermaid"
            wechat_html = replace_mermaid_blocks(
                wechat_html, mermaid_dir, engine=args.mermaid_engine,
                src_base_dir=md_path.parent,
            )
        elif getattr(args, "mermaid", False):
            print("[INFO] dry-run: mermaid rendering skipped (would run on a real publish).")

        # Save preview and WeChat HTML. The preview title is the resolved
        # article title so --title overrides are reflected in the preview.
        preview_path.write_text(
            _wrap_preview(wechat_html, article.title), encoding="utf-8"
        )
        wechat_path.write_text(wechat_html, encoding="utf-8")

    # Discover images (markdown dir, project root and build dir are trusted)
    images = discover_images(
        wechat_html,
        md_path.parent,
        allowed_roots=[md_path.parent, project, build_dir],
    )

    return _DraftStage(
        config=config,
        article=article,
        wechat_html=wechat_html,
        body=body,
        images=images,
        md_path=md_path,
        project=project,
        preview_path=preview_path,
        wechat_path=wechat_path,
        pub_cfg=pub_cfg,
        env=env,
    )


def _print_dry_run(stage: _DraftStage, appid: str, args: argparse.Namespace) -> None:
    """Print the planned operations; the caller guarantees no network use."""
    article = stage.article
    digest_note = (
        "  (AI digest would be generated on a real run)"
        if getattr(args, "ai_summary", False) and not article.digest
        else ""
    )
    cover_display = str(article.cover)
    cover_check = (
        article.cover if article.cover.is_absolute() else stage.project / article.cover
    )
    if not cover_check.exists():
        cover_display += "   [MISSING]"
    cover_note = (
        "  (AI cover would be generated on a real run)"
        if getattr(args, "ai_cover", False) and not cover_check.exists()
        else ""
    )

    print("=== DRY RUN ===")
    print(f"  title:    {article.title}")
    print(f"  author:   {article.author}")
    print(f"  digest:   {article.digest or '(empty)'}{digest_note}")
    print(f"  cover:    {cover_display}{cover_note}")
    print(f"  images:   {len(stage.images)}")
    for img in stage.images:
        print(f"            {img.original_src}")
    print(f"  html size: {len(stage.wechat_html)} chars")
    print(f"  appid:    {mask_appid(appid)}")

    sample = DraftArticle(
        title=article.title,
        author=article.author,
        digest=article.digest,
        content="(HTML content)",
        thumb_media_id="(cover media_id after upload)",
        content_source_url=article.source_url,
        need_open_comment=article.need_open_comment,
        only_fans_can_comment=article.only_fans_can_comment,
    )
    print("\n=== Draft payload shape ===")
    print(json.dumps(build_draft_payload(sample), ensure_ascii=False, indent=2))
    print("\n[DRY RUN] No API calls were made.")


def _generate_digest_if_requested(
    args: argparse.Namespace, stage: _DraftStage, body: str, article: ArticleMetadata
) -> ArticleMetadata:
    """Fill in an AI digest when --ai-summary is on and none is set."""
    if article.digest or not getattr(args, "ai_summary", False):
        return article

    from .ai_summary import generate_digest, resolve_ai_config
    ai_url, ai_key, ai_model = resolve_ai_config(dict(stage.pub_cfg), dict(stage.env))
    if not ai_key:
        print("[WARN] --ai-summary requested but no API key found; skipping.")
        return article

    print(f"[INFO] generating AI digest via {ai_url} ...")
    ai_digest = generate_digest(body, ai_url, ai_key, ai_model)
    if not ai_digest:
        return article

    print(f"[INFO] AI digest: {ai_digest}")
    return ArticleMetadata(
        title=article.title,
        author=article.author,
        digest=ai_digest,
        cover=article.cover,
        source_url=article.source_url,
        need_open_comment=article.need_open_comment,
        only_fans_can_comment=article.only_fans_can_comment,
    )


def _prepare_cover(
    args: argparse.Namespace, stage: _DraftStage, article: ArticleMetadata
) -> Path:
    """Resolve, optionally AI-generate and optionally compress the cover."""
    cover = article.cover
    if not cover.is_absolute():
        cover = stage.project / cover

    ai_generated = False
    if not cover.exists() and getattr(args, "ai_cover", False):
        from .ai_cover import generate_cover_image, resolve_cover_ai_config
        ai_url, ai_key, ai_model, ai_prompt = resolve_cover_ai_config(
            dict(stage.pub_cfg), dict(stage.env)
        )
        if not ai_key:
            raise _Abort(
                "--ai-cover requested but no API key found "
                "(set it via the .env file or the environment)."
            )
        print(f"[INFO] generating AI cover image for '{article.title}' ...")
        ai_cover_path = stage.config.build_dir / "ai_cover.png"
        try:
            cover = generate_cover_image(
                article.title, ai_cover_path, ai_url, ai_key, ai_model, ai_prompt
            )
            print(f"[INFO] AI cover saved: {cover}")
        except Exception as e:
            raise _Abort(f"AI cover generation failed: {e}") from e
        # AI-generated covers always go through compress_cover: the model
        # output can exceed the 10MB material limit and its format/quality
        # is not user-controlled, unlike a user-provided cover file.
        ai_generated = True

    if not cover.exists():
        raise _Abort(f"Cover image not found: {cover}. Use --cover or --ai-cover.")

    if getattr(args, "compress_cover", False) or ai_generated:
        cover = compress_cover(cover)
    return cover


def _publish_stage(
    args: argparse.Namespace, stage: _DraftStage, appid: str, appsecret: str
) -> int:
    """Network half of the draft flow: token, uploads, draft creation, state."""
    config = stage.config
    ensure_state_dirs(config.state_dir)

    # Move any legacy pre-v0.1.1 caches aside (never read/reused) before the
    # account-scoped layout is used.
    quarantine_legacy_state(config.state_dir)

    # All account-scoped state (token + material caches) lives under
    # accounts/<account-key>/ so switching accounts never reuses old state.
    token_cache, image_cache, cover_cache = account_scoped_paths(
        config.state_dir, appid
    )

    article = _generate_digest_if_requested(
        args, stage, stage.body, stage.article
    )

    token = get_access_token(appid, appsecret, token_cache)
    print(f"[INFO] token: {mask_token(token.value)}")

    cover = _prepare_cover(args, stage, article)

    cover_result = _run_with_token_retry(
        token, appid, appsecret, token_cache,
        lambda tv: upload_cover_image(tv, cover, cover_cache),
    )

    wechat_html = _run_with_token_retry(
        token, appid, appsecret, token_cache,
        lambda tv: process_images(
            tv, stage.wechat_html, stage.images, stage.md_path.parent,
            image_cache,
            allow_missing=getattr(args, "allow_missing_images", False),
            allow_private_networks=config.remote_images_allow_private,
        ),
    )

    draft_article = DraftArticle(
        title=article.title,
        author=article.author,
        digest=article.digest,
        content=wechat_html,
        thumb_media_id=cover_result.media_id,
        content_source_url=article.source_url,
        need_open_comment=article.need_open_comment,
        only_fans_can_comment=article.only_fans_can_comment,
    )

    # Persist the final HTML (with uploaded WeChat image URLs) BEFORE
    # draft/add: writing it after a successful draft creation would risk a
    # "remote draft exists but local artifacts missing" state, and a rerun
    # would create a duplicate draft. This write happens before any remote
    # side effect, so a failure here can simply abort the run.
    write_text_atomic(stage.wechat_path, wechat_html)

    result = _run_with_token_retry(
        token, appid, appsecret, token_cache,
        lambda tv: add_draft(tv, draft_article),
    )

    try:
        state_path = save_post_snapshot(
            config.posts_dir,
            title=article.title,
            appid_hash=account_key(appid),
            draft_media_id=result.media_id,
            source_markdown_path=stage.md_path,
            final_html=wechat_html,
        )
    except OSError as e:
        # The draft already exists remotely; failing silently (or with a
        # generic error) would invite a blind rerun that duplicates it.
        raise RemoteDraftCreatedLocalStateFailed(result.media_id, str(e)) from e
    print(f"[OK] snapshot saved: {state_path}")

    print("\n[OK] Draft created successfully!")
    print(f"  media_id: {result.media_id}")
    print(f"  preview:  {stage.preview_path}")
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    """Create a WeChat draft from a Markdown article."""
    try:
        stage = _render_stage(args)
    except _Abort as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    appid, appsecret = resolve_credentials(stage.pub_cfg, stage.env)

    # Dry-run mode: fully offline -- no credentials, no token, no AI, no network.
    if args.dry_run:
        _print_dry_run(stage, appid, args)
        return 0

    if not appid or not appsecret:
        print("[ERROR] WECHAT_APPID and WECHAT_APPSECRET must be set.", file=sys.stderr)
        return 1

    # Preflight: fail fast on locally-detectable problems before any upload.
    cover_path = None
    if not getattr(args, "ai_cover", False):
        cover = stage.article.cover
        cover_path = cover if cover.is_absolute() else stage.project / cover
    validate_publish_preflight(
        title=stage.article.title,
        author=stage.article.author,
        digest=stage.article.digest,
        need_open_comment=stage.article.need_open_comment,
        only_fans_can_comment=stage.article.only_fans_can_comment,
        content_source_url=stage.article.source_url,
        content_html=stage.wechat_html,
        cover_path=cover_path,
    )

    return _publish_stage(args, stage, appid, appsecret)


# ── main ────────────────────────────────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch commands."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "render":
            return cmd_render(args)
        elif args.command == "draft":
            return cmd_draft(args)
        elif args.command == "inspect":
            return cmd_inspect(args)
        else:
            parser.print_help()
            return 1
    except WeChatAPIError as e:
        print(f"[ERROR] WeChat API call failed:\n{e}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
