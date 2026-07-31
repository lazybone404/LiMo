# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

LiMo — 知识管理系统。输入 URL/文本/文件 → 适配器提取正文 → LLM 生成结构化 Markdown。
设计哲学：**复杂性不是设计进去的，而是后续插件化长出来的**。

## 设计语言

LiMo 采用六轴主题系统，所有视觉元素通过 CSS 变量驱动，零硬编码色值：

```
data-theme="light|dark"           ← 明暗极性
data-accent="sky|ocean|…"        ← 主辅配色（26套: 现代9+国风9+自然8，HSL动态派生）
data-base="warm|neutral|cool|…"  ← 界面基调（13种，覆盖冷暖偏色+护眼）
data-texture="plain|xuan|kraft|…" ← 预览区纹理（6种纸张质感）
data-font="system|kaiti|wenkai|…"← 阅读字体（5种，4系统+1web）
```

### 色板架构（两层）

| 层 | 说明 | 例子 |
|----|------|------|
| `--tk-*` 语义令牌 | 由 `data-theme` + `data-base` 联合驱动，所有组件引用此层 | `--tk-surface`, `--tk-elevated`, `--tk-border` |
| `--md-*` Markdown 变量 | 全部从 `--tk-*` 或 accent/secondary HSL 派生，无独立硬编码 | `--md-h1`, `--md-code-bg`, `--md-blockquote-bg` |

**关键约束：**

- **禁止硬编码色值**：任何 CSS/TSX 不得出现 `#FFFFFF`, `#F5F5F7`, `#E8E8ED` 等裸 hex。全部通过 `--tk-*` 或 Tailwind token 引用。
- **github-markdown-light.css 覆盖**：`main.tsx` 导入的第三方 CSS 硬编码了 `.markdown-body { background-color: #ffffff }` 和 `#f6f8fa` 等冷灰色。`index.css` 用 `!important` + `--tk-*` 变量全部覆盖。**新增 markdown 背景样式时必须同步检查此文件。**
- **基调切换一致性**：`data-base` 切换时所有 `--tk-*` 变量同步变化，`--md-*` 通过 `var()` 引用自动跟随。
- **一键主题包**：`THEME_PRESETS` 提供 20 个预设（护眼4/经典4/粉色4/雅致4/国风4），一键设置 base+accent+texture+font 四轴。

### 六种界面基调

| key | 名称 | 色调偏向 | surface |
|-----|------|---------|---------|
| `warm` | 暖灰 | 微红黄（默认） | `#F4F1EE` |
| `neutral` | 中性白 | 无偏色 | `#F5F5F5` |
| `cream` | 奶油 | 暖黄 | `#F9F6F0` |
| `cool` | 冷灰 | 蓝灰 | `#F2F3F5` |
| `rose` | 玫瑰 | 微粉红 | `#F5F1F2` |
| `sage` | 鼠尾草 | 微绿 | `#F2F4F0` |
| `jade` | 柔绿 | 微绿（护眼） | `#EEF2ED` |
| `amber` | 琥珀 | 暖黄（护眼） | `#F9F4EA` |
| `pearl` | 珍珠白 | 柔和中性 | `#F7F6F3` |
| `mint` | 薄荷 | 清爽微绿（护眼） | `#EDF4EF` |
| `sand` | 沙色 | 温暖沙色 | `#F6F2EA` |
| `lilac` | 丁香 | 柔和淡紫 | `#F3F0F6` |
| `cloud` | 云白 | 微蓝白 | `#F3F4F8` |
| `blush` | 腮红 | 极淡粉 | `#F7F1F2` |

## 文档索引

| 文档 | 内容 | 何时读 |
|------|------|--------|
| [architecture.md](docs/architecture.md) | 六层分层架构，每层模块职责、交互、陷阱 | 理解整体架构和模块边界 |
| [frontend-patterns.md](docs/frontend-patterns.md) | 状态管理、取消机制、TaskPanel、apiUrl | 改前端状态/任务相关代码 |
| [engine-standards.md](design/engine-standards.md) | ⚠️ 历史参考 — PDF 引擎已从 AGPL 迁移到 MIT/BSD | 了解引擎评审标准（非当前引擎列表） |
| [export-pipeline.md](docs/export-pipeline.md) | 导出架构、两条路径设计、Tab→内容映射表、KaTeX/Pygments | 改导出功能、新增 tab |
| [CHANGELOG.md](docs/CHANGELOG.md) | 修改记录：做了什么、为什么、影响范围 | 了解历史决策、避免重复踩坑 |
| [STATUS.md](docs/STATUS.md) | 纯事实清单（文件/端点/引擎计数） | 需要统计数据 |
| [user-scenarios.md](docs/user-scenarios.md) | 10 个用户场景及实现状态 | 理解产品需求 |
| [system-design-spec.md](docs/system-design-spec.md) | 原始设计蓝图（历史参考） | 理解早期设计意图 |
| [engine-standards.md](design/engine-standards.md) | 引擎评审标准+实测数据 | 添加/修改引擎 |
| [plugins.md](docs/plugins.md) | 插件系统标准 — 开发 API + GitHub 安装流程 + 安全声明 | 开发/安装/调试插件 |
| [doc-transcript-design.md](docs/doc-transcript-design.md) | Doc 转录原始提案（部分仍有效） | 理解 Word/PPT/EPUB 早期设计意图 |
| [prompts-inventory.md](docs/prompts-inventory.md) | 全部 LLM 提示词清单 + 审视点清单（9 已修复/8 待处理）+ 温度参数速查 + PromptComposer 标签速查 + Agent SSE 事件流 | 审视/优化 prompt、新增管道需要参考 prompt 风格 |
| [BUILD.md](docs/BUILD.md) | PyInstaller + Tauri 完整打包流程，常见陷阱 | 打包/发布 |
| [qianwen-models.md](docs/qianwen-models.md) | 千问平台模型速查（文本/OCR/ASR）含价格+调用方式 | 配模型/选引擎 |
| [components.md](design/components.md) | Apple HIG 风格组件规格，布局+颜色+字体+组件 | 改 UI 组件/设计系统 |

## 版本号管理

全局版本号定义在根目录 [`VERSION`](VERSION) 文件中，通过 [`scripts/sync-version.py`](scripts/sync-version.py) 同步到 7 处：

| 位置 | 说明 |
|------|------|
| `VERSION` | **单一版本源** |
| `frontend/package.json` | npm 版本 |
| `frontend/src-tauri/tauri.conf.json` | Tauri app 版本 |
| `frontend/src-tauri/Cargo.toml` | Rust crate 版本 |
| `backend/pyproject.toml` | Python 包版本 |
| `backend/main.py` | FastAPI + /health 版本 |
| `backend/app/routers/capabilities.py` | capabilities API 返回 |
| `docs/STATUS.md` | 文档版本号 |

**遵循 SemVer 语义化版本**：`MAJOR.MINOR.PATCH`

