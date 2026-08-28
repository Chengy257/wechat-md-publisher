"""Local state persistence for tokens, caches, and post records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

@dataclass(frozen=True)
class PostState:
    """Local record for one rendered or drafted article."""

    title: str
    source_markdown: Path
    wechat_html: Path
    mode: str
    draft_media_id: str | None = None
    images: dict[str, str] | None = None


def ensure_state_dirs(state_dir: Path) -> None:
    """Create local state directories."""
    posts_dir = state_dir / "posts"
    state_dir.mkdir(parents=True, exist_ok=True)
    posts_dir.mkdir(parents=True, exist_ok=True)


def load_json_mapping(path: Path) -> Mapping[str, object]:
    """Load a JSON mapping from disk. Returns empty dict if file is missing."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_json_mapping(path: Path, data: Mapping[str, Any]) -> None:
    """Save a JSON mapping to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    import re

    slug = re.sub(r"[^\w\u4e00-\u9fff\u3400-\u4dbf]+", "-", title.strip())
    slug = slug.strip("-")
    return slug[:60] if slug else "untitled"


def save_post_state(posts_dir: Path, state: PostState) -> Path:
    """Save a per-post state file and return its path."""
    posts_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    slug = _slugify(state.title)
    filename = f"{now}-{slug}.json"
    path = posts_dir / filename

    payload: dict[str, Any] = {
        "title": state.title,
        "source_markdown": str(state.source_markdown),
        "wechat_html": str(state.wechat_html),
        "mode": state.mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if state.draft_media_id is not None:
        payload["draft_media_id"] = state.draft_media_id

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
