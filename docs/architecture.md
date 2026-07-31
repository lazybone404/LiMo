# 分层架构

> 写于 2026-07-10。每一层是什么、有哪些模块、模块之间怎么交互、关键设计决策和已知陷阱。

---

## 总览：六层管道

```
┌─────────────────────────────────────────────────────┐
│ Layer 0: 输入层                                      │
│ PipelineInput → Adapter.accept() → Adapter.adapt()  │
│ 5 个适配器：Video / Document / Image / Webpage / Text │
├─────────────────────────────────────────────────────┤
│ Layer 1: 转录层                                      │
│ Engine.bind() → Engine.transcribe() / .process()    │
│ 13 个内置引擎：ASR×4 / OCR×2 / Doc×7（+插件引擎）    │
├─────────────────────────────────────────────────────┤
│ Layer 2: 存储层                                      │
│ Store — 文件系统 + JSON 索引                        │
│ Transcript / MarkdownView / ProcessResult / Conv    │
├─────────────────────────────────────────────────────┤
│ Layer 3: 生成层                                      │
│ Generator + PromptBuilder + ContentPreparer         │
│ 双产物并行（notes + polished），SSE 流式输出          │
├─────────────────────────────────────────────────────┤
│ Layer 4: 处理层                                      │
│ Process (图片 OCR/表格/数字化/理解)                   │
│ FactCheck (三阶段：提取→搜索验证→交叉核查)             │
├─────────────────────────────────────────────────────┤
│ Layer 5: 导出层                                      │
│ Markdown → HTML / MD / ZIP / Mindmap                │
│ Pygments 高亮 + KaTeX 渲染 + GFM 预处理               │
└─────────────────────────────────────────────────────┘
         │
    ┌────┴────┐
    │ 横切关注 │
    │ Concurrency │ Config │ Logging │ Errors │ Security │
    └─────────┘
```

---

## Layer 0：输入层

### 5 个适配器

| 适配器 | 文件 | 输入 | accept() | adapt() | 引擎依赖 |
|--------|------|------|----------|---------|---------|
| Video | `adapters/video.py` | URL/文件(.mp4/.mkv) | URL含视频平台 | yt-dlp下载→ASR | ASR |
| Document | `adapters/document.py` | URL/文件(.pdf/.docx/.pptx/.xlsx/.epub) | 扩展名匹配 | 引擎提取文字 | Doc(+OCR) |
| Image | `adapters/image.py` | URL/文件(.png/.jpg) | 扩展名匹配 | 保存原图+元数据 | OCR |
| Webpage | `adapters/webpage.py` | URL(http/https) | HTML内容检测 | trafilatura提取正文 | 无 |
| Text | `adapters/text.py` | 纯文本/Markdown | 兜底(始终accept) | 直接通过 | 无 |

### 协议

```python
class PipelineAdapter(Protocol):
    def accept(self, input: PipelineInput, config: dict) -> bool: ...
    async def adapt(self, input: PipelineInput, config: dict, pg) -> AdapterOutput: ...
```

### 路由

注册顺序决定优先级：Video → Document → Image → Webpage → Text（兜底）。`AdapterRegistry.register()` 一行注册，`AdapterRegistry.find(input, config)` 返回第一个 accept 的适配器。

### 关键决策

- **URL 去重**：`find_by_url` 在请求到达时拦截，Store 拒绝写入重复 URL
- **内容去重**：SHA256 contentHash 在提取正文后拦截
- **空文本防护**：TextAdapter 检查 `text.strip()` 非空，避免两个空导入产生相同 SHA256 碰撞
- **SSRF 防护**：WebpageAdapter 必须加 `validate_url()`（曾遗漏，已修复）

### 陷阱

- 新增适配器必须加 SSRF 校验
- 适配器失败应 `raise FatalError`，不要返回 `AdapterOutput(engine="none")`（已修复 video.py 两处）

---

## Layer 1：转录层

### 引擎注册表

