"""WeChat draft payload construction and API interaction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import check_wechat_response
from .http import RetryPolicy, json_response, request_with_retry, require_field

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


def validate_publish_preflight(
    *,
    title: str,
    author: str,
    digest: str,
    need_open_comment: int,
    only_fans_can_comment: int,
    content_source_url: str,
    html_chars: int,
    cover_path: Path | None,
) -> None:
    """Validate every locally-checkable draft field before any network request.

    Runs early in the draft flow (after rendering, before token/upload) so
    misconfiguration fails fast instead of wasting uploads. The final
    :func:`validate_draft_article` still runs as defense in depth.
    """
    if not title:
        raise ValueError(
            "Article title must not be empty. Use --title, --autofill-front-matter, "
            "or add front matter."
        )
    if len(title) > _MAX_TITLE_CHARS:
        raise ValueError(
            f"Article title too long: {len(title)} chars (max {_MAX_TITLE_CHARS}): "
            f"reduce the --title or front matter title."
        )
    if len(author) > _MAX_AUTHOR_CHARS:
        raise ValueError(
            f"Author name too long: {len(author)} chars (max {_MAX_AUTHOR_CHARS}). "
            f"Shorten the --author or front matter author."
        )
    if len(digest) > _MAX_DIGEST_CHARS:
        raise ValueError(
            f"Digest too long: {len(digest)} chars (max {_MAX_DIGEST_CHARS}). "
            f"Shorten the --digest or front matter digest/summary."
        )
    for name, flag in (
        ("need_open_comment", need_open_comment),
        ("only_fans_can_comment", only_fans_can_comment),
    ):
        if flag not in (0, 1):
            raise ValueError(
                f"{name} must be 0 or 1 (got {flag!r}); fix the front matter value."
            )
    if content_source_url and not content_source_url.startswith(("http://", "https://")):
        raise ValueError(
            f"content_source_url must start with http:// or https:// "
            f"(got: {content_source_url}). Fix the source_url field."
        )
    if html_chars >= _MAX_CONTENT_CHARS:
        raise ValueError(
            f"Article content too long: {html_chars} chars "
            f"(official limit {_MAX_CONTENT_CHARS}). "
            f"Shorten the article or simplify inline styles."
        )
    if cover_path is not None and not (cover_path.exists() and cover_path.is_file()):
        raise ValueError(
            f"Cover image not found: {cover_path}. Use --cover or --ai-cover."
        )


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
        policy=RetryPolicy.NON_IDEMPOTENT,
    )
    data = json_response(resp, "add_draft")
    check_wechat_response("add_draft", data)

    media_id = require_field(data, "media_id", "add_draft")
    print(f"[INFO] draft created: media_id={media_id[:6]}...")

    return DraftResult(media_id=media_id, raw_response=data)
