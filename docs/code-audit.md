# 代码质量与缺陷扫描（第一轮）

> **基准：`4174166`（VERSION `0.4.13`，2026-10-05）。**
> **本报告是 `4174166` 时点的快照，不随代码更新。** 引用任一条目前先核对其「证据」是否仍成立；修复完成后在对应条目追加 `✅ 已修 <commit>`；全部修复后本文件归档到 `docs/archive/` 或删除。
>
> 证据格式一律为 `文件:行号`，**每一条都在 `4174166` 上实际打开核对过**（`grep -n <符号名> <文件>` 与报告行号对表）。本报告不描述任何未提交状态——这是它行号可复现的前提。

## 开工前纠偏（2026-10-05，修复启动时）

修复开工前的探索阶段查出本报告 **3 处错误**，其中两处属「照着修会修坏」。证据均已实测核对：

| # | 原述 | 实际 | 处置 |
|---|---|---|---|
| 1 | **P1-09**「`engines/docling.py` 死文件，删」 | `engines/__init__.py:307` `from .docling import PyPdfium2Engine`，`:313-315` 实例化后 `EngineRegistry.register("doc", "pypdfium2", …)` —— **PDF 默认引擎住在这个文件里**。`_migrate_remove_docling`（`:96`）清的是旧 `DoclingEngine` 的绑定残留，与此无关 | **假阳性**，剔除（见文末）。但文件内的静默 `except` 另立 **P1-16**——文件是**活跃**的，问题比「死文件」严重 |
| 2 | **P0-04**「用 `search.py::rebuild` 修搜索幽灵」 | `rebuild` 是「DELETE 全表 + 全量重插」，且 `main.py:319` 启动时已在调用它（故它**也不是** P2-17 说的「零引用」——**同一条目里错了两次**） | **工具选重了**，不是工具不对：`rebuild` 能修掉幽灵（已删文档不在 `list_transcripts()` 里），只是拿全量重建干单条删除的活。改为「只修 `ingest.py` 的静默 except」 |
| 3 | **P0-03**「切到 KaTeX 图片管线」 | `_render_formulas_katex`（`export.py:100`）是给 **HTML** 导出用的，靠 `_get_katex_assets()`（`:838`）注入 `katex.min.js/.css` 在 **WebView 里**渲染 —— 服务端拿不到 SVG/PNG | **前提不成立**。经用户重新拍板：**移除该 UI 选项 + 完整删除 matplotlib 分支**，MD 保留 `$...$` 原文 |

**另两处补充**（探索期发现，原审计未覆盖或叙述不准）：

- **P0-01 扩写**：审计**漏了** `availableFormats` 的两端兜底不同源——`AVAILABLE_OPTIONS` 只有 8 个键（无 `epub`/`document`/`chat`/`image`），后端有 `text` 兜底（`prompt.py:683`）、前端 `constants.ts:91` 没有。**不并入则 P0-01 的修法本身会制造同款新 bug**（epub/document 的「公式集」勾选框会直接消失——正是 P0-01 要修的那个形状）。
- **P1-15 / P1-16 新增**：前者是 `supports_document` 零调用点（能力门未接线，三个同构门里唯一没接的）；后者是 `docling.py` 六处静默 `except` 的逐处 triage。

---

## 扫描方法（可复现）

三路并行普查 + 机械化扫描，判据全部来自项目自身积淀：

1. **把 CLAUDE.md 的 65 条「关键模式」反过来当判据**——它们本就是真实 bug 的沉淀，最高效的用法是找「哪里还可能违反」。
2. 三路 Explore 普查：① 枚举「白名单放行集」vs「下游可消费集」的分叉；② 失败显式化（静默妥协）；③ 同一契约写在多处的同源性。
3. 机械扫描：硬编码色值（铁律）、AST 死代码、`subprocess` flag、死依赖、门禁自动化。

**核实纪律**：Explore 报回的每条都做了机械二次核实。已剔除 **4 条**假阳性（见文末，第 4 条由 2026-10-05 开工前纠偏补入），并修正 **11 处**行号 / 路径错误——其中 `#4` 的 `except` 行、`#19` 的三处「偏 1」全是**引用粒度选错**（引了字典定义行而应引条目行），而非数字算错。

**条目计数**：P0 **4** / P1 **10** / P2 **11** = **25 项**（原报 24：剔除 P1-09 假阳性 −1、新增 P1-15/P1-16 +2；2026-10-05 实施时 **P1-15 降为 P2**，编号为免牵动多处引用而保留）。

### 「刻意为之」判据（跨条目统一）

扫描难免撞上「看起来是缺陷、实际是设计决定」的代码。判据不靠逐条感觉，写死为：

