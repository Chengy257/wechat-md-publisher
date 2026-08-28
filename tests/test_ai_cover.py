"""AI cover image generation tests with mocked HTTP."""

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from wechat_publish.ai_cover import (
    _build_prompt,
    generate_cover_image,
    resolve_cover_ai_config,
)

_FAKE_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50).decode()


class TestBuildPrompt:
    def test_custom_prompt(self):
        result = _build_prompt("Title", "my custom prompt")
        assert result == "my custom prompt"

    def test_auto_prompt_contains_title(self):
        result = _build_prompt("AI的未来")
        assert "AI的未来" in result

    def test_auto_prompt_mentions_ratio(self):
        result = _build_prompt("Test")
        assert "2.35:1" in result


class TestGenerateCoverImage:
    @patch("wechat_publish.ai_cover.requests.post")
    def test_saves_image(self, mock_post, tmp_path: Path):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"inlineData": {"data": _FAKE_PNG, "mimeType": "image/png"}}
                        ]
                    }
                }
            ]
        }
        out = tmp_path / "cover.png"
        result = generate_cover_image("Test", out, "https://api.example.com", "key")
        assert result == out
        assert out.exists()
        assert out.read_bytes().startswith(b"\x89PNG")

    @patch("wechat_publish.ai_cover.requests.post")
    def test_uses_custom_model(self, mock_post, tmp_path: Path):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"inlineData": {"data": _FAKE_PNG, "mimeType": "image/png"}}]}}
            ]
        }
        out = tmp_path / "cover.png"
        generate_cover_image("T", out, "https://api.example.com", "k", model="custom-model")
        call_url = mock_post.call_args[0][0]
        assert "custom-model" in call_url

    @patch("wechat_publish.ai_cover.requests.post")
    def test_raises_on_no_candidates(self, mock_post, tmp_path: Path):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"candidates": []}
        out = tmp_path / "cover.png"
        with pytest.raises(ValueError, match="no candidates"):
            generate_cover_image("T", out, "https://api.example.com", "k")

    @patch("wechat_publish.ai_cover.requests.post")
    def test_raises_on_no_image(self, mock_post, tmp_path: Path):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "description"}]}}]
        }
        out = tmp_path / "cover.png"
        with pytest.raises(ValueError, match="no image"):
            generate_cover_image("T", out, "https://api.example.com", "k")

    @patch("wechat_publish.ai_cover.requests.post")
    def test_network_error_raises(self, mock_post, tmp_path: Path):
        mock_post.side_effect = Exception("network error")
        out = tmp_path / "cover.png"
        with pytest.raises(Exception, match="network error"):
            generate_cover_image("T", out, "https://api.example.com", "k")


class TestResolveCoverAiConfig:
    def test_defaults(self):
        url, key, model, prompt = resolve_cover_ai_config({}, {})
        assert url == "https://generativelanguage.googleapis.com"
        assert key == ""
        assert model == "gemini-2.0-flash-exp"
        assert prompt == ""

    def test_from_config(self):
        cfg = {
            "ai": {
                "cover_api_url": "https://proxy.example.com",
                "cover_model": "gemini-pro",
                "cover_api_key_env": "MY_GEMINI_KEY",
                "cover_prompt": "A blue cover",
            }
        }
        env = {"MY_GEMINI_KEY": "key-123"}
        url, key, model, prompt = resolve_cover_ai_config(cfg, env)
        assert url == "https://proxy.example.com"
        assert key == "key-123"
        assert model == "gemini-pro"
        assert prompt == "A blue cover"

    def test_missing_key(self):
        cfg = {"ai": {"cover_api_key_env": "MISSING"}}
        _, key, _, _ = resolve_cover_ai_config(cfg, {})
        assert key == ""
