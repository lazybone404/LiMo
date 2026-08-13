# 分层架构

> 写于 2026-07-10（2026-08-14 更新：引擎计数/路由计数/配置入口/错误残留 + 补齐 FTS5 搜索、Slot 注册表、启动时序等零文档模块）。每一层是什么、有哪些模块、模块之间怎么交互、关键设计决策和已知陷阱。

---

## 设计哲学

- **口子原则（Keep core lean）**：核心只保留轻量、稳定、可控的能力；重依赖/协议/难维护/法律风险的能力走插件口子（`~/.limo/plugins/`）。已落地：whisper / Tesseract / docling 移出核心，pymupdf4llm 进程外插件。详见 [CLAUDE.md](../CLAUDE.md)
- **失败显式化（No silent degradation）**：引擎绑定只取第一个，失败直接报错（无引擎降级链）；扫描件 OCR 失败占位带原因

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
│ 10 个内置引擎：ASR×2 / OCR×1 / Doc×7（+插件引擎）    │
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
│ AI 审核 (静态分析→验证规划→证据收集→报告)             │
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

注册顺序决定优先级：Video → Document → Image → Webpage → Text（兜底）。`AdapterRegistry.register()` 一行注册（存**类**），`AdapterRegistry.route(input)` 命中前实例化返回**一请求一实例**——防止并发导入共享 `cookies`/`_engine_config`/`cancel_event` 互相污染（2026-08-02 从单例改为存类实例化）。

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
EngineRegistry.register("asr", "bcut-asr", BcutASREngine(...))
EngineRegistry.register("doc", "pypdfium2", PyPdfium2Engine(...))
EngineRegistry.register("ocr", "dashscope-ocr", DashScopeOCREngine(...))
```

插件引擎（AGPL 隔离）：`plugin_loader.py` 从 `~/.limo/plugins/` 加载 `EnginePlugin` 声明，进程外 subprocess 模式（command 指向子目录 venv）动态注册进 EngineRegistry，capabilities 的 `fileTypeMap` 按插件 `file_types` 自动追加。

`self.capabilities` 声明引擎能做什么：`text`, `table`, `image`, `pdf`, `office`。

### 三类引擎

| 类别 | 引擎 | 类型 | capabilities |
|------|------|------|-------------|
| **ASR** | dashscope-asr | 云端 | text |
| ASR | bcut-asr | 免费云 | text |
| **OCR** | dashscope-ocr | 云端 | text |
| **Doc** | pypdfium2 | 本地 | text, table, image, pdf |
| Doc | markitdown | 本地 | text, table, image, office |
| Doc | python-docx | 本地 | text, table, image, office |
| Doc | python-pptx | 本地 | text, table, office |
| Doc | openpyxl | 本地 | text, table, office |
| Doc | ebooklib | 本地 | text (EPUB章节) |
| Doc | pdf-image | 本地 | image（每页渲染 PNG 嵌入 MD，供 OCR/多模态） |
| Doc (插件) | pymupdf4llm | 进程外 | text, table, image, pdf |

> Doc 引擎源码已拆分为独立文件：`doc_types.py`、`doc_markitdown.py`、`doc_docx.py`、`doc_pptx.py`、`doc_excel.py`、`docling.py`（含 PyPdfium2Engine）、`epub.py`。pdfplumber 为 pypdfium2 的表格辅助，不单独注册。**DoclingEngine 已移除（2026-08-03，依赖 torch ~2GB 无法打包，打包版一直不可用）**；text_only 变体引擎（docling-text/python-docx-text/python-pptx-text）也已删除（与完整版重复无意义）。AGPL 引擎不内置，pymupdf4llm / tesseract 以插件形式加载。
>
> **pdf-image 引擎（2026-08-04 新增）**：绑定 `doc:pdf` 替代文字提取——用 pypdfium2 渲染每页为 PNG 嵌入 markdown（供 OCR/多模态），对纯图片/扫描 PDF 更稳（绕开「提取嵌入图的坏图」问题）。`document.py` 会跳过其重复渲染 + OCR（图片已内嵌）。

### TranscriptResult 契约（engines/transcript.py）

**ASR 引擎统一返回类型**（bcut-asr / dashscope-asr 复用，从已移除的 faster-whisper 抽离）：

```python
@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

@dataclass
class TranscriptResult:
    language: str
    duration: float
    segments: list[TranscriptSegment]   # 时间轴（视频）
    full_text: str = ""                 # 拼接全文