```bash
# 查看当前版本
cat VERSION

# 自动升级（SemVer）
python scripts/sync-version.py patch     # 0.1.0 → 0.1.1（Bug 修复）
python scripts/sync-version.py minor     # 0.1.0 → 0.2.0（新功能）
python scripts/sync-version.py major     # 0.1.0 → 1.0.0（破坏性变更）

# 手动指定版本号
python scripts/sync-version.py 0.2.0

# 升级 + 打 Git tag
python scripts/sync-version.py patch --tag

# 升级 + tag + 推送
python scripts/sync-version.py minor --tag --push

# CI 检查一致性（不一致时 exit 1）
python scripts/sync-version.py --check

# 打包前强制同步
python scripts/sync-version.py && bash scripts/build.sh
```

> **禁止手动修改任何文件的版本号**，一律通过 `sync-version.py` 更新。

## 常用命令

### 后端（Python/FastAPI）

```bash
cd backend
# 开发启动（端口 7413，--reload 已启用）
uv run uvicorn main:app --host 127.0.0.1 --port 7413 --reload

# 注意：--reload 只重载代码变更，不重跑 startup event。
# 修改 config.yaml / 适配器注册 / 引擎注册后需手动重启。

# 运行测试（6 个测试）
python tests/test_prompt.py          # Prompt 构建验证
python tests/test_adapters.py        # 适配器注册 + 路由
python tests/test_engines.py         # 引擎注册 + 查找
python tests/test_store.py           # Store CRUD + 并发安全
python tests/test_url_security.py    # URL SSRF 防护
python tests/test_e2e_smoke.py       # 端到端冒烟测试
```

### 前端（React/TypeScript/Vite）

```bash
cd frontend
npm run dev       # 开发服务器，端口 5173，API 代理 → 127.0.0.1:7413
npm test          # vitest（32 tests: store/theme/api，<1s）
npm run build     # 类型检查 + 生产构建
npm run lint      # oxlint
```

### 一键启动

```bash
bash start.sh                  # 启动后端(7413) + 前端(5173)
bash restart-backend.sh [port] # 杀端口进程后重启
```

### Tauri 桌面打包

```bash
cd frontend
npx tauri dev       # Tauri 开发模式（需先启动后端 7413）
npx tauri build     # 生产构建 → NSIS/MSI 安装包
cargo check         # 仅 Rust 类型检查（不完整编译）
```

#### PyInstaller 构建后端

```bash
cd backend
# ⚠️ 必须指定 --distpath，否则输出到 dist/ 而 Tauri 打包的是 bin/ 里的旧文件
uv run pyinstaller LiMoBackend.spec --distpath ../frontend/src-tauri/bin
```

**常见陷阱**：
- spec 文件 `pathex=['backend']` 是相对于项目根目录的，在 `backend/` 目录下执行需注意
- `console=False` 避免黑框闪烁
- `COLLECT` 模式（one-dir）：生成 `LiMoBackend.exe` + `_internal/` 目录
- Tauri `externalBin` 只打包 exe，`resources` 负责 `_internal/`
- Tauri v2 `externalBin` 自动加平台后缀（如 `-x86_64-pc-windows-msvc.exe`），PyInstaller 输出的 exe 需重命名匹配
- 新增 Python 依赖后需同步更新 spec 的 `hiddenimports` 和 `collect_all` 列表
- **certifi 必须加入 spec**：PyInstaller 打包后 SSL 证书路径丢失，启动时需设 `SSL_CERT_FILE` 环境变量
- **searxng .git 目录必须清理**：PyInstaller 收集 `bin/searxng/.git` 后 Tauri 构建会因权限错误(os error 5)失败
- **PyInstaller 必须在项目根目录执行**：spec 中 `pathex=['backend']` 和 `datas` 相对路径依赖 CWD
- **KaTeX + markmap/d3 离线资源**：PyInstaller 通过 `datas` 打包 `backend/static/`（含 katex + markmap子目录）。打包前需确保字体完整（从 `frontend/node_modules/katex/dist/fonts/` 复制 60 字体到 `backend/static/katex/fonts/`），否则导出的 HTML 公式碎掉。HTML 导出中 CSS 的 `url(fonts/...)` 会被 base64 内联，完全自包含
- **ffmpeg 内置**：将系统 ffmpeg.exe + ffprobe.exe 复制到 `frontend/src-tauri/bin/LiMoBackend/ffmpeg/`（PyInstaller 之后、Tauri 之前），Tauri `resources` 自动打包。Rust `enhance_path()` 启动时追加到 PATH + 设 `FFMPEG_BIN_PATH` 环境变量
- **OpenSSL DLL 版本冲突**：`cryptography` 包携带的 `libcrypto-3-x64.dll`（~5MB）会覆盖 Python 原生的（~8MB），导致 `_ssl.pyd` 加载失败（`ImportError: DLL load failed`），最终使后端无法启动。spec 中需排除 cryptography 的 DLL 并显式添加 `sys.base_prefix/DLLs/` 下的原始版本。**每次新增依赖后验证**：直接运行 `LiMoBackend.exe` 确认能输出 `Uvicorn running on http://127.0.0.1:7413`
- **PyInstaller binaries 格式**：`collect_all` 返回 `(dest_name, src_path)`，`Analysis(binaries=[])` 期望 `(src_path, dest_dir)`。追加 binaries 时用 `(abs_src_path, '.')` 格式

## 架构

### 三层管道

```
输入 → Adapter.adapt() → RawTranscript → 存 transcript
                                              ↓
                    ┌─────────────────────────┼──────────────────────────┐
                    │                         │                          │
                    ▼                         ▼                          ▼
              LLM Generator          LLM (image_process)           LLM Chat
         generate_both()          优先→Engine 回退             AgentRunner
                    │                         │                          │
                    ▼                         ▼                          ▼
              MarkdownView              ProcessResult               conversations/
           save_markdown()        save_process_result()
         版本管理(2版)             同 mode 覆盖
```

**生成管道**（视频/网页/文本/文档 → LLM 创作）：
- 输入 `rawText`，触发 `POST /generate/{id}`
- 双产物并行（notes + polished），`source_type` 派发不同 prompt 模板
- OCR 预处理（可选）：文档图片先用廉价 OCR 模型提取文字塞回原文，再用文本 LLM 生成
- 同 mode 保留最近 2 版，前端 v2/v1 切换器
- 存储：按文档聚合 `{doc_dir}/{mode}.md` + `{mode}.prev.md`（两版制），图片用相对路径 `assets/xxx` 引用，API 返回前统一 `rewrite_asset_urls` 改写为绝对 URL

**处理管道**（图片/Document → LLM 优先 → Engine 回退）：
- 输入 `file_path`（原始文件），触发 `POST /process/{id}`
- 4 mode：extract/table/digitize/understand
- 旧端点 `/process-image/{id}` 已废弃，保留兼容
- 同 mode 覆盖（不版本化，OCR 是确定性的）
- 调度：请求 `model_id` → `scene_models.image_process` → `scene_models.generate` → Engine
- 存储：`{doc_dir}/process/{mode}.md`

