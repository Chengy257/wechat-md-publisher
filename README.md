# wechat-md-publisher

把 Markdown 文章渲染为微信公众号兼容 HTML，并一键创建公众号草稿（WeChat Official Account draft API）。

## 功能

- **Markdown → 微信正文**：sanitize（nh3 allowlist）→ 微信兼容化（代码块/列表/表格/标题改造）→ 外链转脚注 → CSS 内联（premailer），全流程无条件执行。sanitize 按 allowlist 语义移除对正常文章排版不安全/不支持的 HTML 构件（未列入白名单的标签与属性、`<script>/<style>` 内容、事件属性、非 http/https 链接等）；它服务于正常创作流程，**不承诺对任意不可信 HTML 安全**
- **内置主题**：`default / elegant / lapis / simple / tech / fancy / nb / filling`（随包分发，pip 安装即用），或用 `--style` 指定自定义 CSS
- **配色 × 版式引擎**：`--layout`（版式，决定结构形态）与 `--palette`（色板，决定全部颜色与代码高亮方案）两个独立维度自由组合；支持项目级自定义色板（`config/palettes/*.json`，同名覆盖内置）

### 内置主题

| 主题 | 风格 | 代码块底色 |
| --- | --- | --- |
| `default` | 经典绿，doocs/md 经典布局 | 浅灰（`#f6f8fa`） |
| `elegant` | 雅蓝，蓝色学术风 | 深蓝（`#2c3e50`） |
| `lapis` | 石青蓝，沉稳学术风 | 深蓝（`#2c3e50`） |
| `simple` | 简约黑白，极简排版 | 浅灰（`#f8f8f8`） |
| `tech` | 科技风，GitHub 暗色代码块 | 深黑（`#161b22`） |
| `fancy` | GitHub 风技术文，灰阶克制、蓝色链接 | 浅灰（`#f6f8fa`） |
| `nb` | 蓝紫现代风，主色 `#5b6cff` 点缀标题与引用 | 深蓝紫（`#272b3d`） |
| `filling` | 朱砂红暖调，主色 `#c0392b` 暖灰正文 | 暖浅灰（`#f7f2f0`） |

代码高亮调色板由主题色板的 `code_scheme` 决定：`elegant / lapis / tech / nb` 的色板声明 `github-dark`，其余声明 `friendly`。

### 配色 × 版式（--layout / --palette）

主题 = **版式**（结构形态：标题、引用、代码块等的排布）× **色板**（颜色令牌集合）。两者解耦后可自由组合：

- `--layout`：版式名。内置 5 个版式全部可用，与任一 `--palette` 自由搭配（5 × 8 = 40 种组合）：

| 版式 | 风格 |
| --- | --- |
| `default` | 经典骨架版式：仅结构公共件（代码块滚动/复制条、表格滚动等），无独立排版形态 |
| `serif` | 期刊衬线风：全文 Georgia/宋体衬线栈，居中加大的一级标题，1.9 倍行距、拉大段距，引用去底色改双侧留白细线 |
| `terminal` | 极简终端风：全局直角去阴影，等宽大写小号标题，仅左细线的无底色引用，代码块条从简（圆点结构保留、视觉隐藏） |
| `card` | 圆角卡片风：代码块/引用/表格卡片化（大圆角、软阴影、内边距加大），h2 带底色章节条；正文克制不铺底色 |
| `classic` | 古风文艺风：楷体栈、居中宽字距标题、大缩进留白式引用、细线表格；分隔线与文末装饰（`✦ ❖ ✦` / `❦`）由 ornaments 钩子注入真实文本 |