```

Doc 引擎**没有**统一数据类——各引擎直接产出 Markdown 字符串 + 媒体清单（图片），由 `_resolve_doc_engine` 分发后进入 `save_transcript`。

### 引擎绑定

通过 `engine_bindings` 配置，按子场景绑定：

```yaml
engine_bindings:
  asr: dashscope-asr
  ocr: dashscope-ocr
  doc:pdf: pypdfium2
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
| .pdf | `doc:pdf` | 绑定链 → 扩展名兜底 pypdfium2 |
| .docx | `doc:word` | python-docx → markitdown |
| .pptx | `doc:ppt` | python-pptx → markitdown |
| .xlsx | `doc:excel` | openpyxl → markitdown |
| .epub | `doc:epub` | ebooklib → markitdown |

> 绑定值支持三种格式：字符串（`"pymupdf4llm"` 直接查注册表）、整数（engine_models id）、列表（旧格式，取首个）。`init_engines` 启动时清理失效 key 与无效 id（回退默认引擎）。**绑定只取第一个可用引擎，失败直接报错**（引擎自动降级已移除）。扩展名兜底是默认行为。

### 五类文档差异总览

> 合并自原 doc-transcript-design.md（已归档删除）。Word/PPT/Excel/EPUB 的提取策略与产物差异。

| 维度 | PDF | Word | PPT | Excel | EPUB |
|------|-----|------|-----|-------|------|
| 转录引擎 | pypdfium2 | **python-docx** | python-pptx | openpyxl | ebooklib |
| 转录产物形态 | 文字+表格+图片 MD | 文字+表格+图片 MD（文档流） | 按 Slide N 要点 MD | 按 sheet 表格 MD | 按章节 MD |
| 转录主要问题 | OCR错字/表格错位 | 断段/标题层级不准 | 要点碎片化 | 纯数据无叙述 | 内容量巨大 |
| 内容组织 | 按 Page N | 按文档流（标题层级） | 按 Slide N | 按 sheet | 按章节 |
| 笔记风格 | IMRaD/商务/教程/会议 | 商务/会议/精要 | 商务/教程/精要 | 数据摘要/详细 | 精要/教程/精简 |
| 整理版原文重点 | 表格修正+OCR修正+公式 | 段落还原+标题修正+表格格式化 | 要点合并+备注 | 表格规范化 | 章节保留+去噪音 |
| 特殊格式选项 | 公式集/参考文献 | toc/summary | — | — | 天然TOC |
| 上下文超限风险 | 低 | 低 | 低 | 中（大表格） | 高 |

**提取策略要点**：Word 按**文档流顺序**组织（无「页」概念，标题层级分段，合并单元格用 `colspan`/`rowspan` 标注，文本框加 `**[文本框]**` 前缀）；PPT 天然页结构，每页标题+要点，演讲者备注是额外上下文；Excel 每 sheet 一张 MD table，笔记本质是数据分析结果；EPUB 内容量大需分章处理。

### 引擎评判标准

> 合并自原 design/engine-standards.md（已归档删除）。判断引擎好坏的唯一标准是**职责是否兑现**。

每个引擎通过 `self.capabilities` 声明自己能做什么：`text`（提取文字按阅读顺序、标题层级保留）、`table`（表格正确转 MD）、`image`（嵌入位图全量提取）、`pdf`/`office`（对应格式支持）。**宣称了就必须做到**，做不到就从 capabilities 里移除——这是及格线。引擎实例 `copy.copy()` 防 api_key 串用（多请求并发时各用各的）。

### 关键决策

- **线程安全**：引擎实例 `copy.copy()` 防 api_key 串用（多请求并发时各用各的）
- **Tesseract 移除**：本地 OCR 已从内置移除（2026-08-03，体积修剪 + 插件化），需本地 OCR 走三方插件模板 `examples/tesseract_plugin.py`
- **模糊帧过滤**：`frame_quality.py` — Laplacian 方差 + 熵 + 边缘密度，丢弃低质量帧

---

## Layer 2：存储层

### 目录结构（v3 按文档聚合）

