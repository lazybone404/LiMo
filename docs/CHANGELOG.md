# 修改记录

> 记录每次重要修改的**做了什么**、**为什么**、**影响范围**。
> 不只是"修了 bug"，更是"当时为什么这样设计，后来为什么改了"。
> 按时间倒序，最近的在最上面。

---

## 2026-07-10 — 项目重命名：DeepDraft → LiMo（沥墨）

### 背景

发现 "DeepDraft" 名称已被他人使用，需要换名。

### 新名称

**LiMo（沥墨）** — LLM 的谐音。沥 = 过滤/沥出精华，墨 = 文字/文档。暗合项目本质：从信息洪流中沥出结构化知识。

### 修改范围

- **应用标识**：`com.deepdraft.app` → `com.limo.app`
- **二进制**：`DeepDraftBackend.exe` → `LiMoBackend.exe`（含 spec/tauri.conf/lib.rs）
- **日志文件**：`deepdraft_startup.log` → `limo_startup.log` 等
- **CSS 主题**：`data-theme="deepdraft"` → `data-theme="limo"`
- **导出默认名**：`DeepDraft_export_*.zip` → `LiMo_export_*.zip`
- **导出主题**：HTML `deepdraft` 主题 → `limo` 主题
- **User-Agent**：`DeepDraft/1.0` → `LiMo/1.0`
- **包名**：`deepdraft-backend` → `limo-backend`
- **所有文档**：CLAUDE.md、README、CHANGELOG、STATUS、export-pipeline、engine-standards、SECURITY_AUDIT
- **前端 UI 标题**：App.tsx、index.html、PreviewArea 欢迎语

### 影响范围

- 25 个源文件（全量文本替换）
- 1 个文件重命名（`DeepDraftBackend.spec` → `LiMoBackend.spec`）
- `target/` 构建缓存下次 `cargo build` 自动更新
- `dist/` 下次 `npm run build` 自动更新

---

## 2026-07-10 — 图片管线断层修复（第四轮）

### 背景

图片管线三段链路存在断层：导入保存原图 ✅ → 引擎转录 rawText（无图引用 ❌）→ LLM 生成笔记（无图可用 ❌）。经代码审计发现根因：

1. **PPTX 默认引擎图片提取严重不足**：`DocumentAdapter` 兜底链中 PPTX 默认使用 `python-pptx`（PptxEngine），该引擎仅提取 shape_type=13 的图片形状，遗漏大量内嵌图片（~1 张 vs markitdown 的 ~33 张）。CLAUDE.md 和引擎注释均说 markitdown 应替代 python-pptx，但代码未更新。
2. **PptxEngine 无 base64 回退**：`media_dir` 为空时图片提取被 `if media_dir` 守卫静默跳过，对标其他引擎（PyMuPDF/DocxEngine 都有双模式）缺失。
3. **EpubEngine 同问题**：`_walk()` 中 `if src and media_dir` 守卫导致无 `media_dir` 时图片静默丢失。
4. **前端图片加载失败静默隐藏**：`markdown-components.tsx:145` 的 `onError` 设 `display:none`，用户看不到本该有图的位置。

### 修改内容

#### 1. PPTX 默认引擎改为 markitdown

**文件**: `backend/app/adapters/document.py`

兜底链中 PPTX 从 `python-pptx` 改为 `markitdown`，与引擎注释和 CLAUDE.md 设计意图一致。

#### 2. PptxEngine 增加 base64 回退

**文件**: `backend/app/engines/doc.py`

`_extract_pptx_image()` 现在支持双模式：
- `media_dir` 非空 → 写文件引用 `![slide image](img_0001.png)`（原行为）
- `media_dir` 为空 → 内嵌 base64 `![slide image](data:image/png;base64,...)`

同时移除调用处的 `media_dir` 守卫条件。

#### 3. EpubEngine 增加 base64 回退

**文件**: `backend/app/engines/epub.py`

`_walk()` 中图片处理逻辑同样改为双模式，移除 `media_dir` 守卫。

#### 4. 前端图片加载失败显示占位符

**文件**: `frontend/src/lib/markdown-components.tsx`

- 新增 `MarkdownImage` 组件（独立组件以使用 `useState` hook）
- 加载失败时显示虚线边框占位符（SVG 图标 + alt 文字），替代原先的 `display:none`
- 对标 `CodeBlock` 独立组件模式

### 图片管线修复后状态

```
导入保存原图 ✅ → 引擎转录 rawText（含 ![]() ✅）→ LLM 生成笔记（可引用 ✅）
                                    ↕
                          content_preparer: keep_refs/send_to_llm 控制
```

