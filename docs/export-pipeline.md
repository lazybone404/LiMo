# 导出管线

> 写于 2026-07-10，最后更新 2026-10-10。
> 记录 Markdown→HTML/Word 导出、思维导图、批量 ZIP 的完整管线架构、设计决策、内容映射表和已知局限。

---

## 1. 架构总览：两条导出路径

导出系统提供两条互补路径，各自有不同的设计目标：

```
用户入口                        前端组件                    后端端点
────────                        ────────                    ────────
PreviewArea 工具栏             ExportMenu（下拉菜单）        GET /export/{id}
  ├─ 下载 MD（不嵌图） →      客户端直读 store              （无后端调用）
  ├─ 下载 MD（嵌图）   →      fetch → downloadBlob    →     GET /export/{id}?format=md&image_md=inline
  ├─ 导出 HTML         →      downloadFromUrl()      →     GET /export/{id}?format=html&mode=...&version=N
  ├─ 导出 Word         →      downloadFromUrl()      →     GET /export/{id}?format=docx&mode=...&version=N
  ├─ 全部产物 ZIP      →      downloadFromUrl()      →     GET /export/{id}/all
  ├─ 思维导图 SVG      →      客户端 DOM clone              （无后端调用）
  ├─ 思维导图 HTML     →      downloadFromUrl()      →     GET /export/{id}/mindmap
  └─ 复制 MD           →      客户端 clipboard             （无后端调用）

顶部工具栏 Download 按钮       ExportPanel（侧边面板）       POST /export/custom
  ├─ 导出全部产物      →      downloadFromUrl() / fetch →  GET /export/{id}/all 或 POST /export/batch
  └─ 详细导出 (ZIP)    →      进度反馈 ⚠️ 未接线        →     POST /export/custom/stream（SSE 进度）
                                                        →     GET /export/download/{temp_id}（临时下载）

AI 对话右键菜单                 ChatCore                     GET /export/conversation/{id}
  ├─ 导出 Markdown     →      downloadFromUrl()      →     GET /export/conversation/{id}?format=md
  └─ 批量导出 ZIP      →      downloadFromUrl()      →     POST /export/conversations/batch
```

> **9 个导出端点**：`GET /export/{id}`（单文件）、`GET /export/{id}/all`（全产物）、`POST /export/batch`（多文档）、`GET /export/{id}/mindmap`、`GET /export/conversation/{id}`、`POST /export/conversations/batch`、`POST /export/custom`、`POST /export/custom/stream`（SSE 进度）、`GET /export/download/{temp_id}`。
>
> ⚠️ **`/export/custom/stream` 与 `/export/download/{temp_id}` 当前无前端调用者**（2026-10-06 P1-12 核实：`api/export.ts::streamExportProgress` 零 import）。端点本身完备（打包已 `to_thread` 不阻塞事件循环），但 `ExportPanel` 的「详细导出」走的是**同步** `POST /export/custom` —— 上表那条「进度反馈」是**能力**不是**现状**。

### 快捷 MD 的图片内嵌（2026-10-11）

「下载 MD」**默认把图片一起 base64 内嵌**（单文件自包含，离线/分享都能看），设置 → 通用可关。

- 判据在 [lib/export-prefs.ts](../frontend/src/lib/export-prefs.ts) 的 `embedImages`（默认 `true`）；`ExportMenu.tsx` 读它决定走客户端直读还是后端那条路。
- **为什么默认走后端**：正文里是 `assets/xxx.png` 这种文档目录内的相对引用，**纯前端拿不到图片字节** —— 内嵌必须后端读盘。关掉它 = **不依赖后端**（纯前端 Blob 下载），代价是图片保持绝对 URL、只在本机应用内有效，与改造前的行为逐字相同。
- 后端 `GET /export/{id}` 加 `image_md: str = Query("link")`：**只有 `inline` 才动图片**，`link`（默认）逐字节零回归 —— 详细导出面板的 `image_md` 是另一套（`separate`/`inline`/`none`），两者互不影响。
- **内嵌与「重写为绝对 URL」互斥**：内嵌后引用是 data URI，不再重写。查不到的图与**越界路径**（`../`）退回绝对 URL，不静默丢引用（`_inline_images` 的 `relative_to` 守卫）。
- **只影响 `format=md`**：HTML 导出早有自己的一套 base64 内联，两条路互不干扰。

### 按版本导出（2026-10-10）

`GET /export/{id}` 加 `version: int = Query(0)`（**0 = 最新版**，与 `get_markdowns_by_transcript` 的语义一致）。三条后端导出路径（HTML / Word / PDF，PDF 复用 HTML 那份 URL）**共用**同一份参数拼装（前端 `ExportMenu.tsx::backendParams()`，版本语义只在一处拼）。

- **用户切到哪一版，导出就是哪一版**（项目「所见即所得」原则）。MD 下载 / 复制 MD 走客户端 store 直读，天然正确，不需要参数。
- ⚠️ **显式指定的版本查不到时显式报错、不回退最新**——沿用 `QueryService` 的既有纪律（读了错的版本比读不到更糟）。
- ⚠️ **前端必须校验 mode**：`markdownMeta` 只在加载文档时设一次（notes 优先），切到 polished tab **不会**更新它。不校验就会把 notes 的版本号塞给 polished 导出，拿到「polished 的第 N 版」而不是用户正在看的那一版（`ExportMenu.tsx::exportVersion` 的 `markdownMeta.mode === tabDef.htmlMode` 判据）。
- 批量导出（`/export/{id}/all`、`POST /export/batch`、`POST /export/custom`）**不带 version**：它们在版本归档布局下自然导出**全部版本**。
- `_pick_artifact`（`export.py`）在 mode 过滤后按 version 再过滤；`_collect_doc_products` / `_build_export_zip` 保持原样。


### 卡片骨架跟随（2026-10-10；判据 2026-10-11 改由标记决定）

预览区有一条**骨架轴**（`data-skeleton="card"`：**被 `card` 节修饰符标过的**章节渲染成一张卡片，其余保持普通正文）。导出**跟随**它——项目核心约束「用户在预览栏看到什么，导出就应该得到什么」，卡片不例外。

⚠️ **触发判据在 2026-10-11 由「每个 H2」改成「H2 + 该节的 `card` 标记」**（用户判「每个 H2 一张卡太简单粗暴」），故页面里卡片与普通章节是**混排**的、**没有外层容器**（`.md-cards` 已删）。**导出侧零改动** —— `split_by_h2` 多返回一个 `card` 字段，`<section class="md-card">` 分支只对 `card=True` 的节触发，其余节照原样穿过渲染链，`.md-cards` 的产出语句删掉即可。

**参数**（三条后端路径 `GET /export/{id}` 共用）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `skeleton` | `"paper"` | `card` 才切卡；**默认 `paper` ⇒ 导出与改造前逐字节一致** |
| `card_style` | `"gpt"` | 8 套视觉方案之一；不在 `_CARD_STYLES` 白名单内**静默回退 `gpt`** |
| `card_vars` | `""` | **逐维度微调**（2026-10-11）：`--a:1;--b:2` 形态，值是**前端解析好的 CSS 值**；空串 ⇒ 一个字节都不多 |

前端拼装点两处：[ExportMenu.tsx](../frontend/src/components/ui/ExportMenu.tsx) 的 `backendParams()`（HTML / Word / PDF 三条路径共用，`lib/print.ts` 无需改）与 [ExportPanel.tsx](../frontend/src/components/ui/ExportPanel.tsx) 的 POST body。⚠️ **两条传输各拼一次，但变量串同出 `lib/card-prefs.ts::serializeCardVars`**。⚠️ **`paper` 时一个参数都不发** —— 后端默认值同为 `paper`，URL 与改造前逐字一致。批量导出面板（`ExportCustomRequest`）有同名字段。

**逐维度微调（`card_vars`，2026-10-11）** ——「底 / 抬升 / 边」三根旋钮（8 套方案覆盖不到的组合：抬升只有 0%/100% 两档、中间一整段为空）。三个**必须同源的落点**：

- ⚠️ **后端只做字符白名单，不持有「档位 → 取值」的第二张表** —— 前端传的是 `--card-lift:40%` 这种**解析好的值**。后端若自己再维护一张表，格式演进时漏改一侧的症状是「预览里调过了、导出还是方案原样」，而导出**照样成功**。名字限 `_CARD_TUNE_VARS`（三个），值不许含 `;{}<>"'\/*` —— 合起来正是「从变量值逃逸出去」需要的全套字符；非法项**逐项静默丢弃**（判据同 `card_style`：微调出错不改变产物的性质）。
- ⚠️ **该段 `<style>` 必须排在 `cards.css` 之后** —— 方案块 `[data-card-style="x"]` 与微调段 `:root` 的**特异性相同**（都是 0,1,0），同特异性下后者胜。放到前面会被方案原样压住。有专门的索引断言钉住。
- ⚠️ **`paper` 模式下不注入**（没有卡片，注入了也是死规则，还会破坏「paper 导出逐字节不变」）。

