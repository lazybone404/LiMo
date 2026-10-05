# 代码质量审查（第二轮）— 外部报告 35 项修复台账

> **基准：`6c2ef28`（VERSION `0.4.16`，2026-10-06）。**
> **本轮评审来自项目外部**，原始报告在 [`pi-docs/代码质量外部审查报告.md`](../pi-docs/代码质量外部审查报告.md)（583 行，
> 含每条的可复现命令与实测输出）。**本文件是修复台账与纠偏记录**，不重复报告正文——查「哪条说了什么」翻原报告，
> 查「这条怎么修、修到哪了」看本文件。
>
> 与 [`code-audit.md`](code-audit.md)（第一轮，`4174166` 快照）的关系：两份**同一作者心智下的独立结论**，
> 覆盖面无强关系（第一轮 25 项已全部修完并记于 CHANGELOG）。本文件**刻意不并入** code-audit.md——
> 那份自述「`4174166` 时点的冻结快照，行号可复现」，追加会把它的行号基准搅乱。

## 条目 slug 表（commit message 引用用）

| # | slug | # | slug |
|---|---|---|---|
| P0-1 | `P0-01-export-path-traversal` | P1-11 | `P1-11-custom-export-two-impl` |
| P0-2 | `P0-02-test-real-config-pollution` | P1-12 | `P1-12-export-zip-blocks-loop` |
| P0-3 | `P0-03-index-corrupt-silent-empty` | P1-13 | `P1-13-provider-settings-no-catch` |
| P0-4 | `P0-04-stream-bypass-promptbuilder` | P1-14 | `P1-14-chatstore-rebuild-race` |
| P0-5 | `P0-05-ingest-error-envelope` | P1-15 | `P1-15-heading-id-three-ways` |
| P1-1 | `P1-01-tests-never-red` | P1-16 | `P1-16-color-guard-narrow` |
| P1-2 | `P1-02-plugin-config-not-restored` | P1-17 | `P1-17-chart-asset-name-collision` |
| P1-3 | `P1-03-placeholder-entry-deleted` | P1-18 | `P1-18-chart-node-normalize` |
| P1-4 | `P1-04-default-config-polluted` | P1-19 | `P1-19-abogus-gpl` |
| P1-5 | `P1-05-atomic-write-fixed-tmp` | P2-1 | `P2-01-no-static-check` |
| P1-6 | `P1-06-stf-return-dropped` | P2-2 | `P2-02-status-stats-stale` |
| P1-7 | `P1-07-excel-phantom-sheet` | P2-3 | `P2-03-claude-md-overflow` |
| P1-8 | `P1-08-dual-product-partial` | P2-4 | `P2-04-changelog-452kb` |
| P1-9 | `P1-09-compressor-skips-user-window` | P2-5~11 | `P2-05…P2-11`（同编号规则） |
| P1-10 | `P1-10-ingest-tmp-leak` | | |

---

## 一、开工前纠偏（2026-10-06，修复启动时）

本轮修复前的核实在外部报告之外查出 **两批问题**：**8 条「照报告的修复建议直接做会引入新问题」**（必须改判修法）
与 **6 条「描述与现状不符」**（照描述去改会改错地方）。另有 **3 处报告漏掉的新发现**。
报告本身 **35 条无一假阳性**——两批都属于「结论对、修法/描述不准」，不是「报错了」。

### A. 照报告改会引入新问题（8 条）

