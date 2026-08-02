# 修改记录

> 记录每次重要修改的**做了什么**、**为什么**、**影响范围**。
> 不只是"修了 bug"，更是"当时为什么这样设计，后来为什么改了"。
> 按时间倒序，最近的在最上面。

---

## 2026-08-03 — 生成参考文件（Reference Text）：参考资料注入笔记生成

### 功能

生成笔记时注入参考资料（如视频评论区观点、用户提供的审阅材料）。**官方不内置任何抓取能力**（评论区爬取有法律风险），只做中性能力：接收参考文本 → 注入生成 prompt。依据 [pi-docs/reference-text-建议.md](../../pi-docs/reference-text-建议.md)。

### 设计：暂存 id（评审 P0）

流式 generate 是 **GET**（query string），参考文件 ≤200KB 直接 JSON 进 URL 会数百 KB（代理默认 4-8KB 直接 414）。改为**暂存**：

- `POST /api/references`（`routers/references.py` + `core/reference_store.py`）→ 内存 dict + **TTL 20 分钟**惰性清理 → 返回短 id（`secrets.token_hex(8)`）
- 生成时传 `reference_ids`（流式 query 逗号分隔 / 非流式 body 数组），后端按 id 取内容注入
- 进程内存 → 重启自动清空 = **仅会话内不持久化** ✓

### prompt 注入（用户决策：仅 notes）

- 新增 `<reference_texts>` section（置于 `<user_request>` 之前，空则 compose 跳过），文本带「来源 + 仅供背景参考，与原文冲突时以原文/模型判断为准」
- **仅 `build_notes_prompt` 注入**；polished（忠实整理）与 Excel 摘要**不注入**（用户决策）
- 两条独立路径都打点：非流式（GenerateRequest → Generator → PromptBuilder）与流式（generate_stream 直接调 build_notes_prompt）

### 前端

- 生成面板「参考文件」区块（用户备注上方）：txt/md 选择器（复用 ImportSection 虚线框样式）、≤200KB 校验、已选展示 + 移除、备注 placeholder 引导
- **意图缺失确认框**：选文件未填备注 → window.confirm 两路径（确认降级背景 / 取消展开备注）
- `api/references.ts` + `BaseGenOptions.reference_ids`；参考文件 state 仅会话内（不写 localStorage）

### 涉及

新增 `core/reference_store.py` / `routers/references.py` / `frontend/src/api/references.ts`；修改 generate.py / generator.py / prompt_builder.py / prompt.py / main.py / api/generate.ts / GenPanel.tsx / GenerateOptions.tsx / tests/test_prompt.py

### 范围外（文档 4 确认）

chat 链路参考 · JSON 格式 · 评论爬取（法律风险）· process/fact_check/export · polished/Excel 注入

### 验证

- 后端 6 测试全绿（test_prompt 新增 reference 注入断言：notes 含 section / polished 无 / 空跳过 / 多段分隔）
- references 冒烟：暂存 id / 存取 / 无效 id 跳过 / TTL 惰性清理
- 前端 82 测试全绿 + `tsc -b` 类型检查通过

---

## 2026-08-03 — 发布 pymupdf4llm 三方插件仓库（v1.0.0）

