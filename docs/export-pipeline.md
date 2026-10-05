# 导出管线

> 写于 2026-07-10，最后更新 2026-09-21。
> 记录 Markdown→HTML/Word 导出、思维导图、批量 ZIP 的完整管线架构、设计决策、内容映射表和已知局限。

---

## 1. 架构总览：两条导出路径

导出系统提供两条互补路径，各自有不同的设计目标：

```
用户入口                        前端组件                    后端端点
────────                        ────────                    ────────
PreviewArea 工具栏             ExportMenu（下拉菜单）        GET /export/{id}
  ├─ 下载 MD           →      客户端直读 store              （无后端调用）
  ├─ 导出 HTML         →      downloadFromUrl()      →     GET /export/{id}?format=html&mode=...
  ├─ 导出 Word         →      downloadFromUrl()      →     GET /export/{id}?format=docx&mode=...
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
- `contentSource`：决定内容从哪个字段读（`contentKey` 的替代，getter 化）。`'none'` = 无内容可导出（原图/OCR tab）
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
| 图片 original tab | 按钮 disabled（无文本内容可导出） | tab 可选但后端无内容 → 跳过 |

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

---

## 10. MD 导出

端点：`GET /export/{id}?format=md&mode=`

直接返回原始 Markdown 文本，不做任何转换。图片路径保持 `/api/files/...` 原样。

**局限**：离线/分享场景下图片不可用。HTML 导出通过 base64 内联解决了此问题。

---

## 11. Word 导出（.docx）

端点：`GET /export/{id}?format=docx&mode=`（单文件）／ `POST /export/custom` 的 `formats: ["docx"]`（批量 ZIP）

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

> 差距收敛进度：2026-07-10 修图片收集/重写/内联 → 2026-08-04 对齐代码高亮 one-dark + checkbox 任务列表 + 暗色代码块 → 2026-09-21 新增 Word 导出（同一解析器，元素映射见 §11）。当前仅剩复制按钮 / 图片缩放两项导出无法复现（静态 HTML 本质限制）。
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