| 引擎 | 图片输出 | 备注 |
|------|---------|------|
| PyMuPDF (text_only=False) | ✅ 文件/base64 | 嵌入图片 + 表格 |
| PyMuPDF4LLM | ✅ 文件/base64 | write_images=True |
| MarkItDown | ✅ 文件/base64 | 全格式通用 |
| DocxEngine (text_only=False) | ✅ 文件/base64 | 行内图片 |
| PptxEngine (text_only=False) | ✅ 文件/base64 | **本轮新增 base64 回退** |
| EpubEngine | ✅ 文件/base64 | **本轮新增 base64 回退** |
| ExcelEngine | ❌ | 无图片管线（表格为主） |

### 影响范围

- 后端：`document.py`（兜底链）、`doc.py`（PptxEngine）、`epub.py`（EpubEngine）
- 前端：`markdown-components.tsx`（新增 MarkdownImage 组件）

---

## 2026-07-10 — 离线资源打包 + 事实验证日志（第三轮）

### 背景

上一轮对话日志中发现 3 个问题：d3.js 离线不可用、事实验证日志不透明、SearXNG 未打包。同时核实了 plan 中 47 项修复的实际状态——大部分已在之前会话中修复完毕。

### 修改内容

#### 1. d3.js / markmap 离线不可用修复

**根因**：`_get_markmap_assets()` 仅从 `frontend/node_modules/` 读取 d3.js 和 markmap JS 文件。PyInstaller 打包后该路径不存在，思维导图 HTML 导出依赖 CDN fallback，但 Tauri WebView2 跟踪防护会拦截 CDN 请求，导致离线环境导出空白。

**改法**：
- `export.py` `_get_markmap_assets()`：增加双路径搜索（对标 KaTeX 已有设计）
  - 开发环境：`frontend/node_modules/`（优先）
  - 打包环境：`backend/static/markmap/`（备选）
- 创建 `backend/static/markmap/` 目录，从 node_modules 复制 3 个文件（d3.min.js 280KB + markmap-view.js 50KB + markmap-lib.js 677KB）
- 同步创建 `backend/static/katex/` 目录（KaTeX 双路径设计已有但 static 目录缺失）
- `LiMoBackend.spec`：datas 新增 `('backend/static', 'static')` 打包离线资源
- `CLAUDE.md`：PyInstaller 常见陷阱新增"KaTeX + markmap/d3 离线资源"条目

#### 2. 事实验证日志不透明

**根因**：`FactCheckEngine.verify_claim()` 中搜索、LLM 调用、未预期异常共用一个笼统的 catch，日志 `验证 claim '...' 失败: {e}` 无法区分失败阶段。上次日志中 `失败: ` 后面为空即是因为异常类型不明。

**改法**：
- 搜索阶段：独立 try/except，记录 provider source + query
- 搜索无结果时：info 级别日志记录 provider 和 query（方便排查是搜索源问题还是 query 问题）
- LLM 交叉验证阶段：独立 try/except，标识"LLM 交叉验证失败"
- 外层 catch-all：日志增加异常类型名 `type(e).__name__`

#### 3. 核实 plan 中 47 项实际状态

| 状态 | 项目 |
|------|------|
| 已修复 | C1, H1-H8, H12（9 项，之前会话中已修） |
| 误报 | C2（`disconnect_task=None` 在 try 前是防御性写法，finally 块引用它做 None 检查） |
| 本轮修复 | d3.js 离线、事实验证日志（2 项） |
| 低优先级 | SearXNG 未打包（200MB+ 依赖，非 bug，搜索降级链正常） |

### 影响范围

- 后端：`export.py`（`_get_markmap_assets` 双路径）、`fact_check.py`（verify_claim 分阶段日志）
- 构建：`LiMoBackend.spec`（新增 static/ datas）、`backend/static/`（新增 markmap + katex 目录，~1.3MB）
- 文档：`CLAUDE.md`（PyInstaller 构建陷阱）、`CHANGELOG.md`（本条记录）

---

## 2026-07-10 — 导出系统全面修复（第二轮）

### 背景

用户反馈两个问题：
1. "详细导出无法导出" — 控制台报 `/api/api/export/custom` 404
2. "快速导出导出的不是预览栏展示的对应产物"

第一轮修复（同日早些时候）解决了 GFM/Pygments/KaTeX/暗色模式等问题，但用户使用时发现路径重复和内容映射错误。

### 修改内容

#### 1. 路径重复 `/api/api/` → 404（6 处）

**根因**：`downloadFromUrl()` / `apiUrl()` 内部已拼接 BASE（含 `/api`），但调用方又传了带 `/api` 前缀的路径。

| 文件 | 修复 |
|------|------|
| `ExportMenu.tsx:85` HTML 导出 | `/api/export/` → `/export/` |
| `ExportMenu.tsx:121` 思维导图导出 | 同上 |
| `ExportMenu.tsx:164` ZIP 导出 | 同上 |
| `ExportPanel.tsx:346` 导出全部产物 | 同上 |
| `ExportPanel.tsx:362` 详细导出 | `apiUrl('/api/export/custom')` → `apiUrl('/export/custom')` |
| `ChatCore.tsx:313` 对话导出 | 同上 |

