# AGENT-ONBOARDING — 其他 AI Agent 快速上手 / 接手开发指南

> **定位**：本文是「课本」，[CLAUDE.md](../CLAUDE.md) 是「手册」。
> 手册给你全部事实（配置、命令、模式清单）；本文给你**上手路径**——从零跑通、读懂架构、会加功能、会打包、会发布。
> 读完本文 + 随时查手册，即可独立接手开发。

---

## 0. 导读

### 0.1 这是什么项目

LiMo（沥墨）——知识管理系统。输入 URL/文本/文件 → 适配器提取正文 → LLM 生成结构化 Markdown。

- **后端**：Python 3.12+ / FastAPI（`backend/`），端口 7413
- **前端**：React 19 + TypeScript + Vite + Zustand + Tailwind v4（`frontend/`）
- **桌面壳**：Tauri 2（Rust，`frontend/src-tauri/`），生产模式 spawn 后端 exe
- **数据**：本地文件系统 + SQLite（`~/.limo/`），无数据库服务器

### 0.2 核心设计哲学（必须先懂）

1. **口子原则**：核心保持轻量；一切重依赖 / 协议风险 / 跨平台难维护 / 法律风险能力 → 插件化（`~/.limo/plugins/`）。判断「内置 vs 插件」四标准见 [plugins.md](plugins.md)。
2. **失败显式化**：引擎绑定只取第一个，失败直接报错，**无隐式降级**。显式重试/备用是独立语义。
3. **注册表驱动**：适配器/引擎/产物/工具/面板/tab 全部注册表化——新增能力 = 注册一个定义，不动分发逻辑。
4. **预览即所得**：用户在预览栏看到什么，导出就应该得到什么。

### 0.3 文档地图

| 文档 | 内容 | 什么时候读 |
|------|------|-----------|
| [CLAUDE.md](../CLAUDE.md) | 全部事实：命令/架构/65 条关键模式/待办 | 改任何代码前 |
| [architecture.md](architecture.md) | 六层分层架构 + 每层模块职责 | 理解模块边界 |
| [frontend-patterns.md](frontend-patterns.md) | 前端状态管理/取消机制/TaskPanel | 改前端状态/任务 |
| [export-pipeline.md](export-pipeline.md) | 导出两路径 + tabRegistry/contentSources | 改导出/tab |
| [prompts-inventory.md](prompts-inventory.md) | 全部 LLM prompt 清单 + 审视点 | 改 prompt |
| [plugins.md](plugins.md) | 插件契约/安装/发布 | 做插件 |
| [对话协议.md](对话协议.md) | Agent SSE 事件流 + 对话 REST 契约 | 改对话 |
| [BUILD.md](BUILD.md) | 打包步骤（PyInstaller + Tauri） | 打包 |
| [qianwen-models.md](qianwen-models.md) | 千问平台模型速查 | 配模型 |
| [AGENTS.md](../AGENTS.md)（仓库根） | `limo` 只读 CLI 的对外契约：命令/退出码/`--json` 硬约定/改 CLI 的六条硬约束 | 改 `limo_cli/` 或 `/api/cli/*` |
| **本文** | 上手路径 + How-to + 打包详细 + 调试 | 接手开发 |

---

## 1. 从零跑通

### 1.1 前置

- Python 3.12+，Node.js 18+（20 更稳）
- 国内网络建议先设镜像（pip 清华源 / npm npmmirror）

### 1.2 开发环境启动（三选一）

**方式 A — 一键脚本**（最简单）：

```bash
bash start.sh
# 后端 127.0.0.1:7413（--reload）+ 前端 127.0.0.1:5173
# 浏览器打开 http://localhost:5173
```

**方式 B — 分步**：

```bash
# 终端 1：后端
cd backend
uv run uvicorn main:app --host 127.0.0.1 --port 7413 --reload

# 终端 2：前端
cd frontend
npm run dev
```

**方式 C — Tauri 桌面**（调桌面壳，需先起后端）：

```bash
cd frontend
npx tauri dev
```

### 1.3 首次配置（必做，否则功能不可用）

打开设置 →「模型 & 引擎」：
- **LLM 厂商**：填 DeepSeek/OpenAI/DashScope 任一 API Key（生成/对话靠它）
- **DashScope Key**：云端 OCR/ASR 需要（导入图片/视频缺 key 时前端有主动提示条）
- 内置免费引擎无需 Key：bcut-asr（B 站语音转写）、pypdfium2（PDF 文字提取）

