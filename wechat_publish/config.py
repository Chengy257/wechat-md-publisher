"""Configuration loading and precedence resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from dotenv import dotenv_values


@dataclass(frozen=True)
class ArticleMetadata:
    """Resolved article metadata for rendering and draft creation."""

    title: str
    author: str
    digest: str
    cover: Path
    source_url: str = ""
    need_open_comment: int = 1
    only_fans_can_comment: int = 0


@dataclass(frozen=True)
class PublisherConfig:
    """Resolved publisher configuration."""

    article: ArticleMetadata
    build_dir: Path
    state_dir: Path
    mode: str = "draft"
    token_cache: Path | None = None
    image_cache: Path | None = None
    cover_cache: Path | None = None
    posts_dir: Path | None = None


# Environment variable name alternatives
_APPID_ENVS = ("WECHAT_APPID", "WECHAT_APP_ID")
_APPSECRET_ENVS = ("WECHAT_APPSECRET", "WECHAT_APP_SECRET")
_AUTHOR_ENVS = ("WECHAT_DEFAULT_AUTHOR", "WECHAT_AUTHOR")


def _first_env(env: Mapping[str, str | None], keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty value from *keys* in *env*."""
    for key in keys:
        val = env.get(key)
        if val:
            return val
    return None


def _load_dotenv(path: Path | None = None) -> Mapping[str, str | None]:
    """Load .env file values. Does not override existing env vars."""
    if path is None:
        cwd = Path.cwd()
        for _ in range(5):
            candidate = cwd / ".env"
            if candidate.exists():
                path = candidate
                break
            cwd = cwd.parent
    if path is None:
        return {}
    return dotenv_values(path)


def load_env_values(project_dir: Path | None = None) -> Mapping[str, str | None]:
    """Load environment values from os.environ + .env file.

    Real environment variables take precedence over .env file values, and
    every variable is exposed (AI keys and custom ``*_env`` names resolve
    from the real environment too, not just the .env file).
    """
    dotenv_vals = _load_dotenv(
        project_dir / ".env" if project_dir else None
    )
    merged: dict[str, str | None] = dict(dotenv_vals)
    merged.update(os.environ)
    return merged


def load_theme_css(path: Path) -> str:
    """Load a CSS theme file. Returns empty string if not found."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


BUILTIN_THEMES = {"default", "elegant", "lapis", "simple", "tech"}


def resolve_style_path(
    *,
    style_arg: Path | None,
    theme_arg: str | None,
    project_dir: Path,
) -> Path:
    """Resolve the CSS theme path from CLI args.

    Priority: --style > --theme > project config/style.css
    """
    if style_arg is not None:
        return style_arg
    if theme_arg and theme_arg in BUILTIN_THEMES:
        builtin = Path(__file__).resolve().parent.parent / "config" / "styles" / f"{theme_arg}.css"
        if builtin.exists():
            return builtin
    return project_dir / "config" / "style.css"


def load_publish_config(path: Path) -> Mapping[str, Any]:
    """Load publish config, falling back to .example variant."""
    if not path.exists():
        example = path.with_suffix(".example.yaml")
        if example.exists():
            path = example
        else:
            return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def resolve_config(
    *,
    cli_values: Mapping[str, Any],
    front_matter: Mapping[str, Any],
    publish_config: Mapping[str, Any],
    env: Mapping[str, str | None],
) -> PublisherConfig:
    """Resolve config using CLI > front matter > YAML config > environment defaults."""
    article_cfg = publish_config.get("article", {})
    paths_cfg = publish_config.get("paths", {})
    wechat_cfg = publish_config.get("wechat", {})

    # Author resolution: CLI > front_matter > publish.yaml > env
    author = (
        cli_values.get("author")
        or front_matter.get("author")
        or article_cfg.get("default_author")
        or publish_config.get("default_author")
        or _first_env(env, tuple(wechat_cfg.get("author_env", _AUTHOR_ENVS)))
        or ""
    )

    # Title: CLI > front_matter
    title = cli_values.get("title") or front_matter.get("title") or ""

    # Digest: CLI > front_matter(summary)
    digest = (
        cli_values.get("digest")
        or front_matter.get("digest")
        or front_matter.get("summary")
        or ""
    )

    # Cover: CLI > front_matter > publish.yaml
    cover_str = (
        cli_values.get("cover")
        or front_matter.get("cover")
        or article_cfg.get("cover")
        or "input/cover.png"
    )
    cover = Path(cover_str) if cover_str else Path("input/cover.png")

    # Source URL
    source_url = (
        cli_values.get("source_url")
        or front_matter.get("source_url")
        or article_cfg.get("source_url")
        or ""
    )

    # Comment settings
    need_open_comment = int(
        front_matter.get("need_open_comment", article_cfg.get("need_open_comment", 1))
    )
    only_fans_can_comment = int(
        front_matter.get(
            "only_fans_can_comment", article_cfg.get("only_fans_can_comment", 0)
        )
    )

    # Paths
    build_dir = Path(paths_cfg.get("build_dir", "build"))
    state_dir = Path(paths_cfg.get("state_dir", ".wechat_publish"))
    token_cache = Path(paths_cfg["token_cache"]) if "token_cache" in paths_cfg else None
    image_cache = Path(paths_cfg["image_cache"]) if "image_cache" in paths_cfg else None
    cover_cache = Path(paths_cfg["cover_cache"]) if "cover_cache" in paths_cfg else None
    posts_dir = Path(paths_cfg["posts_dir"]) if "posts_dir" in paths_cfg else None

    # Mode
    mode = (
        cli_values.get("mode")
        or front_matter.get("mode")
        or publish_config.get("default_mode")
        or "draft"
    )

    article = ArticleMetadata(
        title=title,
        author=author,
        digest=digest,
        cover=cover,
        source_url=source_url,
        need_open_comment=need_open_comment,
        only_fans_can_comment=only_fans_can_comment,
    )

    return PublisherConfig(
        article=article,
        build_dir=build_dir,
        state_dir=state_dir,
        mode=mode,
        token_cache=token_cache,
        image_cache=image_cache,
        cover_cache=cover_cache,
        posts_dir=posts_dir,
    )


def resolve_credentials(
    publish_config: Mapping[str, Any],
    env: Mapping[str, str | None],
) -> tuple[str, str]:
    """Resolve (appid, appsecret) from config hints and environment."""
    wechat_cfg = publish_config.get("wechat", {})

    appid_keys = tuple(wechat_cfg.get("appid_env", _APPID_ENVS))
    appid = _first_env(env, appid_keys) or os.environ.get("WECHAT_APPID", "")

    appsecret_keys = tuple(wechat_cfg.get("appsecret_env", _APPSECRET_ENVS))
    appsecret = _first_env(env, appsecret_keys) or os.environ.get("WECHAT_APPSECRET", "")

    return appid, appsecret