| 注释形态 | 处置 |
|---|---|
| 注释说明这是**设计决定**（「刻意」「不要改它」「占位，不是默认档」） | **剔除**，不进报告 |
| 注释只**承认现状**（「本端点目前前端零调用」「暂未使用」） | **保留为待办**（现状可被改动打破，不是设计意图） |

据此：`store.py:194` 剔除（设计决定）；`config.py:253` 保留为待办（只承认现状）。

---

## P0 — 用户可直接触发（4 项）

### P0-01-formulas-gating 公式集选项对 5 类文档不可见（**含审计漏项，两半必须同批修**）

**半 A —— 原报告项**

- **证据**：后端 `app/generation/prompt.py:666` 的 `AVAILABLE_OPTIONS` 给 docx / pptx / webpage / text / audio 五类都列了 `formulas`；前端 `components/gen-panel/GenerateOptions.tsx:220-221` 把 `formulas` 与 `references` 同包在 `p.typeMeta.isPDF && <>…</>` 块里；`GenPanel.tsx:594` 再硬编码一次 `if (f === 'formulas' || f === 'references') return typeMeta.isPDF`。
- **用户可见症状**：带公式的 Word / PPT / 网页文档，生成面板里**看不到「公式集」勾选框**——后端支持、前端挡住。根因是把 `formulas`（5 类可消费）与 `references`（仅 pdf）当成了一组。

**半 B —— 审计漏项：`availableFormats` 两端兜底不同源**

- **证据**：`AVAILABLE_OPTIONS`（`prompt.py:666-676`）只有 8 个键（video/audio/webpage/pdf/docx/pptx/xlsx/text），**没有 `epub`/`document`/`chat`/`image`**；后端取值走兜底 `prompt.py:683` = `AVAILABLE_OPTIONS.get(st, AVAILABLE_OPTIONS.get("text", []))`，而前端 `components/gen-panel/constants.ts:91` 是 `genOptions[st] || []`——**没有 text 兜底**。
- **用户可见症状**：`GenPanel.tsx:581` 提交时真按它过滤 `.filter(f => typeMeta.availableFormats.includes(f))`——「空表 ⇒ 勾了也被抹掉」这条通道**确实存在**，但 ⚠️ **两个受影响的类型今天都走不到**（2026-10-05 实施时实测修正）：
  - `document` **作为 sourceType 不可达**：`_adapter_to_source_type`（`ingest.py:400-420`）对 document 适配器**总是**细分出 `pdf/docx/pptx/xlsx/epub`（都不命中则兜底 `pdf`），**永不返回 `"document"`**；
  - `epub` 虽可达且确实无键，但格式区的外层条件（`GenerateOptions.tsx:214` 的 `sourceType !== 'epub'`）**根本不渲染**——没有可勾的项，也就无从被抹。

  故半 B 的准确性质是**防御未来的结构不一致**（后端加键 / 前端加类型时两侧不同源），**不是**「今天正在抹用户勾选」。原文把一条不可达的路径写成了现网症状。
  反过来说，**半 A 会把它变成真问题**：`formulas` 一旦改走 `availableFormats`，这两类若哪天进入渲染分支，空表会让「公式集」直接消失。
- **为什么必须并入半 A**：半 A 把 `formulas` 改成走 `availableFormats` 后，这两类的「公式集」勾选框会**直接消失**——后端支持、UI 隐藏，**正是 P0-01 要修的那个形状**。
- **修复方案**：`constants.ts:91` 与后端**同源兜底**（`genOptions[st] ?? genOptions['text'] ?? []`），或给 `AVAILABLE_OPTIONS` 补齐 epub/document 键。
- **危险度判据**：用户可直接触发（勾了等于没勾 / 勾不到）× 中（功能不可达且部分场景连提示都没有）。

### P0-02-style-cross-type 风格档跨类型静默失效

- **证据**：`components/gen-panel/GenPanel.tsx:50` 的 `useState(saved.style)` 只在挂载时读一次，切换文档类型不重置；对照同文件 `:172-180` 对 `selectedCore` **有**重置 effect。后端 `generation/prompt.py:912` 的 `lookup.get(style, "")` 对查不到的档静默吞。
- **用户可见症状**：PDF 选「IMRaD」→ 切到视频 → 生成 → 视频没有该档 → **风格节静默为空**，零报错、按钮也不高亮。
- **修复方案**：前端照抄 `GenPanel.tsx:172-180` 的 `selectedCore` 重置 effect（复用同一 `typeMeta` 模式）；后端在 `prompt.py:912` 查不到文案时按类型清洗并记 warning 兜底。
- **危险度判据**：用户可直接触发 × 中（产出质量静默劣化，用户不知道）。

