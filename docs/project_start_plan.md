# WeChat Markdown Publisher Project Start Plan

> **Historical document.** This is the original project plan and predates the
> implementation. Some details (e.g. the tag-based `config/style.yaml` styling
> approach) were superseded: styling is now done with CSS themes in
> `wechat_publish/themes/` plus optional `config/style.css`. See `README.md`
> and `docs/implementation_guide.md` for the current state.

## Goal

Build a local Python CLI project that renders a Markdown article into WeChat Official Account-compatible HTML and creates a draft in the WeChat backend.

The first implementation milestone is intentionally narrow: render, inspect, dry-run, and create a draft. It does not automatically publish, mass-send, delete drafts, delete materials, or manage published articles.

## MVP Scope

The MVP creates a WeChat draft from a Markdown article:

1. Read a Markdown article.
2. Resolve metadata from CLI arguments, Markdown front matter, `config/publish.yaml`, and environment defaults.
3. Render Markdown into a sanitized HTML fragment with inline styles.
4. Upload the cover image through the permanent material API and use the returned `media_id` as `thumb_media_id`.
5. Upload article body images through the `media/uploadimg` API and replace image `src` values with WeChat image URLs.
6. Call `draft/add`.
7. Save a local post record under `.wechat_publish/posts/`.

Default behavior is draft-only. Automatic publish is reserved for a later milestone and must require explicit user intent.

## Non-Goals

- Browser automation against the WeChat backend.
- Simulated human clicks.
- Mass-send to followers.
- Bypassing WeChat review, copyright, originality, or permission checks.
- Deleting drafts, materials, or published articles in the MVP.
- Reusing full Hugo-generated pages as WeChat article content.

## Inputs

Default article input:

```text
input/article.md
```

The CLI must also accept any Markdown path, including existing Hugo posts:

```text
../chengyu.github.io/content/blog/*.md
```

Recommended stable inputs:

```text
config/publish.yaml
config/style.yaml
input/article.md
```

## Outputs

Render outputs:

```text
build/article.preview.html
build/article.wechat.html
```

State outputs:

```text
.wechat_publish/token.json
.wechat_publish/image_cache.json
.wechat_publish/cover_cache.json
.wechat_publish/posts/*.json
```

## Core Flow

```text
Markdown article
  -> metadata resolution
  -> Markdown render
  -> HTML cleanup
  -> inline style application
  -> image discovery and validation
  -> cover upload via material/add_material
  -> body image upload via media/uploadimg
  -> draft/add
  -> local state record
```

## Initial Project Structure

```text
wechat_auto_publish/
├── config/
│   ├── publish.example.yaml
│   └── style.example.yaml
├── docs/
│   ├── implementation_guide.md
│   ├── project_start_plan.md
│   ├── reference_project_notes.md
│   └── wechat_api_reference.md
├── input/
│   └── article.example.md
├── tests/
│   ├── test_config.py
│   ├── test_html_processor.py
│   ├── test_render.py
│   └── test_wechat_api_payloads.py
├── wechat_publish/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── draft.py
│   ├── errors.py
│   ├── html_processor.py
│   ├── images.py
│   ├── render.py
│   ├── state.py
│   └── token.py
├── pyproject.toml
└── wechat_official_account_publish_skill.md
```

## Acceptance Criteria

- `wechat-publish render --md input/article.md` can render local preview and WeChat HTML outputs once implementation is added.
- `wechat-publish draft --md input/article.md --dry-run` can show the planned metadata, cover, image uploads, and draft payload without contacting WeChat.
- Mocked API tests can validate the `draft/add` payload shape without real credentials.
- The project never reads or prints `.env` secrets during validation.
- The default mode creates a draft and never calls publish or mass-send APIs.