- `--palette`：色板名。内置 8 个色板（与主题同名），也可在项目下放置 `config/palettes/<name>.json` 自定义色板（与内置同名的项目色板优先生效；缺必需键或 `code_scheme` 非法时会 fail-closed 报错）。`--palette` 的候选项会随项目色板目录动态出现在帮助与校验中
- 代码高亮：由命中色板的 `code_scheme` 自动决定（`friendly` / `github-dark`），深色代码块底色的色板会自动搭配 `github-dark`
- classic 的装饰注入在 sanitize 之前完成（装饰符为真实文本字符，`section` + `class` 均在白名单内），幂等可重复调用；`--theme` 预设与 `--style` 文件路径不做装饰注入
- 三个子命令（`render / draft / inspect`）均支持这两个参数，语义一致；`config/publish.yaml` 可选配置 `layout` / `palette` 键作为默认值（存在即生效，CLI 显式参数优先；缺省行为不变）

**自定义色板完整示例**（放在项目根 `config/palettes/my.json`，之后 `--palette my` 即生效；`PALETTE_REQUIRED_KEYS` 全部必填，另附引擎骨架与版式实际引用的扩展 token——缺了会在渲染期 fail-closed 报错）：

```json
{
  "text": "#3e3e3e",
  "muted": "#666666",
  "link": "#576b95",
  "h1_color": "#3e3e3e",
  "h2_color": "#ffffff",
  "h2_accent": "#07c160",
  "h3_color": "#3e3e3e",
  "blockquote_border": "#07c160",
  "blockquote_bg": "#f7f7f7",
  "code_inline_bg": "rgba(27, 31, 35, 0.05)",
  "code_inline_color": "#d14",
  "code_bg": "#f6f8fa",
  "code_border": "rgba(0, 0, 0, 0.08)",
  "bar_bg": "#eef1f4",
  "bar_border": "rgba(0, 0, 0, 0.08)",
  "bar_text": "#57606a",
  "copy_btn_border": "#d0d7de",
  "copy_btn_bg": "#ffffff",
  "copy_btn_color": "#57606a",
  "table_border": "#dfdfdf",
  "th_bg": "rgba(0, 0, 0, 0.04)",
  "row_alt_bg": "#f8f8f8",
  "hr_color": "rgba(0, 0, 0, 0.1)",
  "radius": "8px",
  "code_scheme": "friendly",
  "strong_color": "#07c160",
  "em_color": "#3e3e3e",
  "list_marker_color": "#07c160",
  "footnote_ref_color": "#07c160",
  "footnote_url_color": "#576b95",
  "blockquote_color": "#3e3e3e",
  "codeblock_margin": "1.2em 0",
  "bar_font": "Menlo, Consolas, Monaco, monospace",
  "code_font": "Menlo, Operator Mono, Consolas, Monaco, monospace",
  "code_inline_padding": "3px 5px",
  "pre_code_color": "#3e3e3e",
  "figcaption_color": "#888888",
  "h2_bg": "#07c160",
  "h2_bottom_border": "#07c160",
  "h2_radius": "4px",
  "h3_border": "#07c160",
  "h3_margin": "1.8em 0 0.75em",
  "img_radius": "4px",
  "footnotes_border": "rgba(0, 0, 0, 0.1)",
  "footnotes_color": "#888888",
  "footnotes_li_color": "#888888",
  "footnotes_li_margin": "6px",
  "th_color": "#3e3e3e"
}
```