| # | 报告的修法 | 为什么不能照做 | 改判后的修法 |
|---|---|---|---|
| **P0-4** | 改 `generate.py:948-960`（单产物分支）走 `PromptBuilder` | ① xlsx 实际走 `:659` **双产物分支**（前端 `GenPanel.tsx:611` 发 `mode="both"` → 后端 `:506-507` 展开）——只改 `:948` = **改了个 xlsx 走不到的地方**；② 流式内联调用传了 `video_understanding`（`build_notes_prompt` 签名里本就有），而 `PromptBuilder` 不接该参数 → **静默丢掉视频画面理解**；③ `build_both` 对 xlsx 返回 `(sp, sp)`，而 generator 非流式对该类型只跑一次 LLM → 改走 builder 会**双跑** | ① 把 xlsx 分支提到 `build_notes_prompt` **顶部**（它签名里本来就有 `video_understanding`，所有调用点自动受益）；② `generate.py` 照 `:269-276` 的 composite 闸门给 xlsx 加 `_raw_mode = "notes"`，**只出一个产物**（顺带消掉那次白烧的 polished） |
| **P1-5** | 把产物写盘纳入同一把锁 | `self._lock` 是**非重入** `threading.Lock`，而 `_save_index` 内部**也**调 `_atomic_write` 且恒在已持锁时被调到 → **死锁** | 只改临时名：`tempfile.mkstemp(dir=path.parent)`（同目录唯一名），锁结构完全不动 |
| **P1-9** | 「把 `model_id` 传进去（**调用方手里就有**）」 | **调用方手里没有**：`LLMService` 只暴露 `provider` / `model`（`llm/service.py:49-50`）；且 `reference_compressor` 的设计意图**正是**「按**实际在用的模型**查询」而非场景模型（`core/model_resolver.py:127-128` 注释点明），把 model_id 传进去是**把设计改坏** | 在 `resolve_context_window` **第 1 层**增加「`provider_id + model_name` 匹配 `enabled_models`」的支路，调用点语义不动 |
| **P1-18** | 给 hchain/timeline/branch 补 `_norm_node`，或把 `chart_renderer` 的 except 放宽为 `Exception` | ① `_branch` 依赖 `n['result']` / `n['result_sub']`，而归一化对字符串只产 `{"label": ...}` → 右列结果框全空，**把崩溃变成静默错图**；② 放宽 except 会吞掉真 bug，违背本模块自述的「失败显式化」契约 | 在 `engine.expand` **分派前统一归一化** `spec['nodes']`——10 个算子一起受益，且不动任一算子的字段假设 |
| **P1-16** | 守护正则补禁 `text-white` / `bg-black` | 全仓 49 处里**大部分是有意的**（`bg-accent text-white` 彩底白字、`bg-black/20` 遮罩）→ 一刀切会产生大量假阳性 | ① 补 `--tk-on-accent` 前景 token，让实底按钮引用它（治根因）；② 守护判据改断**语义**（禁裸 hex / 禁非语义底色的黑白），不断某个类名 |
| **P1-3** | 「`transcript.md` 不存在就不删」 | 会让**真正被删目录**的幽灵条目永久残留——只是把一类静默损坏换成另一类 | 加**就绪标记**（条目带 `ready`，`save_transcript` 才置 true），只清理「非 ready 且超时」的条目 |
| **P2-1** | 接入 ruff +「那 262 行 AST 扫描器可以退休」 | 两者**覆盖面不等价**：`test_closure_shadowing.py` 覆盖「函数内条件 import 造成同名局部」（模式 #26 变体）且自带 `_OFFENDER` 自证；报告只给了单例 demo，未端到端证明等价。按模式 #38，**断言存在 ≠ 断言有效** | **本轮不接入 ruff**（用户已决定）；扫描器保留 |
| **P2-7** | 后端三个端点改返 404 | 前端 `ChatSidebar.tsx:57-59 / 69-70` 的 `renameConversation` / `deleteConversation` **不 await、不 catch** → 改成 404 后 `apiFetch` 抛错会变成**未捕获 rejection** | **前后端同批改**：后端返 404 + 前端补 catch 与乐观更新回滚 |

### B. 描述与现状不符（6 条）

| # | 报告原述 | 实际 |
|---|---|---|
| **P1-2** | 「`self.plugin_config = {}` **无读方**」 | **有 3 处读方**（`:217` stdin 契约 / `:569` setter / `:605` 枚举）。真缺陷是「`init_plugins` **从不读 `config["plugin_configs"]` 回填**」（重启即丢）——照「无读方」去删字段会删掉 stdin 契约 |
| **P1-13** | 「19 个异步处理器仅 6 处 catch」 | 处理器确为 19 个，但 6 处 catch 里 2 处不在处理器内 → **处理器内实际只有 4 处**有 try/catch |
| **P1-8** | 拿 `generate.py:1019-1025` 论证「单产物路径落 partial」 | `:1019-1025` 是**超时分支**，非通用路径（正常完成 `:1043-1061` 不显式设 status）。**结论对，例证跨了场景** |
| **P1-12** | 「热路径被阻塞」 | 更该强调的是 **`/export/custom/stream` 整个 SSE 端点无前端调用者**（`api/export.ts:13` 的 `streamExportProgress` 零 import）→ 是「死代码 + 阻塞」双重问题，不是线上症状 |
| **P2-3** | 「关键模式段长 72,579 字节 / 占 40%」 | 那是**到文件尾**的长度（含 `## 待办事项`）；该段（`:694`~`:795`）实际约 **44.4KB / 24%**。核心结论（起点 110,696 > 64KB 截断线）**成立** |
| **P2-4** | 「124 个版本条目」「按年份切分」 | 带日期条目 **122 个**（另 2 个是正文小节）；且 122 条**全在 2026 年** → 按年切分只会切出一个文件 |