**CSS 拼接顺序**（顺序即优先级）：`theme_css` → **`_CARD_TOKENS_CSS` + `_load_card_css()` + 微调 `:root`** → pygments → badge → katex → `_PRINT_CSS`（垫底）。卡片 CSS 必须在 `_PRINT_CSS` 之前。

**HTML 侧**（`_build_selfcontained_html`）：
- `card`：`split_by_h2(md)` 切分 → `preamble` 不包 → **带 `card` 标记的**节 `_markdown_to_html` 后包 `<section class="md-card">`，**未标记的节原样穿过渲染链**（前后是兄弟节点，**不再装进任何容器**）。
  - ⚠️ **节修饰符标记行必须在渲染前剥掉**（剥它的位置在 `parse_blocks` 的标记行剥除，切分器只管认不认得出它）—— 读者不该在页面上看到 `<!-- limo-block … -->`。`test_card_模式下标记行不落进产物` 钉住，且**判据限定在 `<body>` 之后的正文区**（内联的 `cards.css` 注释里就写着这句标记的示例，拿整份 HTML 去断会恒红）。
  - ⚠️ **章末的 `---` 剥不剥，判据挂在 `card` 上而不是挂在骨架轴上**：成卡的节剥（卡的下边界已承担分隔），**不成卡的节保留**（它没有卡边界，`---` 是唯一的分隔手段）；**章节内部**的 `---` 恒留。三个方向各有一条断言 —— 漏掉中间那条就是「开了卡片模式、普通章节之间的分隔线却消失了」，一个与骨架轴无关的损失。
- ⚠️ **`<html>` 必须同时带 `data-skeleton="card"` 和 `data-card-style`** —— 前者是卡片 CSS 的**主作用域**（`cards.css` 里每条规则都带 `[data-skeleton="card"]` 前缀），只带后者的话规则**永不命中** ⇒ 导出的是**没有样式的卡片**，而「section 数与 H2 数一致」「CSS 里有卡片规则」这类断言**全绿**（本项目点名的「断言全过、肉眼等于没有」）。
- ⚠️ **导出 HTML 的主题（`HTML_THEMES`）是自包含裸色值 CSS，一个 `--tk-*` 都不提供** —— 而卡片 CSS 有 27 处 `var(--tk-*)` 引用（8 个令牌），`var()` 无 fallback 时整条声明作废 ⇒ 卡片**没有底色、没有边框、没有圆角**，且**应用内预览里一切正常**（那边 `--tk-*` 由主题系统提供）。故 `export.py` 必须补一份 `_CARD_TOKENS_CSS`（**三态**：浅色 / `prefers-color-scheme: dark` / `@media print`，后者还原浅色）。⚠️ **三态都要给全**：`@media print` 与暗色媒体查询**会同时匹配**。`test_export_cards.py::test_卡片_CSS_引用的每个界面令牌在导出页里都有定义` 是这条的**成对常量守卫**（正则抽 `var(--tk-…)` 后逐一查定义），能自动发现「cards.css 新引了一个令牌而导出侧没补」。

**`_load_card_css()`** —— 卡片 CSS 的**单一真相源在前端** [frontend/src/styles/cards.css](../frontend/src/styles/cards.css)，后端**从磁盘读**并内联，**零重复定义**。候选路径：开发环境 `仓库根/frontend/src/styles/cards.css`；打包环境 `sys._MEIPASS/styles/cards.css`（PyInstaller spec 的 datas 收集源文件本身）。⚠️ **刻意不往 `backend/static/` 放副本** —— 副本就是第二份拷贝，正是要消除的形态，只是绕到打包路径上发生。⚠️ **读不到显式抛 `AppError("EXPORT_CARD_CSS_MISSING", recoverable=False)`**，不做「静默输出无样式卡片」。⚠️ **不缓存**（对照 `_get_katex_assets()` 的 `_KATEX_CACHE`：KaTeX 是只读第三方资产，而 `cards.css` 是**我们会改的源文件**，缓存会让改动静默不生效）。

**Word 侧**（`markdown_to_docx(..., skeleton=...)`）：解析**同一份 HTML**，识别 `<section class="md-card">` → **单单元格表格**包裹该节（`Table Grid` 边框 + 底纹 `FILL_CARD`）。⚠️ **docx 无圆角无投影** —— Word 侧的「卡片」=「有边框和底纹的块」，这是**能力边界不是实现省略**。⚠️ **刻意不接收 `card_style`，同样不接收 `card_vars`**：8 套皮肤会退化成第二份硬编码色表，Word 表达不了圆角/投影；逐维度微调（底/抬升/边）**整根轴都长在 `--card-*` 的 CSS 层上**，Word 侧走的是结构层（单单元格表格 + 固定边框/底纹），`--card-*` 一个都不参与。三处实现陷阱：
1. **`_render_block` 的 elif 链必须加在中间**（`blockquote` 之后）—— 链尾的 `else` 是「未知块 → 递归子元素」，排在它后面的分支**永不可达**。
2. ⚠️ **正因为那个 `else` 兜底，漏加分支不会丢内容**（section 被当未知块递归、正文照旧渲染）⇒「读回断言内容还在」这种断言**在漏加时照样绿**。断言必须落在**结构层**（该节被单单元格表格包裹 / `w:tcPr/w:shd` 的 `w:fill == FILL_CARD`）。
3. `_Cell` **没有 `add_heading`**，而卡片头就是那个 `##` —— `_CellProxy` 代理 `add_paragraph` / `add_table` / `add_heading`（掉了的话标题退化成正文段落，观感全变、零报错）。

**PDF 侧**：**自动跟随，零后端改动** —— PDF 复用 HTML 端点那一份自包含 HTML，前端在 URL 里带上卡片参数即可（`exportPdfFromUrl` 复用 `backendParams`）。

**⚠️ 不跟随的**：纯 **Markdown / 纯文本**导出（md 就是 md，没有视觉层）；`/export/{id}/all`（只打包原始 md 产物，不渲染 HTML）。

**跨语言同源**：前后端**各有一份** H2 切分实现（JS `splitByH2` / Python `split_by_h2`），靠**共享 fixture** [frontend/src/test/fixtures/h2-sections.json](../frontend/src/test/fixtures/h2-sections.json) 钉住一致 —— 前端 vitest 与后端 pytest **各读同一文件、各跑各的断言**。切分点必须在 `_protect_latex` **之前**（前端 `splitByH2` 看到的就是原始 md，两侧输入同源才谈得上切出同一批卡片）。

**章末的 `---`**：`_NOTES_TASK` 第 6 条要求章间写分割线，切卡后章节边界由卡片间距承担，那条 `---` 挂在卡底只是噪音 ⇒ `split_by_h2` 的 `_trim_body` 在**切分器里**剥（前后端都要剥，放切分器避免同源漂移）；章节**内部**的分隔线（在分隔话题）保留。


### 语义标记跟随（`limo-block`，2026-10-10）

产物正文里的**协议元素**（模型写的 `<!-- limo-block:v1 type="…" -->` … `<!-- /limo-block -->`，
块内是普通 Markdown）在三条后端路径上的处理。完整协议与降级规则见
[CHANGELOG 2026-10-10](CHANGELOG.md)。

**⚠️ 与骨架轴正交** —— `paper` 下强调块在正文流，`card` 下在**卡内**（轻量强调块的形态刻意
不是卡片 ⇒ 无「卡中卡」）。⚠️ **顺序是 Markdown 层的：先切卡、再在每节内解析标记**（跨节标记
是异常，在降级里自然消解；反向顺序会让块跨节）。

**参数**：`marker_decor`（默认**开**）。⚠️ 语义是「**隐藏装饰**不是隐藏标记」—— 关掉后在
`<html>` 上写 `data-marker-decor="off"`，`blocks.css` 的归零规则命中（**DOM 一字不动、只是
CSS 归零**），块内内容照常输出。前端同样「关掉才发」（`p.set('marker_decor', '0')`）。

⚠️ **内容卡片族（`table` / `code` / `image`）的归零 = 「还原成没包过围栏的样子」** ——
`blocks.css` 末尾那组规则把边框 / 底 / 内边距 / 居中全部撤掉，**只保留 `margin: 16px 0`**
（恰好等于预览侧 `.md-table-wrap` 的 `my-4`，故「关掉装饰」与「从未包过围栏」的间距一致）。
这正是用户要的「保留原来没有卡片时的显示形式」。

