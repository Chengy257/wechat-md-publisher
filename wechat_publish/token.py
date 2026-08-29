"""WeChat access token retrieval and caching."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import check_wechat_response
from .http import json_response, request_with_retry, require_field
from .state import save_json_mapping

API_BASE = "https://api.weixin.qq.com"

# Refresh token this many seconds before actual expiry
_REFRESH_MARGIN = 300  # 5 minutes


@dataclass(frozen=True)
class AccessToken:
    """Cached WeChat access token metadata."""

    value: str
    expires_at: int


def load_cached_token(path: Path, expected_appid: str | None = None) -> AccessToken | None:
    """Load a cached token if present, still usable, and bound to *expected_appid*.

    The cache stores the appid it was issued for; a mismatch (or a missing
    appid, i.e. a legacy pre-v0.1.1 cache) is treated as a cache miss so a
    token is never reused across accounts.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        if expected_appid is not None and data.get("appid") != expected_appid:
            return None
        value = data.get("access_token", "")
        expires_at = data.get("expires_at", 0)
        if not value or not isinstance(expires_at, (int, float)):
            return None
        return AccessToken(value=str(value), expires_at=int(expires_at))
    except (json.JSONDecodeError, OSError):
        return None


def request_access_token(appid: str, appsecret: str) -> AccessToken:
    """Request a fresh access token from WeChat."""
    url = (
        f"{API_BASE}/cgi-bin/token"
        f"?grant_type=client_credential&appid={appid}&secret={appsecret}"
    )
    resp = request_with_retry("GET", url, operation="get_access_token", timeout=30)
    data = json_response(resp, "get_access_token")
    check_wechat_response("get_access_token", data)

    token_value = require_field(data, "access_token", "get_access_token")
    expires_in = data.get("expires_in", 7200)
    expires_at = int(time.time()) + expires_in

    return AccessToken(value=token_value, expires_at=expires_at)


def _save_token(path: Path, token: AccessToken, appid: str) -> None:
    """Persist a token to disk (atomically), bound to its appid."""
    save_json_mapping(
        path,
        {
            "appid": appid,
            "access_token": token.value,
            "expires_at": token.expires_at,
        },
    )


def get_access_token(
    appid: str,
    appsecret: str,
    cache_path: Path,
    force_refresh: bool = False,
) -> AccessToken:
    """Return a cached or freshly requested access token.

    With ``force_refresh=True`` a new token is requested even when the cache
    is still valid (used to recover from 40001/42001 mid-run).
    """
    now = int(time.time())
    if not force_refresh:
        cached = load_cached_token(cache_path, expected_appid=appid)
        if cached and cached.expires_at > now + _REFRESH_MARGIN:
            remaining = cached.expires_at - now
            print(f"[INFO] access_token loaded from cache, expires in {remaining}s")
            return cached

    token = request_access_token(appid, appsecret)
    _save_token(cache_path, token, appid)
    print(f"[INFO] access_token refreshed, expires in {token.expires_at - now}s")
    return token


def mask_token(token_value: str, chars: int = 6) -> str:
    """Return a masked representation of a token for logging."""
    if len(token_value) <= chars * 2:
        return "***"
    return f"{token_value[:chars]}...{token_value[-chars:]}"


def mask_appid(appid: str) -> str:
    """Return a masked appid for display in dry-run/inspect output."""
    if len(appid) <= 6:
        return "***"
    return f"{appid[:4]}****{appid[-2:]}"