```python
EngineRegistry.register("asr", "faster-whisper-small", FasterWhisperEngine(...))
EngineRegistry.register("doc", "docling", DoclingEngine(...))
EngineRegistry.register("ocr", "tesseract", TesseractEngine(...))
```

插件引擎（AGPL 隔离）：`plugin_loader.py` 从 `~/.limo/plugins/` 加载 `EnginePlugin` 声明，进程外 subprocess 模式（command 指向子目录 venv）动态注册进 EngineRegistry，capabilities 的 `fileTypeMap` 按插件 `file_types` 自动追加。

`self.capabilities` 声明引擎能做什么：`text`, `table`, `image`, `pdf`, `office`。

### 三类引擎

| 类别 | 引擎 | 类型 | capabilities |
|------|------|------|-------------|
| **ASR** | dashscope-asr | 云端 | text |
| ASR | faster-whisper-small | 本地 | text |
| ASR | faster-whisper-medium | 本地 | text |
| ASR | bcut-asr | 免费云 | text |
| **OCR** | dashscope-ocr | 云端 | text |
| OCR | tesseract | 本地(便携版 `bin/tesseract/`) | text |
| **Doc** | docling | 本地 | text, table, image, pdf |
| Doc | pypdfium2 | 本地 | text, table, image, pdf |
| Doc | markitdown | 本地 | text, table, image, office |
| Doc | python-docx | 本地 | text, table, image, office |
| Doc | python-pptx | 本地 | text, table, office |
| Doc | openpyxl | 本地 | text, table, office |
| Doc | ebooklib | 本地 | text (EPUB章节) |
| Doc (插件) | pymupdf4llm | 进程外 | text, table, image, pdf |

> Doc 引擎源码已拆分为独立文件：`doc_types.py`、`doc_markitdown.py`、`doc_docx.py`、`doc_pptx.py`、`doc_excel.py`、`docling.py`（含 PyPdfium2Engine）、`epub.py`。pdfplumber 为 pypdfium2 的表格辅助，不单独注册。text_only 变体引擎（docling-text/python-docx-text/python-pptx-text）已删除（与完整版重复无意义）。AGPL 引擎不内置，pymupdf4llm 以插件形式加载。

### 引擎绑定

通过 `engine_bindings` 配置，按子场景绑定：

```yaml
engine_bindings:
  asr: dashscope-asr
  ocr: tesseract
  doc:pdf: docling
  doc:word: python-docx
  doc:ppt: python-pptx
  doc:excel: openpyxl
  doc:epub: ebooklib
```

**保存机制**：增量合并 — 保存 OCR 绑定不会删除 ASR 绑定。`null` = 不修改，`[]` = 清除绑定。

### 子场景拆分

文档类型根据扩展名拆分为子场景，各有独立引擎链：

| 扩展名 | 子场景 | 引擎优先级 |
|--------|--------|-----------|
| .pdf | `doc:pdf` | 绑定链 → 扩展名兜底 docling |
| .docx | `doc:word` | python-docx → markitdown |
| .pptx | `doc:ppt` | python-pptx → markitdown |
| .xlsx | `doc:excel` | openpyxl → markitdown |
| .epub | `doc:epub` | ebooklib → markitdown |

> 绑定值支持三种格式：字符串（`"pymupdf4llm"` 直接查注册表）、整数（engine_models id）、列表（旧格式，取首个）。`init_engines` 启动时清理失效 key 与无效 id（回退默认引擎）。扩展名兜底是默认行为，不受 `engine_auto_fallback` 控制。

### 降级开关

`engine_auto_fallback` 控制引擎失败时是否静默降级：
- `true`（默认）：引擎失败 → 尝试下一个
- **`false`**：引擎失败 → 直接报错 `FatalError`

6 个解析点全部尊重此开关。

### 关键决策

