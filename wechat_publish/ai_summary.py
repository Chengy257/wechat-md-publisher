"""AI-powered article summary generation via OpenAI-compatible API."""

from __future__ import annotations

import requests

_DEFAULT_API_URL = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-chat"
_MAX_DIGEST_CHARS = 100


def generate_digest(
    markdown_text: str,
    api_url: str,
    api_key: str,
    model: str = "",
) -> str:
    """Generate a Chinese article summary (<=100 chars) via OpenAI-compatible API.

    Returns the summary string, or empty string on any failure.
    """
    url = f"{api_url.rstrip('/')}/chat/completions"
    model = model or _DEFAULT_MODEL

    prompt = (
        "请根据以下微信公众号 Markdown 内容生成一个中文摘要，"
        "不超过100个汉字，不要换行，只输出摘要内容：\n\n"
        f"{markdown_text}"
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.3,
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not content:
            return ""
        # Truncate to 100 characters
        return content[:_MAX_DIGEST_CHARS]
    except Exception as e:
        print(f"[WARN] AI summary generation failed: {e}")
        return ""


def resolve_ai_config(
    publish_config: dict, env: dict[str, str | None]
) -> tuple[str, str, str]:
    """Resolve AI summary config from publish.yaml and environment.

    Returns (api_url, api_key, model). api_key may be empty if not configured.
    """
    ai_cfg = publish_config.get("ai", {})

    api_url = ai_cfg.get("summary_api_url", "") or _DEFAULT_API_URL
    model = ai_cfg.get("summary_model", "") or _DEFAULT_MODEL

    key_env_name = ai_cfg.get("summary_api_key_env", "AI_API_KEY")
    api_key = env.get(key_env_name, "") or ""

    return api_url, api_key, model
