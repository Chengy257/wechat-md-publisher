"""AI summary generation tests with mocked HTTP."""

import json
from unittest.mock import patch

import pytest

from wechat_publish.ai_summary import generate_digest, resolve_ai_config


class TestGenerateDigest:
    @patch("wechat_publish.ai_summary.requests.post")
    def test_returns_summary(self, mock_post):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "这是一篇关于Python的文章"}}]
        }
        result = generate_digest("# Python", "https://api.example.com/v1", "key123")
        assert result == "这是一篇关于Python的文章"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "Authorization" in call_args.kwargs.get("headers", call_args[1].get("headers", {}))

    @patch("wechat_publish.ai_summary.requests.post")
    def test_truncates_long_summary(self, mock_post):
        long_text = "A" * 200
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": long_text}}]
        }
        result = generate_digest("# Test", "https://api.example.com/v1", "key")
        assert len(result) == 100

    @patch("wechat_publish.ai_summary.requests.post")
    def test_returns_empty_on_network_error(self, mock_post):
        mock_post.side_effect = Exception("connection failed")
        result = generate_digest("# Test", "https://api.example.com/v1", "key")
        assert result == ""

    @patch("wechat_publish.ai_summary.requests.post")
    def test_returns_empty_on_empty_response(self, mock_post):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        result = generate_digest("# Test", "https://api.example.com/v1", "key")
        assert result == ""

    @patch("wechat_publish.ai_summary.requests.post")
    def test_uses_default_model_when_empty(self, mock_post):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "summary"}}]
        }
        generate_digest("# Test", "https://api.example.com/v1", "key", model="")
        call_body = mock_post.call_args.kwargs.get("json", mock_post.call_args[1].get("json", {}))
        assert call_body["model"] == "deepseek-chat"

    @patch("wechat_publish.ai_summary.requests.post")
    def test_uses_custom_model(self, mock_post):
        mock_resp = mock_post.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "summary"}}]
        }
        generate_digest("# Test", "https://api.example.com/v1", "key", model="gpt-4o")
        call_body = mock_post.call_args.kwargs.get("json", mock_post.call_args[1].get("json", {}))
        assert call_body["model"] == "gpt-4o"


class TestResolveAiConfig:
    def test_defaults(self):
        url, key, model = resolve_ai_config({}, {})
        assert url == "https://api.deepseek.com/v1"
        assert key == ""
        assert model == "deepseek-chat"

    def test_from_config(self):
        cfg = {
            "ai": {
                "summary_api_url": "https://custom.api.com/v1",
                "summary_model": "custom-model",
                "summary_api_key_env": "MY_KEY",
            }
        }
        env = {"MY_KEY": "sk-123"}
        url, key, model = resolve_ai_config(cfg, env)
        assert url == "https://custom.api.com/v1"
        assert key == "sk-123"
        assert model == "custom-model"

    def test_missing_env_key(self):
        cfg = {"ai": {"summary_api_key_env": "MY_KEY"}}
        url, key, model = resolve_ai_config(cfg, {})
        assert key == ""
