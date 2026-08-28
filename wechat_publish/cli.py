"""Command line entry points for the WeChat publisher CLI."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from .config import (
    ArticleMetadata,
    BUILTIN_THEMES,
    PublisherConfig,
    load_env_values,
    load_publish_config,
    load_theme_css,
    resolve_config,
    resolve_credentials,
    resolve_style_path,
)
from .draft import DraftArticle, add_draft, build_draft_payload
from .errors import WeChatAPIError
from .html_processor import (
    convert_links_to_footnotes,
    discover_images,
    inline_css,
    make_wechat_compatible,
    sanitize_html_fragment,
)
from .images import compress_cover, process_images, upload_cover_image
from .render import parse_front_matter, render_article
from .state import PostState, ensure_state_dirs, save_post_state
from .token import get_access_token, mask_appid, mask_token

_T = TypeVar("_T")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="wechat-publish",
        description="Render Markdown articles into WeChat Official Account drafts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- render ---
    render_p = subparsers.add_parser("render", help="Render Markdown to WeChat HTML.")
    render_p.add_argument("--md", type=Path, default=Path("input/article.md"),
                          help="Path to Markdown article (default: input/article.md)")
    render_p.add_argument("--out", type=Path, default=Path("build/article.wechat.html"),
                          help="Output WeChat HTML path")
    render_p.add_argument("--preview-out", type=Path, default=Path("build/article.preview.html"),
                          help="Output preview HTML path")
    render_p.add_argument("--style", type=Path, default=None,
                          help="Path to CSS theme file (overrides --theme)")
    render_p.add_argument("--theme", default=None, choices=sorted(BUILTIN_THEMES),
                          help="Built-in theme name")

    # --- draft ---
    draft_p = subparsers.add_parser("draft", help="Create a WeChat draft from Markdown.")
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
    draft_p.add_argument("--style", type=Path, default=None,
                         help="Path to CSS theme file (overrides --theme)")
    draft_p.add_argument("--theme", default=None, choices=sorted(BUILTIN_THEMES),
                         help="Built-in theme name")
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
    inspect_p.add_argument("--md", type=Path, default=Path("input/article.md"),
                           help="Path to Markdown article")
    inspect_p.add_argument("--config", type=Path, default=Path("config/publish.yaml"),
                           help="Path to publish config YAML")
    inspect_p.add_argument("--style", type=Path, default=None,
                           help="Path to CSS theme file (overrides --theme)")
    inspect_p.add_argument("--theme", default=None, choices=sorted(BUILTIN_THEMES),
                           help="Built-in theme name")

    return parser


def _project_dir() -> Path:
    """Return the project root directory (where config/ and input/ live)."""
    cwd = Path.cwd()
    if (cwd / "config").is_dir():
        return cwd
    return Path(__file__).resolve().parent.parent


def _resolve_cli_values(args: argparse.Namespace) -> dict[str, Any]:
    """Extract CLI override values from parsed args."""
    values: dict[str, Any] = {}
    for key in ("title", "author", "digest", "cover"):
        val = getattr(args, key, None)
        if val is not None:
            values[key] = str(val)
    return values


def _process_html(html: str, theme_css: str) -> str:
    """Run the full HTML processing pipeline: sanitize → compat → footnotes → CSS inline."""
    html = sanitize_html_fragment(html)
    html = make_wechat_compatible(html)
    html = convert_links_to_footnotes(html)
    html = inline_css(html, theme_css)
    return html


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
    """Render Markdown to preview and WeChat HTML."""
    md_path = args.md
    if not md_path.exists():
        print(f"[ERROR] Markdown file not found: {md_path}", file=sys.stderr)
        return 1

    project = _project_dir()
    style_path = resolve_style_path(
        style_arg=args.style, theme_arg=args.theme, project_dir=project
    )
    theme_css = load_theme_css(style_path)

    result = render_article(
        md_path,
        theme_css=theme_css,
        build_dir=args.out.parent if args.out else None,
        preview_path=args.preview_out,
        wechat_path=args.out,
    )

    print(f"[OK] preview: {result.preview_path}")
    print(f"[OK] wechat:  {result.wechat_path}")
    return 0


# ── inspect command ─────────────────────────────────────────────

def cmd_inspect(args: argparse.Namespace) -> int:
    """Inspect resolved metadata and assets."""
    md_path = args.md
    if not md_path.exists():
        print(f"[ERROR] Markdown file not found: {md_path}", file=sys.stderr)
        return 1

    project = _project_dir()
    pub_cfg = load_publish_config(project / args.config)
    style_path = resolve_style_path(
        style_arg=args.style, theme_arg=args.theme, project_dir=project
    )
    theme_css = load_theme_css(style_path)
    env = load_env_values(project)

    # Parse front matter
    text = md_path.read_text(encoding="utf-8")
    front_matter, _ = parse_front_matter(text)

    cli_values = _resolve_cli_values(args)
    config = resolve_config(
        cli_values=cli_values,
        front_matter=front_matter,
        publish_config=pub_cfg,
        env=env,
    )

    article = config.article
    print("=== Resolved Metadata ===")
    print(f"  title:    {article.title or '(empty)'}")
    print(f"  author:   {article.author or '(empty)'}")
    print(f"  digest:   {article.digest or '(empty)'}")
    print(f"  cover:    {article.cover}")
    print(f"  mode:     {config.mode}")

    # Render to discover images
    _, body = parse_front_matter(text)
    from .render import render_markdown_to_html
    raw_html = render_markdown_to_html(body)
    processed = _process_html(raw_html, theme_css)

    images = discover_images(processed, md_path.parent)
    print(f"\n=== Images ({len(images)}) ===")
    for img in images:
        location = "remote" if img.is_remote else f"local: {img.resolved_path}"
        print(f"  {img.original_src}  ({location})")

    # Show credentials status (masked)
    appid, _ = resolve_credentials(pub_cfg, env)
    if appid:
        print("\n=== Credentials ===")
        print(f"  appid: {mask_appid(appid)}")
    else:
        print("\n=== Credentials ===")
        print("  [WARN] No WECHAT_APPID found in environment")

    return 0


# ── draft command ───────────────────────────────────────────────

def cmd_draft(args: argparse.Namespace) -> int:
    """Create a WeChat draft from a Markdown article."""
    md_path = args.md
    if not md_path.exists():
        print(f"[ERROR] Markdown file not found: {md_path}", file=sys.stderr)
        return 1

    project = _project_dir()
    pub_cfg = load_publish_config(project / args.config)
    style_path = resolve_style_path(
        style_arg=args.style, theme_arg=args.theme, project_dir=project
    )
    theme_css = load_theme_css(style_path)
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

    # Resolve configuration
    config = resolve_config(
        cli_values=cli_values,
        front_matter=front_matter,
        publish_config=pub_cfg,
        env=env,
    )

    article = config.article
    if not article.title:
        print(
            "[ERROR] Article title is required. Use --title, "
            "--autofill-front-matter, or add front matter.",
            file=sys.stderr,
        )
        return 1

    # Render Markdown (no styling — raw HTML)
    from .render import render_markdown_to_html, _wrap_preview
    raw_html = render_markdown_to_html(body)
    build_dir = config.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)

    preview_path = build_dir / "article.preview.html"
    wechat_path = build_dir / "article.wechat.html"

    # Process HTML
    wechat_html = _process_html(raw_html, theme_css)

    # Render mermaid diagrams if requested
    if getattr(args, "mermaid", False):
        from .mermaid import replace_mermaid_blocks
        mermaid_dir = build_dir / "mermaid"
        wechat_html = replace_mermaid_blocks(
            wechat_html, mermaid_dir, engine=args.mermaid_engine,
            src_base_dir=md_path.parent,
        )

    # Save preview
    title = str(front_matter.get("title", ""))
    preview_html = _wrap_preview(wechat_html, title)
    preview_path.write_text(preview_html, encoding="utf-8")

    # Save wechat html
    wechat_path.write_text(wechat_html, encoding="utf-8")

    # Discover images (markdown dir, project root and build dir are trusted)
    images = discover_images(
        wechat_html,
        md_path.parent,
        allowed_roots=[md_path.parent, project, build_dir],
    )

    # Ensure state directories
    ensure_state_dirs(config.state_dir)
    token_cache = config.token_cache or config.state_dir / "token.json"
    image_cache = config.image_cache or config.state_dir / "image_cache.json"
    cover_cache = config.cover_cache or config.state_dir / "cover_cache.json"
    posts_dir = config.posts_dir or config.state_dir / "posts"

    # Resolve credentials
    appid, appsecret = resolve_credentials(pub_cfg, env)
    if not appid or not appsecret:
        print("[ERROR] WECHAT_APPID and WECHAT_APPSECRET must be set.", file=sys.stderr)
        return 1

    # Dry-run mode: everything above is local; nothing below touches the network
    if args.dry_run:
        digest_note = (
            "  (AI digest would be generated on a real run)"
            if getattr(args, "ai_summary", False) and not article.digest
            else ""
        )
        cover_display = str(article.cover)
        cover_check = (
            article.cover if article.cover.is_absolute() else project / article.cover
        )
        if not cover_check.exists():
            cover_display += "   [MISSING]"

        print("=== DRY RUN ===")
        print(f"  title:    {article.title}")
        print(f"  author:   {article.author}")
        print(f"  digest:   {article.digest or '(empty)'}{digest_note}")
        print(f"  cover:    {cover_display}")
        print(f"  images:   {len(images)}")
        for img in images:
            print(f"            {img.original_src}")
        print(f"  html size: {len(wechat_html)} chars")
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
        return 0

    # --- Real execution (network from here on) ---

    # 0. AI digest (opt-in)
    if not article.digest and getattr(args, "ai_summary", False):
        from .ai_summary import generate_digest, resolve_ai_config
        ai_url, ai_key, ai_model = resolve_ai_config(dict(pub_cfg), dict(env))
        if ai_key:
            print(f"[INFO] generating AI digest via {ai_url} ...")
            ai_digest = generate_digest(body, ai_url, ai_key, ai_model)
            if ai_digest:
                article = ArticleMetadata(
                    title=article.title,
                    author=article.author,
                    digest=ai_digest,
                    cover=article.cover,
                    source_url=article.source_url,
                    need_open_comment=article.need_open_comment,
                    only_fans_can_comment=article.only_fans_can_comment,
                )
                print(f"[INFO] AI digest: {ai_digest}")
        else:
            print("[WARN] --ai-summary requested but no API key found; skipping.")

    # 1. Get access token
    token = get_access_token(appid, appsecret, token_cache)
    print(f"[INFO] token: {mask_token(token.value)}")

    # 2. Upload cover image (generate via AI if requested and missing)
    cover = article.cover
    if not cover.is_absolute():
        cover = project / cover

    if not cover.exists() and getattr(args, "ai_cover", False):
        from .ai_cover import generate_cover_image, resolve_cover_ai_config
        ai_url, ai_key, ai_model, ai_prompt = resolve_cover_ai_config(
            dict(pub_cfg), dict(env)
        )
        if not ai_key:
            print(
                f"[ERROR] --ai-cover requested but no API key found "
                f"(set it via the .env file or the environment).",
                file=sys.stderr,
            )
            return 1
        print(f"[INFO] generating AI cover image for '{article.title}' ...")
        ai_cover_path = build_dir / "ai_cover.png"
        try:
            cover = generate_cover_image(
                article.title, ai_cover_path, ai_url, ai_key, ai_model, ai_prompt
            )
            print(f"[INFO] AI cover saved: {cover}")
        except Exception as e:
            print(f"[ERROR] AI cover generation failed: {e}", file=sys.stderr)
            return 1

    if not cover.exists():
        print(
            f"[ERROR] Cover image not found: {cover}. "
            f"Use --cover or --ai-cover.",
            file=sys.stderr,
        )
        return 1

    if getattr(args, "compress_cover", False):
        cover = compress_cover(cover)

    cover_result = _run_with_token_retry(
        token, appid, appsecret, token_cache,
        lambda tv: upload_cover_image(tv, cover, cover_cache),
    )

    # 3. Upload body images and replace src
    wechat_html = _run_with_token_retry(
        token, appid, appsecret, token_cache,
        lambda tv: process_images(
            tv, wechat_html, images, md_path.parent, image_cache,
            allow_missing=getattr(args, "allow_missing_images", False),
        ),
    )

    # 4. Create draft
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
    result = _run_with_token_retry(
        token, appid, appsecret, token_cache,
        lambda tv: add_draft(tv, draft_article),
    )

    # 5. Save state
    state = PostState(
        title=article.title,
        source_markdown=md_path,
        wechat_html=wechat_path,
        draft_media_id=result.media_id,
    )
    state_path = save_post_state(posts_dir, state)
    print(f"[OK] state saved: {state_path}")

    # 6. Update wechat HTML on disk with final processed version
    wechat_path.write_text(wechat_html, encoding="utf-8")

    print("\n[OK] Draft created successfully!")
    print(f"  media_id: {result.media_id}")
    print(f"  preview:  {preview_path}")

    return 0


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