### 1.4 快速验证链路

1. 粘贴一个网页 URL → 导入 → 转录面板出现正文
2. 点「生成」→ 双产物（笔记 + 整理版原文）流式输出
3. 预览 tab 切换、导出 MD/HTML
4. 选中文档 → `Ctrl+I` → 对话提问（Agent 工具搜索）
5. （可选）命令行：`cd backend && python -m limo_cli status` —— 后端没起时 `doctor --local`
   仍可直开 Store 诊断；`--json` 输出即 agent 可复用的契约（见 [AGENTS.md](../AGENTS.md)）

### 1.5 后端启动时序（知道为什么有时慢）

`main.py` lifespan 启动顺序：

```
注入代理 (config.fetch.proxy → HTTP_PROXY/HTTPS_PROXY)
→ 设 SSL_CERT_FILE (certifi, PyInstaller 必需)
→ load_config + 启动同步 (_sync_missing_providers / _migrate_legacy_dir)
→ init_engines（引擎绑定清理 + 插件扫描 init_plugins）
→ 生成 sample_doc（首次体验示例文档）
→ FTS5 搜索索引 rebuild（transcripts_fts 全量重建）
→ 挂载 StaticFiles（基于 store.root 动态路径）
→ 就绪（/health 返回版本）
```

> ⚠️ `--reload` 只重载代码，**不重跑 startup event**。改 config.yaml / 适配器注册 / 引擎注册后需手动重启后端。

---

## 2. 架构全景

### 2.1 三层管道（核心）

```
输入 → Adapter.adapt() → RawTranscript → 存 transcript
                                              ↓
                    ┌─────────────────────────┼──────────────────────────┐
                    ▼                         ▼                          ▼
              LLM Generator          LLM (image_process)           LLM Chat
         generate_both()          优先→Engine 回退             AgentRunner
                    │                         │                          ▼
                    ▼                         ▼                    conversations/
              MarkdownView              ProcessResult
           save_markdown()        save_process_result()
```

- **生成管道**（视频/网页/文本/文档 → 笔记）：`POST /generate/{id}` + `/stream`，双产物并行 `asyncio.gather`
- **处理管道**（图片/文档 → OCR）：`POST /process/{id}`，4 mode（extract/table/digitize/understand）
- **对话管道**（Agent 工具循环）：`POST /chat` + `/chat/stream`，5 工具，SSE 流式

### 2.2 后端目录（`backend/app/`）

| 目录 | 职责 | 关键文件 |
|------|------|---------|
| `adapters/` | 5 适配器（Video/Document/Image/Webpage/Text） | `__init__.py` 注册顺序决定路由优先级 |
| `engines/` | 10 内置引擎 + 插件加载器 + ASR 契约 | `transcript.py`（TranscriptResult 契约） |
| `routers/` | 19 个路由文件（18 业务模块 + `__init__.py`） | `ingest/generate/process/chat/export/files/conversations/capabilities/cli/synthesis/...` |
| `generation/` | 生成/处理/OCR 预处理/prompt | `prompt_builder.py`、`ocr_preprocessor.py` |
| `agent/` | LLM Agent 工具 + 会话管理 | `tools.py`、`tool_registry.py`、`compress.py`、`session.py`、`prompts/` |
| `llm/` | LLMService + AgentRunner | `service.py`、`agent.py` |
| `core/` | 配置/类型/注册表/错误/断连/FTS5 搜索/cookie | `config_io.py`、`model_resolver.py`、`search.py`、`protocols.py` |
| `downloaders/` | 平台下载器 | yt-dlp 通用路径（含抖音，走 `_download` 的 UA/Referer 覆盖） |
| `utils/` | 视频管线 | video_reader/video_helper/screenshot_marker/frame_quality |
| `storage/` | 文件系统存储 | Store 并发安全 |

### 2.3 前端目录（`frontend/src/`）

| 目录 | 职责 |
|------|------|
| `components/` | gen-panel / doc-library / preview / ai-chat / settings / ui |
| `store/` | Zustand：`types.ts` + 7 slices + `chatStore.ts`（对话独立）+ `streamBuffers.ts`（流式缓冲） |
| `api/` | API 客户端 16 文件（core + 域文件 + barrel + sse/taskManager） |
| `hooks/` | useTaskRunner 等 |
| `lib/` | tabRegistry / contentSources / slotRegistry / 主题模块 / health / download |

