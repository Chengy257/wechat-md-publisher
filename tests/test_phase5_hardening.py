"""Phase 5 hardening tests: immutable post snapshots, cache file locks,
inspect/draft render-path unification."""

import hashlib
import json
import threading
from pathlib import Path

from conftest import make_png

from wechat_publish.cli import main
from wechat_publish.state import (
    file_lock,
    load_json_mapping,
    save_json_mapping,
    save_post_snapshot,
)

# ── Post snapshots (§16) ────────────────────────────────────────


class TestPostSnapshots:
    @staticmethod
    def _make_source(tmp_path: Path) -> Path:
        source = tmp_path / "input" / "article.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Hello\n\nBody.\n", encoding="utf-8")
        return source

    def test_same_second_snapshots_do_not_collide(self, tmp_path: Path):
        posts_dir = tmp_path / "posts"
        source = self._make_source(tmp_path)
        html = "<p>final</p>"

        p1 = save_post_snapshot(
            posts_dir, title="同秒标题", appid_hash="abc123def456",
            draft_media_id="M1", source_markdown_path=source, final_html=html,
        )
        p2 = save_post_snapshot(
            posts_dir, title="同秒标题", appid_hash="abc123def456",
            draft_media_id="M1", source_markdown_path=source, final_html=html,
        )

        # Two distinct snapshot directories even for the same title/second
        assert p1.parent != p2.parent
        assert p1.parent.parent == posts_dir
        assert p2.parent.parent == posts_dir

        # Each snapshot holds its own independent artifacts
        for p in (p1, p2):
            assert (p.parent / "final.wechat.html").read_text(encoding="utf-8") == html
            assert (p.parent / "source.md").read_text(encoding="utf-8") == "# Hello\n\nBody.\n"
            data = json.loads(p.read_text(encoding="utf-8"))
            assert data["content_sha256"] == hashlib.sha256(
                (p.parent / "final.wechat.html").read_bytes()
            ).hexdigest()

    def test_snapshot_state_fields_complete(self, tmp_path: Path):
        posts_dir = tmp_path / "posts"
        source = self._make_source(tmp_path)
        html = "<section>hello</section>"

        state_path = save_post_snapshot(
            posts_dir, title="字段齐全", appid_hash="deadbeefcafe",
            draft_media_id="MEDIA_X", source_markdown_path=source, final_html=html,
        )
        data = json.loads(state_path.read_text(encoding="utf-8"))

        assert data["title"] == "字段齐全"
        assert data["appid_hash"] == "deadbeefcafe"
        assert data["draft_media_id"] == "MEDIA_X"
        assert data["created_at"]  # ISO timestamp present
        assert data["content_sha256"] == hashlib.sha256(
            (state_path.parent / "final.wechat.html").read_bytes()
        ).hexdigest()
        assert data["source_markdown"] == "source.md"
        assert data["wechat_html"] == "final.wechat.html"

    def test_content_sha256_matches_file_bytes_with_crlf_translation(
        self, tmp_path: Path
    ):
        """Regression: content_sha256 must hash the bytes actually on disk.

        The final HTML contains newlines; on Windows the text-mode write
        translates LF -> CRLF, so hashing the in-memory string would record
        a digest that never matches the snapshot file. The digest recorded
        in state.json must equal sha256 of the file's real bytes.
        """
        posts_dir = tmp_path / "posts"
        source = self._make_source(tmp_path)
        html = "<html>\n<body>\n<p>line1</p>\n<p>line2</p>\n</body>\n</html>\n"

        state_path = save_post_snapshot(
            posts_dir, title="T", appid_hash="h", draft_media_id="M",
            source_markdown_path=source, final_html=html,
        )
        data = json.loads(state_path.read_text(encoding="utf-8"))
        on_disk = (state_path.parent / "final.wechat.html").read_bytes()
        assert hashlib.sha256(on_disk).hexdigest() == data["content_sha256"]

    def test_snapshot_without_media_id_omits_field(self, tmp_path: Path):
        posts_dir = tmp_path / "posts"
        source = self._make_source(tmp_path)
        state_path = save_post_snapshot(
            posts_dir, title="T", appid_hash="h", draft_media_id=None,
            source_markdown_path=source, final_html="<p></p>",
        )
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert "draft_media_id" not in data

    def test_snapshot_id_is_windows_safe(self, tmp_path: Path):
        posts_dir = tmp_path / "posts"
        source = self._make_source(tmp_path)
        state_path = save_post_snapshot(
            posts_dir, title="T", appid_hash="h", draft_media_id=None,
            source_markdown_path=source, final_html="<p></p>",
        )
        dir_id = state_path.parent.name
        assert ":" not in dir_id
        assert not set(dir_id) & set('*?"<>|/')


# ── Cache file locks (§20) ──────────────────────────────────────


class TestFileLock:
    def test_concurrent_read_modify_write_keeps_all_keys(self, tmp_path: Path):
        cache_path = tmp_path / "accounts" / "acc" / "cover_cache.json"
        errors: list[Exception] = []
        rounds = 5

        def worker(i: int) -> None:
            try:
                for _ in range(rounds):
                    with file_lock(cache_path):
                        data = dict(load_json_mapping(cache_path))
                        data[f"hash_{i}"] = f"url_{i}"
                        save_json_mapping(cache_path, data)
            except Exception as e:  # pragma: no cover - diagnostics only
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        data = load_json_mapping(cache_path)
        assert len(data) == 10  # no lost updates
        assert all(data[f"hash_{i}"] == f"url_{i}" for i in range(10))

    def test_sequential_reacquisition_succeeds(self, tmp_path: Path):
        lock_target = tmp_path / "cache.json"
        with file_lock(lock_target):
            pass
        # The sidecar lock file exists and the lock can be re-acquired.
        assert (tmp_path / "cache.json.lock").exists()
        with file_lock(lock_target):
            save_json_mapping(lock_target, {"ok": True})
        assert load_json_mapping(lock_target) == {"ok": True}

    def test_releases_lock_on_exception(self, tmp_path: Path):
        lock_target = tmp_path / "cache.json"
        try:
            with file_lock(lock_target):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with file_lock(lock_target):
            pass  # must not hang or raise


# ── inspect unification (§17) ───────────────────────────────────


class TestInspectUnification:
    def test_inspect_title_override(self, tmp_project: Path, capsys):
        rc = main(["inspect", "--md", "input/article.md", "--title", "覆盖标题"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Resolved Metadata ===" in out
        assert "覆盖标题" in out
        assert "测试文章" not in out  # front-matter title was overridden

    def test_inspect_finds_images_outside_md_dir(self, tmp_project: Path, capsys):
        # Image inside the project root but outside the markdown directory.
        assets = tmp_project / "assets"
        assets.mkdir(exist_ok=True)
        make_png(assets / "pic.png")
        article = tmp_project / "input" / "article.md"
        article.write_text(
            '---\ntitle: "T"\n---\n\n![pic](../assets/pic.png)\n',
            encoding="utf-8",
        )

        rc = main(["inspect", "--md", "input/article.md"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Images (1) ===" in out
        assert "pic.png" in out
        assert "remote" not in out

    def test_inspect_writes_nothing(self, tmp_project: Path):
        rc = main(["inspect", "--md", "input/article.md"])
        assert rc == 0
        assert not (tmp_project / "build").exists()

    def test_inspect_missing_md_returns_error(self, tmp_project: Path, capsys):
        rc = main(["inspect", "--md", "input/missing.md"])
        assert rc == 1
        assert "not found" in capsys.readouterr().err