**对话管道**（Agent 工具循环）：
- `search_web`（Tavily → SearXNG → DDG 三级降级）+ `fetch_url`（trafilatura/Jina）
- 流式 SSE 端点 + 对话持久化（`conversations/` + CRUD）
- 历史上下文近 40 条消息
- 5 个工具：search_docs / lookup_doc / list_docs / search_web / fetch_url

**AI 审核管道**（AI Review Engine，2026-07-15 从 FactCheck 重构而来）：
- 端点 `POST /fact-check/{id}` + SSE 流式 `GET /fact-check/{id}/stream`（URL 不变）
- 四阶段：Static Review（提取 Claim + 分类 Fact/Data/Opinion + 内部一致性 + 风险评分）→ Verification Planning（LLM 批量决策验证策略）→ Evidence Collection + Review（按需搜索 + 多维度评估）→ AI Review Report（红黄绿报告 + 可信度 + Action）
- 验证源 fallback：已有 notes → 已有 polished → 原始转录
- 存储为 MarkdownView（mode=FACT_CHECK），前端 PreviewArea fact_check tab
- `ReviewEngine`（`app/generation/review.py`）— 复用 agent/tools.py 的 search_web/fetch_url
- 工具层：废弃 DDG Instant Answer API → `duckduckgo_search` 库；新增 `search_official` / `get_url_metadata` / `today` / `is_recent`
- Evidence Manager（`app/agent/evidence.py`）— 统一证据格式 + 缓存（TTL 24h）+ URL 去重
- 旧 `FactCheckEngine`（`app/generation/fact_check.py`）保留不动

### 适配器注册

`app/adapters/__init__.py` → `AdapterRegistry.register()`。新增管道只需实现 `accept()` + `adapt()` 并注册即生效。注册顺序决定路由优先级：Video（含 BilibiliSubtitle / YouTubeSubtitle 字幕优先提取）→ Document → Image → Webpage → Text（兜底）。共 5 个适配器。

### 其他注册表

同类插件化模式还用在两处：
- **ProductRegistry**（`app/generation/product_registry.py`）— 产物注册表，定义 notes/polished/fact_check 及其依赖拓扑。新增产物类型只需加一个 `ProductDef` 即可，无需改分发逻辑或前端 UI。
- **ToolRegistry**（`app/agent/tool_registry.py`）— Agent 工具注册中心，替代硬编码的 `_register_chat_tools()`。每个工具是独立 `ToolDef`，注册后自动被发现和调用。

FastAPI 依赖注入（`app/core/dependencies.py`）提供 `get_store` / `get_config` / `get_guard` / `get_config_lock` 四个依赖，消除路由函数中 `request.app.state.xxx` 样板代码。conversations / capabilities / files 路由已迁移，其余可逐步迁移。

### 引擎系统

`app/engines/__init__.py` → `EngineRegistry.register()`。三类引擎：
- **ASR**：bcut-asr（B站必剪，免费云转写）→ dashscope-asr（云端 Paraformer）→ faster-whisper-small / faster-whisper-medium（本地，CTranslate2）
- **OCR**：dashscope-ocr（云端）→ Tesseract（本地便携版，`backend/bin/tesseract/`）
- **Doc**（全 MIT/BSD，AGPL 引擎不内置）：
  - **pypdfium2**（BSD-3，默认）：C 引擎直读文字 + 嵌入原图，毫秒级。pdfplumber (MIT) 辅助表格提取
  - **docling**（MIT）：ONNX 布局模型，精准标题/表格/矢量图
  - **markitdown**（MIT）：Office/EPUB 转 Markdown
  - **python-docx** / **python-pptx** / **openpyxl** / **ebooklib**：各格式专用引擎
- **插件引擎**：AGPL 引擎（pymupdf4llm）以插件形式进程外加载（`~/.limo/plugins/`，command 指向子目录 venv 隔离依赖），`plugin_loader.py` 动态注册进 EngineRegistry，capabilities 的 fileTypeMap 自动追加

引擎绑定通过 `engine_bindings` 动态 key 分配，doc 拆为子场景（`doc:pdf` / `doc:word` / `doc:ppt` / `doc:excel` / `doc:epub`，text 变体引擎已删除无意义），设置页可独立配置。绑定值支持字符串/数字/列表三种格式，`init_engines` 启动时清理失效 key 与无效 id。

### 模型管理

Provider → EnabledModel（能力标签）→ SceneModels（`generate` / `chat` / `image_process`）。`resolve_model()`（`app/core/model_resolver.py`）是唯一模型解析入口，generate/chat/process 统一调用。多模态能力通过 `_PROVIDER_IMAGE_RULES` 按厂商 include/exclude 规则自动检测。

### 前端四栏布局

```
[GenPanel] | [DocLibrary] | [PreviewArea] | [AIPanel]
```
- 可拖拽 resize，50–600px，<80px 自动折叠为 PanelStrip
- 快捷键：`Ctrl+G` 切换生成面板，`Ctrl+I` 切换 AI 面板
- Zustand store 按域拆 7 slices（`store/themeSlice.ts` / `docsSlice` / `contentSlice` / `tasksSlice` / `chatSlice` / `uiSlice` / `toastSlice`），`store/index.ts` 只剩组合层 + 模块副作用，共享类型在 `store/types.ts`（`Store` 为各 slice 交集）。**action 零跨域引用**，新增域 = 新建 slice 文件，消费方 API 零改动。持久化为手写 localStorage（`mbw_*` 9 个 key），无 persist 中间件。`setProcessing(key, null)` 是前缀树形删除语义（删 key 本身 + `key__` 前缀子任务），`clearDocTasks`/`cancelOneTask`/`failTask` 是兄弟函数

### Tauri 桌面壳

```
LiMo.exe (Rust) ──spawn──▶ python.exe (uvicorn :7413)
       │                              │
       ▼                              ▼
  WebView2 加载                 FastAPI 后端
  frontend/dist/                backend/
```

