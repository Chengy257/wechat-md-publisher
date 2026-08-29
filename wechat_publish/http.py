"""Shared HTTP helpers: retry with backoff and JSON response parsing."""

from __future__ import annotations

import enum
import time
from collections.abc import Mapping
from typing import Any

import requests

from .errors import AmbiguousRequestError, WeChatAPIError, WeChatErrorDetail

# Transient HTTP status codes worth retrying
_RETRYABLE_STATUS = {500, 502, 503, 504}

_MAX_RETRIES = 2
_BACKOFF_SECONDS = 1.5


class RetryPolicy(enum.Enum):
    """Idempotency classes governing which failures may be retried.

    - SAFE: idempotent requests (token GET, remote image download GET).
      Connection errors, timeouts and 5xx are all retried with backoff.
    - UPLOAD: multipart uploads. Only ConnectTimeout (the connection was
      never established, so the request cannot have been sent) and 5xx
      responses are retried. ReadTimeout / generic ConnectionError raise
      :class:`AmbiguousRequestError` because the server may have already
      processed the upload.
    - NON_IDEMPOTENT: draft/add. Only ConnectTimeout is retried; 5xx,
      ReadTimeout and generic ConnectionError raise
      :class:`AmbiguousRequestError` — a replayed draft/add would create a
      duplicate draft.
    """

    SAFE = "safe"
    UPLOAD = "upload"
    NON_IDEMPOTENT = "non_idempotent"


def request_with_retry(
    method: str,
    url: str,
    *,
    operation: str,
    timeout: int = 60,
    max_retries: int = _MAX_RETRIES,
    policy: RetryPolicy = RetryPolicy.SAFE,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request, retrying transient failures per *policy*.

    SAFE (default) keeps the historical behavior: connection errors, timeouts
    and 5xx responses are retried with a simple linear backoff. The last
    response (or the original exception) is returned/raised to the caller.

    UPLOAD and NON_IDEMPOTENT never blindly replay a request whose outcome is
    uncertain; they raise :class:`AmbiguousRequestError` instead so the user
    can check WeChat before retrying.
    """
    attempts = max_retries + 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.exceptions.ConnectTimeout as e:
            # Connection never established: the request cannot have been
            # sent, so retrying is always safe under every policy.
            last_exc = e
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise
        except requests.exceptions.Timeout as e:
            # ReadTimeout: the server may have processed the request.
            if policy is not RetryPolicy.SAFE:
                raise AmbiguousRequestError(
                    operation, e, draft=policy is RetryPolicy.NON_IDEMPOTENT
                ) from e
            last_exc = e
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise
        except requests.ConnectionError as e:
            # Generic connection failure after the request may have been sent.
            if policy is not RetryPolicy.SAFE:
                raise AmbiguousRequestError(
                    operation, e, draft=policy is RetryPolicy.NON_IDEMPOTENT
                ) from e
            last_exc = e
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise

        if resp.status_code in _RETRYABLE_STATUS:
            if policy is RetryPolicy.NON_IDEMPOTENT:
                # A 5xx means the request was delivered; a replay of
                # draft/add could create a duplicate draft.
                raise AmbiguousRequestError(
                    operation, status_code=resp.status_code, draft=True
                )
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
        return resp
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


def require_field(data: Mapping[str, Any], field: str, operation: str) -> Any:
    """Return ``data[field]``, raising a clean WeChatAPIError when absent.

    WeChat occasionally replies with an error body that lacks the expected
    success field; reading it with ``data[field]`` would surface as a bare
    KeyError. Missing or None values raise a descriptive WeChatAPIError
    instead.
    """
    value = data.get(field)
    if value is None:
        raise WeChatAPIError(
            WeChatErrorDetail(
                operation=operation,
                errcode=None,
                errmsg=f"response missing expected field '{field}'",
                hint=(
                    "微信接口响应缺少必需字段（响应异常或不完整）。"
                    "请检查账号接口权限，并确认返回内容未包含 errcode 错误信息。"
                ),
            )
        )
    return value
