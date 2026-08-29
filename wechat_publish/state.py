"""Local state persistence for tokens, caches, and post snapshots."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp file in same dir + os.replace).

    Public wrapper over :func:`_atomic_write_text` for callers outside this
    module that must not leave half-written files behind (e.g. the final
    WeChat HTML, which is persisted before any remote side effect).
    """
    _atomic_write_text(path, text)


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


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive cross-platform advisory lock on a sidecar lock file.

    The lock file is ``<path>.lock`` (created on demand). On Windows the
    region is locked with ``msvcrt.locking`` (``LK_LOCK`` retries internally
    for ~10 seconds before raising ``OSError``); on POSIX ``fcntl.flock``
    with ``LOCK_EX`` blocks until the lock is available. The lock is always
    released and the file descriptor closed in ``finally``.

    Used to serialize load -> modify -> save cycles on shared JSON caches so
    concurrent processes never lose each other's updates. Readers that only
    consume an atomically replaced file (via :func:`load_json_mapping`) do
    not need the lock: they always see either the old or the new complete
    content.
    """
    lock_path = path.parent / (path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass  # Lock release must never mask the protected operation.
    finally:
        os.close(fd)


def save_post_snapshot(
    posts_dir: Path,
    *,
    title: str,
    appid_hash: str,
    draft_media_id: str | None,
    source_markdown_path: Path,
    final_html: str,
) -> Path:
    """Persist an immutable per-publish snapshot and return its state.json.

    Layout (``posts_dir / <snapshot-id>/``)::

        source.md            copy of the original Markdown (bytes)
        final.wechat.html    final HTML as sent to draft/add (atomic write)
        state.json           metadata (atomic write)

    The snapshot id is ``<UTC timestamp with microseconds>Z-<uuid8>``, which
    is collision-free even for identical titles published in the same second
    and contains no characters that are invalid in Windows file names.
    """
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    snapshot_dir = posts_dir / snapshot_id
    try:
        snapshot_dir.mkdir(parents=True)
    except FileExistsError:
        # Theoretically impossible (uuid suffix); retry once with a fresh id.
        snapshot_dir = posts_dir / (
            snapshot_id + "-" + uuid.uuid4().hex[:8]
        )
        snapshot_dir.mkdir(parents=True)

    shutil.copyfile(source_markdown_path, snapshot_dir / "source.md")
    final_html_path = snapshot_dir / "final.wechat.html"
    _atomic_write_text(final_html_path, final_html)

    payload: dict[str, Any] = {
        "title": title,
        "appid_hash": appid_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Hash of the bytes actually on disk (not the in-memory string) so
        # the snapshot stays verifiable even under Windows text-mode CRLF
        # translation.
        "content_sha256": hashlib.sha256(final_html_path.read_bytes()).hexdigest(),
        "source_markdown": "source.md",
        "wechat_html": "final.wechat.html",
    }
    if draft_media_id is not None:
        payload["draft_media_id"] = draft_media_id

    state_path = snapshot_dir / "state.json"
    _atomic_write_text(
        state_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    return state_path