- 样式解析优先级：`--style` 文件 > `--layout` + `--palette` > `--theme` 预设 > 项目 `config/style.css` > 内置 `default` 兜底
- **表格与代码块排版**：表格单元格统一左对齐（忽略 markdown 对齐语法产生的内联 text-align）；表格自动包进横向滚动容器（`section.table-scroll`），宽表格在手机上可横向滚动查看全部列；带语言标注的代码块顶部显示语言条（`codeblock-bar`），长代码行可横向滚动；本地 preview 的每个代码块带"复制"按钮（微信正文保持零 JavaScript，复制按钮仅存在于 `build/*.preview.html`）
- **图片处理**：正文图自动上传（`media/uploadimg`，仅支持 JPG/PNG 且 ≤1MB）并替换 src；封面走永久素材（`material/add_material`，支持 JPG/JPEG/PNG/BMP/GIF 且 ≤10MB）；按接口拆分白名单并校验真实字节格式（魔数嗅探 + Pillow 可用时解码校验，伪图片/后缀与内容不符一律拒绝）；sha256 缓存避免重复上传；`--compress-cover` 可选 Pillow 压缩（AI 生成的封面始终自动压缩）
- **AI 增强**（可选）：`--ai-summary` 生成摘要（OpenAI 兼容接口，默认 DeepSeek）、`--ai-cover` 生成封面（Gemini，默认模型 `gemini-2.5-flash-image`，通过正式 API 参数 `imageConfig.aspectRatio: 21:9` 控制封面比例，并对返回图片做 MIME 与 Pillow 解码校验）
- **Mermaid 图表**：`--mermaid` 本地渲染（mmdc，Windows 下自动兼容 `.cmd`）或在线 API（`--mermaid-engine api`），按内容 hash 缓存
- **健壮性**：重试按幂等性分级（详见下文"错误处理与重试语义"）、45009 限频退避、token 过期（42001）自动刷新重试一次、状态文件原子写、图片上传失败默认中止发布
- **安全**：本地图片仅允许 markdown 目录 / 项目目录 / build 目录内的路径；远程图片下载默认阻断环回/内网/链路本地/保留网段（SSRF 防护，逐跳校验重定向目标）；`.env` 不入库；token 与 appid 输出均掩码

## 安装

```bash
pip install -e .            # 基础安装（运行时依赖齐全，pip 装完即用）
pip install -e ".[dev]"     # 含 pytest / responses / ruff / Pillow，装完可直接跑 pytest
pip install -e ".[cover-compress]"  # 仅 Pillow（--compress-cover 需要）
```

## 项目根与 `--project-dir`

三个子命令（`render / draft / inspect`）都支持 `--project-dir`，用于显式指定项目根（`config/`、`.env` 与输出路径的锚点目录）。未指定时按以下优先级自动解析：

1. 显式 `--project-dir`（最高优先级）
2. 显式 `--config` 所在目录（若在 `config/` 下则取其上一级）
3. 从当前目录向上最多 8 级搜索：命中含 `config/publish.yaml`、`config/publish.example.yaml` 或 `.git/` 的目录；含 `pyproject.toml` 时仅当其 `name = "wechat-md-publisher"` 才算命中
4. 都找不到时回退为当前目录（绝不回退到 site-packages）

## 配置

1. 复制 `config/publish.example.yaml` 为 `config/publish.yaml`，按需调整（代码会带警告忽略未知配置键）。示例文件仅作文档参考，运行时**不会**读取：缺少 `config/publish.yaml` 时使用内置默认值并打印 INFO 提示。注意：`paths.token_cache / image_cache / cover_cache` 自 v0.1.1 起已废弃（仍可解析但不再生效，会打印警告）——token 与素材缓存现在按公众号账号隔离存放在 `.wechat_publish/accounts/<account-key>/` 下；`paths.build_dir / state_dir / posts_dir` 不受影响
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

# 配色 × 版式自由组合（版式管结构，色板管颜色与代码高亮）
wechat-publish render --md input/article.md --layout default --palette nb
wechat-publish render --md input/article.md --layout classic --palette default

# 在项目根之外运行时显式指定项目根
wechat-publish render --md ~/blog/article.md --project-dir ~/blog

# 查看解析出的元数据与图片清单（不写盘）
wechat-publish inspect --md input/article.md

# inspect 同样支持元数据覆盖参数（与 draft 语义一致），用于预检真实发布效果
wechat-publish inspect --md input/article.md --title "覆盖标题" --author "作者" --digest "摘要" --cover cover.png

# 试运行：完全离线（无需微信凭据与 AI key，零网络请求，AI/mermaid 均跳过并提示）
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

## 远程图片与 SSRF 防护

正文中的 `http(s)://` 图片会在上传前下载到本地临时文件。为防止 SSRF（服务端请求伪造），默认策略：