| 路径 | 动作 |
|---|---|
| HTML / PDF | **转换**：块 → `<div class="md-block" data-block-type="…">` |
| Word | **转换**：块 → 单单元格表格（左边框 + 淡底） |
| 思维导图 | 显式 `strip_blocks`（替换掉对通用 `<[^>]+>` 规则的隐式依赖） |
| md / 纯文本 | **剥离**标记行（客户端 `stripMarkers` / 后端 `_strip_blocks`） |

**HTML 侧**：分段与包裹收口在 [artifact_blocks.py](../backend/app/core/artifact_blocks.py) 的
`render_blocks_html(md, render_fragment)` —— `export.py` 只提供**本侧的渲染回调**（KaTeX 保护链），
Word 侧提供自己的（无代码高亮）。⚠️ **这不是过度抽象**：两个消费点各写一遍消费循环就是模式 #54
点名的「同一份判据两处实现」。

- ⚠️ **无标记时 `render_blocks_html` 逐字返回 `render_fragment(md)`**（一个字符都不动）—— 这是
  「无标记产物的导出 HTML 与改造前逐字节相同」那条断言的机制所在。
- ⚠️ **空段必须丢掉**：它转成 HTML 是空串，但更糟的是在拼接结果里留下一个空行。
- CSS 拼接顺序：`theme_css` → **`_CARD_TOKENS_CSS`（闸门见下）+ `_load_card_css()` / `_load_block_css()`** → pygments → badge → katex → `_PRINT_CSS`（垫底）。

⚠️ **`_CARD_TOKENS_CSS` 的注入闸门是 `skeleton == "card" or blocks_present`** —— 该常量是导出页
**唯一的** `--tk-*` 提供者（`HTML_THEMES` 一个都不提供），而 `blocks.css` 在 `paper` 下也要生效。
**闸门漏掉 `blocks_present` 的表现是「强调块一点样式都没有，而应用内预览完全正常、零报错」**
（本项目点名的「断言全过、肉眼等于没有」）。⚠️ 反向的闸门同样重要：**无标记 + `paper` 不注入**，
否则「逐字节零回归」这条断言再也写不出来。

**`_load_block_css()`** —— 与 `_load_card_css()` 同款纪律（单一真相源在前端
[frontend/src/styles/blocks.css](../frontend/src/styles/blocks.css)、从磁盘读、不缓存、读不到抛
`AppError("EXPORT_BLOCK_CSS_MISSING", recoverable=False)`）。⚠️ **刻意写成第二个函数而非给前者加
参数**：仓库路径、打包路径（spec 的 datas 是两行）、错误码、缺失后果各自独立 —— 参数化会把
「哪一份缺失」压成同一个错误，排查时得靠猜。

**Word 侧**：识别 `<div class="md-block">` → 单单元格表格 + 底纹 `FILL_MARKER` + 左边框
`BAR_MARKER`。⚠️ **刻意不复用 `FILL_CARD` + `Table Grid`** —— 那样 `card` 骨架下的「卡内标记块」
会看起来像**一张卡里套着一张卡**。⚠️ **九种 type 在 Word 里都是同一个形态**（单单元格表格），
只有配置色不同 —— 其中内容卡片族取**中性色**（不是语义色，染色等于替它加一层它没有的意思）。
⚠️ **`table` 卡在这里是「表格套表格」**（外层单单元格 + 内层真表格），`code` / `image` 只有外框、
没有工具条 / 居中表达 —— 这是**能力边界不是实现省略**（想复刻预览形态 = 让 Python 复刻 React
的结构，模式 #54）。已写成断言钉在 `test_export_blocks.py` 里。⚠️ **顺带修掉一个既有泄漏缺陷**：`BeautifulSoup` 把
`<!-- … -->` 解析成 `Comment`（`NavigableString` 子类、**不是 `Tag`**）→ 走「非 Tag 即文本」
兜底 → 标记被排成**可见段落**。回归守卫 `test_word_标记行不出现在文档正文里`。

**跨语言同源**：与 H2 切分同款 —— 前后端各有一份实现（JS `lib/markdown-markers.ts` / Python
`artifact_blocks.py`），靠共享 fixture
[frontend/src/test/fixtures/limo-blocks.json](../frontend/src/test/fixtures/limo-blocks.json) 钉住。

**前端接线守卫**：[exportMarkers.test.ts](../frontend/src/test/exportMarkers.test.ts) 把
属性名/取值（`data-marker-decor="off"`）、query 参数名与取值、两个注入闸门、以及三处前端接线
（`ExportMenu` 的 `stripMarkers` 与 `p.set`、`ExportPanel` 的 body 字段）逐条钉住 —— 这类
「加了开关但没接线」的失败**零报错**，只有对照实机才看得出来。

**⚠️ 不跟随的**：与卡片同理，`/export/{id}/all` 只打包原始产物。


### 设计意图

| 特性 | ExportMenu（快捷导出） | ExportPanel（详细导出） |
|------|----------------------|------------------------|
| 入口 | PreviewArea 工具栏下拉 | 顶部 Download 按钮 → 侧边滑出面板 |
| 文档数 | 始终是当前预览的 1 篇 | 可选多篇 |
| 内容选择 | 自动跟随当前 tab | 用户手动勾选 tab |
| 格式/主题 | 固定（MD 原文 / HTML limo 主题 / Word） | 可选 MD/HTML/Word/纯文本 + 4 主题 + 图片/公式策略 |
| 数据来源 | 客户端 store（MD/复制）+ 后端（HTML/ZIP） | 全部走后端 |
| 适用场景 | "我在看什么就导出什么" | "我要精确控制导出内容和格式" |

**关键原则**：用户在预览栏看到什么，导出就应该得到什么。禁止导出与预览不一致的内容。

---

## 2. 内容源映射表

这是导出系统最核心的参考表。所有导出路径（前端 ExportMenu / 后端 export_content / 后端 export_custom）都必须与此表保持一致。

### 2.1 文本/视频/网页类文档

Tabs: `summary`, `polished`, `transcript`, `mindmap`

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| summary | MarkdownPreview(currentContent) | currentContent | notes | `get_markdowns_by_transcript(mode="notes")` |
| polished | MarkdownPreview(currentPolishedContent) | currentPolishedContent | polished | `get_markdowns_by_transcript(mode="polished")` |
| transcript | TranscriptView(currentRawText) | currentRawText | transcript | `tx["rawText"]`，JSON 格式解析为文本 |
| mindmap | MindmapView(currentContent) | currentContent | — (专用端点) | 笔记 markdown → `_build_mindmap_html()` |

### 2.2 Excel 文档

Tabs: `data_preview`, `report`, `summary`

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| data_preview | TranscriptView(currentRawText) | currentRawText | transcript | `tx["rawText"]`（原始数据，不解析 JSON） |
| report | MarkdownPreview(currentPolishedContent) | currentPolishedContent | polished | `get_markdowns_by_transcript(mode="polished")` |
| summary | MarkdownPreview(currentContent) | currentContent | notes | `get_markdowns_by_transcript(mode!="polished")` |

### 2.3 EPUB 文档

Tabs: `summary`, `polished`, `transcript`, `mindmap`（同文本类——skeleton 已随骨架面板移除，epub 一键解读产物走 summary/polished）

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| 同文本类 | — | — | — | — |

### 2.4 图片文档

Tabs: `original`, `extract`, `table`, `digitize`, `understand`

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| original | `<img>` 原图 | — (无文本) | — | 不支持导出 |
| extract | Markdown(imageResults[id].extract.text) | imageResults | extract | `get_process_results → mode="extract"` |
| table | 同上 | imageResults | table | `get_process_results → mode="table"` |
| digitize | 同上 | imageResults | digitize | `get_process_results → mode="digitize"` |
| understand | 同上 | imageResults | understand | `get_process_results → mode="understand"` |

**关键差异**：图片文档没有 Markdown 条目（`markdown/` 目录下无文件），处理结果存储在 `process/` 目录中。因此：
- **MD 导出**：客户端直读 `imageResults` store，不经过后端 ✅
- **HTML 导出**：后端先查 markdown → 未找到 → 回退到 `get_process_results()` ✅
- **自定义导出**：后端 `export_custom` 中未知 tab 统一走 `get_process_results()` 查找 ✅

### 2.5 合成文档（composite，2026-10-01）

Tabs: `summary`, `transcript`（**恰好两个**）

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| summary | MarkdownPreview(currentContent)（= 对比综述） | currentContent | notes | `get_markdowns_by_transcript(mode="notes")` |
| transcript | TranscriptView(currentRawText)（= 合成正文 + `## 来源文档` 清单） | currentRawText | transcript | `tx["rawText"]` |

**没有 polished**：它是 N 篇的综述，**没有「原文」可整理**——后端 `build_both()` 对 composite 返回 `(sp, sp)`，前端 `getTypeMeta` 也不给入口，两层一致。