### P0-03-md-formula-render 导出 MD「公式渲染为图片」从未生效

- **证据**：`app/routers/export.py:639-643` 的 `try: import matplotlib … except ImportError: return md`；而 matplotlib **不在依赖里**（`backend/pyproject.toml` 无此包）。UI 明确提供该选项（`components/ui/ExportPanel.tsx:258` 的 `<option value="render">公式：渲染为图片</option>`）。
- **用户可见症状**：导出 MD 得到原始 `$...$` 源码，界面不报错。HTML 路径被 KaTeX 兜住，**只在 MD 暴露**——最难察觉。
- **修复方案（2026-10-05 重定）**：原定「切到 KaTeX 图片管线」**技术上不成立**——KaTeX `renderToString` 只输出 HTML+CSS，客户端渲染，服务端拿不到 SVG/PNG（详见「开工前纠偏」#3）。改为**移除该 UI 选项 + 完整删除 matplotlib 分支**，MD 保留 `$...$` 原文（Markdown 里 LaTeX 源码本就是标准形态，符合「失败显式化，不做静默妥协」）。
- **⚠️ 落点必须列全（半删比不删更糟）**：删一半会让 `_latex_to_png` 里的 `plt` 失去定义 → `NameError` / `ModuleNotFoundError`。

  | 落点 | 位置 |
  |---|---|
  | 函数体 | `_render_formulas_to_images` `export.py:634-693`（`_latex_to_png` 在 `:645`，被 `:676` / `:684` 调用） |
  | 请求模型字段 | `formula_md` `export.py:1158` |
  | **4 对调用点** | `:1276-1277` / `:1281-1282` / `:1398-1399` / `:1403-1404`（`data.formula_md == "render"`） |
  | 前端 UI | `ExportPanel.tsx:258` |
- **危险度判据**：用户可直接触发 × 低-中（降级产物仍可读，但用户以为拿到了图片）。

### P0-04-search-ghost 删除文档后搜索幽灵

- **证据**：`app/routers/ingest.py:124-127` 的 `except Exception: pass`（连日志都没有）；同一段中 `_engine` 为 `None` 时同样静默。⚠️ 该 `try/except/pass` **压在一行**，本身即额外观察点。对照同文件批量删除路径 `:187-200`——**那侧有 `log.warning`**，可作写法参照。
- **用户可见症状**：删掉的文档**仍出现在搜索结果里**。
- **修复方案（2026-10-05 改小）**：改用 `core/search.py:61` 的 `rebuild(transcripts, get_content)` 是**工具选重了**——它是「DELETE 全表 + 全量重插」，且 `main.py:319` 启动时已在调用（详见「开工前纠偏」#2）。单篇删除的正解是继续用 `engine.delete(tx_id)`，**只把静默 `except` 改为记 warning**（含 `_engine` 为 `None` 的分支）。
- **危险度判据**：用户可直接触发 × 中（数据一致性，用户以为删掉了）。

---

## P1 — 静默失效 / 死代码（10 项）

### P1-05-style-fallback-split 非法 style 兜底双轨

- **证据**：`app/routers/generate.py`（流式路径）与 `app/generation/generator.py:39` 的 `_coerce_style` 回退值不同——前者落 `plain`，后者 `return Style(s) if s in Style._value2member_map_ else Style.DETAILED`。
- **用户可见症状**：同一非法输入，走流式与非流式两条路得到不同风格，且都不报错。
- **修复方案**：两处都改为调 `generation/prompts/notes_styles.py::normalize_style` 的返回值，收敛为单一真相。

### P1-06-synth-dim-warning 合成维度发现失败退回模板维度

- **证据**：`app/generation/synthesis.py:665` / `:668` 两个 `log.warning`（超时 / 异常各一支）→ `return list(DEFAULT_DIMENSIONS)`。
- **用户可见症状**：**服务端有 warning，但用户侧无感知，且产物上不可分辨**——失败后的维度集与「用户主动选了这个模板」在 meta 里长得一样。
- **修复方案**：把该降级写进产物 meta（对齐合成管道既有的「降级落进产物」纪律），复用 `core/transcript_meta.py` 的 meta 装配。

### P1-07-screenshot-marker 截图标记逐条失败残留原始标记文本

- **证据**：`app/generation/generator.py:85-86` 的 `except Exception as e: logger.warning("截图生成失败 ts=%ds: %s", ts, e)`——失败时只记日志，**`*Screenshot-[mm:ss]` 标记不替换、原样留在正文里**（`:84` 的 `markdown.replace(marker, …)` 只在成功分支执行）。
- **修复方案**：失败分支改为占位文本零残留——复用图表路径 `📊 图表生成失败（reason）` 的既有做法。