#### 2. ExportMenu 重写 — 集中式 Tab→内容映射表

**为什么**：原先 tab→导出行为的映射分散在嵌套三元表达式（`handleExportHTML`）和 if-else 链（`getExportContent`）中，14 个 tab 只有 6 个被正确处理，图片处理 tab、skeleton、mindmap 全部 fallthrough 到错误内容。

**改法**：在 `ExportMenu.tsx` 中引入 `TAB_EXPORT` 查找表，13 个 tab 全量覆盖：

```typescript
const TAB_EXPORT: Record<string, TabExportConfig> = {
  summary:     { contentKey: 'notes',      htmlMode: 'notes',      suffix: '', ... },
  polished:    { contentKey: 'polished',   htmlMode: 'polished',   ... },
  transcript:  { contentKey: 'rawText',    htmlMode: 'transcript', ... },
  data_preview:{ contentKey: 'rawText',    htmlMode: 'transcript', ... },
  report:      { contentKey: 'polished',   htmlMode: 'polished',   ... },
  fact_check:  { contentKey: 'factCheck',  htmlMode: 'fact_check', ... },
  mindmap:     { contentKey: 'notes',      htmlMode: '__mindmap__',... }, // HTML 走专用端点
  skeleton:    { contentKey: 'rawText',    htmlMode: 'transcript', ... },
  extract:     { contentKey: 'imageResult',htmlMode: 'extract',    ... },
  table:       { contentKey: 'imageResult',htmlMode: 'table',      ... },
  digitize:    { contentKey: 'imageResult',htmlMode: 'digitize',   ... },
  understand:  { contentKey: 'imageResult',htmlMode: 'understand', ... },
  original:    { contentKey: 'none',       htmlMode: '',           ... }, // 原图无文本可导出
}
```

**设计原则**：新增 tab 时只需在此表加一行，无需改动任何业务逻辑。

#### 3. 后端 `GET /export/{id}` 增加 process result 回退

**为什么**：图片文档（sourceType=image）没有 markdown 条目，处理结果存在 `process_results` 中。原先 `export_content` 端点只查 markdown 表 → 404。

**改法**：在 `export.py:115` 增加回退链：
```
markdown（notes/polished/fact_check）→ process_results（extract/table 等）→ 404
```

#### 4. ExportPanel 修复

- **预览数据源**：原先始终显示 `currentContent`（PreviewArea 当前文档），与导出面板中选中的文档无关。改为仅当 `selectedIds[0] === currentDocId` 时才显示预览。
- **"导出全部产物"按钮**：原先仅单文档可用（`disabled={selectedIds.length !== 1}`）。改为：单文档走 `GET /export/{id}/all`，多文档走 `POST /export/batch`。

#### 5. 文档更新

- [export-pipeline.md](export-pipeline.md)：全面重写，新增架构总览、完整 tab→内容映射表（5 种文档类型 × 14 tab）、设计决策记录、新增模式操作指南
- [CLAUDE.md](../CLAUDE.md)：导出系统关键模式重写，增加两条路径设计、核心约束、回退链、已修复问题清单

### 影响范围

- 前端：`ExportMenu.tsx`（重写）、`ExportPanel.tsx`（3 处修改）、`ChatCore.tsx`（1 处）
- 后端：`export.py`（`export_content` 增加 process result 回退）
- 文档：`export-pipeline.md`（重写）、`CLAUDE.md`（导出系统部分更新）

---

## 2026-07-10 — 导出管线全面审计 + 第一轮修复

### 背景

用户要求："各种产物导出逻辑是否正确，是否能按要求样式设置"。

对 `backend/app/routers/export.py` 进行端到端审查，发现 12 项问题，按 P0-P3 优先级分批修复。

### 修改内容

#### P0：`_remove_formulas` 跨行公式匹配

`$$...$$` 块级公式使用 `r'\$\$[\s\S]*?\$\$'` 替代原正则，支持跨行匹配。

#### P1：`data_preview` 内容源修正

`export_custom` 中 `data_preview` tab 原从 markdown 表读取（错误），改为 `tx["rawText"]`（与预览栏一致）。

#### P1：mindmap 端点内容选择

`GET /export/{id}/mindmap` 原取 `mds[0]`（可能为 polished/fact_check），改为优先取非 polished/非 fact_check 的笔记。

#### P2：行内 `$` 解析 — 防误匹配货币

`_protect_latex` / `_remove_formulas` / `_render_formulas_to_images` 三处行内 `$` 正则统一改为 TeX 惯例：
```
(?<!\d)\$(?=\S)(.+?)(?<=\S)\$(?!\d)
```
`$` 前后不邻数字或空白，不再误匹配 `$5 and $10`。

