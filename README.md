# wechat-md-publisher

把 Markdown 文章渲染为微信公众号兼容 HTML，并一键创建公众号草稿（WeChat Official Account draft API）。

## 功能

- **Markdown → 微信正文**：sanitize → 微信兼容化（代码块/列表/表格/标题改造）→ 外链转脚注 → CSS 内联（premailer），全流程无条件执行，杜绝未消毒输出
- **内置主题**：`default / elegant / lapis / simple / tech`（随包分发，pip 安装即用），或用 `--style` 指定自定义 CSS
- **图片处理**：正文图自动上传（`media/uploadimg`）并替换 src；封面走永久素材（`material/add_material`，≤10MB）；sha256 缓存避免重复上传；`--compress-cover` 可选 Pillow 压缩
- **AI 增强**（可选）：`--ai-summary` 生成摘要（OpenAI 兼容接口，默认 DeepSeek）、`--ai-cover` 生成封面（Gemini）
- **Mermaid 图表**：`--mermaid` 本地渲染（mmdc，Windows 下自动兼容 `.cmd`）或在线 API（`--mermaid-engine api`），按内容 hash 缓存
- **健壮性**：网络错误/5xx 自动重试、45009 限频退避、token 过期（42001）自动刷新重试一次、状态文件原子写、图片上传失败默认中止发布
- **安全**：本地图片仅允许 markdown 目录 / 项目目录 / build 目录内的路径；`.env` 不入库；token 与 appid 输出均掩码

## 安装

```bash
pip install -e .            # 基础安装
pip install -e ".[dev]"     # 含 pytest / responses
pip install -e ".[cover-compress]"  # 含 Pillow（--compress-cover 需要）
```

## 配置

1. 复制 `config/publish.example.yaml` 为 `config/publish.yaml`，按需调整（代码会带警告忽略未知配置键）
2. 在项目根创建 `.env`（已被 .gitignore 忽略）：

```dotenv
WECHAT_APP_ID=你的AppID
WECHAT_APP_SECRET=你的AppSecret
WECHAT_AUTHOR=默认作者

# 可选：AI 功能
AI_API_KEY=DeepSeek等OpenAI兼容接口的key
GEMINI_API_KEY=Gemini的key
```

`.env` 与真实环境变量都会读取，真实环境变量优先。公众号后台需把本机出口 IP 加入 IP 白名单，否则报 87009。

## 使用

```bash
# 预览渲染结果（不联网）
wechat-publish render --md input/article.md --theme default

# 查看解析出的元数据与图片清单
wechat-publish inspect --md input/article.md

# 试运行：完整流程但不发任何网络请求（AI 调用也会跳过）
wechat-publish draft --md input/article.md --dry-run

# 创建草稿
wechat-publish draft --md input/article.md

# 无 front matter 的文章：自动推导标题（首个 # 标题）/日期/作者并写回文件
wechat-publish draft --md input/article.md --autofill-front-matter

# 大封面自动压缩 + 图片上传失败不中止
wechat-publish draft --md input/article.md --compress-cover --allow-missing-images
```

front matter 支持字段：`title / author / digest|summary / cover / source_url / need_open_comment / only_fans_can_comment`。优先级：CLI 参数 > front matter > publish.yaml > 环境变量。

## Mermaid

````markdown
```mermaid
graph TD
    A --> B
```
````

- 默认引擎 `mmdc`：需要 `npm install -g @mermaid-js/mermaid-cli`（Windows 上 npm 的 `.cmd` 垫片已自动处理）
- `--mermaid-engine api`：无需安装，但图表源码会上传到第三方服务 mermaid.ink，注意隐私

## 产物与状态

- `build/article.preview.html`：本地预览（含样式外壳）
- `build/article.wechat.html`：上传后的最终正文（图片已替换为微信 URL）
- `.wechat_publish/`：token 缓存、图片/封面缓存、`posts/` 发布记录（目录已 gitignore；token 为明文 access_token，注意不要分享该目录）

## 开发

```bash
pip install -e ".[dev]"
pytest
ruff check wechat_publish tests   # lint（配置见 pyproject.toml）
```

## 已知限制

- `draft/add` 官方限制：title ≤64 字、author ≤8 字、digest ≤120 字、正文 <2 万字符，超限会在本地直接报错
- 尚未实现 `freepublish/submit`（发布）、素材管理等接口；`draft` 命令只创建草稿，发布仍需在公众号后台手动操作