- **开发模式**：Vite dev server (:5173) + 手动启动后端，Tauri 加载 `devUrl`
- **生产模式**：`frontendDist` 静态文件 + `std::process::Command` 直接启动 PyInstaller 打包的 `LiMoBackend.exe`（非 Tauri sidecar()，避免 ACL 复杂度）
- **就绪探测**：Rust 端 HTTP GET `/health` 轮询（500ms 间隔，45s 超时），就绪后 emit `backend-ready` 事件
- **进程管理**：`SidecarHandle(Mutex<Option<Child>>)` 持有进程句柄；窗口关闭 → `RunEvent::ExitRequested` → `child.kill()`
- **重启命令**：`restart_backend_sidecar` Tauri command，kill 旧进程 → spawn 新进程 → 重新探测
- **诊断**：`analyze_install_path()` 检查路径非 ASCII/空格/父目录可写性；`app_log()` 写 `limo_startup.log`；后端 stderr 重定向到 `limo_backend_stderr.log`
- **PATH 增强**：`enhance_path()` 优先追加安装目录下的 `ffmpeg/`（内置），再追加系统常见路径，确保 yt-dlp 等工具能找到 ffmpeg。同时设 `FFMPEG_BIN_PATH` 环境变量指向内置路径
- **API 适配**：`client.ts` 检测 `__TAURI_INTERNALS__` → 生产用 `http://127.0.0.1:7413/api`，开发用 `/api`（Vite proxy）
- **CORS**：`main.py` 使用 `allow_origin_regex=".*"` + `allow_credentials=True` + `_ForceCORSMiddleware` 兜底（Tauri WebView2 使用 `tauri://localhost` / `https://tauri.localhost` 等动态 origin）
- **配置**：`tauri.conf.json` — 1400×900 窗口 + `maximized: true` + `com.limo.app` 标识
- **依赖**：Rust 端 `tauri` (features=["devtools"]) + `tauri-plugin-shell` + `tauri-plugin-log`

## 关键设计决策

### 数据流与持久化
- **配置唯一入口**：`app/core/config_io.py` → `save_config()`。禁止业务模块直接 `yaml.dump`
- **模型解析唯一入口**：`app/core/model_resolver.py` → `resolve_model()`。generate/chat/process 统一调用
- **引擎映射唯一入口**：`_resolve_doc_engine` 用 `model_name` 查找（非 `provider_id`），支持字符串/数字/列表三种绑定格式
- **引擎绑定保存合并**：`set_engine_bindings` 合并而非替换 — 保存 OCR 绑定不会删除 ASR 绑定。`cfg["engine_bindings"] = stored`（全量替换）→ `existing.update(stored)`（增量合并）
- **LLM Key 双向同步**：`update_provider` 自动同步到 `llm.api_key`；generate router 在 `llm.api_key` 为空时从 providers 回退
- **引擎前端后端驱动**：`GET /api/capabilities` 返回 `fileTypeMap`/`scenarioLabels`/`engineLabels`/`styleLabels` 等完整元数据，前端 `constants.ts` 不再硬编码引擎名/文件类型。新增引擎只需后端注册 + `config.py` builtin_models 加一行 desc，前端自动发现

### 导入与去重
- **唯一导入入口**：GenPanel 是唯一导入入口，DocLibrary 只负责搜索/筛选/列表
- **两层去重**：URL 去重（`find_by_url`）在请求到达时拦截；内容去重（SHA256 contentHash）在提取正文后拦截。Store 级拒绝写入
- **导入区纯粹化**：ImportSection 不受 currentDoc 影响，按钮永远是「导入」

### 生成与处理
- **双产物并行**：`generate_both()` 通过 `asyncio.gather` 并行产出 notes + polished。source_type 派发 prompt（视频/网页→通用，PDF/Document→文档 prompt，Excel→分析报告+数据摘要）
- **图片→LLM 双轨制**：视频和文档走不同路径。视频：「AI 画面理解」一键开关 = VL 管线 + 网格图发 LLM（对标 BiliNote），网格图不嵌入笔记。文档：保留文档图片→AI 分析图片→图片预处理三级，文档图片嵌入文章有价值。`send_to_llm = bool(image_analyze)`，前端保证门控。
- **目标语言**：config.yaml `target_language` + prompt 参数化，8 语言可选
- **LLM 续写**：`finish=length` → 自动续写 3 轮，temperature 递减
- **流式 + 取消**：SSE 端点，cancel_event 真正中断 token 消耗，取消保存 partial 可续写
- **Prompt XML 分节**：`PromptComposer.section()` + `compose()` 组装 8 个标签（role/task/source_content/instruction_priority/hard_constraints/style_preference/output_format/user_request），空节自动跳过。`_INSTRUCTION_PRIORITY` 声明三层优先级：hard_constraints > user_request > style_preference
- **Polish/Notes 风格隔离**：Polish 固定忠实整理策略（无 `<style_preference>` 节），格式白名单 `_POLISH_FORMAT_FNS`（toc/translate/formulas/references/images）。Notes 有 `<style_preference>` 节，风格从 `VIDEO_NOTE_STYLES`（9 种）或 `DOC_NOTE_STYLES`（8 种）查找。`normalize_style()` 兼容旧 `meeting_minutes` key
- **风格定义位置**：`app/generation/prompts/notes_styles.py`（COMMON_NOTE_STYLES → VIDEO_NOTE_STYLES / DOC_NOTE_STYLES），旧 `STYLE_MAP`/`DOC_STYLE_MAP` 保留为 deprecated 别名
- **Agent SSE 事件流**：`agent_status`（判定前）→ `tool_start`（带 `display` 安全文案，后端 `_tool_display()` 生成）→ `tool_result`（仅 `summary` 摘要，完整结果只传 LLM）→ `text` 流式 token。C 端不暴露完整工具结果到前端 UI
- **对话文档注入**：`smart_truncate(rawText, 1000)` 按句子边界截断（优先级：`。` > `. ` > `\n\n` > `\n`），不再硬截 `[:1000]`

### 并发与安全
- **并发控制**：`ExecutionGuard` 双层信号量 — `cloud_semaphore(5)` 给生成/处理/云端引擎，`local_semaphore(2)` 给本地 CPU 引擎（faster-whisper）。本地慢操作不抢占云端快操作的槽位。
- **并发安全**：Store `threading.Lock` 防 RMW 竞态；engine `copy.copy()` 防 api_key 串用
- **安全加固**：path traversal 防护、上传 2GB 预检、SSRF 防护（`url_security` 共享校验，5 个下载入口全部加固）

### 前端关键模式
- **GenPanel 类型门控**：5 类型选择器（网页|视频|文本|文件|图片），文件 5 子标签（PDF|Word|PPT|Excel|EPUB），每种类型只看到相关选项
- **PreviewArea 按 sourceType 分派 tab**：视频 3tab / 文档 3tab / Excel 3tab / 图片 5tab / 网页 3tab / 文本 3tab + 思维导图
- **Transcript 视图分派**：JSON+segments → 时间轴（视频）；否则 → 完整 Markdown 渲染
- **处理进度按 tab 粒度**：生成笔记只影响 summary/polished tab，图片处理只影响对应 mode tab
- **主题系统**：六轴独立 — `data-theme`(明暗) × `data-accent`(26套配色) × `data-base`(13种基调) × `data-texture`(6种纹理) × `data-font`(5种字体)。CSS 变量（`--tk-*` 全动态）+ Tailwind v4 `@theme` 引用。一键预设包（20个: 护眼/经典/粉色/雅致/国风各4）。Markdown 用 `--md-*` 变量（全部引用 `--tk-*` 或 HSL 派生，零硬编码色值）
- **主题模块**：`lib/accent.ts` / `lib/texture.ts` / `lib/theme-base.ts` / `lib/theme-presets.ts` / `lib/theme.ts` — 各轴独立管理，Store 统一分发
- **⚠️ github-markdown-light.css 覆盖**：`main.tsx` 导入的第三方 CSS 硬编码 `.markdown-body { background-color: #ffffff }` 和 `#f6f8fa` 等冷灰色。`index.css` 中用 `!important` + `--tk-*` 变量覆盖。新增 markdown 背景样式时必须同步检查此文件。提交前 `grep` 确认零硬编码色值泄露