### P1-08-llm-presets-dead `llm_presets` 死配置

- **证据**：`app/core/config.py:28` 定义 + `app/core/config_io.py:64` 类型登记 + `tests/test_config_io.py:103` 断言其类型；**全仓无任何业务读取方**。
- **修复方案**：删除三处（先确认无历史 config 依赖该键）。注意与「单纯无人引用」区分：它有类型登记与测试，仍属死配置。

### ~~P1-09-docling-deadfile~~ 【已剔除，2026-10-05】

**假阳性**——`app/engines/docling.py` **不是死文件**，它是 `PyPdfium2Engine` 的宿主：`engines/__init__.py:307` 导入、`:313-315` 实例化并注册为 `("doc", "pypdfium2", …)`，即 **PDF 默认引擎**。按原文「删文件」= 删掉 PDF 主引擎。
原文另提的「`_extract_toc` 静默丢弃」**成立**，但那是**活跃引擎内部**的问题，已改立 **P1-16**（并扩为六处静默 `except` 的逐处 triage）。

### P1-10-resvg-dead-dep `resvg-py` 死依赖

- **证据**：`backend/pyproject.toml:52` 的 `"resvg-py>=0.4.0"`，全仓 **0 个 import**（PIL 自绘已替代，见 CLAUDE.md 图表一节）。
- **修复方案**：从依赖删除。⚠️ **`docs/ACKNOWLEDGMENTS.md` 的那一行要保留**——它不是残留声明，是**三方许可 + 沿革记录**（全表都把已移除的库留着：tesseract / docling / html2text / ebooklib 都在），且它**正是本项的证据出处**（该行自己写着「⚠️ pyproject.toml 里 resvg-py 的依赖声明仍残留…待清理」）。只需把那句 ⚠️ 更新为「已清理」。**在许可/合规文件上做删除，是那份文件里最该谨慎的一类改动。**

### P1-11-id-length-drift id 校验长度上限漂移

- **证据**：写入侧 `app/routers/ingest.py:930-934` 的 `_validate_tx_id` 只校验字符集 `^[a-zA-Z0-9_\-]+$`，**无长度上限**；读取侧 `app/services/query.py:234` 用 `core/resource_id.py:29` 的 `is_valid_resource_id`，它含 `MAX_ID_LEN = 64`（`:26` / `:39`）。
- **用户可见症状**：写路径接受超过 64 字符的 id，读路径拒绝——同一个 id 写进去却读不出来。
- **修复方案**：写入侧换成 `core/resource_id.py::is_valid_resource_id`（该模块 docstring 已回答「写路径允许拒绝吗」这个前提）。

### P1-12-filename-len-drift 导出文件名净化阈值漂移

- **证据**：后端 `app/routers/export.py:264` 的 `_safe_filename` 截 `[:80]`；前端 `components/ui/ExportMenu.tsx:92` 与 `ExportPanel.tsx:108` 各截 `.slice(0, 60)`。
- **用户可见症状**：前端预览的文件名与后端实际落盘的文件名在 60–80 字符区间**不一致**。
- **修复方案**：单一常量来源——要么前端对齐 80，要么后端下发；勿留两份。

### P1-13-filter-missing-audio 前端筛选行缺 `audio`

- **证据**：`components/doc-library/docMeta.tsx:14` 的 `SOURCE_FILTERS` 无 audio 项，而 `app/core/types.py:15` 的 `SourceType.AUDIO = "audio"` 是真实类型。
- **用户可见症状**：音频文档无法单独筛选。
- **修复方案**：`SOURCE_FILTERS` 补 audio 项。

### P1-14-detectmodels-dead-endpoint `detectModels` 死端点

- **证据**：前端 `api/settings.ts:22` 定义了 `detectModels` 但**无调用方**；后端 `app/routers/config.py:219` 的 `detect_models` 仍在；`:234` 的 provider 默认表只剩 `{"deepseek", "openai"}` 两家——**其余 6 家会抛 `DETECT_NO_BASE_URL`**（对照 `routers/model.py:63-93` 的 6 家）。`:253` 注释自认「本端点目前前端零调用……死代码若被复活，不该带着一份不一致的判据回来」。
- **用户可见症状**：当前是死路径，**接回即发作**——那 6 家的模型探测会直接报错。
- **修复方案**：接回则先补 provider 默认表——**复用 `PROVIDER_DEFAULTS` 而非再抄一份**；不接回则连同前端一并删除。⚠️ **落点应在 `core/`**：`PROVIDER_DEFAULTS` 现在住 `routers/model.py:63`，被 `routers/config.py` 复用即 router→router import——不成环，但与项目已确立的做法（共享常量/判据放 `core/`：`resource_id.py`、`token.py`）不一致，与 P2-23 同一手法下沉。
- **判据**：按「刻意为之」判据属**保留为待办**（注释只承认现状）。
- **备注**：这是早先单列的「`detectModels` 死端点」与「provider 默认表只剩 2 家」两条的合并——同一条死路径的两面，分开计数会抬高总数。

