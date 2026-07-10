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
  └─ 详细导出 (ZIP)    →      fetch() + downloadBlob() →   POST /export/custom

AI 对话右键菜单                 ChatCore                     GET /export/conversation/{id}
  └─ 导出 Markdown     →      downloadFromUrl()      →     GET /export/conversation/{id}?format=md
```

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

Tabs: `summary`, `polished`, `transcript`, `fact_check`, `mindmap`

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| summary | MarkdownPreview(currentContent) | currentContent | notes | `get_markdowns_by_transcript(mode="notes")` |
| polished | MarkdownPreview(currentPolishedContent) | currentPolishedContent | polished | `get_markdowns_by_transcript(mode="polished")` |
| transcript | TranscriptView(currentRawText) | currentRawText | transcript | `tx["rawText"]`，JSON 格式解析为文本 |
| fact_check | MarkdownPreview(currentFactCheckContent) | currentFactCheckContent | fact_check | `get_markdowns_by_transcript(mode="fact_check")` |
| mindmap | MindmapView(currentContent) | currentContent | — (专用端点) | 笔记 markdown → `_build_mindmap_html()` |

### 2.2 Excel 文档

Tabs: `data_preview`, `report`, `summary`

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| data_preview | TranscriptView(currentRawText) | currentRawText | transcript | `tx["rawText"]`（原始数据，不解析 JSON） |
| report | MarkdownPreview(currentPolishedContent) | currentPolishedContent | polished | `get_markdowns_by_transcript(mode="polished")` |
| summary | MarkdownPreview(currentContent) | currentContent | notes | `get_markdowns_by_transcript(mode!="polished")` |

### 2.3 EPUB 文档

Tabs: `skeleton`, `summary`, `polished`, `transcript`, `fact_check`, `mindmap`

| Tab | 预览内容 | store 字段 | 后端 mode | 后端内容源 |
|-----|---------|-----------|----------|-----------|
| skeleton | EpubSkeletonPanel(currentRawText) | currentRawText | transcript | `tx["rawText"]`（骨架 JSON） |
| 其余同文本类 | — | — | — | — |

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

### 2.5 前端 ExportMenu 集中式配置

`frontend/src/components/ui/ExportMenu.tsx` 中 `TAB_EXPORT` 表是前端唯一的 tab→导出行为映射。新增 tab 时只需在此表加一行：

```typescript
const TAB_EXPORT: Record<string, TabExportConfig> = {
  summary:     { contentKey: 'notes',       htmlMode: 'notes',       suffix: '',              label: '笔记' },
  polished:    { contentKey: 'polished',    htmlMode: 'polished',    suffix: '-整理版原文',     label: '整理版原文' },
  transcript:  { contentKey: 'rawText',     htmlMode: 'transcript',  suffix: '-转录',           label: '转录' },
  data_preview:{ contentKey: 'rawText',     htmlMode: 'transcript',  suffix: '',              label: '数据预览' },
  report:      { contentKey: 'polished',    htmlMode: 'polished',    suffix: '-分析报告',       label: '分析报告' },
  fact_check:  { contentKey: 'factCheck',   htmlMode: 'fact_check',  suffix: '-事实验证',       label: '事实验证' },
  mindmap:     { contentKey: 'notes',       htmlMode: '__mindmap__', suffix: '',              label: '思维导图' },
  skeleton:    { contentKey: 'rawText',     htmlMode: 'transcript',  suffix: '-骨架',           label: '章节骨架' },
  extract:     { contentKey: 'imageResult', htmlMode: 'extract',     suffix: '-提取文字',       label: '提取文字' },
  table:       { contentKey: 'imageResult', htmlMode: 'table',       suffix: '-表格数据',       label: '表格数据' },
  digitize:    { contentKey: 'imageResult', htmlMode: 'digitize',    suffix: '-文档数字化',     label: '文档数字化' },
  understand:  { contentKey: 'imageResult', htmlMode: 'understand',  suffix: '-理解内容',       label: '理解内容' },
  original:    { contentKey: 'none',        htmlMode: '',            suffix: '',              label: '原图' },
  ocr:         { contentKey: 'none',        htmlMode: '',            suffix: '',              label: 'OCR' },
}
```

**字段说明**：
- `contentKey`：决定 `getExportContent()` 从 store 读哪个字段。`'none'` 表示无内容可导出
- `htmlMode`：传给后端 `GET /export/{id}?mode=` 的值。`'__mindmap__'` 表示走 `/export/{id}/mindmap` 专用端点。空字符串表示 HTML 导出不可用
- `suffix`：文件名后缀
- `label`：人类可读标签，用于无产物时的 tooltip

### 2.6 无产物处理

当用户尚未生成对应产物时（如刚导入还没点"生成笔记"），导出系统必须优雅降级：

| 场景 | ExportMenu 行为 | ExportPanel 行为 |
|------|----------------|-----------------|
| 无笔记（summary tab） | 按钮 disabled，tooltip 显示"尚未生成笔记" | 勾选 summary tab → 后端查不到内容 → 跳过，不写入 ZIP |
| 无整理版（polished tab） | 按钮 disabled | 同上 |
| 无事实验证 | 按钮 disabled | 同上 |
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
    # ~~text~~ → <del>text</del>
    md = re.sub(r'~~(.+?)~~', r'<del>\1</del>', md)
    # - [ ] task → ☐ task
    md = re.sub(r'^- \[ \] (.+)$', r'- ☐ \1', md, flags=re.MULTILINE)
    # - [x] task → ☑ task
    md = re.sub(r'^- \[x\] (.+)$', r'- ☑ \1', md, flags=re.MULTILINE)
    return md
```