### C. 报告漏掉的新发现（5 处，本轮一并处置）

| 发现 | 说明 |
|---|---|
| 标题 id 有**第三处**实现 | `backend/app/services/markdown_render.py:95-114` 的 `add_heading_ids` 独立实现，注释自称「算法对齐前端 headingId」。P1-15 的「两处」实为**三处** |
| `rehype-slug` 是**死依赖** | 在 `frontend/package.json` 里，但全前端零使用（标题 id 由 `markdown-components.tsx` 自己注入） |
| Rust 侧另有 2 个**无监听事件** | `lib.rs:261/274` 的 `backend-restarting` / `backend-restarted`，与 P2-10 那两个同病 |
| **P0-2 的第二条污染路径：端口占用**（已修） | 7413 已被占用时 uvicorn 起不来，而 `start_server` 的判据是「`/health` 返回 200」→ 它会**连上用户正在跑的那个真实后端**，全程对着真实数据目录操作。症状是**所有断言照样全过**（比重启失败危险得多）。修法：起后端前先探端口，占用即拒绝（exit 2） |
| **P0-2 的第三条：pytest 收集阶段就在执行**（已修） | 文件名匹配 `test_*.py`，而顶层代码是**裸执行体**（非 `__main__` 守卫）→ 每跑一次 `pytest tests/` 就静默起一个后端 + 打几十个请求 + 写一次配置。`tests/__pycache__/test_e2e_smoke.cpython-3xx-pytest-9.1.1.pyc` 是它**确实被 pytest 收集过**的物证；而 CHANGELOG 里「该文件需已启动的后端，本轮跳过」的记录说明作者一直以为它不会被 pytest 跑到。修法：执行体收进 `main()` + `tests/conftest.py::collect_ignore` |
| 「不产生用例」有**两种**形态（P1-01 核实） | 报告说的 4 文件确为「模块级 `check()` 裸调用」；但**同目录另有两个文件是另一种形态** —— `test_export_print.py` / `test_docx_export.py` 把断言写在**模块级**（靠 `import` 执行），所以它们**不是 no-op**（跑了，真断言了），但 import 期一失败就是 **collection error，会中断整轮 pytest**。二者都该改写或至少知情：判据是「这个文件的绿，是自己跑出来的还是恰好没被跑」 |

---

## 二、35 项修复台账

**状态**：⬜ 待修 ｜ 🟡 进行中 ｜ ✅ 已修 ｜ ❌ 明确不做

**汇总（2026-10-06 收尾）**：35 项 = **32 ✅ 已修** / **3 ❌ 明确不做**（P2-01 ruff 接入、P2-04 CHANGELOG 切分、P2-11 字体子集化，均为用户决定，理由见 §四）。
另有纠偏 A 的 8 条改判修法、纠偏 B 的 6 条描述订正、纠偏 C 的 6 处报告漏项，全部随本轮落地。

### P0（5 项，批次 1）