### P1-15-supports-document-unwired `supports_document` 是**判据的第二份副本**（⚠️ 实施时降为 **P2**）

> **⚠️ 2026-10-05 实施时降级并改方案。** 原文「能力门**从未接线**、直调 API 可绕过」的**前提不成立**：后端 gate **存在且有效** —— `content_preparer.py:307-310` 对 `llm_config["capabilities"]` 做校验，未标 `document` 则记 warning 并**返回 `None`（保持文本模式）**；而 `tests/test_doc_understand.py:191-202` 对**三态都有实测覆盖**（未标 → 文本模式 / 标了 → 直传 / 无原件路径 → 文本模式）。直调 API 传 `doc_direct=true` 会被这一层挡下，不存在「模型什么都看不到」的路径。
>
> 真正的性质是**同一个判据被写了两份**：`LLMService.supports_document`（`service.py:115/120`）**零调用点**，是纯死属性；真正干活的是 `content_preparer` 里的那份。这是模式 #54「同一契约写在两处、没人用的那处不报错」的形态，**不是** #51「能力存在但该路径没在用」。
>
> **动作因此改为「收敛为单一真相源」，绝不补报错 gate** —— 现有行为是**降级为文本模式**（用户仍拿得到产物）；补一个「显式报错」等于把一次可用的降级变成一次失败的请求，是**行为倒退**。

- **证据**：`app/llm/service.py:120` 的 `supports_document`（属性定义 `:115`）**全仓零调用点**——仅有的两处提及是它自己的 docstring 与 `core/config.py:54` 的一句注释。同构的另两个能力门都**确实接线**：`supports_vision` 在 `service.py:202` + `routers/process.py:145`；`supports_reasoning` 在 `generation/generator.py:173/267/298-300` + `routers/generate.py:828/833/979`。
- **修复方案（已改）**：把两处判据收敛为**一处**，二选一——(a) 让 `content_preparer` 调 `LLMService.supports_document`；(b) 删掉该属性。**倾向 (a)**：能力位判据住在 `LLMService` 上才与它的两个兄弟一致。
- **危险度判据（已改）**：无用户可见症状 × **维护性**——两份判据会漂移（改一份、漏一份，且**不报错**）。

### P1-16-pdf-silent-excepts `docling.py`（活跃 PDF 引擎）内六处静默 `except` ⭐新增

- **证据**：`app/engines/docling.py` 实有 **6 处** `except Exception`，逐处 triage：

  | 行 | 场景 | 判定 |
  |---|---|---|
  | `:44` | pdfplumber 打开失败 | 表格能力降级，**可接受**（保留，补一行注释说明） |
  | **`:88`** | 单张图片嵌入失败 | **修**——静默丢图：PDF 里的一张图在产物上凭空消失，用户零提示（与图表路径的失败显式化纪律相悖） |
  | `:118` | `_pp.close()` 失败 | **可接受**（清理路径） |
  | `:133` | `_extract_toc` → `return None` | **可接受**（无目录）。⚠️ **不是 `pass`** |
  | `:160` | `_paragraph_gaps` → 静默 `[]` | 判定：段落间距丢失不影响正文，**可接受** |
  | **`:182`** | 表格抽取 → 静默 `None` | **修**——与 `:88` **同病**：PDF 里的一段内容凭空消失、用户零提示 |

- **修复方案**：`:88` 与 `:182` 改为记 `log.warning`（带文件名 / 页码定位）；其余四处保留但补注释说明「有意降级」，使其在下次扫描时不被误报。
- **⚠️ 报告原文的「4 处 `except Exception: pass`」描述不准**（实际 6 处 `except Exception`，其中 `:133` 是 `return None` 不是 `pass`，`_131` 实为 `:133`）——纠偏一并改。

---

## P2 — 无机制 / 重复实现（10 项）

### P2-15-engine-labels-unread `engineLabels` 下发但前端无人读

- **证据**：`app/routers/capabilities.py:323` 下发 `engineLabels`，前端 `api/core.ts:176` 声明了该字段但无消费方。
- **修复方案**：前端接入或后端停发。

### P2-16-hardcoded-colors 硬编码色值违反铁律

