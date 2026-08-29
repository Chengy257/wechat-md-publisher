# wechat-md-publisher v0.1.1 Reliability & Account Safety Hardening

## 1. 文档目的

本文档用于指导 coding agent 对 `Chengy257/wechat-md-publisher` 当前 `main` 分支进行一次集中式可靠性、安全性和可发布性修复。

本轮目标不是继续扩展大量新功能，而是把当前已经基本完整的 Markdown → 微信草稿发布工具，从“个人可用 / beta 状态”提升为“长期稳定、可安装、可迁移、不会因账号切换或重试逻辑导致错误发布”的 CLI 工具。

建议目标版本：

```text
v0.1.1 — Reliability & Account Safety Hardening
```

本轮应尽量避免大规模架构重写，优先通过局部重构、行为修正和测试补强完成。

---

# 2. 项目核心定位与必须坚持的产品原则

## 2.1 核心产品定位

项目核心流程应始终保持：

```text
Markdown article
    ↓
WeChat-compatible HTML rendering
    ↓
Body image localization/upload
    ↓
Cover upload
    ↓
WeChat draft creation
```

AI 不是核心依赖。

## 2.2 最小用户输入原则

理想情况下，一次正常发布只需要：

```text
article.md
cover.png
```

以及用户已经一次性配置好的微信公众号凭据：

```dotenv
WECHAT_APP_ID=...
WECHAT_APP_SECRET=...
```

推荐目标体验：

```bash
wechat-publish draft \
  --md article.md \
  --cover cover.png \
  --autofill-front-matter
```

对于包含一级标题的最小 Markdown：

```markdown
# 我的文章标题

正文内容……
```

工具应能够自动补齐必要的 front matter 元数据并创建草稿。

## 2.3 User-provided content first, AI optional

必须固定以下原则：

> User-provided content first, AI-assisted enhancement optional.

也就是：

- 用户提供标题 → 直接使用；
- 用户提供摘要 → 直接使用；
- 用户提供封面 → 直接使用；
- 用户没有启用 AI 参数 → 永远不应因为缺少 AI API Key 而阻塞发布；
- AI 只在用户显式启用时介入。

AI 功能保持显式 opt-in：

```bash
--ai-summary
--ai-cover
```

禁止未来把 AI 自动总结或 AI 封面生成改成默认必经步骤。

---

# 3. 当前项目总体评价

当前项目已经具备较好的模块化结构：

```text
wechat_publish/
  cli.py
  config.py
  render.py
  html_processor.py
  images.py
  token.py
  draft.py
  http.py
  state.py
  errors.py
  mermaid.py
  ai_summary.py
  ai_cover.py
  themes/
```

已经完成的优点包括：

- Markdown → HTML 渲染链路已统一；
- sanitize → 微信兼容化 → 外链脚注 → CSS inline；
- 本地图片路径限制；
- 远程图片大小限制；
- SHA-256 图片缓存；
- token 缓存；
- token 过期恢复；
- 5xx / busy / rate-limit 重试；
- 状态文件原子写；
- Mermaid 支持；
- AI 摘要和 AI 封面作为可选功能；
- 已有较完整的单元测试与 mocked API 集成测试。

因此本轮不需要推翻现有架构。

---

# 4. 本轮修复优先级总览

| Priority | Issue | Main Risk |
|---|---|---|
| P0 | token / media cache 未按 AppID 隔离 | 切换公众号后可能继续操作旧账号 |
| P0/P1 | Gemini 默认封面模型过时 | `--ai-cover` 默认不可用 |
| P1 | `pygments` / `cssutils` 直接依赖未显式声明 | clean install 可能直接失败 |
| P1 | 非幂等 POST 使用通用透明 retry | 可能重复创建草稿 / 重复上传 |
| P1 | `--dry-run` 仍要求微信凭据 | 与“纯本地试运行”语义不一致 |
| P1 | project root 回退到 package install dir | pip 安装后 build/state 路径错误 |
| P1 | `publish.example.yaml` 被当作运行时 fallback | 用户未配置时偷偷启用示例设置 |
| P1 | sanitizer 为 blacklist，不是严格 allowlist | 不可信 Markdown 安全边界不足 |
| P1/P2 | 正文图格式白名单过宽 | GIF/BMP 本地通过但微信失败 |
| P2 | metadata validation 发生太晚 | 上传素材后才发现标题/摘要非法 |
| P2 | state 不是真正历史快照 | 历史 post state 指向被覆盖的 HTML |
| P2 | inspect / draft 路径策略不完全一致 | 用户看到的诊断与实际发布不一致 |
| P2 | cache 并发无锁 | 多进程下可能丢失更新 |
| P2 | post state 文件名秒级冲突 | 并发时存在覆盖可能 |
| Cleanup | 根目录误提交 `-b` 文件 | 仓库卫生问题 |

