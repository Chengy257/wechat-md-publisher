# WeChat Official Account API Reference Notes

Official documentation entry:

- https://developers.weixin.qq.com/doc/offiaccount/

Relevant official documentation paths:

- https://developers.weixin.qq.com/doc/offiaccount/Basic_Information/Get_access_token.html
- https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html
- https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
- https://developers.weixin.qq.com/doc/offiaccount/Publish/Publish.html

These notes are an implementation reference. Re-check official documentation before changing API behavior because WeChat platform rules can change.

## Access Token

Purpose:
: Obtain an API credential for later calls.

Endpoint:

```text
GET https://api.weixin.qq.com/cgi-bin/token
    ?grant_type=client_credential
    &appid=APPID
    &secret=APPSECRET
```

Expected success fields:

```json
{
  "access_token": "ACCESS_TOKEN",
  "expires_in": 7200
}
```

Implementation notes:

- Cache the token.
- Refresh before expiry, usually 5-10 minutes early.
- If an IP whitelist error appears, update the WeChat backend IP whitelist.
- Never print the full token or secret.

## Permanent Material Upload For Cover Images

Purpose:
: Upload the article cover image and get a permanent material `media_id`.

Endpoint:

```text
POST https://api.weixin.qq.com/cgi-bin/material/add_material
    ?access_token=ACCESS_TOKEN
    &type=image
```

Request:

```text
multipart/form-data
field name: media
```

Expected success fields:

```json
{
  "media_id": "MEDIA_ID",
  "url": "https://..."
}
```

Implementation notes:

- Use `media_id` as `thumb_media_id` in `draft/add`.
- Store cover upload results by content hash to avoid duplicate uploads.
- Permanent image materials may count against account material quota.

## Article Body Image Upload

Purpose:
: Upload images used inside the article body and receive WeChat-hosted image URLs.

Endpoint:

```text
POST https://api.weixin.qq.com/cgi-bin/media/uploadimg
    ?access_token=ACCESS_TOKEN
```

Request:

```text
multipart/form-data
field name: media
```

Expected success fields:

```json
{
  "url": "https://mmbiz.qpic.cn/..."
}
```

Implementation notes:

- The returned URL is used in HTML `img src`.
- Do not use this URL as `thumb_media_id`.
- The body-image API and permanent material API have different purpose, return shape, and constraints.
- Treat common documented constraints as jpg/png and under 1 MB unless official documentation is rechecked and updated.

## Add Draft

Purpose:
: Create a draft article in the WeChat backend.

Endpoint:

```text
POST https://api.weixin.qq.com/cgi-bin/draft/add
    ?access_token=ACCESS_TOKEN
```

Request body:

```json
{
  "articles": [
    {
      "title": "Article title",
      "author": "Cy257",
      "digest": "Short summary",
      "content": "<section>...</section>",
      "content_source_url": "https://example.com/original",
      "thumb_media_id": "COVER_MEDIA_ID",
      "need_open_comment": 1,
      "only_fans_can_comment": 0
    }
  ]
}
```

Expected success fields:

```json
{
  "media_id": "DRAFT_MEDIA_ID"
}
```

Implementation notes:

- `content` is an HTML string, not an uploaded `.html` file.
- `thumb_media_id` must come from permanent material upload.
- The MVP stops here by default.

## Publish Draft

Purpose:
: Submit an existing draft for publication.

Endpoint:

```text
POST https://api.weixin.qq.com/cgi-bin/freepublish/submit
    ?access_token=ACCESS_TOKEN
```

Request body:

```json
{
  "media_id": "DRAFT_MEDIA_ID"
}
```

Expected success fields:

```json
{
  "publish_id": "PUBLISH_ID"
}
```

Implementation notes:

- Not part of MVP execution.
- Only call after explicit user request or a future explicit `publish` command.
- Query status after submit; do not assume immediate success.

## Common Error Categories

`invalid credential` or token expired:
: Refresh token and check AppID/AppSecret.

`invalid ip`:
: Check WeChat backend IP whitelist.

`invalid media_id`:
: Verify the cover was uploaded through `material/add_material`.

`image upload failed`:
: Check file path, type, size, and whether the correct API was used.

`content is empty`:
: Check Markdown render and HTML cleanup did not remove all content.

`draft add failed`:
: Print safe JSON response with `errcode` and `errmsg`, without secrets.