- **证据**：
  - **真缺陷**：`accent-[#007AFF]` 3 处（`components/settings/PluginSettings.tsx:62`、`SettingsPage.tsx:252`、`components/ui/ExportPanel.tsx:177`）——**换配色后复选框永远是蓝色**；`index.css:179` 的 `.r-handle` 用 `rgba(0,122,255,.1)`（同规则块里已在用 `var(--tk-accent)`）；`components/ui/LogPanel.tsx:112` 的 `bg-[#1E1E1E]` 终端底色。
  - **⚠️ 非缺陷，按判据剔除（2026-10-05）**：`docMeta.tsx:38-51` 的 `TYPE_COLORS` 12 处。设计声明在**文件头 docstring `docMeta.tsx:5`**（不在 `:38` 定义旁），逐字写着「它们是**类型标识色**（对标 GitHub 的 language color），不是主题色：它们**刻意**不随六轴主题变化，跨主题保持可辨识」→ 属「设计决定」。⚠️ **引用粒度**：依据在 `:5` 而非 `:38`——按 `:38` 找不到声明，正是报告自己承认的「引用粒度选错」。
  - **保留**：`SettingsPage.tsx:606` 的 `|| '#007AFF'` 与 `:607` 的 `|| '#F4F1EE'`（同为 fallback 默认值，非主题色硬编码）。
- **修复方案**：`accent-[#007AFF]` → `accent-accent`（项目已在用，见 `GenerateOptions.tsx:198`）；`.r-handle` 与 LogPanel 底色 → `--tk-*` token。

### P2-17-dead-functions 死函数

- **证据**：AST 扫描（已排除装饰器注册的路由/CLI 命令，否则大量假阳性）。确认零引用 **20 个**，代表性样本：`app/generation/product_registry.py:43` 的 `get_product` / `:48` 的 `resolve_order`（模块公开面，内部只用 `PRODUCTS` / `to_capability_list`）；`app/storage/store.py:1075` 的 `cache_put` / `:1084` 的 `cache_get`（存储门面契约）；`app/adapters/image.py:112` 的 `_ocr`；`app/agent/session.py:49` 的 `get_history`；`app/agent/tool_registry.py:31` 的 `get_defs` / `:45` 的 `get_fn`；`app/core/errors.py:87/91/95/104/108/112/190/207` 的 8 个 classmethod 工厂（错误码目录=公共面）；`app/downloaders/douyin_helper/abogus.py:409/452/459/504` 的抖音签名移植残留；`app/storage/store.py:1060` 的 `clear_epub_task`。
- **⚠️ 修正（2026-10-05）**：原文把 `core/search.py:61 rebuild` 列为「零引用」是**错的**——`main.py:319` 启动时就在调它（与 P0-04 纠偏同源，见「开工前纠偏」#2）。
- **⚠️ 路径修正（2026-10-05）**：`abogus.py` 的实际路径是 `backend/app/downloaders/douyin_helper/abogus.py`（原报告写 `app/engines/abogus.py`，**该路径不存在**）；`image.py` 补全为 `backend/app/adapters/image.py:112`。**删代码的项里，路径错比行号错更危险。**
- **修复方案**：按「删确凿的、保留公共面」逐类处置——
  - **删**：抖音残留 4 个 + `adapters/image.py:112 _ocr`；
  - **保留**：`errors.py` 工厂（错误码目录=公共 API 面）、Store 门面方法、`product_registry` / `tool_registry` 公开接口；
  - **接回**：`store.py:1060 clear_epub_task`——`save_epub_task:1041` / `load_epub_task:1050` 同在，load 在用、clear 无调用方 = **真缺口**（取消 epub 解读时应清 task 文件）。
- **备注**：`llm/service.py:120 supports_document` 已升为 **P1-15**（它不是「多余」，是「未接线」，性质不同）。

### P2-18-asset-regex-6copies `assets/` 图片引用重写正则 ≥6 份副本

- **证据**：`app/storage/store.py:36`（`_RE_ASSET_REF`）/ `app/generation/synthesis.py:245`（`_ASSET_REF_RE`）/ `app/generation/ocr_preprocessor.py:21`（`_RE_IMG_REF`）/ `app/generation/content_preparer.py:413`（内联 `asset_ref_pat`）/ `app/routers/export.py:695`（`_ASSET_RE`；⚠️ **同文件 `:697` 是旧格式 `_IMG_RE`，别指错**）/ `app/routers/markdown_edit.py:44`（反向 `_unrewrite`）——捕获组布局各异。
- **修复方案**：抽公共正则（项目「同一逻辑 ≥3 次即抽」纪律）。

### P2-19-style-alias-4places `meeting_minutes→meeting` 别名兼容 4 个落点

