# 导出管线

> 写于 2026-07-10，最后更新 2026-07-10。
> 记录 Markdown→HTML 导出、思维导图、批量 ZIP 的完整管线架构、设计决策、内容映射表和已知局限。

---

## 1. 架构总览：两条导出路径

导出系统提供两条互补路径，各自有不同的设计目标：

```
用户入口                        前端组件                    后端端点
────────                        ────────                    ────────
PreviewArea 工具栏             ExportMenu（下拉菜单）        GET /export/{id}
  ├─ 下载 MD           →      客户端直读 store              （无后端调用）
  ├─ 导出 HTML         →      downloadFromUrl()      →     GET /export/{id}?format=html&mode=...
  ├─ 全部产物 ZIP      →      downloadFromUrl()      →     GET /export/{id}/all
  ├─ 思维导图 SVG      →      客户端 DOM clone              （无后端调用）
  ├─ 思维导图 HTML     →      downloadFromUrl()      →     GET /export/{id}/mindmap
  └─ 复制 MD           →      客户端 clipboard             （无后端调用）

顶部工具栏 Download 按钮       ExportPanel（侧边面板）       POST /export/custom
  ├─ 导出全部产物      →      downloadFromUrl() / fetch →  GET /export/{id}/all 或 POST /export/batch
  └─ 详细导出 (ZIP)    →      进度反馈                  →     POST /export/custom/stream（SSE 进度）
                                                        →     GET /export/download/{temp_id}（临时下载）

AI 对话右键菜单                 ChatCore                     GET /export/conversation/{id}
  ├─ 导出 Markdown     →      downloadFromUrl()      →     GET /export/conversation/{id}?format=md
  └─ 批量导出 ZIP      →      downloadFromUrl()      →     POST /export/conversations/batch
```

> **9 个导出端点**：`GET /export/{id}`（单文件）、`GET /export/{id}/all`（全产物）、`POST /export/batch`（多文档）、`GET /export/{id}/mindmap`、`GET /export/conversation/{id}`、`POST /export/conversations/batch`、`POST /export/custom`、`POST /export/custom/stream`（SSE 进度）、`GET /export/download/{temp_id}`。

### 设计意图

| 特性 | ExportMenu（快捷导出） | ExportPanel（详细导出） |
|------|----------------------|------------------------|
| 入口 | PreviewArea 工具栏下拉 | 顶部 Download 按钮 → 侧边滑出面板 |
| 文档数 | 始终是当前预览的 1 篇 | 可选多篇 |
| 内容选择 | 自动跟随当前 tab | 用户手动勾选 tab |
| 格式/主题 | 固定（MD 原文，HTML limo 主题） | 可选 MD/HTML/纯文本 + 4 主题 + 图片/公式策略 |
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

### 2.5 前端 Tab 注册表 + 内容源（2026-08-14 重写）

旧的 `TAB_EXPORT` 集中表已移除，重构为**注册表 + getter 绑定**两层（替代散落在 5 处的 switch/case / 映射表）：

1. **`lib/tabRegistry.ts`** — 纯数据元注册：`TabRegistry.register(TabDef)` 一处注册，零改动其他文件。`TabDef` 含 `contentSource`/`htmlMode`/`exportSuffix`/`exportLabel` 导出相关字段：

```typescript
export type ContentSource = 'notes' | 'polished' | 'rawText' | 'imageResult' | 'none'

TabRegistry.register({
  key: 'summary', label: '笔记',
  sourceTypes: ['video', 'webpage', 'text', 'link', 'pdf', 'docx', 'pptx', 'document', 'epub', 'xlsx'],
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

## 3. 核心转换函数 `_markdown_to_html()`

```python
def _markdown_to_html(md: str) -> str:
    md = _preprocess_gfm(md)  # 删除线、任务列表
    extensions = [
        "tables", "fenced_code", "sane_lists", "smarty",
        "md_in_html", "def_list", "abbr",
    ]
    if _PYGMENTS_OK:
        extensions.append("codehilite")
    import markdown
    return markdown.markdown(md, extensions=extensions)