### 2.4 数据持久化

- **文档/转录/产物**：`~/.limo/` 文件系统（Store 层），每文档 `{doc_dir}/`：`transcript.json`、`{mode}/v{N}.md`（**版本归档**，一版一文件、全部保留）、`process/{mode}.md`、`assets/`（`{mode}.md` / `{mode}.prev.md` 是遗留两版制布局，启动时一次性迁移）
- **对话**：`conversations/`（JSONL + 会话目录）
- **配置**：`config.yaml`（**唯一写入口 `app/core/config_io.py` → `save_config()`**，禁止业务模块直接 yaml.dump）
- **FTS5 搜索**：SQLite `search.db` → `transcripts_fts`（CJK 空格插入分词）
- **前端持久化**：手写 localStorage `mbw_*` 16 个 key（无 persist 中间件）

---

## 3. 六大注册表（改代码前先懂这个）

「注册表驱动」是全项目最核心的扩展机制。新增能力几乎都是「注册一个定义」：

| 注册表 | 位置 | 注册什么 | 新增一个怎么做 |
|--------|------|---------|---------------|
| `AdapterRegistry` | `app/core/registry.py` | 适配器**类**（accept + adapt） | `AdapterRegistry.register(MyAdapter)`；适配器存类、route 时实例化（一请求一实例） |
| `EngineRegistry` | `app/core/registry.py` | 引擎实例（category + name） | `EngineRegistry.register("doc", "my-engine", engine)`；`init_engines()` 启动注册 |
| `ProductRegistry` | `app/generation/product_registry.py` | 产物类型 ProductDef + 依赖拓扑 | 加一个 `ProductDef`，分发逻辑/前端 UI 零改动 |
| `ToolRegistry` | `app/agent/tool_registry.py` | Agent 工具 ToolDef | 注册后自动被发现调用（5 工具：search_docs/lookup_doc/list_docs/search_web/fetch_url） |
| `SlotRegistry` | `frontend/src/lib/slotRegistry.ts` | 面板 SlotDef（left/center/right） | 注册即出现在布局，App.tsx 零改动 |
| `TabRegistry` | `frontend/src/lib/tabRegistry.ts` | 预览 tab TabDef + ContentSource | 注册后 PreviewArea/复制/导出三处自动生效 |

> **关键契约**（详见 [architecture.md](architecture.md)）：
> - **PipelineAdapter ABC**（`core/protocols.py`）：`accept(input)` 纯输入判断 + `adapt(input) → AdapterOutput`。失败必须 `raise FatalError`，禁止返回 `engine="none"`。
> - **TranscriptResult**（`engines/transcript.py`）：**ASR 专用**契约 `(language, duration, segments, full_text)`。Doc 引擎**没有**统一数据类——各引擎直接产出 Markdown 字符串 + 媒体清单。

---

## 4. 数据流横切（关键链路走查）

### 4.1 一次导入（URL 为例）

```
POST /api/ingest
→ url_security.validate_url()（SSRF 14 段黑名单 + DNS 二次校验）
→ AdapterRegistry.route() → 首个 accept 命中的适配器实例
→ adapter.adapt() → RawTranscript（存 store，execution guard 本地信号量）
→ SSE /ingest/stream 推送阶段进度
→ 内容去重：SHA256 contentHash，Store 级拒绝写入
```

### 4.2 一次生成（双产物并行）

```
POST /generate/{id}
→ resolve_model()（唯一模型解析入口）→ scene_models.generate
→ PromptBuilder.build()：source_type + mode → prompt 函数（XML 分节）
→ generate_both()：asyncio.gather(polished, notes) ← ⚠️ 解包必须同序
→ 存储：{doc_dir}/{mode}/v{N}.md（版本归档：生成追加新版本、编辑就地覆盖当前版）
→ 返回前 rewrite_asset_urls 改写图片为绝对 URL
```

### 4.3 一次对话（Agent 工具循环）

```
POST /chat/stream
→ resolve_context_window() → budget = 窗口×0.7
→ PromptLoader.build(doc_context)：base.md + rules.md + 当前文档
→ AgentRunner.stream_run() → SSE 事件：
   meta → agent_status → tool_start → tool_result(含 refs) → thinking → text → done
→ 历史按 token 反向裁剪（core/chat_context.py）；超阈值 LLM 压缩（agent/compress.py）
→ 持久化 conversations/（会话 + 消息）
```