| slug | 落点 | 修法要点 | 状态 |
|---|---|---|---|
| `P0-01-export-path-traversal` | `routers/export.py:684,749` | ✅ 抽 `core/path_guard.py::resolve_within`，四处调用点收口（export 收集 / export 内联 / `content_preparer.py:438` / `synthesis.py:411`）。⚠️ **四个来源分支全都能穿越**（报告只点了 `assets`，另三支的末段 `[^\s)]*` 同样吃得下 `..`）；条目名另做 basename 净化以堵 zip-slip（收集侧净化一次，正文改写与内联自动一致）。测试 `tests/test_export_assets_guard.py`（6 例）+ 反向自证（无守卫 `leaked=True` / 有守卫 `False`） | ✅ |
| `P0-02-test-real-config-pollution` | `tests/test_e2e_smoke.py` | ✅ 隔离三件套（`LIMO_CONFIG` + `LIMO_SECRETS` + **临时 config 内写 `data_dir`**——只设前两个时 Store 仍落 `~/.limo`，配置隔离了数据没有）；两条**有区分力**的隔离自证（后端写回临时 config / 临时 data 目录出现 `index.json`）+ 真实位置指纹比对；结果收口 `main()` 退出码。⚠️ 另修报告未点的**两条同源泄漏**（见 C 表）。机器上 `~/.limo/config.yaml` 的裸 `doc: [99999]` 已清除（备份 `config.yaml.manual-bak-20261006`，子场景 `doc:pdf` 等完好） | ✅ |
| `P0-03-index-corrupt-silent-empty` | `storage/store.py:82-99` | ✅ 三种「不可用」（文件缺失 / 内容损坏 / 旧格式 v<3）统一自愈：有文件先 `copy2` 出 `index.json.bak`，再**全盘重建**，warning 逐项列出降级内容。⚠️ 判据与 `_rebuild_index` **同源** —— 不是数「`docs/` 目录数」（**文档目录直接在 `root/` 下，并没有中间层** —— 报告的这句描述不成立），而是让它自己扫、扫出几篇算几篇（新增 `_rebuild_index(save=False)`）。零调用的 `StoreError.index_corrupt` 已删（它承诺的「正在自动重建」现在由这里真做，而作为 `recoverable=False` 的异常抛出去只会阻止启动）。测试 `test_store_index_recovery.py`（6 例：含「掐掉重建必红」对照 + 「正常索引逐字节不变」反向 + 「全新用户不凭空造空索引」） | ✅ |
| `P0-04-stream-bypass-promptbuilder` | `generation/prompt.py:888` + `routers/generate.py:283,507` + `generator.py:296` + `tests/test_generate_gate.py §7` | ✅ ① xlsx 分支加到 `build_notes_prompt` **顶部**（流式内联调用走它，`is_doc` 判定之前——xlsx **在** `DOC_SOURCE_TYPES` 里）；② **两条端点统一单产物闸门** `_single_product = source_type in ("composite", "xlsx")`；③ 删 `PromptBuilder` 死变量（零调用，只给「流式也走 PromptBuilder」的错觉）；④ `generator.py` 那条假注释（自称「与流式路径一致」而流式根本没有 xlsx 处理）改为成对说明。⚠️ **加码（报告与计划都没提）**：**非流式此前也在落两份盘** —— `generate_both` 的 xlsx 分支为满足双产物接口返回 `(sp, sp)` 两份**内容相同**的 View，而第二份前端**够不到**（`tabRegistry` 的 polished 展开源 `ALL_TEXT` 不含 xlsx，Excel 只有「笔记/数据预览/转录」三个 tab）。这条是新测试 **7a 抓出来的**（首次跑就红 `['notes','polished']`）。**新增 3 条断言**：7a（非流式）/ 7b（流式，含 prompt 走「数据分析师」）**各守一份分派实现**——实测临时废掉 `build_notes_prompt` 那段，**只有 7b 红**（非流式走 `PromptBuilder.build` 的另一份，互不掩护）；7c 对照普通网页仍跑 2 个产物、不被截胡。 | ✅ |
| `P0-05-ingest-error-envelope` | `api/ingest.ts:99,131` + 新建 `src/test/ingestErrorEnvelope.test.ts`（7 例） | ✅ 两处都改（主路径 + `fallbackJson`；后者只在流式失败时才走，只改前者的实现能让「SSE 正常时能报错、降级后不能报错」全绿）。⚠️ 修法要点：**判据与取值是两件事** —— 判据 `data.error \|\| data.code`（存在性，对齐 `generate.ts:78` / `transcripts.ts:26`），取值 `data.error \|\| data.message \|\| data.code`（人话优先，最后才退回 code）。⚠️ **不能拿 `message` 当判据**：「已导入过《…》」「内容与已有…完全相同」是**去重成功**，后端响应本来就带 `message`（`routers/ingest.py` 的 duplicate_url / duplicate_content 分支）——拿它判会把正常去重报成失败，等于把一个静默失效换成另一个。已写成测试第 3 组钉住。**对照实验**：两处改回 `data.error` → 3 条新信封用例全红、4 条（旧信封 + 成功响应）仍绿。 | ✅ |

### P1（19 项，批次 2 后端 10 项 / 批次 3 前端图表合规 9 项）

