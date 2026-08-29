"""Configuration loading and precedence resolution."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    token_cache: Path
    image_cache: Path
    cover_cache: Path
    posts_dir: Path


# Environment variable name alternatives
_APPID_ENVS = ("WECHAT_APPID", "WECHAT_APP_ID")
_APPSECRET_ENVS = ("WECHAT_APPSECRET", "WECHAT_APP_SECRET")
_AUTHOR_ENVS = ("WECHAT_DEFAULT_AUTHOR", "WECHAT_AUTHOR")

# Per-account state files that are deprecated as explicit config keys since
# v0.1.1: they are now always account-scoped under <state_dir>/accounts/.
_DEPRECATED_CACHE_KEYS = ("token_cache", "image_cache", "cover_cache")


def account_key(appid: str) -> str:
    """Return the filesystem-safe account namespace key for an appid."""
    return hashlib.sha256(appid.encode("utf-8")).hexdigest()[:12]


def account_scoped_paths(state_dir: Path, appid: str) -> tuple[Path, Path, Path]:
    """Return (token_cache, image_cache, cover_cache) scoped to one account.

    Each account gets its own directory under ``<state_dir>/accounts/`` so
    tokens and uploaded-material caches are never reused across accounts.
    """
    scoped_dir = state_dir / "accounts" / account_key(appid)
    return (
        scoped_dir / "token.json",
        scoped_dir / "image_cache.json",
        scoped_dir / "cover_cache.json",
    )


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

_THEMES_DIR = Path(__file__).resolve().parent / "themes"


def _builtin_theme_path(name: str) -> Path | None:
    """Return a bundled theme CSS path, or None when not shipped."""
    candidate = _THEMES_DIR / f"{name}.css"
    return candidate if candidate.exists() else None


def resolve_style_path(
    *,
    style_arg: Path | None,
    theme_arg: str | None,
    project_dir: Path,
) -> Path:
    """Resolve the CSS theme path from CLI args.

    Priority: --style > --theme > project config/style.css > bundled
    default.css (so a default run always gets styled, sanitized output).
    """
    if style_arg is not None:
        return style_arg
    if theme_arg and theme_arg in BUILTIN_THEMES:
        builtin = _builtin_theme_path(theme_arg)
        if builtin is not None:
            return builtin
    project_style = project_dir / "config" / "style.css"
    if project_style.exists():
        return project_style
    builtin_default = _builtin_theme_path("default")
    if builtin_default is not None:
        return builtin_default
    return project_style


def normalize_string_or_list(value: Any, *, field: str) -> list[str]:
    """Normalize a config value to a non-empty list of strings.

    Accepts a single string (wrapped into a one-element list) or a
    list/tuple of non-empty strings. Anything else raises ``ValueError``
    with the config field name in the message.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, str) and item for item in value):
            return list(value)
    raise ValueError(
        f"config field '{field}' must be a string or a list of strings "
        f"(got: {value!r})"
    )


def load_publish_config(path: Path) -> Mapping[str, Any]:
    """Load publish config; missing file yields built-in defaults ({}).

    The bundled ``publish.example.yaml`` is documentation-only: it is never
    read at runtime. A missing config prints an INFO hint to stderr.
    """
    if not path.exists():
        print(
            f"[INFO] no publish config at {path}; using built-in defaults "
            f"(copy config/publish.example.yaml to customize).",
            file=sys.stderr,
        )
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


_KNOWN_CONFIG_KEYS = {"default_author", "article", "paths", "wechat", "ai"}


def resolve_config(
    *,
    cli_values: Mapping[str, Any],
    front_matter: Mapping[str, Any],
    publish_config: Mapping[str, Any],
    env: Mapping[str, str | None],
    project_dir: Path | None = None,
) -> PublisherConfig:
    """Resolve config using CLI > front matter > YAML config > environment defaults.

    When *project_dir* is given, all relative output paths are anchored to it
    (otherwise they stay relative to the process working directory).
    """
    unknown = set(publish_config) - _KNOWN_CONFIG_KEYS
    if unknown:
        print(
            f"[WARN] publish config: ignoring unknown key(s): "
            f"{', '.join(sorted(unknown))}",
            file=sys.stderr,
        )

    article_cfg = publish_config.get("article", {})
    paths_cfg = publish_config.get("paths", {})
    wechat_cfg = publish_config.get("wechat", {})

    # Author resolution: CLI > front_matter > publish.yaml > env
    author = (
        cli_values.get("author")
        or front_matter.get("author")
        or article_cfg.get("default_author")
        or publish_config.get("default_author")
        or _first_env(
            env,
            tuple(
                normalize_string_or_list(
                    wechat_cfg.get("author_env", _AUTHOR_ENVS),
                    field="wechat.author_env",
                )
            ),
        )
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

    # Paths: optionally anchored to the project directory
    def _anchor(rel_path: str | Path) -> Path:
        path = Path(rel_path)
        return project_dir / path if project_dir is not None and not path.is_absolute() else path

    build_dir = _anchor(paths_cfg.get("build_dir", "build"))
    state_dir = _anchor(paths_cfg.get("state_dir", ".wechat_publish"))

    # Deprecated keys are still parsed (fields stay populated) but the publish
    # stage now always uses account-scoped cache paths; warn so configs get fixed.
    for key in _DEPRECATED_CACHE_KEYS:
        if key in paths_cfg:
            print(
                f"[WARN] paths.{key} is deprecated since v0.1.1; token/image/cover "
                f"caches are account-scoped under {state_dir}/accounts/<account_key>/",
                file=sys.stderr,
            )

    token_cache = _anchor(paths_cfg["token_cache"]) if "token_cache" in paths_cfg else state_dir / "token.json"
    image_cache = _anchor(paths_cfg["image_cache"]) if "image_cache" in paths_cfg else state_dir / "image_cache.json"
    cover_cache = _anchor(paths_cfg["cover_cache"]) if "cover_cache" in paths_cfg else state_dir / "cover_cache.json"
    posts_dir = _anchor(paths_cfg["posts_dir"]) if "posts_dir" in paths_cfg else state_dir / "posts"

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

    appid_keys = normalize_string_or_list(
        wechat_cfg.get("appid_env", _APPID_ENVS), field="wechat.appid_env"
    )
    appid = _first_env(env, tuple(appid_keys)) or os.environ.get("WECHAT_APPID", "")

    appsecret_keys = normalize_string_or_list(
        wechat_cfg.get("appsecret_env", _APPSECRET_ENVS),
        field="wechat.appsecret_env",
    )
    appsecret = _first_env(env, tuple(appsecret_keys)) or os.environ.get(
        "WECHAT_APPSECRET", ""
    )

    return appid, appsecret