**局限**：用 Unicode 字符 ☐/☑ 而非 `<input type="checkbox">`，因为静态 HTML 无法交互。

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

### 客户端回退

HTML 中嵌入 KaTeX auto-render 脚本，CDN 回退。但已优先使用服务端预渲染 + 内联 CSS/JS。

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

调用 `_build_mindmap_html(md, title)` 生成自包含 HTML。内容源优先取笔记 markdown（非 polished、非 fact_check），回退到 rawText。

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
  "content_tabs": ["summary", "polished", "transcript", "fact_check", "mindmap"],
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
| 代码高亮 | Prism (oneDark/oneLight) | Pygments (default) | 颜色方案不同 |
| 语言标签 | ✅ `<span>` | ✅ `_add_lang_labels` 生成 | 一致 |
| 复制按钮 | ✅ `<button>` | ❌ | 静态 HTML 限制 |
| 删除线 | ✅ `remark-gfm` | ✅ `_preprocess_gfm` → `<del>` + CSS | 一致 |
| 任务列表 | ✅ `<input checkbox>` | ✅ Unicode ☐/☑ | 视觉不同 |
| 标题锚点 | ✅ `id` + `<Link2>` | ❌ | 导出无 |
| 时间戳 badge | ✅ `<Play>` 图标 | ❌ | 导出无 |
| KaTeX | remark-math + rehype-katex | Node.js 预渲染 + CDN | 一致 |
| $ 误匹配 | ✅ TeX 惯例 | ✅ 已修复 | 一致 |
| 图片 | apiUrl() 路径 | base64 内联（HTML）/ 保持原样（MD） | HTML 自包含 |
| 暗色模式 | ✅ CSS 变量 + data-theme | ✅ limo 主题支持 | 默认主题支持 |
| 图片缩放 | ✅ react-medium-image-zoom | ❌ | 导出无交互 |

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

ExportMenu 从客户端 store 读数据（即时），ExportPanel 把 tab 名发给后端从磁盘读（可批量）。两者数据源不同，用同一套映射反而会引入耦合。但两者必须映射到**相同的内容**——这就是本文档第 2 节映射表的作用。

### 为什么 mindmap HTML 导出走独立端点？

思维导图需要 markmap 库（d3 + markmap-view + markmap-lib）+ 特定的 HTML 模板结构。与普通的 markdown→HTML 管线完全不同，强行合入会造成大量条件分支。

### 为什么图片文档的 HTML 导出需要后端 process result 回退？

图片文档（sourceType=image）导入后只有 process_results（OCR/提取结果），没有 markdown 条目。`GET /export/{id}?format=html` 原先只查 markdown 表 → 404。修复后增加 `get_process_results()` 回退链：markdown → process results → 404。

---

## 14. 新增导出模式操作指南

1. **后端 `export_custom`**（第 933 行起）：在 `tab_contents` 收集循环中添加新的 `elif tab == "xxx":` 分支
2. **后端 `export_content`**（第 92 行起）：如果新模式不是 markdown/transcript，添加内容源回退逻辑
3. **前端 `ExportMenu.tsx`**：在 `TAB_EXPORT` 表中添加一行，指定 `contentKey`、`htmlMode`、`suffix`、`label`
4. **前端 `constants.ts`**：如果是新文档类型的新 tab，更新 `getTypeMeta()` 中的 tabs 数组
5. **本文档第 2 节**：更新映射表