| slug | 落点 | 修法要点 | 状态 |
|---|---|---|---|
| `P1-01-tests-never-red` | `tests/test_engines.py` 等 4 文件 | ✅ 全部改写成 `def test_*` 真用例（实测 `grep -c "^def test_"`：engines 2 / adapters 2 / epub_pipeline 9 / plugin_loader 23）。此前是模块级 `check()` 裸调用 —— **`pytest tests/` 跑过去一片绿，因为它们根本不产生用例**（模式 #38：断言存在 ≠ 断言有效） | ✅ |
| `P1-02-plugin-config-not-restored` | `engines/plugin_loader.py:356,423` | ✅ `init_plugins` 读 `config["plugin_configs"]` 回填进 `adapter.plugin_config`（`:356` 取 / `:423` 写，带 `dict(...)` 拷贝防串用）；`enabled=False` 跳过 `exec_module`。⚠️ 见纠偏 B：报告的「`plugin_config` **无读方**」不成立（有 3 处读方），真缺陷是**启动路径从不回填** → 插件配置重启即丢。照「无读方」去删字段会删掉 stdin 契约 | ✅ |
| `P1-03-placeholder-entry-deleted` | `storage/store.py:228,384,500` | ✅ 见纠偏 A：加**就绪标记**（写侧 `:228/:384` 置 `ready=True`、`:500` 的占位条目置 `False`），只清理「非 ready **且**超时」的条目（`:685/:709`）。⚠️ **读侧一律 `entry.get("ready", True)`——缺键 = 就绪**：判据写反会让所有历史 index.json 被当成待清理的幽灵条目（与 P0-3「无头 ≠ 损坏」同一类陷阱）。报告建议的「文件不存在就不删」会让**真被删目录**的幽灵条目永久残留 | ✅ |
| `P1-04-default-config-polluted` | `core/config.py:531` | ✅ `user_provs.append(copy.deepcopy(dp))` —— 与姊妹函数 `_sync_models` 的 `copy.deepcopy(dm)`（`:456`）同源；浅拷贝下 `capabilities` 是 list，用户改一次就污染 `DEFAULT_CONFIG`（跨进程累积） | ✅ |
| `P1-05-atomic-write-fixed-tmp` | `storage/store.py:313,326` | ✅ 见纠偏 A：改 `tempfile.mkstemp(dir=path.parent)`（同目录唯一名）—— 固定 `.tmp` 名在两个进程同时写同一份产物时**互相截断**（症状是产物半截且无报错）。⚠️ **锁结构完全没动**：`self._lock` 是非重入 `threading.Lock`，而 `_save_index` 内部**也**调 `_atomic_write` 且恒在已持锁时被调到 → 照报告「纳入同一把锁」会**死锁** | ✅ |
| `P1-06-stf-return-dropped` | `ingest.py:111,829`、`generate.py:1323` | ✅ 三处 `save_transcript_full` 的 `bool` 返回不再丢弃（一律 `if not ...` 判定）。⚠️ 后果不是「少个返回值」而是**回显了没落库的值**：用户看到「已导入」而 store 里没有那份文档。修法是显式报错/降级，不是把返回值接着往下传 | ✅ |
| `P1-07-excel-phantom-sheet` | `generation/prompt.py:526,552,585` | ✅ 删幻影 `Sheet1`：工作表**只在见到 `## Sheet:` 时**才建（`:552`），无头数据（纯 Markdown 表格）才走兜底名（`:569-570`）；`rows`/`cols` 改 `.get` 防空表 `KeyError`（`:585`）。⚠️ 此前那条空壳永远拿不到 `rows`/`cols` → **100%** 走 `except` 兜底把整张表灌进 prompt（不是"偶尔失败"，是每一份 Excel 都命中） | ✅ |
| `P1-08-dual-product-partial` | `routers/generate.py:817,853` | ✅ per-product 失败改发 warning（`:817`）；删掉「一个全局 `status = partial if was_cancelled else complete` 喂两路」的写法（`:853` 注释）。⚠️ 与前端 `GenPanel.tsx:778` 的 `onWarning`（置 `genOutcomeRef='partial'`）**刻意不重复**（`:858` 注明分工）：后端管落库态，前端管展示态 | ✅ |
| `P1-09-compressor-skips-user-window` | `core/model_resolver.py:148` | ✅ 见纠偏 A：`resolve_context_window` **第 1 层**加「`provider_id` **且** `model_name` 匹配 `enabled_models`」支路（`:148-153`）—— 调用点语义不动。⚠️ 判据必须两者都匹配、不能只按名字（`qwen-max` 这类同名模型跨 provider 存在）；报告的「把 `model_id` 传进去，调用方手里就有」不成立：`LLMService` 只暴露 `provider`/`model`，且**按实际在用的模型查询正是设计意图** | ✅ |
| `P1-10-ingest-tmp-leak` | `routers/ingest.py:586,621,946` | ✅ 上传收成 `_handle_file_upload` 一份（`:586`）+ `_discard_upload` 统一清理（`:621`）进 `finally`；补**结束哨兵** `queue.put_nowait(None)`（`:946`，照 `synthesis.py` 做法，`run_pipeline` 的 `finally` 一定投递）。⚠️ 无哨兵时消费侧在异常分支**空等**到超时。**这一条直接消掉了机器上 `~/.limo/tmp` 的 221MB** | ✅ |
| `P1-11-custom-export-two-impl` | `routers/export.py:1129,1153` | ✅ 以 `export_custom` 为准收敛（**它才是前端在用的**，`ExportPanel.tsx:376`）：打包逻辑全收在 `_build_export_zip` 一份里（`:1153`），端点退化为**薄包装**（`:1129` 注释）。此前两份实现已分叉（空结果 400 vs 空 zip） | ✅ |
| `P1-12-export-zip-blocks-loop` | `routers/export.py:1386` | ✅ `await asyncio.to_thread(_build_export_zip, ...)`（`:1386`）。⚠️ 配套：`push` 回调改成**线程安全回调**而不是 `asyncio.Queue`（`:1169` 注释）—— worker 线程里 `put_nowait` 不能跨线程调 loop；取消改抛 `asyncio.CancelledError` 与调用方既有语义一致（`:1180`）。见纠偏 B：更要紧的是该 SSE 端点**前端零调用者** | ✅ |
| `P1-13-provider-settings-no-catch` | `settings/ProviderSettings.tsx:98` | ✅ 加 `guard(fn, label)` 包装器统一 catch + toast（`:98`）。⚠️ 见纠偏 B：「19 个处理器仅 6 处 catch」实为**处理器内只有 4 处**（另 2 处不在处理器里）—— 文件内注释已订正为实测数（`:87`） | ✅ |
| `P1-14-chatstore-rebuild-race` | `store/chatStore.ts:100,146` | ✅ `regenerate`/`editMessage` 补 `await`（后端 rebuild 落定后再发流式请求，`:100` 注释点明）；`deleteMessage` 补 `if (s.streaming) return`（`:146`）。⚠️ 漏 `await` 的形态是**流式请求抢在 rebuild 之前发出** → 读到旧消息树，且**不报错** | ✅ |
| `P1-15-heading-id-three-ways` | `lib/markdown-components.tsx:15-27` + `services/markdown_render.py:104-105` | ✅ 渲染侧改递归 `nodeText(node)`（`:15-27`，处理数组与 React 元素 `props.children`）—— 此前 `children.join('')` 对 React 元素产出 `[object Object]`；后端 `add_heading_ids` 先 `re.sub(r'<[^>]+>', '')` 剥 HTML + `html_lib.unescape`（`:104-105`），时间戳三条规则与前端 `stripHeadingDecor` **逐条对应**。**三处同源**（渲染侧 / 抽取侧 / 后端导出）。顺带删死依赖 `rehype-slug`（`package.json` 已无） | ✅ |
| `P1-16-color-guard-narrow` | `index.css:117`、`lib/onAccent.ts` + `test/onAccent.test.ts` | ✅ 见纠偏 A：新增 `--tk-on-accent` 前景 token（`index.css`）+ `lib/onAccent.ts` 推导函数，实底按钮引用它；守护判据改断**语义**（禁裸 hex / 禁非语义底色的黑白），**不断某个类名**。⚠️ 照报告「正则补禁 `text-white`/`bg-black`」会假阳性一片 —— 全仓 49 处里大部分是有意的（彩底白字、`bg-black/20` 遮罩） | ✅ |
| `P1-17-chart-asset-name-collision` | `services/chart_renderer.py:253-260` | ✅ 文件名改 `chart_{sha1(svg)[:8]}.png` —— **内容 hash 而非序号**。⚠️ 报告提的「版本前缀」不够：counter 每次生成都从 0 起，改一半的实现仍会撞名；hash 同时给了两个性质——**内容相同 → 同名**（幂等、不累积孤儿）、**内容不同 → 必不同名** | ✅ |
| `P1-18-chart-node-normalize` | `services/chart_layout/engine.py:99` | ✅ 见纠偏 A：归一化提到 `engine.expand` 的**语义校验阶段**（`:99`「语义校验 + 归一化，返回处理后的副本」），10 个算子一起受益、**不动任一算子的字段假设**。⚠️ 照报告「给 hchain/timeline/branch 补 `_norm_node`」会把 `_branch` 的崩溃换成**静默错图**（它依赖 `n['result']`/`n['result_sub']`，而归一化对字符串只产 `{"label":...}` → 右列结果框全空） | ✅ |
| `P1-19-abogus-gpl` | `video.py`、`downloaders/douyin_*`（已删）、spec、`THIRD-PARTY-NOTICES.md`、`pyproject.toml` | ✅ **删自研抖音路径**（用户拍板）：`douyin_downloader.py` + `douyin_helper/`（含 `abogus.py` 598 行，头部自称 GPLv3）已删、spec 两行 hiddenimports 已删、`THIRD-PARTY-NOTICES.md` 声明已改；⚠️ **连带清掉两个死依赖** `gmssl` / `pycryptodomex`（唯一使用方就是 abogus）。抖音回落 yt-dlp 通用 `_download`（只保留 UA/Referer 反爬头，那是 yt-dlp 的配置，不含 GPL 签名代码）—— **功能不丢，只是要下整段视频** | ✅ |