### 导出系统

**两条路径设计**（详见 [export-pipeline.md](docs/export-pipeline.md)）：
- **ExportMenu**（PreviewArea 工具栏下拉）：快捷单文件导出，内容自动跟随当前预览 tab。MD/复制走客户端 store 直读，HTML/ZIP 走后端
- **ExportPanel**（顶部 Download → 侧边面板）：批量多文档导出，用户手动选 tab/格式/主题。全部走后端

**核心约束**：
- 用户在预览栏看到什么，导出就应该得到什么。禁止导出与预览不一致的内容
- 前端 `ExportMenu.tsx` 中 `TAB_EXPORT` 表是 tab→导出行为的**唯一映射入口**。新增 tab 时在此表加一行即可，无需改其他逻辑
- 后端 `GET /export/{id}` 内容源回退链：markdown（notes/polished/fact_check）→ process_results（图片 extract/table 等）→ transcript（rawText）→ 404
- HTML 导出已对齐预览：Pygments 代码高亮 + 语言标签、GFM 删除线/任务列表、时间戳 badge（Content 蓝色/Screenshot 橙色）、标题锚点 id
- MD 导出自动将图片路径重写为绝对 URL（基于 `request.base_url`），外部查看不再裂图
- 图片文档没有 markdown 条目，HTML 导出依赖 `get_process_results()` 回退
- mindmap HTML 导出走独立端点 `/export/{id}/mindmap`（markmap 库需要特殊 HTML 模板）
- `apiUrl()` / `downloadFromUrl()` 内部已拼接 BASE（含 `/api`），调用方传路径时**禁止加 `/api` 前缀**，否则产生 `/api/api/...` 导致 404

**已修复的关键问题**（2026-07-10）：
- `/api/api/` 路径重复 → 6 处修复
- data_preview tab HTML 导出 fallthrough 到 notes → 修复为 transcript
- skeleton tab 导出笔记而非骨架 → 修复为 rawText
- mindmap tab HTML 导出笔记 → 修复为走 mindmap 端点
- 图片 tab HTML 导出 404 → 后端增加 process result 回退
- ExportPanel 预览显示无关文档 → 限制为仅当选中=当前文档时显示
- ExportPanel "导出全部产物"按钮仅单文档 → 改为支持多文档（POST /export/batch）

- 图片收集/重写/内联三个核心函数 → 4 端点改造
- HTML 自包含（KaTeX 预渲染·内联 CSS·零 CDN·4 主题），默认 `limo` 匹配应用内预览的 `--md-*` CSS 变量
- ZIP 三种图片模式（独立文件夹/base64 内嵌/不包含）+ 多产物标签（笔记/整理版原文/事实验证/提取文字等）
- 思维导图：SVG 下载 + markmap 交互式 HTML（~980KB·暗色自适应）

### 安全
- **敏感信息脱敏**：LLM Key / Cookie / Tavily Key 在 `GET /api/config` 中统一脱敏返回（`mask_sensitive`），保存时 `merge_sensitive` 防 `***` 覆盖真实值
- **SSRF 防护**：`url_security.validate_url()` — 14 个内网/保留 IP 段黑名单 + DNS 解析二次校验 + 协议白名单。⚠️ WebpageAdapter 曾遗漏 SSRF 校验（已修复），新增管道必须加
- **路径遍历防护**：`_safe_filename()` 取文件名末段 + `resolve().relative_to()` 双重校验，图片/文件服务均防护
- **Markdown XSS 防护**：前端 `<a>` 组件拒绝 `javascript:`/`data:` 协议，ReactMarkdown 默认不渲染原始 HTML
- **SSL 证书 (PyInstaller)**：启动时从 `certifi.where()` 设 `SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` 环境变量。缺此则 dashscope SDK / openai / yt-dlp 的 HTTPS 请求全部失败
- **代理注入**：启动时从 `config.fetch.proxy` 读到代理地址后设 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，确保所有 HTTP 库（dashscope/requests/httpx/yt-dlp）统一走代理