```

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
  "formats": ["md", "html", "plain"],
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

---

## 10. MD 导出

端点：`GET /export/{id}?format=md&mode=`

直接返回原始 Markdown 文本，不做任何转换。图片路径保持 `/api/files/...` 原样。

**局限**：离线/分享场景下图片不可用。HTML 导出通过 base64 内联解决了此问题。

---

## 11. 导出 vs 预览差异速查

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

> 差距收敛进度：2026-07-10 修图片收集/重写/内联 → 2026-08-04 对齐代码高亮 one-dark + checkbox 任务列表 + 暗色代码块。当前仅剩复制按钮 / 图片缩放两项导出无法复现（静态 HTML 本质限制）。

---

## 12. 依赖

| 包 | 用途 | pip 名 | PyInstaller hiddenimport |
|----|------|--------|--------------------------|
| markdown | Markdown→HTML 核心转换 | `markdown` | `markdown` |
| Pygments | 代码语法高亮（可选） | `pygments` | `pygments, pygments.lexers, pygments.formatters, pygments.styles` |
| KaTeX | 公式渲染 | —（node_modules 读取） | —（仅需前端 node_modules） |

---

## 13. 设计决策记录

### 为什么 MD 导出走客户端而 HTML 导出走后端？

- **MD**：就是原始文本，无需转换。从 store 直读即可，零网络开销。
- **HTML**：需要 KaTeX 预渲染（Node.js 子进程）、Pygments 语法高亮、图片 base64 内联。这些能力只有后端有。

### 为什么 ExportMenu 和 ExportPanel 不共享内容映射？

ExportMenu 从客户端 store 读数据（即时），ExportPanel 把 tab 名发给后端从磁盘读（可批量）。两者数据源不同，用同一套映射反而会引入耦合。但两者必须映射到**相同的内容**——这就是本文档第 2 节映射表的作用。前端取值层统一由 `getContentForView(view, ctx)`（`lib/contentSources.ts`）提供，PreviewArea 预览、复制、快捷导出三处消费同一 getter，保证「预览看到什么导出就得到什么」。

### 为什么 mindmap HTML 导出走独立端点？

思维导图需要 markmap 库（d3 + markmap-view + markmap-lib）+ 特定的 HTML 模板结构。与普通的 markdown→HTML 管线完全不同，强行合入会造成大量条件分支。

### 为什么图片文档的 HTML 导出需要后端 process result 回退？

图片文档（sourceType=image）导入后只有 process_results（OCR/提取结果），没有 markdown 条目。`GET /export/{id}?format=html` 原先只查 markdown 表 → 404。修复后增加 `get_process_results()` 回退链：markdown → process results → 404。

---

## 14. 新增导出模式操作指南

1. **前端 `lib/tabRegistry.ts`**：`TabRegistry.register(TabDef)` 注册新 tab，指定 `contentSource`（→ §2.5 字段说明）、`htmlMode`、`exportSuffix`、`exportLabel`。零改动其他文件
2. **前端 `lib/contentSources.ts`**：若新 tab 的内容源不是已有的 6 个 `ContentSource` 枚举之一，加枚举 + `CONTENT_GETTERS` 补 getter（`Record` 类型约束编译期强制），三处消费（PreviewArea/CopyContentBtn/ExportMenu）自动生效
3. **后端 `export_content`**（`GET /export/{id}`，第 92 行起）：确认 `htmlMode` 走 content 回退链（markdown → process_results → transcript → 404）
4. **后端 `export_custom`**（`POST /api/export/custom`，第 933 行起）：如需批量/自定义导出，在 `tab_contents` 收集循环加 `elif tab == "xxx":` 分支
5. **后端 `_markdown_to_html`**：若新内容引入新的 GFM 风格语法，在 `_preprocess_gfm` 补对应预处理（删除线/任务列表/时间戳 badge 模式）
6. **本文档第 2 节**：更新内容源映射表
