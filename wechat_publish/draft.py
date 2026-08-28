"""WeChat draft payload construction and API interaction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .errors import check_wechat_response

API_BASE = "https://api.weixin.qq.com"


@dataclass(frozen=True)
class DraftArticle:
    """Article fields accepted by the WeChat draft/add endpoint."""

    title: str
    author: str
    digest: str
    content: str
    thumb_media_id: str
    content_source_url: str = ""
    need_open_comment: int = 1
    only_fans_can_comment: int = 0


@dataclass(frozen=True)
class DraftResult:
    """Draft creation result."""

    media_id: str
    raw_response: dict[str, Any]


def build_draft_payload(article: DraftArticle) -> dict[str, Any]:
    """Build the JSON payload for draft/add."""
    return {
        "articles": [
            {
                "title": article.title,
                "author": article.author,
                "digest": article.digest,
                "content": article.content,
                "content_source_url": article.content_source_url,
                "thumb_media_id": article.thumb_media_id,
                "need_open_comment": article.need_open_comment,
                "only_fans_can_comment": article.only_fans_can_comment,
            }
        ]
    }


def add_draft(access_token: str, article: DraftArticle) -> DraftResult:
    """Create a WeChat draft through draft/add."""
    url = f"{API_BASE}/cgi-bin/draft/add?access_token={access_token}"
    payload = build_draft_payload(article)

    resp = requests.post(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    data = resp.json()
    check_wechat_response("add_draft", data)

    media_id = data["media_id"]
    print(f"[INFO] draft created: media_id={media_id[:6]}...")

    return DraftResult(media_id=media_id, raw_response=data)
