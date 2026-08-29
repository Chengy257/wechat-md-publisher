"""Per-endpoint image format whitelists and real byte-level format validation."""

from pathlib import Path

import pytest
from PIL import Image

from wechat_publish.images import validate_body_image, validate_cover_image


def _save(path: Path, fmt: str) -> Path:
    Image.new("RGB", (4, 4), "red").save(path, fmt)
    return path


# ── per-endpoint whitelists ─────────────────────────────────────

class TestEndpointWhitelists:
    def test_body_jpg_accepted(self, tmp_path: Path):
        validate_body_image(_save(tmp_path / "a.jpg", "JPEG"))

    def test_body_jpeg_accepted(self, tmp_path: Path):
        validate_body_image(_save(tmp_path / "a.jpeg", "JPEG"))

    def test_body_png_accepted(self, tmp_path: Path):
        validate_body_image(_save(tmp_path / "a.png", "PNG"))

    def test_body_gif_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not allowed"):
            validate_body_image(_save(tmp_path / "a.gif", "GIF"))

    def test_body_bmp_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not allowed"):
            validate_body_image(_save(tmp_path / "a.bmp", "BMP"))

    def test_cover_gif_accepted(self, tmp_path: Path):
        validate_cover_image(_save(tmp_path / "c.gif", "GIF"))

    def test_cover_bmp_accepted(self, tmp_path: Path):
        validate_cover_image(_save(tmp_path / "c.bmp", "BMP"))

    def test_error_message_lists_endpoint_formats(self, tmp_path: Path):
        img = tmp_path / "a.gif"
        Image.new("RGB", (4, 4), "red").save(img, "GIF")
        with pytest.raises(ValueError) as exc_info:
            validate_body_image(img)
        message = str(exc_info.value)
        supported = message.split("Supported:", 1)[1]
        assert ".jpg" in supported
        assert ".png" in supported
        # The body endpoint must NOT advertise gif/bmp support
        assert ".gif" not in supported
        assert ".bmp" not in supported


# ── real byte-level format validation ───────────────────────────

class TestRealFormatValidation:
    def test_text_bytes_disguised_as_png_rejected(self, tmp_path: Path):
        fake = tmp_path / "fake.png"
        fake.write_bytes(b"hello world, definitely not an image payload")
        with pytest.raises(ValueError, match="文件内容与图片格式不符"):
            validate_body_image(fake)

    def test_zero_bytes_rejected(self, tmp_path: Path):
        fake = tmp_path / "empty.png"
        fake.write_bytes(b"\x00" * 64)
        with pytest.raises(ValueError, match="文件内容与图片格式不符"):
            validate_body_image(fake)

    def test_png_bytes_stored_as_jpg_rejected(self, tmp_path: Path):
        mislabeled = tmp_path / "mislabeled.jpg"
        Image.new("RGB", (4, 4), "red").save(mislabeled, "PNG")
        with pytest.raises(ValueError, match="文件内容与图片格式不符"):
            validate_body_image(mislabeled)

    def test_jpeg_bytes_stored_as_png_rejected(self, tmp_path: Path):
        mislabeled = tmp_path / "mislabeled.png"
        Image.new("RGB", (4, 4), "red").save(mislabeled, "JPEG")
        with pytest.raises(ValueError, match="文件内容与图片格式不符"):
            validate_body_image(mislabeled)

    def test_jpeg_extension_canonicalized(self, tmp_path: Path):
        # .jpeg is the same family as .jpg: a real JPEG must pass
        validate_body_image(_save(tmp_path / "a.jpeg", "JPEG"))

    def test_corrupted_truncated_png_rejected(self, tmp_path: Path):
        truncated = tmp_path / "broken.png"
        Image.new("RGB", (4, 4), "red").save(truncated, "PNG")
        data = truncated.read_bytes()
        truncated.write_bytes(data[: len(data) // 2])
        with pytest.raises(ValueError, match="文件内容与图片格式不符"):
            validate_body_image(truncated)

    def test_magic_check_survives_without_pillow(
        self, tmp_path: Path, monkeypatch
    ):
        import sys

        fake = tmp_path / "fake.png"
        fake.write_bytes(b"not an image at all")

        class _NoPIL:
            def __getattr__(self, name):
                raise ImportError("PIL not available")

        monkeypatch.setitem(sys.modules, "PIL", _NoPIL())
        with pytest.raises(ValueError, match="文件内容与图片格式不符"):
            validate_body_image(fake)

    def test_real_still_passes(self, tmp_path: Path):
        validate_body_image(_save(tmp_path / "ok.png", "PNG"))
        validate_cover_image(_save(tmp_path / "ok.gif", "GIF"))