```
data/
├── index.json                  # v3 索引（transcripts 含 dir_name + source_file_path）
├── config.yaml / config.yaml.bak
├── search.db                   # SQLite FTS5 全文索引（见下方「全文搜索」）
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

### 全文搜索（FTS5，2026-08-04）

`core/search.py` 的 `SearchEngine` 用 SQLite FTS5 虚拟表 `transcripts_fts(id, title, content, tokenize='unicode61')` 建全文索引，`search.db` 与文件系统存储互补（关键词全文搜索已实现；**embedding 语义搜索未做**）。

- **写入**：摄入/重转录时 `engine.index(tx_id, title, raw_text)` 增量更新（先删后插）；删除文档调 `delete`；启动时 `rebuild()` 从零重建
- **CJK 处理**：FTS5 `unicode61` 不处理中文分词 → 索引/查询均对 CJK 逐字插空格（「机器学习」→「机 器 学 习」），查询词加前缀通配符 + `AND` 组合；`sqlite3.OperationalError`（查询语法错）回退 `LIKE`
- **结果**：`snippet()` 生成 `<mark>` 高亮上下文片段，按 `rank` 相关性排序
- **端点**：`GET /api/search?q=&limit=`（`routers/search.py`），前端 DocLibrary 顶栏 `SearchBar.tsx`「搜意思不搜字」之外的**关键词全文搜索**入口

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

### 参考文件（Reference Text）

生成笔记时可注入参考资料（如评论区观点）。流式 GET 无法传大 JSON → `POST /api/references` 内存暂存（TTL 20 分钟）+ 短 id，生成时传 `reference_ids`，路由层 `_resolve_reference_texts` 转回内容 → **仅 notes** 注入 `<reference_texts>` section（user_request 前）。标签清单见 [prompts-inventory.md](prompts-inventory.md)。

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

**OCR 预处理（`generation/ocr_preprocessor.py`）**：文档图片生成前可选——先用廉价 OCR 模型（dashscope `scene_models.image_process`）提取图片文字**塞回原文**，再用文本 LLM 生成。与「AI 分析图片」（VL 多模态整图理解）是两条独立路径。

调度优先级：
```
model_id → scene_models.image_process → scene_models.generate → Engine 回退
```

即：LLM 优先（多模态 VL），不可用时降级到引擎（OCR）。

### AI 审核（原事实验证）

四阶段管道（`ReviewEngine`，`app/generation/review.py`）：
```
1. Static Review — 提取 Claim + 分类 Fact/Data/Opinion + 内部一致性 + 风险评分
2. Verification Planning — LLM 批量决策验证策略
3. Evidence Collection + Review — 按需搜索 + 多维度评估
4. AI Review Report — 红黄绿报告 + 可信度 + Action
```

验证源优先级：已有 notes → 已有 polished → 原始转录

SSE 流式：`static_review → verify → review → done`

### 对话管道（Agent + Chat）

- **端点**：`POST /api/chat` + `GET /api/chat/stream`（SSE 8 事件：meta → compressed → agent_status → tool_start → tool_result → thinking → text → done/error；引用不独立成事件，随 `tool_result.refs` 携带）。完整协议见 [对话协议.md](对话协议.md)
- **Agent 循环**：`llm/agent.py`（`stream_run`）+ `agent/tools.py` 5 工具（search_docs/lookup_doc/list_docs/search_web/fetch_url），`tool_registry.py` ToolDef 注册
- **会话管理**：`agent/session.py` 的 `SessionManager`——会话 CRUD + `append_user_message`/`append_assistant_message` + 上下文边界（clear-context）
- **上下文预算**：`core/chat_context.py`（compute_budget/trim_history/build_doc_context）+ `agent/compress.py`（超阈值 LLM 摘要早期消息）

- 全部长时端点（generate/ingest/retranscribe/fact_check/chat/process）复用 `disconnect_watcher` + `cancel_event`（见 `app/core/disconnect.py`）
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
    local_semaphore = Semaphore(2)   # local CPU engines (doc etc.)
```

**设计意图**：本地慢操作（CPU 密集的文档解析）不抢占云端快操作（API 调用）的槽位。

| 操作 | 端点 | 使用信号量 |
|------|------|-----------|
| Generate | `/generate/{id}` + `/stream` | cloud |
| Process | `/process/{id}` | cloud |
| Retranscribe | `/transcripts/{id}/retranscribe` | **local**（曾错误使用 cloud，已修复） |

**陷阱**：`acquire()` 必须在 `try` 紧接之后，否则异常泄漏信号量（generate.py stream 已修复）。

### 配置系统

- **存储位置**：`~/.limo/config.yaml`（绝对路径，自动创建目录）
- **读入口**：`core/config.py:184` → `load_config()`；**写入口**：`core/config_io.py:12` → `save_config()`（**唯一写入口**，业务模块禁止直接 `yaml.dump`）
- **原子写入**：`tmp_path` → `os.replace(tmp_path, config_path)`
- **启动同步**（`config.py`）：`_sync_missing_providers`（补齐 providers 中缺失的内置模型，保证 `llm.api_key`/provider 从旧配置自动迁移）、`_migrate_legacy_dir`（旧数据目录迁移）

