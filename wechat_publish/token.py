"""WeChat access token retrieval and caching."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .errors import check_wechat_response

API_BASE = "https://api.weixin.qq.com"

# Refresh token this many seconds before actual expiry
_REFRESH_MARGIN = 300  # 5 minutes


@dataclass(frozen=True)
class AccessToken:
    """Cached WeChat access token metadata."""

    value: str
    expires_at: int


def load_cached_token(path: Path) -> AccessToken | None:
    """Load a cached token if present and still usable."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        value = data.get("access_token", "")
        expires_at = data.get("expires_at", 0)
        if not value or not isinstance(expires_at, (int, float)):
            return None
        return AccessToken(value=str(value), expires_at=int(expires_at))
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def request_access_token(appid: str, appsecret: str) -> AccessToken:
    """Request a fresh access token from WeChat."""
    url = (
        f"{API_BASE}/cgi-bin/token"
        f"?grant_type=client_credential&appid={appid}&secret={appsecret}"
    )
    resp = requests.get(url, timeout=30)
    data = resp.json()
    check_wechat_response("get_access_token", data)

    token_value = data["access_token"]
    expires_in = data.get("expires_in", 7200)
    expires_at = int(time.time()) + expires_in

    return AccessToken(value=token_value, expires_at=expires_at)


def _save_token(path: Path, token: AccessToken) -> None:
    """Persist a token to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token.value,
        "expires_at": token.expires_at,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_access_token(appid: str, appsecret: str, cache_path: Path) -> AccessToken:
    """Return a cached or freshly requested access token."""
    cached = load_cached_token(cache_path)
    now = int(time.time())

    if cached and cached.expires_at > now + _REFRESH_MARGIN:
        remaining = cached.expires_at - now
        print(f"[INFO] access_token loaded from cache, expires in {remaining}s")
        return cached

    token = request_access_token(appid, appsecret)
    _save_token(cache_path, token)
    print(f"[INFO] access_token refreshed, expires in {token.expires_at - now}s")
    return token


def mask_token(token_value: str, chars: int = 6) -> str:
    """Return a masked representation of a token for logging."""
    if len(token_value) <= chars * 2:
        return "***"
    return f"{token_value[:chars]}...{token_value[-chars:]}"