⚠️ **tab 注册是导出链路最容易漏的一环**：`sourceTypes` 不含新类型 → 预览区**一个 tab 都没有**（静默空屏，无报错），导出侧也就无从取材。故 composite **只**加在 `summary` 与 `transcript` 两处，**绝不可加进 `DOC_TYPES`**（它被 `ALL_TEXT` 展开，会把 polished / mindmap 一并送出）；`summary` 的 `defaultFor` 也要加（`forSourceType` 与 `defaultFor` 是两件事）。详见 CHANGELOG 2026-10-01。

### 2.6 前端 Tab 注册表 + 内容源（2026-08-14 重写）

旧的 `TAB_EXPORT` 集中表已移除，重构为**注册表 + getter 绑定**两层（替代散落在 5 处的 switch/case / 映射表）：

1. **`lib/tabRegistry.ts`** — 纯数据元注册：`TabRegistry.register(TabDef)` 一处注册，零改动其他文件。`TabDef` 含 `contentSource`/`htmlMode`/`exportSuffix`/`exportLabel` 导出相关字段：

```typescript
export type ContentSource = 'notes' | 'polished' | 'rawText' | 'imageResult' | 'none'

TabRegistry.register({
  key: 'summary', label: '笔记',
  sourceTypes: ['video', 'webpage', 'text', 'link', 'pdf', 'docx', 'pptx', 'document', 'epub', 'xlsx', 'composite'],
  priority: 10,
  contentSource: 'notes', htmlMode: 'notes', exportSuffix: '', exportLabel: '笔记',
  isStandardMarkdown: true, showTOC: true, showVersionSwitcher: 'notes',
  processingKinds: ['import', 'retranscribe', 'generate'],
})
// 已注册 11 个内置 tab（summary/polished/transcript/data_preview/mindmap
//   /original/extract/table/digitize/understand/ocr；skeleton 已随骨架面板移除）
```

2. **`lib/contentSources.ts`** — 内容取值绑定：`CONTENT_GETTERS: Record<ContentSource, getter>` 用 **Record 类型约束强制补 getter**（新增 contentSource 枚举 → 编译期缺一即错）。三处消费统一走 `getContentForView(view, ctx)`：PreviewArea `displayContent` / CopyContentBtn / ExportMenu：

```typescript
export const CONTENT_GETTERS: Record<ContentSource, (ctx) => string | null> = {
  notes:      (ctx) => ctx.readStreamBuffer?.('notes') ?? ctx.currentContent,
  polished:   (ctx) => ctx.readStreamBuffer?.('polished') ?? ctx.currentPolishedContent,
  rawText:    (ctx) => ctx.currentRawText,
  imageResult:(ctx) => ctx.imageResults[ctx.currentDocId || '']?.[ctx.activeView]?.text || null,
  none:       () => null,
}
// ⚠️ readStreamBuffer 必须返回 null（非 ''）表示无缓冲——用 `??` 回退已保存内容
```

**字段说明**：
- `contentSource`：决定内容从哪个字段读（`contentKey` 的替代，getter 化）。`'none'` = 无内容可导出（原图/OCR tab）—— ⚠️ **导出面板的 tab 清单据此滤掉它们**（`gen-panel/constants.ts::getTypeMeta`，2026-10-11）：判据仍只有这一个字段，前端不另存黑名单
- `htmlMode`：传给后端 `GET /export/{id}?mode=` 的值；`'__mindmap__'` = 走 `/export/{id}/mindmap` 专用端点；空字符串 = HTML 导出不可用
- `exportSuffix` / `exportLabel`：文件名后缀 / 人类可读标签
- `visible` 回调：处理动态显隐（如 mindmap 需已有 notes）

### 2.6 无产物处理

当用户尚未生成对应产物时（如刚导入还没点"生成笔记"），导出系统必须优雅降级：

| 场景 | ExportMenu 行为 | ExportPanel 行为 |
|------|----------------|-----------------|
| 无笔记（summary tab） | 按钮 disabled，tooltip 显示"尚未生成笔记" | 勾选 summary tab → 后端查不到内容 → 跳过，不写入 ZIP |
| 无整理版（polished tab） | 按钮 disabled | 同上 |
| 图片未处理（extract 等） | 按钮 disabled | 同上 |
| 图片 original / ocr tab | 按钮 disabled（无文本内容可导出） | **不出现**在勾选项里（前端按 `contentSource === 'none'` 过滤，2026-10-11；此前是「可选但后端跳过」——选项在、勾得动，导出后多出一个名为「原图」而内容是 rawText 的幻影文件） |

**后端无内容时**：`export_custom` 检查 `if content and content.strip():` 后才写入 ZIP。`export_content` 返回 404。

---

## 3. 核心转换函数 `markdown_to_html()`

**实现位置**：`backend/app/services/markdown_render.py`（2026-09-21 从 `routers/export.py` 下沉）。

下沉原因：Word 导出（`services/docx_export.py`）需要**同一份 markdown 理解**，而 services 层不能反向 import routers。现在是「一份解析器，两个消费者」：

```
markdown 文本 ──preprocess_gfm()──┬── markdown_to_html(codehilite=True)  → HTML 导出（Pygments 逐 token 上色）
                                  └── BeautifulSoup 解析 → docx_export  → Word 导出
```

```python
def markdown_to_html(md: str, codehilite: bool = True) -> str:
    md = preprocess_gfm(md)  # 删除线、任务列表、时间戳 badge
    extensions = [
        "tables", "fenced_code", "sane_lists", "smarty",
        "md_in_html", "def_list", "abbr",
    ]
    if codehilite and PYGMENTS_OK:
        extensions.append("codehilite")
    import markdown
    return markdown.markdown(md, extensions=extensions)
```

`routers/export.py` 侧只剩薄包装 `_markdown_to_html(md) = markdown_to_html(md, codehilite=True)`。

> **抽取正确性验证（2026-09-21）**：从 git 取旧版 `export.py` 独立加载，对 8 份样本逐一比对 HTML 输出，差异 0 处 —— 不是靠推理得出的「应该没变」。
>
> `codehilite=False` 分支目前无调用方（Word 导出直接解析 HTML 的 `<pre><code>` 结构，不需要 Pygments 的逐 token `<span>`），保留它只是为了**参数语义完整**：将来若有第三个消费者不需要高亮，不必再动解析器。

### 扩展说明

| 扩展 | 作用 | 备注 |
|------|------|------|
| `tables` | GFM 表格 `\|...\|` | ✅ |
| `fenced_code` | 围栏代码块 ` ``` ` | 生成 `<pre><code class="language-xxx">` |
| `codehilite` | Pygments 语法高亮 | 可选，需 `pygments` 包 |
| `sane_lists` | 列表缩进按 GFM 规范 | ✅ |
| `smarty` | 智能引号 `"..."` → `"..."` | ✅ |
| `md_in_html` | HTML 块内 Markdown | ✅ |
| `def_list` | 定义列表 `term\n: def` | ✅ |
| `abbr` | 缩写 `*[HTML]: ...` | ✅ |

### 刻意移除的扩展

| 扩展 | 原因 |
|------|------|
| `nl2br` | 单行换行变 `<br>` 与 CommonMark/react-markdown 行为不一致，导出和预览排版差异巨大 |

---

## 4. GFM 缺失功能预处理

Python `markdown` 库不原生支持 GFM 删除线和任务列表。在转换前用正则预处理：

```python
def _preprocess_gfm(md: str) -> str:
    # 表格前补空行 — Python markdown tables 扩展要求表格前有空行，
    # 但 GFM/react-markdown 允许紧跟前导文字。LLM 常生成 "文字\n|表头|\n|---|" 格式
    md = re.sub(r'(^[^\n|][^\n]*)\n(\|[^\n]+\|[ \t]*\n\|[-: |]+\|)',
               r'\1\n\n\2', md, flags=re.MULTILINE)
    # ~~text~~ → <del>text</del>
    md = re.sub(r'~~(.+?)~~', r'<del>\1</del>', md)
    # 任务列表 → 静态 checkbox（disabled 不可交互，视觉对齐前端预览）
    md = re.sub(r'^- \[ \] (.+)$',
                r'- <input type="checkbox" disabled style="margin-right:6px;vertical-align:middle"> \1',
                md, flags=re.MULTILINE)
    md = re.sub(r'^- \[x\] (.+)$',
                r'- <input type="checkbox" checked disabled style="margin-right:6px;vertical-align:middle"> \1',
                md, flags=re.MULTILINE)
    # 时间戳 badge — Content-[mm:ss] → 蓝, Screenshot-[mm:ss] → 橙（对齐前端 ts-badge）
    md = re.sub(r'Content-\[(\d{2}:\d{2})\]',
                r'<span class="ts-badge ts-content">▶ \1</span>', md)
    md = re.sub(r'Screenshot-\[(\d{2}:\d{2})\]',
                r'<span class="ts-badge ts-screenshot">📷 \1</span>', md)
    return md