### 其他
- **数学公式**：全局 `remark-math` + `rehype-katex`，LaTeX `$$` 块自动排版
- **日志系统**：`contextvars.ContextVar` 注入 `request_id` → ASGI middleware，全链路追踪。`MemoryHandler`（`main.py` 中配置）缓存最近 1000 条日志供前端 LogPanel 轮询（`GET /api/logs`），重启后清空
- **客户端断连监测**：`app/core/disconnect.py` — `@asynccontextmanager` 封装，5 个 SSE 端点复用，新增端点直接 `async with disconnect_watcher(request, cancel_event):`
- **引擎自动降级开关**：`engine_auto_fallback` 配置项（默认 `False`），设置页一键切换。6 个引擎解析点全部尊重此开关，关闭时引擎失败直接报错，不再静默降级
- **Tesseract 便携版**：`backend/bin/tesseract/` 内置，启动自动注册并绑定 OCR，开箱即用
- **EPUB**：ebooklib 章节+骨架提取，前端 EpubSkeletonPanel 章节树，支持选中章节/全书生成
- **模糊帧过滤**：`frame_quality.py` — Laplacian 方差 + 熵 + 边缘密度评分，视频抽帧时自动丢弃低质量帧
- **KaTeX 离线缓存**：`_get_katex_assets()` 从 node_modules/static 加载 CSS/JS/字体，CSS 中 `url(fonts/...)` 替换为 base64 data URI（60 个字体），导出 HTML 完全自包含（零 CDN，断网可用）
- **KaTeX 服务端渲染**：`_render_formulas_katex()` 调 Node.js 脚本预渲染公式。打包环境无 Node.js → 先 `shutil.which("node")` 预检，不存则静默回退客户端渲染
- **markmap 离线化**：`markmap.ts` 不调用 `markmap-common` 的 `loadCSS`/`loadJS`（动态 CDN 加载会被 Edge WebView2 跟踪防护拦截），KaTeX CSS 在 PreviewArea 中静态 import，hljs 由 react-syntax-highlighter 内联
- **设置持久化**：`ProviderSettings.tsx` 中引擎/LLM Key 的保存不能仅靠 `onBlur`（组件卸载时不触发），需加 `useEffect` cleanup 用 ref 兜底保存
- **上下文窗口可配置**：`addModel()` API 支持 `context_window` 参数，ProviderSettings 中展示（如 "1M ctx"），前端按模型粒度配置
- **路由异常标准化**：全部 11 个路由模块已统一使用 AppError 体系（2026-07-25 完成 config/model/files 最后 15 处迁移）
- **Content-Disposition 中文文件名**：必须用 RFC 5987 `filename*=UTF-8''` 编码，否则 Starlette 会尝试 latin-1 编码 → UnicodeEncodeError
- **日志函数签名**：`app.core.logging.warning(msg, **kwargs)` 只接受 1 个位置参数 + kwargs，不能用 `%s` 占位符多参数调用。多参数用 f-string
- **空文本去重碰撞**：空字符串 SHA256 固定为 `e3b0c44...`，两个空导入会误判为重复。TextAdapter 需检查 `text.strip()` 非空
- **前端 apiUrl() 辅助函数**：`client.ts` 导出 `apiUrl(path)`，Tauri 下拼接 `http://127.0.0.1:7413/api`，浏览器下保持相对路径。所有 HTTP 请求必须走这个函数或 `apiFetch`
- **processingTasks key 粒度**：`${docId}__${kind}` — 同一文档的不同操作（retranscribe/generate/process）不互相覆盖，store 提供 `clearDocTasks(docId)` 批量清除
- **设置页 overlay**：`App.tsx` 中设置页用条件渲染 overlay 而非替换整个 render tree，避免 GenPanel 卸载丢失进行中的任务状态
- **SSE 断开保护**：`streamGenerate` 追踪 `receivedDoneOrError` 标志，连接异常断开时触发 `onError('连接意外断开')` 防止 `generating` 永不复位
- **操作互斥**：GenPanel 中 `lockOtherOps` — 转录/图片处理中禁止生成，反之亦然。`Ctrl+G` 操作进行中拦截+Toast 提示
- **取消机制**：6 个端点全部覆盖 — generate/ingest/retranscribe/fact_check/chat/process 均有 `cancel_event` + `_watch_disconnect`（0.5s 检测）。LLM 调用逐 chunk 检查取消信号停止 token 消耗。本地同步引擎（faster-whisper）用 `asyncio.to_thread` 放到线程池避免卡死事件循环，引擎内部逐 segment 检查 `cancel_event.is_set()` 立即停止 CPU

## 关键模式（2026-07-10 审查沉淀，2026-07-11 追加）

以下模式是实现中易错的关键约束，新增代码必须遵守。

### 前端

1. **processingTasks 键格式**：`{docId}__{kind}`，永不用裸 `docId` 查找。`clearDocTasks` 只清 store，必须先 `cancelTask` 再 `clearDocTasks`。详见 [frontend-patterns.md](docs/frontend-patterns.md)。
2. **取消操作**：`cancelTask(docId)` 用裸 docId（非完整键），导入固定用 `"__import__"`。`createAbort` 注册的 key 必须与 `cancelTask` 的 key 一致。
3. **所有 HTTP 请求**：必须走 `apiUrl()` 或 `apiFetch()`。裸 `fetch('/api/...')` 在 Tauri 下失败。
4. **API Key 保存**：`(val || undefined) ?? current` — 空字符串不是 nullish，会覆盖真实 key。
5. **操作互斥**：`lockOtherOps = retranscribing || imageProcessing`。Ctrl+G 拦截检查 `generating || processingTasks > 0`。
6. **query string 参数序列化**：`streamGenerate` 中新增参数时必须在 `client.ts` 加对应的 `params.set()`。布尔/数字参数用 `!== undefined` 而非 truthiness，否则 `false`/`0` 被当作 falsy 跳过，后端收到默认值。**本次已修复**：`dedupe_enabled`、`unit_width`、`unit_height`、`save_quality`、`frame_quality_filter` 此前因缺失序列化从未生效。
7. **React 列表 key 唯一性**：`React.Children.map` 会展平嵌套数组，若各子元素内部都用 `key={i}`（数组索引），展平后产生重复 key。使用 `React.useId()` 生成组件实例唯一前缀 `key={`${id}-${i}`}`。**本次已修复**：markdown-components 中 `p`/`li` 组件的时间戳 badge。
8. **视频画面理解对标 BiliNote**：「AI 画面理解」一个开关 = VL 管线 + 网格图发 LLM。网格图不嵌入笔记（对读者无意义）。不要恢复 formatImages/imageAnalyze/imageOptimize 三级嵌套。

### 后端