- 仅接受 `http` / `https` URL；主机名 `localhost`（不区分大小写）一律拒绝
- 下载前解析目标主机的**全部** IP（含 IP 字面量直判），任一命中环回（127.0.0.0/8、::1）、内网（10/8、172.16/12、192.168/16 等）、链路本地（169.254/16，含云厂商元数据端点 169.254.169.254）、组播/保留/未指定地址即拒绝；DNS 解析失败同样拒绝
- 重定向不交给 HTTP 库自动跟随：每一跳都重新校验（含 Location 目标），最多 5 跳，重定向进内网会被拦截，且校验发生在发起请求之前

内网环境（如自建图床）确有需要时，在 `config/publish.yaml` 显式放行：

```yaml
remote_images:
  allow_private_networks: true   # 默认 false；非布尔值会在配置解析时报错
```

放行后跳过内网/环回判定，但 scheme 检查（仅 http/https）仍然保留。

## 产物与状态

- `build/article.preview.html`：本地预览（含样式外壳，标题与实际草稿一致——`--title` 等覆盖参数同样生效）
- `build/article.wechat.html`：上传后的最终正文（图片已替换为微信 URL）；在 `draft/add` 之前先行原子写盘，保证本地产物先于远端副作用就绪
- `.wechat_publish/`：本地状态目录（已 gitignore）
  - `accounts/<account-key>/`：按公众号账号隔离的 `token.json`、`image_cache.json`、`cover_cache.json`（`<account-key>` 为 AppID 的 sha256 前 12 位；切换公众号后各账号状态完全独立，token 与素材缓存绝不跨账号复用；缓存读改写由跨平台文件锁保护，并行发布不会互相丢失更新）
  - `posts/<id>/`：每次成功发布产生的**不可变发布快照**（`<id>` 为 UTC 时间戳（微秒）+ uuid，同秒同标题也不会碰撞）：
    - `state.json`：元数据（title、appid_hash、draft_media_id、created_at、content_sha256 等）
    - `final.wechat.html`：当次发给 `draft/add` 的最终正文，可随时复核发布时的真实内容
    - `source.md`：当次文章源文件的字节副本
  - `legacy/`：升级前 v0.1.1 的旧缓存文件会被自动移入此处，不读取也不复用（token 重新获取、缓存重建）
  - 注意：token 为明文 access_token，不要分享该目录

## 错误处理与重试语义

重试按请求幂等性分级，非幂等请求绝不透明重放：

- **幂等请求**（token 获取、远程图片下载等 GET）：连接错误/超时/5xx 自动退避重试（默认 2 次）。
- **素材上传**（封面、正文图 multipart 上传）：仅"连接未建立"（ConnectTimeout）与 5xx 响应可安全重试；读超时或一般连接错误时请求可能已被微信处理，工具会抛出 `AmbiguousRequestError` 并提示先检查微信素材库，绝不盲目重发。
- **`draft/add`**（创建草稿，非幂等）：只有 ConnectTimeout 可重试；其余不确定失败一律抛 `AmbiguousRequestError` 并提示"先查微信草稿箱再重试"——重放可能创建重复草稿。
- **远端成功、本地失败显式化**：草稿创建成功后若本地状态文件写盘失败，工具会报出远端 `media_id` 并警告"Do NOT blindly rerun"，先到公众号后台草稿箱核对，不要直接重跑（会重复建草稿）。最终 HTML 写盘发生在 `draft/add` 之前，此时失败尚无远端副作用，直接中止即可。

## 开发

```bash
pip install -e ".[dev]"
pytest
ruff check wechat_publish tests   # lint（配置见 pyproject.toml）
```

## 已知限制

- `draft/add` 官方限制：title ≤64 字、author ≤8 字、digest ≤120 字、正文 <2 万字符，超限会在本地直接报错
- 尚未实现 `freepublish/submit`（发布）、素材管理等接口；`draft` 命令只创建草稿，发布仍需在公众号后台手动操作