### 4.4 取消机制（6 端点全覆盖）

每个长时端点：`cancel_event` + `disconnect_watcher(request, cancel_event)`（`core/disconnect.py`）。
- SSE 端点：逐 chunk 检查取消信号 → 停 token 消耗
- 本地同步引擎：`asyncio.to_thread` 线程池 + 引擎内部逐段检查 `cancel_event.is_set()`
- 前端：`createAbort(taskKey)` 注册 AbortController ↔ `cancelTask(docId)` 必须同 key

---

## 5. How-to（8 个常见任务）

### 5.1 新增一个内容源适配器（如 RSS）

1. `app/adapters/rss_adapter.py`：实现 `PipelineAdapter`，`accept()` 判输入类型，`adapt()` 产 `AdapterOutput`
2. `app/adapters/__init__.py`：`AdapterRegistry.register(RssAdapter)`——**注册顺序决定路由优先级**（Video→Document→Image→Webpage→Text）
3. 前端自动发现：`GET /api/capabilities` 返回元数据，GenPanel 类型门控无需硬编码
4. ⚠️ Webpage 类 URL 抓取**必须过 `url_security.validate_url()`**（曾漏过导致 SSRF 漏洞）
5. 测试：`backend/tests/test_adapters.py`

### 5.2 新增一个引擎（如新的本地 PDF 解析器）

1. 实现 `Engine` ABC（`core/protocols.py`），支持 `media_dir` 双模式（写文件 `![alt](img_0001.png)` / 空则内嵌 base64）
2. `app/engines/` 注册：`EngineRegistry.register("doc", "my-engine", engine)`，并在 `config.py` builtin_models 加 desc
3. 耗时本地引擎必须 `asyncio.to_thread` + 内部检查 `cancel_event`（否则卡死事件循环，`_watch_disconnect` 失效）
4. 若依赖重 / AGPL / 跨平台难维护 → **做成插件**（见 [plugins.md](plugins.md)），不要硬塞核心

### 5.3 新增产物类型（如「时间线」）

1. `product_registry.py`：加 `ProductDef`（id/label/依赖拓扑）
2. `generation/prompt.py` 或 `prompt_builder.py`：加 prompt 函数
3. 前端：`tabRegistry.ts` 注册新 TabDef（contentSource 若需要新枚举，`contentSources.ts` 加 getter）
4. `export-pipeline.md` §16 的操作指南：确认导出链路

### 5.4 新增一个 API 端点（含 SSE）

```python
# 普通端点
@router.post("/my-endpoint")
async def my_endpoint(store=Depends(get_store), config=Depends(get_config)):
    ...

# SSE + 可取消端点（复用标准模式）
@router.get("/my-endpoint/stream")
async def my_stream(request: Request):
    cancel_event = asyncio.Event()
    async with disconnect_watcher(request, cancel_event):
        async def gen():
            yield sse_event("text", {"text": "..."})
        return StreamingResponse(gen(), media_type="text/event-stream")
```

⚠️ 前端必须走 `apiUrl()` / `apiFetch()`（裸 `fetch('/api/...')` 在 Tauri 下失败）。

### 5.5 新增 store slice（前端状态）

1. 新建 `store/mySlice.ts`，**action 零跨域引用**
2. `store/index.ts` 组合层挂载
3. 持久化：手写 localStorage `mbw_*` key（注意 16 个已占用 key 的命名规范）
4. 对话相关状态**不要**进主 store——放 `chatStore.ts`（流式高频更新隔离）

### 5.6 新增预览 tab

1. `tabRegistry.ts`：`TabRegistry.register(TabDef)`，指定 `contentSource/htmlMode/exportSuffix/exportLabel`
2. `contentSources.ts`：`CONTENT_GETTERS` 加 getter（Record 类型强制）
3. PreviewArea 渲染：按 tab key 分派组件
4. ⚠️ `App 内置欢迎文档`（PreviewArea 空态）能力变更时同步更新

### 5.7 新增对话工具

1. `app/agent/tools.py`：写 ToolDef（name/description/execute）
2. `tool_registry.py` 注册
3. `routers/chat.py` 或 `agent.py`：加入工具列表
4. 工具结果走 SSE：`tool_start`（display 安全文案）+ `tool_result`（summary + refs，**完整结果不暴露前端**）

### 5.8 开发一个插件

