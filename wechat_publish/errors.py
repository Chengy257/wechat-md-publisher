"""Error types and WeChat response normalization."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WeChatErrorDetail:
    """Safe error details from a WeChat API response."""

    operation: str
    errcode: int | None
    errmsg: str | None
    hint: str


class WeChatAPIError(RuntimeError):
    """Raised when a WeChat API response contains an error code."""

    def __init__(self, detail: WeChatErrorDetail) -> None:
        self.detail = detail
        super().__init__(
            f"[{detail.operation}] errcode={detail.errcode} "
            f"errmsg={detail.errmsg}\n  hint: {detail.hint}"
        )


class AmbiguousRequestError(RuntimeError):
    """Raised when a request may or may not have been processed by WeChat.

    Non-idempotent requests (draft/add, multipart uploads) are never blindly
    replayed after an uncertain failure (read timeout / connection error /
    5xx): the caller must check WeChat first, because the server may have
    already created the draft or stored the material.
    """

    def __init__(
        self,
        operation: str,
        cause: Exception | None = None,
        *,
        status_code: int | None = None,
        draft: bool = False,
    ) -> None:
        if cause is not None:
            detail = f"{type(cause).__name__}: {cause}"
        else:
            detail = f"HTTP {status_code} response"
        check = (
            "Check the WeChat draft list before retrying"
            if draft
            else "Check the WeChat material library / draft content before retrying"
        )
        self.operation = operation
        self.cause = cause
        super().__init__(
            f"[{operation}] The request outcome is uncertain: the request may "
            f"have already reached WeChat when the failure occurred ({detail}). "
            f"{check}. "
            f"hint: WeChat may have already processed this request (a draft or "
            f"material may already exist); do not blindly rerun the publish."
        )


class RemoteDraftCreatedLocalStateFailed(RuntimeError):
    """The draft exists remotely but persisting local state afterwards failed.

    Carries the remote media_id so the user can reconcile manually instead of
    blindly rerunning (which would create a duplicate draft).
    """

    def __init__(self, media_id: str, reason: str) -> None:
        self.media_id = media_id
        super().__init__(
            f"Draft was created remotely (media_id: {media_id}). "
            f"Do NOT blindly rerun — check the WeChat draft list first: {reason}"
        )


def check_wechat_response(operation: str, response: Mapping[str, Any]) -> None:
    """Raise `WeChatAPIError` when a WeChat response indicates failure."""
    errcode = response.get("errcode")
    if errcode is None or errcode == 0:
        return
    errmsg = response.get("errmsg", "")
    hint = hint_for_error(operation, errcode, errmsg)
    raise WeChatAPIError(
        WeChatErrorDetail(
            operation=operation,
            errcode=errcode,
            errmsg=errmsg,
            hint=hint,
        )
    )


_ERRCODE_HINTS: dict[int, str] = {
    -1: "微信系统繁忙，请稍后重试。",
    40001: "access_token 无效或已过期，请检查 AppID/AppSecret 并重新获取 token。",
    40002: "grant_type 不合法，请确认使用 client_credential。",
    40003: "AppID 不合法，请检查 WECHAT_APPID 环境变量。",
    40004: "媒体类型不合法。",
    40007: "invalid media_id：请确认 thumb_media_id 来自永久素材上传（material/add_material），而非正文图片接口（media/uploadimg）。",
    40009: "图片尺寸或格式不合法：封面（material/add_material）支持 10MB 内图片，正文图（media/uploadimg）需小于 1MB。",
    41001: "缺少 access_token 参数。",
    42001: "access_token 已过期，请刷新 token 缓存。",
    43002: "需要 POST 请求。",
    45009: "接口调用频率超限，请稍后重试。",
    45064: "创建草稿频率超限。",
    48001: "API 无权限，请确认公众号已认证且接口权限已开通。",
    61004: "access_token 过期或 IP 不在白名单，请检查公众号后台 IP 白名单设置。",
    87009: "IP 不在白名单中，请在公众号后台 → 设置与开发 → 基本配置中添加本机 IP。",
}

_OPERATION_HINTS: dict[str, str] = {
    "get_access_token": "检查 AppID、AppSecret 是否正确，IP 是否在白名单。",
    "upload_cover_image": "检查图片路径、格式（jpg/png）和大小（封面 ≤10MB）。",
    "upload_body_image": "检查图片路径、格式（jpg/png）和大小（<1MB）。",
    "add_draft": "检查 title、content、thumb_media_id 是否有效。",
}


def hint_for_error(operation: str, errcode: int | None, errmsg: str | None) -> str:
    """Return a safe troubleshooting hint for a WeChat API error."""
    parts: list[str] = []

    if errcode and errcode in _ERRCODE_HINTS:
        parts.append(_ERRCODE_HINTS[errcode])

    if operation in _OPERATION_HINTS:
        parts.append(_OPERATION_HINTS[operation])

    errmsg_lower = (errmsg or "").lower()
    if re.search(r"\bip\b", errmsg_lower):
        parts.append("请在公众号后台 → 设置与开发 → 基本配置中添加本机 IP 到白名单。")

    if "access_token" in errmsg_lower and errcode not in (40001, 42001):
        parts.append("尝试删除对应账号的 .wechat_publish/accounts/<account-key>/token.json 后重试。")

    if not parts:
        parts.append(f"未知错误，请查阅微信官方文档（errcode={errcode}）。")

    return " ".join(parts)