```

**说明**：
- 任务列表用 `<input type="checkbox" disabled>` 而非 Unicode ☐/☑——静态 HTML 无法交互，`disabled` 保证视觉对齐预览（checkbox 可勾选）但不响应点击
- 时间戳 badge 依赖 `_get_badge_css()` 注入的 CSS（Content 蓝 `#007aff` / Screenshot 橙 `#ff9500`），由 `_markdown_to_html` 统一调用

---

## 5. 代码高亮（Pygments）

### 依赖检测

```python
try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, guess_lexer
    _PYGMENTS_OK = True
except ImportError:
    _PYGMENTS_OK = False
```

### CSS 注入

Pygments CSS 通过 `_get_pygments_css()` 动态生成，追加到主题 CSS 末尾。语言标签通过 `_add_lang_labels()` 从 `<code class="language-xxx">` 提取生成。

### PyInstaller 打包

`LiMoBackend.spec` 中必须包含：
```python
hiddenimports += ['pygments', 'pygments.lexers', 'pygments.formatters', 'pygments.styles']
```

---

## 6. KaTeX 公式处理

### 保护/还原机制

Markdown→HTML 转换前保护公式为占位符，防止 `markdown` 库破坏 LaTeX 语法：

```python
md, placeholders = _protect_latex(md)
html = markdown.markdown(md, extensions=...)
html = _restore_latex(html, placeholders)
```

### 服务端预渲染

`_render_formulas_katex()` 调用 Node.js 脚本预渲染 `$$...$$` / `$...$` → HTML。Node.js 不可用时静默回退，公式保留原样。

### 资源加载（`_get_katex_assets`）

双路径搜索，**完全自包含零 CDN**：
1. `frontend/node_modules/katex/dist/`（开发环境优先，取最新版）
2. `backend/static/katex/`（打包环境回退，PyInstaller `datas` 打进 `_internal/`）

加载后处理：
- CSS 中 `@font-face` 的 `url(fonts/xxx.woff2)` 全部替换为 base64 data URI（60 个字体）——`_inline_font` 按 MIME 内联
- JS 拼接 `contrib/auto-render.min.js`
- 首次加载后内存缓存 `_KATEX_CACHE`，后续导出零磁盘 IO

### 客户端回退

打包版无 Node.js → `_render_formulas_katex()` 先 `shutil.which("node")` 预检，不存在则静默回退客户端渲染（嵌入 auto-render 脚本）。开发环境有 Node 时服务端预渲染优先。

### 已知陷阱

1. **行内 `$` 保护**：使用 `(?<!\d)\$(?=\S)(.+?)(?<=\S)\$(?!\d)`，遵循 TeX 惯例（`$` 前后不邻数字或空白），防止误匹配 `$5 and $10` 等货币金额。
2. **`$$` 块跨行**：`re.DOTALL` 标志允许跨行匹配。
3. **`_remove_formulas`**：使用 `[\s\S]*?` 跨行匹配，与 `_protect_latex` 保持一致。

### ⚠️ 未修复：「公式：渲染为图片」选项自上线起从未生效（2026-09-21 发现）

`_render_formulas_to_images()`（`routers/export.py`）依赖 **matplotlib，而 matplotlib 不在 `pyproject.toml`、不在 PyInstaller spec、不在任何文档里**（全仓 grep 只有这一处引用）。它永远走 `except ImportError: return md` 静默返回原文 —— ExportPanel 的「公式：渲染为图片」**选了等于没选**。

**未在本轮修复的理由**：修它要给打包版加 matplotlib（~50MB+），而合理修法是**切到已有的 KaTeX 图片管线并删掉 matplotlib 分支**，属独立议题（已记入 CLAUDE.md 待办「md 公式渲染修复」）。

**Word 导出的关系**：Word 分支不接 `formula_md` 字段，恒保留 LaTeX 原文 —— 即这个失效选项对 Word 导出**零影响**（它本来就没接）。

---

## 7. HTML 主题

4 个内置主题，CSS 硬编码在 `HTML_THEMES` 字典中：

| 主题 | 定位 | h1 颜色 | 字体 |
|------|------|---------|------|
| `github` | 通用 | `#1C1C1E` | system-ui |
| `paper` | 学术 | `#111` | Times/宋体 |
| `minimal` | 极简 | `#000` | system-ui |
| **`limo`** | **默认** | `#1E3A5F` | system-ui + 蓝色系 |

**limo 主题颜色精确匹配前端 `--md-*` CSS 变量**。已支持暗色模式（`@media (prefers-color-scheme: dark)`），其他 3 个主题暂未适配。

---

## 8. 思维导图导出

### 端点：`GET /export/{id}/mindmap`

调用 `_build_mindmap_html(md, title)` 生成自包含 HTML。内容源优先取笔记 markdown（非 polished），回退到 rawText。

### 资源内联策略

d3.js、markmap-view、markmap-lib 三个库优先从本地 `node_modules/` / `static/markmap/` 读取并内联为 `<script>...</script>`，不可用时 CDN 回退。**注意**：内联路径必须用 `<script>` 标签包裹，CDN 回退路径自带 `src` 属性。2026-07-10 修复了内联 JS 裸放（无 `<script>` 包裹）导致浏览器不执行的问题。

### Markdown 清洗顺序

与前端 `MindmapView.tsx` 对齐：代码块 → 图片 → BibTeX → 锚点链接 → HTML 标签。

### 前端差异

前端无锚点链接清洗步骤（锚点保留），清洗顺序为 代码块→HTML标签→图片→BibTeX。在极端边界情况下可能产生不同结果。

---

## 9. 自定义导出面板（ZIP）

### 端点：`POST /api/export/custom`

```json
{
  "doc_ids": ["..."],
  "formats": ["md", "html", "docx", "plain"],
  "content_tabs": ["summary", "polished", "transcript", "mindmap"],
  "html_theme": "limo",
  "formula_md": "keep",
  "image_md": "separate",
  "formula_plain": "remove",
  "filename": "LiMo_export_2026-07-10"
}
```

### 图片处理模式

- `separate`：图片放 `images/` 子目录，Markdown 路径重写为 `images/xxx.png`
- `inline`：base64 data URL 内嵌
- `none`：不包含图片

### mindmap 特殊处理

mindmap tab 始终输出为完整 `.html` 文件（跳过 markdown→HTML 转换和格式选择），因为思维导图无法用纯文本/Markdown 表示。

### 格式循环有两处

`export_custom`（SSE 流）与 `_build_export_zip`（打包）**各有一份 `for fmt in formats` 循环**。新增格式时两处都要加分支 —— 漏一处则 ZIP 导出静默少文件（不报错，只是文件不出现）。Word 分支用的是**原始 `content` 而非 `processed`**：`image_md=inline` 已把图片换成 base64 data URI，此时再走图片收集会得到空字典。

### 判据修复（2026-10-11 审查）

一轮针对三条入口（`POST /export/custom`、`POST /export/batch`、`GET /export/{id}/all`）的审查，六条：

1. **`_resolve_tab_content` 不再回退 `rawText`**（`export.py`）—— 图片处理类 tab（`extract` / `table` / `digitize` / `understand`）在原文档没有对应处理结果时返回**空串**（调用方据此不产文件）。成因：前端发来的 `content_tabs` 是**各类型的并集**（见 §2.6），一旦回退 `rawText`，一份网页文档会凭空多出「原图」「OCR」「提取文字」三份内容全同的幻影文件。
2. **空结果显式 404**（`EXPORT_NO_CONTENT`）—— `export_batch` 与 `_build_export_zip` 在一个文件都没产出时不再返回**空 zip**（用户拿到一个 0 字节的包，看起来像成功）。
3. **批量导出 md 剥标记行** —— `_collect_doc_products` 与 `_build_export_zip` 的 md 分支走 `strip_blocks`，与单文件导出同源（此前只有单文件那条路剥）。
4. **批量 md 走 `_inline_images`** —— `_build_export_zip` 内嵌时补传 `doc_dir`，使 `assets/xxx` 相对路径也能解析（此前只有单文件导出能解）。
5. **`separate` 模式的落空目录** —— 收集图片的条件补 `any(f != "docx" for f in formats)`：所选格式**只有 docx** 时不再单独建 `images/`（docx 自带图片内嵌，那份图没人引用）。
6. **前端导出 tab 清单滤掉 `contentSource === 'none'`**（`gen-panel/constants.ts`）—— 「原图」「OCR」两个 tab 自己声明了没有可导出的内容源，不过滤则选项在、勾得动，导出后多出幻影文件。**与第 1 条是同一件事的两端**：判据仍只有 `TabDef.contentSource` 一份。