- **证据**：`app/generation/prompts/notes_styles.py:136`（别名条目 `"meeting_minutes": "meeting"`）/ `components/gen-panel/constants.ts:97`（前端别名条目）/ `components/gen-panel/genPrefs.ts:91-92`（localStorage 迁移分支——**是迁移分支不是别名表**）/ `app/routers/markdown_edit.py:79`（读侧归一，调 `normalize_style`）。前三者是**三种不同形态**，故第 4 处（读侧）最易漏。
- **修复方案**：前端两处合并为一份（`constants.ts` 表 + `genPrefs` 引用它）；后端保持表 + `normalize_style` 双方向。

### P2-20-ocr-labels-handcopy OCR 四模式中文标签手抄 6 份

- **证据**：`components/gen-panel/ImageModeCards.tsx:14-17`（label + desc）/ `components/gen-panel/GenPanel.tsx:1019`（内联）/ `components/preview/ImageResultView.tsx:10`（`MODE_LABELS`）/ `components/preview/PreviewArea.tsx:745`（内联 `modeLabels`）/ `components/preview/ProcessingPlaceholder.tsx:9`（`MODE_LABELS`）/ `lib/tabRegistry.ts:207-210`（label + suffix）。
- **修复方案**：抽单一常量表。

### P2-21-style-fallback-mismatch 风格 fallback 三组数组与后端 key 集不一致

- **证据**：`components/gen-panel/GenPanel.tsx:146-151`（`styleKeys` 的三组兜底数组）vs 后端 `notes_styles.py` 的真实 key 集；仅在后端未起 / 旧后端时（capabilities 拿不到）触发。
- **修复方案**：改为从 capabilities 下发，不在前端留第二份 key 集。

### P2-22-engine-cats-dual `ENGINE_CATS` 同文件双写（类型还不同）

- **证据**：`app/routers/capabilities.py:158` 的 `ENGINE_CATS = {"asr","ocr","doc"}`（**set**） vs `:361` 的 `cats = ["asr","ocr","doc"]`（**list**——⚠️ 变量名是 `cats` 不是 `ENGINE_CATS`，grep 后者会在 `:361` 扑空）。
- **修复方案**：合并为一个常量。

### P2-23-slot-width-triple 面板默认宽度 260 三处

- **证据**：`store/uiSlice.ts:101` 的 `DOC_LIB_DEFAULT_WIDTH = 260` + `:109` 的 `genPanel: { width: 260, … }`（内联第三处）vs `lib/slotRegistry.ts:83` / `:95` 的两处 `defaultWidth: 260`。`uiSlice.ts:99` 注释自认「改一处必须改另一处」——因循环依赖不能 import。
- **修复方案**：下沉到零依赖模块供双方 import。

### P2-24-no-ci 无 CI

- **证据**：无 `.github/workflows`——39 个后端测试 + 30 个前端测试文件 + 16 项 CLI 门禁全靠人记得跑。
- **修复方案**：加最小 workflow，跑 `pytest` + `npm test` + `scripts/check-cli`。

---

## 已定方案（用户选定）

| 条目 | 方案 | 状态 |
|---|---|---|
| P0-01 公式集可见性 | `formulas` 与 `references` 拆开，改由 `typeMeta.availableFormats` 驱动门控，与后端 `AVAILABLE_OPTIONS` 同源；**同时补齐两端兜底同源**（半 B，否则本修法制造同款新 bug） | 2026-10-05 扩写 |
| P0-03 公式渲染为图片 | ~~切到 KaTeX 图片管线~~ → **移除 UI 选项 + 完整删除 matplotlib 分支**，MD 保留 `$...$` | 2026-10-05 重定（原前提不成立） |
| P0-02 风格档 | **前端条件重置 + 后端按类型清洗**。⚠️ 是**条件**重置——**仅当档位不在新类型的可用集里才重置**；无条件重置会冲掉跨类型共享的合法档（plain / academic / business） | 2026-10-05 明确 |
| P2-17 死函数 | 「删确凿的、保留公共面」（用户拍板） | 2026-10-05 定 |
| P2-24 CI | 加最小 GitHub Actions（用户拍板） | 2026-10-05 定 |

## 被剔除的假阳性（记录以免复报）

| 曾报 | 剔除理由 |
|---|---|
| `store.py:194` index 重建写死 `"detailed"` | 该处**有注释说明是「无法还原时的占位，不是默认档」**——属「设计决定」，按判据剔除 |
| 13 处 `subprocess` 缺 `CREATE_NO_WINDOW` | 逐点核实 **11/11 全带**；先前计数把类型注解 `tools.py:25` 当成了调用点 |
| 「默认 style 四处不一致」 | 分属不同语义层次（默认值 vs 无法还原占位），非同一件事 |
| **原 P1-09：`app/engines/docling.py` 是死文件** | `engines/__init__.py:307` 导入、`:313-315` 实例化并注册为 **`("doc", "pypdfium2")`——PDF 默认引擎**。删了 = 删掉主引擎。文件内的静默 `except` 已另立 **P1-16** |