读 [plugins.md](plugins.md) + `backend/plugins/examples/` 三个模板（pymupdf4llm 进程外 / tesseract 本地 OCR / trusted 进程内）。要点：
- 进程外：`command` 指向子目录 venv，stdin JSON 协议，stdout JSON 结果
- 依赖重/协议问题选进程外；轻量选 `trusted=True`；都没有 → 拒绝加载
- `~/.limo/plugins/` 扫描，重启生效，zip 安装有 zip-slip 防护

---

## 6. 打包与发布（本章最详细）

> 详细步骤见 [BUILD.md](BUILD.md)。本章是**完整命令 + 陷阱排查 + 新增依赖 checklist**。

### 6.1 两条链路

```
开发:  Vite dev :5173 ──┬── API 代理 → http://127.0.0.1:7413/api
                        └── Tauri dev 加载 devUrl（需先手动起后端）

生产:  LiMo.exe (Rust) ──spawn──▶ LiMoBackend.exe (PyInstaller, uvicorn :7413)
            │                              │
            ▼                              ▼
       WebView2 加载                 FastAPI 后端
       frontend/dist/ (静态)          backend/
```

Tauri v2 `externalBin` 自动加平台后缀 → PyInstaller 产物 exe 需重命名匹配。

### 6.2 产物位置与体积（0.4.13 实测，2026-10-05 文档多标签页 + 正文字号缩放后）

| 产物 | 路径 | 大小 |
|------|------|------|
| NSIS 安装包 | `frontend/src-tauri/target/release/bundle/nsis/LiMo_0.4.13_x64-setup.exe` | ~148MB |
| MSI 安装包 | `frontend/src-tauri/target/release/bundle/msi/LiMo_0.4.13_x64_en-US.msi` | ~195MB |

> 体积优化（2026-08-14）：docling/pytesseract 依赖残留清理 + ffmpeg 换精简版后，安装包从 ~265M/~366M 降到 ~148M/~195M。详见 [CHANGELOG.md](CHANGELOG.md)（2026-08-14 打包体积优化条目）。

### 6.3 打包步骤（顺序铁律）

```bash
# ⚠️ 全部在项目根目录执行（spec 的 pathex 依赖 CWD）
# ⚠️ ① 必须用 venv python -m 方式：uv run（根目录无 pyproject）/ pyinstaller.exe trampoline 都会 canonicalize 失败

# ① 清理 + PyInstaller（--noconfirm 会重建整个 bin/LiMoBackend/，删掉手动复制的东西）
rm -rf frontend/src-tauri/bin/LiMoBackend
backend/.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean backend/LiMoBackend.spec --distpath frontend/src-tauri/bin

# ② 重命名 exe + 复制 ffmpeg（必须在 ① 之后重新做！）
#    ffmpeg 精简版分发源在 ~/.limo/ffmpeg-portable/（gyan.dev essentials 8.1.1，194M）
cd frontend/src-tauri/bin/LiMoBackend
cp LiMoBackend.exe LiMoBackend-x86_64-pc-windows-msvc.exe
mkdir -p ffmpeg
cp ~/.limo/ffmpeg-portable/ffmpeg.exe ffmpeg/
cp ~/.limo/ffmpeg-portable/ffprobe.exe ffmpeg/
cd ../../../..

# ③ 前端构建 + Tauri 打包
cd frontend
npx tauri build
```

**⚠️ 顺序铁律**：步骤 ① 的 `--noconfirm` 每次重建 `bin/LiMoBackend/`，会**删除**你复制好的带后缀 exe 和 `ffmpeg/`。所以「重命名 + 复制 ffmpeg」**必须在 PyInstaller 之后、Tauri 之前**执行——新增/更新依赖重跑打包时必须完整走 ①→②→③。

### 6.4 打包前版本同步

```bash
# 版本号 7 处同步（VERSION 单一源）——禁止手动改任何版本文件
python scripts/sync-version.py --check          # CI 一致性检查
python scripts/sync-version.py patch            # 0.4.0 → 0.4.1（Bug 修复）
python scripts/sync-version.py minor            # 0.4.0 → 0.5.0（新功能）
python scripts/sync-version.py 0.5.0            # 手动指定
python scripts/sync-version.py minor --tag      # 升级 + 打 git tag v0.5.0
```

### 6.5 产物验证清单（打包后必查）