---

## 10. MD 导出

端点：`GET /export/{id}?format=md&mode=&version=`（`version` 0/省略 = 最新版，见「按版本导出」）

直接返回原始 Markdown 文本（`image_md=link`，默认），图片路径重写为 `/api/files/...` 绝对 URL。

`image_md=inline`（2026-10-11 起为快捷「下载 MD」的默认，见「快捷 MD 的图片内嵌」）时把图片 base64 内嵌、引用改成 data URI，单文件自包含 —— 离线/分享场景也能看。此时不再重写为绝对 URL（两者互斥）；查不到的图与越界路径退回绝对 URL，不静默丢引用。

---

## 11. Word 导出（.docx）

端点：`GET /export/{id}?format=docx&mode=&version=`（单文件，`version` 见「按版本导出」）／ `POST /export/custom` 的 `formats: ["docx"]`（批量 ZIP，导出全部版本）

**实现**：`backend/app/services/docx_export.py` —— `markdown_to_docx(md, title, *, images, resolver) -> bytes`

### 为什么是两跳（Markdown → HTML → docx）

不引入第二套 Markdown 解析器。LiMo 的 Markdown 是**方言**：表格前须补空行、`~~` 删除线、`- [ ]` 任务列表 checkbox、`Content-[mm:ss]` 时间戳 badge、`\[...\]` 公式分隔符 —— 这些全靠 `preprocess_gfm()` 处理。若 Word 导出另起一套解析，方言支持会分叉成两份需要同步维护的实现。

```
md ──preprocess_gfm()── markdown 库 ──┬─→ HTML 导出
                                      └─→ BeautifulSoup 树 → OOXML 段落/运行 → .docx
```

代价：HTML 中间层丢失了部分源信息（如围栏代码块的语言标签位置）。实际影响为零 —— 语言标签本就由 `_add_lang_labels` 在 HTML 上后置生成，Word 侧从 `<code class="language-xxx">` 直接取类名，反而更直接。

### 元素映射表

| Markdown | docx 实现 | 备注 |
|----------|----------|------|
| `#`~`######` 标题 | `Heading 1`~`Heading 6` 样式 | 用内置样式而非手写字号 → **Word 导航窗格可直接跳转**，用户可插入自动目录 |
| 段落 | 正文段落，段后 6pt | 中文字体 `w:eastAsia` 槽位单独设「微软雅黑」，否则掉回宋体 |
| `**粗**` / `*斜*` / `~~删~~` | run 级 bold / italic / strike | 嵌套样式用 `_InlineStyle` 栈传递（`**粗 *斜* **`） |
| `` `行内代码` `` | Consolas + `F2F3F5` 底纹 | 底纹需手写 `w:shd`（python-docx 无 API） |
| 围栏代码块 | 单单元格表格（浅底纹 + 细边框） | 用表格而非段落 → 长代码自动换行不溢出页面 |
| 有序/无序列表 | `List Number` / `List Bullet` 样式，按嵌套层级 | 层级由 `_render_list` 递归深度决定 |
| 任务列表 `- [x]` | `☑` / `☐` 字符 | **必须用 `_SingleChild` 包住每个子节点** —— 见下方陷阱 |
| 引用块 `>` | 左缩进 + `pBdr` 左边框 + `F7F8FA` 底纹 | 均需手写（python-docx 无边框/底纹 API） |
| 表格 | docx 表格 + `Table Grid` 样式 | 首行加粗 |
| 分隔线 `---` | 段落底边框 | |
| 链接 | 真超链接（`w:hyperlink` + 外部关系） | python-docx 无超链接 API（issue #74 挂起至今） |
| 图片 | 按内容宽 `6.27in` 等比缩放内嵌 | 缺失图留 `[图片：alt]` 占位，**不吞后续正文** |
| 时间戳 badge | Content 蓝 `#1A73E8` / Screenshot 橙 `#EB6C36` | 从 HTML 的 `class="ts-badge ts-content"` 逐类名查表 |
| 公式 `$$...$$` | **保留 LaTeX 原文文本** | 见下方局限 |

### 实现陷阱（都只有 Word 能看见）

**① OOXML `w:pPr` / `w:rPr` 是有序序列，不是集合。** 元素必须按 schema 顺序排列，否则 Word 报「文件已损坏」并**静默丢弃该段格式**——而 python-docx 照常读回，测试全绿。

```
w:pPr: … pStyle · pBdr · shd · tabs · spacing · ind · jc …
                     ↑ 底纹必须在间距/缩进之前
w:rPr: … rFonts · color · sz · u · shd …
```

python-docx 未暴露 `_insert_shd` / `_insert_pBdr`，`append()` 一定插在末尾（错误位置）。`docx_export._ordered_insert()` 按显式顺序表插入；`test_docx_export.py` 的「OOXML 顺序」节遍历全部 `w:pPr`/`w:rPr` 子元素做下标单调校验。

> **该检查自证过**：测试里先用 `paragraph_format.space_after` 生成 `w:spacing`（下标 21），再 `append` 一个 `w:shd`（下标 9）——这正是修复前 `_shade` 的真实写法——确认检查确实抛错。「检查存在」不等于「检查有效」（CLAUDE.md 第 38 条）。

**② 中文字体有两个槽位。** `run.font.name = "Calibri"` 只写 `w:ascii` / `w:hAnsi`；中文走 `w:eastAsia`，不显式设置则 Word 用默认东亚字体（宋体）渲染汉字。`_set_run_font()` 三处都写。

**③ void 元素陷阱。** `_render_list` 遍历 `<li>` 子节点时，任务列表的 `<input>` 是 void 元素（无 children）。若用「拿标签当容器遍历其子节点」的辅助函数包装它，遍历结果为空 → `☑`/`☐` 静默消失。必须用 `_SingleChild(node)` 显式包一层。

### 已知局限

| 局限 | 原因 | 影响 |
|------|------|------|
| 公式保留 LaTeX 原文 | Word 的 OMML 是另一套完整 DSL，转换器体量远超本次范围 | 用户可在 Word 内用自带公式编辑器重录，或改用 HTML/PDF 导出看渲染结果 |
| 无自动目录域 | `TOC` 是需要 Word 打开时刷新的域字段（fldChar 三段式），当前产物打开即为最终态 | 但标题已用内置 `Heading N` 样式 → 用户「引用 → 目录」一键生成 |
| mindmap tab 不能导出 Word | 思维导图是 SVG 交互图，无 docx 对应表示 | ExportMenu 中 Word 项对 mindmap tab 禁用（`htmlMode === '__mindmap__'`） |
| 主题轴不生效 | 六轴主题是 CSS 变量驱动的 Web 概念，docx 无对应抽象 | Word 用固定的 ink/muted 配色（与 `limo` 主题同源色值） |

### 与 HTML 导出的取舍差异

| | HTML | Word |
|---|------|------|
| 代码高亮 | Pygments 逐 token 上色 | 不做（整块等宽 + 底纹）—— 需保真用 HTML |
| 图片 | base64 内联，完全自包含 | 内嵌进 docx 包（docx 本身就是 zip，同样自包含） |
| 公式 | KaTeX 预渲染 + 字体内联 | LaTeX 原文 |
| 二次编辑 | 需 HTML 编辑器 | **Word 直接可改**（这是 Word 导出的核心价值） |

---

## 12. PDF 导出（2026-09-21）

**没有独立的 PDF 端点**，也没有独立的 PDF 模板。喂给打印引擎的，就是 HTML 导出的**同一份自包含 HTML**（`/export/{id}?format=html&mode=…`，自带 `_PRINT_CSS`）—— 于是「预览 / HTML 导出 / PDF 导出」三者同源，只维护一套打印 CSS。

两条落地路径，**内容完全相同，只差最后一步**：

| 环境 | 路径 | 落盘方式 |
|------|------|---------|
| **Tauri 桌面版** | `exportPdfFromUrl()` → Rust `export_pdf` → WebView2 `PrintToPdf` | **直接写文件**，无对话框，存到系统「下载」目录 |
| **浏览器** | `printFromUrl()` → 屏幕外 iframe → `contentWindow.print()` | 用户打印对话框里选「另存为 PDF」 |

```
ExportMenu「导出 PDF」
   → [Tauri] invoke('export_pdf', { url, fileName })
        → Rust：屏幕外窗口加载该 HTML → 等 load → ICoreWebView2_7::PrintToPdf → 关闭窗口 → 返回路径
   → [浏览器] fetch → <iframe srcdoc>（屏幕外 1024×1400）→ 等 img + 300ms → contentWindow.print()
```