- **线程安全**：引擎实例 `copy.copy()` 防 api_key 串用（多请求并发时各用各的）
- **Tesseract 便携版**：`backend/bin/tesseract/` 内置，启动自动注册
- **模糊帧过滤**：`frame_quality.py` — Laplacian 方差 + 熵 + 边缘密度，丢弃低质量帧

---

## Layer 2：存储层

### 目录结构（v3 按文档聚合）

```
data/
├── index.json                  # v3 索引（transcripts 含 dir_name + source_file_path）
├── config.yaml / config.yaml.bak
├── files/                      # 原始文件按 hash 存，不变
│   └── {hash12}/{filename}
├── conversations/              # AI 对话，不变
│   └── {prefix}/{id}.json
└── {title}-{6位hex}/           # ★ 一文档一目录
    ├── transcript.md           # rawText（Markdown 或 image JSON）
    ├── notes.md                # 笔记最新版
    ├── notes.prev.md           # 笔记上一版
    ├── polished.md / polished.prev.md
    ├── fact_check.md / fact_check.prev.md
    ├── process/                # 图片处理结果
    │   └── {mode}.md           # extract/table/digitize/understand
    └── assets/                 # 图片资源（img_*.png / cover / original / screenshot_*）
```

- 目录命名 `{sanitized_title}-{6位hex}`（标题变更时重命名目录），冲突时循环追加序号
- md 文件用**相对路径** `assets/xxx` 引用图片，Typora/Obsidian 直接打开可渲染
- 磁盘相对路径 → API 绝对 URL 由 `rewrite_asset_urls(content, tx_id)` 统一改写（`app/storage/store.py`），所有内容类 API 返回前调用
- 删除文档 = `rmtree(doc_dir)`，自然清理所有产物

### Store 接口

```python
class Store:
    # Transcript（rawText 在 {doc_dir}/transcript.md，元数据在 index.json）
    get_transcript(tx_id) -> dict | None
    save_transcript(tx) -> bool           # 含去重检查
    save_transcript_full(tx_data) -> bool # 覆盖 + 标题变更时重命名目录
    find_by_url(url) -> dict | None       # URL 去重
    delete_transcript(id) -> bool         # rmtree(doc_dir)

    # Markdown（两版制：{mode}.md + {mode}.prev.md）
    get_markdown(id) -> str | None        # 按 index 查 mode → 判断版本读 .md 或 .prev.md
    get_markdowns_by_transcript(tx_id, mode="") -> list
    save_markdown(mv: MarkdownView) -> bool

    # Process（{doc_dir}/process/{mode}.md）
    get_process_result(id) -> str | None
    get_process_results(tx_id) -> list
    save_process_result(result) -> str

    # Conversation（不变）
    get_conversation(conv_id) -> dict | None
    list_conversations() -> list
    save_conversation(conv) -> str
```

### 并发安全

`threading.Lock` 保护 RMW（Read-Modify-Write）操作——读取索引→修改→写回是原子操作。

### 关键决策

- **版本管理**：同 mode 保留最近 2 版（`.md` + `.prev.md`），前端 v2/v1 切换器
- **处理结果覆盖**：同 mode 覆盖不版本化（OCR 确定性的）
- **去重两层**：URL 去重 + SHA256 contentHash 去重（空文本固定 hash 需在 TextAdapter 层防碰撞）
- **索引重建**：`_rebuild_index` 扫描根目录含 `transcript.md` 的目录反推

---

## Layer 3：生成层

### 调用链

```
POST /generate/{id}/stream
  → resolve_model(config, model_id)  # 唯一解析入口
  → ContentPreparer.prepare()        # VL截图 + 图片剥离 + 截断
  → PromptBuilder.build_polish/notes_prompt()  # 按 source_type 派发
  → LLMService.stream(prompt, cancel_event)    # OpenAI-compatible SSE
  → SSE → 前端逐 token 渲染
```

### 模块