1. **后端能启动**：直接运行 `./frontend/src-tauri/bin/LiMoBackend/LiMoBackend.exe`，看到 `Uvicorn running on http://127.0.0.1:7413` 即成功
2. **exe 文件后缀正确**：`bin/LiMoBackend/` 下有 `LiMoBackend-x86_64-pc-windows-msvc.exe`（Tauri 找这个名字）
3. **ffmpeg 存在**：`bin/LiMoBackend/ffmpeg/ffmpeg.exe` + `ffprobe.exe`
4. **健康检查**：装好的 exe 启动后 `curl http://127.0.0.1:7413/health` 返回 200 + 版本号
5. **核心链路冒烟**：导入 → 生成 → 导出（HTTP 均通）
6. **无黑框**：PyInstaller `console=False`
7. **KaTeX 字体完整**：`backend/static/katex/fonts/` 60 个字体（导出的 HTML 公式不碎）

### 6.6 构建陷阱排查对照表

| 症状 | 根因 | 修复 |
|------|------|------|
| Tauri 构建 os error 5（权限） | `bin/searxng/.git` 被 PyInstaller 收集 | ⚠️ **仅在手动部署 searxng 时可能遇到**——2026-08-26 起 SearXNG 不随包（tauri resources 未配置），常规打包不再触发；如需手动部署，打包前清理 `bin/searxng/.git` |
| 后端启动 `ImportError: DLL load failed` | cryptography 的 `libcrypto-3-x64.dll`（~5MB）覆盖 Python 原生 DLL（~8MB），`_ssl.pyd` 加载失败 | spec 排除 cryptography DLL + 显式加 `sys.base_prefix/DLLs/` 原生版本 |
| HTTPS 请求全部失败 | SSL 证书路径丢失 | spec 含 certifi；启动时设 `SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` |
| 启动报 `ModuleNotFoundError: app.xxx.yyy` | 函数内 `from app.xxx import yyy` 不被 PyInstaller 静态分析捕获 | 加到 spec `hiddenimports`（见 [BUILD.md](BUILD.md) 已固化清单） |
| 导出的 HTML 公式碎掉 | KaTeX 字体未完整打包 | 从 `frontend/node_modules/katex/dist/fonts/` 复制 60 字体到 `backend/static/katex/fonts/` |
| Tauri 找不到 exe | `externalBin` 自动加平台后缀，PyInstaller 产物没重命名 | 复制为 `LiMoBackend-x86_64-pc-windows-msvc.exe` |
| 视频/转写找不到 ffmpeg | ffmpeg 未进包 | PyInstaller 后复制 `ffmpeg.exe`+`ffprobe.exe` 到 `bin/LiMoBackend/ffmpeg/`（Rust `enhance_path()` 启动时追加 PATH） |
| 弹出 CMD 黑框 | subprocess 缺 `CREATE_NO_WINDOW` | 所有 Windows subprocess 调用加 `creationflags=subprocess.CREATE_NO_WINDOW`（5 文件已覆盖，新增时必带） |
| 改完依赖后 exe 还是旧的 | `--noconfirm` 重建目录，忘了重做 ② | 完整走 ①→②→③ |

### 6.7 新增 Python 依赖 checklist（8 步）

```bash
# 1. 安装（项目统一用 uv）
cd backend && uv add <package>

# 2. 扫描函数内 import，和 spec hiddenimports 比对
rg "^\s+from app\.\S+ import" --glob '*.py' app/

# 3. 同步 spec：hiddenimports + collect_all（如需打包数据文件）
# 4. 重跑 PyInstaller（根目录，--noconfirm）
# 5. 重做 ②（重命名 exe + 复制 ffmpeg）⚠️ 顺序铁律
# 6. 直接运行 LiMoBackend.exe 验证能启动 + health 200
# 7. 若依赖带重 DLL（如 cryptography）：检查 OpenSSL DLL 冲突（见 6.6）
# 8. npx tauri build 重新打包
```

### 6.8 发布门禁（**打包 ≠ 发布**）

只有用户主动提出「上传发行版/发布」才执行上传。发布前展示门禁清单，用户确认后才执行：

| # | 门禁 | 检查 |
|---|------|------|
| 1 | 版本一致 | `python scripts/sync-version.py --check` + 产物文件名匹配 VERSION |
| 2 | 验证全绿 | 后端测试 + 前端测试 + exe health 200 + 核心链路冒烟 |
| 3 | 文档同步 | CHANGELOG 有对应版本记录；STATUS 版本号已同步 |
| 4 | Tag + Notes + 校验 | `git tag v{VERSION}` → `gh release create`（notes 从 CHANGELOG）→ 附安装包 SHA256 |