9. **信号量**：`await guard.semaphore.acquire()` 后立即 `try:`，否则异常泄漏。
10. **配置深拷贝**：`DEFAULT_CONFIG` 必须 `copy.deepcopy()`，浅拷贝污染嵌套 dict。
11. **引擎绑定合并**：保存用增量合并，`null`=不修改，`[]`=清除绑定。
12. **适配器错误**：失败必须 `raise FatalError`，不要 `return AdapterOutput(engine="none")`。
13. **markdown→HTML 导出**：移除 `nl2br` 扩展，GFM 特性（删除线/任务列表/表格前空行）用 `_preprocess_gfm()` 正则预处理，Pygments 可选依赖。详见 [export-pipeline.md](docs/export-pipeline.md)。
14. **引擎图片双模式**：所有 Doc 引擎必须支持 `media_dir` 非空（写文件引用 `![alt](img_0001.png)`）和 `media_dir` 为空（内嵌 base64 `![alt](data:image/...;base64,...)`）两种模式。不可用 `if media_dir` 守卫静默跳过图片提取。已统一：PyMuPDF / PyMuPDF4LLM / MarkItDown / DocxEngine / PptxEngine / EpubEngine。
15. **本地引擎异步化**：耗时引擎（faster-whisper 等）必须通过 `asyncio.to_thread` 调用以释放事件循环，否则 `_watch_disconnect` 协程被卡死无法 set cancel_event。引擎 `execute()` 内需逐段检查 `cancel_event.is_set()` 并在取消时抛 `FatalError`。新增耗时本地引擎时遵守此模式。
16. **prepare() 参数对称**：`ContentPreparer.prepare()` 的 `send_to_llm` 和 `optimize_images` 参数在双产物路径和单产物路径必须保持一致。**本次已修复**：单产物路径此前漏传，导致「AI 分析图片」在只选 notes 或只选 polished 时静默失效。
17. **图片优化覆盖率**：`ContentPreparer` 中新增图片路径时，必须确保 `optimize_images` 同时覆盖「嵌入文本」和「发给 LLM」两个方向。**本次已修复**：VL 图片和文档 data URI 发给 LLM 时此前不走优化。
18. **图片剥离占位符**：base64 剥离后不能用 `![alt]`（Markdown 渲染成 `<img src="">`），改用 `[📷 alt]` 纯文本占位符。否则空 src 触发浏览器重载整页。
19. **screenshot 后处理双产物对称**：`_post_process_screenshots` 必须同时应用于 polished 和 notes。`generate_both` 此前只处理 notes。**本次已修复**。
20. **异常类导入完整性**：新增 `raise FatalError`/`AppError` 等自定义异常时必须确认文件顶部有对应 import。PyInstaller 打包后不会通过间接导入自动补全。**本次已修复**：document.py（FatalError）、model.py（AppError）、export.py（shutil）。
21. **`appUrl()` 统一应用内 URL**：前端所有图片组件（MarkdownImage / DocLibrary / VideoBanner）必须用 `appUrl()` 而非裸 URL。打包端无 Vite proxy，`/api/` 和 `/static/` 路径需转为 `http://127.0.0.1:7413` 绝对 URL。`apiUrl()` 仅用于 `/api/` 下的 fetch 请求。
22. **`async def` 全调用链 `await`**：将同步函数改为 `async def` 后，必须检查所有调用点是否加 `await`。Python 不会为漏掉的 await 报编译错——静默返回 coroutine。**本次已修复**：video.py 三处 `_transcribe` 调用，两处漏 `await`。
23. **`subprocess.CREATE_NO_WINDOW`**：所有 Windows 下 `subprocess.run/Popen` 调用必须加 `creationflags=subprocess.CREATE_NO_WINDOW`，否则 ffmpeg/yt-dlp 等会弹出 CMD 黑框。当前已涵盖 video.py、video_reader.py、video_helper.py、asr_cloud.py、export.py 共 5 个文件。新增 subprocess 调用时必须携带此 flag。
24. **文件路径基于 `store.root`**：打包环境下 `os.getcwd()` 指向 exe 所在目录而非数据目录。所有文件读写路径（截图、导出、临时文件等）必须基于 `store.root`。`StaticFiles` 挂载也应在 startup event 中用 `store.root` 动态计算路径，而非模块级 `os.getcwd()` 求值。
25. **`_watch_disconnect` 标准取消模式**：5 个端点（generate/ingest/retranscribe/chat/process）均使用相同的 `_watch_disconnect` 闭包 + `cancel_event` + `disconnect_task` 模式。新增支持取消的 SSE/长时端点时复用此模式，保持 0.5s 检测间隔和 `finally` 清理逻辑一致。未来可提取为 `app/core/disconnect.py` 公共工具。
26. **禁止函数内重复导入**：不要在函数内部写已存在于模块顶部的 `from X import Y`。Python 解析到函数内的赋值语句后会将 `Y` 视为整个函数的局部变量——若函数内导入在条件分支中且条件未命中，函数内其他引用会抛出 `UnboundLocalError`。**本次已修复**：process.py:215、model.py:578 的 `EngineRegistry` 阴影导入。新增代码时：函数内仅导入模块级不存在的名称（如打破循环导入），且放在函数顶部无条件路径上。
27. **remark 插件顺序**：`remarkMath` 必须在 `remarkGfm` 之前。否则公式内的 `_`（下标）和 `<`（`x_{<k}`）会被 GFM 先解释为斜体/HTML 标签 → `$$` 定界符破裂 → KaTeX 渲染失败。**本次已修复**：markdown-plugins.ts。
28. **`engine_auto_fallback` 语义**：该开关仅控制**引擎失败后是否重试下一个**，不控制扩展名兜底（pdf→pymupdf 等）。扩展名兜底是默认行为，新用户无绑定时必须可用。**本次已修复**：document.py adapt() 中扩展名兜底移出 auto_fallback 条件块。
29. **`init_engines` 禁止每次启动覆盖配置**：引擎初始化中的 `save_config` 会写全量配置到 config.yaml。若此调用在每次启动都触发，一旦内存中的 config 对象缺失敏感字段（API Key 等），这些字段将永久丢失。**本次已修复**：ASR 绑定逻辑从「强制先删后插重排」改为「仅空列表初始化」，确保后续启动 asr_changed=False。新增引擎/绑定变更时才保存。
30. **FastAPI Depends 依赖注入**：用 `store=Depends(get_store)` 替代函数体内 `request.app.state.store`，消除样板代码。`app/core/dependencies.py` 提供 `get_store/config/guard/config_lock` 四个依赖。全部 11 个路由已迁移完成（2026-07-30）。
31. **disconnect_watcher 客户端断连监测**：`app/core/disconnect.py` 提供 `@asynccontextmanager` 封装的 `disconnect_watcher(request, cancel_event)`。替换了 5 个路由中 ~45 行重复的 `_watch_disconnect` 实现，修复了 process.py 资源泄漏和 chat.py 缺 await 两个 bug。新增长时端点时复用此工具。
## 待办事项

> 图例：✅ 已完成　🟡 基础可用，待增强　❌ 未开始

### 📥 内容导入

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | URL 导入 | 网页/视频/图片 URL → 适配器路由 → 正文提取 |
| ✅ | 文本粘贴 | 直接粘贴纯文本/Markdown |
| ✅ | 文件上传 | 本地文件（PDF/Word/PPT/Excel/EPUB/图片/视频），最大 2GB |
| ✅ | URL 去重 | `find_by_url` 拦截重复 URL |
| ✅ | 内容去重 | SHA256 contentHash 拦截重复内容 |
| ✅ | SSE 流式导入 | 实时进度推送 + 心跳保活 + 客户端断开不丢结果 |
| ✅ | 平台认证 | B站/YouTube Cookie 注入，自动解析 Netscape 格式 |
| ✅ | 文件拖拽上传 | ImportSection `onDragOver`/`onDrop` + `validateDropFile` 扩展名校验，拖拽区高亮反馈 |

### 📝 转录 & 引擎

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | ASR 引擎 | bcut (B站必剪免费云转写) + dashscope Paraformer (云端) + faster-whisper small/medium (本地) |
| ✅ | OCR 引擎 | dashscope VL (云端) + Tesseract 便携版 (内置 `backend/bin/tesseract/`) |
| ✅ | Doc 引擎 | pypdfium2 (PDF默认,BSD-3) + docling (PDF布局,MIT) + pdfplumber (表格,MIT) + python-docx/pptx/xlsx + openpyxl + markitdown + ebooklib |
| ✅ | 引擎绑定 | `engine_bindings` 按子场景配置，doc 拆为 text+image / text-only / scan-ocr / word / ppt / excel / epub |
| ✅ | 引擎降级开关 | `engine_auto_fallback`，失败直接报错，不再静默 fallback |
| ✅ | 视频帧提取 | OpenCV 抽帧 + 网格拼接 + 去重 |
| ✅ | 模糊帧过滤 | Laplacian 方差 + 熵 + 边缘密度评分，丢弃低质量帧 |
| ❌ | PDF Layer 2 | 表格数字化/理解（pdfplumber 已成熟提取表格，本项取消） |