首个公开插件仓库 [lazybone404/limo-pymupdf4llm-plugin](https://github.com/lazybone404/limo-pymupdf4llm-plugin)（Public，MIT 插件代码 + AGPL 底层依赖声明，README/Release v1.0.0）。plugins.md 新增「已知三方插件」小节——明确标注发布身份为**三方（非官方维护）**，遵循口子原则：主程序只维护契约 + 加载 + 文档，插件生态由三方发布。

---

## 2026-08-03 — 笔记生成超时各 +30s（chunk 间隔 120→150 / 总超时 300→330）

用户反馈笔记生成挂起时间需延长。三处同步 +30s：

- 流式生成 **chunk 间隔超时** 120s → 150s（双产物 + 单产物两路径；两次输出之间更长容忍，deepseek 长思考静默 90s+ 不误判）
- 流式生成 **总超时** 300s → 330s（长文档 + 多次续写的整体上限）
- LLM 客户端（OpenAI SDK）timeout 300 → 330（对齐总超时，避免 HTTP 层先掐断）

错误消息与 CLAUDE.md 描述同步更新。

---

## 2026-08-03 — 参考文件意图缺失提醒防重复：会话内只弹一次

「选文件未填备注」的确认框此前每次生成都弹（每次都点确认的用户被反复打扰，违背文档 2.3「不烦人」）。修复：GenPanel 模块级标记 `refNoNotesPrompted`，**首次**弹（无论确认/取消都先置位），本次会话内不再重复；刷新页面清空（与 refFile 仅会话内不持久化一致）。

---

## 2026-08-03 — pymupdf4llm 插件内嵌图片统一 PNG

参考实现 `examples/pymupdf4llm` 去掉 `image_format` 配置项（原可选 png/jpeg），内嵌图片统一 PNG（嵌入式最常见格式）。`engine.py` 固定 `image_format="png"`，与核心引擎 pypdfium2 / 用户侧 heavy 版一致，确保 `_persist_media_images` 的 glob 覆盖无缺口。

---

## 2026-08-03 — 插件管理反馈修复：开关即时翻转 + 待重启徽章 + 重启后端按钮

### 问题

设置页插件**启用/禁用开关点击后完全无变化**，用户不知道是否已生效。

### 根因

`enable/disable` 路由只写 config（`plugin_states`）到磁盘，**未同步 `PLUGIN_REGISTRY` 内存态**——前端点完开关重拉列表，`enabled` 仍是启动时固化的旧值 → 开关视觉纹丝不动。

### 修复

- **后端**：新增 `set_plugin_enabled()` 同步内存态（enable/disable 路由调用），开关即时翻转
- **后端**：`PLUGIN_REGISTRY` 加 `loaded` 字段（运行时是否已注册进 `EngineRegistry`，仅启动时决定）；`enumerate_plugins()` 返回 `pendingRestart`（`enabled != loaded`）
- **前端**：卡片显示「待重启」徽章（`重启后生效` / `重启后停用`）——明确告知"期望状态已改，引擎在下次启动时注册/注销"
- **前端**：设置页加「重启后端」按钮——Tauri 生产调用 Rust `restart_backend_sidecar` + 轮询 `/health` 就绪后重拉插件列表；浏览器 dev 模式提示手动重启

### 决策：不做热加载

用户确认**不做热加载**，保留「重启后生效」干净语义。理由：

1. 进程外插件每次调用都是 spawn 独立子进程，没有"热/冷"之分，热加载只省一个重启
2. 禁用正在被使用的引擎 → 新请求绑定解析立即失败（隐性），违背「失败显式化」
3. 热加载 = config / PLUGIN_REGISTRY / EngineRegistry 三方运行时同步 + 竞态，复杂度与收益不成正比
4. 真实痛点是反馈缺失（开关无变化），已由本次三项增强解决

### 影响范围

`plugin_loader.py` / `routers/plugins.py` / `frontend/src/api/plugins.ts` / `frontend/src/components/settings/PluginSettings.tsx`

### 验证

- 后端 6 个测试全绿；`set_plugin_enabled` + `enumerate` 联动单测通过（enable→True / disable→False / pendingRestart 四场景）
- 前端 82 测试全绿 + `tsc -b` 类型检查通过

---

## 2026-08-03 — 设计哲学沉淀：口子原则 + 失败显式化

确立两条设计哲学（写入 [CLAUDE.md](../CLAUDE.md) 与 [architecture.md](../docs/architecture.md)）：

- **口子原则（Keep core lean）**：核心只保留轻量、稳定、可控的能力；重依赖/协议/难维护/法律风险的能力走插件口子（`~/.limo/plugins/`）。判断「内置 vs 插件」四标准：依赖重 · 协议问题 · 跨平台难维护 · 法律风险。已落地：whisper / Tesseract / docling 移出核心，pymupdf4llm 进程外插件
- **失败显式化（No silent degradation）**：核心内在语义干净——引擎绑定只取第一个失败直接报错（无降级链，2026-08-03 彻底移除）；扫描件 OCR 失败占位带原因

---

## 2026-08-03 — 彻底移除引擎降级链

### 做了什么

用户要求**引擎不应有降级链，彻底移除**；大模型降级链 + 用户可配置降级链留后面再做。

- **Engine 协议层删 fallback**（protocols.py）：删 `fallback` 字段 + `run()` 失败时委托 `fallback.run()` 的分支——`run()` 直接 `execute()`，失败抛出
- **init_engines 删 fallback 赋值**（dashscope-asr / bcut 的 `fallback = None`）
- **扫描件 OCR 移除多引擎降级链**（document.py）：`_resolve_ocr_engine_chain` → `_resolve_ocr_engine()` 返回单个（绑定第一个可用）；`_ocr_pdf_with_images` 逐页不再尝试多个引擎，只用第一个，失败 → **占位带原因** `_(OCR 失败: <原因>)_`（不再「所有 OCR 引擎均失败」）
- **Doc 主循环简化**：`for`+`break` → 取 `engine_chain[0]` 直接执行；删不可达 `if not raw_text` 分支；保留链空处理（扩展名兜底 → 占位 AdapterOutput）
- test_engines.py 删 fallback 检查，降级链测试改为「引擎失败直接报错测试」；registry.py 注释同步

### 行为变更

- 引擎失败不再自动切换其他引擎（绑定只取第一个是既有语义，本次彻底移除协议层 fallback 兜底）
- 扫描件 OCR 无可用引擎 / 无 API Key → 占位文本**带失败原因**（利于排查），不再尝试下一个引擎

### 保留（非引擎降级链，不动）

- 扩展名兜底（未绑定时的默认引擎）、绑定修复回退（config 迁移）
- process.py LLM→OCR 回退、agent 搜索降级（Tavily→SearXNG→DDG）、webpage 提取回退——均属大模型降级链 / 非引擎，留后置

### 验证

- 后端 7 测试全绿（test_engines 无 fallback 报错）
- 冒烟：capabilities 正常（doc: pypdfium2 等、ocr: dashscope 系列）+ 导入 PDF 走 pypdfium2 成功
- 扫描件 OCR mock 验证：单引擎失败 → 占位带原因

---

## 2026-08-03 — 移除 DoclingEngine + 体积二次修剪

### 做了什么

**移除 DoclingEngine**（打包版 docling 依赖 torch ~2GB 无法内置，实际一直不可用）：
- `docling.py` 删 DoclingEngine，保留 PyPdfium2Engine（独立于 docling 包，默认 PDF 引擎不受影响）
- `init_engines` 删注册 + `_migrate_remove_docling` 迁移（清 builtin_models / engine_models / 指向 docling 的绑定）
- config / capabilities / document 兜底引擎 docling → pypdfium2
- spec 移除 docling/docling_parse/rapidocr/huggingface_hub 收集；onnxruntime 保留（magika 文件类型检测需要）
- `.venv` 卸载 docling 依赖链（torch/transformers/scipy/pandas/sympy/rapidocr/cv2/av/ctranslate2 等 ~1.3G → 398M）

### 行为变更

- **docling 引擎不再提供**（无论打包版还是开发版）。PDF 布局解析回归 pypdfium2（文字+表格+嵌入原图，毫秒级）；需要 docling 式布局/矢量图解析的用户可装三方插件（参考 pymupdf4llm 模板）

### 打包体积累计（本次 + 上次 tesseract 修剪）

| 指标 | 原始 | 现在 | 节省 |
|------|------|------|------|
| PyInstaller `_internal` | 955M | 222M | **733M（77%）** |
| 后端 exe | 67M | 27.6M | 39M |
| `.venv` 依赖 | ~1.3G | 398M | ~900M |
| NSIS 安装包 | 345M | 待 tauri build 确认 | — |

### 影响范围

- 代码：`docling.py` / `engines/__init__.py` / `config.py` / `capabilities.py` / `document.py` / `LiMoBackend.spec`
- 文档：architecture / STATUS（引擎计数 10→9）/ CLAUDE.md / plugins.md
- 验证：后端 7 测试全绿 + 打包产物冒烟（导入 PDF 走 pypdfium2 正常）+ 关键包保留（numpy/onnxruntime/magika）

---

## 2026-08-02 — 移除内置 Tesseract + 打包体积修剪

### 做了什么

**移除内置 Tesseract OCR**（体积修剪 + 插件化方向，与 faster-whisper 同模式）：
- 删除 `TesseractEngine`（ocr.py）、init_engines 注册块、config/capabilities 的 tesseract 条目、spec 的 pytesseract（datas/hiddenimports/collect_all）
- 删除 `backend/bin/tesseract/`（280M，含 ~60M 训练工具 + 57M tessdata + DLL）+ setup.exe + zip
- 新增 `_migrate_remove_tesseract` 启动迁移（清理 engine_models 的 tesseract 条目 + engine_bindings.ocr 悬空 id）
- **体积节省**：tesseract 整链 ~378M（bin 280M + PyInstaller 顶层重复 libtesseract-5.dll 97M + pytesseract 1M）+ spec excludes `av`/`faker` ~12M（docling 运行时主链路不加载，静态收集冗余）

### 行为变更

- **本地无 key OCR 从「可用」变「占位」**：无 dashscope API Key 时扫描件 OCR 逐页输出「原图 + `_(所有 OCR 引擎均失败)_`」占位文本（之前 Tesseract 是本地兜底）。需本地 OCR 请装三方插件参考模板 [examples/tesseract_plugin.py](../backend/plugins/examples/tesseract_plugin.py)（含 tesseract 便携版自备说明 + chi_sim 排查）
- 前端引擎下拉自动不再显示 Tesseract（全动态渲染，零改动）

### 影响范围

- 代码：`ocr.py` / `engines/__init__.py` / `config.py` / `capabilities.py` / `LiMoBackend.spec`
- 模板：`examples/tesseract_plugin.py` + `tesseract/{engine.py, README.md, requirements.txt}`
- 文档：`plugins.md` / `CLAUDE.md` / `STATUS.md`（引擎计数 11→10）
- 验证：后端测试全绿 + capabilities 冒烟 + 前端 82 tests + 重新打包体积对比

---

## 2026-08-02 — 插件系统完整正式化（EnginePlugin v1 · pymupdf4llm 首例）

### 做了什么

按 `pi-docs/12-插件系统设计建议` + 用户定稿 v2（删除协议维度）完整正式化插件系统：

**核心 bug 修复（驱动本计划的硬伤）**：
- `_PluginEngineAdapter.execute()` 原返回纯 `str`，消费方（document.py `.text` / video.py `.segments` / image.py `.text`）访问结构化字段**必崩 AttributeError** → pymupdf4llm 插件一直不可用
- 修复：`_dispatch_result(category, payload, name)` 按 category 分派返回 `DocResult`/`OCRResult`/`TranscriptResult`，消费方零改动

**EnginePlugin v1 契约**（`plugin_loader.py`）：
- 默认进程外 + trusted 例外门控：`command` 有值→external；`trusted=true`→inproc（全类别统一，引擎不特殊）；无 command 且未 trusted→拒绝加载
- **trusted 为纯技术声明**（可进程内 import，崩溃/依赖风险自担），不涉及任何协议判断；主程序不判断/不警告/不声明插件协议，license 仅前端展示
- 元数据字段位：`display_name`/`user_desc`/`config_schema`/`namespace`/`permissions`/`api_version`(PLUGIN_API_VERSION)

**进程外契约 JSON 化**：
- stdin JSON：`{file, cancel_file(绝对路径), config(插件自身配置), ...}`；argv 保留 `--input`（v0 兼容）
- stdout JSON：doc/ocr→`{text,pages,is_scanned}`、asr→`{segments}`；**非 JSON 回退 v0 纯文本** → 用户旧 `pymupdf4llm_heavy` 零改动恢复可用
- `subprocess.run`→`Popen` + cancel 标记文件（监视线程写标记→宽限 3s→kill，timeout 兜底）

**管理 API + 前端**：
- 新增 `routers/plugins.py`：GET 列表（含禁用）· POST enable|disable（写 `plugin_states`，重启生效）· DELETE 卸载（resolve 路径校验 + 清理残留键 + Windows 占用反馈）· PUT config（写 `plugin_configs` + `set_plugin_config` 内存即时生效，**stdin 即含新 config**）
- `PLUGIN_REGISTRY`（Phase 1 建立）登记全部插件（含门控拒绝/禁用），管理 API 数据基础
- 设置页新增「插件」tab（PluginSettings.tsx）：按 category 分组卡片 + 启用开关 + 卸载二次确认 + config_schema 简版表单
- capabilities：插件判定 `bool(_license)`→`is_plugin` 标志；engineOptions 增 `displayName/userDesc/configSchema`
- EngineSelect：license 徽章去警告语义（v2：仅展示）；optionLabel 优先 displayName；ProviderSettings 修复丢 license/plugin 徽章

**参考模板 + 文档**：
- `backend/plugins/examples/` 重构：`pymupdf4llm_plugin.py`+`pymupdf4llm/{engine.py,requirements.txt}`（进程外参考）+ `simple_trusted_plugin.py`（trusted 进程内参考），删旧进程内示例；**不随包分发**（spec 不加 datas）
- `docs/plugins.md` 按 v1 契约重写（契约/门控/安装/发布指南）；新增 `test_plugin_loader.py`（14 用例）

### 为什么

whisper 已移除且不插件化；pymupdf4llm 作为 AGPL 引擎走插件通道，但插件返回类型不匹配导致不可用。按 12 号文档完整正式化插件系统，pymupdf4llm 为首个参考实现。

### 影响范围

- 后端：`plugin_loader.py` / `capabilities.py` / 新增 `plugins.py` / `config.py` / `main.py` / `registry.py`（命名空间注释）
- 前端：新增 `plugins.ts` / `PluginSettings.tsx` / `SettingsPage` 插件 tab / `ProviderSettings` / `EngineSelect` / `core.ts`
- 模板/文档：`examples/` / `plugins.md` / 新增 `test_plugin_loader.py`
- 验证：后端 7 测试全绿 + 前端 82 tests 全绿 + 启动冒烟（`/api/plugins` 返回用户已部署 pymupdf4llm_heavy）+
  端到端（旧纯文本插件走 subprocess → DocResult，文本提取成功）

---

## 2026-08-02 — 卸载时可选移除个人配置与数据

### 做了什么

**自定义 NSIS 模板**（`frontend/src-tauri/nsis/installer.nsi`，基于 Tauri 内置模板 + Handlebars 占位符），扩展 Tauri 卸载器「删除应用数据」复选框的行为：

- 勾选时除 Tauri 框架数据（`%APPDATA%\com.limo.app` / `%LOCALAPPDATA%\com.limo.app`）外，**一并删除 LiMo 业务数据目录** `~/.limo`（知识库/配置/文档）及降级目录（`%LOCALAPPDATA%\LiMo`、`%APPDATA%\LiMo`）
- `tauri.conf.json` 加 `bundle.windows.nsis.template` 指向自定义模板
- **默认不勾选** → 卸载重装保留数据；勾选 → 完全移除（含个人配置与知识库）

### 影响范围

- 打包配置（NSIS 模板 + tauri.conf.json），代码零改动
- 验证：`npx tauri build` 渲染成功，生成的 installer.nsi 确认含 `RmDir /r "$PROFILE\.limo"` 及降级目录

---

## 2026-08-02 — 全面漏洞排查与修复（P0 高危 8 项 + 关键 P1 10 项）

### 做了什么

3 个排查 agent 分区扫描（后端逻辑/并发安全/前端）共 55 项发现，去重分级后本次修复 **P0 高危 8 项 + 关键 P1 10 项**；P2 低危（信号量 acquire 在 try 外、mktemp 废弃、死代码、前端次要项、SSRF 重定向加固）记录不修。

**P0 安全（4 项）**：
- **CORS Origin 白名单**（main.py）— 从 `allow_origin_regex=".*"` 收紧为 `tauri://localhost`/`https://tauri.localhost`/本机 localhost 端口白名单，`_ForceCORSMiddleware` 仅白名单 origin 回显凭证 → 阻隔任意恶意网页跨站读取本地后端（含 `/api/config` 的 Cookie 明文、文档、日志）与 POST 篡改配置
- **serve_asset 路径遍历**（ingest.py）— 校验收紧到 `doc_dir/assets/`（原只限 data_root，filename 含 `..` 可越出读任意文档图片）
- **导出 temp_id 路径穿越**（export.py）— 校验字符集 + resolve 后限定 `_TEMP_DIR`（原 `os.path.join` 直拼，`..%2F` 可读/删任意 .zip）
- **content_preparer asset_ref 任意读** — `assets/../../config.yaml` 越界校验（原未限目录，可读任意文件 base64 发 LLM/导出包）

**P0 必崩（3 项）**：
- **Generator.config 未初始化**（generator.py）— `__init__` 不设 `self.config`，非流式 `POST /generate` 开 OCR → AttributeError（修复：`__init__` 加 config 参数，generate.py 传参）
- **process_image_legacy 500**（process.py）— 废弃端点直接调用带 `Depends` 的函数拿到未解析对象（修复：注入 store/config/guard 后转发）
- **chapter_indices 强转 int**（generate.py）— 非数字章节号 ValueError → 500（修复：`isdigit()` 过滤）

**P0 竞态（2 项）**：
- **适配器单例竞态**（registry.py + adapters/__init__.py）— `AdapterRegistry` 改存**类**，`route()` 命中前实例化 → 一请求一实例，并发导入的 `cookies`/`_engine_config`/`cancel_event` 不再互相污染
- **/process 断连任务泄漏**（process.py）— 手写 `_watch_disconnect` 从不 cancel，改复用 `disconnect_watcher` 自动清理

**P0 前端卡死（3 项）**：
- **streamChat 永久卡加载**（chat.ts）— 缺终态追踪，断连后 onDone/onError 不触发（对齐 generate.ts 的 `receivedDoneOrError`）
- **handleFactCheck 卡死**（PreviewArea.tsx）— `getConfig().then` 无 `.catch`，配置失败时 `factChecking` 永不复位
- **streamGenerate 取消误报**（generate.ts）— `cancelled` 不置终态标志 → 取消后误弹「连接意外断开」

**关键 P1（10 项）**：
- 非流式单产物截图写 `store.root` 错误目录 → 修正为文档目录 + 防御 None
- review 流式验证死锁（`queue.get()` 无超时 + cancel 瞬间等 sem 任务不放队列）→ 加 `wait_for` 超时
- EPUB 章节选择串文档 → PreviewArea 渲染加 `key={currentDocId}`
- 事件循环阻塞：image/webpage 适配器、封面下载、bilibili/youtube 字幕的同步 `requests.get` → 统一 `asyncio.to_thread`
- 临时资源泄漏：VL 截帧目录（数百 MB）、视频下载临时目录、上传临时源文件、空 media_dir、导出 ZIP 激活 `_TEMP_TTL` 过期清理
- LLMService.aclose()：generate 流式/chat（流式+非流式）/process/fact_check（流式+非流式）主端点关闭 httpx 连接池

### 影响范围

- 后端：`main.py` · `registry.py` · `adapters/{__init__,video,image,webpage}.py` · `routers/{generate,process,ingest,export,chat,fact_check}.py` · `generation/{generator,review,content_preparer}.py` · `llm/service.py`
- 前端：`api/{chat,generate}.ts` · `components/preview/{PreviewArea,EpubSkeletonPanel}.tsx`
- 验证：后端 5 单测全绿 + TestClient 7 项端到端（CORS 白名单/遍历拒绝/temp_id/legacy 不再 500）+ Generator.config + 前端 build + 82 测试全绿
- 记录不修：P2 低危（见计划文末）

---

## 2026-08-02 — 整理版原文适度利用 Markdown 排版

### 做了什么

**polish（整理版原文）5 类 prompt 统一加入「适度 Markdown 排版」引导** — 解决 polished 产物偏「段落式」、Markdown 排版能力未充分利用的问题。

- **背景**：polish 定位「忠实整理原文」，此前 prompt 只引导「`##` 分段标题」+ 公式 LaTeX，缺少像 notes 那样的排版引导 → 实际产物标题层级有，但加粗/列表/引用块/表格呈现弱
- **改动**：`_POLISH_TASK`（视频）/ `_DOC_POLISH_TASK` / `_WEB_POLISH_TASK` / `_TEXT_POLISH_TASK` / `_IMAGE_POLISH_TASK` 末尾各加一条「适度利用 Markdown 排版」：
  - 原文本身就是列表/表格/引用的内容 → 用对应 Markdown 元素呈现（`-` / `| ... |` / `>`）
  - 关键术语/数据可加粗；段落间空行分隔
  - **明确禁止为排版重组/改写/总结原文**——排版服务于忠实呈现，结构化重组是「笔记」职责（忠实优先，避免偏离 polish 定位）
- **影响**：仅 prompt 文本，零代码逻辑改动；已生成产物不变，重新生成后生效

### 影响范围

- 后端 `app/generation/prompt.py`（5 个 polish task 常量）
- 已生成产物不受影响（重新生成才用新 prompt）

---

## 2026-08-02 — 文档全面整理：清理过时文档 + 合并有效内容

### 做了什么

**1. 删除 10 个过时/无参考价值文档**（约 3000 行）：

- `docs/system-design-spec.md`（1146 行）— 原始设计蓝图，已标注「实际实现见 CLAUDE.md/STATUS」，技术选型（FunASR/MinerU/PaddleOCR）全部被实际实现取代
- `docs/doc-transcript-design.md`（256 行）— Doc 转录原始提案，有效部分（五类文档差异总览）已合并进 architecture.md
- `design/engine-standards.md`（246 行）— 历史引擎评审（pymupdf/faster-whisper 均已移除、绑定 key 已拆分子场景），有效部分（评判标准）已合并进 architecture.md
- `pi-docs/` 下 7 个文件（竞品分析 FlyMD/memos/immich、架构图、待做列表、设计提案、计划审阅）— 借鉴点已沉淀到 CLAUDE.md；**pi-docs/ 目录保留**（作为 agent 评审文档存档处）
- 注：删除的架构图/待做列表/设计提案中有价值的**待做项已并入 CLAUDE.md「🚀 规划中」**

**2. 合并有效内容到 [architecture.md](docs/architecture.md)**：
- 追加「五类文档差异总览」表 + 提取策略要点（Word 文档流 / PPT 按页 / Excel 按 sheet / EPUB 分章）
- 追加「引擎评判标准」（职责兑现：capabilities 宣称必须做到）
- 更新过时内容：引擎计数 13→11（ASR×4→×2）、AI 审核三阶段→四阶段（Static Review→Verification→Evidence→Report）、Process 取消陷阱（已全部接入 disconnect_watcher）

**3. CLAUDE.md 更新**：
- 新增「🚀 规划中」节（语义搜索 / 更多导入源 / 知识图谱 / 渲染风格预设 / remark-tag / 沉浸阅读 / 插件挂载 / 后台队列 / 分层重构 / UI 重构，合并自被删的 08/09）
- 文档索引移除 4 个被删文档条目（system-design-spec / doc-transcript-design / engine-standards ×2）

**4. 其余文档过时内容清理**：
- `design/components.md`：faster-whisper/Groq/PaddleOCR/TrOCR 引擎 mockup → bcut-asr/dashscope-asr/tesseract（当前实际引擎）
- `docs/STATUS.md`：引擎计数 13→11
- `docs/prompts-inventory.md`：「pymupdf 合并单元格错位」→「引擎合并单元格错位」（pymupdf 已移除）
- `SECURITY_AUDIT.md`：标注时点审计性质 + H1/M1/L1 已修复状态（追溯用，避免误读为当前漏洞）

### 影响范围

- 纯文档维护，零代码改动
- 保留文档：CLAUDE.md / README.md / SECURITY_AUDIT.md / docs（architecture/frontend-patterns/export-pipeline/plugins/prompts-inventory/STATUS/BUILD/qianwen-models/user-scenarios/CHANGELOG）/ design（components.md + tokens.css）/ pi-docs（空目录）

---

## 2026-08-02 — 移除 faster-whisper 内置 ASR + 移除引擎自动降级

### 做了什么

**1. faster-whisper 彻底移出内置** — 体积大（模型 464MB~3GB）、CPU 推理慢、GPU 又依赖笨重的 CUDA/cuDNN/cuBLAS 运行时，不适合随安装包分发。移除后默认 ASR 只剩 **bcut-asr**（免费云，无需 Key）+ **dashscope-asr**（需 Key）。

- 抽取 `TranscriptResult` / `TranscriptSegment` 到独立 [transcript.py](backend/app/engines/transcript.py)（原定义在 asr.py 里，删除后 bcut/asr_cloud 的 import 不破）
- 删除 `FasterWhisperEngine` 注册 + [asr.py](backend/app/engines/asr.py) 整文件
- 打包清理：spec 移除 `faster_whisper`/`ctranslate2`/`av`/`tokenizers`（保留 `onnxruntime`——magika/rapidocr 依赖）、pyproject 移除 `faster-whisper` 依赖
- **用户 config.yaml 残留迁移**（防启动后设置页仍有 whisper 条目、绑定后无引擎报错）：`init_engines` 开头清理 `engine_providers` 的 faster-whisper 整组 + `engine_models` 相关条目 + 残留 `engine_auto_fallback` 键，变更计入 save_config 判定落盘；`config.py` 的 `_sync_builtin_models` 废弃 provider 集合加入 faster-whisper（双保险）
- 示例文档 ASR 表格 / QA 同步更新（本地转写引擎标注「需自行安装」）

**2. 移除引擎自动降级**（`engine_auto_fallback`）— 内置引擎官方测试稳定，绑定列表改为**只取第一个可用引擎，失败直接报错**；用户换引擎去设置页手动改。降级链分工：
- **引擎**（内置，稳定）→ 移除多引擎尝试循环（image/video/document/process 四个解析点）
- **LLM**（外部服务，中转站不稳定需常换）→ **保留**备用模型降级语义，独立于引擎体系。process.py 的 LLM 不可用 → OCR 引擎回退**无条件保留**（大模型降级有价值，计划中的「备用模型配置」本次不实现，仅记录方向）
- **视频字幕回退**（字幕失败 → 下载转写）→ 属网络获取策略回退，**默认执行、不向用户暴露**：去掉 `engine_auto_fallback` 条件无条件回退，warning 弱化为 **debug 日志**
- `_transcribe` 的 fallback 链循环（`while current is not None`）一并简化：单引擎执行，防未来插件设置 fallback 复活降级行为
- 报错文案清理：document.py「或在设置中开启引擎自动降级」→「请检查引擎配置或更换引擎」；process.py「自动降级已关闭」文案随分支删除
- 前端：删除 `getEngineAutoFallback`/`setEngineAutoFallback` API + ProviderSettings 的「引擎自动降级」开关 UI + faster-whisper 删除按钮保护引用

**3. LLM 自动降级列入计划（本次不实现）** — 设计方向：`resolve_model()` 或 `LLMService.call` 层捕获 provider 失败 → 自动切换到配置的备用 provider/模型（如 deepseek 中转失败 → 切 openai 直连）。备用模型是**独立 provider 级配置，不参与 engine_bindings 体系**。需要设置页「备用模型」配置 + 失败判定（网络/429/5xx）+ 切换不重试死循环。

### 影响范围

- 后端：新增 `engines/transcript.py`（数据类抽取）；删除 `engines/asr.py`；改 `engines/__init__.py`（迁移 + 删 whisper 注册）、`adapters/{image,video,document}.py`（绑定只取第一个）、`routers/process.py`（LLM 回退无条件 + 单引擎）、`routers/{model,capabilities,ingest}.py`（删降级 API / provider / 注入）、`core/config.py`（删配置项 + provider + 废弃集合）
- 前端：`api/settings.ts` + `components/settings/ProviderSettings.tsx`（删降级开关）
- 测试：`tests/test_engines.py`（删 whisper 断言）、`tests/test_e2e_smoke.py`（删第 8 节降级 toggle）
- 文档：CLAUDE.md / architecture.md / plugins.md / README.md / STATUS.md / sample_docs 同步

---

## 2026-08-02 — 预置示例文档「✨ 使用说明」+ 修复全新安装启动崩溃

### 做了什么

**1. DocLibrary 首启自带「✨ 使用说明（示例）」文档** — 面向新用户的示例/欢迎文档，作者写给用户的应用说明 + 免责声明，同时演示 LiMo 的 Markdown 排版能力。

- 内容分层三 tab：
  - **「笔记」** — 面向大众：一分钟了解 / 核心功能 / 痛点场景对照 / 开始使用 / 名字与理念（沥墨）/ 常见问题 QA
  - **「整理版原文」** — 深度说明：处理链路 / 转换引擎详解（Doc/ASR/OCR 三张表格）/ 大模型选择与场景绑定 / 自定义大模型接入（图形界面 + config.yaml）/ 数据与隐私 / 免责声明
  - **「转录」** — 简短欢迎语
- **内容源为独立 md 文件**（`app/core/sample_docs/notes.md` + `polished.md`）—— 改 md 即更新，无需改代码；PyInstaller datas 同步打包
- **内容指纹同步机制**（marker 存 notes+polished 联合哈希）：
  - 改 md → 下次启动自动同步
  - 内容更新 → 即使被删除也重建（**兼作更新说明**，让用户知道新内容）
  - 内容无更新 + 已删除 → 尊重删除不打扰
  - 重新安装（数据目录清空）→ 自然重建
- 实现：[sample_doc.py](backend/app/core/sample_doc.py) + [main.py](backend/main.py) startup 挂载（置于 init_engines 之后）+ [LiMoBackend.spec](backend/LiMoBackend.spec) datas

**2. 修复全新安装启动崩溃（严重）** — `DEFAULT_CONFIG` 的 `engine_bindings` 中 `doc`/`doc:epub` 为**空 list**，[engines/__init__.py](backend/app/engines/__init__.py) 清理失效绑定循环 `emap.get([])` 抛 `TypeError: unhashable type: 'list'` → 全新安装（无 `~/.limo/config.yaml`）启动必崩；打包版能跑只因用户 config 绑定已非空。修复：空 list 视为无效绑定，自动回填该子场景默认引擎（doc→pypdfium2、doc:epub→ebooklib 等）。

### 影响范围

- 后端：新增 `app/core/sample_doc.py` + `app/core/sample_docs/`（首次启动自动创建示例文档，不影响已有数据；marker 幂等）
- 修复启动崩溃 → 新用户开箱即用
- 前端零改动

---

## 2026-08-02 — v0.1.5：docling 打包崩溃 + 文档图片三开关链路修复

### 做了什么

**1. 打包版 docling 引擎崩溃（pdf_resources 缺失）** — v0.1.4 打包版导入 PDF（绑定 docling）报 `RuntimeError: no existing pdf_resources_dir: C:\Program Files\LiMo\_internal\docling_parse\pdf_resources/` → FatalError。

- 根因：`docling_parse`（docling 的 Rust 解析后端，独立包）含数据目录 `pdf_resources/`（440 文件，24MB）+ 3 个依赖 DLL（`libgcc_s_seh-1.dll`/`libstdc++-6.dll`/`libwinpthread-1.dll`）。spec 中 `collect_all` 列表只有 `'docling'`，不会递归收集依赖包 `docling_parse` 的数据文件 → 打包版 `_internal\docling_parse\` 只有 .py/.pyd，`pdf_resources` 缺失。docling 初始化 `DoclingPdfParser`（编译进 `pdf_parsers.pyd`）按 `Path(__file__).parent / "pdf_resources"` 定位失败
- 修复：spec `collect_all` 列表加 `'docling_parse'`（实测 datas 448 条含 440 条 pdf_resources + 3 DLLs + 4 hiddenimports）
- ⚠️ 教训：`collect_all('docling')` 不递归收集依赖包数据文件，PyInstaller 下必须逐个显式收集有数据目录的依赖包

**2. 文档图片三开关（保留/OCR/AI分析）正交化** — 原前端把三个开关做成嵌套依赖，多组合不可达：

- 前端 [GenerateOptions.tsx](frontend/src/components/gen-panel/GenerateOptions.tsx)：`AI 分析图片` 曾依赖 `formatImages && !ocrEnabled`（OCR 勾上后 AI 分析选项消失，人为互斥）；`OCR`/`AI 分析` 都依赖 `formatImages`。改为三个平铺独立开关，任意组合
- 前端 [GenPanel.tsx](frontend/src/components/gen-panel/GenPanel.tsx)：`image_analyze`/`image_optimize` 曾被 `formatImages` 门控（不勾保留图片则 AI 分析静默失效），改为独立传递
- 后端 [generator.py](backend/app/generation/generator.py)：非流式端点两处 OCR 调用漏传 `doc_dir` → 新格式 `assets/img_NNNN.png` 定位到错误路径 `{store_root}/files/assets/...`，OCR 全部 `[图片缺失]`。流式端点原本正确，前端走流式所以未暴露
- 后端 [ocr_preprocessor.py](backend/app/generation/ocr_preprocessor.py)：正则只匹配 `img_NNNN`，docling 引擎产出 `fig_NNNN` 被跳过。放宽为 `(?:img|fig)_\d{4}`（捕获组结构保持 3 元素解包不变）

**3. 笔记 prompt 加 Markdown 排版引导** — 5 个笔记变体（视频/网页/文本/图片/文档）统一加一句：「充分利用 Markdown 排版：不要只用列表平铺内容。按内容形态选样式——对比/条目用表格、论断/名言用引用块、原始文本用代码块、章节用分割线、重点加粗」。（通用版，按内容形态给判据而非列举式）

**4. GenPanel 类型选择器顺序调整** — `link → video → text → file → image` 改为 `video → file → link → text → image`，视频和文档（file）靠前。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/LiMoBackend.spec` | `collect_all` 加 `'docling_parse'` |
| `backend/app/generation/generator.py` | OCR 调用补传 doc_dir（2 处） |
| `backend/app/generation/ocr_preprocessor.py` | 正则容纳 img_/fig_ |
| `backend/app/generation/prompt.py` | 5 个笔记 prompt 加 Markdown 排版引导 |
| `frontend/src/components/gen-panel/GenerateOptions.tsx` | 三开关平铺独立 |
| `frontend/src/components/gen-panel/GenPanel.tsx` | image_analyze 独立传递 |
| `frontend/src/components/gen-panel/ImportSection.tsx` | 类型选择器顺序 |

---

## 2026-08-01 — 发布前修复：换行标题目录崩溃 + e2e_smoke 健壮化

### 做了什么

**标题含换行/控制字符 → 目录创建崩溃（SSE 永不完成）** — `_get_or_create_doc_dir` 的清洗正则 `[<>:"/\\|?*]` 漏掉控制字符。标题含 `\n`（如 `_extract_title_from_rawtext` 从多行文本提取标题）时 `Path.mkdir` 抛 `WinError 123`（文件名语法不正确）→ `run_pipeline` 崩溃（`Task exception was never retrieved`）→ **done 事件永不发** → 客户端（前端/测试）无限等待。

- 根因：`re.sub(r'[<>:"/\\|?*]', '', title)` 未覆盖 `\x00-\x1f`（换行/回车/TAB 等 Windows 非法控制符）
- 修复：两处（查找/创建）清洗正则补 `\x00-\x1f`
- 触发面：任何标题含换行的文本/文档导入；e2e_smoke 的 text 用例（`# Hello World\n\nTest...`）恰好触发——**发布门禁价值：不跑全量测试就发现不了**

**e2e_smoke SSE 读取健壮化** — `_post_form` 改用逐行读 + done 事件 break，不依赖连接关闭（uvicorn StreamingResponse 结束后保持 keep-alive，`read()` 可能等 EOF 超时）。timeout 15→30s。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/storage/store.py` | `_get_or_create_doc_dir` / `_get_doc_dir` 清洗补控制字符 |
| `backend/tests/test_e2e_smoke.py` | SSE 逐行读取 + timeout 30s |

---

## 2026-08-01 — 生成并行化（推翻串行）+ 思考链展示 + 产物写反修复

### 做了什么

**服务端（generate.py / service.py / prompt.py）**：
- **双产物改回并行（推翻上一条目的串行决策）**：上一条记录"串行"原因是 deepseek 并发排队（第二个响应 108s+），用户实测后确认排队结论证伪——108s+ 实为 **thinking 阶段思考时间长**（服务端静默不推流）。并行时两路同时开始，总耗时 = max 而非叠加，且 notes 先出、体验渐进。两路事件经 `asyncio.Queue` 汇合（chunk 交错到达）→ SSE 顺序安全，最终 `asyncio.gather` 收拢结果
- **思考链回调**（`LLMService.stream` 加 `on_reasoning`）：deepseek-v4 的 `reasoning_content` 逐段回调，不混入正文——`getattr(delta, "reasoning_content", None)` 防护 qwen 等无此字段的模型；单产物路径收集后统一带出（回调累积 → anext 返回 → 发 SSE）
- **思考强度控制**（`reasoning_effort` 经 OpenAI SDK `extra_body` 传递）：deepseek-v4 文档确认 thinking 默认开启且 effort 默认 high——复杂任务思考 90s+ 零数据（用户感知"慢到诡异"）。**polished 专用 `reasoning_effort="low"`**：整理版原文是忠实转录整理任务，无需深度思考，思考静默期大幅缩短（用户实测"生成速度得到极大提升"）。qwen 忽略 extra_body 未知字段，天然兼容
- **chunk 间隔超时 90s → 120s**：45s/90s 判定曾把正常长思考误判为挂起触发重试；推理链 chunk 到达时 anext 正常返回（有 reasoning 也在推流），不触发超时
- **prompt 模糊约束**：三个 polish task（_POLISH_TASK / _DOC_POLISH_TASK / _IMAGE_POLISH_TASK）加"转录不清处不强行推理、忠实呈现"，减少过度思考诱因
- **⚠️ 产物写反修复（gather 解包顺序错位）**：`asyncio.gather(*_tasks)` 返回顺序 = 任务创建顺序，但解包时 notes 任务的结果被赋给 `polished_chunks` → notes_prompt 的笔记内容存进 polished.md、polished_prompt 的整理版原文存进 notes.md（前端显示层忠实读取磁盘，两边都显示"对的文件、错的内容"）。实测铁证：notes.md 43KB 流水账 vs polished.md 14KB 结构化要点。修复：解包顺序与任务创建顺序对齐

**前端（GenPanel / PreviewArea / ProcessingPlaceholder）**：
- **思考链展示**：SSE `type=reasoning` → `onReasoning` 回调 → `TaskInfo.reasoning {notes, polished}`（与 progress 同构，并行双产物 task.mode 来回跳也不串台）→ ProcessingPlaceholder 渲染"模型思考中..."滚动区。思考链是临时展示（打消用户不耐心），正式文本流式生成后直接替换
- **reasoningBuf per-taskKey Map**：双文档并发生成思考链不互踩（此前单 ref 污染 + 节流负数冻结）
- **genOutcomeRef 状态机**（ok/error/partial）：修复"超时报错 + 生成成功双 Toast 矛盾"——部分失败只报对应产物的 warning，不再同时报超时错误 + done
- **cancelledHandled ref**：并行两路 cancelled 事件去重（此前双产物取消会触发两次取消 Toast）
- **onDone refreshSaved 防竞态**：done 后立刻重新生成 → 旧 getMarkdown 响应不再覆盖新流式内容
- **taskForView（占位按 tab 匹配任务）**：替换 9 处 `activeTasks[0]`——多文档/多产物并行时占位符选错任务（"正在生成笔记"但实际在生成整理版）
- **F4 守卫 / F5 fact_check 清理 / N2 fact_check 生成中守卫 / F6 Excel displayContent**：生成中切文档/切产物竞态全面加固
- **mindmap 有内容直接渲染**：笔记生成完成后思维导图不再显示"正在生成中"
- **版本切换器 isStreaming 禁用**：生成期间禁止切换 v1/v2（防止读到生成中的半成品）

### 为什么

上一条目"串行"决策基于"deepseek 并发排队"的错误归因。用户贴日志（20:01:39 两条 LLM 流式同时开始，5265 + 14776 chars 完成，无排队）证明并发本身没问题；`polished 挂起（第 1 次），重试` 日志实为 120s 内无 chunk 的 thinking 静默。根因链：thinking 默认 high → 复杂任务思考 90s+ 静默 → 被 45s/90s 判定误判挂起重试 → 用户感知"慢到诡异"。解法四件套：思考链展示（等待有反馈）+ 120s 容忍 + effort=low（polished 专用）+ prompt 模糊约束（减少过度思考诱因）+ 并行（总耗时 max）。

产物写反是并行化引入的回归：gather 收拢结果的解包顺序与任务创建顺序不一致，notes/polished 内容互换。用户"笔记预览栏生成的是整理版原文"的报障由此而来。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/llm/service.py` | on_reasoning 回调 + reasoning_effort extra_body |
| `backend/app/routers/generate.py` | queue 汇合并行 / 120s 超时 / saved_partial / **gather 解包顺序修复** |
| `backend/app/generation/prompt.py` | 三个 polish task 模糊约束 |
| `frontend/src/api/generate.ts` | onReasoning + type=reasoning 分发 |
| `frontend/src/store/types.ts` | TaskInfo.reasoning |
| `frontend/src/components/gen-panel/GenPanel.tsx` | onReasoning / Map 化 / genOutcomeRef / cancelledHandled / refreshSaved |
| `frontend/src/components/preview/PreviewArea.tsx` | taskForView / F4-F6 / N2 / mindmap / 版本切换器禁用 |
| `frontend/src/components/preview/ProcessingPlaceholder.tsx` | 思考链渲染 + mode prop |

---

## 2026-08-01 — 生成链路全面修复（服务端串行 + 前端产物接收 + 切换竞态）

### 做了什么

**服务端（generate.py）**：
- **双产物改串行**（notes → polished）：deepseek 对并发请求排队严重（并行时第二个响应 108s+），串行时每个请求独占连接（~30s/个），前端先看到笔记再看到整理版，渐进反馈符合旧版体验
- **chunk 间隔超时**（90s）：deepseek 长输出流式中途断流时 httpx 静默等 EOF（不抛异常）→ 前端永远"加载中"。`asyncio.wait_for + anext` 判定挂起 → 重试/报错
- **空响应重试**：LLM finish=stop 但 0 chars → 重试一次，仍空 → warning 事件（前端 Toast，另一产物不受影响）
- **未完成 marker**：`.generating` 标记 + `capabilities.interruptedGenerations` 上报，前端启动提示"上次应用中断"
- **双产物取消补 `saved_partial`**：此前 cancelled 事件缺字段 → 前端无"已保留"提示

**前端产物接收（GenPanel/PreviewArea）**：
- **配置变更事件总线**（`lib/configEvents.ts`）：设置页保存后广播 `limo:config-changed`，GenPanel 重新拉取模型/厂商列表、App 刷新 capabilities —— 修复"设置保存后导入面板需手动刷新页面才显示"
- **onDone 以服务器内容刷新产物**：此前只刷 versions 不刷内容 → `streamDisplay=false` 或流式丢失时产物 tab 永远空白"尚未生成"
- **onDone 防竞态**：done 后立即重新生成 → 旧 getMarkdown 响应覆盖新流式内容（`processingTasks[taskKey]` 重新激活检查）
- **取消保留 partial**：双产物取消时 buffer 写入 store（此前 both 模式丢弃）
- **polished-only 视图跳转**：生成后直接跳整理版 tab（此前强制 summary，看不到整理版流式）
- **产物级进度**：ProcessingPlaceholder 显示「正在生成整理版原文...（已生成 N 字符）」（TaskInfo.progress）

**切换链路（生成中切文档/切产物）**：
- **onChunk 顺序重构**（核心）：isSameDoc 检查后置——切走期间流式内容无条件写入 docId 隔离的 buffer，切回显示完整（此前第一行 return 丢弃全部 chunk → 流式断裂）
- **productChars per-taskKey Map**：双文档并发生成（MAX=2）进度不互踩
- **切文档 effect 竞态守卫**：生成快结束时切回 → 旧版本 V0 不再覆盖 onDone 的 V1（notes/polished/fact_check 三处）
- **切文档状态清理**：补 `factCheckProgress`/`factChecking`（A 审核中切到 B 不再残留 spinner）
- **Excel 分支改用 displayContent**：生成中切走切回显示流式而非"尚未生成"
- 生成期间禁用版本切换器

### 为什么

用户实测发现：整理版原文生成慢/挂起（deepseek 流式断流——服务端问题，换 qwen 走同一代码路径 33s 完成证明链路本身正常）、产物 tab"尚未生成"（onDone 不刷新内容）、设置保存后下拉不更新（无事件通知）、生成中切换文档后流式断裂（onChunk 提前 return）。逐条复现后按「服务端事件契约 → 前端接收 → 切换竞态」三层修复。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/routers/generate.py` | 串行生成 + chunk 超时 + 重试 + marker + saved_partial |
| `backend/app/routers/capabilities.py` | interruptedGenerations 扫描 |
| `frontend/src/lib/configEvents.ts` | 新建：配置变更事件总线 |
| `frontend/src/components/gen-panel/GenPanel.tsx` | onChunk 重构 / Map 化 / onDone 刷新 / 防竞态 / 视图跳转 |
| `frontend/src/components/preview/PreviewArea.tsx` | 守卫 / factCheck 清理 / Excel displayContent / 版本切换器禁用 |
| `frontend/src/components/settings/ProviderSettings.tsx` | 保存后广播配置变更 |
| `frontend/src/App.tsx` | 订阅配置变更刷新 capabilities |

---

## 2026-08-01 — E2E 验证 + 空壳目录 bug 修复

### 做了什么

**端到端验证 14 项清单**（存储重构 + 前端重构后首次全链路实测）：文本/图片导入 → `{标题}-{6位hex}/` 目录 → 双产物生成 → 两版制（notes.md + notes.prev.md）→ v1/v2 版本切换（内容对比不同）→ HTML 导出（自包含零 CDN）→ ZIP 导出（中文文件名数据正确）→ assets 服务端点 → OCR 处理链路（`process/extract.md`）→ coverUrl v3 格式 → 删除 rmtree + index 清理。

**修复：导入重复创建空壳目录** — `_get_or_create_doc_dir` 在 index 无 entry 时创建目录但不记录，`save_transcript` 再次调用时 index miss → 重复创建 + `_1` 序号 → 空壳目录永久残留（实测一次导入产生两个目录）。修复：创建目录后立即占位记录 dir_name 到 index，`save_transcript` 改为 upsert 补全字段。修复后实测只产生一个目录。

**排除 ZIP 中文名乱码假警报** — 验证中发现 ZIP 文件名显示乱码（`ת¼ԭ��`），深度排查到 zipfile 源码层确认是 GBK 终端显示假象：Python 3.12.13 的 zipfile 写文件名时 ASCII 编码失败自动回退 UTF-8 + 设 flag 0x800，布尔比较 `names[0] == '测试文档/测试笔记.md'` 为 True，跨平台解压正常。**export.py 无改动**。

### 为什么

单元测试全绿只证明单层正确，跨层链路（导入→目录→生成→版本→导出→删除）此前从未端到端确认过。验证即发现存储重构的核心路径存在重复创建目录缺陷——这正是清单验证的价值。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/storage/store.py` | `_get_or_create_doc_dir` 占位 entry + `save_transcript` upsert（16 行） |

---

## 2026-08-01 — 前端工程质量重构（五项，可维护性视角）

### 做了什么

**store 拆 slices** — `store/index.ts`（497 行单文件）拆为 `types.ts` + 7 个 slice（theme/docs/content/tasks/chat/ui/toast），`index.ts` 只剩组合层 + 模块副作用。探索确认 action 层零跨域引用（每个 action 只读自己域字段），84% 字段是单字段单 setter 纯状态，拆 slice 无行为风险。`Store[...]` 自引用类型抽为具名类型（`ActiveView`/`MarkdownMeta` 等），`msgCounter` 归 toastSlice，apply 五主题轴 + matchMedia 监听留在 index.ts（避免运行时循环依赖）。39 字段 53 action API 零变化，22 个消费方零改动。

**client.ts 按域拆分** — 967 行拆为 `core.ts`（共享核心）+ 7 个域文件（capabilities/transcripts/ingest/generate/chat/settings/export），`client.ts` 变纯 barrel（15 个 import 方零改动），`BASE` 保持模块私有不泄漏。顺带合并 `fetchSSE`/`fetchFileSSE`（154 行近逐行重复）为 `ingestStream` + `fallbackJson`，手写 SSE 解析统一走 `sse.ts` 的 `parseSSEStream`（消除第三份重复）。

**contentSource 类型绑定** — `TabDef.contentSource` 的取值逻辑此前在 3 处独立维护（PreviewArea displayContent 硬编码 `activeView === 'polished'` 三元、CopyContentBtn switch、ExportMenu contentKey 中间层），且 `displayContent` 根本没走 contentSource。新建 `lib/contentSources.ts`：`CONTENT_GETTERS: Record<ContentSource, getter>`（新增枚举值编译器强制补实现）+ `getContentForView` 统一入口，ExportMenu 冗余中间层整层删除。

**PreviewArea 展示拆分** — 773 → 646 行。6 个 props 驱动组件（`EmptyState` 替代 8 处重复空态、`ProcessingPlaceholder`/`VersionSwitcher`/`TranscriptQualityNote`/`ImageResultView`/`CopyContentBtn`），全部无 store 依赖。新增**首个组件测试**（`CopyContentBtn.test.tsx` 3 用例，@testing-library/react 此前已装未用）。

**useTaskRunner hook** — `handleRetranscribe`/`handleProcessImage` 逐字重复的 ~20 行样板（capturedId → taskKey → abort → setProcessing → await → stale 检查 → 错误处理 → finally 清理）抽取为 `hooks/useTaskRunner.ts`。AbortError 静默返回，finally 用 `setProcessing(key, null)` 前缀树形删除，onSuccess 内部多段 await 后的 stale 检查逐字保留。`handleGenerate`/`handleImport` 结构不同不强行套用。

### 为什么

用户收到前端工程质量报告（可维护性视角）：God Store 单文件、组件与 store 强耦合、字符串字面量隐式约定（contentSource 映射 3 处独立维护、漏改不报错）、client.ts 长文件、handler 模板重复。决策：P0-P2 全部五项 + store 一次全拆（探索确认 action 零跨域引用，全拆行为风险低）。

### 两处有意的行为变更（重构附带修正）

1. **复制按钮**：extract/table/digitize/understand 四 tab 有处理结果时复制按钮新出现（与 ExportMenu 复制行为对齐）
2. **流式期间非 notes/polished tab**：原逻辑任何非 polished tab 一律读 notes 缓冲（fact_check tab 显示笔记流式内容的 bug 语义）→ 现在读各自内容源

### 影响范围

| 文件 | 变更 |
|------|------|
| `store/index.ts` | 重写为组合层（~60 行） |
| `store/types.ts` + 7 个 slice | **新建** — 39 字段 53 action 按域拆分 |
| `api/client.ts` | 重写为 barrel |
| `api/core.ts` + 7 个域文件 | **新建** — 按域拆分，ingestStream 合并 |
| `lib/contentSources.ts` | **新建** — Record<ContentSource> getter |
| `lib/tabRegistry.ts` | contentSource 抽具名类型 |
| `components/preview/` 6 个组件 | **新建** — 展示拆分 |
| `components/preview/PreviewArea.tsx` | 773 → 646 行，getter 化 + 组件化 |
| `components/ui/ExportMenu.tsx` | 删除 contentKey 中间层 |
| `components/gen-panel/GenPanel.tsx` | 两个 handler 改 useTaskRunner |
| `hooks/useTaskRunner.ts` | **新建** |
| `test/CopyContentBtn.test.tsx` | **新建** — 首个组件测试 |

验证：82 tests 全绿（79 + 3 新组件测试），`npm run build`（tsc + vite）+ `npm run lint` 通过。

---

## 2026-07-31 — 存储重构 v3 + 插件引擎 + 引擎审计（补记）

### 做了什么

**存储重构（v3 按文档聚合）** — 存储从"类型分桶 + 随机 ID"重构为"一文档一目录"：`{sanitized_title}-{6位hex}/`，内含 `transcript.md`（纯文本 rawText）、`{mode}.md` + `{mode}.prev.md`（两版制）、`process/{mode}.md`、`assets/`（图片统一入文档目录）。md 文件用**相对路径** `assets/xxx` 引用图片，Typora/Obsidian 直接打开可渲染；磁盘相对路径 → API 绝对 URL 由 `rewrite_asset_urls(content, tx_id)` 统一改写（所有内容类 API 返回前调用）。删除文档 = `rmtree(doc_dir)`。目录名冲突循环追加序号，标题变更时重命名目录。INDEX_VERSION 3。软件未投产，测试数据直接删，零迁移负担。

**插件引擎（进程外加载）** — AGPL 引擎 pymupdf4llm 以插件形式加载：`~/.limo/plugins/` 下 `EnginePlugin` 声明（`command` 指向子目录 venv 隔离依赖），`plugin_loader.py` 动态注册进 EngineRegistry，capabilities 的 `fileTypeMap` 按插件 `file_types` 自动追加。Windows 修复：command 解析为绝对路径 + `sys.stdout.reconfigure(encoding="utf-8")` 防 GBK 乱码。

**引擎 UI 一致性审计** — 删除 3 个 text_only 变体引擎（docling-text/python-docx-text/python-pptx-text，与完整版重复无意义）；子场景统一为 `doc:pdf`/`doc:word`/`doc:ppt`/`doc:excel`/`doc:epub`（旧 `doc:text+image`/`doc:text-only`/`doc:scan-ocr` 清理）；engineProviders `pymupdf` → `doc-local`；engine_models 残留清理（21 → 9 条）；`_sync_builtin_models` 改为保守方案（只补不删，清理废弃 provider 整组）。

**体积优化** — 移除 opencv-python 死依赖（app 代码零引用，视频抽帧用 ffmpeg，帧质量用 PIL+numpy），节省 ~90MB 打包体积。

### 为什么

存储重构：类型分桶时代一个文档的产物散落在 6 个目录，只有 index.json 知道关系，打开数据目录无法看出哪个文件属于哪个文档。用户强调"尽早做，等以后真的有用户再改就麻了"。

插件引擎：pymupdf4llm 是 AGPL 许可证，内置会污染整体许可证；进程外 subprocess 模式 + 独立 venv 彻底隔离。

引擎审计：用户反馈"导入面板的快捷选择栏与设置页不一致，有些引擎是重复的没有意义"——text 变体引擎与完整版行为重复，UI 出现无意义选项。

### 影响范围

| 文件 | 变更 |
|------|------|
| `backend/app/storage/store.py` | v3 重构（doc_dir/两版制/rewrite_asset_urls/_rebuild_index） |
| `backend/app/routers/ingest.py` | 图片持久化入 doc_dir + 时序修复 + `/api/assets/` 端点 |
| `backend/app/engines/__init__.py` | 删 text 变体注册 + 失效绑定清理 |
| `backend/app/engines/plugin_loader.py` | 进程外插件加载（Windows 路径修复） |
| `backend/app/core/config.py` | `_sync_builtin_models` 保守化 + doc-local |
| `backend/app/routers/capabilities.py` | fileTypeMap 动态追加插件引擎 + scenarioLabels 统一 |
| `backend/app/adapters/document.py` | `_ext_to_sub_scenario` → doc:pdf |
| `backend/pyproject.toml` + `LiMoBackend.spec` | 移除 opencv-python |
| `frontend/` 多处 | 引擎场景 key、assets URL、文案同步 |
| `~/.limo/plugins/` | **新增** pymupdf4llm 插件（AGPL 隔离） |

---

## 2026-07-31 — Bug 修复 + SSE 重构

### 做了什么

**Cancel 按钮无效** — 多任务重构将 `createAbort` 改为 per-task key（如 `doc123__generate`），但 `cancelTask` / `cancelOp` 仍用 base docId（`doc123`）查找 → 永不命中。用户每次取消都浪费了 LLM token（HTTP 连接未断开）。修复：`registerAbort` 内部归一化 key，doc 任务按 base docId 存储，导入任务独立 Map 支持多导入并发。

**导入取消后仍继续运行** — `fetchSSE` / `fetchFileSSE` 的 catch 块未检查 `AbortError`，取消后回退到非流式端点继续请求。用户以为取消了，实际后端还在处理。修复：catch 开头加 `if (err.name === 'AbortError') return`。

**SSE 解析提取公共函数** — 新建 `api/sse.ts`，提取 `parseSSEStream()`。`streamGenerate` / `streamFactCheck` / `streamExportProgress` / `streamChat` 4 个函数的 SSE 解析循环统一为一个调用。消除 130+ 行重复代码。

**废弃 types 替换** — 移除 `@types/react-syntax-highlighter`（v15 类型与 v16 不匹配），新建最小类型声明文件 `src/types/react-syntax-highlighter.d.ts`。

**VideoBanner 阅读宽度适配** — 将 VideoBanner 从 `preview-content` 的 `maxWidth` 约束容器内移到外面，不受阅读宽度（narrow/normal/wide）切换影响。

**console.log 清理** — App.tsx 两处残留移除。

### 为什么

Cancel bug 是真实生效的——Map key 存 `doc123__generate`，查 `doc123`，永远匹配不上。多任务重构带来的 regression。

导入 AbortError 漏查和 SSE 重复解析是代码质量债——之前 6 个函数各自 copy 一份几乎一样的 SSE 解析逻辑。

### 影响范围

| 文件 | 变更 |
|------|------|
| `api/taskManager.ts` | registerAbort 归一化 key，导入独立 Map |
| `api/client.ts` | fetchSSE/fetchFileSSE +AbortError 检查，4 函数改用 parseSSEStream |
| `api/sse.ts` | **新建** — 公共 SSE 流解析器 |
| `types/react-syntax-highlighter.d.ts` | **新建** — 替代废弃 @types 包 |
| `PreviewArea.tsx` | VideoBanner 移出约束容器 |
| `App.tsx` | console.log 清理 |
| `generate.py` | 导入 disconnect_watcher 标注 |

---

## 2026-07-31 — 多任务并发重构

### 做了什么

将生成/转录/图片处理/导入 4 种异步操作从全局单槽改为按文档+任务粒度隔离：

**统一状态来源** — 所有操作状态从 `processingTasks`（per-doc per-kind）推导，移除 7 个全局单槽：`generating`、`isStreaming`、`streamingContent`、`retranscribing`（useState）、`imageProcessing`（useState）、`importing`（useState）+ 3 个 GenPanel ref 缓冲区。

**双层流式缓冲** — 新建 `store/streamBuffers.ts`：模块级 Map（高速写入，无 React re-render）+ Store `streamTick`（120ms 节流驱动 PreviewArea 重读）。替代之前 ref 缓冲区的方案。

**操作互斥 per-doc** — `lockOtherOps` 从全局改为只看当前文档。Doc A 转录中不再阻止 Doc B 生成。

**并发上限** — 生成最多 2 个同时、导入最多 3 个同时，超限 Toast 提示。确保 LLM API 限流 + 文件 I/O 可控。

**逐任务取消** — 新增 `cancelOneTask` store action。TaskPanel 取消 Doc A 的生成不影响 Doc B。

**移除续写功能** — LLM 续写没有上文上下文，容易重复/风格断裂。模型输出长度已足够（4K-128K）。保留取消时 partial 保存，PartialBanner 简化为纯信息展示。

**切换文档不丢流式内容** — 切回正在生成的文档时显示已累积 buffer + 继续实时追加，而非空白加载。

**后端 `has_active_task`** — `GET /api/markdown/{id}` 在文档有活跃生成任务时返回 `has_active_task: true`，前端据此 3s 轮询直到完成（刷新恢复）。

**移除 PreviewArea 欢迎页导入栏** — 拖放区 + URL 输入框与 GenPanel ImportSection 功能重复，违反"唯一导入入口"原则。空状态回归简洁引导。

### 为什么

之前每个异步操作各有一套管状态的方法（store 字段、组件 useState、闭包 ref），代码里散布着 `setGenerating(true/false)`、`setRetranscribing(true/false)` 等调用。切换文档时状态管理完全靠 `isSameDoc(capturedId)` 检查，稍有不慎就串数据。扩展新产物类型需要加新变量。

现在所有操作状态统一从 `processingTasks` 推导——它是一个 key 为 `${docId}__${kind}` 的字典，天然支持多文档多任务。新增操作类型只需 `setProcessing(key, {kind})` 一行。

### 影响范围

| 文件 | 变更 |
|------|------|
| `store/index.ts` | +`streamTick`/`bumpStreamTick`/`cancelOneTask`，-`streamingContent`/`isStreaming`/setters |
| `store/streamBuffers.ts` | **新建** — 模块级 Map + 节流 flush |
| `GenPanel.tsx` | -4 个 useState，-3 个 ref，+processingTasks 推导，+并发限制 |
| `PreviewArea.tsx` | displayContent 改用 streamBuffer + tick，移除欢迎页拖放区 |
| `PartialBanner.tsx` | 移除续写按钮 |
| `App.tsx` | `generating` → `processingTasks` |
| `generate.py` | +`_active_generations` 追踪，`get_markdown` 返回 `has_active_task` |
| `store.test.ts` | +多任务并发/取消/tick 测试 |
| `streamBuffers.test.ts` | **新建** — 21 个缓冲区单元测试 |

---

## 2026-07-31 — 6 项体验优化

### 做了什么

基于 [03-质感主题系统设计方案](../pi-docs/03-质感主题系统设计方案.md)，实施 6 项 UX 改进：

**P0 空状态拖放区** — PreviewArea 欢迎页从静态文字改为交互式拖放区 + 链接输入框。新用户第一眼就能"丢东西进来"，而非阅读说明书。复用 GenPanel ImportSection 的拖放逻辑，直接调用 ingest API。

**P2 默认 Tab 按文档类型** — PDF/文档切换后默认打开「整理版原文」，视频默认「转录」，图片默认「原图」，Excel 默认「数据预览」，网页默认「笔记」。包含「已有生成内容」和「尚无内容」两种场景。

**P3 暗色模式极淡纹理** — 之前暗色模式完全清除纹理（`background-image: none !important`），现改用 `::after` 遮罩方案，让纹理以 2-3% 透明度透过，恢复暗色下的纸张质感。

**首次启动向导（三屏）** — 新建 `OnboardingWizard`，替代旧 `SetupWizard`：
- 屏 1：阅读氛围选择 — 展示经典系列 4 张预设卡片（书香/简净/复古/青竹），点击即实时预览背景
- 屏 2：系统组件检测 — 复用旧 SetupWizard 的 ffmpeg/SearXNG 检测逻辑

**转录完成提示** — 非图片导入完成后 Toast 明确提示"点击生成按钮生成笔记"，解决用户不知道下一步做什么的问题。

### 为什么

旧 SetupWizard 只检测系统组件，缺乏情感化的首次体验。空状态是用户对产品的第一印象，拖放区比文字说明更有引导力。Tab 默认值和转录提示消除用户的操作困惑。

### 影响范围

- `frontend/src/components/ui/OnboardingWizard.tsx` — 新建（225 行）
- `frontend/src/components/preview/PreviewArea.tsx` — 拖放区 + 默认 Tab
- `frontend/src/lib/texture.css` — 暗色纹理 ::after 方案
- `frontend/src/components/gen-panel/GenPanel.tsx` — Toast 提示优化
- `frontend/src/App.tsx` — SetupWizard → OnboardingWizard

---

## 2026-07-31 — 公式渲染 + 导出管线全面修复

### 做了什么

**A. 公式 `\[...\]`/`\(...\)` 分隔符支持**

**问题**：导入学术论文（arXiv 等）后，公式在预览中不渲染，显示为原始 LaTeX 代码。

**根因**：`remark-math` 只识别 `$`/`$$` 分隔符，不识别 LaTeX 标准的 `\(...\)`（行内）和 `\[...\]`（块级）分隔符。学术论文普遍使用后者。

**修复 — 双管齐下**：

1. **前端预处理兜底**（治标）— `markdown-plugins.ts` 新增 `normalizeLatexDelimiters()` 函数，在 Markdown 解析前将 `\[...\]` → `$$...$$`、`\(...\)` → `$...$`。保护代码块内的内容不被修改。6 个 ReactMarkdown 渲染入口全部接入。新增 11 个测试用例。

2. **Prompt 约束**（治本）— Notes 的 `_NOTES_TASK` 之前只说"LaTeX 语法形式"，未指定分隔符格式。现明确要求使用 `$...$`/`$$...$$`，禁止 `\(...\)`/`\[...\]`。其他 prompt（Polished、文档、Excel 等）早有此约束，仅这一处遗漏。

3. **后端导出管线补充**（遗漏修复）— `render_math.js`、`_protect_latex`、`_render_formulas_katex`、`_render_formulas_to_images`、`_build_mindmap_html` 共 5 处原先也只识别 `$$`/`$`，现已全部接入归一化预处理。

**B. 导出 HTML 渲染对齐前端预览**

排查前端预览与后端导出 HTML 的渲染差异，修复 3 项不一致：

1. **代码高亮配色** — Pygments style: `default` → `one-dark`（暗色程序员风格）。代码块背景 `#f6f8fa`（冷灰）→ `#282C34`（one-dark 暗色）。4 套 HTML 主题 `pre`/`code` 背景同步更新。

2. **GFM 任务列表** — `- [ ]` → `<input type="checkbox" disabled>`（替代 Unicode ☐），`- [x]` → `<input type="checkbox" checked disabled>`（替代 Unicode ☑）。视觉对齐前端 remark-gfm 的真实 checkbox。

3. **代码块统一暗色** — 无论导出主题明暗，代码块始终保持暗色底色，匹配程序员阅读习惯。

### 为什么

前端预处理解决非 LLM 内容的渲染（粘贴文本、网页原始内容），Prompt 约束确保 LLM 产出天生正确，后端管线补漏覆盖导出场景。三者互补，形成完整闭环。

导出对齐让用户"看到什么导出什么"，消除信任摩擦。

### 影响范围

- `frontend/src/lib/markdown-plugins.ts` — 新增 `normalizeLatexDelimiters`（~40行）
- `frontend/src/components/preview/` — 5 文件，各 1 行调用
- `frontend/src/components/ai-chat/ChatCore.tsx` — 1 处调用
- `frontend/src/test/normalize-latex.test.ts` — 新增 11 个测试用例
- `backend/app/generation/prompt.py` — `_NOTES_TASK` 1 行
- `backend/app/routers/export.py` — `_normalize_latex_delimiters` + 4 处接入 + Pygments 配色 + GFM 任务列表 + 4 主题代码背景
- `backend/scripts/render_math.js` — 新增 `normalizeLatexDelimiters` + 调用

---

## 2026-07-30 — 深度评估后续改进（四合一）

### 做了什么

基于 [04-深度评估报告](../pi-docs/04-深度评估报告.md) 的 4 个剩余改进项，一次性完成：

**1. doc.py 拆分为独立文件** — `backend/app/engines/doc.py`（756 行）拆为 5 个文件：`doc_types.py`（共享 DocResult）+ `doc_markitdown.py` + `doc_docx.py` + `doc_pptx.py` + `doc_excel.py`。3 个未注册死代码引擎（PyMuPDFEngine/PyMuPDF4LLMEngine/PDFOCREngine）随文件删除。`docling.py` 和 `__init__.py` 的 import 路径同步更新。

**2. Zustand Store 领域分节** — `frontend/src/store/index.ts` 用分节注释（`════ 域名称 ════`）清晰划分 11 个逻辑域，保持单一 `create()` 调用的零类型错误优势。接口完全不变，所有 `useStore(s => s.field)` 选择器兼容。

**3. 前端测试基础设施** — 从零搭建：vitest + @testing-library/react + jsdom。3 个测试文件，32 个测试用例（store 19 + theme 9 + api 4）。`npm test` / `npm run test:watch` 脚本。

**4. 引擎配置 UX 优化** — EngineSelect 加"当前引擎"标签，ProviderSettings 加"恢复默认"按钮。附带修复 `EngineBindData` Pydantic 类型 bug（`int|str|None` → `int|str|list|None`，后台代码已有 `isinstance(val, list)` 处理但类型拒绝）。

### 为什么

报告指出这 4 项是不影响主线使用的结构性优化，但修完让项目更完整。实际耗时约 1.2 小时。

### 影响范围

- `backend/app/engines/` — 5 新建 + 2 修改 + 1 删除
- `frontend/src/store/index.ts` — 重构为 11 域分节
- `frontend/src/test/` — 4 新建（vitest.config + setup + 3 tests）
- `frontend/package.json` — test scripts
- `frontend/src/components/gen-panel/EngineSelect.tsx` — "当前引擎"标签
- `frontend/src/components/settings/ProviderSettings.tsx` — "恢复默认"按钮
- `backend/app/routers/model.py` — Pydantic 类型修复
- `frontend/src/api/client.ts` — saveEngineBindings 类型同步

### 验证

- 后端: `from app.engines import init_engines` + `test_engines.py` 全部通过
- 前端: `tsc -b --noEmit` 零错误 + `vitest run` 32/32 通过

---

## 2026-07-29 — 工程质量系统性提升（disconnect_watcher + Depends + 审核分片 + 导出进度）

### 做了什么

**Part 1: disconnect_watcher 公共工具** — 将 5 个路由中 ~45 行重复的 `_watch_disconnect` 实现提取为 `@asynccontextmanager`。同时修复 2 个 bug：`process.py` 资源泄漏（disconnect_task 从不 cancel）和 `chat.py` 缺 await（Task destroyed but pending）。

**Part 2: FastAPI Depends 全量迁移** — 8 个路由模块全部从 `request.app.state.xxx` 迁移到 `Depends(get_store/config/guard/config_lock)`。共修复 5 个文件缺失 `Depends` 导入的错误。

**Part 3: AI 审核长文档分片** — `split_into_chunks()` 按句子边界分片 + `asyncio.gather` 并行 LLM 提取 + `SequenceMatcher` 去重（>0.85 相似度），最多32块。

**Part 4: 导出进度反馈** — `POST /export/custom/stream` SSE 流式进度 + `GET /export/download/{temp_id}` 临时下载，复用 ingest.py 已验证的 queue + background task 模式。

### 为什么

对话中明确：工程质量全部做完，审核分片+导出进度作为功能增强。前两项消除重复代码并统一路由风格，后两项填补功能空白。

### 影响范围

- `backend/app/core/disconnect.py` — 新建（公共工具）
- `backend/app/routers/generate.py, ingest.py, chat.py, process.py, fact_check.py` — disconnect_watcher 替换
- `backend/app/routers/export.py, chat.py, process.py, fact_check.py, config.py, ingest.py, generate.py, model.py` — Depends 迁移
- `backend/app/core/text_utils.py` — `split_into_chunks()`
- `backend/app/generation/review.py` — `static_review()` 分片化
- `backend/app/routers/export.py` — 2 新端点
- `frontend/src/api/client.ts` — `streamExportProgress()`
- `frontend/src/components/ui/ExportPanel.tsx` — 进度 UI

### 验证

- `grep -r "request\.app\.state" backend/app/routers/` → generate.py 仅 7 处无害闭包残留
- `grep -r "return {\"error\"" backend/app/routers/` → 0 处旧格式
- 6 个后端测试全部通过
- Tauri + PyInstaller 打包成功（MSI 569MB / NSIS 404MB）

---

## 2026-07-26 — 多任务状态泄漏修复 + 刷新内容恢复

### 问题

两个打包端 bug，根因相同——SSE 回调中的 `isSameDoc()` 守卫拦住了任务清理逻辑。

**Bug 1 — 切换文档后产物预览永远加载中**：用户在文档 A 点生成 → 切换到文档 B → 文档 A 的 `onDone` 被 `isSameDoc` 拦截 → `setProcessing(key, null)` 永不调用 → 任务条目泄漏 → 切回 A 时 `isViewProcessing` 返回 true → 永远显示加载中。

**Bug 2 — 刷新后内容不同步**：`processingTasks` 纯内存，刷新后 = `{}`。App.tsx `init()` 只恢复文档列表，不预加载已有内容 → 用户看到空预览区。

### 修复

**任务清理脱离 isSameDoc 守卫**（`GenPanel.tsx` + `PreviewArea.tsx`）：
- 原则：`isSameDoc` 仅保护内容更新（setContent），不保护生命周期清理（setProcessing null）
- `handleGenerate` onDone/onError/onCancelled、`handleRetranscribe` finally、`handleProcessImage` finally、`handleFactCheck` onDone/onError — 7 处 `setProcessing(key, null)` 移到守卫前
- 全局 flag（`generating`/`isStreaming`）仅在 `processingTasks` 中无其他文档生成任务时才复位，防并发场景误关

**刷新后预加载内容**（`App.tsx`）：
- `init()` 恢复文档后，主动调 `getTranscript()` + `getMarkdown()` 预加载 rawText/notes/polished → 已有的生成结果立即可见

**向前兼容加固**（`PreviewArea.tsx`）：
- `activeTask`（单值 `find()`）→ `activeTasks`（数组 `filter()`）
- `isViewProcessing` 改用 `some()` — 未来同文档多任务并发时不漏检

### 影响范围
- `frontend/src/components/gen-panel/GenPanel.tsx`（+27/-7 行，7 处守卫调整）
- `frontend/src/components/preview/PreviewArea.tsx`（+47/-16 行，数组重构 + 守卫调整）
- `frontend/src/App.tsx`（+26/-1 行，预加载逻辑）

### 设计决策

- **清理与内容更新解耦** — 清理是文档级操作（key 含 docId），内容更新是视图级操作（需 isSameDoc）。两者不应共用同一个守卫。
- **全局 flag 防御式复位** — 检查 `processingTasks` 中所有文档而非仅当前文档，确保未来并发场景不会出现"一个完成→全部复位"的 bug。
- **不持久化 processingTasks** — 任务状态是 SSE 连接绑定的临时数据，持久化后会引入僵尸条目恢复问题。替代方案是主动从服务端加载已保存的内容。

---

## 2026-07-26 — DashScope 模型体系加固

### 问题

1. **第三方模型混入**：DashScope API `/v1/models` 返回阿里云平台上托管的所有模型（含 DeepSeek/Kimi/GLM 等第三方），刷新模型列表时混在千问模型中间，用户困惑。
2. **能力检测依赖命名规则**：`_PROVIDER_IMAGE_RULES` 用字符串前缀猜测模型能力（如 `qwen3.6` → image），但同系列下有纯文本也有多模态（qwen3.6-flash 实为纯文本），规则不可靠。
3. **低成本模型缺失**：`qwen-flash`（¥0.15/M 最便宜文本）、`qwen3.5-flash`（¥0.2/M 视觉 Flash）、`qwen3.5-ocr`（卡证票据 OCR）未在预设中。
4. **模型描述冗长**：含价格、上下文等信息，用户真正需要的是快速判断"能不能处理图片"。

### 解决方案

**能力标注策略**：`预设声明（确定可靠） > 用户手动标记 > 自动检测规则（仅明确视觉前缀）`

### 改动

**模型列表净化**（`routers/model.py`）：
- `refresh_models`：DashScope API 结果按 `owned_by` + ID 前缀双重过滤，排除 `deepseek-`/`kimi-`/`glm-`/`minimax-` 等第三方模型
- 新增 `_lookup_preset_capabilities()`：`add_model` 时优先从 `llm_models` 预设查能力标签，预设无此模型才回退到自动检测
- `_PROVIDER_IMAGE_RULES` dashscope 收紧：去掉模糊的 `qwen3.7-plus`/`qwen3.6`/`qwen3.5`，仅保留 `qwen-vl`/`qwen-omni`/`qvq`/`qwen3-vl` 四种明确视觉前缀

**模型预设加固**（`core/config.py`）：
- `llm_models.dashscope` 全部 13 个模型内置 `capabilities` 字段 — 5 个 `["text"]`，8 个 `["text", "image"]`
- 新增 3 个低成本模型：`qwen-flash`、`qwen3.5-flash`、`qwen3.5-ocr`
- 描述统一为 `能力类型 · 一句话定位`（去掉价格/上下文等冗余信息）
- `engine_providers.dashscope.builtin_models` 新增 3 个 OCR 引擎模型（`qwen-vl-ocr-latest`/`qwen3-vl-flash`/`qwen3.5-ocr`）

### 影响范围
- `backend/app/core/config.py`（DashScope 预设段重写，+3 模型，+13 capabilities）
- `backend/app/routers/model.py`（+50 行：过滤逻辑 + 预设查表 + 规则收紧）

### 设计决策

- **预设覆盖已知，规则兜底未知**：不会出现"预设遗漏 → 自动检测误判"的夹缝。已知模型的能力是人工声明的事实，不依赖命名规则猜测。
- **新模型交给用户判断**：API 拉取到不在预设中的模型时，仅名称含 `qwen-vl`/`qwen-omni`/`qvq`/`qwen3-vl` 的自动标视觉，其他默认纯文本。用户可在设置页手动调整。

---

## 2026-07-26 — OCR 预处理管线 + 千问模型集成

### OCR 预处理管线

适用于 PDF/Word/PPT/EPUB 等所有文档类型。在生成笔记前，先用廉价 OCR 模型（如 qwen-vl-ocr-latest ¥0.3/M）提取图片中的文字，塞回原文，再交给文本 LLM 生成。无需多模态模型也能"看到"图片内容，成本降低 5-10 倍。

**后端**：
- `ocr_preprocessor.py`（新建）：正则匹配 `/api/files/{tx_id}/img_NNNN.png` 图片引用 → 读文件 base64 → `LLMService.call(prompt, image_urls=[...])` → OCR 文字插入原文
- `Generator.generate/generate_both`：在 `ContentPreparer.prepare()` 之前执行 OCR（需要原始图片路径）
- `generate.py`：接收 `ocr_enabled` / `ocr_model_id` 参数，流式和非流式端点均已接入
- `config.py`：DEFAULT_CONFIG 加 qwen-vl-ocr-latest、qwen3-vl-flash、qwen3.7-flash、qwen-plus、qwen-turbo 等模型

**前端**：
- OCR 开关 + OCR 模型下拉（仅 Document 类型显示，从 enabledModels 中筛选 vision 能力模型）
- OCR 与 imageAnalyze 互斥：开启 OCR 时隐藏 imageAnalyze
- genPrefs 持久化 ocrEnabled / ocrModelId

### 千问平台模型文档

新增 `docs/qianwen-models.md`，涵盖文本生成、OCR/视觉、ASR 三类 LiMo 适用模型，含价格、上下文、调用方式。

### 影响范围
- `backend/app/generation/ocr_preprocessor.py`（新建，111 行）
- `backend/app/generation/generator.py`（+22 行）
- `backend/app/routers/generate.py`（+27 行）
- `backend/app/core/config.py`（+15 行）
- `frontend/src/components/gen-panel/`（+41 行）
- `frontend/src/api/client.ts`（+4 行）

---

## 2026-07-25 — AI 审核前端显示简化 + 参考来源后处理注入

### 问题

AI 审核（fact_check）前端用复杂状态机（4 种状态 A/B/C/D）+ 阶段指示器 + 仪表盘 + AI 评分，和其他产物（notes/polished）的流式 Markdown 体验完全不同。

更关键的是：AI 评分（"可信度 7.5/10"）让用户误以为这是客观裁定——实际上是用 AI 来审核 AI 生成的内容，评分本身可能不准确。我们只应该做能做到的（静态分析 + 联网验证），不做能力边界之外的事（给内容打分）。

### 改动

**后端** — 参考来源后处理注入（`review.py`）：
- 新增 `_build_references_section()`，从 `review_results[].evidence_items` 中提取所有证据 URL
- `generate_report()` 和 `generate_report_streaming()` 在 LLM 报告后程序化追加「参考来源」section
- 不依赖 LLM 是否按格式输出 URL，由系统保证

**前端** — fact_check tab 简化为纯 Markdown 流式展示（`PreviewArea.tsx`）：
- 从 ~280 行状态机（阶段指示器 + 仪表盘 + 4 种状态）→ ~35 行三态（Markdown / 加载 / 引导）
- `onProgress` 阶段结果以 Markdown blockquote 拼接到内容区，和生成笔记体验一致
- `onChunk` 处理所有 phase 的 text（修复 verify 阶段 text 被吞的 bug）
- 去掉 AI 评分/仪表盘/阶段指示器，能力边界在引导页用纯文本说明
- 删除未使用图标导入 `ClipboardList`、`Search`

**Store** — `factCheckMeta` 移除 `overall_score`、`trustworthiness`、`trustworthiness_label`、`deductions` 字段

### 影响范围
- `backend/app/generation/review.py`（+39 行）
- `frontend/src/components/preview/PreviewArea.tsx`（-161/+122 行）
- `frontend/src/store/index.ts`（类型精简）

---

## 2026-07-16 — AI 对话架构重构（参照 Pi）+ 提示词分类型重写 + 公式修复

### AI 对话架构重构

参照 Pi 的分层架构重构 `chat.py`（286 行全耦合 → 5 个独立模块）：

| 模块 | 职责 |
|------|------|
| `agent/prompts/` | 分层 System Prompt：`base.md`（角色）+ `rules.md`（硬约束）+ 动态文档注入 |
| `agent/tool_registry.py` | 工具注册中心：`ToolDef` 标准化，`ToolRegistry.apply_to()` 替代硬编码注册 |
| `agent/session.py` | 对话会话管理：`SessionManager` 封装 CRUD + 历史提取 |
| `routers/chat.py` | HTTP 调度（~220 行），只做组装 |

AgentRunner 核心循环不动，前端 API 零改动。加新工具只需创建 ToolDef 并 register。

### 前端格式指令跨类型泄露修复

`GenPanel.formatStr` 未按 sourceType 过滤 → 用户在 PDF 勾选"公式集"后切到视频，`formatFormulas=true` 仍发送 → 后端给视频 prompt 加入 `get_formulas_format()` 指令。修复：`TypeMeta` 新增 `availableFormats`（来自 API 的 `genOptions`），`formatStr` 按当前类型可用格式过滤。

同时修正视频产物公式策略：`formulas` 格式从 video AVAILABLE_OPTIONS 移除（不再强制"公式汇总"章节），改为在 `_POLISH_TASK` 中加轻量提示（"如有公式用 LaTeX，没有则忽略"）。网页 Polish task 同步补上。

### Polish 提示词按文件类型独立化

`build_polish_prompt` 原先只区分「文档 / 非文档」，webpage/text/image 被错误分到视频转录 prompt。新增 3 个独立 Polish prompt：

| 类型 | 新 prompt | 核心指令 |
|------|-----------|---------|
| webpage | `_WEB_POLISH` | 清导航/侧边栏残留、规范化标题层级、保留图片/表格/引用 |
| text | `_TEXT_POLISH` | 保留原文风格、适度格式化、修正错别字 |
| image | `_IMAGE_POLISH` | 修正 OCR 同音字/形近字、重组段落、重建表格、公式转 LaTeX |

video/audio 保持原有转录整理 prompt，doc 保持文档整理 prompt。

### Notes 提示词同步独立化

`build_notes_prompt` 原先也是 binary dispatch，webpage/text/image 共用视频 NOTES prompt（含"填充词"等视频专属指令）。新增 3 个独立 NOTES prompt，分发逻辑对齐 Polish。

### 版本显示修复

两种竞态导致版本列表错乱：
- 流式 onDone / 取消续写：notes 版本用 `setMarkdownVersions` 直接替换，若返回晚于 polished 则抹掉后者
- 重新转录：只取 notes.versions，polished 版本完全丢弃

修复：按 mode 过滤合并而非替换。同时版本切换器按当前 tab 过滤——polished tab 只显示 polished 版本，notes tab 只显示 notes 版本，互不污染。

---

## 2026-07-25 — 文档更新 + 打包

- 更新 CLAUDE.md：待办事项标记"报告可交互"和"旧格式错误迁移"为已完成，汇总 62/68→63/68（91%→93%）
- 更新 STATUS.md：事实验证 4/5→5/5，合计 59/63→60/63（94%→95%）
- 同步更新 pi-docs/01-C端项目分析.md 进度
- PyInstaller + Tauri 打包

---

## 2026-07-25 — 路由错误体系统一迁移

将 config/model/files 路由中 15 处旧格式 `return {"error":"..."}` 全部迁移为 `raise AppError("CODE", "...")`，完成错误体系的统一。

| 路由 | 旧格式数 | 新旧 |
|------|---------|------|
| config.py | 7 | `DETECT_NO_API_KEY` / `DETECT_NO_BASE_URL` / `DETECT_BAD_SCHEME` / `DETECT_BAD_URL` / `DETECT_REQUEST_FAILED` / `DETECT_FAILED` |
| files.py | 3 | `COVER_BAD_ID` / `COVER_NOT_FOUND` |
| model.py | 5 | `PROVIDER_NOT_FOUND` / `PROVIDER_BUILTIN` / `MODEL_NOT_FOUND` |

影响：前端 `apiFetch` 的统一错误拦截现在可以正确捕获这些端点的错误。

---

## 2026-07-25 — AI 审核报告来源链接后处理注入

### 问题

LLM 在生成审核报告时经常不写出可点击的 `[标题](URL)` 格式链接——prompt 已明确要求，但 LLM 执行不稳定。用户在 fact_check tab 看到"来源：相关网页"却无法点击查看原文。

### 修复

在 `review.py` 中新增 `_build_references_section()` 方法，**程序化地**从 `review_results[].evidence_items` 中提取所有证据 URL，按域名去重后在报告末尾追加「📎 参考来源」section。

- `generate_report()`：LLM 生成完成后拼接参考来源 section
- `generate_report_streaming()`：LLM 流结束后续传参考来源 section（一个 chunk）

这样 URL 的呈现不依赖 LLM 是否听话，由系统保证。

修改文件：[`backend/app/generation/review.py`](../backend/app/generation/review.py)（+35 行）

---

## 2026-07-16（早前）— 配置防丢失 + 截图修复 + 内容宽度自适应 + UI 优化

从 MyBigWork 移植。

### 配置防丢失（防御性加固）

- `save_config` 拒绝写入 `llm=None` 的损坏配置
- `save_config` 自动修复 None 列表字段为 `[]`
- `load_config` 启动时创建 `config.yaml.bak` 备份

### 原片截图修复

- `_POLISH_FORMAT_FNS` 缺 `screenshot`/`link` → polished 产物不生成截图
- `_post_process_screenshots` 不传 `store_root` → 截图写入错误路径
- 非流式端点缺截图视频下载 + `want_polished` 漏传 `formats`

### 内容宽度自适应

`ResizeObserver` + 百分比三档（80%/90%/97%），上限 1100px。`Ctrl+W` 循环切换，字体联动缩放。

### UI 优化

- 前端 emoji → lucide 图标全量替换
- 预览栏响应式间距 + 固定滚动条

---

## 2026-07-16 — 配置防丢失 + 截图修复 + 版本显示修复 + 内容宽度自适应 + UI 优化

### 配置防丢失（防御性加固）

排查 API Key / 配置偶发丢失问题。未发现确定性 bug，但识别出架构脆弱性：`save_config` 无条件写盘，若内存中 config 因任何原因缺失关键字段则会被固化。三道防御：

- `save_config` 拒绝写入 `llm=None` 的损坏配置
- `save_config` 自动修复 `llm_presets`/`engine_providers` 等 None 列表字段为 `[]`
- `load_config` 启动时创建 `config.yaml.bak` 备份

### 原片截图修复（4 处）

视频笔记「原片截图」开启后标记不替换为图片的根因分析 & 修复：

| 问题 | 修复 |
|------|------|
| `_POLISH_FORMAT_FNS` 缺 `screenshot`/`link` → polished 产物不生成截图 | 加入格式白名单 |
| `generator.py` `_post_process_screenshots` 不传 `store_root` → 截图写入 CWD 而非 data_dir | 传入 `self._prep.store_root` |
| 非流式端点缺截图视频下载 → `video_path=None` 导致后处理跳过 | 补下载逻辑 + 后处理 |
| 非流式端点 `want_polished` 漏传 `formats=req.formats` | 补传 |

### 版本显示修复

`loadVersion` 对 notes 模式传空字符串 `''`，后端不做 mode 过滤，版本列表混入 fact_check/polished 条目。改为传 `'notes'`。

### 内容宽度自适应切换

预览栏工具栏新增宽度切换按钮（`Ctrl+W`），三档基于面板实际宽度的百分比（ResizeObserver 实时追踪）：紧凑 80% / 标准 90% / 宽屏 97%，上限 1100px。切换时宽度+字体+padding 平滑过渡 350ms。

### 预览栏 UI 优化

- 外层 wrapper `overflow-y-scroll` 固定滚动条位置
- 响应式水平间距：`px-4 md:px-12 lg:px-20 xl:px-32`（紧凑/标准），宽屏特小间距

---

## 2026-07-15（深夜·二）— 前端 emoji → lucide 图标全量替换

### 改动

UI 中所有 emoji 替换为 lucide-react 开源图标，保持语义一致、风格统一。

| 位置 | 原 emoji | 替换图标 | 语义 |
|------|---------|---------|------|
| PreviewArea 错误/警告 | ⚠️ | `AlertTriangle` | 警告/注意 |
| PreviewArea modeMeta fallback | 📄 | `FileText` | 文档 |
| PreviewArea 静态审核标题 | 📋 | `ClipboardList` | 审核清单 |
| PreviewArea 验证中标题 | 🔍 | `Search` | 搜索/查找 |
| ExportPanel 类型分组标题 | 📄(fallback) | `TYPE_ICON` 映射 | 各类型对应图标 |
| ExportPanel 预览行前缀检测 | 📹🌐📄📝🖼📊📚 | `TYPE_LABELS` 中文检测 | 死代码修复 |
| GenPanel Toast 前缀 | ⚠️ | 移除 | Toast 组件已有图标 |

### 影响范围

- `PreviewArea.tsx` — 新增 FileText/ClipboardList/Search 导入
- `ExportPanel.tsx` — 新增 TYPE_ICON 映射 + Film/PenLine/Image/BarChart3/BookOpen 导入
- `GenPanel.tsx` — 移除 Toast ⚠️ 前缀
- 纯 UI 层改动，不影响 API/后端/打包

---

## 2026-07-15（深夜）— 安全漏洞修复 + 视频阻塞修复 + verify_mode

### 安全漏洞扫描与修复（6 项）

- **HIGH — `get_url_metadata` SSRF**：新增工具函数未调用 `validate_url()`，攻击者可探测内网。已补校验
- **MEDIUM — `search_official` 域名注入**：LLM 生成的域名参数未清理，可注入搜索操作符。正则清理 `[^a-zA-Z0-9.\-]`
- **MEDIUM — `run_streaming` 任务泄漏**：取消时后台 `asyncio.create_task` 未 cancel，持续消耗资源。`finally` 块 cancel+gather
- **MEDIUM — evidence.py URL 去重竞态**：并发 check-then-add 无锁保护。`threading.Lock` 保护 + 定期 GC 过期缓存
- **MEDIUM — `CancelledError` 未被捕获**：`_verify_item` 只 catch `Exception`，`CancelledError` 导致 result_queue 永久阻塞
- **LOW — SearXNG Popen 缺 CREATE_NO_WINDOW**：打包后启动 SearXNG 弹 CMD 黑框

### 视频导入阻塞修复

视频 `_download`（yt-dlp）/ `_extract_audio`（ffmpeg）/ 抖音 `dy_download` 四个同步操作未包装 `asyncio.to_thread`，下载期间阻塞整个事件循环。已全部包装。

### verify_mode 参数

AI Review 路由新增 `verify_mode` 参数（`llm` 默认 / `web` 联网），后端透传到 ReviewEngine。

---

## 2026-07-15（晚间）— P0-1~P0-3 Prompt 体系重构 + 语言策略统一 + Agent 体验

### P0-1: Polish/Notes 风格边界重设计 + Prompt XML 模块化

**问题**：`STYLE_MAP` 被 Polish 和 Notes 共用，Polish + business → "保持原文" vs "结论先行" 矛盾。

**修复**：
- 风格体系解耦：`COMMON_NOTE_STYLES` → `VIDEO_NOTE_STYLES` / `DOC_NOTE_STYLES`（`prompts/notes_styles.py`）
- Polish 固定忠实整理策略（无 `<style_preference>` 节），格式白名单 `_POLISH_FORMAT_FNS`
- Prompt 从扁平拼接改为 XML 分节：`<role>` → `<task>` → `<source_content>` → `<instruction_priority>` → `<hard_constraints>` → `<style_preference>` → `<output_format>` → `<user_request>`
- `PromptComposer` 分节组装器（`prompts/common.py`）
- `meeting_minutes` → `meeting` alias（`normalize_style()` 前后端兼容）
- 前端：polished only 模式隐藏风格选择器，both 模式标注"风格仅影响笔记"

**影响**：`prompt.py` 重写（986→650 行），`prompt_builder.py` / 前端 `GenerateOptions.tsx` / `GenPanel.tsx` / `genPrefs.ts` / `constants.ts` 适配。

### P0-2: 统一语言策略 — 输出语言 = target_language

**问题**：4 个 role 硬编码矛盾语言策略（视频强制中文、文档跟随原文、Excel 强制中文）。

**修复**：
- 所有 role 瘦身为纯角色定义（各 1 行），语言要求提取为动态 `<output_language>` 节
- `_build_output_language(target_language, source_type)` — 术语保留行业通用写法 + 不强制翻译
- Excel 独立规则：字段名保留原文，分析文字用目标语言
- `normalize_target_language()` — zh→zh-CN 等 5 个 alias
- `_INSTRUCTION_PRIORITY` 扩展为四层：hard_constraints > output_language > user_request > style_preference
- 删除 `translate` 格式选项（全链路清理：后端 5 处 + 前端 2 处，genPrefs 保留 deprecated 字段）
- 删除 `get_translate_format()` 函数
- `_build_hard_constraints()` 移除 `target_language` 参数

**影响**：`prompt.py` / `prompt_builder.py` / 前端 `GenerateOptions.tsx` / `GenPanel.tsx` / `genPrefs.ts`。

### P0-3: Agent 工具调用可见性 + 文档注入智能截断

**问题**：Agent 工具判定回合无任何反馈，用户干等；`rawText[:1000]` 硬截断可能切在句子中间。

**修复**：
- 新增 `agent_status` SSE 事件（工具判定前推送"正在分析问题..."）
- `tool_start` 增加 `display` 字段（后端 `_tool_display()` 生成安全文案，C 端不暴露路径）
- `tool_result` 改为 `summary` 摘要（仅计数/长度，完整结果只传 LLM 不出现在前端 UI）
- 新增 `smart_truncate()` — 按句子边界截断（`app/core/text_utils.py`），优先级：`。` > `. ` > `\n\n` > `\n`
- 前端 `streamChat()` 增加 `onStatus` / `onToolResult` 回调，`ChatCore.tsx` 适配新事件流
- spec hiddenimports 防御性添加 `app.core.text_utils`

**影响**：`agent.py` / `chat.py` / `text_utils.py`（新增）/ `client.ts` / `ChatCore.tsx` / `LiMoBackend.spec`。

---

## 2026-07-15（下午）— mode 过滤修复 + 打包兼容

### mode 精确匹配修复

`GET /markdown/{id}` 之前用 `(mode == "polished") == want_polished` 布尔技巧筛选，导致 `notes` 和 `fact_check` 两个模式被混在"非 polished"条目里一起返回。审核报告保存为 `fact_check` 模式后，前端加载笔记版本列表时也会把审核报告版本混进去展示。

**修复 3 处**：
- `generate.py:664` — `GET /markdown/{id}` 改为 `mode == mode` 精确匹配
- `generate.py:325` — 生成续写 partial 匹配改为 `mode == stream_mode` 精确匹配
- `export.py:1060` — summary tab 导出改为 `mode == "notes"` 精确匹配

### 打包兼容修复

- `review.py` 函数内 `from app.agent.evidence import format_evidence` 提升到模块顶部（PyInstaller 不追踪函数内导入）
- `LiMoBackend.spec` 新增 `app.generation.review` / `app.agent.evidence` 到 hiddenimports
- `LiMoBackend.spec` 新增 `duckduckgo_search` 到 collect_all（依赖 primp Rust 原生库）

### 默认搜索方案切换

`agent_tools.search.provider` 默认值从 `searxng` 改为 `duckduckgo`。DDG Instant Answer API 替换为 `duckduckgo_search` 库（真实网页搜索）。新用户无需配置即可使用搜索验证。

---

## 2026-07-15 — 事实验证重构为 AI Review（AI 报告审核系统）

### 做了什么

将原有 Search-First 的 FactCheckEngine 重构为四阶段 AI Review Engine：

**① Static Review（不联网）**: 提取可核查断言（Fact/Data/Opinion），检查内部一致性问题（缺少引用、绝对化表述、推理跳跃、逻辑冲突），双维度风险评分（Risk + Priority）。
**② Verification Planning**: 一次 LLM 批量决定每条 claim 的验证策略（LLM 已知/需联网/无法验证/观点跳过），为需搜索的 claim 生成 Query + Intent。
**③ Evidence Collection + Review**: 按需搜索（规则过滤域名优先权威来源），多维度评估（Evidence Strength + 事实一致性 + 来源可信度 + 时效性 + 引用充分性 + 推理合理性）。
**④ AI Review Report（流式）**: 红黄绿报告（🔴 需要立即修改 / 🟡 建议修改 / 🟢 可接受）+ 每个问题带 Action + 报告可信度 + 扣分项。

### 为什么

- DDG Instant Answer API 不是真正的网页搜索（只返回 Wikipedia 摘要），大量查询返回空结果
- Search-First 流程把搜索当目的，实际上搜索只是审核的手段之一
- 逐条串行验证极慢（30-90s），批量 LLM 调用可减少 80% 时间
- 输出只是 verified/disputed 二元标签，对用户帮助有限
- 新增全局设计原则：**审核结果必须可解释（Explainable by Design）**

### 影响范围

| 文件 | 动作 | 说明 |
|------|------|------|
| `backend/app/generation/review.py` | 新建 | ReviewEngine 四阶段管道 |
| `backend/app/generation/prompt.py` | 新增 4 prompt | STATIC_REVIEW / VERIFICATION_PLANNER / EVIDENCE_REVIEWER / AI_REVIEW_REPORT |
| `backend/app/routers/fact_check.py` | 修改 | 路由切换到 ReviewEngine（URL 不变） |
| `backend/app/generation/fact_check.py` | 保留 | 旧 FactCheckEngine 不动 |
| `backend/app/agent/tools.py` | 重写 DDG + 新增工具 | DDG Instant Answer API → `duckduckgo_search` 库；新增 `search_official` / `get_url_metadata` / `today` / `is_recent` |
| `backend/app/agent/evidence.py` | 新建 | Evidence Manager：统一证据格式 + 缓存（TTL 24h）+ URL 去重 |
| `frontend/src/store/index.ts` | 扩展 | 新增 reviewIssues / reviewSummary / trustworthiness 等字段 |
| `frontend/src/api/client.ts` | 扩展 | SSE 事件解析适配新格式（static_review / verify / review） |
| `frontend/src/components/preview/PreviewArea.tsx` | 重写 | fact_check tab 改为红黄绿审核报告 + 静态审核摘要 + 可信度展示 |
| `frontend/src/components/gen-panel/constants.ts` | 文案 | "事实核查" → "AI 审核" |
| `CLAUDE.md` / `CHANGELOG.md` | 更新 | 文档同步 |

---

## 2026-07-13 — 全面修复：8 项增强 + Bcut ASR + KaTeX + 公式渲染 + 引擎语义修正 + 打包

### 本轮追加修复（同日）

- **配置 Key 重启丢失**：`init_engines` 中 ASR 绑定强制先删后插重排 → 每次启动 `asr_changed=True` → `save_config` 写全量 config.yaml。若内存 config 缺字段则永久丢失。改为仅空列表初始化，后续启动不再触发保存。新增日志标注每次保存的变更来源
- **表格滚动条**：全局 CSS 补 `height: 4px`（横向滚动条之前 0px=不可见），新增 `.md-table-wrap` 8px 可见滚动条 + hover 变色。表格改用标准 `overflow-x: auto` 包裹（对标 GitHub），不嵌套纵向滚动避免"滑到底才能横滚"的体验问题
- **Excel 分析报告无价值**：新增 `_parse_markdown_table()` 解析表格→结构化摘要（行列数+列名+首尾样品），重写 prompt 模板新增 `⚠️ 严禁事项` 禁止重复输出原始表格。51KB 原始数据→~1KB 摘要发给 LLM
- **废弃代码清理**：删除 `agent/loop.py`（整个文件 86 行废弃），清理 `store.py` 5 个 DEPRECATED 方法、`export.py` 3 块废弃注释代码、`config.py` 1 个废弃函数，合计 ~300 行
- **FastAPI Depends 依赖注入**：新增 `app/core/dependencies.py`（get_store/config/guard/config_lock），conversations/capabilities/files 三个路由已迁移，其余可逐步迁移
- **Excel 分析报告移除**：LLM 无法做真正的数值分析，报告只是重复输出表格。删除 EXCEL_REPORT_PROMPT/build_excel_report_prompt，Excel 只生成数据摘要
- **PDF 默认引擎 pymupdf4llm**：pymupdf 的 builtin_models 新增 pymupdf4llm，`doc:text+image` 默认引擎改为 pymupdf4llm（文字+图片+矢量图，效果最好）
- **API Key 输入清洗**：`clean_api_key()` 自动去除 HTML 标签包裹、首尾空格和引号，防止从网页复制 Key 时带入 HTML
- **网页提取三层回退**：requests（正确编码+UA）→ trafilatura → html2text → 正则去标签。垃圾检测 >30% 自动降级，避免 SPA 页面乱码
- **网页/文本纯文本渲染**：跳过 ReactMarkdown + KaTeX，直接用 `whitespace-pre-wrap` 渲染，避免 base64 残留被误解析为 Markdown 格式
- **产物选择同步**：切换文档类型时自动重置 selectedCore 到可用产物列表，消除 Excel 默认勾选不一致
- **右键菜单定位修复**：ContextMenu 改为 `createPortal` 渲染到 body + fadeIn 动画去 `transform`，修复菜单偏移到预览栏/屏幕外的问题
- **PyInstaller 模块遗漏**：`agent/`/`generation/`/`routers/` 三个目录补 `__init__.py`；generate.py/fact_check.py 函数内 import 提到模块顶部，PyInstaller 不再遗漏 app 模块

### Bug 修复

- **UnboundLocalError**：`process.py`/`model.py` 函数内条件导入 `EngineRegistry` 覆盖模块级导入 → 条件未命中时抛异常。AST 扫描确认全代码库无其他同类问题
- **KaTeX 字体 404**：CSS 内联后 `@font-face` 保留相对路径 → 离线 HTML 公式渲染异常。60 字体 base64 内联 + 补齐 `backend/static/katex/fonts/`
- **公式渲染破裂**：`remarkGfm` 在 `remarkMath` 之前 → `_`/`<` 被误解析 → `$$` 块破裂。交换插件顺序
- **LogPanel 始终显示"暂无日志"**：`setInterval` 闭包陈旧，`logs.length` 永远为 0。改用 `useRef`
- **表格无滚动**：`<table>` 无容器 → 宽表溢出。新增 `overflow-x: auto` 包裹 + `.md-table-wrap` 8px 可见滚动条

### 引擎与适配器

- **引擎降级语义修正**：`auto_fallback` 不再拦截扩展名兜底（新用户无绑定时必须可用），仅控制引擎失败后的重试行为
- **未知扩展名兜底**：`markitdown` → `pymupdf`
- **Bcut ASR 引擎**：B站必剪免费云转写，启动时自动绑定为 ASR 默认，优先级最高

### HTML 导出

- 标题锚点 id、时间戳 badge（Content 蓝色/Screenshot 橙色）、MD 图片路径绝对 URL 重写
- Pygments 代码高亮 + GFM 删除线/任务列表 + `nl2br` 移除

### 错误处理

- 5 端点静默失败 → Toast（App 初始化/设置保存/聊天加载）
- 引擎降级 toggle Toast、streamFactCheck `receivedDoneOrError` 守卫
- LogPanel 阈值 12s→6s、SSE queue 异常类型收窄

### 代码质量

- spec `hiddenimports` 清理 5 处无效条目 → 0 ERROR
- 3 条新后端模式（`CREATE_NO_WINDOW`、`store.root`、`_watch_disconnect`）+ 2 条（阴影导入、插件顺序、auto_fallback 语义）
- 文档全面同步（CLAUDE.md/STATUS.md/CHANGELOG.md）

### 打包

- ffmpeg.exe + ffprobe.exe（434MB）内置，MSI 514MB
- KaTeX 60 字体打包

### 影响范围

- **后端**：export.py / ingest.py / process.py / model.py / document.py / LiMoBackend.spec / engines/**init**.py / prompt.py
- **前端**：client.ts / ProviderSettings.tsx / LogPanel.tsx / GenPanel.tsx / markdown-components.tsx / markdown-plugins.ts / index.css / App.tsx / ChatCore.tsx / SettingsPage.tsx
- **打包**：bin/LiMoBackend/ffmpeg/ / backend/static/katex/fonts/
- **文档**：CLAUDE.md / STATUS.md / CHANGELOG.md

---

## 2026-07-12 — 新图标 + README 重写 + 文档中链接修复

### 新应用图标

白底深蓝国风"沥墨"图形，替换了全部尺寸的图标（Tauri 打包 + favicon + 顶栏 logo）。

### README 重写

按用户任务重新组织——导入能做什么、AI 能产出什么、怎么导出——而不是堆技术细节。

### 旧产物死链接修复

历史 `[▶ mm:ss](#)` 链接渲染为静态 Play badge，不再触发页面滚动。

### 影响范围

- **文档**：README.md / CHANGELOG.md
- **前端**：App.tsx / index.html / markdown-components.tsx / 图标文件

---

## 2026-07-12 — Content 时间戳可点击 + 标题锚点滚动修复 + PPT 默认引擎切换

### Content-[mm:ss] → 可点击跳转

对标 BiliNote：`Content-[mm:ss]` 现在是可点击的链接，点击跳转到原视频对应时间点（B站/YouTube/通用格式）。仅视频文档生效，非视频文档显示静态 badge。

### 标题锚点 `scrollIntoView`

点击 `#heading-id` 链接不再带动整个窗口滚动，改为 `preventDefault` + 目标元素 `scrollIntoView`，仅影响预览区。

### 旧产物 `[▶ mm:ss](#)` 死链接

`_post_process_content_markers` 历史残留的 `href="#"` 链接不再跳转，渲染为静态 Play badge。

### PPTX 默认引擎 markitdown → python-pptx

MarkItDown 对某些 PPTX 不提取图片（找到 0 个 base64）。`doc:ppt` 默认引擎换回 `python-pptx`。

### 影响范围

- **前端**：markdown-components.tsx / MarkdownPreview.tsx / PreviewArea.tsx / GenPanel.tsx / ExportPanel.tsx
- **后端**：engines/**init**.py / document.py

---

## 2026-07-12 — 引擎开箱即用 + 截图导出内联 + 思维导图格式提示

### Doc 引擎自动绑定（开箱即用）

用户第一次用不需要碰引擎设置。三层修复：

- **后端**：`init_engines` 启动时自动注册 7 个 doc 子场景（text+image/word/ppt/excel/epub 等）到 `engine_models` + `engine_bindings`，MD5 ID 与 `build_engine_models` 对齐。save_config 移出 Tesseract try 块，绑写不受影响
- **前端**：页面加载时自动把 null 的 binding 填成第一个可用引擎 ID，`saveEngineBindings` 写盘
- 下拉框「系统默认」不再是空壳——背后有真实 ID

### 截图导出内联

导出系统之前只内联 `/api/files/` 文档图片，漏了 `/static/screenshots/` 视频截图。

- `_find_image_refs` 新增 `_SS_RE` 匹配截图 URL
- `_collect_images` 支持 `screenshots/` 目录读取
- `_inline_images` / `_rewrite_image_paths` 同时处理两种图片源
- HTML 导出自包含、ZIP separate/inline 三种模式全覆盖

### ExportPanel UI 提示

- 思维导图勾选时显示"始终导出为交互式 HTML，不受格式选择影响"
- 导出内容区显示"仅导出已生成的产物，未生成的内容会被自动跳过"

### 其他

- 后端 4 个 `subprocess` 调用统一加 `CREATE_NO_WINDOW` 消除 ffmpeg 黑框
- `Content-[mm:ss]` 后端不再替换为死链接，改由前端渲染为时间戳 badge
- `Screenshot-[mm:ss]` 前端新增 Camera 图标 badge
- 生成/导出完成后均有 Toast 提示

### 影响范围

- **后端**：engines/**init**.py / export.py / generator.py / video\*.py / asr_cloud.py
- **前端**：GenPanel.tsx / ExportPanel.tsx / ExportMenu.tsx / markdown-components.tsx / DocLibrary.tsx / VideoBanner.tsx

---

## 2026-07-12 — ffmpeg 黑框消除 + 标记渲染修复 + URL 统一

### subprocess 黑框消除

生成视频产物时 ffmpeg 会弹出 cmd 黑框。6 处 `subprocess.run` 调用加 `creationflags=subprocess.CREATE_NO_WINDOW`：

- `video_reader.py` / `video_helper.py` / `video.py` / `asr_cloud.py` / `export.py`

### Content/Screenshot 标记渲染修复

- `Content-[mm:ss]`：后端不再替换为死链接 `[▶ mm:ss](#)`，改为保留原文。前端渲染为 Play 图标时间戳 badge
- `Screenshot-[mm:ss]`：前端新增 Camera 图标 badge 渲染。后端勾选「原片截图」后 ffmpeg 抽帧替换为真实图片
- 后端 `_post_process_content_markers` 仅移除前导 `*`（避免 markdown 斜体干扰），不改变标记主体

### `appUrl()` 统一应用内 URL

Tauri 打包端无 Vite proxy，`/api/` 和 `/static/` 路径需转为绝对 URL。新增 `appUrl(path)` 函数，统一 `MarkdownImage` / `DocLibrary` / `VideoBanner` 三处图片加载。

### 影响范围

- **后端 5 文件**：video_reader.py / video_helper.py / video.py / asr_cloud.py / export.py / generator.py
- **前端 3 文件**：client.ts / markdown-components.tsx / DocLibrary.tsx / VideoBanner.tsx

---

## 2026-07-12 — 打包兼容 + 路径统一 + 测试支撑

### `_transcribe` 漏 `await` 修复

video.py 两处 `self._transcribe(...)` 改为 `async def` 后漏 `await`，返回 coroutine → 下游 `asr_result.get("segments")` 抛 `AttributeError`。

### `appUrl()` 统一 URL 转换

打包端（Tauri）无 Vite proxy，`/api/` 和 `/static/` 路径需转为 `http://127.0.0.1:7413` 绝对 URL。新增 `appUrl(path)` 函数，统一处理 `/api/`、`/static/`、外部链接三种情况。

**受影响组件**（全改为 `appUrl()`）：

- `MarkdownImage`：笔记内嵌图片、截图
- `DocLibrary`：文档封面
- `VideoBanner`：视频横幅封面

### 截图路径从 CWD → data_dir

- 后端生成：`os.getcwd()/data/screenshots/` → `{store_root}/screenshots/`
- StaticFiles 挂载：模块级相对路径 → startup 内 `{data_dir}/screenshots/`
- 打包端和网页端统一到 `~/.limo/screenshots/`

### 文件日志

新增 `app.log`（5×2MB 滚动，INFO 全量），路径 `{data_dir}/app.log`。打包端用户无法看终端，错误排查全靠这个文件。

### 文档删除时清理图片

`Store.delete_transcript` 现在清理 `files/{tx_id}/` 目录（文档内嵌图片），避免孤儿文件堆积。

### MarkItDown 图片诊断

`MarkItDownEngine` 加日志：找到 base64 图片数、写入成功数、解码失败详情。

### 影响范围

- **后端 4 文件**：video.py / main.py / generator.py / store.py / doc.py
- **前端 4 文件**：client.ts / markdown-components.tsx / DocLibrary.tsx / VideoBanner.tsx

---

## 2026-07-11 — 交互修复 + 导出面板重构 + 默认值修正

### AI 面板 / 思维导图滚动修复

- AI 面板 `scrollIntoView` → 容器 `scrollTop`，不再带动全页上翻
- 思维导图 `mm.fit()` defer 到 `requestAnimationFrame`，避免布局未完成触发页面滚动

### 思维导图白屏

`flex-1 bg-elevated` + `minHeight:200px` → 容器高度不足时 SVG 只有 200px，剩余空间显示浅色背景（像白色遮罩）。改为 `absolute inset-0` 填满父容器。

### ExportPanel 重构

- 按钮标签：「导出全部产物」→「一键导出所有产物 (ZIP)」；「导出 N 个文件」→「按上方设置导出 N 个文件」
- 删除 Markdown 预览（内容和导出不一致），替换为文字摘要
- 清理死代码：5 个无用 import、3 个 store selector、2 个 state 变量

### SVG 导出文字消失

`cloneNode` 丢失外部 CSS → 内联页面所有样式表规则到 `<style>` 标签注入 SVG。

### `streamDisplay=false` 产物静默丢失

流式关闭时 `onChunk` 直接 return，`onDone` 从空缓冲区取内容 → 产物永远为空。改为内部 ref 缓冲区始终累积，不受开关影响。

### SSE `\r\n` 解析

部分 API 返回 CRLF 换行 → JSON 解析失败，事件静默丢弃。4 处 SSE 解析统一加 `\r\n→\n` 归一化。

### 默认值修正

- `engine_auto_fallback`：`True → False`，引擎失败直接报错而非静默降级
- 公式图片渲染：`dpi 150→100, fontsize 16→14, pad 0.08→0.05`

### 影响范围

- **前端 5 文件**：GenPanel.tsx / client.ts / MindmapView.tsx / ExportPanel.tsx / ExportMenu.tsx
- **后端 2 文件**：config.py / export.py

---

## 2026-07-11 — 打包前缺陷修复：漏导入 + 截图 prompt + SVG 下载 + 死代码

### 漏导入（PyInstaller 打包后致命）

全量审计 36+ Python 文件，修复 3 处漏 import：

| 文件                   | 漏的 import  | 触发场景                                                    |
| ---------------------- | ------------ | ----------------------------------------------------------- |
| `adapters/document.py` | `FatalError` | Doc 导入时引擎未绑定 → NameError 500                        |
| `routers/model.py`     | `AppError`   | 厂商/引擎名称校验失败 → NameError 500                       |
| `routers/export.py`    | `shutil`     | KaTeX 服务端渲染预检 `shutil.which("node")` → NameError 500 |

### 纯文本 LLM 截图 prompt 修复

`video_understanding=false` 时 LLM 看不到网格图，但 prompt 还在说"从网格图左上角黄色数字中选取"。LLM 无图可选，不生成 `*Screenshot-[mm:ss]` 标记 → 截图后处理无标记可替换。

**修复**：`get_screenshot_format(has_visual=bool)` 按 VL 开关分叉——无 VL 时让 LLM 从转写时间戳中选取。

### tiktoken 打包兼容

PyInstaller 打包后 `tiktoken.get_encoding()` 抛 `AttributeError`（模块正常导入但原生扩展未链接），`except ImportError` 未捕获。改为 `except (ImportError, AttributeError)`。

### 思维导图 SVG 下载死按钮

`ExportMenu` 用 `document.querySelector('#mindmap svg')` 但 MindmapView 外层没有 `id="mindmap"` 容器，选择器永远返回 null。

### AI 面板 `scrollIntoView` 带动全页滚动

ChatCore 用 `bottomRef.scrollIntoView()` 滚动聊天到最新消息。面板刚挂载、布局未完成时，`scrollIntoView` 会带动整个页面向上翻。

**修复**：改用聊天容器的 `scrollTop = scrollHeight`，仅影响自身区域。

### 死代码清理

- `engineHasImages`：GenPanel 中 ~12 行 useMemo 计算 + GenerateOptions 中 prop 声明，从未被读取

### 打包

PyInstaller + Tauri v2 双包构建成功，含 ffmpeg 运行时。

### 影响范围

- **后端 4 文件**：document.py / model.py / export.py / content_preparer.py
- **后端 2 文件**：prompt.py + generate.py（函数签名 + 调用者）
- **前端 3 文件**：PreviewArea.tsx / GenPanel.tsx / GenerateOptions.tsx / ChatCore.tsx

---

## 2026-07-11 — 代码质量审查：修复 6 项 bug + 死代码清理 + 诊断增强

### 修复

| #   | 严重度 | 问题                                                                                          | 文件                  |
| --- | :----: | --------------------------------------------------------------------------------------------- | --------------------- |
| B1  |   中   | `_warn_mismatched_fields` 误报：`image_analyze` 加入 video 后，两个类型互相检查时会打 warning | `generate.py`         |
| B2  |   高   | 单产物路径用 raw `mode` 而非 `stream_mode` 判断 GenerateMode，传入非标准 mode 值时可能误判    | `generate.py`         |
| B6  |   中   | `generate_both` 只对 notes 做截图后处理，漏了 polished 内容                                   | `generator.py`        |
| B7  |   中   | `_optimize_data_uri` 缺 RGBA→RGB 转换，VL RGBA 图片保存 JPEG 可能失败                         | `content_preparer.py` |
| B8  |   中   | yt-dlp 下载失败时临时目录泄漏，长时间运行累积磁盘占用                                         | `content_preparer.py` |
| B10 |   低   | ASR 绑定日志说 whisper-small 是默认，实际 bcut 是首选                                         | `engines/__init__.py` |

### 死代码清理

- **`engineHasImages`**：GenPanel.tsx 中 ~12 行 useMemo 计算 + GenerateOptions.tsx 中 prop 声明，GenerateOptions 从未读取。已删除。
- **React 重复 key**：版本切换器用 `v.version` → 改为 `v.id`。markdown `p`/`li` 时间戳 badge 用 `useId()` 前缀。

### 图片→LLM 链路修复

- **空 src 警告**：图片剥离后 `![alt]` 渲染成 `<img src="">` → 改为 `[📷 alt]` 文本占位符
- **`MarkdownImage`**：空 src 直接不渲染元素（`return null`），避免浏览器重载整页

### LLM 流式诊断增强

`service.py` 加两份日志：

- 多模态请求体大小：`img_chars=2500K`，帮助诊断上传瓶颈
- 首个 chunk 到达时间：定位延迟在 API 侧还是网络侧

### 影响范围

- `generate.py`：type fields 共享字段处理 + `mode`→`stream_mode`
- `generator.py`：screenshot 双产物一致性
- `content_preparer.py`：RGBA 兼容 + temp dir 清理 + 空 src 修复
- `engines/__init__.py`：日志文案修正
- `client.ts`：VL 参数序列化补全 + `!== undefined` 统一
- `markdown-components.tsx`：空 src 守卫 + key 唯一性
- `PreviewArea.tsx`：版本 key 修复
- `GenPanel.tsx`：死代码删除 + VL 联动简化
- `GenerateOptions.tsx`：对标 BiliNote 简化 UI + VL 参数折叠
- `service.py`：多模态流式诊断日志

---

## 2026-07-11 — 对标 BiliNote 简化视频画面理解：一个开关 = VL + 发给 LLM

### 背景

之前 LiMo 的视频画面理解被拆成三层嵌套：`启用画面截图 → 保留截帧图片 → AI 分析图片`。对标 BiliNote 后发现过度设计——VL 管线生成的网格图是给大模型看的，嵌入笔记毫无意义（2×2 四格拼图对读者什么也说明不了）。BiliNote 的设计是 `video_understanding` 一个开关直接控制"VL 跑 + 发给 LLM"，简洁直观。

### 修改

**删除三级嵌套，改为对标 BiliNote 的单一开关：**

```
之前（过度设计）：
  视觉理解
  └── 启用画面截图 [checkbox]
      ├── 截图间隔、拼图尺寸...
      笔记格式
      └── 保留截帧图片 [checkbox]     ← 网格图嵌入文章（无意义）
          └── AI 分析图片 [checkbox]   ← 真正发给 LLM
              └── 图片预处理 [checkbox]

现在（对标 BiliNote）：
  AI 画面理解 ← "截取视频画面发给多模态模型辅助分析"
  └── 启用 [checkbox]
      ├── 截图间隔（秒）
      ├── 拼图尺寸（列 × 行）
      └── ▸ 高级设置（可折叠）
          ├── 单帧分辨率 / JPEG 质量
          ├── 相邻重复帧去重 / 帧质量过滤
          └── 图片预处理

  笔记格式
  └── 原片截图  ← 独立，ffmpeg 单帧，不经过大模型
```

**文档类型不变**：保留文档图片 → AI 分析图片 → 图片预处理 三级嵌套。文档图片对读者有意义，不同于 VL 网格图。

**后端同步简化**：`send_to_llm = bool(image_analyze)`。前端已保证门控（视频=videoUnderstanding，文档=formatImages&&imageAnalyze），后端不再重复检查 `keep_refs`。

**VL 参数精简**：默认只显示间隔+拼图（对标 BiliNote），其余折叠到 `<details>` 高级设置中。

### React 重复 key 修复

`markdown-components.tsx` 中 `p` 和 `li` 组件的 `key={i}` 在 `React.Children.map` 展平后产生重复 key。修复：使用 `React.useId()` 为每个组件实例生成唯一前缀 `key={`${pid}-${i}`}`。

### 前台参数序列化修复

`client.ts` 缺失 5 个 VL 参数（`dedupe_enabled`/`unit_width`/`unit_height`/`save_quality`/`frame_quality_filter`）的序列化。全部布尔/数字参数改为 `!== undefined` 判断。

### 单产物路径 send_to_llm / optimize_images 漏传

`generate.py` 单产物路径只传了 `keep_refs`，漏了 `send_to_llm` 和 `optimize_images`。

### 图片优化覆盖率

VL 图片和文档 data URI 发给 LLM 时此前不经过 optimize_images。

### 影响范围

- **前端 `GenerateOptions.tsx`**：三级嵌套改为单一开关 + VL 参数折叠 + 无模型时禁用
- **前端 `GenPanel.tsx`**：删除 VL↔formatImages 双向联动，image_analyze 按视频/文档分派
- **前端 `client.ts`**：补 5 个 VL 参数 + `!== undefined` 序列化
- **后端 `generate.py`**：`send_to_llm` 简化 + 单产物补参
- **后端 `generator.py`**：`send_to_llm` 简化
- **后端 `content_preparer.py`**：VL 图片 + data URI 优化覆盖
- **后端 `markdown-components.tsx`**：React 重复 key 修复

---

## 2026-07-11 — 视频截图逻辑解耦 + 图片→LLM 全链路修复

### 视频截图两条管线解耦

视频类型有两套完全独立的图片系统，之前被错误地耦合在一起：

| 选项             | 管线                                   | 时机                                   | 图片流向                               |
| ---------------- | -------------------------------------- | -------------------------------------- | -------------------------------------- |
| **原片截图**     | ffmpeg 单帧抽取                        | LLM 生成**后**，按 AI 标记的时间点抽帧 | 嵌入文章，**不经过大模型**             |
| **启用画面截图** | VL 管线（下载视频→抽帧→拼网格→base64） | LLM 生成**前**                         | 嵌入文章 + 可选发给 LLM（AI 分析图片） |

**问题 1**：「原片截图」被 `disabled={!videoUnderstanding}` 人为禁用，但后端 `generate.py:378` 本来就在无 VL 时独立下载视频做截图。**修复**：移除该约束，两个选项完全独立。

**问题 2**：关掉「保留截帧图片」后 VL 管线照样空跑（下载视频→抽帧→拼网格→丢弃）。**修复**：加反向联动 `!formatImages → setVideoUnderstanding(false)`。

**问题 3**：开 VL 自动开保留截帧图片，但没有反向联动。已在 GenPanel.tsx 补齐。

### 图片→LLM 参数传输修复

#### 5 个 VL 参数前端从未传给后端（严重）

`client.ts:streamGenerate()` 缺失 `dedupe_enabled`、`unit_width`、`unit_height`、`save_quality`、`frame_quality_filter` 的 query string 序列化。用户调整「单帧分辨率」「JPEG 质量」「去重」「帧质量过滤」**从未生效**，后端永远用默认值。

**同时修复**：所有布尔/数字参数从 truthiness 判断（`if (opts.xxx)`）改为 `!== undefined`，确保 `false` 和 `0` 也能正确传输。此前 `dedupe_enabled=false` 被当作 falsy 跳过 → 后端用默认值 `true`。

#### 单产物模式 send_to_llm / optimize_images 漏传

`generate.py` 单产物路径（line 538-540）调用 `prepare()` 时只传了 `keep_refs`，没传 `send_to_llm` 和 `optimize_images`。双产物路径正确传了三个参数。

**影响**：用户只勾选「笔记」或只勾选「整理版」时，「AI 分析图片」和「图片预处理」静默失效——图片永不给 LLM，也不压缩。

### 图片优化覆盖率修复

`optimize_images=True`（图片预处理）此前只在部分路径生效：

| 图片来源      |        嵌入文本        |           发给 LLM           |
| ------------- | :--------------------: | :--------------------------: |
| VL 网格图     | ✅ `_format_vl_images` |  ❌→✅ `_optimize_data_uri`  |
| 文档 data URI |     不嵌入（剥离）     | ❌→✅ `_optimize_image_data` |
| 文档文件引用  |       保留原路径       |  ✅ `_optimize_image_data`   |

### 影响范围

- **前端 `GenerateOptions.tsx`**：移除原片截图 disabled 约束 + 描述文案
- **前端 `GenPanel.tsx`**：加反向联动 effect（关保留截帧→关 VL）
- **前端 `client.ts`**：补 5 个 VL 参数 + 全部改为 `!== undefined` 序列化
- **后端 `generate.py`**：单产物路径补 `send_to_llm` + `optimize_images`
- **后端 `content_preparer.py`**：VL 图片 + data URI 发给 LLM 时走优化

---

## 2026-07-10 — 新增 Bcut（必剪）免费云转写引擎，设为默认

### 背景

faster-whisper 需要本地下载模型（~500MB），首次使用慢且占用 CPU。BiliNote 项目已有 B站必剪 API 的免费云转写实现，可直接搬运。

### Bcut API 特点

- **免费**：无需 API Key，只需 User-Agent
- **云转写**：上传音频 → B站服务器转写 → 返回分段结果
- **中文优化**：B站背景，中文识别质量好
- **耗时**：通常 10-60 秒（取决于音频长度和排队）

### 实现

`backend/app/engines/asr_bcut.py` — `BcutASREngine`，实现 `Engine` 协议：

- 分片上传音频（最多 ~500 分片）
- 创建转写任务 → 轮询结果（最长 500s 超时）
- 逐轮检查 `cancel_event`，支持取消
- 返回 `TranscriptResult`（语言 + 分段 + 时间戳）

### 默认绑定优先级

```
bcut（免费云）→ faster-whisper-small（本地兜底）→ dashscope（需 Key）
```

用户开箱即用，无需配置任何 Key 或下载模型。

### 影响范围

- **`backend/app/engines/asr_bcut.py`**：新增，~150 行
- **`backend/app/engines/__init__.py`**：注册 + 自动模型条目 + 绑定
- **`backend/app/adapters/video.py`**：`_resolve_asr_engine` 加 bcut provider 分支

---

## 2026-07-10 — 修复：PyInstaller 打包后后端启动崩溃（OpenSSL DLL 冲突）

### 现象

安装 LiMo 后启动，前端加载成功但所有 API 请求返回 `ERR_CONNECTION_REFUSED`。直接运行 `LiMoBackend.exe` 报错：

```
ImportError: DLL load failed while importing _ssl: 找不到指定的程序。
```

### 根因

`cryptography` 包携带的 `libcrypto-3-x64.dll`（~5MB）覆盖了 Python 原生版本（~8MB）。`_ssl.pyd` → `libssl-3-x64.dll` → `libcrypto-3-x64.dll` 依赖链中，cryptography 提供的 libcrypto 缺少 `libssl` 需要的导出符号，导致整个 `ssl` 模块无法加载 → FastAPI/anyio 无法启动。

### 修复

`LiMoBackend.spec` 中：

1. `collect_all('cryptography')` 后过滤掉其携带的 `libcrypto*/libssl*` DLL
2. 显式从 `sys.base_prefix/DLLs/` 添加 Python 原生的 OpenSSL DLL

### 影响范围

- **`backend/LiMoBackend.spec`**：新增 DLL 过滤逻辑 + 显式添加原生 OpenSSL
- **`CLAUDE.md`**：常见陷阱新增 OpenSSL 冲突条目
- 后续新增 pip 依赖时，若该包携带 OpenSSL DLL，可能再次触发此问题

### 验证方法

打包后直接运行 `LiMoBackend.exe`，确认输出 `Uvicorn running on http://127.0.0.1:7413`

---

## 2026-07-10 — 思维导图 HTML 导出空白（JS 未包裹 script 标签）

### 现象

导出的思维导图 HTML 文件在浏览器中打开后完全空白，什么都没有显示。

### 根因

`_build_mindmap_html()` 中，当本地 d3.js / markmap-view / markmap-lib 文件存在时，直接内联 JS 源码但**没有用 `<script>` 标签包裹**：

```python
# 修复前：裸 JS 文本，浏览器不执行
{d3_js if d3_js else '<script src="..."></script>'}
```

浏览器将这三个库的源代码当作文本节点渲染，`window.markmap` 永远为 undefined。只有 CDN 回退路径（`else` 分支）有 `<script>` 标签。

### 修复

每个内联 JS 都用 `<script>...</script>` 包裹：

```python
# 修复后
{f'<script>{d3_js}</script>' if d3_js else '<script src="..."></script>'}
```

已验证 d3.min.js / markmap-view.js / markmap-lib.js 均不含 `</script>` 字面量，无提前闭合风险。

### 影响范围

- **`backend/app/routers/export.py`**：`_build_mindmap_html()` 第 921-923 行
- 同步影响 `GET /export/{id}/mindmap` 和 `POST /export/custom` 两条路径

---

## 2026-07-10 — GFM 表格渲染遗漏 + mode 回退静默返回错误内容

### 表格问题

LLM 生成的 Markdown 中，"三大痛点：\n| 痛点 |" 这种表格紧跟前导文字无空行的写法在 GFM 中合法（react-markdown 正常渲染），但 Python markdown `tables` 扩展要求表格前有空行，导致导出 HTML 中表格被当作段落文本输出（原样显示管道符）。

**修复**：`_preprocess_gfm()` 中加正则，检测非表格行后紧跟表头+分隔行的模式，自动插入空行。

### mode 回退问题

`GET /export/{id}?mode=notes` 在 notes 未生成时，回退逻辑取全部 markdown 的第一个条目。若 polished 已生成，导出会静默返回 polished 内容，违反"预览一致"原则（预览栏显示"尚未生成笔记"）。

**修复**：仅当 mode 为空（默认）时回退到任意产物；mode 已被显式指定但未找到时，不进回退链，后续走 process results 或 404。

### 影响范围

- **`backend/app/routers/export.py`**：`_preprocess_gfm()` + `export_content()` 回退逻辑

---

## 2026-07-10 — ASR 引擎自动注册（启动即可用）

### 现象

界面显示已绑定 ASR 引擎，但导入视频时报"ASR 绑定链为空或全部失败，自动降级已关闭"。engine_models 表中只有 OCR 记录，ASR 模型条目缺失。

### 根因

Tesseract OCR 在 `init_engines()` 中有完整的自动注册逻辑（写入 `engine_models` + `engine_bindings`），但 faster-whisper / DashScope ASR 只注册了引擎实例，缺失了模型条目和默认绑定。`_resolve_asr_engine()` 通过 `engine_models` 表查找绑定 → 找不到 → 返回空。

### 修复

`app/engines/__init__.py`：对标 Tesseract 补齐 ASR 三步自动注册：

1. 写入 `engine_models`（faster-whisper-small/medium + dashscope）
2. faster-whisper-small 作为默认首选（本地免费，零配置即可用）
3. 清理 `engine_bindings` 中失效的绑定 ID

### 影响范围

- **`backend/app/engines/__init__.py`**：新增 ~60 行 ASR 自动注册
- 首次安装用户开箱即用，已有用户启动后自动修复失效绑定

---

## 2026-07-10 — 取消机制全链路修复（LLM Token + 本地 ASR CPU）

### 现象

点击取消按钮后前端 UI 清除、`AbortController.abort()` 触发，但后端继续消耗 token / CPU：

- **chat/stream**：`cancel_event` 创建后没人 set，Agent 工具循环继续跑
- **process（图片处理）**：完全没有取消机制，LLM 调用不可中断
- **本地 ASR（faster-whisper）**：同步阻塞代码卡死事件循环，`_watch_disconnect` 协程无机会执行

### 根因

三个独立问题：

1. `chat.py` 有 `cancel_event = asyncio.Event()` 但没有 `_watch_disconnect` 任务来监听客户端断连并 set 它
2. `process.py` 是普通 POST 端点，`async with guard.semaphore` 内部没有任何取消检测
3. `video.py:_transcribe()` 中 `engine.run()` 是同步阻塞调用，整个事件循环被卡死（包括 `_watch_disconnect`）。faster-whisper 逐段 yield 但未检查取消信号

### 修复

| 层           | 文件                   | 改动                                                                         |
| ------------ | ---------------------- | ---------------------------------------------------------------------------- |
| chat 端点    | `routers/chat.py`      | 加 `_watch_disconnect` + finally cleanup                                     |
| process 端点 | `routers/process.py`   | 加 `cancel_event` + disconnect 监听，传入 `llm.call()`                       |
| 适配器       | `adapters/video.py`    | `_transcribe` 改为 `async`，`engine.run()` 用 `asyncio.to_thread` 放到线程池 |
| ASR 引擎     | `engines/asr.py`       | `execute()` 接受 `cancel_event`，逐 segment 检查并抛 `FatalError`            |
| 云端 ASR     | `engines/asr_cloud.py` | `execute()` 签名加 `**kwargs` 兼容 cancel_event 透传                         |

**关键设计**：`asyncio.to_thread` 将同步引擎调用放到后台线程，事件循环恢复空闲 → `_watch_disconnect` 可正常运行 → `cancel_event.set()` → 线程中引擎在下一个 segment 检测到 → 立即停止，不再跑完整个转写。

### 影响范围

- 6 个文件，涉及聊天/图片处理/视频导入三条管线
- 取消后 token 消耗和本地 CPU 消耗可真正停止
- DashScope ASR（云端）因 HTTP 请求无法中断，但耗时短（几秒），影响小

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

| 引擎                         | 图片输出       | 备注                     |
| ---------------------------- | -------------- | ------------------------ |
| PyMuPDF (text_only=False)    | ✅ 文件/base64 | 嵌入图片 + 表格          |
| PyMuPDF4LLM                  | ✅ 文件/base64 | write_images=True        |
| MarkItDown                   | ✅ 文件/base64 | 全格式通用               |
| DocxEngine (text_only=False) | ✅ 文件/base64 | 行内图片                 |
| PptxEngine (text_only=False) | ✅ 文件/base64 | **本轮新增 base64 回退** |
| EpubEngine                   | ✅ 文件/base64 | **本轮新增 base64 回退** |
| ExcelEngine                  | ❌             | 无图片管线（表格为主）   |

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

| 状态     | 项目                                                                             |
| -------- | -------------------------------------------------------------------------------- |
| 已修复   | C1, H1-H8, H12（9 项，之前会话中已修）                                           |
| 误报     | C2（`disconnect_task=None` 在 try 前是防御性写法，finally 块引用它做 None 检查） |
| 本轮修复 | d3.js 离线、事实验证日志（2 项）                                                 |
| 低优先级 | SearXNG 未打包（200MB+ 依赖，非 bug，搜索降级链正常）                            |

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

| 文件                               | 修复                                                        |
| ---------------------------------- | ----------------------------------------------------------- |
| `ExportMenu.tsx:85` HTML 导出      | `/api/export/` → `/export/`                                 |
| `ExportMenu.tsx:121` 思维导图导出  | 同上                                                        |
| `ExportMenu.tsx:164` ZIP 导出      | 同上                                                        |
| `ExportPanel.tsx:346` 导出全部产物 | 同上                                                        |
| `ExportPanel.tsx:362` 详细导出     | `apiUrl('/api/export/custom')` → `apiUrl('/export/custom')` |
| `ChatCore.tsx:313` 对话导出        | 同上                                                        |

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