---

## 7. 测试门禁

```bash
bash scripts/check-all            # 全部六项（约 60s）—— 提交前跑这个
bash scripts/check-all frontend   # 只改前端时
bash scripts/check-all backend    # 只改后端时
bash scripts/check-all cli        # 只改 limo_cli / QueryService 时
bash scripts/check-all docs       # 只改文档时（「第 N 节」交叉引用，约 0.2s）
bash scripts/check-all quick      # 约 5s，仅 tsc + lint
```

⚠️ **分层的轴是「域」不是「快慢」**：每一层都是该域的**完整验证**。`quick` 是唯一
例外——它只看得到类型 / lint 能看见的东西。2026-10-06 那轮 25 项审计发现里**没有
一项**是它能抓到的（全是静默失效、同源漂移、死代码，必须跑起来才现形），故别拿
它的绿当「没问题」。

⚠️ 这个脚本**不重新实现任何检查**：`cli` 那层就是 `bash scripts/check-cli`。编排
脚本复刻被编排的脚本，是「同源漂移」最经典的入口（模式 #54）。

### 7.1 后端

测试文件都在 `backend/tests/`。两种跑法**都行**——每个文件带 `if __name__ == "__main__"`
（可直接跑），同时顶层 `test_*` 函数也是合法的 pytest 用例（实测 `pytest tests/` 能
全部收集到）。**优先用 pytest**：单进程、有汇总、失败详情完整。

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q     # 全量
./.venv/Scripts/python.exe -m pytest tests/ -q -k search         # 按名字筛