### ✨ LLM 生成

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 双产物并行 | notes + polished，`asyncio.gather` 并发 |
| ✅ | prompt 按类型派发 | 视频/网页→通用，PDF/文档→文档 prompt，Excel→分析+数据摘要 |
| ✅ | SSE 流式输出 | 逐 token 推送 + cancel_event 真正中断 |
| ✅ | 自动续写 | `finish=length` → 3 轮续写，temperature 递减 |
| ✅ | 版本管理 | 同 mode 保留最近 2 版，前端 v2/v1 切换 |
| ✅ | 模型解析 | `resolve_model()` 唯一入口，多模态能力自动检测 |
| ✅ | 目标语言 | config `target_language`，8 语言可选 |

### 🔍 图片处理

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 4 种模式 | extract (文字提取) / table (表格) / digitize (数字化) / understand (理解) |
| ✅ | LLM 优先 | 请求 model_id → scene_models.image_process → scene_models.generate → Engine fallback |
| ✅ | 图片优化 | `optimize_images` 开关，PIL 缩放 ≤ 1024px |
| ✅ | 同 mode 覆盖 | OCR 结果确定性，不版本化 |

### 💬 AI 对话

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 5 个工具 | search_docs / lookup_doc / list_docs / search_web / fetch_url |
| ✅ | 搜索多级降级 | Tavily API → SearXNG 自动启动 → DuckDuckGo |
| ✅ | SearXNG 便携版 | 自动检测 `.exe` 启动，asyncio.Lock 防并发 |
| ✅ | SSE 流式 + 持久化 | 逐 token 推送 + 自动保存对话 + 40 条历史上下文 |
| ✅ | 对话 CRUD | 新建/重命名/删除/追加消息 |

### ✅ AI 审核（原事实验证）

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 四阶段管道 | Static Review → Verification Planning → Evidence Collection → Report |
| ✅ | SSE 流式进度 | static_review → verify → review → done |
| ✅ | 前端 tab | PreviewArea fact_check tab + 纯 Markdown 流式展示（和其他产物一致） |
| ✅ | 验证源 fallback | notes → polished → transcript |
| ✅ | Evidence Manager | 统一证据格式 + 缓存（TTL 24h）+ URL 去重 |
| ✅ | 工具增强 | DDG 真实搜索（duckduckgo_search 库）+ Official Search + 日期工具 + URL 元信息 |
| ✅ | verify_mode 设置 | LLM（默认，模型知识）和 Web（联网搜索）两种验证方式，设置页可选 |
| ✅ | 报告可交互 | 参考来源链接后处理注入 + 前端纯 Markdown 流式展示 |
| ✅ | Chunk 提取 | `split_into_chunks()` 按句子边界分片 + 并行 LLM 提取 + 去重（`SequenceMatcher` 相似度>0.85），最多32块 |
| ❌ | 专业模式 | 学术/法律/医疗专用搜索源（暂不实施） |

### 📤 导出

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | MD 下载 | 前端 Blob 直接下载 |
| ✅ | HTML 导出 | 代码高亮(Pygments)+GFM+时间戳badge+标题锚点，4 主题，完全自包含 |
| ✅ | KaTeX 内联 | CSS/JS/字体(60个) base64 内联，零 CDN，断网可用 |
| ✅ | MD 图片路径 | 导出时自动重写为绝对 URL，外部查看不裂图 |
| ✅ | ZIP 批量 | 全部产物打包，图片独立文件夹 |
| ✅ | 思维导图 | SVG 下载 + markmap 交互式 HTML (~980KB，暗色自适应) |
| ✅ | 对话导出 | Markdown + JSON，支持批量 ZIP |
| ✅ | 导出进度反馈 | `POST /export/custom/stream` SSE 流式进度 + `GET /export/download/{temp_id}` 临时下载 |
| ❌ | 图片预处理复用 | 导入时已 optimize_images，导出 ZIP 未复用压缩后图片 |

### 🎨 前端 UI/UX

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | 四栏布局 | GenPanel \| DocLibrary \| PreviewArea \| AIPanel，可拖拽 resize |
| ✅ | PanelStrip | <80px 自动折叠为图标条 |
| ✅ | 快捷键 | `Ctrl+G` 生成面板，`Ctrl+I` AI 面板 |
| ✅ | 主题系统 | 六轴独立 — `data-theme`(明暗) × `data-accent`(26套配色) × `data-base`(13种基调) × `data-texture`(6种纹理) × `data-font`(5种字体) × 强度滑条。CSS 变量（`--tk-*` 全动态）+ Tailwind v4 `@theme` 引用。一键预设包（20个: 护眼/经典/粉色/雅致/国风各4）。Markdown 用 `--md-*` 变量（全部引用 `--tk-*` 或 HSL 派生，零硬编码色值） |
| ✅ | GenPanel 类型门控 | 5 类型 × 5 子标签，逐类型显隐选项 |
| ✅ | EPUB 骨架面板 | EpubSkeletonPanel 章节树 + 选中章节/全书切换 |
| ✅ | 事实验证 UI | fact_check tab + 流式进度指示 + 报告渲染 |
| ✅ | 首次启动向导 | SetupWizard 检测 ffmpeg/SearXNG 等系统依赖 |

### 🛡️ 安全 & 运维

| 状态 | 功能 | 说明 |
|------|------|------|
| ✅ | SSRF 防护 | 14 个内网/保留 IP 段黑名单 + DNS 二次校验 + 协议白名单 |
| ✅ | 路径遍历防护 | `_safe_filename()` + `resolve().relative_to()` 双重校验 |
| ✅ | 敏感信息脱敏 | LLM Key / Cookie / Tavily Key 统一 `mask_sensitive` + `merge_sensitive` |
| ✅ | XSS 防护 | Markdown `<a>` 拒绝 `javascript:`/`data:` 协议 |
| ✅ | 并发控制 | `ExecutionGuard` Semaphore(3)，generate/process/retranscribe 已接入 |
| ✅ | 上下文窗口可配置 | `addModel()` API + ProviderSettings 展示 |
| ✅ | 日志全链路追踪 | `contextvars.ContextVar` 注入 `request_id`，前端日志面板 |
| ✅ | 旧格式错误迁移 | 全部 11 个路由模块统一 AppError 体系，0 处旧格式残留 |

### 📊 汇总

| 域 | 完成 | 总计 | 进度 |
|----|------|------|------|
| 📥 内容导入 | 8 | 8 | 100% |
| 📝 转录 & 引擎 | 8 | 9 | 89% |
| ✨ LLM 生成 | 7 | 7 | 100% |
| 🔍 图片处理 | 4 | 4 | 100% |
| 💬 AI 对话 | 5 | 5 | 100% |
| ✅ AI 审核 | 9 | 10 | 90% |
| 📤 导出 | 8 | 9 | 89% |
| 🎨 前端 UI/UX | 8 | 8 | 100% |
| 🛡️ 安全 & 运维 | 8 | 8 | 100% |
| **合计** | **65** | **68** | **96%** |
