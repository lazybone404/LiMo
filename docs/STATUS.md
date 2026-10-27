# LiMo 项目状态

> 纯事实清单。设计决策见 [CLAUDE.md](../CLAUDE.md)，分层架构见 [architecture.md](architecture.md)。
> 修改历史见 [CHANGELOG.md](CHANGELOG.md)。

**更新**: 2026-10-27 | **版本**: 0.5.19

---

## 规模

| 指标 | 数量 |
|------|------|
| 后端 Python 文件 | 136（11 子目录：adapters 8 / agent 7 / core 33 / downloaders 1 / engines 18 / generation 18 / llm 4 / routers 20 / storage 4 / utils 6 / services 17）+ `limo_cli/` 9（CLI，独立于 `app/`；口径 = `find app -name '*.py'`，不含 `main.py`） |
| 前端 TS/TSX/CSS 文件 | 243（237 ts/tsx + 6 css；业务 154，测试 82，`setup.ts` 1） |
| 前端测试文件 | 83（82 测试文件 + `setup.ts`；1290 个测试用例，vitest 全绿） |
| 后端测试文件 | 94（93 个 `test_*.py` + `conftest.py` 共享 fixture；2026-10 新增 `test_logging_val` /  `test_store_version_layout` / `test_store_legacy_migration` / `test_export_by_version` / `test_proxy_exemption` / `test_youtube_subtitle` / `test_markdown_sections` / `test_export_cards` / `test_artifact_blocks` / `test_export_blocks` / `test_block_prompt` / `test_classify` / `test_classify_paths` / `test_folder_tree` / `test_export_md_images` / `test_export_tab_applicability` / `test_followup` / `test_artifact_followup` / `test_web_search_reference` / `test_chat_web_search` / `test_chat_followups` / `test_export_theme` / `test_chat_answer_mode` / `test_embedding_engine` / `test_embedding_local` / `test_vector_index` / `test_vector_store` / `test_vector_worker` / `test_vector_search` / `test_search_api` / `test_vector_runtime` / `test_deps` / `test_plugin_examples` / **`test_plugin_embedding`（27 条：`EmbeddingPlugin` 契约 / 常驻子进程 IPC / 进程生命周期 / 协议违例四档 / 配置传递 / 自检 / 静态解析与分派，2026-10-27）**） |
| API 路由模块 | 19 业务模块（+ `__init__.py`，共 20 个 .py） |
| API 端点 | 97（`@router.` 装饰器计数；含 `/api/cli/*` 七端点、语义检索的 `/search/vector` 与 `/search/vector/rebuild`） |
| 适配器 | 5 |
| 引擎 (ASR/Doc/OCR/Embedding) | 11 内置（+三方插件 pymupdf4llm、本地方案模板 local_embedding） |
| LLM Agent 工具 | 5 |
| 设计文档 | 19（docs/ 目录 .md 计数：architecture / frontend-patterns / export-pipeline / patterns / search-retrieval / 对话协议 / AGENT-ONBOARDING / user-scenarios / plugins / prompts-inventory / BUILD / qianwen-models / ACKNOWLEDGMENTS / code-audit 1-4 + CHANGELOG / STATUS；design/ 另计） |
| 代码行数 | 后端 app 39,103 + CLI 1,428 / 前端业务 28,516 / 前端测试 15,939（`wc -l` 累加；前端业务 = `src/` 下 ts/tsx **排除 `*.test.*` 与 `setup.ts`**，2026-10-26 按此口径实测重算） |

---

## 适配器

| # | 适配器 | 输入类型 | 引擎依赖 |
|---|--------|---------|---------|
| 1 | Video | URL/文件 (.mp4/.mkv/...) | ASR |
| 2 | Document | URL/文件 (.pdf/.docx/.pptx/.xlsx/.epub) | Doc (+ OCR for 扫描件) |
| 3 | Image | URL/文件 (.png/.jpg/...) | OCR |
| 4 | Webpage | URL (http/https) | 无 |
| 5 | Text | 纯文本 | 无 |

> Video 适配器内含 BilibiliSubtitle / YouTubeSubtitle 字幕优先提取逻辑，不计为独立适配器。

## 引擎

