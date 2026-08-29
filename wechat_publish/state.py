"""Local state persistence for tokens, caches, and post records."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PostState:
    """Local record for one rendered or drafted article."""

    title: str
    source_markdown: Path
    wechat_html: Path
    draft_media_id: str | None = None


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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to a file atomically (temp file in same dir + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def save_json_mapping(path: Path, data: Mapping[str, Any]) -> None:
    """Save a JSON mapping to disk atomically."""
    _atomic_write_text(
        path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )


# Legacy (pre-v0.1.1) state files that used to live directly under state_dir.
_LEGACY_STATE_FILES = ("token.json", "image_cache.json", "cover_cache.json")


def quarantine_legacy_state(state_dir: Path) -> None:
    """Move legacy pre-v0.1.1 cache files into ``<state_dir>/legacy/``.

    Legacy files are never read or reused (they are not account-scoped); they
    are only moved aside so the new account-scoped layout can take over. The
    operation is idempotent and a no-op when no legacy files exist.
    """
    moved: list[str] = []
    legacy_dir = state_dir / "legacy"
    for name in _LEGACY_STATE_FILES:
        src = state_dir / name
        if src.is_file():
            legacy_dir.mkdir(parents=True, exist_ok=True)
            os.replace(src, legacy_dir / name)
            moved.append(name)
    if moved:
        print(
            f"[WARN] legacy pre-v0.1.1 state moved to {legacy_dir}; "
            f"token will be re-acquired and caches rebuilt."
        )


def _slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\u4e00-\u9fff\u3400-\u4dbf]+", "-", title.strip())
    slug = slug.strip("-")
    return slug[:60] if slug else "untitled"


def save_post_state(posts_dir: Path, state: PostState) -> Path:
    """Save a per-post state file and return its path."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    slug = _slugify(state.title)
    filename = f"{now}-{slug}.json"
    path = posts_dir / filename

    payload: dict[str, Any] = {
        "title": state.title,
        "source_markdown": str(state.source_markdown),
        "wechat_html": str(state.wechat_html),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if state.draft_media_id is not None:
        payload["draft_media_id"] = state.draft_media_id

    _atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    return path