---

# 5. P0：Account-scoped state 与缓存隔离

## 5.1 问题说明

当前 token cache 只记录：

```json
{
  "access_token": "...",
  "expires_at": 123456789
}
```

缓存没有绑定当前 AppID。

因此场景：

```text
公众号 A → 获取 token → cache
↓
修改 .env 切换到公众号 B
↓
A 的 token 尚未过期
↓
再次运行 draft
```

当前实现有可能继续复用 A 的 token。

更严重的是：

- cover cache 保存的是公众号作用域 `media_id`；
- body image cache 保存的是微信返回 URL；
- cache key 目前只与文件 hash 相关；
- 切换账号后可能错误复用旧账号素材状态。

这是当前最重要的 correctness / account safety 问题。

## 5.2 推荐设计

优先推荐目录级 account namespace：

```text
.wechat_publish/
  accounts/
    <account-key>/
      token.json
      image_cache.json
      cover_cache.json
  posts/
```

其中 `<account-key>` 不建议直接使用完整 AppID，可使用：

```text
sha256(appid)[:12]
```

或者安全的短标识：

```text
wx12ab...ef
```

### token.json

建议结构：

```json
{
  "appid": "wx...",
  "access_token": "...",
  "expires_at": 123456789
}
```

即使已经使用 account namespace，token 内仍建议写入 AppID 用作二次校验。

## 5.3 兼容旧 cache

首次升级时：

- 检测旧 `.wechat_publish/token.json`；
- 不建议自动信任并迁移旧 token；
- 最安全策略：忽略旧 token 并重新获取；
- image/cover cache 可选择不迁移，避免跨公众号误用。

## 5.4 验收标准

必须新增测试：

```text
1. A 账号生成 token/cache
2. 切换到 B 账号
3. B 不能命中 A token
4. B 不能命中 A cover media_id
5. B 不能复用 A account-scoped image cache
```

通过条件：不同 AppID 的所有账号相关 state 完全隔离。

---

# 6. P0/P1：修复 AI cover 默认模型与 API 行为

## 6.1 当前问题

当前默认：

```python
_DEFAULT_MODEL = "gemini-2.0-flash-exp"
```

该实验模型已经过时/退役，不能再作为长期默认值。

因此：

```bash
wechat-publish draft --ai-cover
```

在默认配置下可能直接失败。

## 6.2 修复要求

### 6.2.1 选择当前稳定 image model

Agent 实施时必须重新核对 Google Gemini 官方最新图像生成 API 文档，选择当前稳定、正式支持 image output 的模型。

不要继续使用 `*-exp` 作为长期默认。

建议结构：

```python
_DEFAULT_IMAGE_MODEL = "<current-stable-image-model>"
```

### 6.2.2 结构化控制图片比例

当前代码只在 prompt 中写：

```text
Use a 2.35:1 aspect ratio
```

应尽可能使用 API 支持的正式参数控制 aspect ratio，而不是依赖自然语言提示。

公众号封面目标比例建议继续使用接近：

```text
2.35:1
```

### 6.2.3 强化返回内容验证

生成后至少校验：