### P2（11 项，批次 4）

| slug | 落点 | 修法要点 | 状态 |
|---|---|---|---|
| `P2-01-no-static-check` | — | ❌ **不做**（用户决定；且自写扫描器覆盖面不等价，见纠偏 A） | ❌ |
| `P2-02-status-stats-stale` | `docs/STATUS.md:14-24` | ✅ **最小修复**（用户决定不写生成脚本）：手工校正 11 处为实测值 —— 后端 Python 文件 115→**116**（core 23→27 / downloaders 4→1）、前端文件 146→**167**、前端测试 30→**45** 文件 / 384→**503** 用例、后端测试 40→**57**、行数 29,572→**29,636**+CLI 1,356、进度表合计 113/116→**118/121** | ✅ |
| `P2-03-claude-md-overflow` | `CLAUDE.md` + 新建 `docs/patterns.md` | ✅ 拆出 `docs/patterns.md`（**65 条 / 45,153 字节**，`grep -c "^[0-9]\+\. \*\*"` 实测 65），`CLAUDE.md` 原位留指路段（分三组说明 + 「写代码前」触发条件），两处交叉引用路径改同目录。`CLAUDE.md` **184,292 → 140,753 字节**。⚠️ **未达计划验证表写的 < 64KB** —— 拆分前的前 693 行本身就有 110KB，模式段只占其中 44KB；如实记录，不谎报 | ✅ |
| `P2-04-changelog-452kb` | — | ❌ **不做**（122 条带日期条目**全在 2026 年**，按年切分只会切出一个文件） | ❌ |
| `P2-05-dead-code-cluster` | `store.py`、`core/concurrency.py`、`routers/generate.py` | ✅ 三处全删：① `store.py` 的 `_get_doc_dir = get_doc_dir` 私有名兼容别名 —— **注释自称「历史外部调用方（routers/export.py 等）仍按私有名调用」，全仓 grep 零命中**（注释描述的调用方不存在）；② `concurrency.py` 末尾 `def shutdown(self): pass`（零调用点）；③ `generate.py` 的 `GenerateStreamRequest` Pydantic 模型（零引用，且**字段漂移 7 个**：抄出 16 个而真实签名 23 个，`reference_ids`/`image_analyze`/`image_optimize`/`doc_direct`/`ocr_enabled`/`ocr_model_id`/`reasoning_effort` 从未出现）。⚠️ 第三处的处置是**删除而非同步**——它既不被 FastAPI 消费也不被 import，唯一作用是让人误以为「这里有校验」（模式 #51） | ✅ |
| `P2-06-write-agents-exit-code` | `limo_cli/cli.py:169` | ✅ 包**内层 try**，**不复用**外层那个 —— 外层 except 的文案是「无法打开本机库」，而这一支根本不碰数据源：文件系统写失败（权限/占用/磁盘满）被套上那句，用户会去查库路径，而问题在目标目录；未捕获时 traceback 裸奔 + 退出码 1（= `USAGE`，语义错） | ✅ |
| `P2-07-conversation-404` | `routers/conversations.py` + `ChatSidebar.tsx` + `chatStore.ts` + `ChatCore.tsx` | ✅ 见纠偏 A：**前后端同批**。后端 `rename`/`clear_context`/`delete` 三个端点补 `AppError(..., status_code=404)`（`delete` 此前**不查存在性**、无条件回 `{"status":"deleted"}`，而 `store.delete_conversation` 恒返回 True）；前端三个 handler 改**乐观更新 + 失败回滚**（此前 `renameConversation`/`deleteConversation` 不 await 不 catch → 改 404 后会变成未捕获 rejection）；⚠️ 另修报告未提的一处：`chatStore.clearContext` 的 `catch {}` 吞掉异常 → `ChatCore` **无条件弹「已清除上下文」**（对话不存在时是假成功，模式 #53），改为不吞、成功才弹 | ✅ |
| `P2-08-alibaba-merge-missing` | `routers/config.py:154` | ✅ 写侧补 `cloud.alibaba` 回填 —— 与 GET 侧 `_mask_fields(cfg["cloud"], ("dashscope", "alibaba"))` **成对**：GET mask 几个字段，写侧就得回填几个。此前只回填 dashscope 而 alibaba 一并被 mask → 用户「打开设置页 → 不改 → 保存」就把 `***` 写进 `cloud.alibaba`，**真实 key 无声丢失**（模式 #52） | ✅ |
| `P2-09-store-error-disk-full` | `core/errors.py::StoreError.write_failed` + `storage/store.py:326` | ✅ 新增 `write_failed(op, exc)` 按 errno 细分（磁盘满 / 占用 / 路径过长 / 没权限 / 未知），调用点 `_atomic_write` 的 except 收窄为 `OSError`。⚠️ **Windows 特例**：文件被占用是 `WinError 32` 而 `errno` 被一并给成 `EACCES`(13) → 只看 errno 会误判成「没有权限」，故 **winerror 优先**。「一律说磁盘空间不足」的代价不是不够精确，是**用户朝错误的方向行动** | ✅ |
| `P2-10-rust-dead-events` | `src-tauri/src/lib.rs` + `frontend/src/App.tsx` + 新建 `lib/installWarning.ts` | ✅ **一接一删**（按模式 #53「这条信息能不能驱动用户行动」分流）：① **接上** `backend-warning` —— Rust 一直在发、前端零监听（等于白发），而它描述的正是「后端可能起不来」的原因（路径含空格/非 ASCII/不可写）→ 换个安装目录就能解决，故新建 `lib/installWarning.ts`（无问题时返空串、不渲染横幅）+ `App.tsx` 监听 + 可关闭横幅；② **删** `backend-restarting`/`backend-restarted`（`restart_backend_sidecar` 是**同步**命令，前端 `await` 的 resolve 就等价于 restarted —— 这是同步命令的冗余第二出口）与 `get_install_path_diagnostics`（同一份诊断数据已由 `backend-warning` 出门，且那条出口现在真的有人听）。新增 `test/installWarning.test.ts`（6 例，逐条只改一位） | ✅ |
| `P2-11-font-25mb` | — | ❌ **不做**（CJK 子集化会让生僻字变豆腐块；woff2 需仓库没有的构建链） | ❌ |