| 类别 | 引擎 | 类型 |
|------|------|------|
| ASR | bcut-asr | 免费云 |
| ASR | dashscope-asr | 云端 |
| Doc | pypdfium2 | 本地（BSD-3 C 引擎） |
| Doc | pdf-image | 本地（整体转图片，供 OCR/多模态） |
| Doc | markitdown | 本地 |
| Doc | python-docx | 本地 |
| Doc | python-pptx | 本地 |
| Doc | openpyxl | 本地 |
| Doc | epub-container | 本地 |
| Doc (三方插件) | pymupdf4llm | 进程外（AGPL 隔离，[仓库](https://github.com/lazybone404/limo-pymupdf4llm-plugin)，非官方维护） |
| OCR | dashscope-ocr | 云端 |
| Embedding | dashscope-embedding / local-bge-small | 语义检索用，**不作用于文件**；2026-10-26 **阶段 1–4 全部落地**：后台 worker 编码**文档块**（增量）+ `/search` 在开关开启时编码**查询串**并与字面结果做 RRF 融合；**默认关**，开启后首次检索前需先建一次向量索引。**两个引擎可选**（`config.search.vector_model`）：云端 `text-embedding-v4` 1024 维按字数计费 / 本地 `bge-small-zh-v1.5-int8` 512 维**免费·零外发**（`engines/embedding_local.py`，`onnxruntime`+`tokenizers` 不随包、模型 22.8MB 在 `~/.limo/models/`）；切引擎必然「口径不符」（1024 ↔ 512）→ 显式提示需要重建，**不自动重建** |

> Doc 引擎源码已拆分为独立文件：[doc_types.py](../backend/app/engines/doc_types.py)（共享类型）、[doc_markitdown.py](../backend/app/engines/doc_markitdown.py)、[doc_docx.py](../backend/app/engines/doc_docx.py)、[doc_pptx.py](../backend/app/engines/doc_pptx.py)、[doc_excel.py](../backend/app/engines/doc_excel.py)、[docling.py](../backend/app/engines/docling.py)（含 PyPdfium2Engine）、[epub.py](../backend/app/engines/epub.py)。pdfplumber 为 pypdfium2 的表格辅助（不单独注册）。text_only 变体引擎已删除。
>
> Embedding 引擎源码：[embedding_base.py](../backend/app/engines/embedding_base.py)（`EmbeddingEngine` Protocol + `l2_normalize`）、[embedding_dashscope.py](../backend/app/engines/embedding_dashscope.py)。⚠️ 它**刻意不继承** `core.protocols.Engine` —— 那个 ABC 的 `execute()` 对本类是永不被调用的空壳。
>
> 引擎计数方式：`EngineRegistry.register()` 调用次数。ASR 2 + Doc 7 + OCR 1 + Embedding 1 = 11 内置（插件 pymupdf4llm / local_embedding 动态注册，不计数 —— 后者与内置 `local-bge-small` 同名、覆盖它，2026-10-27）。faster-whisper 与 tesseract 已从内置移除（分别需自行安装 / 三方插件模板 `examples/tesseract_plugin.py` / `examples/faster_whisper_plugin.py`）；docling 已移除（2026-08-03，依赖 torch ~2GB 无法打包）。

## 管道

| 管道 | 端点 | 触发 |
|------|------|------|
| 导入 | `POST /api/ingest` + `/stream` | URL/文本/文件 |
| 生成 | `POST /api/generate/{id}` + `/stream` | LLM 生成笔记；**版本归档** `{doc_dir}/{mode}/v{N}.md`（一版一文件、全部保留，生成追加新版 / 编辑就地覆盖当前版，2026-10-10 从两版制重做）；**联网检索**（2026-10-12，勾选后生成前 LLM 从正文抽检索词 → 复用 Agent 侧三级降级搜索 → 结果拼成**一份**参考资料走既有 `reference_texts` 通道注入笔记，只注 notes；失败显式 `warning kind=web_search_failed`，自证头 `webSearch`） |
| 处理 | `POST /api/process/{id}` | 图片 OCR |
| 对话 | `POST /api/chat` + `/chat/stream` | AI Agent，5 工具（search_docs / lookup_doc / list_docs / search_web / fetch_url）；**联网开关**（2026-10-12，`web_search=0` 时**整组摘掉** `search_web` / `fetch_url`，system prompt 同步换离线层 `web_tools.md` ↔ `no_web.md` —— 工具表与 prompt 层**由同一个 `allow_web` 驱动**；默认开 = 零回归，与产物侧 `web_search` 默认关**方向相反**、判据同一条）；**`tool_result.sources`**（公网来源 `[{title,url}]` → 工具气泡展开列表可点开；与库内文档 `refs` **是两条路、刻意不合并** —— 形状 / 跳转语义 / 消费方都不同；URL 后端 `_safe_http_url` 抽取阶段丢整条 + 前端 `toChatSources` 纵深防御） |
| 图表 | `POST /api/generate/{id}`（formats=chart）| 生成笔记勾选「图表」→ AI 输出 ```fig 语义 JSON（1~N 张、**prompt 层面 3 种布局** `grid`/`flowchart`/`state`；`_LAYOUTS` 仍保留 10 个算子、删的是引导不是能力，2026-10-20、不写坐标）→ `chart_layout` 算几何 → `chart_svg` 生成 → PIL 本地自绘渲染 PNG 嵌入（`app/services/chart_layout/` 包 + `chart_svg.py` + `chart_renderer.py`；resvg 已切换，Windows 文字 bug；legacy ```chart 手写 SVG 路径保留为回退） |
| EPUB 解读 | `POST /api/epub/generate/{id}` + `GET /api/epub/task/{id}` + `POST /api/epub/cancel/{id}` | EPUB 一键解读（阶段1 逐章 + 阶段2 归并总结 + 中断续跑 + 失败章跳过标注） |
| 文档库 | `GET/POST /api/folders` + `POST /api/folders/ensure-path` + `PATCH/DELETE /api/folders/{id}` + `POST /api/transcripts/batch` | **可嵌套文件夹**（`parentId`，`""` = 根；**纯逻辑分类不映射磁盘目录**，零迁移——读时 `get("parentId", "")`、触碰即补字段，**不 bump `INDEX_VERSION`**）；`ensure-path` 一次建完整条路径（全有或全无，前端**不解析路径**）；`list_folders` 同时给 `count`（直接）与 `totalCount`（含子树）；删除 = 整棵子树一起删、各层文档回落未分类（**绝不删文档**）；CLI `limo list --folder-id X --recursive` 透传 `include_descendants`；批量删除/打标签/移动；`GET /api/transcripts?folderId=` 三态语义（不传=不过滤 / 空串=仅未分类 / id=该文件夹）；`GET /api/search` 全文检索（内存过滤即时 + FTS5 消抖合并，命中并入主列表 + 「原文」角标 + 相关度排序键）+ `GET /api/search/status`（索引健康：`available` / `docCount` / `sizeBytes` / `lastError` / `rebuiltAt`）+ `POST /api/search/reindex`（用户主动重建，带 `vacuum=True`；`X-LiMo-Client` 头守卫）+ **语义检索**（2026-10-26 阶段 3）：`GET /api/search` 返回 `{hits, semantic}`（`semantic: null` = 开关未开；开则恒为 `{error, hits}` —— `hits: 0` 保留以区分「开了但索引还空」与「压根没开」），`semantic.error` 非空时**字面结果照给**且一次编码都不发（`_precheck` 三档：库不可用 / 没建过 / 口径不符）；`POST /api/search/vector`（开/关 + **切引擎**：body `{enabled, model?}`，非法 `model` → 400 `VECTOR_NO_ENGINE` 且零写盘；worker 与融合层**成对**启停）、`POST /api/search/vector/rebuild`（先停 worker → `reset_identity` → 再同步 = 换模型后重建）；`GET /api/search/status` 增 `vector` 段（`enabled` / `reason` / `available` / `docs{total,ok,pending,failed}` / `chunks` / `sizeBytes` / `identity` / **`model` 当前选中（读配置不读 runtime）/ `models` 可选引擎清单**（`label` / `modelId` / `dimension` / `local` / `ready` / `reason`，2026-10-26 阶段 4））。⚠️ **三态且必须能看见**：可用 / 索引不可用（`available=false` + 原因，用户点一下重建即可修）/ 引擎不在场（开发者问题）—— 后两态此前都汇成「搜不到」，与「库里没有」逐字相同。**检索机制**（2026-10-26 实测重做，29 题 41% → 83%）：索引三列 `title` / `body`（UNINDEXED，供摘要）/ `toks`（CJK 二字组，**唯一匹配列**）；索引文本 = rawText + 最新版笔记（`doc_text.search_index_text`，**五个写点共用** `core.search.update_index`）；查询侧二字组 **OR** + BM25（逐字 AND 区分度为零、二字组 AND 一票否决）。⚠️ `_tokenize` 的 `limit` 默认 None —— 曾无条件 `uniq[:64]` 让每篇只索引开头 64 个 token |
| 智能分类 | `POST /api/classify/preview` + `POST /api/classify/commit` | 选中 N 篇 → **对话框先选配置**（取材深度 / 文件夹策略 / 新建上限 / 标签策略）→ 按「开始分析」才发起一次 LLM 调用产出「每篇 → 建议文件夹 + 建议标签」（**零写入**）→ 逐篇审定 → **一次加锁一次写盘**。**打开对话框零请求**（`onStart` 是唯一计费出口）；模型**永不直接写库**；两项策略各三档（`folderMode`：复用或新建 / 只复用 / 只打标签；`tagMode`：全库词表 / 用户手填基础标签 / 完全交给模型），**非法策略显式报错 `CLASSIFY_BAD_OPTION` 且不发起调用**；分类空间**优先复用**既有文件夹（新建上限 5）；取材深度用户选（600/200/仅标题，无笔记篇显式标 `title_only`、被预算缩减标 `shortened`）；**不降级**（超时/坏 JSON 一律显式报错）。新夹由前端在 commit 前用现成 `POST /api/folders` 建，commit 只做存在性校验。⚠️ **同日三轮：分批 + 关思考**——实测 90 篇一次调用时模型把 8192 输出预算**全烧在推理链上**（正文 0 字符、`finish=length`），而报错是误导性的「JSON 不合法」→ 每 **20 篇一批**（`CLASSIFY_BATCH_SIZE`，每批独立组装 prompt，**合并后仍交给同一个 `normalize_suggestions`**，收口逻辑不翻倍）+ **关思考**（`disable_thinking` → `thinking={"type":"disabled"}`；**low 档实测同样烧光**，降档救不了；与 `reasoning_effort` 服务端**互斥**故代码用 `elif`）。全失败 → 整次显式报错；**部分批失败 → 结果照给 + 全局 warning 点名哪几批**；空输出另有 `CLASSIFY_EMPTY_OUTPUT`。双层超时（每批 120s / 整次 600s）。⚠️ **2026-10-18 支持嵌套路径**：模型可建议 `技术/前端`，`newFolderName` 仍是单个字符串（schema 不变）；解析收口 `folder_tree.parse_path`（**整串先逐字命中现有夹名**保护历史数据 → 再按 `/` 切分，`kind ∈ {existing, ambiguous, path, reject}`）；同名歧义**显式化**（`none` + warning，不再按数组取第一个）；新建额度改按**实际新建的文件夹行数**计；口袋夹按**完整路径**分组；前端改走 `ensure-path`（原串透传、不解析）；复审下拉显示路径而非裸名 |
| 多篇合成 | `POST /api/synthesis/stream` | 选中 N 篇（≥2）→ 按 multi 预算组装正文 → 落库为 `sourceType="composite"` 新文档。**只建文档不生成**（综述由用户核对「## 来源文档」后自行点生成，走现成 `/generate/{id}/stream`）。Step 0 分叉：装得下 → Stuff（零 LLM 调用）/ 超出 → Map-Reduce（维度发现 + 逐篇固定维度结构化抽取）。业务失败（篇数不足 / 源已删 / 窗口不够 / 缺 Key / **非法原料模式 / 非法读者意图**）全走 SSE error 事件，HTTP 仍 200——⚠️ 入参清洗必须在 `runner` **内**（写在 `StreamingResponse` 构造之前会让 `normalize_*` 的抛错变成 HTTP 500，前端只看到「连接中断」） |
| 导出 | 9 端点：单文件/全产物/批量/思维导图/对话单篇/对话批量/自定义/SSE进度流/临时下载 | HTML(代码高亮+GFM+时间戳badge+标题锚点+KaTeX字体内联)/MD(默认图片重写为绝对 URL；`?image_md=inline` 时 base64 内嵌为单文件，2026-10-11)/Word(docx 图片内嵌+标题大纲级别)/ZIP(3模式)/思维导图(交互版+SVG)/**PDF**(复用 HTML 产物，无独立端点：Tauri 走 WebView2 PrintToPdf 直接落文件 / 浏览器走打印对话框)。**按版本导出**：`?version=N`（0=最新，查不到**显式报错不回退**），HTML/Word/PDF 三条后端路径共用。**卡片骨架跟随**（2026-10-10；2026-10-11 判据改由标记决定）：`?skeleton=card&card_style=…&card_vars=…`（默认 `paper` = 逐字节零回归），**只对带 `card` 节修饰符的 H2 切卡**（未标记的节原样穿过、与卡片混排），HTML 包 `section.md-card` / Word 包单单元格表格 / PDF 自动跟随。**逐维度微调**（2026-10-11）：`card_vars` = 「底 / 抬升 / 边」三根旋钮的变量覆盖串（前端传**解析好的 CSS 值**，后端只做字符白名单、不持有第二张「档位 → 取值」表；空串不多一字节；Word 侧不接收）。**语义标记跟随**（2026-10-10）：`?marker_decor=0`（默认开=跟随预览）——HTML 把 `limo-block` 段包成 `div.md-block` / Word 包单单元格表格+左边框 / **md 与纯文本恒剥离标记行**（那里没有视觉层）。⚠️ 内容卡片族（`table`/`code`/`image`）**块内原样穿过**（HTML 侧仍是 `<table>` / `codehilite` / `<img>`）；Word 侧 `table` 卡是**表格套表格**（能力边界）。**对话导出**（2026-10-11）：`.md` 剥标记行（单条 + 批量两处）/ `.json` **刻意保留**（可回灌格式，剥了再导入卡片结构就没了）。**同日详细导出审查**：`_resolve_tab_content` 删掉 rawText 回退（图片类 tab 查不到处理结果返回空串 → 不再凭空多出「原图」「OCR」等幻影文件）/ 批量与全产物导出空结果**显式 404**（不再产出空 zip）/ 批量导出 md 同步剥标记行 / 批量与自定义导出 md 走 `_inline_images` / 前端导出 tab 清单滤掉 `contentSource === 'none'`。**导出跟随应用主题**（2026-10-16）：`?theme_mode=&accent=&base=&font=&texture=&texture_opacity=` 六参数，**各轴独立、非空才写 `<html>` 属性**（零参数 = 不写属性、不注入主题层 = 逐字节等于改造前）；后端 `_load_theme_layer_css()` 读**前端同一份** `frontend/src/styles/theme-layer.css`（打包走 `_MEIPASS/styles/`，读不到**显式抛 500**），注入位置在 `HTML_THEMES` **之前**（变量先定义后消费）；`html_theme=limo` 文案改「跟随应用主题」（key 不变、零迁移），`paper`/`github`/`minimal` 三套固定主题仍可选；`_CARD_TOKENS_CSS` 改名 `_EXPORT_NEUTRAL_TOKENS_CSS` 且**只在未跟随时注入**（它不能删 —— `cards.css`/`blocks.css` 的消费者身份与 `html_theme` 无关）；暗色块包进 `@media screen`（「暗色是屏幕概念，纸永远是浅的」，打印一律浅色）；属性值只做**字符白名单**（`^[a-z0-9-]{1,24}$` / 强度 `^(?:0\|1\|0?\.\d+)$`），后端**不持有第二份 key 列表**（那是第二真相源） |

### 共享查询层（2026-10-04，无独立端点）

`app/services/query.py::QueryService` —— **读路径的唯一成形逻辑**（分页 / ID 解析 / 版本编排 / meta 装配 / 截断标记）。它不新增端点，而是被既有读端点共用：`ingest.py`（列表/单篇）、`search.py`、`folders.py`、`generate.py`（`GET /markdown/{id}`）、`export.py`（tab 内容解析）、`markdown_edit.py`（编辑保存时取头/回写头）。

- `app/services/*.py` **零 FastAPI import**（与 `services/epub_pipeline.py` 同层），API adapter 与将来的 CLI 直接 import 同一 service —— 两侧不是「实现相似」，是**同一份**（`tests/test_query_contract.py` 逐字段比对 HTTP 响应与 service 输出）
- 惰性构造在 `core/dependencies.py::get_query_service`：`main.py` 先建 `Store`、后建 `SearchEngine`，提前构造会让 `search_resources` 拿到 `None` → 静默空结果

### 只读命令行（2026-10-04 三期，7 个端点）

`limo` —— 只读 CLI（`backend/limo_cli/`，9 个 .py / 1,356 行），三层：命令层 → `QueryService` → `Store`。**不解析库格式**（不读 `index.json` / `search.db` / 不拼磁盘路径），两个传输层同契约（`HttpSource` 默认 / `LocalSource` 仅 `doctor --local`）。

| 端点 | 用途 |
|------|------|
| `GET /api/cli/status` | 库概览（计数 / 类型分布 / schema 版本） |
| `GET /api/cli/resources` | 文档列表（分页：`limit` / `cursor` / `all`；`include_descendants=true` 连子文件夹一并返回，对应 `limo list --recursive`） |
| `GET /api/cli/resources/{id}` | 单篇元数据（**含 `rawText`**，`assets/` 保持相对路径） |
| `GET /api/cli/resources/{id}/artifacts` | 产物清单（有哪些 notes/polished/extract/…，零内容） |
| `GET /api/cli/resources/{id}/artifacts/{part}` | 产物正文（`version` / `offset` / `max_chars` / `full` / `include_raw`） |
| `GET /api/cli/search` | 全文搜索（FTS5，不分页，超限用 `truncated`）。⚠️ **纯字面、不接语义融合** —— 融合是异步编排（要读向量矩阵 + 编码查询串），而 `QueryService` 是同步纯读层；CLI 侧要等这条边界被打开才有语义检索 |
| `GET /api/cli/folders` | 文件夹清单（每项 `id` / `name` / `parentId` / `count` 直接数 / `totalCount` 含子树；CLI 人类输出按前序缩进） |

- **七命令**：`status / list / search / get / export / folders / doctor`；**无写操作**
- **退出码** `0/2/3/4/5/6` + 130：区分「空结果」与「坏输入」；判据按响应 `code` 而非 HTTP 状态
- **门禁** `scripts/check-cli` 16 项（独立端口 7419，含 3 条负向对照）
- **契约文档**：库内 `~/.limo/AGENTS.md`（`limo doctor --write-agents`）+ 仓库根 `AGENTS.md`

### 密钥与凭据卫生（2026-10-04 二期，无独立端点）

`config.yaml` **不再落明文凭据**：值在 `%LOCALAPPDATA%\LiMo\secrets.bin`（DPAPI 加密，`ctypes` 直调 `crypt32.dll`，零新依赖），YAML 只留 `secrets_ref: dpapi:limo`。

- **四张成对表**收口 `core/config_io.py`：`SECRET_SCALARS` + `SECRET_LISTS`（抽↔填）、`DERIVED_AUTH_SUFFIX`（剥↔重算）、`fold_to_providers`（下沉↔跳过）、`_EMPTY_CONTAINERS`（键名↔类型，2026-10-04 新增）
- **读侧实时派生**：`GET /api/config` 不返回 `auth.*_parsed`（历史三写时代的 cookie 副本，含 yt-dlp 可用内容），改回摘要 `{platform, cookieCount}`；`cloud.*` / `fetch.proxy` 一并脱敏
- **自检两处，分工不重复**：启动期 `config_io.startup_check`（查 DPAPI + **磁盘** `config.yaml`/`.bak` 有无明文）｜ 全量 `scripts/check-secrets.py`（遍历数据目录做内容侧扫描）
- **迁移工具** `scripts/migrate-secrets.py`：`--check`（零写入）/ 迁移（幂等）/ `--rollback`；**不写明文备份**；退出码三态 `0/1/2`

| 脚本 | 用途 |
|------|------|
| `scripts/sync-version.py` | 版本号 7 处同步 |
| `scripts/check-models.py` | 模型预设核对（开发期） |
| `scripts/check-secrets.py` | 凭据卫生自检 |
| `scripts/migrate-secrets.py` | 明文凭据迁移 / 回滚 |
| `scripts/check-cli` | CLI 契约门禁（16 项，自启 7419 端口后端） |
| `scripts/check-doc-refs.py` | 文档交叉引用校验（门禁 `docs` 层；`--selftest` 9 条自证判据有区分力） |

## 前端组件

| 组件 | 说明 |
|------|------|
| ActivityBar | 左侧活动栏（常驻最左竖排图标列）：面板项**从 `SlotRegistry.grouped().left` 派生**（加一个面板自动多一个图标），两个动作项 AI 对话 / 设置；左侧面板默认**互斥切换**（`layout-prefs.ts`，可在设置→外观切回并排）。顶栏不再有面板按钮 |
| GenPanel | 导入 + 生成选项（epub 专属一键解读入口 EpubInterpretPanel，三态 UI；思考档位三档 + PDF 直传开关，均由模型能力位门控） |
| DocLibrary | 文档列表/搜索/筛选（拆分 **18 文件**：编排 DocLibrary + DocLibraryToolbar + SearchField + FilterArea/FilterSection/filters/FilterPopover 筛选区 + ListToolbar 排列/排序旋钮 + DocList/GroupHeader/DocRow 列表 + BatchBar 批量条（**含多篇合成入口 + 智能分类入口**）+ ClassifyDialog 分类审定对话框 + hook 层 useDocQuery/useDocSelection + docLibraryPrefs 偏好 + docMeta 元信息 + entryDelay 动画延迟） |
| PreviewArea | 预览 + 多 tab（**文档标签页 `DocTabBar`**：VS Code 双标签模型——单击=预览/双击=常驻，延后提交与未保存守卫协同；**正文字号缩放** Ctrl+滚轮 / Ctrl+± / Ctrl+0，只作用于 `.preview-area`；空态欢迎文档三产物：笔记/整理版原文/转录；产物编辑三态弹窗保护 + 编辑中未保存拦截） |
| FloatingAI | AI 对话浮窗（唯一形态；位置/尺寸记忆、内部栏拖拽调宽；含 ChatCore/ChatSidebar/MessageList/MessageItem/ChatInput） |
| SettingsPage | 模型/引擎/配置（overlay，含对话上下文设置 + 模型元数据编辑）；六轴主题选择（色块走 `data-*` + `var()`，**JS 不存色值**；纹理色块显示真实纹理；预设卡与向导同款） |
| TaskPanel | 浮动任务管理（可拖拽、可取消） |
| ExportMenu | 快捷导出（MD/HTML/Word/PDF/ZIP/复制）；`backendParams()` 一并带六条轴参数（HTML/Word/PDF 共用） |
| ExportPanel | 自定义导出（tab选择/格式 MD·HTML·Word·纯文本/主题）+ SSE 进度反馈；POST body 同样带六条轴参数，主题下拉 `limo` 项文案为「跟随应用主题」 |
| EngineSelect | 引擎绑定下拉（含"当前引擎"标签） |
| LogPanel | 实时日志查看 |
| OnboardingWizard | 首次启动向导（三屏） |

## 进度

| 域 | 完成 | 总计 | 进度 |
|----|------|------|------|
| 内容导入 | 8 | 8 | 100% |
| 文档库 | 10 | 10 | 100% |
| 多篇合成 | 16 | 16 | 100% |
| 转录 & 引擎 | 8 | 8 | 100% |
| LLM 生成 | 14 | 14 | 100% |
| 图片处理 | 4 | 4 | 100% |
| AI 对话 | 10 | 10 | 100% |
| 图表生成 | 11 | 12 | 92% |
| 导出 | 11 | 12 | 92% |
| 前端 UI/UX | 21 | 21 | 100% |
| 安全 & 运维 | 8 | 8 | 100% |
| 产物复用 | 4 | 5 | 80% |
| **合计** | **124** | **127** | **98%** |

> 进度说明：
> - 产物复用（4/5）：已落地**产物自证头 + 共享查询层**（一期）、**密钥与凭据卫生**（二期）、**CLI + 契约文档 + 门禁**（三期），均 2026-10-04；待做**导出 meta 承载 + 渠道扩展**（四期）。路线图见 [CHANGELOG.md](CHANGELOG.md) 2026-10-04 条目
> - AI 审核域已移除（2026-08-16，方案：图表功能与 fact_check 移除）——事实核查产物转历史，历史 fact_check.md 保留不删
> - 导出：11/12 — **打印样式 + PDF 导出已落地**（2026-09-21）：Tauri 内走 WebView2 PrintToPdf 直接落文件（**真实文档已端到端实测**，期间修掉 `Content-Disposition: attachment` 导致打印白纸的伪成功），浏览器内退回打印对话框（原「打印 / 另存为 PDF」与「PDF 一键导出」两行已合并为一，因为它们是同一个按钮的两条分支）；待完成**图片预处理复用**（ZIP 导出复用导入时的 optimize_images 结果）与 **md 公式渲染修复**（`formula_md=render` 依赖不在依赖树里的 matplotlib，自上线起从未生效）
> - 不做的项目：PDF Layer 2（pdfplumber 已成熟提取表格）、VL 帧过滤（成本/收益不成正比）

## 工程质量（2026-10-16 状态）

| 指标 | 状态 |
|------|:--:|
| FastAPI Depends 依赖注入 | ✅ 18/19 路由用 `Depends`（未迁：generate/ingest/markdown_edit；`references.py` 本就不需要。`search.py` 2026-10-26 已迁——那残留的一处 `app.state` 在 `markdown_edit.py` 的 `update_index`，`getattr` 兜底是有意的） |
| disconnect_watcher 公共工具 | ✅ 6 端点统一（新增 classify/preview），修复 2 个 bug |
| 错误体系统一（AppError） | ✅ 19/19 路由全量完成，0 处 `HTTPException`（2026-10-11 复核） |
| 前端测试 | ✅ vitest + 1284 tests（81 个测试文件 + `setup.ts`：store/theme/api/contentSources/streamBuffers/tabRegistry/chatStore/docQuery/genParams/docLibrary/tagSuggest/文档库 6 个/contextMenu/组件/synthesis/getTypeMeta/docLibrarySynthesis/classifyDialog/readingPrefs/readingLayout/**chatFont（对话字号，含与阅读字号互不干扰的双向断言）/chatMessageList（滚轮 / 回到最新 / 代码块复制）/chatWebSearch（联网开关 + 公网来源呈现：开关→请求参数、两开关互不影响、URL 安全、四种 rebuild 不丢来源）/searchHealth（索引健康横幅：渲染判据 / 懒探测 / 重建成功与失败 / 关闭 / 30s TTL / 取不到状态不误判，2026-10-26）**/**语义检索退化的两条提示不混（`docLibrarySearch` 2 条：`semantic.error` 出提示且**不**冒充检索失败 / 未启用或零命中零渲染）与 503 强制刷新（`docLibraryLayout` 1 条：`ApiErrorException.status=503` → 刷新健康状态 → 横幅浮出；判据只读 `status`，对照实验改回 `message.includes('503')` 即红，2026-10-26）**/docTabs 3 个/字号、接线与快捷键守卫 3 个/版本切换与预览同步 2 个/卡片偏好、切分器、骨架组件 3 个/标记解析、偏好、组件、导出接线 4 个/HTML 注释 1 个/**产物元数据 2 个**/导出偏好 1 个/**themeAxisSync（轴注册表 key 集合 ↔ CSS `[data-*]` 跨语言一致，2026-10-16）**/onAccent/installWarning） |
| 后端测试 | ✅ 94 文件（含**插件 2 个**：plugin_loader（39 条：门控 / 一键装依赖 / 进程内三条边界）/ **plugin_embedding（27 条：`EmbeddingPlugin` 契约 / 常驻子进程 IPC / 进程生命周期 / 协议违例四档 / 配置传递 / 自检 / 静态解析与分派，2026-10-27）**；含**向量检索 8 个**：embedding_engine（三条契约：按 `index` 重排 / 已 L2 归一化 / 空输入不发起调用）/**embedding_local（16 条：协议三条契约 / 条数与维度不符显式报错 / 不阻塞事件循环 / 并发预热只构造一次 / `precheck` 缺依赖与缺模型两种文案 / 构造零副作用 / 真模型已归一化，2026-10-26）**/ vector_index（切块 flat+h2 / 分组排名 / rrf，产品与评测共用同一份）/ vector_store（四元组身份 / 状态机 / 块读写）/ vector_worker（块级 hash 增量 / 每篇 checkpoint / 失败上限 / 启动对账 / **预热：只调一次、失败写 `stopped` 而不 `mark_failed`、修好后下一轮重试** / **模型内存三档：`never` 不动 / `idle` 交给引擎判闲置 / `immediate` 只在建完一轮之后放一次（空转轮次不动，否则每次搜索都冷启动）**）/ **vector_runtime（11 条：语义检索开关的唯一维护点 —— **六条「不在用」路径都释放模型（`never` 档也释放，关功能 ≠ 平时常驻）** / 重建与重启路径**不**释放 / 策略变了才重启 worker / 停机清干净 `vector_stop`·`vector_task`·`vector_worker` 三个引用 / 库不可用·引擎找不到·口径不符各有出口，2026-10-26）** / **vector_search（24 用例：`_precheck` 三档拦在编码之前 / 两路同深度 / RRF 只吃排名 / 超时与失败显式化 / `hits` 恒在，2026-10-26）** / **search_api（10 条：`POST /search/reindex` 走 HTTP 真的灌满（篇数 + 可搜性两条都断）/ 陌生 Origin → 403 且索引未被重建 / 向量未初始化时状态段给出明确原因——此前这两个端点**零 HTTP 层覆盖**；**+4 条引擎切换（2026-10-26 阶段 4）：非法 `model` → 400 `VECTOR_NO_ENGINE` 且零写盘 / `status.models` 两个引擎都在且 `local` 标志由引擎给出 / 切到本地引擎后 `model` 与成本分支跟着变 / 切引擎不改 `vector_enabled` 也不自动重建（必然口径不符、`reason` 明说要重建）**；**+3 条释放策略：`/search/status` 回报当前档 / 开关请求里的 `release` 真的落进配置并重启 worker / 默认配置的档位与 `DEFAULT_RELEASE` 同源**）**；合成 6 个：transcript_meta/budget/store_composite/synthesis/generate_gate/synthesis_api；查询层 3 个：artifact_meta/query_service/query_contract；密钥 3 个：secrets/config_mask/config_io；版本布局 2 个：store_version_layout/store_legacy_migration；导出与网络 8 个：export_by_version/proxy_exemption/youtube_subtitle/markdown_sections/export_cards/export_md_images/export_tab_applicability/**export_theme（导出跟随主题，18 用例：零回归 / 五条轴属性 / 强度走行内 / 各轴独立 / 白名单边界 / 注入闸门 / 主题层两个 `@media screen` / 跨语言成对守卫，2026-10-16）**；**产物标记 3 个**：artifact_blocks/export_blocks/block_prompt；**智能分类 1 个**：classify，57 用例；**联网检索 1 个**：web_search_reference，39 用例（纯函数 / 检索层 / 编排 / 端点四段，含 9 条对照实验 + `search_web` 返回结构的源码哨兵；2026-10-12 审查后补「整体超时闸门 / 取消透传（起跑前、检索中）/ 显式关闭联网」5 条）；**对话联网 1 个**：chat_web_search，38 用例（工具表开关 / prompt 五层叠加 / `_safe_http_url` / `_extract_sources` / 落盘白名单 / 接线守卫六组，含 4 条对照实验，2026-10-12）；2026-10-11 代码审查修 `fit_to_budget` 分批预算 + 每批新建额度 + 空词表兜底；同日五轮/六轮补策略矩阵、候补与口袋夹、回显同源、标签清洗同源，+10）；**检索索引 1 个**：search，含 2026-10-26 新增的 5 条（状态端点 / 坏库不炸构造 / 坏库自愈 + 文件头魔数 / VACUUM 按需（`PRAGMA freelist_count` 精确判据）/ `QueryError.index_unavailable` 与 `no_search_engine` 的对照）） |
| CLI 契约门禁 | ✅ `scripts/check-cli` 16 项全绿（含 3 条负向对照：后端不可达 / 坏 ID / 截断显式化）；`test_query_contract.py` §8 覆盖 `/api/cli/*` 七端点 + 两条对照实验（2026-10-04） |
| 产物自证头 | ✅ `notes`/`polished`/`process` 落盘带 `<!-- limo-meta -->` 首行（从哪来/怎么来的/是什么/可信度边界四条），`core/transcript_meta.py` 拆出 envelope 层统一编解码；历史无头产物**不判损坏**；编辑保存带回原头；思维导图等派生视图剥头（2026-10-04）。⚠️ **预览侧**（2026-10-11）：自证头信息由**产物自证条**（正文上方一横排带图标小标签：版本/时间/字数/模型）呈现，展示判据收口 `lib/artifactMeta.ts`；纯视图装饰、**不参与导出** |
| 共享查询层 | ✅ `services/query.py::QueryService` 收口全部读路径（HTTP 与 CLI **同一份**实现）；顺带修掉存活四个月的 `search.py` 双 `prefix` 导致 `/api/api/search`（前端搜索一直 404 而两侧测试全绿，2026-10-04） |
| 产物语义标记 | ✅ `limo-block` v1（2026-10-10 起，**十二种块 + 一种节修饰符**，2026-10-11 扩至九种、2026-10-20 补 `cycle` / `sequence` / `quadrant`）：模型写进产物正文的协议元素，前端渲染为轻量强调块 / 内容卡片 + 一键隐藏装饰。**十二种块分两族**——九个**语义强调块**（`key-takeaway` / `alert` / `recommend` / `metric` / `steps` / `timeline` / `cycle` / `sequence` / `quadrant`，标「这段文字是什么性质」）+ 三个**内容卡片**（`table` / `code` / `image`，标「整块内容自成一体」；**块内是真表格/真代码/真图片，一个字节不动**，只加外壳）。⚠️ **`card` 不是块而是「节修饰符」**（2026-10-11）：写在 `## 标题` **下一行**、**无闭合标记**，标的是「这一节」——它是骨架轴 `card` 的**唯一触发源**（没有勾选项）。`core/artifact_blocks.py` ↔ `lib/markdown-markers.ts` 前后端各一份、共享 fixture `limo-blocks.json` 钉住。三条不变量（专用标记隔离 / **零残留降级** / 围栏内零触碰）；**无标记产物导出逐字节零回归**。⚠️ 装饰开关（`lib/marker-prefs.ts`，UI 名「内容块装饰」）关掉后内容一字不少、内容卡片**还原成没包过围栏的样子**。⚠️ 顺带修 4 个既有缺陷（导出页 `--tk-*` 令牌注入闸门漏 `paper`+有标记 / Word 把注释泄漏成可见段落 / 合成把标记当模型输入 / `chars` 把标记算进正文字数）；实机验收待用户操作（2026-10-10） |
| 对话语义标记 | ✅ 同一套 `limo-block` 协议、**另一套类型**（2026-10-11）：`card-group`（并列比较 2–4 个同级对象 → 块内 `###` 各成一张并排卡片）/ `follow-up`（2–4 个下一步问题 → 每项一颗 chip，点了直接发送）/ `info`（信息卡）/ `digest`（摘要卡）——后两种同日补，**规范带总量闸门**（四种合计不超两块）。⚠️ 两侧集合**不相交**，拆成 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` / `KNOWN_BLOCK_TYPES = 并集`（「协议知道」≠「某侧实现了」）；⚠️ **只共享判据不共享渲染器**（`ai-chat/ChatMarkerContent.tsx`，不复用产物侧 `MarkerContent` —— 它会波及全部历史消息）；⚠️ 流式**半截标记行整行抑制**（命名空间感知，只在对话且 streaming 时调用）；消费点：存储/LLM 历史/JSON 导出**保留**，`.md` 导出与复制**剥离**；规范走 `agent/prompts/formats.md`（system prompt 第四层）。⚠️ 顺带修**实时流引用 chips 不出现**（`api/chat.ts` 丢了 `tool_result.refs` 又在等一个不存在的 `ref` 事件）；实机验收待用户操作 |
| 只读 CLI | ✅ `limo` 七命令 + `/api/cli/*` 七端点（只读，无写操作）；`--json` 时 stdout 纯 JSON、退出码 `0/2/3/4/5/6` 区分「空」与「坏」、`has_more`/`next_cursor` 与 `truncated`/`continuation` 不混用；契约文档两处（库内 `~/.limo/AGENTS.md` + 仓库根）；顺带修 3 缺陷（坏 ID 退出码分叉 / 子命令选项不可用 / `--all` HTTP 静默退化成第一页）（2026-10-04） |
| 第三轮代码自查 | ✅ 10 条（P0 迁移调用整库重建抹掉标签/文件夹/来源 + P1 前端跨 mode 版本滞后 + `chars` 伪造 + 版本切换竞态 + 导出测试脚本式假绿 + 4 条 P2），另有 5 条「记录不修」，见 [code-audit-3.md](code-audit-3.md)；对照实验 6 条（2026-10-10） |
| 第四轮代码审查 | ✅ 8 条（P1 两条：删文档不清向量 → 语义检索命中幽灵 / `status()` 一次读失败把全文检索永久判坏；P2 六条含密钥前 7 位进日志、`index` 集合不校验致文本与向量静默错位、重建阻塞事件循环），**修 7 条、`P2-05` 记录不修**，见 [code-audit-4.md](code-audit-4.md)；每条配能红的对照实验，后端全量 1065 passed（2026-10-26） |
| 对话重写 | ✅ 独立 chatStore + 消息层组件树 + 工具气泡/引用/思考折叠（2026-08-04） |
| 对话上下文管理 | ✅ context_window 四层解析 + 预算引擎 + 历史压缩 + token 进度/费用（2026-08-04 C1-C5） |
| AI 浮窗 | ✅ 移除插槽，统一浮窗（位置记忆/边界钳制/内部栏拖拽） |
| 多任务并发 | ✅ processingTasks 统一推导，双层流式缓冲，per-doc 操作互斥，逐任务取消 |
| 并发控制 | ✅ 生成上限 2、导入上限 3，超限 Toast 提示 |
| 引擎文件拆分 | ✅ doc.py → 5 文件 + 删死代码 |
| Store 领域分节 | ✅ 39 字段 53 action 拆 7 slices（2026-08-01） |
| API 客户端按域拆分 | ✅ core + 7 域文件 + barrel，fetchSSE/fetchFileSSE 合并 |
| contentSource 类型绑定 | ✅ `Record<ContentSource>` getter 统一三处映射 |
| PreviewArea 展示拆分 | ✅ 6 个 props 驱动组件（EmptyState 替代 8 处空态） |
| useTaskRunner hook | ✅ retranscribe/process 公共骨架抽取 |
| 组件测试 | ✅ 首个组件测试（CopyContentBtn 3 用例） |
| 引擎配置 UX | ✅ "当前引擎"标签 + "恢复默认"按钮 |
| 公式 LaTeX 分隔符 | ✅ `\[...\]`/`\(...\)` 前端预处理 + Prompt 约束 + 后端管线 |
| 导出 HTML 对齐预览 | ✅ one-dark 代码高亮 + checkbox 任务列表 + 暗色代码块 |
| 首次启动向导 | ✅ 三屏引导（氛围 + 组件检测 + AI 配置），基于 missingKeys 跳过已配项（2026-08-13 扩展第三屏） |
| 空状态拖放区 | ✅ PreviewArea 交互式拖放 + 链接输入 |
| 默认 Tab 优化 | ✅ PDF→整理版原文 / 视频→转录 / 图片→原图 |
| 暗色纹理 | ✅ ::after 遮罩 2-3% 透明度保留质感 |
| 转录完成提示 | ✅ Toast 引导用户点击生成按钮 |
| 请求级日志 | ✅ middleware 计时 + status/duration_ms + SSE 长连接不判慢（2026-08-13） |
| 前端持续探活 | ✅ 10s 轮询 /api/health + down/crashed 分级横幅（2026-08-13） |
| 缺 key 主动提示 | ✅ missingKeys 检测 + ImportSection 内联提示条 + chatStore 首提 toast（2026-08-13） |
| 健壮性三批优化 | ✅ 45 项（P0 崩溃/安全 + P1 图表/泄漏/UX 竞态 + P2 中断恢复/死代码）——错误中文化 + ingest 资产服务去重 + 日志风格收敛（2026-08-16） |
| 阅读宽度自适应 | ✅ contentCap 随面板动态放宽（max(1100, 面板宽−160)），折叠侧栏后利用空白（2026-08-16） |
| 编辑器分屏拖拽 | ✅ 产物编辑器 源码\|预览 分隔条可拖拽调宽（25-75%，2026-08-16） |
| 编辑/输入 mono 字体 | ✅ ChatInput + ImportSection 输入框统一等宽字体（2026-08-16） |
| 欢迎文档三产物 | ✅ 空态示例文档 = 笔记（欢迎简介）/整理版原文（开发者功能）/转录（更新记录，2026-08-16） |
| 视频直传模型 | ✅ 阶段 A Qwen 打通——DashScope SDK `MultiModalConversation` 原生通道（file:// ≤100MB 服务端抽帧），生成链路零改动（直传为 prepare 阶段一次非流式理解），截帧链路保留为降级（2026-08-25） |
| 日志/错误系统全面修复 | ✅ P0/P1/P2 三批（AppError 全局迁移 + 请求级日志 + `/api/logs` 防御 + log. helper 来源模块注入 + 45 项健壮性优化，2026-08-25） |
| 错误体系 sentinel 语义 | ✅ AppError `_UNSET` 哨兵区分「未显式指定」/「显式传 400」——显式 400 + recoverable 不再被压 200（评审 H1：export_custom 前端曾下载损坏 zip，2026-08-25） |
| 编辑态绑定来源 docId | ✅ 编辑态 `{mode, docId}`，无参保存（Ctrl+S）永远写回编辑内容归属文档，弹窗期间切文档不错存/不错回退（评审 H2/H3，2026-08-25） |
| 编辑中未保存 × 重新生成拦截 | ✅ `dirtyEditingDocId` 轻量桥（uiSlice 标记，不提升编辑态）+ GenPanel 生成入口两态 confirm，放弃编辑后 PreviewArea 自关闭编辑态（评审遗留①，2026-08-25） |
| 图表语义化布局 | ✅ 出图从「模型手写 SVG 坐标」改为「模型填语义 JSON + 后端算几何」——9 种 layout（tree/flowchart/state/sequence/quadrant 新增 + 形状层 + `shapes` 通道）/ 1~N 张图 / 自然尺寸 + 等比缩放 / 超时分档 / `chart_{hash8}.json` 协议切换证据（2026-09-19） |
| 图表几何缺陷六修 | ✅ 同行跨列边穿节点 / 走廊边叠同一 y / 隔行边穿中间行 / edges·shapes 不参与 bbox / 报错文案误导（「节点过多」掩盖「有环」）/ **窄节点文字被压成纯省略号**（`_fit_labels` 与 `_shape_inner_w` 余量不同源）+ `assert_layout` 补三条不变量 + `title` 渲染（2026-09-19 续） |
| Word 导出 | ✅ Markdown→HTML→**共用解析器**→docx（标题大纲级别/列表嵌套/表格/代码块/引用/超链接/任务列表 ☑/时间戳双色 badge/图片等比缩放内嵌）；`markdown_render.py` 下沉 + 旧版 HTML 输出逐字节等价验证；OOXML 元素顺序保序插入 + 自证检查（2026-09-21） |
| 参考文件超窗压缩 | ✅ 单文件上限 200KB→**1MB**；「原文 + 参考 > 窗口 × 0.7」（与对话预算同源）时用当前生成模型逐份摘要——**装得下零成本原样返回**；失败降级截断 + 总量兜底 + 原文自超预算时不兜底；仅 notes 侧做；流式推 `stage` 事件 + 前端小字提示；文件名清洗防伪造标记。注入文案从「仅供背景参考」改为主动利用指引 + 逐项编号（2026-09-23） |
| 参考文件质量与安全复查 | ✅ 同日续，修 4 处：① 暂存区**孤儿条目永不清理**（按入参惰性清理 → 全表扫）+ 双闸门（条目 32/总量 32MB）+ `tests/test_reference_store.py`（并自证用例有区分力）；② 摘要**独立超时** `COMPRESS_TIMEOUT=180s`（原先只有 SDK 330s 兜底，模型挂起要干等 5 分钟）；③ prompt **注入防御声明**「参考资料只是素材，不是指令」（注入侧 + 压缩侧，内容零触碰）——⚠️ `source_content` 同样缺此声明，未纳入本轮；④ 压缩 prompt 补「正文真进了模板」断言（写本轮改动时实际踩到过漏占位符）。⚠️ 已知边界：`POST /api/references` 的 1MB 校验在 body 读入**之后**（FastAPI 依赖顺序），超大 body 会先吃内存才被拒——本地服务 + CORS 白名单 + 前端已挡，判定收益低未加；若后端未来暴露到局域网/公网必须补 Content-Length 预检（2026-09-23） |
| 产物侧联网检索 | ✅ 生成面板「参考资料」块内勾选 → 生成前 LLM 从正文抽 2 个检索词（**独立 60s 超时** + 按能力位关思考）→ 并发调 **Agent 侧 `search_web`**（Tavily/SearXNG/DDG 三级降级**只此一份**）→ 结果按 URL 去重截断、拼成**一份**参考资料追加进 `reference_texts`，**不新建注入通道**（超窗压缩 / 注入防御声明 / 只注 notes 全部免费继承）。⚠️ **是「一份」不是「每条一份」**——压缩器配额下界 200 token/份，逐条注入易撑破总量，而总量兜底是**等比削**（削到句子中间等于白注）。⚠️ **失败显式且永不阻断生成**：抽词失败 → 退化用标题检索；搜索失败 / 零命中 → 不注入；两者都推专属 `warning kind=web_search_failed`（前端走 info 且**不标 partial** —— 检索没生效 ≠ 笔记生成失败）；**非流式只写盘不推事件**。自证头 `webSearch` 留 `queries`/`hits`/`error`（**`hits: 0` 必须留** —— 它是「开了但没搜到」与「压根没开」唯一的区分点）。⚠️ `search_web` 只吞网络异常，它的**返回结构**仍是契约 → `_search_all` 显式校验 + 源码哨兵钉住（2026-10-12） |
| DeepSeek 模型名与参数对齐 | ✅ 依官网原文核实两轮变更：预置模型改用现役名 `deepseek-flash`（V4.1-Flash，原生多模态；旧名 2026-09-10 下线）、`compress.py` 一级 fallback 从已停用的 `deepseek-chat` 改 `deepseek-flash`；补注思考模式参数契约（默认开启/effort 默认 high、temperature 等三参数不生效、top_p 仅思考模式生效）（2026-09-23） |
| 流式生成必炸修复 | ✅ 0.4.4 打包后首次生成即 `UnboundLocalError: ref_texts`（**必然**非偶发）：`generate_stream` 顶层变量被嵌套函数 `merged_stream` 内的赋值拽成后者局部变量 → 赋值前的读取直接炸（模式 #43）。改用局部名 `refs` 承接（两处：双产物/单产物）；新增 `tests/test_closure_shadowing.py` 静态 AST 全库扫描防**整类**（判据含「按执行顺序展开读写」，`x = x + 1` / `x += 1` 均能抓到；自证有区分力 + 回滚真实代码确认可捕获；全库其余 5 处遮蔽经核对均为安全写法）。⚠️ 暴露的缺口：流式端点运行路径此前无测试覆盖（2026-09-23）。**2026-10-21 扩为两种形态**：新增 `find_import_shadowing`（同函数内 `import` 遮蔽 + 更早读取，0.5.14 启动即退的根因），扫描范围从 `app/` 扩到 `main.py` + `limo_cli/`（此前「全绿」与「看不见出事的地方」是同一件事），并修掉推导式循环变量的误报；全库 138 文件 |
| 文档库组织能力 | ✅ ① **可嵌套文件夹**（2026-10-18；`routers/folders.py` 五端点，含 `POST /folders/ensure-path`）——`parentId` 表达父子（`""` = 根），**零迁移**（读时 `get("parentId","")`、不 bump `INDEX_VERSION`）；**纯逻辑分类永不映射磁盘目录**（每文档已有独立目录含 assets 相对引用与版本归档产物，改目录会同时破坏三样）；**五条不变量**（缺字段/悬空/自引用→根；环在下标最小处断开；兄弟序=数组序+前序父先于子；`descendantIds` 不含自身、`totalCount`=直接数+后代求和；未分类不是实体固定最后）收口 `storage/folder_tree.py` ↔ `doc-library/folderTree.ts`（两端同构，共享 fixture `folder-tree.json`；**`parse_path` / `path_key` 只活在后端一处**）；点文件夹看到**整棵子树**的文档（`include_descendants`）、删文件夹**整棵子树一起删**且各层文档回落未分类（绝不删文档，返回 `deletedFolderIds` 供前端回落）；悬空 folderId 双向容错。**同日交互改造**：移动改树形 `FolderPicker`（三处共用）、分组头就地新建子夹 + 文件夹右键四项、右键菜单与批量条按用途分组、**VS Code 式勾选**（点行首整列选中 + `Ctrl` 按住划选，`Ctrl+mousedown` 快照 base → `mousemove` 跨行加选 → `mouseup` 抑制尾随 click）。② **全文搜索接入**——内存过滤 0ms 即时 + 消抖 250ms 合并 FTS5（`docs` 无正文，客户端**不可能**实现，FTS5 是唯一解不是优化）；「正文匹配」独立分组 + 双命中「正文」角标。③ **排序 + 标签筛选**（来源类型与用户标签此前混淆，现拆两组）。④ **批量操作** `POST /api/transcripts/batch`（按 action 显式枚举校验 + failed 明细 + 全失败 400 + store 一次加锁一次写盘）。⑤ **大列表分批渲染** 60 条 + 全选跨分批（2026-09-23） |
| DeepSeek 思考档位 | ✅ `generation/effort.py` 全系统唯一真相（此前 `"low"` 散落四处）；能力位 `reasoning` 按模型标注；**整理版恒 low 除非选 max** 且 UI 明示；静默期分档 150/180（不改则「选最高档反而更容易失败」）；默认档 `high` = 服务端默认，零回归；AST 断言钉住全库无硬编码档位字面量（2026-09-23） |
| PDF 直传 | ✅ 用户可选**二选一**（文本模式默认 / 直传模式将 PDF 原件以 `file` 块随生成请求发出，**只发原文件**防 token 双花）。**探针实测**：data-uri 20 MiB 上限约束的是 **base64 之后的字符串**（19MB 被拒）→ 反解原字节上限 ≈15.0 MiB，工程取 **14 MiB**（贴边的 15 MiB 算出 data-uri 恰多 25 字节，照样被拒）；首包**次线性**（12KB→3.4s、9.5MB→143s、13.7MB→149.3s，服务端逐页渲染故由页数主导）；**必须走 Chat Completions**（Responses API 官方记载会 200 但丢文件，且无客户端可见信号）。⚠️ **双真相源**：rawText 是导出/搜索/预览的真相源但不是模型所见的真相源（扫描件上真分叉）→ rawText < 100 字符时完成期 warning（判据取 100 而非空串：OCR 失败的**占位文本**约 50 字符「看起来有内容实际没内容」）。能力位 `document` **只标查证过的**：dashscope 官方名单的三个（`qwen3.8-max` / `qwen3.8-flash` / `qwen3.8-27b`，**任何 qwen-vl / qwen3.5 视觉模型都不在名单**）；`routers/model.py` 不做名字自动检测（2026-09-23） |
| 文档能力逐家查证 | ✅ 同日续，用户要求「其他模型的文档能力去官网文档调查」。7 家结论：**仅 dashscope 标**（《PDF 理解》页明列 qwen3.8 系四个）；openai（通道在但 gpt-5 系社区反馈看不到文件/Azure gpt-4o 直接报错）、anthropic（用 `document` 块不是 `file` 块，而全线共用 AsyncOpenAI）、google（兼容层不支持）、zhipu / moonshot（独立端点抽文本，非原件直传）、opencode 网关（未查证 + 该能力仅限华北2 地域）**一律不标**。**结论表唯一沉淀点** = `services/doc_understand.py` 模块文档。配套修正三处：新增 flash/27b 预设；`qwen3.8-max` 的 `context_window` 131072→**1000000**（原把「最大输出」当成了「上下文」，对话预算 700k 缩成 91k）；`DOC_DIRECT_TIMEOUT` 240→**330** / `DOC_DIRECT_GEN_TIMEOUT` 450→**660**（240 按实测 ×1.6 算，而官方明写首包最坏 300s → 区间内正常请求被自己掐断且只表现为静默降级；**实测证明不了上界**，模式 #48）+ 修 `generate.py` 流式端点漏传 `doc_direct`（模式 #16）（2026-09-23） |
| 阶段 0 四项独立小修 | ✅ ① `DocLibrary` 动画延迟 `i*50ms`（第 1000 篇要等 **50 秒**，是功能性错误不是性能问题）；② `search.py` snippet 列号 1→**2**（列序 `(id,title,content)`，列号 1 是标题 —— 不修则正文命中摘要显示标题）；③ `model.py` `"*"` 分支**跳过 exclude 检查**（`if`/`elif` 互斥使排除表成死代码）+ deepseek 规则改 `{"include":["*"],"exclude":[pro,reasoner,coder,embed]}`；④ `token.py` `DEFAULT_CONTEXT` 补 4 家（zhipu 刻意取 131072 不拉平 1M —— 兜底层取保守值才安全）（2026-09-23） |
| 文档库交互重设计 | ✅ **三条「删除」是本轮核心**：① `selectMode` —— 批量选择从「模式」变常态，批量条由 `selectedIds.size > 0` **派生**（要原子修改来防的漂移，因为漂移的载体本身没了）；② **「正文匹配（N）」独立分组取消**（不受筛选约束 / 行是裸 `<button>` / 与排列旋钮打架），唯一值得保的性质由「**相关度**」排序键承接（rank 取 `hits` **数组索引**、**零后端改动**、搜索词从空变非空时自动切、无命中时不出现——否则是点不动的死键）；③ `FolderTree.tsx`（文件夹降为「排列方式」的一个取值，与类型/时间共用 `GroupHeader`，但增删改只对文件夹组开放）。**⚠️ 单击行 = 打开文档且不动选择集**（按「点即选中」实现则用户再也打不开文档），复选框常驻 + Ctrl/Shift 区间/Ctrl+A（输入框内不劫持）/Esc。**排列 ⊥ 排序**：排列（平铺/按文件夹/按类型/按时间）管横向归类，排序管组内顺序，**刻意无「筛选时退化成扁平」的特殊模式**（那正是「树和筛选打架」的来源）。筛选区五小节 + 两级折叠 + 收起仍显激活摘要 + 可拖分隔条（双击复位）；标题关键词纯内存不触发 FTS（「筛标题」vs 搜索框「找内容」）；「本周」= 本自然周。状态归属由「折叠即卸载」判定（`SlotLayout` 是 `collapsed ? PanelStrip : Comp`，本地 state 收起即归零）→ `selectedIds`/`listLimit`/`folderBusy`/`docsLoading`/`collapsedGroups` 进 docsSlice（⚠️ 此前三份文档写作 `expandedFolders`，那是个**从未存在过的字段名**；反向语义——存的是「收起的组」），`density`/`groupBy`/`sort`/`expandedSections`/`filterAreaHeight` 进 prefs，非 boolean 的 **nonce** 进 uiSlice。`SearchBar` 从**第二套搜索实现**（限 10 条/无 AbortController/自带 snippet 分支）退化为入口按钮，与 Ctrl+K 共用 `openDocLibSearch`。顺手修 10 处真缺陷（右键复制复制的是**另一篇** / 首帧假空态 / 删文档不清产物 / `''` 哨兵写进 localStorage / ContextMenu divider 未渲染 / 哨兵 IO 未绑 root / 文件夹加载失败被静默吞 / 拖拽 `onDragOver` 读旧 state 致 drop 永不触发 / 新建失败丢草稿 / 部分失败整体回滚）。测试 130 → **209**（+5 文件），28 条对照实验自证（其中 3 条第一版判据没区分力，暴露的是判据本身）。**简略模式 = IDE 文件树**（同日续，用户看过实现后推翻原「平铺不分组」设计）：固定按文件夹分组、忽略「排列方式」偏好，子行缩进 + 缩进参考线（`treeIndent`，只给有行的组画），**分组头与文件行同高 26px（成对常量）**；顺带修掉一条从未命中的骨架判据——「分组模式下 `groups` 永远非空」，旧判据 `!groups?.length` 在默认排列下一次都没生效（冷启动显示的是「未分类 0」，正是 #19 要杀的假空态），沉淀为模式 #50。**后端零改动**（2026-09-25） |
| 智能分类 | ✅ 选中 N 篇 → `POST /api/classify/preview`（**恰一次 LLM 调用、零写入**）产出「每篇 → 建议文件夹 + 建议标签」→ 对话框审定/编辑 → `POST /api/classify/commit`（**一次加锁一次写盘**）。**模型永不直接写库**（先建议后写入是产品决策，不是实现细节）。⚠️ **同日二轮改成两阶段**：对话框先呈现配置（取材深度 / 文件夹策略 / 新建上限 / 标签策略），**用户按「开始分析」才发起调用**——`onStart` 是计费调用的唯一出口，`handleOpenClassify` 只打开对话框、**一个请求都不发**（此前「打开即分析」：用户没点任何东西就已经花了钱、界面已被填满）。⚠️ **两项策略各三档**：`folderMode` = 优先复用或新建（默认）/ 只复用 / 只打标签；`tagMode` = 喂全库词表（默认）/ 用户手填基础标签（**替代**词表而非叠加；全库无标签时前端默认落这档）/ 完全交给模型。判据粒度分两档：**策略非法 → 显式报错 `CLASSIFY_BAD_OPTION`，且在校验处就返回（不发起计费调用）**；`notesChars` / `maxNewFolders` / `seedTags` 非法 → 静默夹紧（分野 = 「这个值错了会不会改变**产物性质**」）。策略的**两个投影同源**：prompt 侧（`_folder_section` / `_tag_section`）与收口侧（`_normalize_one`）收到同一次取值。⚠️ **对话框与文档库/对话侧共 6 处浮层改为 `createPortal(…, document.body)`**：`DocLibrary` 与 `FloatingAI` 的根 `<aside>` 都带 `backdrop-blur-[20px]`，而它**会创建包含块**，把 `fixed` 后代锁进面板（用户原话「卡在那一个文档栏里面」）。四条：① 分类空间**优先复用**既有文件夹（prompt 给 id + 名称 + 现有篇数，硬规则「优先复用、确实无归处才新建、新建总数 ≤5」）；② 取材深度**用户选**（600/200/仅标题，切换即**重调 preview**——判据与执行同源，前端不本地重算），后端再按输入预算兜底缩减并**逐篇显式标注**（`shortened` / `title_only`，无笔记篇明说「只能依据标题判断」）；③ 标签喂**全库现有词表**要求优先复用；④ **分类不做降级**——超时 / JSON 非法一律 `AppError`（与 `reference_compressor` 刻意相反：那里降级返回 `None` 继续，而分类没有「降级继续」的语义）。⚠️ **不扩 `POST /api/transcripts/batch` 的第 4 个 action**：它的形状是「一个动作、一个 folderId、一组统一 tags」，表达不了逐篇异构，硬塞会让它七成字段只对某一个 action 有效；而 commit 的收益与 batch 存在的理由**逐字同源**（一次写盘而非 N 次）。⚠️ **新文件夹由前端在 commit 前用现成 `POST /api/folders` 建**（名字是用户可改的自由文本，只有前端知道最终值），commit 只做**存在性校验**——两次请求之间用户完全可能在别处把那个夹删了。三处抽公共：`store._merge_tags`（批量打标签与分类写入**同一份** add/replace，各写一份会让 add 语义慢慢漂移）、`generation/doc_text.latest_notes_text`（「最新版」版本序判据只此一份）、`core/text_utils.parse_json_object`（`chart_renderer` 的私有版改为一行委托 + 身份断言钉住）。⚠️ **实机验证抓到一个单测抓不到的缺陷**：`LLMService.__init__` 第一参数是**整份 config**（内部自取 `config["llm"]`），而最初写的是 `LLMService(resolve_llm_config(...))` —— 传 llm 子字典进去取到空 dict → `api_key` 恒为空 → **每次预览都报「未配置 Key」，功能 100% 不可用而 34 条单测全绿**（端点用例注入的是假 `LLMService`，压根不经过构造）。全项目其余 10 处调用点都是传顶层 config，只有这一处新写的传错。修后补守卫：**替身记下构造时收到的入参**、断言其中有 `"llm"` 键（`resolve_llm_config` 的返回没有这个键，判据因此精确）；对照实验改回去 → **只此一条红、其余 33 条全绿**，如实复现「坏掉而没人发现」的形态（2026-10-11） |
| PDF 导出 | ✅ 两条路径共用一份自包含 HTML。Tauri：Rust `export_pdf` → 屏幕外窗口 → `ICoreWebView2_7::PrintToPdf` **直接落文件**（A4+背景保留+无页眉页脚，存系统下载目录，同名加序号）；浏览器：屏幕外 iframe + 打印对话框。**Tauri 链已端到端实测** —— 真实文档导出 + PDF 读回断言（6 页全 209.9×297.0mm / 代码块底色·Pygments 三色高亮·表格·跨页表头·图表 PNG 逐页核对全对，2026-09-21）。⚠️ 加载 URL 必须带 `inline=1`：默认的 `Content-Disposition: attachment` 会让 WebView2 去「下载」而非渲染 → 打印出 876 字节白纸却报成功。**浏览器链只测到打印 CSS 层**（headless 4 套主题 + 暗色模拟），`printFromUrl` 的 iframe 交互路径未经真人点击验证 |

| 合成产物形态重做 | ✅ 产物实测是**评审报告而非文章**（6 篇 7295 字符：后四段占比 **96%**——同一份对齐结果铺了四遍、加粗 **40 处**、引证 80 处=每 91 字符一次、首句是系统元数据）。**根因不在模型**——三处症状在提示词里逐句有源头，模型在忠实执行一个错误的任务定义。本轮：① 默认套 `paper` 5 维 → **`general` 7 维**（⚠️ `general` 必须排预设字典**第一位**——前端取 `[0]` 预填，排后面 = 后端换默认而前端仍预填 paper，且**零报错**；⚠️ 历史文档重新生成的骨架随之 5 → 7 行）；② 约束拆三块（事实 5 条 / 通用文风 7 条 / 体裁增量 4 类 × 3 条，**每类第三条是停止条件**、多标签必须去重）；③ **覆盖说明换位置而非取消**：删 `<task>` 段的强制元数据开场（它与新增的「禁止元数据开场」条款**直接矛盾**——两条对立指令同时发给模型时验收标准不可能达成），改在「## 综合评价」末尾以一句话说明输入覆盖；④ 删死参数 `user_notes`（签名里有、函数体零引用）；⑤ `dump_meta` 改**递归清洗**（原先只清 `sourceTitles`，`dimensions` 的 `-->` 泄漏一并覆盖）；⑥ 入参清洗移进 `runner`（`normalize_*` 的抛错写在 `StreamingResponse` 构造之前 = HTTP 500 逃出 SSE 通道，而 `source_mode` 这条路径**一直是 500**）（2026-10-04） |
| 合成读者意图与体裁 | ✅ 两只旋钮：`READER_INTENTS` 六选一（2026-10-18 加「梳理观点」）决定 `<task>` 段的「读者目的」**与产物形态**（`shape` → `_SHAPE_SKELETONS` 五套骨架之一），`CONTENT_TYPES` 多标签决定加载哪几类体裁增量条款。⚠️ 两者连同 `dimensions`/`source_mode` **全部只落出处头 meta**（生成是第二次独立请求，届时手里只有 rawText）；⚠️ **错误语义刻意不同**——意图与原料模式非法**显式报错**（选错了产物的读者目的/性质就变了，而产物上看不出异常），维度与体裁**静默清洗**（判据 = 这个值错了会不会改变产物的性质）；⚠️ 「理解主题 / 比较材料」的 `defaultContentType` 是 `None`、**刻意不预设体裁**（给读者的动作猜材料体裁，正是要消灭的那类错配）；对话框加意图单选组 + **折叠的高级区**（维度预设 / 维度列表 / 体裁多选）（2026-10-04） |
| 合成超窗分层 | ✅ 「窗口不够」**不是一个问题，是四个**：① 总量超窗（已有逐篇抽取，非妥协）② **单篇自身超窗**（原只标「已截断」，约 78% 内容永久丢失且原因与超时/模型抽风**不可分辨**）③ 病态长文拖垮整次（超时 = 前面所有篇作废）④ 用户事前不知情。**②→篇内分块归并**：关键洞察是「分块不增加配额，而是让它在配额内覆盖全文」（现状从**头部**截断 → 后 78% 从未进模型）；三条预算约束闭合 + 每块 `_truncate_to_tokens(t, q)` + 归并去重；⚠️ **每块必须推 stage**——篇内静默 = Σ 各块耗时，K≥6 冲破 900s 会让**正常路径被判挂死**。**③→软 deadline**：到点则剩余篇硬截断 + 标 `truncated_timeout`，正常走到落库。**顺带订正** `SYNTH_QUIET_TIMEOUT` 与 `SYNTH_TOTAL_TIMEOUT` **同为 900**（「挂死」与「整体超时」同刻到期、由竞态决定收到哪条错误）+ 注释与值不符 → 改 300 并把三者关系写成**断言**。**降级状态 3 态→8 态**（`chunked`/`chunked_partial`/`truncated_over_window`/`truncated_timeout`/`truncated_llm_error`）+ 基础状态回退（历史 `truncated` 与未知状态都不崩）。**④→预览端点** `POST /api/synthesis/preview`（零 LLM 调用）：逐篇 token + 预计处理方式 + `stuff_window`；⚠️ 判据与执行**同源**（同一个 `_classify`）；`stuff_window` 语义是「让全部原样**装下**所需窗口」**不是**「能做合成的最小窗口」；换模型 banner 触发集**收窄**（`chunked` 是设计认可的终态，若也触发就在「不用换」的路径上不断劝人换）（2026-10-02） |
| 合成维度可控 | ✅ **维度就是产物骨架**（「## 对比总表」= 行=维度 × 列=各篇）。查出一处**设计说了代码没做**：Stuff 路径（**多数情况**）**零维度注入** → 唯一的补救机制（维度发现）根本不跑 → 维度机制在最常见路径上一次都没生效。修：`DIMENSION_PRESETS` **五套**（general 通用资料 / 论文 / 技术资料 / 产品评测 / 观点内容），`general` 排字典第一位为默认（前端取 `[0]` 预填）→ capabilities 下发（前端不硬编码）→ 用户给定则**跳过维度发现调用** → **两条路都注入**。⚠️ 维度**落在出处头 meta**（生成是第二次独立请求，届时只有 rawText；落 index 会被重建丢失，落可见正文会与原始材料混淆）（2026-10-02） |
| 合成原料可选 | ✅ 原文（默认）/ 已生成的笔记，**互斥不混合**——「只能选一项」是**刻意的设计**：混合会同时打破 ① 等比配额前提（笔记 token 密度与原文差一个数量级）② 产物可解释性（`✓ 未删节` 会让人以为给的是原文）③ 降级语义（笔记本身已是压缩品，再叠 `summarized` 让状态细分失效）。**整批同源 ⇒ 三个问题一并消失**。⚠️ 前端拿不到「有没有笔记」（`DocEntry` 无产物字段），**解法是不新增字段/端点**——预览端点本就在逐篇读文档，顺带返回 `hasNotes`/`notesTokens`；切模式**重调预览**（本地重算 = 复刻 `plan_synthesis`，成对逻辑禁止）；**后端执行时再校验一次**（预览与执行之间隔着用户操作）；`sourceMode` 落 meta 是**为产物自解释**而非为生成（2026-10-02） |
| 合成 `excluded` 语义 bug | ✅ 落地时**由测试抓到**：后端 `done.excluded` 填的是**标题**，前端拿它从选中集做差集（按 id）→ **永远匹配不上** → 被排除的篇仍写进来源清单，「N 合 1」徽章指向产物根本没读过的文档；预览侧 `excludedSet.has(id)` 同病。**两边都错、两边看起来都对**，失败完全静默（不报错、差集恒为空）。统一为 **id**（标题经清洗且重名合法，做身份判定必然错 —— §4.2.2「标题是展示字段，不是身份凭据」）；`not_enough_notes` 的 detail 在调用点转回标题。⚠️ **只断言「有 2 条」的写法抓不到它**，必须断值本身（2026-10-02） |
| 合成产物形态重做（二） | ✅ 上一轮（见上）改了约束与落点，**但骨架没换**。本轮从产物实测反推真因：**旧五段与已写入的文风条款正面冲突**——文风第 3/4/5 条禁「无新增信息地重复」，而骨架同时要求「分维度对比 → 共识与分歧 → 综合评价」把同一份对齐结果走三遍、且硬写「不得省略任何一段」⇒ 两条硬指令互斥、模型只能违反一边，**这才是「后四段占 96% 篇幅」的结构性来源**（不是文风条款不够）。换骨架：`## 这批材料放在一起说明了什么`（1–3 句给判断，禁元数据开场）/ `## 分歧在哪`（无分歧时不写）/ `## 读者问题`（3–6 个 `###`，每节三步：结论 / 材料怎么说 + `[文档 i]` 锚点 / 什么条件下不成立）/ `## 材料作用不同` / `## 材料索引`（**全文唯一允许表格处** + 覆盖情况一句话）。**维度降为「读者问题」的候选来源**（用户拍板：问题由模型按读者目的推导，不再当矩阵行标签——矩阵列 = 各篇文档，10 篇就是 11 列、中文单元格被压到十几个字符宽）——⚠️ 但**逐条注入不变、也不能删**（「模型倾向保留出现次数最多的内容，而不是最重要的内容」）。三处连带：① 事实约束第 3 条「未提及」是**表格单元格的填空规则**、表格移出正文后改写为「材料不足以判断」（**抽取阶段 `synthesis.py` 仍用「未提及」**——那是喂给最终模型的中间产物）；② 覆盖说明**第三次搬家**（概览段 → 综合评价末尾 → 材料索引表下，每次都跟着骨架走）；③ 文风续写第 8/9 条（说人话 / 不设篇幅上限但按读者问题数量定）——⚠️ 第 8 条**刻意不抄**单篇 `plain` 档的「不堆小标题和列表」，那句与要求 3–6 个 `###` 的新骨架正面冲突，是同一个病换个骨架复现一次（2026-10-05） |
| 默认文风改通俗 | ✅ 新增 `plain` 档并设为**全局默认**（用户判断：「大部分用户一般默认文风就是要偏通俗，除非是论文那种要求严谨的才要求详细更多」）——此前**风格清单里根本没有「通俗」档**，默认是 detailed（与用户预期相反）。落点 **8 处，缺一即静默无风格**：`Style` 枚举 / `COMMON_NOTE_STYLES`（VIDEO、DOC 经 `**COMMON` 自动带出）/ `EXCEL_STYLE_MAP`（**同值引用 detailed**——数据摘要要的是准确与完整，「通俗」在此无意义）/ `styleLabels`（**手写 dict，不会随三张 MAP 自动带出**）/ 前端 `genPrefs` 默认值 / `GenPanel` 三处 fallback 数组 / 后端 6 处签名默认值。⚠️ 两条副作用显式处理：polish 豁免元组加 `plain`（否则**每生成一次整理版刷一条「已废弃」warning**）；**存量用户不受影响**（`{...DEFAULTS, ...raw}` 让已存的 `style` 覆盖新默认，**刻意不迁移**——用户当前的 detailed 可能是他显式选的，静默改掉是替他做决定）。**顺带修三个「静默改写风格档」的既有缺陷**（同一形态 ×3：**枚举值与风格表 key 不同形 → 白名单不含实际发来的值 → 被改回 detailed**，零报错）：`meeting`（枚举值是 `meeting_minutes`，而前端发的是 `meeting`）、`none`（不在枚举里、白名单没放行，而上一行注释白纸黑字写着「可能传 none」）、以及**修 `meeting` 时的反向回归**（白名单排在 `normalize_style` **之前** → `meeting_minutes` 旧调用方被拒，而 `STYLE_ALIAS` 存在的全部意义就是兼容它）→ 归一与校验**都走 `normalize_style`** + `none` 显式放行，收口到 `routers/generate.py::_normalize_and_validate_style()`（抽成模块级是为了让测试**直调真实实现**，而不是复刻一遍表达式——复刻的测的是它自己）。测试侧新补 `test_prompt.py` §8 五组断言（**全仓唯一**守护默认风格档的断言，此前每个调用点都显式传 `'detailed'`，默认值改成什么都不会变红）（2026-10-05） |
| 文档 id 唯一 | ✅ 控制台 `Encountered two children with the same key, tr_336b128e8316` 查出真因：磁盘上**两份内容相同的文档目录**（Moby-Dick 两份，`transcript.md` 长度与 sha256 前 12 位完全一致）+ `index.json` 损坏触发 `_rebuild_index` → 它按**内容**派生 id ⇒ 同内容同 id。⚠️ 真正的危害不是前端告警而是 **`delete_transcript` 的 `find` 只命中第一条 ⇒ 第二条永远删不掉**（告警是唯一的可见征兆）。三条修：① `_unique_tx_id(seed, taken)` —— **首次尝试逐字等于旧行为**（绝大多数库零回归，否则升级一次全库 id 漂移）、撞了加盐、16 次仍撞退化为随机 id 绝不返回重复；② `_rebuild_index` 记 `taken_ids` 走它；③ 新增 `_dedupe_transcript_ids()` 启动时修**已有脏索引**（幂等，**只改 id 一个键**、不重扫磁盘、不碰 tags/folderId/来源/不删目录 —— 第 3 轮审计 P0 的教训；`markdowns`/`processes` 的 key 留在原 id 上，与 `find` 取首条一致）。⚠️ 不选「输出前静默去重」（违反失败显式化原则、且掩盖「磁盘上真有两份」）。测试 +5（`test_store_index_recovery.py`，含对照实验 —— ⚠️ 只掐第一道网时第二道会当场兜住，**对照实验反而全绿而结论是错的**）（2026-10-12） |
| 模型预设核对脚本 | ✅ 开发期**变更哨兵** `scripts/check-models.py`（不进打包、不被后端 import）。考察参考项目 pi 的生成式模型目录（从 models.dev 生成进源码 + manifest 校验）后**推翻照搬方案**——实测 models.dev 不可作权威源：与官方页冲突（`qwen-turbo` 标 1M，官方 131072）、**自家数据自相矛盾**（anthropic 的 haiku 标 200K 而同家 sonnet/opus 标 1M，Claude 的 1M 需 beta header）、覆盖率仅 **54%** 且缺口正落在最新/最冷门模型上 ⇒ 定位改为「产出的**是问题不是答案**」，唯一用途是决定「打开哪一页」；照搬另会冲掉 `config.py` 里逐条人工查证注释（官方页 URL + 陷阱说明）。四类报告 A 新 / B 下架 / C 数值不一致 / D 缺字段；**墓碑同源**（消费既有 `RETIRED_MODEL_IDS`，否则每次都重报已知噪声）；**能力位不参与核对**（models.dev 的 `modalities` 说「模型能读 PDF」与我们认不认 `file` 块是两回事，模式 #51，仍只能逐家手标）；`PROVIDER_MAP` 漏映射 = **静默少核对一整个厂商**（报告照常输出、只是从不提及它）→ 脚本喊 stderr + `tests/test_check_models.py` 14 用例守卫（含「守卫真会红」对照实验）。**本轮只做数据层，不碰计费**（2026-10-02） |
| 主题系统优化（档 A + B + 导出跟随） | ✅ 三类问题一次收口：① **色值三份副本**（`BASE_OPTIONS` 56 hex / `ACCENT_SWATCH` 52 hsl 且亮度散在 13 种取值、实机恒 50/62% / `TEXTURE_SWATCH` 6 hex 与 CSS **6/6 全不符**）→ **JS 里只剩 label，一个色值都不留**（色块改 `data-*` + `var()`）；② 加轴改 5~6 处 → **轴注册表** `lib/theme-axes.ts`（加轴 = 2 处，启动链 `THEME_AXES.forEach(a => a.apply(a.saved()))`）；③ **导出完全不跟随主题**（`HTML_THEMES["limo"]` 自包含裸色值、两入口零参数、恒用硬编码蓝 `#2563EB`）→ 六条轴参数贯通 + 主题层注入。**新增主题层** `frontend/src/styles/theme-layer.css`（纯声明层，前端 `@import` 与后端 `_load_theme_layer_css()` 读**同一份**）。⚠️ 三条硬约束：**CSS 自定义属性继承的是计算值** ⇒ `[data-accent]` 必须自己重跑派生（否则设置页 26 个色块全显示成当前配色，比现状更糟，模式 #69）；**删表前必须先换 `getSavedAccent()` 的校验源**（否则已保存配色被静默重置回 `sky`）；**`_CARD_TOKENS_CSS` 不能删**（`cards.css`/`blocks.css` 的消费者身份与 `html_theme` 无关，删了会让 paper/github/minimal 的卡片底色从纯白变暖白）→ 改名 `_EXPORT_NEUTRAL_TOKENS_CSS` 且只在未跟随时注入。⚠️ 暗色块包进 `@media screen`（**暗色是屏幕概念，纸永远是浅的**，模式 #70）—— 一处改动让三个宿主同时正确，且删掉「暗色→浅色还原表」那份第二真相源。⚠️ 顺带修：向导 4 张预设卡**圆点/标题色全等于当前配色**（真 bug，D1#2 后自动正确）、`rgba()` 的 `ARBITRARY_COLOR` 守卫缺口、`test_export_blocks.py` 的 `'--tk-accent:' in out` 假绿灯（升级为「`<html>` 上有 `data-accent`」）。⚠️ **打包必须带 spec datas** `('../frontend/src/styles/theme-layer.css', 'styles')` —— 漏了是「开发版全绿、打包版导出 500」。测试：前端 +27（1064）、后端 `test_export_theme.py` 18 条；`scripts/check-all` 六项全绿（2026-10-16） |
| 索引重建不再有损 | ✅ 此前 `index.json` 名为「缓存」，却是文件夹归属 / 标签 / 创建时间 / 来源的**唯一**存放处 ⇒ `_rebuild_index`（缺失 / 损坏 / 旧格式时**自动**触发，一次断电就够）只能清空它们。现在真相搬回自然归属地：文档级 → `{doc_dir}/meta.json`、库级 → `{root}/folders.json`（`storage/doc_meta.py`），index **仍保留这些字段当运行时缓存**（`folderId`/`tags` 参与过滤计数，每次列 N 篇读文件太慢），只有重建才回落 sidecar。⚠️ **恢复主键是 `dir_name` 不是文档 id**（重建把 id 由正文 sha256 重推、必然变）—— 这也是 sidecar 必须住在**目录**里的原因。⚠️ `_save_index` **先 folders.json 后 index.json**（真相领先缓存）；`_migrate_classification_sidecars()` 启动时补写老库、**只写 sidecar 不碰 index.json**、幂等判据是「读得出来吗」（损坏的也能自愈）。⚠️ **刻意不从 `index.json.bak` 回落** —— 它存的是**损坏原文**（给人抢救用），捞不出东西。**已知边界**：升级前索引就已经坏了的老库，那部分分类回不来。顺带把原子写收口到 `core/atomic_io.py`。测试 +6（`test_store_index_recovery.py`，含「无 sidecar 时如实降级」这条记录放弃点的用例）（2026-10-18） |
| 图表回边分道 | ✅ `_state` 的 `corr_y` / `entry` 原先**只依赖层号**（`_below(rank[u])`）⇒ 同一节点发出的多条回边水平段 y 完全重合、沟槽 x 只差 6px，画面上并成一条横线，标签只能靠 `_relax_labels` 上下让位——让开后一个压线一个悬空，读者分不出哪个标签说的是哪条线。改为**走廊车道池**统一分道：前向边占 1 段、回边占 2 段（源节点下行段 + 进入目标层段），走廊高度按段数增长（末行也留），`lane[(边下标, 段号)]` 取等距车道。⚠️ **`_relax_labels` 保留但降级为兜底**（只管「标签 × 标签」，让不开则丢弃标签，好过画一块压住节点的色块）；测试有对照断言钉住「让位不是这次成功的原因」。纯前向图的 lane 值与改造前**逐值相同**（既有硬编码路径断言一字未变）（2026-10-26） |
| 三个「偶发」bug | ✅ 现象各自独立、报告里合成一句：① **沉浸阅读被旁人的任务拦住**（`toggleImmersive` 查全局 `processingTasks`）→ **整段守卫移除**，依据是三条架构事实（abort 控制器在 `taskManager` 模块级 Map / SSE 回调直接写 store / 收尾 `setProcessing(key,null)` 在 `finally` 里，三条都与组件挂载无关 ⇒ 不存在「切走就僵尸」）；② **追问卡片偶发不生成** → 真因是 `!isSameDoc` + `markdownMeta.mode === 'notes'` 两道早退，而「切回来不发 GET」使原理由不成立 ⇒ 新增按版本索引的**晚到通道** `followUpsByVersion`（key = `docId::mode::version`，SSE `followup` payload 补 `version`），与「用户更关心当前文档」两个需求同时满足；③ **图表偶发失败**见上一行（同时补了 prompt 硬边界：`flowchart` **结构性拒绝环**，有回环的改用 `state`；`PROMPT_VERSION` → `"20"`）（2026-10-26） |

## 目录结构

```
backend/app/
  adapters/      5 个适配器（8 个 py 文件，含 BilibiliSubtitle/YouTubeSubtitle）
  engines/       12 个内置引擎 + 插件加载器 + transcript 契约（含 embedding_base 协议 + embedding_dashscope + embedding_local，2026-10-26）
  routers/       19 个路由模块（含 folders/plugins/references/search/epub/markdown_edit/cli/classify）
  generation/    生成/处理/OCR预处理/prompt/参考文件超窗压缩/思考档位/provenance（自证头装配 + PROMPT_VERSION）/classifier（智能分类）/doc_text（最新版笔记正文 + **全文索引文本**，分类 / 合成 / 检索共用）/web_search（产物侧联网检索：抽检索词 + 并发搜索去重 + 组料，2026-10-12）（fact_check/review 已移除 2026-08-16）
  agent/         LLM Agent 工具 + 会话管理（tools/tool_registry/compress/session）
  downloaders/   平台下载器（yt-dlp 通用路径；抖音走同一 `_download` 的 UA/Referer 覆盖。⚠️ 自研 `douyin_downloader` + `douyin_helper/abogus`（GPL-3.0 声明）已于 2026-10-06 整体删除）
  llm/           LLMService + agent 循环
  core/          配置/类型/注册/错误/disconnect/搜索（FTS5 三列：title 索引 / body UNINDEXED 供摘要 / toks 二字组匹配）/cookie/sample_doc + vector_index（向量纯算子：切块 flat+h2 / 分组排名 / rrf，产品与评测共用同一份）+ vector_store（`vec.db` 三表：四元组身份 / 状态机 / 块读写；写点只打 `pending` 不编码）+ transcript_meta（产物自证头编解码）+ artifact_blocks（产物正文协议标记 limo-block）+ resource_id（读路径 ID 校验）+ text_utils（JSON 对象抽取等公共纯函数，图表/分类共用）+ atomic_io（全项目唯一原子写：临时名唯一 + `os.replace`）
  utils/         视频管线（video_reader/video_helper/screenshot_marker/frame_quality）
  services/      共享查询层（query，HTTP 与 CLI 同一份读路径）+ 视频直传理解（video_understand）+ 文档直传（doc_understand，PDF file 块）+ 图表（chart_layout/ 包：geometry 几何 + layouts 算子 + engine 校验 / chart_svg 生成 / chart_renderer 渲染）+ epub 解读 + markdown_render（HTML/Word 共用渲染）+ markdown_sections（H2 切分 + `card` 节修饰符判据，导出卡片骨架与统计脚本共用）+ docx_export（Word 导出）+ vector_worker（向量索引后台 worker：分块 → 块级 hash 增量 → 编码 → 每篇 checkpoint → 失败上限 → 启动对账 → **模型内存释放（`never`/`idle`/`immediate` 三档 + `release_now` 供「关掉功能」这条路）**）+ vector_search（查询融合：字面 ∪ 语义 RRF，`depth=max(limit,50)` / `k=10` / 三档前置校验拦在编码之前 / 失败显式化）+ vector_runtime（语义检索开关的**唯一维护点**：worker 与融合层成对启停，口径不符只停用不重建）+ 错误体系
  storage/       文件系统存储 + folder_tree（文件夹树纯函数：归化 / 前序 / 后代 / 路径标签 / `parse_path` + `path_key`，与前端 `folderTree.ts` 同构）+ doc_meta（分类真相源：文档级 `{doc_dir}/meta.json` + 库级 `{root}/folders.json`，`DOC_FIELDS` 17 字段，含 `title`）
backend/limo_cli/  只读命令行（cli 入口 / commands 七命令 / doctor 诊断 / output 输出契约 / sources 两个传输层 / agents_md 契约文档模板 / exit_codes）
backend/tests/     后端测试（94 个 .py，含 conftest.py 共享 fixture；test_e2e_smoke 经 collect_ignore 排除）
frontend/src/
  components/    UI 组件（ui/ 布局层：SlotLayout 布局引擎 + ActivityBar 活动栏 + PanelStrip 折叠条；preview/ 下 6 个展示组件拆分 + CardSections 卡片骨架 + MarkerContent 标记渲染 + ArtifactMetaBar 产物自证条；ai-chat/ 对话组件 + ChatMarkerContent 对话块渲染；doc-library/ 20 文件拆分（含 folderTree 建树 / FolderPicker 树形选择））
  store/         Zustand 状态（types.ts + 7 slices + chatStore + streamBuffers）
  api/           API 客户端（17 文件：core + 域文件 + client.ts barrel + sse/taskManager）
  hooks/         useTaskRunner 等自定义 hook
  lib/           tabRegistry / contentSources / slotRegistry（持有 SlotDef.component）/ panelToggle（开合判据）/ layout-prefs（侧栏布局偏好）/ slotMetrics / modelCaps / markdown-scan（围栏感知标题扫描 + limo-block 行分类/半截标记判据）/ markdown-sections（H2 与任意层级切分，含 `card` 节修饰符判据）/ marker-prefs（标记装饰开关）/ markdown-markers（limo-block 解析/剥离）/ chat-block-content（追问项抽取）/ **chat-font（对话正文字号倍率，独立于阅读字号；档位换算复用 reading-prefs）** / card-prefs（骨架轴偏好 + 底/抬升/边微调与 `serializeCardVars`）/ **artifactMeta（产物元数据展示判据：时间/字数格式化 + 取哪几项；自证条与 VersionSwitcher 共用）** / export-prefs（快捷导出偏好，`embedImages` 默认开）/ 主题模块（accent / theme-base / theme-presets / theme，外加 **theme-axes 轴注册表** —— key / attr / storageKey / options / fallback / apply，**只放 label 不放色值**，启动链与预设遍历它）/ health / download / print
  styles/        主题层（theme-layer.css：六轴纯声明层，前端 `@import` 与后端 `_load_theme_layer_css()` 读**同一份**，打包须在 spec datas）+ 卡片骨架 CSS（cards.css）+ 语义标记 CSS（blocks.css，进导出）+ 对话块 CSS（chat-blocks.css，**不进导出**），前后端共用单一真相源 —— 后端导出时从磁盘读它
  test/          前端测试（81 测试文件 + setup.ts + fixtures 目录，含组件测试；fixtures/h2-sections.json 与 limo-blocks.json 前后端共享；themeAxisSync 守卫轴注册表 key 集合 ↔ CSS `[data-*]` 跨语言一致）
docs/            设计文档（13 份）
design/          引擎实测标准 + 卡片视觉方案（cards.md）
scripts/         门禁与工具（check-all / check-cli / check-secrets / check-artifact-structure 产物结构统计 等）
```
