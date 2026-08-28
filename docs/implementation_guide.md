# WeChat Markdown Publisher Implementation Guide

## Configuration

Configuration precedence is fixed:

```text
CLI arguments > Markdown front matter > config/publish.yaml > environment defaults
```

The stable configuration file should hold account-independent publishing defaults, not secrets. App credentials stay in environment variables or `.env`.

Supported environment variable names should include:

```text
WECHAT_APPID
WECHAT_APPSECRET
WECHAT_DEFAULT_AUTHOR
```

For compatibility with the current local `.env`, also accept:

```text
WECHAT_APP_ID
WECHAT_APP_SECRET
WECHAT_AUTHOR
```

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
  "cover": "assets/cover.png",
  "draft_media_id": "MEDIA_ID",
  "mode": "draft",
  "created_at": "2026-05-28T10:00:00+08:00",
  "images": {
    "images/fig1.png": "https://mmbiz.qpic.cn/..."
  }
}
```

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

Planned command shape:

```bash
wechat-publish render --md input/article.md --out build/article.wechat.html
wechat-publish inspect --md input/article.md
wechat-publish draft --md input/article.md --dry-run
wechat-publish draft --md input/article.md
```

The CLI implementation can use either `typer` or standard-library `argparse`. That choice should be confirmed before full coding starts.

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

