# WeChat Markdown Publisher Implementation Guide

## Configuration

Configuration precedence is fixed:

```text
CLI arguments > Markdown front matter > config/publish.yaml > environment defaults
```

The stable configuration file should hold account-independent publishing defaults, not secrets. App credentials stay in environment variables or `.env`.

Environment values are read from a project-root `.env` file **and** the real
process environment; real environment variables take precedence. Every
variable is exposed, so AI keys (`AI_API_KEY`, `GEMINI_API_KEY`) and custom
`*_env` names configured in `publish.yaml` resolve from either source.

## Module Responsibilities

`wechat_publish.config`
: Load YAML configuration, Markdown front matter, environment defaults, and merge them using the fixed precedence.

`wechat_publish.render`
: Convert Markdown to a clean HTML fragment and produce preview/wechat outputs.

`wechat_publish.html_processor`
: Remove unsupported elements, apply inline styles, discover image references, and keep only content suitable for WeChat article bodies.

`wechat_publish.images`
: Resolve local and remote image references, validate limits, compute hashes, upload images, and use caches to avoid duplicate uploads.

`wechat_publish.token`
: Get and cache `access_token`, refreshing before expiry.

`wechat_publish.draft`
: Build and submit the `draft/add` payload. Later milestones may add publish calls, but draft remains the default.

`wechat_publish.state`
: Persist token cache, image cache, cover cache, and per-post state records.

`wechat_publish.errors`
: Normalize WeChat error responses and provide actionable hints.

`wechat_publish.http`
: Shared HTTP layer: transient-failure retry (connection errors, timeouts, 5xx) and clean wrapping of non-JSON responses.

`wechat_publish.ai_summary`
: Optional AI digest generation via OpenAI-compatible chat APIs.

`wechat_publish.ai_cover`
: Optional AI cover generation via the Gemini image API.

`wechat_publish.mermaid`
: Render mermaid code blocks to PNG (local mmdc CLI or mermaid.ink API) with content-hash caching.

## HTML Rules

Submit an HTML fragment, not a complete webpage. Do not submit Hugo navigation, sidebars, search widgets, scripts, external stylesheets, or footer markup.

Remove:

```text
script
iframe
style
link
form
input
button
```

Prefer inline styles for:

```text
h1/h2/h3
p
blockquote
ul/ol/li
table/th/td
pre/code
hr
img
figure/figcaption
```

## Image Flow

Image handling is deterministic:

```text
parse Markdown and img[src]
  -> resolve local path or download remote URL
  -> validate type and size
  -> compute sha256
  -> check cache
  -> upload
  -> replace src
```

Cover images and body images use different WeChat APIs:

- Cover: `material/add_material`, returns `media_id`, used as `thumb_media_id`.
- Body image: `media/uploadimg`, returns URL, used as article image `src`.

Never use a body-image URL as `thumb_media_id`.

## State

Write local state under `.wechat_publish/`:

```json
{
  "title": "Article title",
  "source_markdown": "input/article.md",
  "wechat_html": "build/article.wechat.html",
  "created_at": "2026-05-28T10:00:00+08:00",
  "draft_media_id": "MEDIA_ID"
}
```

`draft_media_id` is only present when a draft was actually created.

## Error Handling

Every WeChat response must be checked for `errcode`.

Errors should include:

- operation name
- endpoint category
- `errcode`
- `errmsg`
- safe hint

Do not log complete `access_token`, `AppSecret`, or raw `.env` values. Token logs may show only a short masked prefix/suffix.

## CLI Commands

Implemented with standard-library `argparse` (entry point `wechat-publish = wechat_publish.cli:main`):

```bash
wechat-publish render  --md input/article.md [--out ...] [--preview-out ...] [--style ...|--theme ...]
wechat-publish inspect --md input/article.md [--style ...|--theme ...]
wechat-publish draft   --md input/article.md [--dry-run] [--title ...] [--author ...]
                       [--digest ...] [--cover ...] [--config ...] [--style ...|--theme ...]
                       [--ai-summary] [--ai-cover] [--mermaid] [--mermaid-engine mmdc|api]
                       [--autofill-front-matter] [--compress-cover] [--allow-missing-images]
```

`--dry-run` performs all local rendering and validation but never touches the
network (AI calls are skipped too). Missing covers, failed image uploads, and
fields exceeding the official `draft/add` limits abort the run with a clear
error unless explicitly waived.

## Testing Strategy

Unit tests:

- configuration precedence
- front matter parsing
- Markdown element rendering
- HTML cleanup
- image path resolution
- WeChat payload construction
- WeChat error normalization

Integration tests:

- mock `token`
- mock cover upload
- mock body image upload
- mock `draft/add`
- verify no real network calls in dry-run

Manual tests:

- render `input/article.example.md`
- render a sample from `../chengyu.github.io/content/blog/`
- inspect output HTML in a browser
- create a real draft only after credentials and IP whitelist are confirmed