#### P2：`_to_html` / `HTML_HEAD` / `HTML_FOOT` 弃用

所有 HTML 导出已统一走 `_build_selfcontained_html()`（零 CDN）。旧函数注释保留以便未来恢复 CDN-base 轻量导出。

#### P3：代码语法高亮（Pygments）

- 新增 `_PYGMENTS_OK` 可选依赖检测
- `codehilite` 扩展在 Pygments 可用时启用
- `_get_pygments_css()` 生成 CSS + `.codehilite` 样式
- `_add_lang_labels()` 从 `<code class="language-xxx">` 生成语言标签

#### P3：删除线支持

`_preprocess_gfm()`：`~~text~~` → `<del>text</del>`，4 主题 CSS 追加 `del { text-decoration: line-through; }`。

#### P3：任务列表支持

`- [ ] task` → `☐ task`，`- [x] task` → `☑ task`。

#### P3：limo 暗色模式

limo 主题增加 `@media (prefers-color-scheme: dark)` 完整暗色适配。

### 构建验证

- 前端 `npm run build` ✅
- 后端 6 测试文件全过（38/38）✅
- Tauri MSI + NSIS 安装包生成成功 ✅

### 影响范围

- 后端：`export.py`（约 200 行修改，新增/修改 ~10 个函数）
- 文档：`export-pipeline.md` 全面更新
- 内存：`docs/export-pipeline.md` 记录所有修复细节

---

## 2026-07-10 之前 — 基础架构沉淀

以下内容来自 CLAUDE.md 和既有文档记录的关键设计决策，日期为最后审查确认日。

### 数据流与持久化

- 配置唯一入口 `config_io.py → save_config()`，禁止业务模块直接 `yaml.dump`
- 模型解析唯一入口 `model_resolver.py → resolve_model()`
- 引擎映射用 `model_name` 查找（非 `provider_id`），支持字符串/数字/列表三种绑定格式
- 引擎绑定保存增量合并（`existing.update(stored)`），非全量替换
- LLM Key 双向同步：`update_provider` 自动同步到 `llm.api_key`

### 导入与去重

- GenPanel 是唯一导入入口，DocLibrary 只负责搜索/筛选/列表
- 两层去重：URL 去重（请求到达时）→ 内容去重（SHA256，提取正文后）
- 空文本去重碰撞：空字符串 SHA256 固定，TextAdapter 检查 `text.strip()` 非空

### 生成与处理

- 双产物并行（`asyncio.gather`）：notes + polished，source_type 派发 prompt
- 图片三开关：`keep_refs` → `send_to_llm` → `optimize_images`
- LLM 续写：`finish=length` → 3 轮续写，temperature 递减
- 流式 + 取消：SSE 端点，`cancel_event` 真正中断 token 消耗

### 并发与安全

- `ExecutionGuard` 双层信号量：cloud(5) + local(2)
- Store `threading.Lock` 防 RMW 竞态
- 安全加固：path traversal 防护、上传 2GB 预检、SSRF 防护（14 个 IP 段黑名单 + DNS 二次校验）

### 前端关键模式

- GenPanel 类型门控：5 类型 × 5 子标签
- PreviewArea 按 sourceType 分派 tab
- processingTasks 键格式：`${docId}__${kind}`
- 所有 HTTP 请求必须走 `apiUrl()` 或 `apiFetch()`
- 主题系统：14 个 CSS 令牌 + Tailwind v4 + Light/Dark/System
- 取消机制：`cancelEvent` + `request.is_disconnected()` 轮询

### 导出系统（初始）

- 图片收集/重写/内联三个核心函数
- HTML 自包含（KaTeX 预渲染·内联 CSS·零 CDN·4 主题）
- ZIP 三种图片模式 + 多产物标签
- 思维导图：SVG 下载 + markmap 交互式 HTML

### Tauri 桌面壳

- 生产模式：`std::process::Command` 启动 PyInstaller 打包的 exe
- 就绪探测：HTTP GET `/health` 轮询（500ms 间隔，45s 超时）
- 进程管理：`SidecarHandle(Mutex<Option<Child>>)`
- PATH 增强：`enhance_path()` 追加 ffmpeg 路径
- CORS：`allow_origin_regex=".*"` + `_ForceCORSMiddleware` 兜底

---

## 如何添加新记录

1. 在文件顶部 `## YYYY-MM-DD — 标题` 添加新条目
2. 包含三个部分：
   - **背景**：为什么做这个修改
   - **修改内容**：具体改了哪些文件、哪些行
   - **影响范围**：前端/后端/文档/构建
3. 如果修改涉及设计决策，同时更新 `docs/export-pipeline.md` 等专题文档
4. 如果修改了关键约束，同步更新 `CLAUDE.md` 中的"关键模式"部分