**陷阱**：
1. `DEFAULT_CONFIG.copy()` 是浅拷贝，必须用 `copy.deepcopy()`（已修复）
2. `merge_sensitive` 保护 `***` 不被当作真实值覆盖
3. 保存失败时 in-memory state 与文件不一致（极端情况）
4. **`init_engines` 禁止每次启动覆盖配置**：引擎初始化中的 `save_config` 只在「引擎绑定为空」等变更时触发，否则启动会写全量配置、缺失敏感字段（API Key）将永久丢失（2026-08-04 已修复）

### 启动时序（main.py）

```
启动 → 加载配置（load_config）→ 代理注入（HTTP_PROXY/HTTPS_PROXY）
     → SSL 证书（certifi.where() → SSL_CERT_FILE/REQUESTS_CA_BUNDLE，PyInstaller 打包必需）
     → init_engines()（引擎注册 + 绑定清理，仅变更时保存配置）
     → sample_doc（无文档时生成首启示例）
     → SearchEngine.rebuild()（FTS5 索引重建）
     → StaticFiles 挂载（store.root 动态路径）
     → uvicorn :7413
```

### Cookie 解析（core/cookie_parser.py）

平台认证 Cookie 注入：解析 Netscape 格式/JSON/header 字符串三种输入，B 站提取 5 字段、YouTube 提取 7 字段，`_engine_config.cookies` 供下载器/适配器使用。

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

**迁移状态**：✅ 12/14 个路由模块统一 AppError 体系（2026-07-25 完成 config/model/files 最后迁移）。**仍有残留**：`export.py`（9 处）+ `ingest.py`（17 处）直接 `raise HTTPException`（历史遗留，2026-08-14 核实时点）。新增代码统一用 AppError。

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
│   └── SearchBar        # FTS5 关键词全文搜索（GET /api/search）
├── PreviewArea         # 预览 + 多 tab（按 sourceType 分派）
│   ├── TranscriptView  # 时间轴/转录
│   ├── EpubSkeletonPanel # EPUB 章节树
│   └── MindmapView     # 思维导图
├── FloatingAI          # AI 对话浮窗（唯一形态，2026-08-04 移除插槽面板）
│   └── ChatCore        # 对话核心（会话列表/消息/输入，内部栏可拖拽调宽）
├── TaskPanel           # 浮动任务管理
├── ExportMenu          # 快捷导出按钮
├── ExportPanel         # 自定义导出面板
├── LogPanel            # 日志查看
├── OnboardingWizard    # 首次启动向导（三屏：氛围 → 组件检测 → AI 配置）
└── SettingsPage        # 设置（overlay）
    └── ProviderSettings # LLM/引擎配置
```

### 状态管理

详见 [frontend-patterns.md](frontend-patterns.md)。

关键点：
- Zustand store 按域拆 7 slices + 独立 `chatStore` + 独立 `streamBuffers.ts`（模块级 Map，非 slice），`store/index.ts` 只剩组合层，类型在 `store/types.ts`（`Store` 为交集）
- `processingTasks` 用 `${docId}__${kind}` 键格式，`setProcessing(key, null)` 前缀树形删除
- 持久化为手写 localStorage（`mbw_*` 14 个 key），**无 persist 中间件**；LLM/引擎配置在后端 `config.yaml`，不由前端持久化
- API 客户端按域拆分（`api/` 13 文件，`core.ts` 提供 `apiUrl()`/`appUrl()`/`apiFetch`，`client.ts` 是 barrel）；内容源统一走 `lib/contentSources.ts`（`TabDef.contentSource` 驱动）

### Slot 插槽注册表（lib/slotRegistry.ts）

三栏插槽引擎：`SlotDef`（位置 left/right/center + 渲染组件 + 折叠行为）注册即得可拖拽面板：

- **`slotRegistry.ts`**：SlotDef 注册表（`registerSlot()`/`getSlotDef()`），新增插槽面板 = 注册一个 SlotDef，无需改布局组件
- **`SlotLayout.tsx`**：布局引擎（可拖拽 resize，50–600px，<80px 自动折叠为 PanelStrip，`uiSlice.slots` 驱动宽度状态）
- 与 AI 浮窗（FloatingAI，覆盖式唯一形态）正交：插槽管内容面板，浮窗管对话

### 设计决策

- **GenPanel 类型门控**：5 种 sourceType × 5 子标签，每种类型只显示相关选项
- **PreviewArea 按 sourceType 分派 tab**：视频 3tab / 文档 3tab / 图片 5tab / ...
- **设置页 overlay**：`fixed inset-0 z-[10000]` 覆盖而非替换 render tree，避免 GenPanel 卸载丢失任务状态
- **操作互斥**：`lockOtherOps = retranscribing || imageProcessing` 防止并发操作冲突
- **Ctrl+G 保护**：操作进行中拦截快捷键+Toast 提示