| 模块 | 文件 | 职责 |
|------|------|------|
| `resolve_model` | `core/model_resolver.py` | 唯一模型解析入口，多模态能力自动检测 |
| `ContentPreparer` | `generation/content_preparer.py` | VL截图、图片剥离、文本截断 |
| `PromptBuilder` | `generation/prompt_builder.py` | 按 source_type 构建 prompt |
| `Generator` | `generation/generator.py` | 调度双产物并行生成 |

### 双产物并行

```python
polished_mv, notes_mv = await gen.generate_both(
    raw_text, title, style, source_type, formats, vl_config, ...
)
# 内部 asyncio.gather 并行跑 polished + notes
```

### Prompt 派发

| source_type | notes prompt | polished prompt |
|-------------|-------------|-----------------|
| video/webpage | 通用 prompt | 通用 prompt |
| pdf/document | 文档 prompt（表格修正、OCR修正、公式LaTeX化） | 文档 prompt |
| excel | 分析报告 prompt | 数据摘要 prompt |

### 续写机制

`finish=length` → 自动续写 3 轮，temperature 递减（0.7→0.5→0.3）。

### 取消

`cancel_event = asyncio.Event()` 传给 `llm.stream()`，SSE 流式端点支持 `_watch_disconnect()` 轮询客户端断连（0.5s 间隔）。

### 关键决策

- **模型解析唯一入口**：`resolve_model()` — generate/chat/process 统一调用
- **流式+取消**：cancel_event 真正中断 token 消耗，取消保存 partial 可续写

---

## Layer 4：处理层

### 图片处理（Process）

`POST /process/{id}` — 4 种模式：extract / table / digitize / understand

调度优先级：
```
model_id → scene_models.image_process → scene_models.generate → Engine 回退
```

即：LLM 优先（多模态 VL），不可用时降级到引擎（OCR）。

### 事实验证（FactCheck）

三阶段管道：
```
1. 提取事实断言（LLM 从内容中抽取 claim 列表）
2. 逐条搜索验证（web search + fetch → 找证据）
3. LLM 交叉核查（证据 vs 断言 → 真/假/不确定）
4. 生成 Markdown 报告（FactCheckEngine）
```

验证源优先级：已有 notes → 已有 polished → 原始转录

SSE 流式：`extract → verify (逐条) → report (流式)`

### 陷阱

- Process 端点无取消机制（应加 `cancel_event`）
- verify 阶段限速（每条搜索间隔 1s）

---

## Layer 5：导出层

详见 [export-pipeline.md](export-pipeline.md)。

---

## 横切关注

### 并发控制

`ExecutionGuard` 双层信号量：

```python
class ExecutionGuard:
    cloud_semaphore = Semaphore(5)   # generate/process/cloud engines
    local_semaphore = Semaphore(2)   # local ASR (faster-whisper)
```

**设计意图**：本地慢操作（CPU 密集的 faster-whisper）不抢占云端快操作（API 调用）的槽位。

| 操作 | 端点 | 使用信号量 |
|------|------|-----------|
| Generate | `/generate/{id}` + `/stream` | cloud |
| Process | `/process/{id}` | cloud |
| Retranscribe | `/transcripts/{id}/retranscribe` | **local**（曾错误使用 cloud，已修复） |

**陷阱**：`acquire()` 必须在 `try` 紧接之后，否则异常泄漏信号量（generate.py stream 已修复）。

### 配置系统

- **存储位置**：`~/.limo/config.yaml`（绝对路径，自动创建目录）
- **读写入口**：`config_io.py` → `load_config()` / `save_config()`
- **原子写入**：`tmp_path` → `os.replace(tmp_path, config_path)`

**陷阱**：
1. `DEFAULT_CONFIG.copy()` 是浅拷贝，必须用 `copy.deepcopy()`（已修复）
2. `merge_sensitive` 保护 `***` 不被当作真实值覆盖
3. 保存失败时 in-memory state 与文件不一致（极端情况）

### 错误体系

