"""Shared fixtures: a minimal disposable project directory."""

from pathlib import Path

import pytest


def make_png(path: Path) -> Path:
    """Write a real (Pillow-generated) PNG so tests pass real-format checks."""
    from PIL import Image

    Image.new("RGB", (4, 4), "red").save(path, "PNG")
    return path


@pytest.fixture
def tmp_project(tmp_path: Path, monkeypatch) -> Path:
    """A temp directory shaped like a project root, with credentials set.

    Contains ``config/`` (so _project_dir() resolves to it), an
    ``input/article.md`` with front matter, and WECHAT_APPID/SECRET env vars.
    """
    (tmp_path / "config").mkdir()
    article = tmp_path / "input" / "article.md"
    article.parent.mkdir(parents=True)
    article.write_text(
        '---\ntitle: "测试文章"\nauthor: "Cy257"\n---\n\n# Hello\n\nBody.\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WECHAT_APPID", "wx_test_appid")
    monkeypatch.setenv("WECHAT_APPSECRET", "test_secret")
    return tmp_path