实现见 [frontend/src/lib/print.ts](../frontend/src/lib/print.ts)（前端双路径）与 [frontend/src-tauri/src/lib.rs](../frontend/src-tauri/src/lib.rs) 的 `pdf_export` 模块（Rust 侧）。

### 为什么 PrintToPdf 必须新开一个窗口

`ICoreWebView2::PrintToPdf` **只能打印该 WebView2 实例的主文档** —— 而主窗口的主文档是应用 UI 本身，不是导出内容。iframe 也不行：`ICoreWebView2Frame2` 只有 `PrintAsync`（弹打印 UI），**没有**落文件的 PrintToPdf。故：新建屏幕外窗口加载导出 URL，打完即销毁。

⚠️ 窗口用 `position(-32000, -32000)` 挪出屏幕，**而不是 `visible(false)`** —— WebView2 对不可见窗口会推迟渲染，PrintToPdf 有概率拿到空白页。装饰与任务栏按钮一并关掉，用户看不到但渲染照常。

⚠️ `on_page_load` 回调跑在 **UI 线程**，就地调 `with_webview` 有重入事件循环的风险，故换线程 + 延迟再抓 controller。延迟取 **3000ms**（实测值，非推导值）：`Finished` 只代表主文档加载完，而导出 HTML 自包含（~1.8MB base64 字体 + 内联图片），字体解码与 KaTeX 客户端渲染都在其后。**没有「更短也够」的成功样本，别调小**；代价是每次导出多等约 3s。

### ⚠️ 加载的 URL 必须带 `inline=1`（否则打印出一张白纸）

这是本路径**最隐蔽的一处**：`/export/{id}?format=html` 默认返回 `Content-Disposition: attachment`（点「HTML」按钮就该下载文件，这个默认是对的）。但 Tauri 路径是让 WebView2 **直接导航**到这个 URL —— attachment 会让它去**下载**而非渲染，窗口全白，PrintToPdf 打印的正是这张白纸，**而回调照样返回 `succeeded=true`**（没有任何报错，用户拿到一个 876 字节的空白 PDF）。

`export_content` 因此加了 `inline` 查询参数（默认 False），[print.ts](../frontend/src/lib/print.ts) 的 `withInline()` 在出口统一追加，**不要绕过它**。
• 浏览器路径（`fetch` + `srcdoc`）**不受影响** —— fetch 在 JS 里拿得到 body，无视该头。
• `file://` 也不查该头 —— 这正是当初把根因收窄到「HTTP 响应头」而非「渲染链路」的对照实验依据。

### PrintSettings 的取值（都不是默认值）

| 项 | 取值 | 为什么 |
|----|------|--------|
| `PageWidth/Height` | 8.2677 × 11.6929 in | **PrintSettings 默认是 Letter**，不设就印出美式尺寸（实测显式设置后为 209.9×297.0mm） |
| 四边 `Margin*` | 与 `_PRINT_CSS` 的 `@page { margin: 18mm 16mm }` **同值** | 两边一致则无论 CSS 与 PrintSettings 谁胜出，纸上都是这个边距 |
| `ShouldPrintBackgrounds` | **true** | 一键导出没有对话框可勾「背景图形」；关掉后代码块只剩一圈边框、引用只剩一条竖线 |
| `ShouldPrintHeaderAndFooter` | false | 不要 URL/日期/页码（与浏览器「另存为 PDF」默认一致） |

### ⚠️ 依赖脆弱点：COM 接口不跨 crate 实例

`pdf_export` 直接用 `webview2-com` 的 COM 类型，其版本**必须与 tauri 内部的同源**（`tauri 2.11` → `wry 0.55` → `webview2-com 0.38` / `windows 0.61`）。版本不同源时 `ICoreWebView2` 会变成两个不相干的类型，`.cast()` 直接编译失败。`Cargo.toml` 里已写死这一约束 —— 升级 tauri 后此处报错，先核对 `Cargo.lock` 再改版本号。

另：`export_pdf` 的落盘路径由 Rust 侧用 `PathResolver::download_dir()` 决定（与其余导出行为一致，同名自动加序号 `(1)`），文件名过 `sanitize_stem()` —— 命令可被任意前端调用，**Rust 侧不能假设调用方守规矩**（`\/:*?"<>|` 混进来就是路径穿越）。

### 打印 CSS 的三处落点（**不可合并**）

| 落点 | 管什么 |
|------|--------|
| `_PRINT_CSS`（`routers/export.py`） | `@page A4 18/16mm`、解除 `max-width`+`margin:auto`、分页控制、代码容器底色 |
| `_get_pygments_css()` 的 `@media print` 段 | **逐 token 配色**换成 Pygments `default`（浅底主题） |
| `HTML_THEMES["limo"]` 的 `@media print` 段 | 把该主题暗色段的浅色取值还原 |

⚠️ **顺序即优先级**：`style_block` 拼接为 主题 → Pygments → badge → KaTeX → `_PRINT_CSS`。媒体查询**不增加特异性**，同特异性下后者胜 —— `_PRINT_CSS` 必须垫底才能压住前面全部屏幕样式。（[test_export_print.py](../backend/tests/test_export_print.py) 把这条隐式契约连同下面两条一起固化了）

### 三条踩过的坑（都是读 PDF 截图发现的）

1. **token 配色压不住**：one-dark 的 token 是 `.codehilite .k` 这类**类选择器**，作用在 `<span>` 上；写 `.codehilite code { color: … }` 只影响没有自身 color 的文本，span 按自己的规则走 → 白底上仍是浅色 token（注释几乎不可见）。**必须整份换浅底主题的 defs**，不能逐条覆盖。
2. **`pre` / `table` 不能加 `break-inside: avoid`**：长度无上界，装不进当页剩余空间时会被整体推到下页，**留下一整页空白**（实测 3 页文档凭空多一页）。让它们自然跨页，只保留行/项级原子性（`li`/`tr`）与短块（`blockquote`/`figure`/`img`）；长表格靠 `thead { display: table-header-group }` 每页重复表头。
3. **`@media (prefers-color-scheme: dark)` 与 `@media print` 不互斥**：系统暗色偏好下两者同时生效，`blockquote` 的 `#AEAEB2`、行内 `code` 的 `#93C5FD` 落在白纸上几乎不可见。`_PRINT_CSS` 的 `!important` 只管了 body/代码容器，**其余由引入暗色段的主题自己还原**（主题自洽）。

### 前端注意

- `canExportPdf = canExportDocx` —— 同门控（都需要 `htmlMode` 且非思维导图）。**思维导图 tab 不开 PDF**：走专用端点，markmap 的打印效果未验证
- **不要绕过 `withInline()`** —— 直接拿 `/export/...` 的 URL 去 `printFromUrl()` / `invoke('export_pdf')` 会打印出一张白纸（原因见上节），且**不会报错**
- Tauri 下成功 toast 报出**完整落盘路径**（用户需要知道文件去哪了）；浏览器下提示「请在对话框中选择另存为 PDF」
- 导出中按钮转 loading —— PrintToPdf 是异步的，大文档要几秒

### 已知局限

| 局限 | 说明 |
|------|------|
| 浏览器下不是真导出 | 无 WebView2 COM 可用，退回打印对话框，需用户手动选「另存为 PDF」 |
| 固定存下载目录 | 不弹「另存为」对话框（要它得引入 `tauri-plugin-dialog`）；同名自动加序号，用户不做选择 |
| 仅 Windows | `PrintToPdf` 是 WebView2 能力；非 Windows 平台该 command 返回「不支持」错误 |

---

## 13. 导出 vs 预览差异速查

