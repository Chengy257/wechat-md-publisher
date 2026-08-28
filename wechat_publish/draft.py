"""WeChat draft payload construction and API interaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import check_wechat_response
from .http import json_response, request_with_retry

API_BASE = "https://api.weixin.qq.com"

# Official draft/add limits
_MAX_TITLE_CHARS = 64
_MAX_AUTHOR_CHARS = 8
_MAX_DIGEST_CHARS = 120
_MAX_CONTENT_CHARS = 20_000


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


def validate_draft_article(article: DraftArticle) -> None:
    """Validate article fields against the official draft/add limits."""
    if not article.title:
        raise ValueError("Draft title must not be empty.")
    if len(article.title) > _MAX_TITLE_CHARS:
        raise ValueError(
            f"Draft title too long: {len(article.title)} chars (max {_MAX_TITLE_CHARS}): "
            f"reduce the --title or front matter title."
        )
    if len(article.author) > _MAX_AUTHOR_CHARS:
        raise ValueError(
            f"Author name too long: {len(article.author)} chars (max {_MAX_AUTHOR_CHARS})."
        )
    if len(article.digest) > _MAX_DIGEST_CHARS:
        raise ValueError(
            f"Digest too long: {len(article.digest)} chars (max {_MAX_DIGEST_CHARS})."
        )
    if len(article.content) >= _MAX_CONTENT_CHARS:
        raise ValueError(
            f"Article content too long: {len(article.content)} chars "
            f"(official limit {_MAX_CONTENT_CHARS}). "
            f"Shorten the article or simplify inline styles."
        )
    if not article.thumb_media_id:
        raise ValueError("thumb_media_id is required (upload a cover first).")


def build_draft_payload(article: DraftArticle) -> dict[str, Any]:
    """Build the JSON payload for draft/add."""
    validate_draft_article(article)
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

    resp = request_with_retry(
        "POST",
        url,
        operation="add_draft",
        json=payload,
        timeout=60,
    )
    data = json_response(resp, "add_draft")
    check_wechat_response("add_draft", data)

    media_id = data["media_id"]
    print(f"[INFO] draft created: media_id={media_id[:6]}...")

    return DraftResult(media_id=media_id, raw_response=data)
