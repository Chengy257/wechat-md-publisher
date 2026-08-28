---
title: "微信公众号 Markdown 自动发布项目样例"
summary: "用于验证 Markdown 渲染、图片处理和草稿 payload 的最小样例。"
date: 2026-05-28
tags: ["Markdown", "微信公众号", "自动化"]
categories: ["工具开发"]
author: "Cy257"
---

# 微信公众号 Markdown 自动发布项目样例

这是一个可替换的 Markdown 正文样例。首版工具会把正文渲染成公众号兼容 HTML，并默认创建草稿。

<!--more-->

## 核心能力

- 读取 Markdown 和 front matter。
- 应用独立的排版参数文件。
- 上传封面图和正文图片。
- 创建微信公众号草稿。

## 表格示例

| 阶段 | 输出 |
|------|------|
| render | `build/article.wechat.html` |
| draft | `.wechat_publish/posts/*.json` |

## 代码块示例

```python
print("hello wechat draft")
```

> 默认只创建草稿，不自动发布。