| 功能 | 预览（react-markdown） | 导出（Python markdown） | 差距 |
|------|----------------------|------------------------|------|
| 代码高亮 | Prism (oneDark/oneLight) | Pygments (one-dark) | 颜色一致（2026-08-04 对齐） |
| 语言标签 | ✅ `<span>` | ✅ `_add_lang_labels` 生成 | 一致 |
| 复制按钮 | ✅ `<button>` | ❌ | 静态 HTML 限制 |
| 删除线 | ✅ `remark-gfm` | ✅ `_preprocess_gfm` → `<del>` + CSS | 一致 |
| 任务列表 | ✅ `<input checkbox>` | ✅ `<input checkbox disabled>` | 一致（不可交互） |
| 标题锚点 | ✅ `id` + `<Link2>` | ✅ `_add_heading_ids` 生成 `id` | 一致 |
| 时间戳 badge | ✅ `<Play>` 图标（Content 蓝/Shot 橙） | ✅ `.ts-badge ts-content/ts-screenshot` | 一致 |
| KaTeX | remark-math + rehype-katex | Node 预渲染 + 内联 CSS/JS/字体 | 一致（零 CDN） |
| $ 误匹配 | ✅ TeX 惯例 | ✅ 已修复 | 一致 |
| 图片 | apiUrl() 路径 | base64 内联（HTML）/ 保持原样（MD） | HTML 自包含 |
| 暗色模式 | ✅ CSS 变量 + data-theme | ✅ limo 主题支持 | 默认主题支持 |
| 图片缩放 | ✅ react-medium-image-zoom | ❌ | 导出无交互 |
| 章节卡片 | ✅ `data-skeleton="card"` + `CardSections` | ✅ HTML `section.md-card` / Word 单单元格表格 | 一致（8 套视觉方案仅 HTML 生效，Word 是能力边界） |
| 内容块（`limo-block`，9 种） | ✅ `Div` 强调块 / 内容卡片（`MarkerContent`） | ✅ HTML `div.md-block` / Word 单单元格表格（+左边框） | 一致（开关状态经 `marker_decor` 跟随；md/纯文本**剥离**标记行）。⚠️ **内容卡片族（`table`/`code`/`image`）块内一律原样穿过**：HTML 侧 `table` 仍是 `<table>`、`code` 仍是 `codehilite`、`image` 仍是 `<img>`；Word 侧 `table` 是**表格套表格**（能力边界）。⚠️ **卡片外壳只落在 `.md-block` 上，绝不用预览专有 class**（`.md-table-wrap` / `.group` 在导出侧不存在 —— 两侧 DOM 本来就不同，这层差异先于卡片存在） |

> 差距收敛进度：2026-07-10 修图片收集/重写/内联 → 2026-08-04 对齐代码高亮 one-dark + checkbox 任务列表 + 暗色代码块 → 2026-09-21 新增 Word 导出（同一解析器，元素映射见 §11）→ 2026-10-10 卡片骨架跟随（§1「卡片骨架跟随」）+ 语义标记跟随（§1「语义标记跟随」）。当前仅剩复制按钮 / 图片缩放两项导出无法复现（静态 HTML 本质限制）。
>
> **Word 导出与预览的差距**（单独列出，因为差异维度不同）：标题/列表/表格/引用/链接/任务列表/时间戳 badge/图片**全部一致**；代码块**不高亮**（保真请用 HTML）；公式**保留 LaTeX 原文**；主题六轴**不生效**（固定 ink/muted 配色）。

---

## 14. 依赖

| 包 | 用途 | pip 名 | PyInstaller hiddenimport |
|----|------|--------|--------------------------|
| markdown | Markdown→HTML 核心转换 | `markdown` | `markdown` |
| Pygments | 代码语法高亮（可选） | `pygments` | `pygments, pygments.lexers, pygments.formatters, pygments.styles` |
| python-docx | Word 导出（OOXML 组装） | `python-docx` | `docx` |
| beautifulsoup4 | Word 导出（HTML 树遍历） | `beautifulsoup4` | `bs4, soupsieve` |
| KaTeX | 公式渲染 | —（node_modules 读取） | —（仅需前端 node_modules） |

> `beautifulsoup4` 此前是传递依赖（markitdown / trafilatura 带入），2026-09-21 起被 `services/docx_export.py` 直接 import → 在 `pyproject.toml` 提为显式依赖。**直接 import 的包必须显式声明**，否则上游哪天换实现就断链。
>
> ⚠️ PyInstaller 的 `hiddenimports` 用的是 **import 名**而非分发名：`python-docx` → `docx`，`beautifulsoup4` → `bs4`（+ 解析器依赖 `soupsieve`）。spec 中三者原本已随 markitdown 收集，本次只是把新增的两个 app 模块（`app.services.markdown_render` / `app.services.docx_export`）补进列表。

---

## 15. 设计决策记录

### 为什么 MD 导出走客户端而 HTML 导出走后端？

- **MD**：就是原始文本，无需转换。从 store 直读即可，零网络开销。
- **HTML**：需要 KaTeX 预渲染（Node.js 子进程）、Pygments 语法高亮、图片 base64 内联。这些能力只有后端有。
- **Word**：与 HTML 同理走后端（需要 python-docx 组装 zip + 图片内嵌）。

### 为什么 Word 导出复用 HTML 那套 Markdown 解析器？

见 §11「为什么是两跳」。一句话：LiMo 的 Markdown 是方言，两套解析器必然分叉。代价是接受一层 HTML 中间表示。

> 这也是一次**分层倒置的修正**：`markdown_render.py` 原在 `routers/export.py` 里，Word 导出（services 层）要用它，而 services 不能 import routers → 下沉到 services 层。**判断标准**：一个函数若被两个 router 以上、或被 services 直接需要，它就不属于任何单个 router。

### 为什么 ExportMenu 和 ExportPanel 不共享内容映射？

ExportMenu 从客户端 store 读数据（即时），ExportPanel 把 tab 名发给后端从磁盘读（可批量）。两者数据源不同，用同一套映射反而会引入耦合。但两者必须映射到**相同的内容**——这就是本文档第 2 节映射表的作用。前端取值层统一由 `getContentForView(view, ctx)`（`lib/contentSources.ts`）提供，PreviewArea 预览、复制、快捷导出三处消费同一 getter，保证「预览看到什么导出就得到什么」。

### 为什么 mindmap HTML 导出走独立端点？

思维导图需要 markmap 库（d3 + markmap-view + markmap-lib）+ 特定的 HTML 模板结构。与普通的 markdown→HTML 管线完全不同，强行合入会造成大量条件分支。

### 为什么图片文档的 HTML 导出需要后端 process result 回退？

图片文档（sourceType=image）导入后只有 process_results（OCR/提取结果），没有 markdown 条目。`GET /export/{id}?format=html` 原先只查 markdown 表 → 404。修复后增加 `get_process_results()` 回退链：markdown → process results → 404。

---

## 16. 新增导出模式操作指南

### 新增内容 tab

1. **前端 `lib/tabRegistry.ts`**：`TabRegistry.register(TabDef)` 注册新 tab，指定 `contentSource`（→ §2.5 字段说明）、`htmlMode`、`exportSuffix`、`exportLabel`。零改动其他文件
2. **前端 `lib/contentSources.ts`**：若新 tab 的内容源不是已有的 5 个 `ContentSource` 枚举之一，加枚举 + `CONTENT_GETTERS` 补 getter（`Record` 类型约束编译期强制），三处消费（PreviewArea/CopyContentBtn/ExportMenu）自动生效
3. **后端 `export_content`**（`GET /export/{id}`，第 164 行起）：确认 `htmlMode` 走 content 回退链（markdown → process_results → transcript → 404）
4. **后端 `export_custom`**（`POST /api/export/custom`，第 1166 行起）：如需批量/自定义导出，在 `tab_contents` 收集循环加 `elif tab == "xxx":` 分支
5. **后端 `services/markdown_render.py`**：若新内容引入新的 GFM 风格语法，在 `preprocess_gfm` 补对应预处理（删除线/任务列表/时间戳 badge 模式）—— **一处生效，HTML + Word 两条导出路径同时受益**
6. **本文档第 2 节**：更新内容源映射表

### 新增导出格式（以 2026-09-21 的 Word 为例）

一次格式扩展的完整落点清单，可照抄：

| # | 落点 | 说明 |
|---|------|------|
| 1 | `services/<format>_export.py` | 转换核心，纯函数 `markdown_to_x(md, title, ...) -> bytes` |
| 2 | `routers/export.py` `export_content` | `GET /export/{id}?format=xxx` 分支（图片收集 + `Content-Disposition` RFC 5987 编码） |
| 3 | `routers/export.py` `export_custom` + `_build_export_zip` | 两条循环**都要加** `elif fmt == "xxx"` —— 漏一处则 ZIP 导出静默缺文件 |
| 4 | `ExportCustomRequest.formats` 注释 | 更新合法值列表 |
| 5 | 前端 `ExportPanel.tsx` | `type Format` 联合类型 + `FORMAT_META` + 按钮数组 + 摘要行 + 设置块说明 |
| 6 | 前端 `ExportMenu.tsx` | `canExportXxx` 门控 + `handleExportXxx`（注意 mindmap 等特殊 tab 的排除） |
| 7 | `pyproject.toml` | 新增依赖（直接 import 的包必须显式声明，见 §14） |
| 8 | `LiMoBackend.spec` | `hiddenimports` 加新 app 模块；新增第三方包按需加 `collect_all` |
| 9 | `tests/test_<format>_export.py` | **回读产物再断言**（docx/xlsx 是 zip，生成不报错 ≠ 内容正确） |
| 10 | 文档 | 本文档 + CHANGELOG + CLAUDE.md + STATUS.md |

⚠️ **`_collect_images` 的调用方必须传 `mode` 对应的 content**：`image_md=inline` 会把图片换成 base64 data URI，此时再走图片收集会得到空字典。Word 分支用的是**原始 `content`** 而非 `processed`（见 `export_custom` 内的注释）。
