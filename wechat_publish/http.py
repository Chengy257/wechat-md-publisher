"""Shared HTTP helpers: retry with backoff and JSON response parsing."""

from __future__ import annotations

import time
from typing import Any, Mapping

import requests

from .errors import WeChatAPIError, WeChatErrorDetail

# Transient HTTP status codes worth retrying
_RETRYABLE_STATUS = {500, 502, 503, 504}

_MAX_RETRIES = 2
_BACKOFF_SECONDS = 1.5


def request_with_retry(
    method: str,
    url: str,
    *,
    operation: str,
    timeout: int = 60,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request, retrying transient failures.

    Retries on connection errors, timeouts and 5xx responses with a simple
    linear backoff. The last response (or the original exception) is
    returned/raised to the caller.
    """
    attempts = max_retries + 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise
    raise last_exc  # pragma: no cover - unreachable


def json_response(resp: requests.Response, operation: str) -> Mapping[str, Any]:
    """Parse a WeChat API JSON response, wrapping decode failures cleanly."""
    try:
        data = resp.json()
    except ValueError as e:
        raise WeChatAPIError(
            WeChatErrorDetail(
                operation=operation,
                errcode=None,
                errmsg=f"non-JSON response (HTTP {resp.status_code}): {e}",
                hint="微信接口返回了非 JSON 内容（常见于网关错误或 IP 被拦截），请稍后重试。",
            )
        ) from e
    if not isinstance(data, dict):
        raise WeChatAPIError(
            WeChatErrorDetail(
                operation=operation,
                errcode=None,
                errmsg=f"unexpected response type (HTTP {resp.status_code})",
                hint="微信接口返回了意外的数据结构，请检查 API 地址配置。",
            )
        )
    return data