## 实施阶段新证实的报告错误（2026-10-05，动手修 P0 四项时）

除「开工前纠偏」那三处外，真正开始改代码后又核实出 4 处不符 —— 记录在此，**同一份报告不要被后来的读者当成可信基线**：

| # | 报告原述 | 实际 |
|---|---|---|
| 1 | P1-15「`supports_document` 能力门**从未接线**，直调 API 可绕过」 | 后端 gate **存在且有效**（`content_preparer.py:307-310`：未标 `document` → warning + 保持文本模式），`test_doc_understand.py:191-202` **有三态实测**。真相是**判据的第二份副本**（`service.py:120` 零调用点）→ 降为 P2，动作改为「收敛为单一真相源」，**不补报错 gate**（那会把现有降级变成失败 = 行为倒退） |
| 2 | P0-01 半 B「epub/document 用户勾的格式被静默抹掉（**今天就已存在**）」 | 通道成立，但**两个类型今天都不可达**：`document` 作为 sourceType 不可达（`_adapter_to_source_type` 永不返回它）、`epub` 被格式区外层条件排除 → 性质是**防御未来结构不一致**，不是现网症状。**半 A 才会把它变成真问题** |
| 3 | P0-03 落点写作 `app/export.py` | 实为 `app/routers/export.py`。**删代码的条目里，路径错比行号错更危险**（照原文去删 = 找不到文件 = 有可能去改另一个同名概念的位置） |
| 4 | 「后端测试全绿」（隐含前提） | `test_folders_api.py::test_batch_tag_and_delete` **全量跑时失败、单独跑通过**。根因：`_setup()` 注入临时 `Store` 却**漏注入 `search_engine`** —— 它的赋值在 `main.py` 的 startup event 里，而该文件刻意不进 TestClient 上下文，于是拿到**先前某个跑了 startup 的测试留下的、指向已删 tempdir 的实例** → `unable to open database file` → `BATCH_ALL_FAILED`。**既有缺陷，非本轮引入**（`git stash` 后复现确认），已随手修（`_setup` 补 `SearchEngine(d)`） |

⚠️ 第 4 条的意义超出它本身：**「全绿」这个门禁判据此前不可信，而每批提交都依赖它判定**。修好后后端基线 145 → **148** passed（含 P0-04 新增 3 条）；前端 **411** passed / 31 文件。

## 修复时的验证建议

本报告不含代码改动，故本轮未跑门禁。修任一条目后，按项目既有门禁验证：

- **后端**：`cd backend && ./.venv/Scripts/python.exe tests/<相关测试>.py`；涉及存储 / 契约的改动跑全量 39 个测试文件（含 `test_e2e_smoke.py`，需后端在跑）。
- **前端**：`cd frontend && npm test`（397 passed / 30 文件）+ `npm run build` + `npm run lint`。
- **CLI 链**：`bash scripts/check-cli`（16 项，自启 7419 端口）。
- **对照实验**：P0 各项建议先复现症状（如 P0-01 打开 Word 文档生成面板看有无「公式集」），修完再复跑同一动作确认。

### 本轮修复新增的必测项（2026-10-05）

| 项 | 断言 |
|---|---|
| P0-01 | **双向对照**：epub 勾「目录」→ 提交 formats 含 `toc`；document 勾「公式集」→ UI 可见**且**提交带上；PDF 两者皆真 |
| P0-02 | 切类型后 style **条件重置**（共享档如 plain 不被冲掉）；`describe.each` 逐类型覆盖 |
| P0-03 | `ExportPanel` 无该选项 + **`formula_md` 的 `render` 取值已不存在于任何请求模型/调用点** + 后端无 matplotlib 分支 |
| P0-04 | 删除文档后 `/api/search` 不再命中；**且** `engine` 为 `None` 时有 warning（对照实验：改回 `pass` 必红） |
| P1-15 | ~~直调 API 显式报错~~ → 后端 gate **已存在且有测试**（`test_doc_understand.py:191-202` 三态）。新断言：收敛后 `supports_document` 与 `content_preparer` 的判据**只应存在一处**（AST 源码守卫，模式 #54） |
| P1-16 | 造一张嵌入失败的图 → 断言 warning 被记录（模式 #38：断言必须有区分力） |
| P2-22 | 三处 `ENGINE_CATS` 集合相等（AST 源码守卫，模式 #54） |