```python
class AppError(Exception):
    code: str
    detail: str
    status_code: int = 400
    recoverable: bool = True

class FatalError(AppError): ...     # 不可恢复
class RetryableError(AppError): ...  # 可重试
class TranscribeError(AppError): ... # 转录特定
class GenerateError(AppError): ...   # 生成特定
```

前端 `apiFetch` 自动解析非 2xx 响应为 `ApiErrorException`。

**迁移状态**：✅ 全部 11 个路由模块已统一 AppError 体系（2026-07-25 完成 config/model/files 最后迁移），0 处旧格式残留。

### 安全

| 机制 | 实现位置 | 说明 |
|------|---------|------|
| SSRF 防护 | `utils/url_security.py` | 14 个内网/保留 IP 段黑名单 + DNS 二次校验 |
| 路径遍历 | `_safe_filename()` + `resolve().relative_to()` | 双重校验 |
| 敏感信息 | `mask_sensitive` / `merge_sensitive` | LLM Key/Cookie/Tavily Key 脱敏 |
| XSS | `markdown-components.tsx` `<a>` 组件 | 拒绝 `javascript:`/`data:` 协议 |
| SSL (PyInstaller) | `main.py` 启动时 | `certifi.where()` → `SSL_CERT_FILE` |
| 代理注入 | `main.py` 启动时 | `config.fetch.proxy` → `HTTP_PROXY`/`HTTPS_PROXY` |

### 日志

- `contextvars.ContextVar` 注入 `request_id`，全链路追踪
- `MemoryHandler` 缓存最近 1000 条，前端 LogPanel 轮询 `GET /api/logs`
- `log.warning(msg, **kwargs)` 只接受 1 个位置参数，多参数用 f-string

---

## 前端分层

### 组件树

```
App
├── GenPanel            # 导入 + 生成选项（唯一导入入口）
│   ├── ImportSection   # URL/文本/文件输入
│   ├── EngineSelect    # 引擎绑定
│   ├── GenerateOptions # 模型/格式/风格/产物
│   └── ImageModeCards  # 图片处理模式选择
├── DocLibrary          # 文档列表/搜索/筛选
├── PreviewArea         # 预览 + 多 tab（按 sourceType 分派）
│   ├── TranscriptView  # 时间轴/转录
│   ├── EpubSkeletonPanel # EPUB 章节树
│   └── MindmapView     # 思维导图
├── AIPanel             # AI 对话
│   └── ChatCore        # 对话核心
├── TaskPanel           # 浮动任务管理
├── ExportMenu          # 快捷导出按钮
├── ExportPanel         # 自定义导出面板
├── LogPanel            # 日志查看
└── SettingsPage        # 设置（overlay）
    └── ProviderSettings # LLM/引擎配置
```

### 状态管理

详见 [frontend-patterns.md](frontend-patterns.md)。

关键点：
- Zustand store 按域拆 7 slices（`store/themeSlice.ts` 等），`store/index.ts` 只剩组合层，类型在 `store/types.ts`（`Store` 为交集）
- `processingTasks` 用 `${docId}__${kind}` 键格式，`setProcessing(key, null)` 前缀树形删除
- 持久化为手写 localStorage（`mbw_*` 9 个 key），**无 persist 中间件**；LLM/引擎配置在后端 `config.yaml`，不由前端持久化
- API 客户端按域拆分（`api/core.ts` + 7 域文件，`client.ts` 是 barrel）；内容源统一走 `lib/contentSources.ts`（`TabDef.contentSource` 驱动）

### 设计决策

- **GenPanel 类型门控**：5 种 sourceType × 5 子标签，每种类型只显示相关选项
- **PreviewArea 按 sourceType 分派 tab**：视频 3tab / 文档 3tab / 图片 5tab / ...
- **设置页 overlay**：`fixed inset-0 z-[10000]` 覆盖而非替换 render tree，避免 GenPanel 卸载丢失任务状态
- **操作互斥**：`lockOtherOps = retranscribing || imageProcessing` 防止并发操作冲突
- **Ctrl+G 保护**：操作进行中拦截快捷键+Toast 提示