- response candidate 存在；
- image part 存在；
- base64 合法；
- MIME 是 image/*；
- bytes 非空；
- 如果 Pillow 可用，执行真实图片 decode/verify；
- 图片超限时走已有压缩逻辑。

## 6.3 产品原则

AI cover 必须继续是：

```text
explicit opt-in
```

即：只有以下情况才调用 AI：

```text
--ai-cover
AND
没有可用本地 cover
```

如果用户已经提供 `--cover cover.png`，原则上不得无故调用 Gemini。

---

# 7. P1：修复 Python package dependencies

## 7.1 当前问题

源码直接 import：

```python
from pygments import ...
import cssutils
```

但 `pyproject.toml` 运行时依赖未明确声明所有 direct imports。

这会导致 editable 开发环境看起来正常，但 clean wheel install 失败。

## 7.2 修复要求

`pyproject.toml` 中 direct dependency 至少补齐：

```toml
Pygments>=...
cssutils>=...
```

版本下限应选择合理稳定值，不要过度锁死。

## 7.3 dev dependency

当前测试中直接使用 Pillow，因此：

```toml
[project.optional-dependencies]
dev = [
  ...,
  "Pillow>=10",
]
```

或者将 Pillow 测试改为 `pytest.importorskip()`。

推荐前者，使 README：

```bash
pip install -e ".[dev]"
pytest
```

真正可直接工作。

## 7.4 新增 package smoke test

CI 必须增加：

```text
python -m build
pip install dist/*.whl
wechat-publish --help
wechat-publish render --md <minimal-example>
```

这个测试专门验证：

- runtime dependencies；
- bundled themes；
- package-data；
- CLI entry point；
- 安装后而非源码树中的真实行为。

---

# 8. P1：重新设计 HTTP retry policy，避免重复副作用

## 8.1 当前问题

通用 `request_with_retry()` 对：

- ConnectionError
- Timeout
- 500/502/503/504

统一自动重试。

这个策略对 GET 较安全，但目前也用于：

```text
material/add_material
media/uploadimg
draft/add
```

其中 `draft/add` 是明确的非幂等副作用 API。

风险：

```text
POST draft/add
↓
微信已经成功创建草稿
↓
客户端在读取响应时 timeout
↓
透明 retry
↓
再次创建草稿
```

## 8.2 推荐实现

新增 retry policy 概念，例如：

```python
RetryPolicy.SAFE
RetryPolicy.UPLOAD
RetryPolicy.NON_IDEMPOTENT
```

最低要求：

### GET / safe request

允许：

```text
connect timeout
read timeout
connection error
5xx
```

自动退避重试。

### 非幂等 POST

保守策略：

- 明确“请求尚未发送”的连接错误可重试；
- ambiguous read timeout 不透明自动重放；
- 微信 JSON 明确返回 busy/rate-limit code 时，可按现有业务层策略重试；
- `draft/add` 尤其必须避免 blind replay。

## 8.3 错误提示

如果发生 ambiguous network failure，应明确告诉用户：

```text
The request outcome is uncertain. Check WeChat draft list before retrying.
```

不要简单输出“failed, rerun”。

## 8.4 测试

新增测试：

- GET 502 后正常 retry；
- draft/add 明确业务错误按策略处理；
- draft/add read timeout 不自动发第二个 POST；
- error message 标记 outcome uncertain。

---

# 9. P1：`--dry-run` 必须真正完全离线、无需凭据

## 9.1 当前问题

当前逻辑先：

```python
resolve_credentials()
```

然后如果缺少 AppID/AppSecret 立即失败，之后才判断 `--dry-run`。

所以：

```bash
wechat-publish draft --md article.md --dry-run
```

在完全未配置微信凭据的机器上仍会失败。

## 9.2 正确语义

`--dry-run` 应意味着：

```text
0 network requests
0 required remote credentials
0 token access
0 AI calls
```

即使用户启用：

```bash
--ai-summary --ai-cover --dry-run
```

也应只打印：

```text
AI summary would be generated
AI cover would be generated
```

而不实际调用 API。

## 9.3 建议实现顺序

```python
stage = _render_stage(...)
appid, appsecret = resolve_credentials(...)

if args.dry_run:
    _print_dry_run(stage, appid or "", args)
    return 0

require_appid_and_secret()
```

## 9.4 测试

新增一个 fixture：

```text
无 WECHAT_APPID
无 WECHAT_APPSECRET
无 AI keys
```

验证：

```bash
wechat-publish draft --dry-run
```

仍返回 0。

---

# 10. P1：修复 project root / path resolution

## 10.1 当前问题

当前 `_project_dir()`：

```text
cwd 有 config/ → cwd
否则 → Path(__file__).parent.parent
```

pip 安装后，后者可能变成：

```text
site-packages/
```

这会导致：

```text
build/
.wechat_publish/
config/
```

错误地围绕 package installation directory 解析。

## 10.2 新设计

推荐优先级：

```text
1. explicit --project-dir
2. explicit --config 所在工程上下文
3. 从 cwd 向上搜索项目 marker
4. fallback = cwd
```

项目 marker 可以包括：

```text
config/publish.yaml
config/publish.example.yaml
pyproject.toml
.git/
```

需要注意：

- 不能因为某个 Python package 目录里有 pyproject 就误认；
- fallback 永远是用户当前目录，而不是 `site-packages`。

## 10.3 建议新增 CLI

```bash
--project-dir PATH
```

可优先用于 agent/CI/自动化场景。

## 10.4 测试

至少覆盖：

```text
1. 在项目根运行
2. 在项目子目录运行
3. pip 安装后在任意空目录运行 render
4. --project-dir 显式覆盖
```

---

# 11. P1：`publish.example.yaml` 不再作为 runtime fallback

## 11.1 当前问题

当前配置加载逻辑：

```text
publish.yaml 不存在
→ 自动读取 publish.example.yaml
```

这意味着 example 文件实际上具有 runtime semantics。

示例中又包含：

```yaml
default_author: "Cy257"
```

公开工具中不应让示例配置偷偷成为用户默认配置。

## 11.2 修复要求

修改为：

```text
publish.yaml 不存在
→ return {}
→ optional warning
```

`publish.example.yaml` 只用于：

```bash
cp config/publish.example.yaml config/publish.yaml
```

## 11.3 example 清理

建议：

```yaml
default_author: ""
```

并避免任何带开发者个人信息的默认值。

---

# 12. P1：HTML sanitizer 升级为 allowlist 安全模型

## 12.1 当前问题

Markdown renderer 当前允许 raw HTML：

```python
MarkdownIt(..., {"html": True})
```

sanitizer 主要使用 blacklist：

```text
strip script / iframe / style / link / form ...
remove on* attributes
```

对于作者自用 Markdown 基本够用，但不能声称能够安全处理完全不可信 HTML。

## 12.2 推荐方案

优先评估成熟 sanitizer：

```text
nh3
bleach
```

推荐采用 explicit allowlist：

### allowed tags

例如：

```text
p, section, span,
strong, em, del,
blockquote,
pre, code,
br,
img,
table, thead, tbody, tr, th, td,
sup
```

结合项目实际生成结构确定。

### allowed attributes

例如：

```text
class
style
src
alt
title
colspan
rowspan
```

### URL protocol

只允许必要协议：

```text
http
https
```

如保留内部 anchor，则允许安全的 `#...`。

## 12.3 注意顺序

建议：

```text
Markdown render
↓
allowlist sanitize
↓
WeChat compatibility transforms
↓
link footnotes
↓
CSS inline
↓
optional final sanitize/validation
```

要避免 sanitizer 把项目自己生成的必要 class/style 全删掉。

## 12.4 README 文案

在实现完整 allowlist 前，不要将安全描述扩大成：

```text
safe for arbitrary untrusted HTML
```

可以保留更准确描述：

```text
removes unsafe/unsupported HTML constructs for normal article authoring
```

---

# 13. P1/P2：图片验证与 remote image security

## 13.1 正文图与封面图白名单拆开

当前共用：

```text
jpg/jpeg/png/gif/bmp
```

应拆为至少：

```python
_BODY_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_COVER_EXTENSIONS = {...}
```

以微信实际 API 当前约束为准。

## 13.2 验证真实图片格式

不能只依赖后缀。

推荐：

- 轻量 magic bytes 检查；
- Pillow 可用时执行 `Image.verify()`；
- 检查真实 format 与 extension / MIME 是否合理匹配。

## 13.3 Remote image SSRF guard

当前 Markdown 中任意 http/https 图片会触发本机下载。

需要阻止：

```text
localhost
127.0.0.0/8
::1
RFC1918 private networks
link-local
169.254.169.254
multicast/reserved ranges
```

并注意：

- DNS resolution 后检查目标 IP；
- redirect 后再次检查；
- 禁止 redirect 到 private network。

## 13.4 配置策略

可增加配置：

```yaml
remote_images:
  allow_private_networks: false
```

默认必须为 false。

个人自用场景如确实需要访问内网图片，可显式开启。

---

# 14. P2：所有本地可发现错误必须在上传前验证

## 14.1 当前问题

部分 DraftArticle validation 在：

```text
cover 已上传
body images 已上传
↓
build_draft_payload()
```

才执行。

因此标题/摘要超长等错误可能浪费前面的素材上传。

## 14.2 新增 preflight validation

增加：

```python
validate_publish_preflight(...)
```

在任何网络请求前检查：

- title required；
- title length；
- author length；
- digest length；
- comment flags 范围；
- source URL 基础格式；
- cover 是否存在 / 可读；
- local image path；
- 最终 HTML 大小估算；
- required credentials（仅非 dry-run）。

## 14.3 保留最终 validation

仍保留 `DraftArticle` 最终校验作为 defense in depth。

---

# 15. P2：发布副作用后的错误处理必须明确

## 15.1 当前风险

远程 draft 创建成功后，如果本地 state/html 写入失败，CLI 可能整体返回失败。

用户可能误以为没有创建成功而直接重跑，造成重复草稿。

## 15.2 修复建议

### 顺序优化

推荐：

```text
render
↓
upload body images
↓
得到 final HTML
↓
先把 final HTML 原子写盘
↓
draft/add
↓
记录 remote media_id
```

### remote success / local failure

如果：

```text
draft/add 已成功
state save 失败
```

必须输出特别错误类型，例如：

```text
RemoteDraftCreatedLocalStateFailed
```

并明确显示：

```text
remote media_id: XXXXX
Do not blindly rerun. Check WeChat draft list first.
```

---

# 16. P2：将 post state 改为真正的 immutable history

## 16.1 当前问题

多个 post state 都指向：

```text
build/article.wechat.html
```

后续发布会覆盖它，因此旧 state 不能复现当时内容。

## 16.2 推荐结构

例如：

```text
.wechat_publish/posts/
  20260829T101530-abc123/
    state.json
    final.wechat.html
    source.md
```

state.json：

```json
{
  "title": "...",
  "appid_hash": "...",
  "draft_media_id": "...",
  "created_at": "...",
  "content_sha256": "...",
  "source_markdown": "source.md",
  "wechat_html": "final.wechat.html"
}
```

是否复制 `source.md` 可根据磁盘开销决定，至少 final HTML 必须 snapshot。

## 16.3 文件名唯一性

不要只使用秒级时间 + title。

建议：

```text
UTC timestamp with microseconds
+
short random UUID / content hash
```

---

# 17. P2：统一 inspect / render / draft 行为

需要确保三条命令的路径和渲染语义不产生明显分叉：

```text
render
inspect
draft
```

重点修复：

- `inspect` 使用与 `draft` 相同的 allowed roots；
- preview title 使用最终 resolved article.title，而不是仅 front matter title；
- CLI `--title` override 后 preview 与实际 draft 标题一致；
- `<!--more-->` 处理一致；
- theme resolution 一致；
- image discovery 一致。

建议将重复逻辑进一步收敛到 shared preflight/render context，但避免大重构。

---

# 18. P2：配置 schema normalization

当前部分配置如：

```yaml
appid_env:
  - WECHAT_APPID
  - WECHAT_APP_ID
```

如果用户误写：

```yaml
appid_env: WECHAT_APP_ID
```

简单 `tuple(string)` 会产生字符 tuple。

建议新增 normalization：

```python
normalize_string_or_list(...)
```

并明确处理：

```text
str → [str]
list[str] → list[str]
其他 → validation error / warning
```

进一步可引入轻量 config schema，但本轮不要求 Pydantic 大重构。

---

# 19. P2：API response schema validation

当前 JSON 解析主要确认：

```text
response is dict
errcode == 0 / absent
```

之后直接访问：

```python
data["media_id"]
data["url"]
data["access_token"]
```

如果微信返回异常但无 errcode，可能暴露裸 `KeyError`。

建议每个 API 层增加 expected field validation：

```python
require_field(data, "media_id", operation)
```

错误统一包装为：

```text
WeChatAPIError / WeChatProtocolError
```

不要把内部 KeyError 直接暴露给用户。

---

# 20. P2：并发安全与 cache 写入

## 20.1 当前状态

JSON 写入采用 temp + `os.replace`，原子写设计是好的。

但：

```text
load cache
modify
save cache
```

没有文件锁。

两个并行 publisher 可能：

```text
A load old cache
B load old cache
A save A entry
B save B entry
→ A entry 丢失
```

## 20.2 本轮建议

如果保持 JSON cache：

- 增加跨平台 file lock；
- 或增加轻量 lock library；
- 或把 cache 迁移 SQLite（本轮不是必须）。

推荐 v0.1.1 使用 file lock，避免过度设计。

---

# 21. Repository hygiene

删除根目录异常文件：

```text
-b
```

并检查来源。

确保 `.gitignore` 覆盖：

```text
.env
build/
.wechat_publish/
config/publish.yaml
input/*
!input/article.example.md
```

视实际仓库规则微调。

---

# 22. CI / Quality Gate

当前应建立 GitHub Actions。

## 22.1 推荐矩阵

Python：

```text
3.10
3.11
3.12
3.13
```

OS：

```text
ubuntu-latest
windows-latest
```

至少 Windows 需要覆盖，因为 Mermaid `mmdc.cmd` 有专门兼容代码。

## 22.2 Jobs

### lint

```bash
ruff check wechat_publish tests
```

### tests

```bash
pytest
```

### package

```bash
python -m build
pip install dist/*.whl
wechat-publish --help
```

### smoke render

创建临时：

```markdown
# Smoke Test

Hello.
```

执行：

```bash
wechat-publish render --md article.md
```

验证：

```text
preview exists
wechat html exists
bundled theme works
no ModuleNotFoundError
```

---

# 23. 必须新增的回归测试清单

## Account safety

- token cache bound to AppID；
- cover cache isolated by account；
- image cache isolated by account；
- switch A → B cannot reuse A state。

## Dry run

- no WeChat credentials；
- no AI keys；
- `--dry-run` returns success；
- monkeypatch all network functions to fail if called；
- `--ai-summary --ai-cover --dry-run` still makes no requests。

## Package install

- wheel install；
- Pygments import；
- cssutils import；
- bundled themes present。

## Retry safety

- GET retries 502；
- non-idempotent draft timeout not blindly replayed；
- busy error behaves as expected；
- ambiguous response tells user to check remote draft state。

## Project root

- project root；
- nested directory；
- empty directory after pip install；
- explicit `--project-dir`。

## Config

- no publish.yaml → empty config；
- example never auto-loaded；
- string env-key normalized；
- invalid type gets clean error。

## Images

- body jpg pass；
- body png pass；
- body gif reject locally；
- body bmp reject locally；
- fake `.png` invalid image rejected when verification enabled；
- remote localhost blocked；
- redirect to private IP blocked；
- oversized remote image blocked。

## Preflight

- title too long fails before any network call；
- digest too long fails before any upload；
- missing cover fails before token/material upload where appropriate。

## History

- two published posts preserve separate final HTML snapshots；
- same title same second does not collide。

---

# 24. 推荐实施阶段

## Phase 1 — Account Safety + Critical Correctness

实施：

1. account-scoped cache/state；
2. AppID binding；
3. cache migration strategy；
4. dry-run no credentials；
5. preflight validation；
6. remove `-b`。

完成后先跑全测试。

## Phase 2 — Packaging + Path Semantics

实施：

1. direct dependencies；
2. dev Pillow；
3. project root resolution；
4. `--project-dir`；
5. example config no fallback；
6. wheel smoke tests。

## Phase 3 — Network / Side-effect Reliability

实施：

1. retry policy split；
2. draft/add ambiguous failure handling；
3. API schema validation；
4. remote-success/local-failure explicit error。

## Phase 4 — Input & HTML Hardening

实施：

1. body image format split；
2. image content validation；
3. SSRF guard；
4. sanitizer allowlist。

## Phase 5 — State History + CI

实施：

1. immutable post snapshots；
2. unique IDs；
3. cache locking；
4. GitHub Actions matrix。

---

# 25. 本轮不建议优先做的功能

在 v0.1.1 完成前，不建议优先实现：

```text
freepublish/submit
GUI
Web UI
多人协作
CMS server
自动从 URL 抓文章
大量新增 AI provider
复杂模板市场
```

原因：当前最大价值不是增加新功能，而是先保证：

```text
不会发错公众号
不会因为网络重试重复建草稿
pip install 后可正常运行
不配置 AI 也可以完整发布
用户只提供 article.md + cover.png 即可完成核心流程
```

---

# 26. v0.1.1 Definition of Done

只有以下全部完成，才认为本轮结束。

## Core experience

以下流程必须成立：

```bash
wechat-publish draft \
  --md article.md \
  --cover cover.png \
  --autofill-front-matter
```

在：

```text
无 AI_API_KEY
无 GEMINI_API_KEY
```

情况下仍能够完整创建草稿。

## AI optionality

只有显式：

```bash
--ai-summary
```

才生成摘要。

只有显式：

```bash
--ai-cover
```

且没有可用本地封面时才生成封面。

## Account safety

切换 AppID 后：

```text
不得复用其他公众号 token
不得复用其他公众号 cover media_id
不得复用其他公众号 account-scoped state
```

## Reliability

- 非幂等 draft POST 不得 blind retry；
- 本地 metadata 错误必须在上传前发现；
- remote success + local failure 必须清晰提示；
- package wheel clean install 可运行；
- CI Linux / Windows 通过。

## Security

- body image format 严格匹配微信要求；
- remote image 默认禁止 private network SSRF；
- HTML sanitization 使用明确 allowlist 或达到等价安全边界。

## State

每篇成功草稿都有不可变的本地最终 HTML snapshot 和 remote draft media ID。

---

# 27. 后续版本方向（非本轮范围）

v0.1.1 完成后，可考虑 `v0.2 / v1.0`：

## Multi-account profile

```bash
wechat-publish --profile lab draft ...
wechat-publish --profile personal draft ...
```

## Draft management

```text
list drafts
update draft
replace cover
republish from local snapshot
```

## Free publish

实现 `freepublish/submit`，但必须建立在当前副作用安全模型之上。

## Better article metadata inference

使最小工作流进一步变成：

```bash
wechat-publish draft article.md --cover cover.png
```

自动从首个一级标题提取 title，无需显式 `--autofill-front-matter`。

这是推荐的未来 UX，但应在保持行为可预测的前提下实现。

---

# 28. Agent 实施要求

Coding agent 在实施时必须遵守：

1. 每个 Phase 独立 commit；
2. 每修复一个重要 bug，同步增加 regression test；
3. 不删除已有安全检查以简化实现；
4. 不把 AI 变成核心依赖；
5. 不默认执行真实微信发布；
6. 测试中所有微信 / AI 网络调用必须 mock；
7. 修改外部 API 相关代码前重新核对当前官方文档；
8. 不以 README 当前描述代替真实 API 行为；
9. 对已有行为如有 breaking change，必须更新 README 与 example config；
10. 最终提交前执行：

```bash
ruff check wechat_publish tests
pytest
python -m build
```

并在 clean environment 对生成 wheel 做 smoke test。

---

# 29. 最终目标

本项目下一阶段不应追求“功能更多”，而应首先达到以下状态：

> 一个以用户内容为主、AI 为可选辅助、只需要 Markdown + 指定封面即可完成微信草稿创建，并且具备账号隔离、可预测网络行为、可靠安装和可追溯发布状态的 Markdown publishing CLI。

核心产品原则固定为：

```text
User-provided content first.
AI-assisted enhancement optional.
Publishing side effects explicit and safe.
Account state isolated by default.
```

