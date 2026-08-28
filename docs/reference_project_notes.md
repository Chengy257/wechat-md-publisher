# Reference Project Notes

Reference project:

```text
../chengyu.github.io
```

The reference project is a Hugo + Hextra blog. It is useful for content and metadata conventions, but its generated pages should not be submitted directly to WeChat.

## Useful Patterns

Blog posts under `content/blog/*.md` commonly use front matter fields:

```yaml
title: "Article title"
summary: "Article summary"
date: 2024-08-20
draft: false
tags: ["Python", "CLI"]
categories: ["工具开发"]
author: "Cy257"
showToc: true
TocOpen: false
```

Reusable fields for this publisher:

```text
title
summary
date
tags
categories
author
```

`summary` can map to WeChat `digest`.

## Content Patterns To Support

Existing blog content includes:

- Chinese headings.
- Long technical paragraphs.
- Tables.
- Blockquotes.
- Bash, Python, R, and Snakemake code fences.
- External image URLs from a CDN.
- `<!--more-->` markers.

## What Not To Reuse Directly

Do not use the full Hugo-generated HTML under `public/` as WeChat content.

Reasons:

- It includes navigation, sidebars, footer, search UI, theme scripts, and full document shell.
- It depends on external CSS and generated class names.
- WeChat article content is better represented as a sanitized HTML fragment with inline styles.

## Recommended Reuse Strategy

Use Hugo Markdown files as source documents. Render them through this project's own Markdown-to-WeChat pipeline. Keep the rendered WeChat HTML independent from Hugo theme output.