# 常跑的几个
python tests/test_prompt.py          # Prompt 构建验证
python tests/test_store.py           # Store CRUD + 并发安全
python tests/test_e2e_smoke.py       # 端到端冒烟（需后端已在跑）
python tests/test_query_contract.py  # HTTP/CLI 契约 == QueryService（含对照实验）
python tests/test_closure_shadowing.py  # AST 全库扫描（#26/#43 那类作用域错）
```

### 7.2 前端

测试文件都在 `frontend/src/test/`。

```bash
cd frontend
npm test        # vitest（<15s；覆盖 store/theme/api/tabRegistry/chatStore/文档库/文档标签页… 等域）
npx tsc -b      # 类型检查（⚠️ 必须 -b：tsconfig.json 是 solution file，`--noEmit` 什么都不检查）
npm run lint    # oxlint
npm run build   # 类型检查 + 生产构建（发布前才需要，日常用 check-all frontend 即可）
```

⚠️ **此处刻意不写死测试数量**：写死的计数必然过期（本节此前写着 384/29，而实际已是
467/39），而**写错的计数比不写更糟**——它给出确定感，让下一位读者不再核对（模式 #54）。
当前数量跑 `bash scripts/check-all` 即可看到。

### 7.3 CLI 契约门禁（16 项）

```bash
bash scripts/check-cli          # 自启独立端口 7419 的后端，跑完杀掉（不打扰 7413）
bash scripts/check-cli 9        # 只跑第 9 项（调试用）
```

⚠️ **不是 pytest、也不是「跑一遍看看」**：它是 `limo` 对外承诺的行为契约 ——
退出码 / `--json` 纯净性 / 分页与截断的形状 / **负向对照**（后端不可达 4、坏 ID 5、
不存在 3、用法错 2）。改 `limo_cli/` 或 `/api/cli/*` 后必跑。

### 7.4 改动后自测原则

- **提交前** → `bash scripts/check-all`（约 60s，六项全跑）
- 改后端路由/引擎/适配器 → `bash scripts/check-all backend` + 手动 curl 端点
- 改前端状态 → `bash scripts/check-all frontend` + 手动跑链路
- 改 `limo_cli/` 或 `app/routers/cli.py` 或 `services/query.py` → `bash scripts/check-all cli`
- 改 `docs/` 或任何带编号章节的 md（尤其**重排过章节结构**）→ `bash scripts/check-all docs`：正文里的「第 N 节」是**手写**的，重排后不会自己跟着改（模式 #68）
- 删代码（死代码清理）→ 必须跑 tsc（`check-all frontend` 含）：漏引用只有它能抓
- 提交前 `grep` 确认零硬编码色值泄露（主题铁律）

⚠️ **跑绿只说明「有测试覆盖的部分」没问题**。前端视觉改动（主题色、引擎标签）绿了也
只说明 token 用对了，说明不了看起来对不对 —— 那类改动仍需人工看一眼。别拿这里的绿
当作「验收通过」。

---

## 8. 调试速查

### 8.1 常用诊断

| 问题 | 命令/方法 |
|------|----------|
| 后端日志 | `backend/limo_backend.log`（生产）/ 前端 LogPanel（`GET /api/logs` 轮询最近 1000 条） |
| Tauri 启动日志 | `limo_startup.log` + `limo_backend_stderr.log`（安装目录） |
| 后端没起来 | `netstat -ano | grep 7413`；`restart-backend.sh` 杀端口重启 |
| 端口占用 | 幽灵 socket：温和 taskkill 失败，手动任务管理器结束 |
| 前端 401/跨域 | 检查后端 Origin 白名单（CORS）：生产必须是 `http://tauri.localhost` |
| API 404 | 前端禁止 `/api/api/`——`apiUrl()` 已拼接 BASE，传路径别带 `/api` |
| 导出内容不对 | 看 `export-pipeline.md` §13 速查表（预览 vs 导出差异） |
| 请求日志 | middleware 计时（status/duration_ms），SSE 长连接不判慢 |

### 8.2 模型/LLM 问题

| 症状 | 排查 |
|------|------|
| 生成无输出/超时 | `reasoning_effort` 高 → 思考静默 90s+（chunk 超时 150s 容忍）；polished 传 `"low"` |
| 提示词没生效 | 查 `prompts-inventory.md` prompt 位置（XML 分节标签） |
| 模型能力不对 | `resolve_model()` 是唯一入口；多模态由 `_PROVIDER_IMAGE_RULES` 自动检测 |
| 对话上下文 | `context_window` 四层解析（用户 > 预设 > 默认表 > 32k），预算 = ×0.7 |

### 8.3 常见概念混淆

- `ChatMsg`（chatStore，对话消息）**≠** `ChatMessage`（主 store types.ts 遗留类型）
- `apiUrl()`（/api 下 fetch）**≠** `appUrl()`（/api 和 /static 图片资源绝对 URL）
- `AdapterRegistry` 存**类**（route 时实例化）**≠** `EngineRegistry` 存**实例**
- `disconnect_watcher`（`core/disconnect.py`）**替代**旧的 `_watch_disconnect` 闭包（6 端点统一）
- 引擎绑定只取第一个 + 失败报错；「自动降级链」已移除（2026-08-03）

---

## 9. 上手练习（按顺序做，逐步深入）

1. **读配置**：打开 `config.yaml`（`~/.limo/`），对照 CLAUDE.md 理解每个字段
2. **改一个样式**：换一个主题 accent，看 CSS 变量链（`--tk-*` → `--md-*`）
3. **加一个格式选项**：`get_xxx_format()` → AVAILABLE_OPTIONS → 前端 GenPanel
4. **加一个对话工具**：按 5.7 步骤，注册一个 `today` 类工具
5. **加一个预览 tab**：按 5.6 步骤，tabRegistry + contentSources
6. **加一个 slot 面板**：slotRegistry 注册 SlotDef，观察布局自动变化
7. **做一次完整打包**：按第 6 章走 ①→②→③，验证产物
8. **写一个插件**：仿 `examples/tesseract_plugin.py`，做本地 OCR 插件

每完成一步，跑对应测试 + `npm run build` 保证不破坏现有功能。

---

## 附：新手最容易踩的坑（top 10）

1. 函数内 `from app.xxx import yyy` 漏进 spec hiddenimports → 打包后 `ModuleNotFoundError`
2. `async def` 改了忘加 `await` → Python 不报错，静默返回 coroutine
3. 本地引擎不用 `asyncio.to_thread` → 卡死事件循环，取消失效
4. 新端点不用 `disconnect_watcher` → 客户端断开后资源泄漏
5. 图片组件用裸 URL 不用 `appUrl()` → 打包端裂图
6. 引擎/适配器改动后不重启（`--reload` 不跑 startup）→ 注册不生效
7. 前端 fetch 裸 `'/api/...'` 不走 `apiUrl()` → Tauri 下失败
8. 空字符串 key 覆盖真实 key（API Key 保存要 `(val || undefined) ?? current`）
9. 新 markdown 背景样式硬编码 hex → 破坏主题系统（必须 `--tk-*`）
10. `--noconfirm` 后忘了重做「重命名 exe + ffmpeg」→ 打的包是旧的
