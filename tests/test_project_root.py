"""Project-root resolution tests: --project-dir, discovery, cwd fallback."""

import argparse
from pathlib import Path

from wechat_publish.cli import (
    _discover_project_dir,
    _render_stage,
    _resolve_project_dir,
)

MARKER = 'name = "wechat-md-publisher"'


def _make_project(root: Path) -> Path:
    """A minimal project root recognized by the discovery markers."""
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "publish.yaml").write_text(
        "default_author: 'YamlAuthor'\n", encoding="utf-8"
    )
    return root


def _args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


# ── _discover_project_dir ───────────────────────────────────────

class TestDiscoverProjectDir:
    def test_project_root_with_publish_yaml(self, tmp_path: Path):
        proj = _make_project(tmp_path)
        assert _discover_project_dir(proj) == proj

    def test_project_root_with_example_yaml_marker(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "publish.example.yaml").write_text("default_author: ''\n")
        assert _discover_project_dir(tmp_path) == tmp_path

    def test_subdirectory_walks_up_to_project_root(self, tmp_path: Path):
        proj = _make_project(tmp_path)
        nested = proj / "src" / "deep"
        nested.mkdir(parents=True)
        assert _discover_project_dir(nested) == proj

    def test_git_dir_is_a_marker(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        assert _discover_project_dir(tmp_path) == tmp_path

    def test_own_pyproject_toml_is_a_marker(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\n{MARKER}\n', encoding="utf-8"
        )
        assert _discover_project_dir(tmp_path) == tmp_path

    def test_foreign_pyproject_is_not_a_marker(self, tmp_path: Path):
        """A pyproject.toml with a different name must not match."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "some-other-package"\n', encoding="utf-8"
        )
        assert _discover_project_dir(tmp_path) == tmp_path  # fallback = start

    def test_foreign_pyproject_does_not_stop_upward_search(self, tmp_path: Path):
        """Walk-up continues past a foreign pyproject.toml to the real root."""
        proj = _make_project(tmp_path)
        child = proj / "vendor" / "other_pkg"
        child.mkdir(parents=True)
        (child / "pyproject.toml").write_text(
            '[project]\nname = "other-package"\n', encoding="utf-8"
        )
        assert _discover_project_dir(child) == proj

    def test_empty_dir_falls_back_to_start(self, tmp_path: Path):
        assert _discover_project_dir(tmp_path) == tmp_path


# ── _resolve_project_dir ────────────────────────────────────────

class TestResolveProjectDir:
    def test_explicit_project_dir_overrides_everything(self, tmp_path: Path):
        proj = _make_project(tmp_path / "proj")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        args = _args(project_dir=proj, config=Path("config/publish.yaml"))
        assert _resolve_project_dir(args) == proj.resolve()

    def test_explicit_config_under_config_dir(self, tmp_path: Path):
        proj = _make_project(tmp_path / "proj")
        args = _args(
            project_dir=None,
            config=proj / "config" / "custom.yaml",
        )
        assert _resolve_project_dir(args) == proj.resolve()

    def test_explicit_config_outside_config_dir(self, tmp_path: Path):
        cfg = tmp_path / "settings" / "publish.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("default_author: 'X'\n")
        args = _args(project_dir=None, config=cfg)
        assert _resolve_project_dir(args) == (tmp_path / "settings").resolve()

    def test_default_config_uses_discovery(self, tmp_path: Path, monkeypatch):
        proj = _make_project(tmp_path / "proj")
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        args = _args(project_dir=None, config=Path("config/publish.yaml"))
        result = _resolve_project_dir(args)
        assert result == empty.resolve()
        assert result != proj.resolve()

    def test_empty_cwd_falls_back_to_cwd_not_site_packages(
        self, tmp_path: Path, monkeypatch
    ):
        empty = tmp_path / "no_markers"
        empty.mkdir()
        monkeypatch.chdir(empty)
        args = _args(project_dir=None)
        result = _resolve_project_dir(args)
        assert result == empty.resolve()
        assert "site-packages" not in str(result)

    def test_discovery_from_cwd(self, tmp_path: Path, monkeypatch):
        proj = _make_project(tmp_path / "proj")
        nested = proj / "docs"
        nested.mkdir()
        monkeypatch.chdir(nested)
        args = _args(project_dir=None)
        assert _resolve_project_dir(args) == proj.resolve()


# ── CLI integration ─────────────────────────────────────────────

class TestCliProjectDirIntegration:
    def test_draft_dry_run_uses_project_dir_config(self, tmp_path: Path, monkeypatch):
        proj = _make_project(tmp_path / "proj")
        article = proj / "input" / "article.md"
        article.parent.mkdir(parents=True)
        article.write_text(
            '---\ntitle: "T"\n---\n\n# Hello\n\nBody.\n', encoding="utf-8"
        )
        # Run from an unrelated directory with no markers.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        from wechat_publish.cli import build_parser

        args = build_parser().parse_args(
            [
                "draft",
                "--md", str(article),
                "--dry-run",
                "--project-dir", str(proj),
            ]
        )
        stage = _render_stage(args)
        assert stage.article.author == "YamlAuthor"
        assert stage.project == proj.resolve()
        assert stage.preview_path.parent == (proj / "build").resolve()

    def test_render_uses_project_dir_for_outputs(self, tmp_path: Path, monkeypatch):
        proj = _make_project(tmp_path / "proj")
        article = proj / "input" / "article.md"
        article.parent.mkdir(parents=True)
        article.write_text("# Hello\n\nBody.\n", encoding="utf-8")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        from wechat_publish.cli import build_parser, cmd_render

        args = build_parser().parse_args(
            [
                "render",
                "--md", str(article),
                "--out", str(proj / "build" / "out.wechat.html"),
                "--preview-out", str(proj / "build" / "out.preview.html"),
                "--project-dir", str(proj),
            ]
        )
        assert cmd_render(args) == 0
        assert (proj / "build" / "out.preview.html").exists()
        assert (proj / "build" / "out.wechat.html").exists()