---

## 三、验证纪律

每条修复必须配**有区分力的断言**（模式 #38）：把实现改回原样，该断言**必须变红**。
逐条的断言清单见 plan 文件；批次间跑：

```bash
cd backend  && ./.venv/Scripts/python.exe -m pytest tests/
cd frontend && npm test && npm run build && npm run lint
bash scripts/check-all
bash scripts/check-cli
```

---

## 四、明确不做（防下一轮重报）

| 项 | 理由 |
|---|---|
| ruff 接入（P2-1 的建议） | 用户决定；且与自写 AST 扫描器**覆盖面不等价**（见纠偏 A） |
| CHANGELOG 按年份切分（P2-4） | 122 条带日期条目**全在 2026 年**，按年切分只会切出一个文件 |
| 字体子集化（P2-11） | CJK 常用字表子集化会让导入的**生僻字**渲染成豆腐块；woff2 需仓库目前没有的构建链 |
| STATUS.md 统计脚本化（P2-2 的建议） | 需跑 vitest 才能取用例数，不适合塞进版本脚本；按最小修复手工校正 |
| 把资产解析收敛进 `core/markdown_assets.py`（P0-1 的建议） | 该模块 docstring 明确声明「**只放形态 + 成对改写**，路径穿越守卫留在调用方」，且 `ocr_preprocessor.py:21` 那份刻意更严不收 —— 塞进去会踩自己的设计边界 |
| 给 `chart_renderer` 放宽 except（P1-18 的建议） | 违背本模块「失败显式化」契约（见纠偏 A） |
| `TYPE_COLORS` 12 处硬编码、`SettingsPage.tsx:606/607` fallback | 第一轮已判「刻意保留」 |
| `cache_put`/`cache_get`、`clear_epub_task` | 第一轮已判「有意保留」（见外部报告 §5） |
