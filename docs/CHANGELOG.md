# 修改记录

> 记录每次重要修改的**做了什么**、**为什么**、**影响范围**。
> 不只是"修了 bug"，更是"当时为什么这样设计，后来为什么改了"。
> 按时间倒序，最近的在最上面。

---

## 2026-10-29 — 检索重排（rerank）：全套落地但**默认关**，要不要开交实验裁决

**起因**：`_experiments/05` 那张表的读法是 —— **等权 RRF 是用 H@5 的召回换来的 H@1 稳定性**。
H@50 已经 100%（好东西全在候选池里），H@1 却掉到 52.3%、与纯字面并列最低；两个方向
逐题可见：字面把向量给的 2 名拖到 12 名，向量把字面给的 2 名拖到 15 名。

⚠️ **这不是「只能上 cross-encoder」的局面** —— `0.8/1.2` 加权零延迟零费用就把 H@1 抬到
63.6%。所以本轮的目标不是「装上就算改进」，而是**把重排做成可测的一臂**：它治的是
「排第一那篇不对」，**不动召回**，因此 **H@50 必须逐字不变**（这是实验的第一条自证不变式）。

### 改了什么

**① 第三族插件基类 `RerankPlugin` + 协议 `engines/rerank_base.py`**

契约是「一个查询 + 一组候选 → 一组 `[原始下标, 分数]`」—— `EnginePlugin` 的
`process(file_path)`（一个文件 → 一段文本）装不下，`EmbeddingPlugin`（一组文本 → 一组
向量）也装不下。常驻子进程的理由同 embedding：本地重排权重 544MB，冷读几十秒，撞
`RERANK_TIMEOUT = 5.0` 会每次都超时。

三条契约（`test_plugin_rerank.py` 逐条钉）：**`results` 的第一个数是「入参下标」不是
「结果里的新位置」**（搞反 = 整张排名表错位，而每行看起来都合法 ⇒ 越界/重复必须**显式抛**）/
**空输入返回 `[]` 且连子进程都不拉起** / **允许只返回前 k 条**（尾部由调用方按原序补齐，
**不许补假分数凑满**）。

`precheck` / `warmup` / `release` 同 embedding —— **一律 `getattr`，不进 Protocol**
（`runtime_checkable` 加成员会让云端引擎不再满足）。`dimension` / `capabilities` 也刻意不放：
前者 rerank 没有维度、后者没人读。

**② 云端引擎 `engines/rerank_dashscope.py`（`gte-rerank-v2`）**

- **用 `AioTextReRank` 不用 `TextReRank`** —— 同步版走 `requests`，在事件循环里调它会把
  SSE 心跳与 worker 的 await 一起阻塞。
- **先判 `resp.status_code` 再碰 `resp.output`** —— 非 200 时 SDK 的 `from_api_response`
  else 分支**根本不给 `output` 赋值**，直接读是 `AttributeError` 而不是可读的服务端错误。
- `max_documents = 500` 是**实测的服务端硬上限**（500 条 200 / 501 与 800 条 400），
  不是本地保守值 —— 计划里写的「执行时递增探测」探出来的就是这个数。

**③ `SearchEngine.bodies(ids)` + `core/search.py::rerank_window`**

取候选正文走**新的批量查询**，**刻意不让 body 搭 `search_resources` 的 `Page` 车**：省下的
只是 50 个 id 的 `IN`（FTS 表上 <1ms，旁边就是几百毫秒到几秒的网络调用），换来的是
「`Page` 形状随参数变」+「必须靠测试守住的正文泄漏点」。**结构上不可能泄漏 > 省 1ms。**

⚠️ **选窗口绝不能复用 `_make_snippet` 的定位逻辑**（本条最要命的坑）：它 `re.split(r"\s+")`
后逐词 `body.find`，中文口语查询**没有空格** ⇒ terms 是一个整句 ⇒ 必然失败 ⇒ **窗口恒等于
正文开头**，而长文档的答案通常不在开头。后果不是报错，是**系统性压低实测收益**，最坏让
阶段 5 得出「rerank 没用」的**假阴性**。`rerank_window` 改用 `_tokenize` 的二字组算**覆盖
不同 token 数最多**的 480 字窗口（判据是覆盖数不是命中次数 —— 同一个高频词出现十次不说明
这段更相关）。

**④ 编排 `services/rerank_search.py` + 插点**

`RERANK_TIMEOUT = 5.0`（在线检索不能等 SDK 默认的 300s，那是批处理级）/
`RERANK_LOAD_TIMEOUT = 60.0`（本地 544MB 比 embedding 的 22.8MB 大得多，不能沿用 30s）/
`DEFAULT_CANDIDATES = 50`。**不重试** —— 重试把延迟和费用翻倍，而失败语义已经明确。

`fused_search` 加 `reranker=` 参数，**两条分支都覆盖**（单查询与多查询）。多查询分支原来
是 `rrf(...)[:limit]` 先截再返回 —— 不改的话候选池只剩 `limit` 条，重排等于没做。

⚠️ **`rerank` 是返回值的顶层键，不塞进 `semantic`**：那会让「向量路失败」与「重排失败」
共用同一个 `error` 字段，而两件事用户该做的动作不同。四态：`null`（压根没开）/
`{error: null, applied: true}` / `{error: null, candidates: 0|1, applied: false}`（候选不足）/
`{error: "...", applied: false}`（失败或超时，**保留 RRF 原序**）。

**⑤ 配置 + 运行时 + 端点 + 前端**

`config["search"]` 追加 `rerank_enabled`（**默认 `False`**）/ `rerank_model` /
`rerank_candidates`；`services/rerank_runtime.py::sync_rerank_runtime`（形态照
`vector_runtime`）；`POST /api/search/rerank`；`GET /api/search/status` 增 `rerank` 段。

⚠️ **入口严、读回宽**：端点对非法 `model` 报 400 `RERANK_NO_ENGINE` 且**零写盘**；但
`candidates <= 0` 夹到 **1**（错的是「量」不是「性质」），而磁盘上的脏值回退
`DEFAULT_CANDIDATES = 50` 而**不是 1** —— 「用户刚提交的值」与「磁盘上可能过期的值」是两回事，
后者落回 1 会让功能在、效果没了。

⚠️ **只挂对话侧**（判据与 `query_rewrite` 逐字相同）：`/api/search` 是 250ms 防抖逐字联想，
接了 = 打五个字发五次重排请求 × 50 篇 × 480 字。写进设置页文案**不是防线**。

**⑥ 引擎族原子变更**

`ENGINE_CATEGORIES` 加 `rerank` + `_PluginRerankAdapter` + `plugin_loader` 的
`_RESIDENT_ADAPTERS` 分派 —— **必须同批**：只交付插件模板而不加分派，用户把模板拷进
`{data_dir}/plugins/` 的那一刻它会被以 **doc 契约静默注册成功**（形状合法、零报错，而每次
调用都在要求一个它永远收不到的文件路径）。

### 影响范围

- **零回归是结构保证**：`reranker=None` 时 `fused_search` 的返回值与改造前逐字节相同，
  `rerank` 恒为 `null`；配置默认关，回退成本 = 一行配置。
- 新增 `/api/search/rerank` 端点（99 → 100）；`GET /api/capabilities` 的 `engines` 多一个
  `rerank` 组 —— 前端各处按具体 key 取，**无遍历全部 key 的消费方**。
- ⚠️ **要不要默认开、加权 `0.8/1.2` 要不要进产品，交 `_experiments/07-rerank/` 四臂实验
  裁决**（A0 等权 / W 加权 / B 等权+rerank / B+W）。**只看 A0 vs B 会把「排序本来就该调」
  那 11.3pp 算成 rerank 的收益**，结论会人为指向「必须上模型」。实验是门禁不是可选项；
  若 B+W 不比 W 高，结论就是「先采纳加权、rerank 缓做」，必须如实记录。

---

## 2026-10-29 — RSS / Atom 与 arXiv 导入：都不新增适配器类型

**起因**：梯队一两项导入源，成本都在「复用现有管线」这一档。

### RSS / Atom —— 走 `WebpageAdapter` 的内部分支

**不新建适配器**：`WebpageAdapter.accept()` 接受任何 http URL，新适配器与它**必然打架**
（同一 URL 两个 `accept()` 都返回 True，谁赢取决于注册顺序）。走内部分支还能**免费继承**
SSRF 防护 / 逐跳重定向校验 / 编码处理。

- `_download()` 签名从 `-> str` 改成 `-> tuple[str, dict]` —— 原先只 `return resp.text`，
  **响应头在闭包里就丢了**；主路径与回退路径两处 return 都改。
- 嗅探：`Content-Type` 含 `xml`，或响应体**前 2KB** 含 `<rss` / `<feed` / `<rdf:rdf`
  （只看开头 —— 全文扫描会让「正文里引用了 `<rss>` 字样的博客文章」误入）。⚠️ 这只是
  第一道筛，**真判据是 `feedparser.parse()` 出不出 `entries`**，出不来就回退普通网页提取。
- 合并成**一篇**（用户选定，不是一条一个文档）：`# {feed 标题}` + 每条 `## {标题}` /
  链接 / 摘要，上限 `RSS_MAX_ENTRIES = 20`、每条截 `RSS_ENTRY_CHARS = 2000`。这样一份
  「订阅聚合」直接可喂生成管道。
- 新依赖 `feedparser`（BSD-2-Clause，纯 Python）→ `pyproject.toml` + `LiMoBackend.spec`
  的 `hiddenimports`，许可证同步进 `ACKNOWLEDGMENTS.md` / `THIRD-PARTY-NOTICES.md`。
  不用 `xml.etree` 手写：RSS 0.9/1.0/2.0/Atom/RDF 五种变体 + 日期格式 + 编码全得自己处理。

### arXiv —— 新适配器，委托 `DocumentAdapter`

`accept()` 同步纯正则、**不发网络请求**；`adapt()` 抽 id → 构造
`https://arxiv.org/pdf/{id}` → `safe_get` → 临时文件 → **委托 `DocumentAdapter`**
（零复制引擎链逻辑）。

⚠️ **必须注册在 Document 之前** —— `/pdf/2401.12345v1.pdf` 这种带扩展名的形态会被
DocumentAdapter 按扩展名抢走。

### ⚠️ 刻意不新增 SourceType

`rss` / `arxiv` 会触发 `sourceTypeCoverage.test.ts` 强制同步 `SOURCE_FILTERS` +
`TYPE_COLORS` + `tabRegistry`，漏一处就是预览区白屏。故 RSS 记作 `webpage`、arXiv 记作
`pdf`（`_ADAPTER_SOURCE_MAP` 加一行 `"arxiv": "pdf"`），既有能力自动可用。

⚠️ **措辞要准确**：arXiv 的效果是「**与现有 URL 导入 PDF 行为一致**」，不是「PDF 专属能力
全部可用」—— 现有 URL 导入 PDF 同样不持久化源文件，所以依赖原始文件的那部分（PDF 直传、
重新解析）在这里同样受限。

### 影响范围

适配器 5 → 6；`_download()` 签名变更（唯一的调用方在同一个类里）。普通网页路径
**逐字节零回归**（`test_adapter_rss.py` 断）。

---

## 2026-10-29 — DeepSeek 搜索接入：搜索 provider 从三选一变四选一

**起因**：用户提出。现有 `search_web` 的降级链里没有一家是「原生 JSON API + 按成功请求
计费」的搜索引擎，而 DeepSeek 的 Search / Fetch 还带两个适合喂模型的字段：`summary`
（官方写明 *intended to be read by a model*，600–800 字）与 `authority`（0–4）。

### 改了什么

`app/agent/tools.py`（**唯一改动点** —— 生成侧的 `_search_all` 直接 import 它）：

- `_deepseek_search`：`POST https://api.deepseek.com/v1/tools/search`，body
  `{query, count, need_content: false}` → 归一成现有契约
  `{"results": [{title, url, snippet, summary}]}`。
- `_deepseek_fetch`：`POST /v1/tools/fetch {url}` → `{title, content}`。
- `search_web` / `fetch_url` 各加一个 `elif provider == "deepseek":`，**失败统一降级 DDG**
  （search）/ trafilatura（fetch），同 tavily / searxng 的处理。

⚠️ **`need_content: false`**：`summary` 已经够用，`content` 是整页正文，白烧 token。
⚠️ **`fetch_url` 开头的 `validate_url` 是「已知满足」不是新增工作** —— 记这一条只为提醒
**不要在新分支里提前于它发请求**。

**Key 两级来源**：`agent_tools.search.deepseek_api_key` 显式优先，为空回退 `providers` 里
`id == "deepseek"` 那一项（**只认 `id`，不按 `base_url` 猜**）；该键已进
`config_io.SECRET_SCALARS`（走 DPAPI 密钥库，不落 YAML）。`fetch` 与 `search` **共用同一把
Key** —— 同一个账户，配两把只会让用户多抄一遍。

**`compose_reference` 优先用 `summary`**：`snippet = summary or snippet or ""`。
⚠️ 对 tavily / searxng / ddg **逐字零回归**（它们没有 `summary` 键）。
`SNIPPET_CHARS = 400` 不变 —— summary 600–800 字会被截到 400，这是**有意的预算控制**。

**前端**：`SettingsPage.tsx` 的下拉加 `deepseek` 选项 + Key 输入框（选中时显示），文案标明
计费（$0.75 / 1000 次**成功**请求，失败与超时不收费）与限流。

### 影响范围

- **默认 provider 仍是 `duckduckgo`**（零回归）。
- 计费口径与别的 provider 不同（按成功请求、不看 `count`），所以设置页文案必须写清。
- 官方重试策略：408/429/5xx 等待重试、402 充值、**其它 4xx 不要重试** —— 本轮不实现重试
  （降级 DDG 更快更明确）。

---

## 2026-10-29 — 查询改写接进对话侧检索：把「口语问题」换成库里可能出现的说法

**起因**：`search_docs` 接上真检索（同前一条）之后，剩下的那个洞是**词汇鸿沟** ——
用户问「一边猜一边验证的那个技术」，库里写的是「投机解码 草稿模型」，同一个意思，
一个字都不重叠。`_experiments/06-query-rewrite/` 在真实库 44 题上量过了，形态是
**「原词 + 3 条改写一起进 RRF」**（融合路 H@1 52.3% → 75.0%、H@5 88.6% → 97.7%）。

### 改了什么

**① 新增 `app/services/query_rewrite.py`**

`rewrite_queries(llm, question, *, cancel_event) -> list[str]`：一次 LLM 调用，出 2–3 条
检索式。**失败一律返回 `[]`**（调用方按原词检索，那本来就是原设计），只有
`CancelledError` 照常上抛。

⚠️ **没塞进 `web_search.py`**：它服务检索、不服务生成，且两者的失败语义不同
（联网检索失败要推 warning，改写失败用户看不出区别也无事可做）。

**② `services/search_fuse.py::fused_search` 加 `extra_queries`**

同一个问题的其他说法**各检索一次**，再把几条 id 序列交给 `core/vector_index.rrf` 合并。
**RRF 本体不在这里**（这里只多跑几次 `_one`）；`extra_queries` 为空时走的分支与改造前
**逐字相同**（零回归是结构保证，不是「应该没变」）。

三条实现判断，每条都对应一个「写错了也看不出来」的坑：

- **串行而非 `asyncio.gather`**：实测 `VectorSearcher._ensure_loaded` **没有锁**
  （`await asyncio.wait_for(warmup(), ...)` 直接调），四条并发进冷启动会把 22.8MB 权重
  读四遍；而耗时大头是前面那次 LLM 调用（中位 1.06s），并发到这 4 次检索上省不到 1 秒。
- **`semantic` 自证段取原词那次**：口径不符 / 没建过 / 库不可用都是全局状态，
  改写那几次只会重复同一句话。
- **合并等权**：实验里「原词 + 3 条改写」的推荐形态就是等权。给原词加权是一个
  **没测过的超参**，而 `0.8/1.2` 偏向量那组虽然分数更好，却是在这 44 题上后验挑出来的
  （`_experiments/05` 已知边界第 1 条），不能直接拿来用。

**③ `config["search"]["rewrite_queries"]`（默认 `False`）**

**默认关 = 零回归** —— 打开它等于每轮 `search_docs` 多一次计费调用。
**刻意只作用于对话侧**：文档库搜索框是逐字联想，每敲一次多一秒会直接毁掉输入体验；
对话里用户本来就在等一次工具调用，这一秒藏在里面。

**④ `routers/chat.py` 接线 + `cancel_event` 上移**

`_rewrite_enabled(config)` 抽成函数（两个端点各写一次 `.get()` 就是同源漂移入口）。
⚠️ **`cancel_event` 从 `sse_stream` 前一行上移到工具注册之前**：改写要拿到它
（用户按了停止就别再花钱发一次新调用），而 `disconnect_watcher` 要置的就是**这一个**事件
—— 在 `search_docs` 里另建一个就分叉了。

### 关思考是无条件的，不是「模型支持才关」

`LLMService.call(disable_thinking=True)` 无条件下发 `thinking: disabled`。

实验第一版写成 `disable_thinking=llm.supports_reasoning` —— 那个能力位标的是「认不认
`reasoning_effort`」，而 DeepSeek 服务端**思考默认开启**且能力位没标 ⇒ 没进关闭分支
⇒ 推理链把 300 token 预算全烧光 ⇒ `content` 空串，现象看起来像「解析器坏了」。
不关思考中位 2596ms 且**随机失败**，关掉后 1059ms、0/44 退化。
`test_必传关闭思考` 钉住这条，对照实验已验（改回按能力位判 → 17 条测试红）。

### 验证

`tests/test_query_rewrite.py`（新增 38 条）：解析与清洗 / 调用形状 / 失败退化 /
`fused_search` 的合并语义 / 工具层接线 / prompt 与实验脚本同源。
做了一轮**注入退化**的对照实验（同时改回按能力位判 + 只跑改写不跑原词）→ **17 红**。

### 影响范围

- **默认关，所以对现网零影响**。打开方式：`config.yaml` 的 `search.rewrite_queries: true`
  （本轮**刻意没做设置页开关**：这是一个人工调过的旋钮，先在真实使用里看效果）
- 打开后每轮 `search_docs` 多一次 LLM 调用（flash 模型 + 关思考，中位 1.06s）
- `_experiments/06-query-rewrite/` 的 prompt 与实现**逐字一致**，有测试比对
  （`_experiments/` 不进仓库，缺文件时该条跳过 —— 但本地跑得出来时必须比对）

---

## 2026-10-29 — 导出进度反馈：把已经写好的 SSE 端点接上（它此前零调用）

**起因**：`POST /export/custom/stream`（SSE 进度）+ `GET /export/download/{temp_id}`
后端**全就绪**，打包也已 `await asyncio.to_thread(_build_export_zip, ...)`（不阻塞事件循环），
但前端**零调用** —— `ExportPanel` 的「详细导出」走的是同步 `POST /export/custom`。
那个 `streamExportProgress` 封装早就作为死代码删了（留着仍是零调用，死代码只换了个地方躺）。
批量导出几十篇时界面一片静止，用户唯一能判断的就是「浏览器转圈」。

**改了什么**：`ExportPanel::handleCustomExport` 改为 `fetch('/export/custom/stream')` +
`parseSSEStream` → 进度条跟上 `{phase, doc, total}` → `done` 事件拿 `download_url` 落盘。

⚠️ **刻意只留这一条路径**，不保留同步端点作为回退：流式端点内部调的**就是**
`_build_export_zip` 那一份，留两条只会在将来某次改动时分叉（模式 #54）。

⚠️ **事件字段名踩过一次**：第一版按 `ev.type === 'collecting'` + `ev.current` 写，
真实形状是 `{"type":"progress","phase":"collecting","doc":N,...}`。按第一版上线的话
进度条永远停在 `0/N` 且 `collecting` 分支进不去 —— **静默失效、零报错**。

### 影响范围

只影响「详细导出」这一条用户路径（`ExportMenu` 的快捷单文件导出不受影响）。
`npx tsc --noEmit` 与 `npm run lint` 通过。

---

## 2026-10-29 — 语义检索真实库实测结论回写（`_experiments/05` + `06`）

规划表上「真实库端到端实测」此前一直标着未做（2026-10-26 决定本轮先跳过）。
实际上 **2026-10-27 的 `_experiments/05-real-library-product-path/` 已经做完了**，
只是结论没回写进文档 —— 现补上，并把「仍未做」改准。

**测的是什么**：用户本机 `~/.limo` 真实库 **145 篇**（84 网页/文本 + 58 视频转录 + 3 合成），
`vec.db` 由**产品 worker 建好**（36,861 块 / 512 维 / `bge-small-zh-v1.5-int8` / chunk 200），
44 题走**产品的 `VectorSearcher.fuse`** —— 不是评测脚本自己重新编码一遍。

| 路 | H@1 | H@5 | H@50 | MRR@10 |
|---|---|---|---|---|
| 字面（FTS5） | 52.3% | 77.3% | 97.7% | 63.8% |
| 纯向量（bge-small-int8） | 56.8% | 84.1% | 93.2% | 67.2% |
| **RRF 等权（产品现状）** | 52.3% | **88.6%** | **100.0%** | 68.0% |

**三条结论**（详见该目录 README）：

1. **融合的价值是「两路互补」不是「某一路更强」** —— 最硬的证据是 H@50：字面 97.7% /
   向量 93.2% / 融合 **100.0%**，单路各有一个洞且**不重叠**。
2. **等权 RRF 的代价在 H@1** —— 它把第一名压回到字面的水平（52.3% == 52.3%），
   低于纯向量的 56.8%。机理是一方给出的烂名次会**拖累**另一方给出的第 1–2 名。
   即「用 H@5 的召回换 H@1 的稳定性」。
3. **`0.8/1.2` 偏向量加权在 H@1 / MRR / nDCG 上高 11.3 / 5.0 / 4.6pp**，但**不改产品** ——
   权重是在这 44 题上后验挑出来的，样本太小，要改需要另一批没参与挑权重的题。

⚠️ **一条普适的坑（值得单独记）**：批量编码查询 ≠ 逐条编码查询。第一版纯向量路一次
编码 29 条，`[self-check]` 直接变 False —— 前 50 的**集合**只差 4 个 id，但**顺序**
有 28–38 处不同（tokenizer 在 batch 内 pad 到该批最长，bge-int8 下大量候选余弦极接近）。
产品是逐条编码的，评测也必须逐条。**「调的是同一个函数」不够，得调在同一个形状上。**

⚠️ **另一条**：评测集的构成比算法更能左右那个百分数。向量在 29 题原标注集上与字面
**完全打平**（79.3% vs 79.3%），只在自拟 15 条纯语义题上大幅领先（93.3% vs 73.3%）。
**往后报检索数字必须连题目来源一起报**，否则 83% 和 41% 可以同时是真的。

---

## 2026-10-29 — 对话侧检索：`search_docs` 接上真检索（它此前根本没在检索）

**起因**：做规划调研时读到 `chat.py::_make_search_docs`，发现它调的是
`store.list_transcripts({"query": q})` —— 那是**标题子串过滤**，既不碰 FTS5 也不碰
向量。同一时刻文档库搜索框走的是 `GET /api/search`（字面 ∪ 语义 RRF 融合）。

失效形态很具体：**正文里逐字存在的词，只要标题没有就搜不到**。而界面上表现为
「AI 说库里没有，可我用搜索框自己搜得到」—— 两边都不报错，用户只能靠对比结果察觉。
这不是「检索不够好」，是**两处入口根本不是同一件事**（模式 #54 的同源漂移）。

### 改了什么

**① 抽出 `app/services/search_fuse.py`（新增）**

把 `routers/search.py:74-90` 那十几行编排（两路同深度取数 → `VectorSearcher.fuse` →
按融合顺序组装、语义独有命中回补 title）抽成 `fused_search(svc, searcher, q, *, limit, depth)`。
**RRF 算子一个都不在这里**（仍在 `core/vector_index.py` / `services/vector_search.py`），
它只回答「一次融合检索按什么顺序做哪几步」。

⚠️ **抽出的理由不是文件变小**，是对话侧要做同一件事。两处各写一遍的失效形态正是上面那条。

⚠️ **字面索引坏时这里不吞异常**（`QueryError` 照抛）：`/api/search` 要把它变成 503 +
可恢复标记，对话侧要把它降级成标题过滤并**标明降级**。两种处置都对，差别只能由调用方
决定 —— 在这里吞掉等于替两边选了一个，且选错的那一边完全看不出来。

**② `routers/search.py` 改为调用它**（不留第二份，行为逐字不变）

**③ `routers/chat.py::_make_search_docs` 改 async，走 `fused_search`**

- `AgentRunner` 有 `asyncio.iscoroutinefunction(fn)` 分支，工具可以是 async，核心循环零改动。
- 每条结果带 **`snippet`**（FTS 的 90 字摘要），模型不再只能凭标题猜内容。
- **移除 `sourceType`**（刻意的交换）：grep 确认 prompt 与前端 chat 侧都零消费方。
- **`found` 与 `hasMore` 拆开**：改造前 `found = len(items)` 而 `items` 已 `results[:5]`，
  上限恒 5。接上真检索后 5 条几乎恒满 —— 不拆开的话「命中 200 篇」会被说成
  「找到 5 个相关文档」，那不是省略，是**说假话**。故请求 `limit=6`，多的一条只判 `hasMore`。
- **降级可观测**：`AppError` → 回退标题过滤 + `degraded: "index_unavailable"`。
  抛错会被 `agent.py` 吞成 `{"error":...}`（索引坏一次，对话从此一篇都搜不到）；
  静默降级会让模型把「没搜到」当事实。两条都不行。
- `_build_registry` 增 `svc` / `searcher`（**默认 `None`**：既有 `_build_registry(None)` 用例
  不必改签名，`None` 时降级并标 `no_query_service`，与「索引坏」同样可观测）。

**④ `llm/agent.py::_tool_summary` 跟随新字段**

`hasMore` → 「找到 5 个以上相关文档」；`degraded` → 「检索不可用，按标题找到 N 个文档」；
其余不变。

### 影响与代价

- ⚠️ **对话侧不再免费**：向量检索开着时，每次 `search_docs` 会发**一次 query embedding**。
  单条短文本量级可忽略，但这条成本是新增的。向量开关默认关 ⇒ 默认仍零成本。
- ⚠️ **延迟**：改造前纯内存过滤（毫秒），现在一次 50 深 FTS（+ 可选的 3 秒语义上限）。
  换来的正确性收益是主诉求。
- ⚠️ **`test_chat_web_search.py::test_接线_开关驱动工具表` 的断言放宽了一个字符**：
  调用点从 `_build_registry(store, allow_web=allow_web)` 变成多了两个参数，故断言从
  「带右括号的完整调用」改为「同一行还接着别的参数」。区分力不降反升：去掉检索依赖会退回原形态。

### 顺带修的欠账（① 引入）

本地大模型接入时把 13 处空 key 检查换成 `provider_requires_api_key(llm.provider)`，
但 `test_chat_followups.py` / `test_classify.py` 的 LLM 替身没有 `provider` 属性 →
15 个用例以 `AttributeError` 失败（看着像被测逻辑坏了）。给三个替身补上 `provider = "dashscope"`。
**教训**：改「所有调用点都要传的东西」时必须跑一次全量，单个测试文件全绿说明不了什么。

### 验证

- `backend/tests/test_chat_search_docs.py`（新增，13 用例）：async 化 / 接线守卫 /
  `hasMore` 边界（不足·恰好·超出）/ 降级三态 / 注册表里挂的就是它 / 路由层两个端点都传了依赖。
- 后端全量 `1260 passed`；`check-all search` 绿。
- **未做**：真实库端到端手验（`hasMore` 场景需要一篇正文关键字命中而标题不含的文档）。

---

## 2026-10-29 — 规划：把 AI 应用能力评估的结论写进待办

检索了 2026 年的 AI 应用 / RAG / MCP 生态现状，把「值得在本项目落地」的能力按成本分两档写进 CLAUDE.md 的「🚀 规划中」，并给出建议推进顺序。

- **【一】** 本地大模型接入（Ollama 等 OpenAI 兼容端点）· 检索查询改写 · RSS 与 arXiv 导入 —— 半天到三天，不新增架构概念
- **【二】** Reranker 重排（新增第三族插件 `RerankPlugin`）· MCP 服务端（`QueryService` 的第三个传输层）· 跨会话记忆 —— 三到七天，会动架构
- **横切**：检索质量门禁（29 题评测接进 `check-all`）排在所有检索改动之前
- 原有「更多导入源」三合一拆开：RSS / arXiv 同一档成本，播客需 ASR 就位后再评估

⚠️ 梯队只表达实施顺序、不表达价值高低；「表格顺序即建议推进顺序」写进文档，避免后人以为可以按喜好挑。⚠️ 三项剔除的理由也记了：知识图谱/GraphRAG 在几百篇规模的个人库上索引成本与收益不成正比，MCP 客户端排在服务端之后，TTS/发布归另一条产品线。

---

## 2026-10-29 — 图表：块名被当成了布局名（认得模型想表达什么，而不是报错）

**起因**：用户贴来一条产物里的报错——

```
📊 图表生成失败（布局失败：未知布局 'cycle'（可用：hchain / layers / timeline /
grid / branch / tree / flowchart / state / sequence / quadrant））
```

### 诊断：模型写对了内容、选对了块，只填错了字段

溯源到 `~/.limo/宝洁测评速通经验分享-ac259f/notes/v1.md` 第 131 行（`promptVersion:"21"`，
`2026-10-28T23:23:02`，`deepseek-flash`）。紧接报错的那句正文是：

> 实际顺序为：记住小圆点位置 → 做一道干扰题 → 出现新的小圆点 → 再记 → 再做干扰题，如此循环。

这正是 `get_chart_format()` 明写「**没有分支的**首尾相接因果循环 → `cycle` 块」的那一类。
模型写出的 fig 是 `{"layout":"cycle","nodes":[…]}` —— 它把**块名当成了布局名**。

根因不在措辞（"21" 那轮刚把「没有分支的」补上），在**位置**：整段 `get_chart_format()` 都在教
fig JSON，那句「→ `cycle` 块」夹在中间，而 `cycle` 和 `grid` / `flowchart` 长得一样是个单词，
最容易被读成「一种新的 layout」。`_LAYOUTS` 从来没有环形算子（2026-10-20 的决策是：这种关系
用列表表达就够了），于是 `validate_spec` 判「未知布局」，整段降级成失败文本。

⚠️ **报错里的十个算子有七个是 prompt 早已撤下引导的**（`hchain` / `layers` / `timeline` /
`branch` / `tree` / `sequence` / `quadrant`）—— 对用户是无意义噪音，也无法据此采取任何行动。

### 改了什么

**① 后端兜底：认得出来就转换（`chart_renderer.py`）**

新增 `_fig_as_alternative_block()`：当 fig 的 `layout` 是**「用列表代替画图」族里后端没有算子的
类型**时，把节点文字**无损**搬进同名语义块，而不是失败占位。

```
⟶ <!-- limo-block:v1 type="cycle" -->
  1. 记住小圆点位置
  2. 做一道干扰题
  …
  <!-- /limo-block -->
```

- **判据从两个集合派生**：`_BLOCK_ONLY_LAYOUTS = tuple(t for t in CHART_ALTERNATIVE_BLOCK_TYPES
  if t not in chart_layout._LAYOUTS)`，当前只剩 `steps` / `cycle`。⚠️ **必须派生不能手写** ——
  `timeline` / `sequence` / `quadrant` 在 `_LAYOUTS` 里**有算子**，写 `{"layout":"timeline"}` 是
  合法用法，手写名单会把它们一起截走（测试有负向对照钉住）。
- **列表类型是 CSS 判据**：`steps` → 无序（`<ul>`）、`cycle` → 有序（`<ol>`），写错就退化成普通
  列表（内容不丢，但白标了一个块）。
- **抽不出节点不硬造空块**：给指路式文案 `"{layout} 不是图表布局（这种关系用同名语义块表达）"`，
  替掉那句罗列十个算子的旧文案。
- ⚠️ **这不违反「失败显式化」**：不是把失败藏起来，是**已经知道它该落在哪里**。真正的失败
  （JSON 坏、布局装不下、抽不出节点）照旧零残留替换。

**② prompt 减少复发（`prompt.py` + `PROMPT_VERSION` `"21"` → `"22"`）**

「三种结构，选最贴合的一种」→ 明写封闭枚举：

> `layout` **只能是** `grid` / `flowchart` / `state` 这三个值之一 —— 上面那几种该用列表表达的关系
> （steps / timeline / cycle / sequence / quadrant）**不是布局名**，写进 fig 也画不出图。

⚠️ **两侧不互相替代**：prompt 侧是「别写错」，后端是「写错了也认得出」。只有后者能救历史行为与
换模型后的新形态。

### 影响范围

- `backend/app/services/chart_renderer.py`（+1 派生常量、+2 函数、`_process_fig` 加一个分支）
- `backend/app/generation/prompt.py`（一行措辞）
- `backend/app/generation/prompts/__init__.py`（`PROMPT_VERSION` → `"22"` + 版本注释）
- `backend/tests/test_chart_renderer.py`（+5 组断言：派生集合自证 / cycle 有序 / steps 无序 /
  **timeline 有算子不得被截走** / 抽不出节点给指路文案且不造空块）
- ⚠️ 已有的历史产物**不回溯修改** —— 那句失败文本已经写进 `v1.md` 了，重新生成才会有新行为

---

## 2026-10-29 — 合成入口：装得下就是「选好 → 合并」

**起因**：用户原话「我在想我们合成是不是做的太复杂了，有时候用户就是只是想把几篇文档
单纯的合在一起生成笔记，就假如我们把那些转录简单的合在一起那不就可以复用目前的产物路线
而且不用写那么复杂了」。

### 关键判断：要砍的是入口摩擦，不是管道

查证后发现用户描述的那条路**已经是现状**：`synthesize()` 只产正文不生成笔记；装得下时
`build_stuff_text` 是**零 LLM 调用的纯字符串拼接**；生成走现成端点。1412 行里真正占地方的
是超窗处理（~530 行）与 prompt 骨架（~310 行），拼接那一段只有 12 行——**复杂度不在
「合并」，在「装不下怎么办」**，而那部分删掉就是拿「大材料直接失败」换简洁。

用户**实际感知到的**复杂度在界面：一屏原料单选 / 读者意图 / 高级区维度+体裁，让一件简单的事
看起来复杂。所以改的是入口。

⚠️ **顺带否掉一个看似更彻底的方案**（上一轮提议的「合并后直接生成」）：`docLibrarySynthesis.test.tsx`
有 `describe('done 之后不自动生成（D7）')` 钉住「一次 `/generate/` 请求都不许有」，而技术上
也不成立——要复刻 `GenPanel.tsx::handleGenerate` 的 20+ 参数收集（模式 #54 的第二份实现）。

### 改了什么（`DocLibrary.tsx`，纯前端）

- 新增 `synthTuneOpen` state 与 `simpleMode` / `needsTuning` 两个判据：
  `simpleMode = !needsTuning && !synthTuneOpen`。
  装得下（`synthPreview.mode === 'stuff'`，或预览还没回来）时，**配置区（原料 / 意图 / 高级区）
  默认收起**，只留已选清单 + 标题 + 一句「全部原样合并，不压缩、不改写」+「调整选项」链接。
- 标题与主按钮文案随之变：「合并 N 篇文档 / 合并」↔「合成 N 篇对比综述 / 合成」。
- `handleOpenSynthesize` 每次打开都 `setSynthTuneOpen(false)`——回到简单形态，与「原料/意图/维度
  不继承上次」同款。

### 三条刻意为之

1. **收起不减能力**：收起时发出的就是打开那一刻的默认值（原文 / 首预设维度 / 首个意图），
   与「展开了但一个都不改」逐字相同。有测试直接断言收起态 body 的四个字段。
2. **需要压缩时不许收起**（`map_reduce` / `reject`）——会压缩、会截断、可能要换模型，那是
   用户该知道的事；藏进「调整选项」等于不说。此时 `data-synth-tune` 整个不渲染。
3. **预览未返回时按简单模式渲染**：先收起、必要时再展开，避免「先完整后收起」的跳动。

### 测试

`docLibrarySynthesis.test.tsx` 新增 `简单模式` 一组 4 条 + 既有 12 条测配置项的用例补
`openTune()`（`openAdvanced()` 内部已先调 `openTune()`，故高级区那批调用点未改）。

对照实验两条（均实测）：
- `simpleMode` 恒 `false` → 3 条红（存量用例全绿 ⇒ 不写这条改动不会被现有测试拦住）
- `needsTuning` 恒 `false` → **只有**「需要压缩时强制展开」那条红（钉得准）

### 不做

- **不拆 `synthesis.py`**：超窗分层是「大材料能不能做完」的唯一保障，与本轮无关。
- **不改 D7**（合成后自动生成）：见上，判据仍在测试里。
- **不动 `BatchBar` 的 title**（「生成对比综述」）：composite 类型的生成侧 label 本来就是它，
  合并完点生成产出的也确实是对比综述——措辞是准确的，改它只会破 `synthBtn()` 的取法。

---

## 2026-10-27 — 性能打点：只记录，不判断（0.5.20 内续）

**起因**：用户原话「我觉得各种可能要考虑性能问题的地方都可以加上打点，这样我真实运行时
候的问题你也可以知道」，并追加「全量保存留档」。目标不是优化，是**让慢这件事在数据上
存在**——打包版装在别人的机器上，出问题时我这边看不到任何曲线。

### 关键判断：横切，不是逐点加 timer

第一版方案是「8 个可疑处各加一个 `time.time()`」——用户一句「横切不就行了，属于debug的」
把它否了。查证后发现 `main.py::add_request_id` 中间件**早就在量每个请求的耗时**，只是
判据是 `duration_ms >= 2000` 才 WARN，于是 1900ms 的退化和 12ms 的正常请求在数据上
长得一模一样。**真正缺的不是计时器，是不带判断的记录层。**

### 改了什么

- **新增 `backend/app/core/perf.py`**：`record` / `timer` / `snapshot` / `reset`。
  - **不做判断**：没有慢阈值、没有采样率。阈值该由看数据的人按分位数定，不该由写代码的
    人猜一个 2000ms 写死。
  - **双轨**：进程内 `deque` 分位数（`GET /api/perf`，重启即清零）+ `TimedRotatingFileHandler`
    全量 JSON Lines 落盘（`{data_dir}/perf.log`，按天滚动保留 14 天）——后者才是「留档」。
  - **独立文件**：`limo.perf` 的 `propagate = False`，否则性能行会挤占 app.log（5×2MB 业务
    日志），并把 `GET /api/logs` 的 1000 条内存环形缓冲几秒刷空。
  - **写失败绝不影响业务**：目录不可写只记一次 warning，进程内统计照常。
- **HTTP 层（横切，改 3 行）**：`_log_request` 末尾追加 `perf.record`，**保留原有的
  `>= 2000` WARN 行为不变**——那是**日志**判断（给人看），perf 是**数据**记录，两者不冲突。
- **三个没有请求边界的点**：`startup.store`（`Store()` 构造，索引一坏就要重建全库的地方）、
  `store.save_index`（每次全量写盘，10MB 级）、`vector.worker_round`（编码一轮，钱和 CPU 都在
  这里）。空转与「hash 全中」的轮次刻意不记——接近 0ms，记进去只会淹掉数据。
- **`GET /api/perf`**：只读端点，按 p95 降序（最该看的排最前）。

### 两个坑（都写进模块 docstring 了）

1. **SSE 的量到的是 TTFB，不是流总时长。** `BaseHTTPMiddleware.call_next` 在
   `http.response.start` 时就返回，所以 `/api/generate/{id}/stream` 恒在 2–14ms，而生成
   实际跑几十秒。故给 SSE 的 key 加 ` [ttfb]` 后缀，免得这些 2ms 样本把普通请求的分位数
   整体拉低。
2. **路径必须用路由模板归一化。** 原始 path 一篇文档一个统计组，`snapshot()` 会返回一张
   没人看得懂的表且内存无上界（另有 `_MAX_GROUPS = 200` 兜底）。
   ⚠️ 实测发现 **FastAPI 0.139 的 `route.path` 不含 `include_router(prefix="/api")` 的前缀**
   （`include_router` 挂的是 `_IncludedRouter`，prefix 收进 include_context，
   `scope["root_path"]` 也是空串）—— 于是统一在 `_log_request` 里剥掉 `/api`，
   让「已匹配路由（模板）」与「未匹配路由（退回原始 path）」两种来源**口径一致**，
   且将来 FastAPI 若变回展开式，统计键也不会被一次升级劈成两组。

### 前端入口：日志面板的「性能」标签页

有了数据还得有地方看。`components/ui/LogPanel.tsx` 从「只有日志」变成两个标签页
（`系统日志` / `性能`，`role="tab"`）：性能页拉 `GET /api/perf`，表格列
「打点 / 次数 / p50 / p95 / max」，每 3 秒刷新，一键复制成可直接贴给 agent 的 Markdown 表。

四条刻意为之（`frontend/src/test/logPanel.test.tsx` 8 条，每条配对照实验）：

1. **不做颜色分级。** 染红某个数字就是在替用户决定「多少算慢」，与 `perf.py` 的整个设计
   前提（阈值该由看数据的人定）正面冲突。
2. **`logFile: null` 如实说「落盘不可用（仅进程内统计）」。** 落盘不可用时后端只记一次
   warning，前端若照样写「完整留档 perf.log」，用户会以为有文件可看。
3. **`[ttfb]` 分组存在时才给说明。** 不给的话，`generate/stream p95 = 8ms` 会被读成
   「生成很快」；而没有这类分组时又没必要摆一句让人去找不存在的东西。
4. **默认停在日志页、不发 `/perf`。** 面板的主要用途仍是看日志，多一个后台请求不划算。

### 影响范围

新增/改动 7 个文件（`perf.py` 新增、`main.py` / `store.py` / `vector_worker.py` 各一处、
`tests/test_perf.py` 17 条守卫；前端 `LogPanel.tsx` + `logPanel.test.tsx` 8 条）。**对业务零行为变更**：不改任何返回值、不增任何阻塞、
HTTP 打点走的是中间件里**本来就有的**那次计时。⚠️ `{data_dir}/perf.log` 要**重启后端**
才会生成（`setup` 在 startup 里）。

---

## 2026-10-27 — 建索引的进度与完成提示（0.5.20 内续）

**起因**：打包版装上本地向量引擎、实际跑通一次之后，用户的反馈是「本地的确实可以运行，
然后就是索引重建完成之后可以加一个小提示」。查下来不止一处**没有任何收尾反馈**：

1. 字面索引的「重建索引」是同步动作，成功与否只体现在卡片上「上次重建：…」那行文字
   **悄悄跳了一下** —— 要刻意去比对才看得见。
2. 向量索引更彻底：`syncVectorIndex()` 的注释写明 `requested` 只表示「话递到了 worker」，
   **不代表编码完成**。而卡片点完就再没刷新过，于是「待索引 N 篇」永远停在点击那一刻
   的数字，用户既看不到进度，也不知道什么时候可以开始用。

### 改了什么（全在 `SearchIndexCard.tsx`）

- **字面索引**：`runReindex` 成功后 `addToast('索引已重建完成 · N 篇')`，N 取**重建后**的
  `next.docCount`（不是点击前的快照）。失败仍只留在卡片内的 `actionError` —— 不弹
  「完成」，也不弹红色 toast（错误就在按钮旁边，弹窗只会让人去找它）。
- **向量索引的进度**：只要「真的有人在干」就每 3 秒读一次状态，把 `待索引 N 篇` 变成
  一个会往下走的数字。判据是 `vec.enabled && vec.available && docs.pending > 0` 的**布尔**
  合取 —— 只写 `pending > 0` 不够：开关关掉或索引不可用时 `pending` 是个**静止的数字**
  （worker 没在跑，它永远不会自己降），那样轮询换不来任何变化，只多一张后台读盘的卡片。
- **向量索引的完成提示**：`pending` 从 >0 变成 0 的**那一次**弹一句；伴随失败项时用
  info 而不是 success，并点名「可点『重试失败项』」——失败篇混进一句「完成」里会掩盖
  还差几篇。

### ⚠️ 这与「索引状态刻意不轮询」不矛盾

那份纪律（`lib/search-health` 与这张卡的模块注释）说的是**字面索引的 `available`**：
它只在三个时刻变化（启动重建 / 手动重建 / 外部删 `search.db`），没有「进行中」这个状态，
轮询只会读一个几乎不变的值。而向量的 `pending` 是真的在往下走的，且**有明确终点**
（归零即停止，`hasPending` 变假、interval 自动清掉）。模块顶部注释已按这条分界改写。

⚠️ 依赖写成**布尔** `hasPending` 而不是 `pending` 数值：写成数值的话每降一篇就清掉
定时器重设一个，等于用 setInterval 模拟 setTimeout 链。

### 测试（`searchIndexCard.test.tsx` 新增 6 例）

字面重建成功才提示（失败不提示）/ 归零那一刻提示且报的是建完后的篇数 / 有失败项走
info 且点名重试入口 / 挂载时已建完**不**弹 / 有待索引时 6 秒两拍、无待索引时 30 秒零拍 /
开关关掉时零拍。做过对照实验：把 `hasPending` 还原成只看 `pending > 0`、把完成 effect 的
`prev <= 0` 守卫去掉 —— 三条用例同时变红（其中一条是被挂载时误弹的提示挤掉的，
正好印证 `prev <= 0` 那道守卫在挡什么）。

---

## 2026-10-27 — 插件管理端点补来源守卫（0.5.20 内续）

**起因**：打包验收时顺手审了一遍 `routers/plugins.py`，发现**六个写端点一个都没过
`ensure_trusted_request`** —— 而它们是全应用副作用最大的一组：
`POST /plugins/install` 往 `~/.limo/plugins/` 解压任意 zip（重启后那份 `.py` 被 `exec_module` 执行）、
`install-deps` 在插件目录里建 venv 并 pip install、`DELETE` 直接删目录、`enable/disable/config`
改启动行为。

### 为什么这条链真的能被跨站触发

CORS 只拦跨站**读响应**，不拦请求**发出**（见 `app/core/origin.py` 模块文档）。而下面两种形态
属于**简单请求**、不触发 preflight、浏览器直接发：

- `POST /plugins/{name}/enable`、`/disable`、`/install-deps` —— **无 body、无自定义头**
- `POST /plugins/install` —— `multipart/form-data`（表单上传也是简单请求）

于是任意一个恶意页面（或 `file://` 打开的一份 HTML）只要知道客户端在 7413 上，
就能推动「装一个插件 → 重启后任意代码执行」的完整链条，**用户什么都不会看到**。
这与 2026-10-20 给 `/api/search/reindex` 补守卫是**同一类判据**：有副作用的端点，
守卫是主防线而非纵深防御。

### 改了什么

六个写端点各加 `request: Request` + 函数体**首行** `ensure_trusted_request(request)`。
`GET /plugins`（只读）不加 —— 它不改任何状态，加了只会让 `curl` 排障多一步。

⚠️ **位置在 `_require_plugin(name)` 之前**是刻意的：先判来源再查存在性，攻击者拿不到
「这个插件名存不存在」的探测信号。

### 影响面

前端零改动 —— `apiFetch` 对**每个**请求都带 `X-LiMo-Client: 1`（`api/core.ts::CLIENT_HEADERS`），
而它是 `is_trusted_request` 的第一判据。开发模式（Vite，`Origin: http://localhost:5173`）
与 Tauri（`tauri.localhost`）都在白名单里。

### 测试

新增 `backend/tests/test_plugins_guard.py`（9 例）：六个端点参数化断言陌生 Origin → 403
`UNTRUSTED_ORIGIN`；一条钉**顺序**（evil origin + 不存在的插件名 → 403 而非 `PLUGIN_NOT_FOUND`）；
两条**反向对照**（带 `X-LiMo-Client` / 带 `localhost` Origin → 不被拦，防「写成一律 403 也全绿」）。
对照实验实测：删掉 `install-deps` 那一行 → 正好那一条红、其余 8 绿；把 `enable` 的守卫挪到
`_require_plugin` 之后 → `test_守卫排在存在性校验之前` 红。

### ⚠️ 同批审计出的、**本轮刻意不修**的一条（留给下一轮）

按 `bash` 统计（`grep -cE '^@router\.(post|put|delete|patch)'` vs `grep -c ensure_trusted_request`），
其余路由的写端点**同样没有来源守卫**：`model.py` 14 / `conversations.py` 7 / `ingest.py` 6 /
`export.py` 4 / `folders.py` 4 / `chat.py` · `classify.py` · `config.py` · `epub.py` · `synthesis.py` 各 2 /
`markdown_edit.py` · `process.py` · `references.py` · `generate.py` 各 1（`generate` 的两处守卫是
流式 GET 用的）。**为什么本轮只修插件**：

- 危害差一个量级 —— 插件链的终点是 `exec_module`（任意代码执行），其余最坏是改配置 / 删文档；
- 覆盖面差在**简单请求**这一条缝上：`PUT` / `DELETE` / `application/json` 的 `POST` 都会触发
  preflight，被 CORS 白名单挡下；真正能裸发的只有「`POST` + 无 body / 表单 body」的形态
  （插件那 4 个正好都在这一类）；
- 正确的修法大概率**不是逐个端点加两行**，而是一条 ASGI 中间件（`method not in GET/HEAD/OPTIONS`
  + `path.startswith("/api/")` → `is_trusted_request`），一处实现不可能漏 —— 但那要单独评估
  SSE / 导出下载 / CLI 无 Origin 三类路径，不该夹在打包中途做。

⚠️ **DNS rebinding 是这套守卫防不住的残余风险**（攻击者让页面占住 `evil.com:7413`，rebinding 后
同源、无 `Origin` 头 → 三条判据全放行）。正解是校验 `Host` 头必须是 `127.0.0.1` / `localhost` /
`tauri.localhost`，同样待单独评估（WebView2 的实际 Host 形态需实测）。两条一并记进下一轮议题。

---

## 2026-10-27 — 「重试失败项」：给已达上限的失败篇一条自助出口（0.5.20 内续）

**起因**：上一轮把触发方式做成两档时，那条已知边界是**明确记下来的** ——
`fail_count >= MAX_FAIL` 的篇在**任何档位、点任何按钮**都不会再被尝试一次，设置页会长期显示
「失败 N 篇」而**没有自助出口**。当时的处置是「先记着，正确的落点是『显式重试失败项』按钮」。
本轮就是把这个出口补上。

### ① 为什么不是「把 `force` 放宽成忽略上限」

`force` 跳过的是**闸门**（`_should_run`），不是 `_next_ids` 的取件范围 —— 后者只取 `pending`
与 `retry_ids(max_fail)`。把 `force` 改成忽略上限，等于**一次误点就对全部坏文档发起一次计费扫描**，
且没有任何一步是用户明确要求的。判据同「失败显式化」：**决定权在用户手上，不在默认行为里**。

于是正确的形状是「用户点一下 → 重置这些篇的状态 → 再跑一轮」，即 `requeue_failed()`。

### ② `VectorStore.requeue_failed()`：一条 UPDATE，三条语义

```sql
UPDATE vec_state SET content_hash = NULL, status = 'pending',
       fail_count = 0, error = NULL WHERE status = 'failed'
```

- **必须清 `content_hash`**：失败项里有「先成功后失败」的篇（改了内容、第二次编码失败），
  hash 还是上次成功时的值 ⇒ worker 的整篇快速路径判「hash 相等」直接 `mark_ok`，
  **一次编码都不发却显示成功**。清空后走块级比对。
- **不删 `vec_chunks`**：块级 hash 增量靠旧块比对 —— 重试的是「没编码成功的那些块」，
  不是整篇重花钱。
- **归零 `fail_count` 与 `error`**：前者是门槛，不归零则下一轮照样取不到；后者是给用户看的，
  不清则「重试成功了但状态里仍挂着上次的错」。
- **一条 UPDATE 一次事务**，不是逐篇 `mark_pending`（那会是 N 次写盘 + N 次唤醒）；
  锁外 `_notify_wake()`，与 `mark_pending` 同款。
- `available is False` 时返回 0，不抛 —— 同该文件其余方法（坏库只让语义检索不可用）。

### ③ 端点 `POST /api/search/vector/retry-failed`

`ensure_trusted_request` → 取 worker（None → 400 `VECTOR_DISABLED`，与 `/sync` 一致：
**不现场造 worker**）→ 取 `vector_store`（None → 503 `VECTOR_UNAVAILABLE`）→
`requeue_failed()` → **仅当 `requeued > 0`** 才 `worker.request_sync()` → 返回 `{requeued, status}`。

⚠️ **必须是 `request_sync()` 而不是 `await run_once()`**：理由与 `/sync` 逐字相同 ——
`attempted` 每轮私有，并发两条协程会让同一篇编码两次、计费翻倍而产物完全正常。
⚠️ **`requeued == 0` 时不叫醒**：没有要干的事，叫醒只是白跑一轮。

### ④ 前端：「重试失败项 N」

设置页语义检索状态区，**只在 `docs.failed > 0` 时渲染**（常驻只是噪音 —— 旁边的「待索引 N 篇」
已承担常态进度提示），数量直接写在按钮上，用户不必回上面那行状态里数。
动作结果就地生效（`runVector` 的既有路径），失败项归零后按钮自己消失。
语义检索未开启时 `disabled`（后端会 400，别让用户撞墙 —— 同「立即建立索引」）。

### ⑤ 测试与对照实验

- `test_vector_store.py::test_requeue_failed_清空计数与哈希但保留块`：一篇「先成功后失败」+
  一篇连失败三次 + 一篇成功 + 一篇已在队列，断言四篇各自不受影响、块仍在、再调返回 0（幂等）。
- `test_search_api.py` 三条：有失败项 → 重排 2 篇且 `request_sync` 恰好一次、状态从 failed 变 pending ｜
  无失败项 → `requeued: 0` 且**一次都不叫醒** ｜ worker 未启用 → 400 `VECTOR_DISABLED`。
- 前端 `searchIndexCard.test.tsx` 一条（含三态：无失败不渲染 / 有失败点一下 / 未开启禁用）。
- **两条对照实验**（都实测判红后还原）：SQL 去掉 `content_hash = NULL,` → 后端用例红；
  前端把 `{vec.docs.failed > 0 && (` 改成 `{false && (` → 前端用例红。

**影响范围**：`app/core/vector_store.py`、`app/routers/search.py`、`app/services/vector_worker.py`（未改，
仅复用 `request_sync`）、`frontend/src/api/search.ts`、
`frontend/src/components/settings/SearchIndexCard.tsx` + 两侧测试（后端 4 条 / 前端 1 条）。
文档：`docs/search-retrieval.md` §4.2 / §4.2.1（已知边界 → 已有出口）、`README.md`、`CLAUDE.md`、`docs/STATUS.md`。

---

## 2026-10-27 — 索引触发方式可配：攒批 / 手动 + 「立即建立索引」（0.5.20 内续）

**起因**：向量端到端验证跑通后，汇报里那句「worker 是 60 秒轮询，导入新文档后最多等一分钟才建索引」
引出了用户的直接要求 ——「**自动的话应该看累积到多少程度，比如每 100 篇重建一次；手动的话就是
用户手动触发重建一次；模型只在重建的时候加载**」。

第三条是判据，前两条是它的两个形态：本地 bge-small 是 22.8MB 的 ONNX，加载 3–5 秒、常驻约 90MB，
**为一两篇反复「加载 → 编码 → 释放」是纯浪费**。为此付出的代价是「零星几篇可能一直不建」——
而这必须**能被看见**，于是同一批做了「立即建立索引」这条显式通路。

### ① 两档触发方式 + 篇数阈值

`vector_worker.py` 新增 `TRIGGERS = ("auto", "manual")` / `DEFAULT_TRIGGER = "auto"` /
`DEFAULT_THRESHOLD = 100`；配置键 `search.vector_trigger` / `search.vector_threshold`。

- `auto`（默认）：`pending` + 尚可重试的 `failed` 攒够阈值才跑一轮。
- `manual`：**永不自动跑**，只有用户点「立即建立索引」才动。

⚠️ **这是本轮唯一一处「默认值 ≠ 改造前行为」，且是用户拍板的语义变更**：改造前是「有活就干」，
现在默认攒到 100 篇 —— 对小于 100 篇的库，等于**不会自动建索引**。三条补偿缺一不可：
设置页的「待索引 N 篇」计数（不建也要看得见）、「立即建立索引」按钮（恒在，`auto` 档下是兜底、
`manual` 档下是唯一通路）、状态区 `trigger` / `threshold` 回显（改了要能确认改到了）。
⚠️ **「重建」与「建索引」在这里是两件事**：用户口语里的「重建」指的是「跑一轮」，
不是 `rebuildVectorIndex`（那个清空口径与全部块、重花一遍全库的钱）。文案与代码都按后者区分。

### ② 闸门住在执行层之外 ⇒「达不到阈值就不加载模型」是结构保证

`_should_run()` 排在 `_next_ids` 与 `_warm` **之前**：达到阈值才取活、才预热模型。
顺序反过来的失效形态是「每轮都先把 22.8MB 载进内存再判断没事干」，行为上看不出区别，
只有内存曲线知道。`test_vector_worker.py` 用 `eng.warm_calls == 0` 钉住。

⚠️ **重试不受闸门约束**：`failed` 且 `fail_count < MAX_FAIL` 是**纠错**不是**增长**，
被闸门挡住会让一篇坏文档永远等不到重试，而 `fail_count` 本来就是它的上限。

### ③ 写点叫醒 worker（那 60 秒的去向）

`VectorStore.set_wake_callback()` / `_notify_wake()`：`mark_pending` 末尾回调一次，
于是「攒够阈值的那一瞬间」就开工，不必等下一次心跳。

⚠️ `mark_pending` 可能跑在 `asyncio.to_thread` 里（写点全在同步路径上），而
`asyncio.Event.set()` **不是线程安全的** ⇒ `vector_runtime._start_worker` 用
`loop.call_soon_threadsafe` 包一层。⚠️ 两个注册点都走
`getattr(vec, "set_wake_callback", None)` 兜底：唤醒是**可选优化**，不是 `VectorStore`
的对外契约（同 `precheck` / `warmup` / `release` 三个钩子的取向），测试里的假 store 没有它也不该炸。

`DEFAULT_INTERVAL = 60.0` 语义随之变更：从「唯一触发方式」退化为**纯兜底心跳**
（唤醒丢失 / 外部改了 `vec.db` / 启动瞬间漏标）。

### ④ 「立即建立索引」不能直接 `await run_once()`

`run_once` 的 `attempted` 集合是**每轮私有**的。另起一条协程跑它，会与 `loop` 里那一轮并发，
两轮互不可见 ⇒ **同一篇在两条协程里各编码一次，计费翻倍而产物完全正常**。
故手动触发走「置 `_force` 旗标 + 唤醒 `loop`」，执行永远留在 `loop` 这一条线上。

新端点 `POST /api/search/vector/sync`；worker 没在跑（未启用 / 索引不可用）时
400 `VECTOR_DISABLED` —— **不现场造一个 worker**（那会绕过开关与 precheck）。

### ⑤ 校验分野：`trigger` 显式报错，`threshold` 静默夹紧

判据是「**这个值错了会不会改变产物的性质**」：
- 非法 `trigger` → 400 `VECTOR_BAD_TRIGGER`，**零写盘**。静默退回 `auto` 会让「选了手动档」
  变成一句谎话，而 worker 继续自动花钱。
- `threshold ≤ 0` → 夹紧到 1。0 只是让闸门恒真（= 用户想要「多建点」），错的是**量**不是性质。
- 状态侧读回一律宽：`vector_runtime._trigger` 脏值回退 `auto`、`_threshold` 非法值回退默认
  —— 一边是「用户刚选的值」，一边是「磁盘上可能过期的值」。
- 写入是**合并式**：只带 `threshold` 不会抹掉上一轮写的 `vector_trigger`
  （`test_vector_toggle_sets_trigger_and_clamps_threshold` 顺带钉住这条，同 `saveChatPrefs` 的教训）。

### ⑥ 前端：设置页「建索引时机」

- 两档文案由 **capabilities** 下发（`triggerPolicies` / `_TRIGGER_META`），前端只存联合类型
  `TriggerPolicy` —— 与 `ReleasePolicy` 同一条纪律（加一档要改后端，前端再存一份则设置页
  永远看不到它，而界面上看不出少了一档）。
- **阈值输入框只在 `auto` 档渲染**：`manual` 承诺「永不自动跑」，摆着只会让人以为
  「攒够 N 篇还是会自动跑」，而那正是这一档保证不会发生的事。
- 阈值**失焦/回车才提交**（输入过程中零请求），非法值或未改动则回弹到后端值 ——
  受控到后端值会让每敲一位就发一次请求。
- 「立即建立索引」在「建索引时机」段下方，语义检索未开启时**禁用**（否则撞后端 400）。

⚠️ **`threshold` 的前端判空用 `!== undefined` 而不是真值判断**：`0` 是合法入参（后端夹紧成 1），
`if (threshold)` 会把它静默丢掉。

⚠️ **它与 §4.5 砍掉的「积压阈值（pending ≥ 50% 才显示进度条）」不是同一件事**：那条是**显示**的
阈值（结论是「永远显示更好」），这条是**执行**的闸门。

**影响范围**：`app/services/vector_worker.py`、`app/services/vector_runtime.py`、
`app/core/vector_store.py`、`app/core/config.py`、`app/routers/search.py`、`app/routers/capabilities.py`、
`frontend/src/api/{core,search}.ts`、`frontend/src/components/settings/SearchIndexCard.tsx`
+ 两侧测试（后端 `test_vector_worker.py` 8 条 / `test_search_api.py` 5 条，前端 7 条）。
前端全量 **82 files / 1297 tests** 全绿。

---

## 2026-10-27 — 插件系统审查修复：编码、锁超时、钩子成对（0.5.20 内续）

**起因**：向量化插件落地后做了一轮代码质量与漏洞审查，两条 P1 都是**静默类**——
一条不报错地把乱码文本送进编码器，一条把整个后端卡住三分钟。用户拍板全修。

### ① P1 子进程 stdin 编码（`PYTHONUTF8` + 三个流都 reconfigure）

父进程 `Popen(..., encoding="utf-8")` 只管**父侧**。子进程未开 UTF-8 模式的 Windows 上
`sys.stdin.encoding == "gbk"`、`errors == "surrogateescape"`：进来的 UTF-8 字节被 GBK 解码、
解不掉的留成代理字符而**不抛异常**，`json.loads` 照样成功 ⇒ **编码出来的是乱码文本的向量**，
检索质量下降、日志一片绿。开发机因 `PYTHONUTF8=1` 从未复现，假 server 又全 ASCII。

三处修：
- `plugin_loader._plugin_env()`（新）—— `os.environ.copy()` + `PYTHONUTF8=1`，
  `_run_external` 与 `_spawn_locked` **共用一份**（只改一处 = 「文件引擎好好的、向量插件全是乱码」）
- `local_embedding/server.py` 补 `sys.stdin.reconfigure(...)`（此前只配了 stdout/stderr）
- 三个文件引擎模板（pymupdf4llm / faster_whisper / tesseract）同款补齐 —— 它们的
  `json.load(sys.stdin)` 走的是同一条盲区
- [plugins.md](plugins.md) 的「三条硬要求」→ **四条**，第四条专讲编码，并写明文件引擎同样适用

### ② P1 `release()` 抢锁无超时 → 阻塞事件循环

`vector_runtime._stop_worker` 在 `await task` 之后**同步**调 `worker.release_now()`。而
`task.cancel()` 打断不了已经跑在 `asyncio.to_thread` 里的编码线程，它可能仍持着 `_proc_lock`
等到一次往返结束（上限 `EMBED_REQUEST_TIMEOUT = 180s`）⇒ 整个事件循环停摆，用户看到顶栏
「正在重试」而真正的原因是「在等一次编码」。原注释「杀进程是微秒级」忽略了还有 `proc.wait(3)`。

三处修（两层都要，缺一层就有漏网场景）：
- `_PluginEmbeddingAdapter.release()` 改为 `acquire(timeout=RELEASE_LOCK_TIMEOUT)`（新常量 5s），
  抢不到返回 `False`（语义：正在编码中，这次不释放，下一轮 `maybe_release` 再来）
- `_stop_worker` 的 `release_now()` → `await asyncio.to_thread(...)`
- `VectorWorker.loop` 的 `maybe_release()` → `await asyncio.to_thread(...)`

### ③ P2 三条

- **闲置门槛守卫**：`EMBED_IDLE_RELEASE` 与 `embedding_local.DEFAULT_IDLE_RELEASE` 都是 600，
  但此前**没有任何东西钉住这个相等**。`idle` 档由引擎自己判门槛（worker 刻意不传数字），
  分叉时零报错而「换引擎后释放节奏变了」。加 `test_idle_release_threshold_matches_builtin`。
- **进程内 `release` 没转发**：`warmup()` 用 `getattr` 转发了插件钩子，`release()` 没有 ⇒
  `EmbeddingPlugin` 写了 `release` 也永不被调用，而外部模式那份工作正常（插件换个形态发布
  才发现「选常驻内存就永远不还」）。补转发，[plugins.md](plugins.md) 同步写进程内插件可以
  自己实现 `warmup` / `release` 两个钩子。
- **模板 `command` 写死 Windows 路径**：`probe_venv` 是纯文件系统检查、不读 `command`，
  两边判据不同源 ⇒ Linux/macOS 上「自检说就绪、一 spawn 就 FileNotFoundError」。
  `local_embedding_plugin` 的类属性保留 Windows 版，`__init__` 里按 `os.name` 换 POSIX 版。

### ④ P2 插件配置明文（文档明示，不动密钥库）

`plugin_configs` 明文落 `config.yaml`，**不走 DPAPI 密钥库** —— 那套成对表按扁平键名组织，
装不下任意形状的嵌套 dict。改密钥库是另一个工程，本轮把它从「隐含风险」变成**显式约定**：
[plugins.md](plugins.md) 与 `routers/plugins.py` 的保存端点各写一句「`config_schema` 里不要放
密钥，凭据请走环境变量或系统凭据管理器」。

### ⑤ P3 四条

- `server.py` 的预推理 `dim = ...` 挪进 `try` —— 推理失败时走 `{"ready": false}`，
  否则进程带 traceback 退出，主进程只能报「（进程无输出）」，原因一个字都不在消息里
- `_drain_stderr` 改收**局部 list**（`_spawn_locked` 传参）—— 旧进程的 drain 线程可能活过
  重启那一刻，用 `self._stderr_tail` 会让它的最后几行串进新进程的尾部
- `ready` 里的 `dimension` 非整数时显式报「报告的维度不是整数」
  （此前 `int()` 抛裸 `ValueError`，把「插件写错协议」显示成主程序内部错误）
- 删 `plugin_loader.py` 两处重复 `import shutil`、删 `test_plugin_embedding.py`
  一个未使用的 fixture 参数

**影响范围**：`app/engines/plugin_loader.py`、`app/services/vector_runtime.py`、
`app/services/vector_worker.py`、`app/routers/plugins.py`、四个插件模板、`docs/plugins.md`。
行为变更只有一条：Linux/macOS 上本地向量化插件现在真能起来（此前必定 FileNotFoundError）。
其余全是「本该如此」的修正，正向路径逐字节不变。`bash scripts/check-all backend` 全绿（44s）。

---

## 2026-10-27 — 向量化插件：`EmbeddingPlugin` + 常驻子进程（0.5.20 内续）

**起因**：打包版的本地向量检索一直不可用 —— 内置的 `local-bge-small` 依赖
`onnxruntime` / `tokenizers`，而冻结的 `LiMoBackend.exe` **没有 pip**，用户补不了。
设置页那句 precheck 只能指一条路：「改用云端」。用户拍板：**改成进程外插件包**
（离线能力不该只有源码版有）。

### ① 新基类 `EmbeddingPlugin`（`EnginePlugin` 的子类）

原先「本地模型走插件口子」这条设计**一直是空的**，理由写在 [search-retrieval.md](search-retrieval.md)
§6.1：`EnginePlugin.execute(file_path)` 是「一个文件 → 一段文本」，装不下「一组文本 → 一组向量」。

新基类只换入口，**扫描 / 门控 / 启用停用 / 卸载 / 一键装依赖全部复用**（`issubclass` 一脉相承）：

```python
class EmbeddingPlugin(EnginePlugin):
    category = "embedding"
    model_id: str = ""      # 四元组身份之一
    dimension: int = 0      # 不声明会被自检拦下
    is_local: bool = True
    requires_modules: tuple[str, ...] = ()   # 交给 adapter 用 probe_venv 查
    requires_files: tuple[str, ...] = ()
    def embed(self, texts) -> list[list[float]]: ...
```

⚠️ 声明文件 `from app.engines.plugin_loader import EmbeddingPlugin` 会把**基类本身**带进模块命名空间，
而它也是 `EnginePlugin` 子类 ⇒ 原判据 `obj is not EnginePlugin` 拦不住，会被当成插件实例化
（幽灵条目 `name=""`）。`init_plugins` 改判 `obj not in (EnginePlugin, EmbeddingPlugin)`，
`test_init_plugins_skips_base_class_import` 钉住。

`_peek_plugin_meta` 同步放宽：识别 `EmbeddingPlugin` 基类名，并**补上继承来的 `category`**
（否则「禁用即不加载」对这类插件失效、禁用条目还会分错组）。

### ② `_PluginEmbeddingAdapter`：常驻子进程 + NDJSON over stdio

**为什么必须常驻**：模型加载 3–5 秒，而查询侧 `QUERY_TIMEOUT = 3.0` —— 每次搜索现起一个进程
等于每次搜索都超时。**为什么不是一次一进程**：同上，且每次要重载 22.8MB。

| 时机 | 一行 JSON |
|---|---|
| 启动 | `{"ready": true, "model_id": ..., "dimension": N}` / `{"ready": false, "error": ...}` |
| 请求 | `{"id": N, "texts": [...]}` |
| 响应 | `{"id": N, "vectors": [[...]]}` / `{"id": N, "error": "..."}` |

**失败分两档，判据是「下一步动作是否相同」**：

- **单条 `error` / 条数不符 / 维度不符** → 抛错但**进程保留**（下一批继续用）
- **非 JSON 行 / `id` 错位 / 请求超时** → 抛错并**杀进程**（管道状态已不可信；留着只会让每一轮
  都读到上一条的结果）。子进程被外部杀掉（OOM）时下次调用自动重起。

**两个静默坑各有对策**：
- `stderr` 必须有**后台 drain 线程** —— Windows 管道约 64KB 写满，子进程卡在写日志上，
  `ready` 永远读不到，现象是「启动超时」而真因在没人读的那段里（测试用 200KB stderr 灌入钉住）；
- 声明的 `dimension` / `model_id` 与 `ready` 报告不符时**启动即失败** —— 写进库之后口径不符
  是**静默的**（检索质量下降，不报错）。

`warmup()` / `release(after_idle)` / `precheck()` 三个可选钩子齐备（**不进 Protocol**，
消费方 `getattr`），于是 `vector_worker` / `vector_search` / `vector_runtime` **零改动**即可消费插件引擎。

### ③ `plugin_config` 同步给插件实例（同时修好文件引擎插件的同类问题）

`precheck()` 要按用户配置判断（模型目录、ONNX 文件名），而配置此前只存在 adapter 上 ——
插件自己读到的是类默认值，自检判的是**另一份配置**。

`_PluginEngineAdapter.plugin_config` 改成 **property + setter**：写入时同步给插件实例；
`EnginePlugin` 基类补类属性兜底（`__slots__` / 只读属性的插件退回默认值，不炸）。

**配置怎么进子进程**：环境变量 `LIMO_PLUGIN_CONFIG`（JSON），在子进程启动时读一次。
没往 ready 握手行里混配置 —— 那是协议行，混进去等于让「协议」与「配置」互相耦合版本。

### ④ `_resolve_command` 把命令行开关当成了路径

`command = ["…/python.exe", "-u", "server.py"]` 里 `-u` 不是绝对路径 → 被拼成
`<插件目录>/-u`，Python 报 `can't open file '...\-u'`（读起来像「解释器找不到脚本」）。
修法是 `-` 开头或绝对路径一律原样保留。这同样会打中带 flag 的文件引擎插件。

### ⑤ 模板：`local_embedding_plugin.py` + `local_embedding/`

- 声明文件：`name = "local-bge-small"` **刻意与内置同名**（用户优先覆盖内置），
  `model_id` / `dimension` / 默认模型目录与内置**完全一致** ⇒ 装了插件不必重建向量库
- `server.py`：CLS pooling + L2 归一化 + `enable_cpu_mem_arena=False`（照抄内置 `Embedder`，
  它是产品与评测共用的唯一一份实现），每行 flush，日志走 stderr
- `requirements.txt` / `README.md`（安装四步 / 模型来源 / **换权重必须改 `model_id`** / 三个常见故障）

### ⑥ 内置引擎的 precheck 文案补上第二条出路

打包版此前只说「可改用云端向量化引擎」—— 那会让人以为离线选项在安装版上不存在。
现在给两条：云端，或装本地向量化插件。

**影响范围**：`backend/app/engines/plugin_loader.py`（`EmbeddingPlugin` / `_PluginEmbeddingAdapter` /
`_peek_plugin_meta` / `init_plugins` / `plugin_config` property / `_resolve_command`）、
`backend/app/engines/embedding_local.py`（`_fix_hint` 文案）、
`backend/plugins/examples/`（`local_embedding_plugin.py` + `local_embedding/` 四文件 + README 两处）、
`backend/tests/test_plugin_embedding.py`（新增 27 条，全绿：契约 / 进程生命周期 / 协议违例 /
配置传递 / 自检 / 进程内路径 / 静态解析与分派）、`docs/plugins.md`（新增「向量化插件」整节）、
`docs/search-retrieval.md`（§3.1 状态、§6.1（4）（6）重写、阶段表第 4 行）。

**前端零改动**：`routers/search.py::_embedding_models` 只读 `display_name` / `model_id` /
`dimension` / `is_local` / `precheck()`，插件 adapter 全部具备。

**未做**：rerank 仍未实现；真实库（131 篇）端到端实测与 `eval_search.py --vector` 产品侧对照
仍是待办（2026-10-26 用户决定本轮先不做）。

---

## 2026-10-27 — 进程内（trusted）插件补三条边界：异常 / 返回值 / 依赖（0.5.18 → 0.5.19）

上一轮让 `precheck` 进了契约、四个模板都能自检了。这一轮补的是**「trusted 插件可以当代码跑」
的前提** —— 前两条是既有的静默失效点，第三条是昨天新能力的边界没划干净。

### ① 进程内插件的异常没有边界（最严重）

`_PluginEngineAdapter.execute` 的 inproc 分支此前是**裸调用** `self._plugin.process(...)`。
进程外插件崩了只是子进程退出（stderr 带回来），进程内插件崩了，异常顺着调用链冒到
adapter 层，而那里只留得下一句：

```python
raise FatalError(f"Doc 引擎 {eng_name} 失败: {str(e)[:100]}") from e   # document.py
```

**traceback 全丢、原因截到 100 字符**。第三方代码最需要的就是那两样。

**修法**：边界放在 adapter（唯一同时看得见插件名与原始异常的地方）——
`logger.exception` 留住完整 traceback，对外抛
`FatalError("插件 <name> 执行失败：<类型>: <原因>")`。上游那句 `[:100]` 因此拿到的是
一句已经说清了「哪个插件、什么错」的话，而不是一段被腰斩的第三方栈顶。

### ② 返回值类型非法时，错误被讲成了「文件没内容」

旧代码把任何返回值按 `str` / `dict` 三元包装：

```python
payload = out if isinstance(out, dict) else {"text": out}   # out=None → {"text": None}
```

插件返回 `None`（忘了 return、或某条分支漏了）→ `DocResult(text=None)` → 上层报
**「返回空结果」**。那句话把「插件坏了」讲成了「这份文件没内容」，用户会去换文件、
重试、以为是 PDF 的问题。现在非 `str` / `dict` 当场抛
`FatalError("插件 <name> 返回了不支持的类型 <T>（契约要求 str 或 dict）")`。

### ③ 「一键安装依赖」不该出现在进程内插件上

`hasRequirements` 的判据此前是**「子目录里有没有 requirements.txt」**，而它真正要回答的是
**「能不能一键安装」**。进程内插件不从 venv 加载，给它装依赖只会造出一个用不上的 `.venv`：
界面显示「已安装」、插件 `import` 仍然失败、两边都不报错。

前端其实早有门控（`PluginSettings.tsx` 的 `execMode === 'external'`），**但接口本身没校验** ——
`curl` 一下就能在插件目录里堆出那个 venv。故：

- 抽 `_deps_installable(adapter)` 作为**唯一判据**（`external` + 有 `requirements.txt`），
  `hasRequirements` 从它取值；
- `install_plugin_deps` 早退返回 `status="inproc"` 并说明原因，**一个目录都不建**。
  ⚠️ 早退**只判 execMode**，不判 `_deps_installable` —— 否则「进程外但没有 requirements.txt」
  会被错说成「在进程内运行」，而那条路径本来有自己的 `no-requirements` 状态。

**影响范围**：`backend/app/engines/plugin_loader.py`（4 处）、`backend/tests/test_plugin_loader.py`
（新增 4 条：异常带插件名 / 类型非法 / 进程内无安装入口 / 接口拒绝且零写入，39 passed）、
`docs/plugins.md`（核心边界补「进程内的两条硬边界」+ 安装段 + 端点表）、
`frontend/src/api/plugins.ts`（`hasRequirements` 注释改准）、
`backend/plugins/examples/simple_trusted_plugin.py`（模板补依赖与异常说明）。

**未做**（下一步）：`on_load` / `on_unload` 生命周期钩子、超时、失败熔断。

---

## 2026-10-27 — 插件模板「装得上却用不了」三个静默缺陷 + 新增本地转写模板（0.5.17 → 0.5.18）

上一轮把 `precheck` 和依赖探测做进了契约，这一轮去验**官方模板是不是真的能用**。
结果是三个都不行 —— 而且三个缺陷的共同点是「界面上完全看不出问题」。

### ① 「一键安装依赖」永远找不到目录（最严重）

`_plugin_subdir` 此前用**声明文件 stem** 推子目录：`pymupdf4llm_plugin.py` → 找
`pymupdf4llm_plugin/requirements.txt`。但官方模板的目录叫 `pymupdf4llm/`（没有
`_plugin` 后缀），于是：

```
requirements.txt 找不到 → install-deps 返回 {"status": "no-requirements"}
                        → 前端**根本不渲染安装按钮**
                        → 用户以为这个插件不需要依赖
```

插件本身照样能加载（`command` 里的路径是对的），所以整件事从界面上看就是
「这个插件零依赖」。而用户照着 README 手动建 venv 又会绕过它，于是模板长期没被发现。

**修法**：优先从 `command` 里的相对路径推导（跳过 `.venv` 段、绝对路径、单段路径），
推导不出才回退 stem。理由是**同源** —— `_run_external` 执行时解析的就是
`self._plugin_dir / part`，两处必须指向同一个目录；让「安装」与「执行」各算一次
目录，迟早分叉。

### ② 三个模板都没有 `precheck`，依赖没装也显示「就绪」

`precheck` 是上一轮刚进契约的能力，模板没跟进 —— 用户看到绿灯、切到引擎、点生成，
才发现 `No module named 'pymupdf4llm'`。现在 pymupdf4llm / tesseract 都补上了
（后者还额外查 tesseract 二进制），trusted 模板用注释示范正确写法。

⚠️ **进程外插件必须用 `probe_venv` 而不是 `probe`**：依赖在**子 venv** 里，主进程
`sys.path` 没有它们，用 `probe` 会**永远**报「无法导入」—— 那是与「检查不出来」
相反的失效：用户照着提示反复重装，而东西一直是好的。`docs/plugins.md` 的示例
此前正是错的那一版（`probe("pytesseract")` 配进程外形态），一并改正。

### ③ venv 判据有两份实现

`plugin_loader._venv_valid`（设置页的 `depsInstalled` 字段）与插件 `precheck`
回答的是**同一个问题**。两份实现的失效形态是「设置页说依赖已装、自检说没装」——
同一个 venv，两个答案，两边都不报错。

**修法**：判据整体搬进 `core.deps.probe_venv`（含 `pyvenv.cfg` / base python 校验 /
WinError 448 的 `OSError` 兜底），`_venv_valid` 变成薄委托。顺带把 `home` 的解析
从 `startswith("home =")` 改成按 key 判（`=` 两侧空格在各家工具下不一致）。

⚠️ 探的是**文件系统**而不是真起子进程：`precheck` 被 `GET /api/plugins` 调用，
契约明写「必须轻」。它是近似（目录在 ≠ 一定能 import），真正的失败仍由首次执行
显式报错兜底 —— 只是把最常见的几种缺失提前到设置页。

### ④ 新增 faster-whisper 本地转写模板（第四个模板）

核心已移除 whisper（CUDA 依赖打爆安装包），内置两个 ASR 引擎都是云端的。于是
「离线、零费用、不外发」的本地转写在**核心里是真空缺口** —— 正是口子原则该管的
那类能力。

`plugins/examples/faster_whisper/`：`faster-whisper`（CTranslate2 推理）跑在独立
venv 里，`ctranslate2` / `onnxruntime` / `tokenizers` 数百 MB 不进主包。
配置项含模型规格 / device / compute_type / language / VAD / HF 镜像与缓存目录。

三条设计取舍：
- **逐段轮询 `cancel_file`**（转写是流式生成器，解码发生在迭代时），命中即退出且
  **零输出** —— 交出半截 `segments` 会被主进程当成成功落库，用户以为转完了。
- **`device=cuda` 失败绝不静默退回 CPU**：缺 cuBLAS/cuDNN、compute_type 非法、
  模型下载失败，四种原因的处置完全不同，混成「慢一点」会让用户以为 GPU 在跑。
- **HF 镜像只在子进程设 `os.environ`**，不污染主进程、不写用户的系统变量。

⚠️ **首次转写要下载模型**（默认 small 约 250MB），这件事**不在**「一键安装依赖」里
—— 模型跟着 `model` 配置项走、缓存可被 `download_root` 指到别处，放进安装步骤会让
「装好但换模型又没下载」显得莫名其妙。README 里单列一节说明。

### 测试

- `test_deps.py` 新增 13 条 `probe_venv` 用例（没建 / 缺解释器 / 缺 pyvenv.cfg /
  base python 已失效 / 缺 site-packages / 缺依赖 / 单文件包 / OSError 兜底），
  含**对照实验**：同一个 venv，`probe` 说没装而 `probe_venv` 说装好了。
- `test_plugin_loader.py` 新增 6 条 `_plugin_subdir` 用例 + 1 条**拿真实模板文件跑**
  的守卫（子目录存在、`engine.py` 与 `requirements.txt` 都在）。
- 新增 `test_plugin_examples.py`：用**假的 `faster_whisper` 包**把 `engine.py` 真跑一遍
  —— 契约 key 名、空白段丢弃、取消零输出、两种失败显式报错。纯静态断言对这些
  **全部看不出来**。

### 影响范围

- 修复对**存量用户零行为变化**：`_plugin_subdir` 只在「一键安装」链路上被调用，
  而它此前在三个官方模板上都是错的（用户要么手动装、要么根本没装成）。
- `probe_venv` 是新函数，`_venv_valid` 的调用方接口不变。
- faster-whisper 是**新增模板**，不安装就完全不存在。

### 验证

后端门禁 `check-all backend` 全绿（1400+ 用例，含新增 27 条）。
模板本身**尚未在真机装依赖跑过**（需下载约 400MB 依赖 + 250MB 模型），
`engine.py` 的契约由假包端到端覆盖。

---

## 2026-10-27 — 打包版日志编码：GBK → UTF-8（0.5.16 → 0.5.17）

装完 0.5.16 第一件事就撞上了。`limo_backend_stderr.log` 里躺着的是

```
--- Logging error ---
UnicodeEncodeError: 'gbk' codec can't encode character '✨' in position 69
Call stack:
  File "app\core\sample_doc.py", line 125, in seed_sample_doc
```

而不是那句本该出现的「示例文档已创建: ✨ 使用说明（示例）」。

### 根因

PyInstaller 是 `console=False`，Tauri 把后端的 stdout/stderr **重定向到文件**。
Windows 上 Python 的流在**非 tty** 时按 locale 编码（简体中文系统 = GBK），于是
emoji / 生僻字统统编不进去 → `UnicodeEncodeError` → `logging` 把它吞成一行
"Logging error"，**真正的消息反而消失了**。

同一件事还有第二个面：Rust 侧读日志用 `String::from_utf8_lossy`
（[lib.rs](../frontend/src-tauri/src/lib.rs) 的 `read_tail`），GBK 内容在「启动失败」
屏上全是 `���` —— 那份日志存在的意义就是给人看，读出来是乱码等于没有。

⚠️ 功能本身没受影响：出错的 `logger.info` 在 `seed_sample_doc` 的 `try` 块**末尾**，
写盘早已完成，后续照常打出 `Search index ready (143 docs)` 与
`Application startup complete`。但「启动失败」屏拿到的证据里混着一串
"Logging error"，正好在最需要读日志的时候变成噪音。

### 修法

`backend/main.py` 的流归一化（紧接 `sys.stdout is None` 兜底之后）分两支：

| 流 | 处置 | 理由 |
|---|---|---|
| **非 tty**（重定向到文件 / 管道） | `reconfigure(encoding="utf-8")` | 那是文件不是终端；UTF-8 是唯一能让 Rust 侧正确读回的编码 |
| **tty**（终端里直接跑） | **保持终端编码**，只设 `errors="backslashreplace"` | 老 cmd 代码页 936，强行 UTF-8 会满屏乱码；backslashreplace 让写不出的字符变成 `\u2728` 而不是炸掉整次日志调用 |

⚠️ 两支都设 `errors`：UTF-8 本身什么字符都能编码，所以这条**只在 tty 分支真正起作用**，
非 tty 分支留着兜 lone surrogate 这类边界。

### 影响范围

- 只改输出的**字节表示**，不碰任何业务逻辑
- 历史日志文件不迁移，下次启动追加时才变 UTF-8（同一文件里前后两段编码不同 —— 按行读仍然清楚，
  而真正要紧的是**下一次崩溃的证据**里不再有噪音）
- **开发环境行为完全不变**：终端里跑 uvicorn 走的是 tty 分支，与改造前逐字相同

### 验证

对照实验：同一份 `open(path, "w", encoding="cp936")` 流，写 `✨` 在归一前抛
`UnicodeEncodeError`、归一后正常落盘并按 UTF-8 读回；tty 分支断言 `encoding` 仍是
`cp936`、只有 `errors` 变成 `backslashreplace`。后端门禁 `check-all backend` 全绿。

---

## 2026-10-27 — 插件/引擎「就绪自检」进契约 + 依赖探测换成真 import（0.5.15 → 0.5.16）

起因是实测出来的一个谎：安装版里 `tokenizers` 的目录只有 7.5MB 的 `tokenizers.pyd`、
**没有 `__init__.py`**。`importlib.util.find_spec` 说「找得到」，`import` 也成功
（拿到一个空壳 namespace package），于是本地向量引擎的自检报「就绪」——用户切过去，
编码那一刻才炸。代价已经付了（选了引擎、点了开始、一次失败任务）。

### ① `core/deps.py::probe()` —— 依赖可用性的唯一判据

`probe(module, attrs=(...))`：**真 import 一次并查关键属性**，返回 `None`（可用）
或一句可直接展示的原因。三种 `find_spec` 看不出来的形态一并覆盖：namespace package
（导入成功但缺属性）、DLL 缺失（`__init__` 抛 `OSError`）、包的 `__init__` 自己炸。

代价是首次 import 会执行包的顶层代码（onnxruntime 约几百毫秒），故只给「回答就绪与否」
用；第二次及以后由 `sys.modules` 天然命中，**不需要另做缓存层**。

⚠️ `Embedder.__init__` 同步改成走它：原先写的是 `except ImportError`，而 DLL 缺失抛的是
`OSError` —— 抓不到，错误会在更远的地方以更难懂的形式出现。

### ② `EnginePlugin.precheck()` 进契约

```python
def precheck(self) -> Optional[str]:   # None = 就绪，str = 给用户看的不可用原因
    return None
```

默认实现返回 `None`（「装上了就能用」），存量插件零改动、零回归。需要外部二进制 /
模型文件 / Key 的插件重写它。**必须「报告」而不是「第一次执行时抛异常」**——自检在
设置页跑，用户还没付出任何代价。

⚠️ 这一条进契约、而 `precheck`/`warmup`/`release` 三个同类钩子**刻意不进**
`EmbeddingEngine` 协议，**不是双标**：那个协议是 `runtime_checkable`，加成员会让云端
引擎不再满足它；`EnginePlugin` 是普通基类，加一个有默认实现的成员不排斥任何子类。

⚠️ `_PluginEngineAdapter.precheck()` 把插件的异常兜成「不可用」。兜底放在这一层而非
各消费方 —— 插件是任意第三方 Python，任一处漏了 try/except 的症状都是「打开插件设置页
整页 500」，看起来像主程序坏了。

### ③ 三态 `ready` 下发到设置页

`GET /api/plugins` 每条新增 `ready` / `precheckReason`：

| `ready` | 含义 | 前端 |
|---|---|---|
| `true` | 自检通过 | 无 |
| `false` | 自检说不可用 | 卡片上渲染 `precheckReason` |
| `null` | **无法判断**（插件被禁用、代码没加载，自检无从调用） | **不渲染** |

⚠️ `null` 不能折成 `false`：那会让每个被禁用的插件都挂一条点不动的「不可用」。
同 `lib/search-health.ts` 的「问不到 ≠ 坏了」。测试有反向断言（`is not False`）钉住。

⚠️ `/api/plugins` 改走 `asyncio.to_thread`：`enumerate_plugins` 现在会调第三方
`precheck()`，真 import 一个原生库几百毫秒，放在事件循环上会让 SSE 心跳与健康检查
一起停住（现象：「打开插件页时应用卡一下」）。

### ④ 文档同步

- **内置示例文档**（`sample_docs/notes.md` / `polished.md`，即文档库里那篇「✨ 使用说明（示例）」）：
  清掉三处「AI 审核」残留（该功能 2026-08-16 已移除，读者一直被告知有一个并不存在的功能）；
  补上文档库整理、多篇合成、追问建议、语义检索（两个向量引擎 + 本地引擎要自备
  `onnxruntime` / `tokenizers` 与模型文件）、插件系统；导出一栏补 Word / PDF。
  顺手修一处过期表述：「图片 OCR（本地引擎）开箱即用」—— 本地 OCR 自 2026-08-02 起已不在内置。
  ⚠️ 内容指纹变了，用户库里的那篇示例文档**下次启动会被自动更新**（这是 `sample_doc.py`
  的既有设计：示例文档兼作「更新说明」）；指纹无变化时仍尊重用户的删除。
- `README.md` 新增「插件」一节：安装方式、进程外/进程内两种形态、**不可用原因直接写在插件卡片上**。
- 版本 `0.5.15 → 0.5.16`（`scripts/sync-version.py patch`，本次是修复不是新功能）。

### 影响范围

`core/deps.py`（新）、`plugin_loader.py`（契约 + adapter + 枚举）、`routers/plugins.py`
（线程池）、`engines/embedding_local.py`（`precheck` 与 `Embedder.__init__` 的判据）、
前端 `api/plugins.ts` + `PluginSettings.tsx`（卡片多一行原因）。插件列表接口**只增字段**，
旧消费方零影响。`docs/plugins.md` 补 `precheck` 的写法与理由。

---

## 2026-10-27 — 0.5.15 打包准备：spec 补向量模块 + 清理无用文件

这一版是**发行整理**，不是功能改动。两件事：补打包配置，删磁盘上的历史残留。

### ① `LiMoBackend.spec` 补 8 个模块（打包版会塌的一处）

语义检索（2026-10-26 阶段 1–4）新增的模块 **全部在函数体内延迟导入**：

| 模块 | 导入位置 |
|---|---|
| `app.core.vector_store` / `vector_index` | `main.py` 的 startup / shutdown |
| `app.services.vector_runtime` / `vector_worker` / `vector_search` | 同上 + `routers/search.py` |
| `app.engines.embedding_dashscope` / `embedding_local` | `engines/__init__.py::init_engines()` |
| `app.core.search` | 五个写点（`update_index`） |

这与 spec 里**已列**的那批（`app.engines.doc_*`、`app.services.docx_export`、
`app.generation.generator` ……）是同一形态 —— 它们之所以被逐条列进 `hiddenimports`，
正是因为 PyInstaller 对函数体内导入的追踪不可靠。本轮按同一惯例补齐 8 条。

⚠️ **失效形态**：开发版全绿，装好的版本点开「检索索引」开关后 worker 起不来，
界面上只说「不可用」，日志里是一行 `ModuleNotFoundError` —— 只有打了包才看得见。

⚠️ 刻意**没有**把 `onnxruntime` / `tokenizers` 加进 `collect_all`：本地 embedding 引擎
在打包版按设计不可用（两者不在 `pyproject.toml`，且桌面安装版没有 pip）。`embedding_local`
本身顶部零重依赖 import，进包是安全的 —— 调用时才抛 `MissingDependency` 并给出安装命令。

### ② 清理无用文件（约 300 MB）

全部已在 `.gitignore` 内、且**零引用**（`grep` 覆盖 `*.md` / `*.spec` / `*.sh` / `*.json` / `*.toml`）：

| 删除 | 原因 |
|---|---|
| `build/`（272 MB） | PyInstaller 中间产物，且含旧项目名 `DeepDraftBackend/`（175 MB） |
| `frontend/dist-demo/`（29 MB） | 构建产物副本，非 `frontendDist` 指向的目录 |
| `backend/test_images/`（0.9 MB） | DSpark 论文测试图，一次性素材 |
| `backend/data/` | 2026-06~07 的旧运行数据（`transcripts/` 只剩 1 个空壳、31 张旧截图）。真实库在 `~/.limo/` |
| `data/`（根） | 只有一个空的 `screenshots/` |
| `backend_err.log` | 2026-06-28 的旧 uvicorn 日志（端口 8484，早期测试） |
| `test_export.html` | 2026-07-05 的导出测试产物（还引用 CDN，与现在的自包含导出完全不同代） |
| `backend/tests/TEST_REPORT.md` | 0.1.0 时代的测试报告，`.gitignore` 里本就有 `TEST_REPORT.md` |
| `frontend/gen-panel-demo.html` / `image-preview-demo.html` | 2026-06-30 的设计 demo |

**保留**：`backend/python/`（715 MB 嵌入式 Python，2026-07-09，全仓零引用）——
体积大且删除不可逆，留待用户确认；`_interview/` / `_experiments/` / `_archive/` /
根 `_*.md`（评审与实验材料，见 `.gitignore` 注释）；`design/`（`cards.md` 是文档索引
里的一等公民）；`测试用例/` / `调研/`（用户资料）。

### ③ 文档同步

`STATUS.md` 更新日期与版本；`README.md` / `CLAUDE.md` 经核对与现状一致（规模数字
136 / 90 / 243 / 82 / 97 / 20 逐项实测对得上）。

**影响范围**：运行时行为零改动。①只影响打包产物能否启动新功能，②只删磁盘残留。
版本 `0.5.14 → 0.5.15`（patch）。

---

## 2026-10-27 — 图表：flowchart 示例改成「分支 + 汇合」（`PROMPT_VERSION` 20 → 21）

用户报「环形图偶尔还是会失败」，贴出的实际产物是：

```
📊 图表生成失败（布局失败：流程存在环（c、d、e）——有回环/自环的结构请改用 state 布局）
```

### ① 根因不是布局算子，是 prompt 的示例形状

先排除了算子：对 `state` 布局跑确定性压力测试（链 + 多条回边、节点数 4/6/8/9、回边数最多 8 条，
共 4320 + 24 例）**零失败**，带标签的边标签 100% 保留（`_relax_labels` 一条都没丢）。
仅有的 2 例失败是「自然高 1024px，需缩到 62%」—— 那是 `_MIN_SCALE=0.65` 的**设计认可**
的显式失败（可读性下限），不是缺陷。

真正的问题是：**模型把带环的流程写成了 `flowchart`**。而 `_flowchart` 结构性地拒绝任何环
（`_topo_layers` 排不出拓扑序就抛 `LayoutError`），整张图就降级成占位文本。

为什么模型会这么写？看 `get_chart_format()` 里那两处 flowchart —— 描述写的是「带分支与汇合」，
但**给的两份示例都是 `a→b→c` 单链**（清单行一份、末尾「示例（照改文字）」一份），既无分支
也无汇合。模型从示例里学到的形状就是「一条链」，于是遇到「有分支 + 有回退」的流程时继续套
flowchart。⚠️ 而 `PROMPT_VERSION` "20"（2026-10-26）已经加过一句「单向前进，不能有回边/自环」
的括注 —— **没治住**。这轮的结论是：**示例的形状比限制语的措辞管用**。

### ② 改了什么（三处文本，零代码逻辑）

- 两份 flowchart 示例都换成 5 节点「提交 → 通过? →（发布 / 退回）→ 上线」：真的分支
  （`b` 出两条边）、真的汇合（`c`/`d` 都进 `e`）、无环。已用 `chart_layout.expand` 实测
  `width=176 height=372 scale=1.0`，5 个节点全部落地、2 个边标签都在。
- 抽出一行**边界句**：「flowchart 只许单向前进，任何一条边指回它自己的上游（a→b→…→a）
  就是环，**整张图会被丢弃**；有回边一律改用 state」。
- 指路语里的「首尾相接的因果循环 → `cycle` 块」补上「**没有分支的**」——`cycle` 块只有
  3–6 个有序项，**装不下带分支的环**；那一类归 `state` 布局，不归块。这同时回答了
  「不是已经用卡片代替了一部分图吗」：替代的是无分支纯闭环，带分支的有向图本来就得画。

### ③ 守卫（`test_prompt.py`）

新断言**解析示例 JSON 看结构**，而不是比对字符串：每份 flowchart 都必须存在一个节点出 ≥2
条边（分支）、一个节点入 ≥2 条边（汇合）。改文案不会误红，退回单链立刻红 —— 已用旧单链
示例做对照实验确认判据有区分力（`outs`/`ins` 全是 1，两条判据都 False）。
另加「单向前进 / 回边 / state」三词必须同场，防止只留「不许」而没给出路。

### ④ 顺带：`test_url_security.py` 改为零网络依赖

门禁曾在**收集阶段** `Interrupted`：`pytest` 导入该模块时，第一组「公网 URL 应通过」对
`api.openai.com` 做真实 DNS，本机把它污染到 `2001::6ca0:aa2c`（Teredo，`ipaddress` 判
`is_private`）→ 被 `validate_url` 正确拦下 → 断言失败 → 整个 `pytest tests/` 收集中断。
一个与仓库代码无关的网络状况把门禁打成红，且报错位置指向不了真因。

改为 `unittest.mock.patch` 掉 `socket.getaddrinfo`（公网组返回 `93.184.216.34`，内网组返回
`127.0.0.1`）。**判据一字不变**（解析到公网 → 通过，解析到环回 → 阻止），换台机器结果相同；
`is_private_ip` 本身不 mock（那是本文件下半段要直接验证的对象）。

**影响范围**：只改 prompt 文本与测试；`chart_layout` / `chart_svg` / 渲染链路零改动；
历史产物不受影响（它们已渲染成 PNG 或已带失败占位）。`PROMPT_VERSION` 20 → 21 是纪律要求
（发给模型的文本变了就 bump），不代表生成行为有确定性变化。

---

## 2026-10-27 — 编辑入口搬进顶部窄条（`VideoBanner` → `ContentBanner`）

产物预览区的「编辑」按钮原先**单占正文上方一整行**（右对齐、只为放两个小元素），
而它上面那条窄条里正好有「原链接」—— 两者都是「针对这个文档 / 这一版」的东西，
分成两行只是把正文白白往下推。现在合成一行：`标题 …… 原链接  ｜  已手动编辑  编辑`。

### ① 组件改名与泛化

`preview/VideoBanner.tsx` → `preview/ContentBanner.tsx`，新增 `actions` 槽
（右侧整块 `ml-auto` 包住「原链接 + actions」）。它的形状没变，变的只是**谁在用**。

⚠️ 改名不是洁癖：编辑按钮在**非视频**文档上同样需要落点，而窄条原先被
`currentDoc?.sourceType === 'video'` 收窄成「视频专属」。名字继续叫 `VideoBanner`
会把这条新语义埋进一个说谎的标识符里。

### ② 显示判据：`video` 收窄 → `isVideo || canEditArtifact`

```ts
showContentBanner = !!activeTabDef?.showBanner && (isVideo || canEditArtifact)
```

- 视频：照旧（`showBanner` 已有，与改造前逐项相同）
- 非视频：**只在有编辑入口时**才出现。若改成「去掉 video 限制让所有 tab 都显示」，
  图片 / 转录 / 原始数据这些 tab 会平白多一条标题栏 —— 那是另一件事。

`canEditArtifact` 三条判据缺一条就是「按钮在、点了没用」：只有 `summary` / `polished`
两个 tab 有可编辑产物；Excel 数据摘要走 `summary` 内容源但**不可编辑**（`typeMeta.isExcel`
早退，与下方正文分支同源）；编辑态下不显示（正文区整个被编辑器接管）。

⚠️ 窄条 hover 色从 `hover:bg-surface-hover` 改成 `hover:bg-elevated` —— 容器的底色
**本身就是** `bg-surface-hover`，同色 hover 等于没有反馈。

### ③ 顺带收口一处重复判据

正文分支里原有的局部 `editMode` / `inEditing` 与新加的组件级 `artifactEditMode` /
`editingThisMode` 是同一份判断的两份写法。全部改用组件级变量（窄条与正文两处同源）——
各写一份的失效形态是「窄条说没在编辑、正文却已是编辑器」这类静默分叉。

**影响范围**：仅预览区 UI。导出、生成、版本、编辑落盘逻辑零改动（`startEditing` /
`handleSaveEdit` 原样复用）。`TabDef.showBanner` 语义从「是否显示 VideoBanner」改为
「是否**允许**显示顶部窄条」（必要条件，充分判据在 `PreviewArea`）。

---

## 2026-10-26（同日续十一） — 释放策略收口：关掉功能也释放、档位改由 capabilities 下发

续十把「建完索引之后放不放」做成了三档设置，但收尾时盘出四条欠账，本轮一并补上。

### ① 关掉语义检索后，模型**不会被释放**（真缺陷）

`release` 三档此前只在 `VectorWorker.maybe_release` 里生效，而那条路径只有 worker
**在跑**时才走得到。用户一旦关掉开关，worker 被 `_stop_worker` 取消，模型句柄却留在
`LocalEmbeddingEngine` 里 —— 那几十 MB 一直不还，而界面上所有痕迹都消失了。

⚠️ **不能靠 `never` 档挡住它**：`never` 的意思是「**平时**常驻内存」，不是「关掉这个
功能之后还占着」。用户选常驻是因为还要用；关掉之后再占着，没有任何「下次搜索」可以等。
所以新增 `VectorWorker.release_now()`：**不看策略**，无条件 `after_idle=0.0` 释放。

`_stop_worker` 加 `release_model` 参数（**默认 False**），调用方的语义各不同：

| 路径 | 释放？ | 理由 |
|---|---|---|
| `sync_vector_runtime` 的**六条**「不在用」（关开关 / 库未初始化 / 引擎找不到 / 库不可用 / 自检不过 / 口径不符） | ✅ | 共同含义是「语义检索现在不工作」 |
| `_ensure_worker` 的策略变化重启 | ❌ | 用户可能刚把 `immediate` 改成 `never`（明确要它常驻），这次重启正是为落实那个选择 |
| `stop_vector_runtime`（停机 / 重建向量索引） | ❌ | 停机放不放没区别；而「重建」马上就是一轮全库编码，放掉等于白付一次加载 |

⚠️ `release_now()` 传 `after_idle=0.0` 而不是不传：不传走引擎默认门槛（600s），刚
放下键盘就关功能时引擎会以「还在闲置期内」为由拒绝 —— 一次什么都不做的释放。

### ② `_stop_worker` 只清了 `vector_stop` / `vector_task`，留下 `vector_worker`

指向已死 task 的 `vector_worker` 是一颗定时炸弹：将来任何按它读 `worker.release` 或
`worker.stats` 的代码都会拿到上一个实例的陈旧值，**且不会有任何报错**。现在三个引用
一起清，且**先清引用再 cancel**（`_start_worker` 的「已在跑」判据读的正是这个引用）。

顺带：原先的 `if task is None or task.done(): return` 会把释放整个跳过 —— 改成
`if task is not None and not task.done():` 包住 cancel/await，释放排在块外。

⚠️ **释放必须在 `await task` 之后**：worker 活着时可能正用着这份 ONNX 句柄，先放再
cancel 会让那次编码在半路失去模型（在 ONNX 上表现为崩溃而非异常）。

### ③ 三档的文案在前端硬编码了第二份 → 改由 capabilities 下发

`SearchIndexCard` 里手写了一组 `RELEASE_OPTIONS`（key + label + hint）。后端
`maybe_release` 是按 key 分派的 —— **加一档要改后端**，而前端那份不改，设置页就永远
看不到它（用户选不到等于没加），且编译期与运行期都不报错。

现在 `GET /api/capabilities` 下发 `releasePolicies`（`_RELEASE_POLICY_META` 是文案唯一
出处，模块级 `assert` 守键集与 `RELEASE_POLICIES` 相等），前端照单渲染、**顺序也用后端
的**。同一条纪律下 `styleLabels` / `dimensionPresets` / `readerIntents` 早已是下发的。

⚠️ 前端 `ReleasePolicy` 联合类型留在 `api/core.ts`（它是编译期的东西，与运行期枚举天然
无法合并）；`Capabilities` 里该字段是**可选**的，消费处 `?? []` —— **取不到就整组不渲染**，
不拿本地默认表兜底（那恰好会把这条纪律悄悄废掉，而测试照样全绿）。同 `lib/search-health`
的 `null`：问不到不等于坏了。

### ④ 补测试（此前 `vector_runtime` **零覆盖**）

- 新增 `test_vector_runtime.py`（11 条）：六条停用路径都释放 / 重建与重启路径不释放 /
  `never` 档关掉功能也释放 / 策略变了才重启 / 三个引用都清干净 / 库不可用·引擎找不到·
  口径不符各有出口。
- 新增 `searchIndexCard.test.tsx`（6 条）：档位顺序与文案照后端下发 / capabilities 缺失时
  整组不渲染 / 云端引擎不渲染 / `release` 缺失按 `idle` 高亮 / 切档时**不传模型名**
  （传了会走「切引擎」分支，口径必然不符、索引被停掉）/ 点已选中那档不发请求。
- `test_capabilities.py` 加一条：键与顺序必须来自 `vector_worker.RELEASE_POLICIES`，
  且 `DEFAULT_RELEASE` 在清单里（前端拿它做兜底高亮，漏了会渲染出「一个都没选中」）。

**四组对照实验**（都实测判红后还原）：去掉「取不到就不渲染」的守卫 → 只有那条红；
切档时误传 `vec.model` → 只有那条红；`_release_policies` 少一档 → 只有那条红；
`_stop_worker` 的释放判据改成 `if False` → 5 红 6 绿。

### 影响范围

- 后端：`services/vector_runtime.py`（`release_model` 参数 + 三个引用）、
  `services/vector_worker.py`（`release_now` / `_do_release`）、
  `routers/capabilities.py`（`releasePolicies`）
- 前端：`api/core.ts`（`ReleasePolicy` / `ReleasePolicyOption`）、`api/search.ts`（转出类型）、
  `components/settings/SearchIndexCard.tsx`（删本地常量、改读 capabilities）
- 文档：`docs/STATUS.md` 计数（后端 91 / 前端 83；`vector_runtime` 与 `releasePolicies` 补进清单）
- 用户可见：无新增控件 —— 只是「关掉语义检索后那几十 MB 真的还回去」

## 2026-10-26（同日续十） — 建完索引后释放模型内存（三档策略，用户可选）

承接上一条的 arena 修复（686MB → 92MB）：建完库之后那 92MB 其实也**可以还回去**。
用户提了两件事——「建完库之后其实可以释放掉」，以及「用户开启/关闭相关设置这方面
可能得有详细的设置」——合起来就是：**释放不硬编码，做成带代价说明的三档设置**。

### ① 三档策略（`search.vector_release`）

| 档 | 行为 | 代价 |
|---|---|---|
| `never` | 模型一直留着 | 那几十 MB 常驻不还 |
| `idle`（**默认**） | 闲置超过阈值就释放 | 日常约等于「建完就放」，但刚建完顺手搜一下仍然快 |
| `immediate` | 建完立刻释放 | 之后第一次搜索等几秒重新载入 |

三档的实质是「**哪一刻要等模型载入**」，所以界面文案讲的是等待，不是「省不省内存」——
只写省不省等于把选择压成一个没有信息的偏好。

⚠️ **默认 `idle` 而非 `never`**：默认值要等于「用户什么都不做时的合理行为」，而 worker
跑完不退出进程、模型一旦加载就一直占着，正是上一轮 arena 修复要治的那个病的残余。
⚠️ 阈值（600s）住在**引擎侧**（`embedding_local.DEFAULT_IDLE_RELEASE`），worker 刻意不知道
——「多久算闲置」是本地引擎的属性，云端引擎根本没有「加载」这回事。`idle` 档调
`release()` 时**不传 `after_idle`**，传了就是第二份判据。

### ② 释放之后查询必然超时 —— `LOAD_TIMEOUT` 是一档独立的超时

`QUERY_TIMEOUT = 3.0`，而模型加载 3–5 秒。释放后第一次查询**必超时**，而错误文案还是
「语义检索超时」——读起来像网络问题，用户会去查网。

`VectorSearcher._ensure_loaded()` 在 `_precheck` 之后、编码之前调 `warmup()`，用**独立的**
`LOAD_TIMEOUT = 30.0` 包住。载入超时 / 载入失败各自成一句文案（「语义模型载入超时（>30s）」
/「语义模型载入失败（RuntimeError）」），与真正的查询超时分开。失败只降级这一次查询
（字面结果照给 + `semantic.error`），不写 `mark_failed` —— 模型没起来不是这篇文档的错。

⚠️ **不是放宽 `QUERY_TIMEOUT`**：那会让「一次查询真的卡住」也要干等 30 秒。

### ③ `immediate` 只在**本轮真有推进**之后释放一次

`run_once` 空转（队列空 / 全部跳过）时不该释放：用户可能刚刚搜过一次把模型载进了内存，
一轮空转把它踢掉 = 「明明刚用过，下一次搜索又要等几秒」。`loop` 在
`if stats["indexed"] or stats["failed"]` 之后才置 `_pending_release`，`maybe_release()`
消费一次即复位。

释放后 `_warmed = False` —— 下一轮走 `_warm()` 而不是每篇惰性加载。

### ④ 顺带：`_warm` 从 `run_once` 开头挪到「确认有活之后」

原来无条件预热，队列空着时每 60 秒加载一次模型（22.8MB 读盘）再释放一次。现在排在
`_next_ids` 之后。这条与释放策略是同一个病的两面：**内存花在没人用的地方**。

### ⑤ 入口严、读回宽

- `POST /api/search/vector` 的非法 `release` → **400 `VECTOR_BAD_RELEASE`，零写盘**
  （错的是性质：用户以为改成功了，而内存回收行为一点没变）。
- `GET /api/search/status` 读配置时对脏值**静默回退 `idle`**（那是「磁盘上可能过期的值」，
  为一个读不懂的键让整张卡报错，用户无从下手）。
- `vector_runtime._ensure_worker` 在**策略变化时重启 worker** —— 原先的「已在跑不重启」
  会让配置改了行为不变，是最典型的静默失效。

### 影响范围

- 后端：`engines/embedding_local.py`（`release()` 钩子）、`services/vector_worker.py`
  （三档 + `maybe_release`）、`services/vector_search.py`（`LOAD_TIMEOUT`）、
  `services/vector_runtime.py`（策略变化重启）、`routers/search.py`（校验 + 状态回显）、
  `core/config.py`（默认值）
- 前端：`api/search.ts`（`ReleasePolicy` / `setVectorSearch(envelope, model?, release?)`）、
  `SearchIndexCard`（三档按钮 + 代价说明，**仅本地引擎下渲染**）
- 测试：`test_embedding_local.py`(4) / `test_vector_worker.py`(6) / `test_vector_search.py`(4) /
  `test_search_api.py`(3)，含「默认配置的字面量与常量同源」一条守卫
- 用户可见：README「语义检索」条补一段；设置页多一行「模型内存」

## 2026-10-26（同日续九） — 本地引擎的内存与使用条件

用户问「这 22 兆大概需要多少运存，可不可以进（发进安装包）」，两问都答了「不行 / 不该」，
连带修了一个**量级差 7 倍**的缺陷，并把「本地引擎不是装上就能用」这件事写到了用户眼前。

### ① ONNX Runtime 的 CPU arena：686MB 常驻 → 92MB

**22.8MB 是磁盘上的权重，不是内存。** 实测（Win11 / 本机 / 工作集，3.5 万块外推全库）：

| bs | `enable_cpu_mem_arena` | 加载后 | 建库跑完常驻 | 全库耗时 |
|---|---|---|---|---|
| 32 | on（**原状**） | 87.8MB | **686.4MB** | 3.8 分钟 |
| 16 | on | 87.6MB | 388.1MB | 4.6 分钟 |
| 8 | on | 87.9MB | 239.6MB | 3.4 分钟 |
| 4 | on | 88.2MB | 165.0MB | 3.3 分钟 |
| 32 | **off** | 87.2MB | **92.7MB** | 4.6 分钟 |
| 8 | **off** | 87.1MB | **92.3MB** | 4.7 分钟 |

根因是 ORT 默认把每次推理申请的缓冲留在自己的 arena 里复用、**永不归还系统**，而
embedding 的中间激活比权重本身大一个量级（bs=32 × 202 token 的注意力矩阵就是几百 MB）。
`Embedder.__init__` 现在设 `SessionOptions().enable_cpu_mem_arena = False`。

取舍：**多花 20% 的一次性建库时间，换掉 600MB 常驻** —— 建库是一次性的，内存是终身的
（worker 跑完不退出进程）。副作用是 `bs` 不必再为内存调小（关掉 arena 后 bs=4 与 32 的驻留差不到 1MB）。

### ② `precheck` 把「使用条件」说全，且**打包版不给做不到的命令**

原文案只报「缺少 onnxruntime」并给 `pip install`。两个问题：

- **只报缺哪个包，读起来像「这次漏打了、下次更新会带上」** —— 而本地引擎是**刻意不随包**的
  （模型 22.8MB + ORT 的 CPU 原生库都不进安装包），它对谁都不是「装上就能用」。
- **冻结环境没有 pip**：`LiMoBackend.exe` 跑在冻结的 Python 里，用户机器上通常也没有第二个
  Python。给一条跑不了的命令比不给更糟 —— 他会以为是操作错了、反复试。

现在：前半句「不随应用分发，需自行准备：运行库 X 与 Y、模型文件」，后半句由
[`_fix_hint`](../backend/app/engines/embedding_local.py) 给补法 —— 源码模式给镜像源命令 +
模型文件落点 + 「补齐后重启应用生效」（`find_spec` 查的是**当前进程**的 `sys.path`，不重启看不见新包）；
`sys.frozen` 时**直说做不到**并给出路（改用云端引擎）。

⚠️ `_fix_hint` 是**唯一一份**补法文案：`precheck`（设置页那行）与 `Embedder.__init__` 抛的
`MissingDependency` 共用。两处各写一份的失效形态是「设置页说这里装不了、真跑起来又让你 pip install」。

### ③ 前端：未就绪原因**不用点下拉就看得见** + 重建确认框不再说「计费」

- **`SearchIndexCard`**：新增 `notReady` 派生 = `vec.reason ?? (选中引擎未就绪 ? 它的 reason : null)`。
  此前未就绪原因只在「开关开着却起不来」时通过 `vec.reason` 出现 —— 用户没开语义检索时
  **只能靠去点一下下拉才发现**自己选的引擎用不了（`sync_vector_runtime` 在 `vector_enabled=false`
  时 reason 恒为 `None`）。那块 `data-vector-requirement` 现在常驻。
  两处同时渲染是同一句话连说两遍，所以判据是「`vec.reason` 缺席才回落」。
- **向量重建确认框**：原写「这会产生一次完整的计费」——**本地引擎下一分钱不花**，是错话。
  现在与 `data-vector-notice` 同源，按引擎的 `local` 分支（「全程在本机跑，不产生费用，但要花上一段时间」）。

### ④ 决策记录：为什么不进包、为什么不做成插件

| 方案 | 没 Python 的用户 | 装了 Python 的用户 | 核心纯净度 |
|---|---|---|---|
| **现状（核心引擎、自己备环境）** ← 用户拍板 | ❌ | ✅ | 引擎代码在核心，重依赖不在 |
| 做成常驻插件 | ❌ **同样装不了** | ✅ | ✅ |
| 内置进安装包 | ✅ | ✅ | ❌（+80MB、原生 DLL 风险） |

- **进包**：技术可行（模型 23MB + ORT 解包 50–60MB + tokenizers 5MB，许可证 MIT/MIT/Apache-2.0
  无冲突），但 ORT 是原生 DLL，与已踩过的 OpenSSL DLL 冲突同类，且与口子原则正面冲突。
- **插件**：`plugin_loader._resolve_venv_base_python()` 在 frozen 下**只能找系统 Python**，
  找不到就 `no-python` 直接返回 —— 插件路线的覆盖范围**一点没扩大**。加上现有插件是
  一次性 `Popen`，而 embedding 要常驻（加载 3–5s > `QUERY_TIMEOUT = 3.0`）。

结论：**现状 + 把使用条件说清楚**。

### 影响范围

`engines/embedding_local.py`（arena / precheck / `_fix_hint`）、`settings/SearchIndexCard.tsx`、
`README.md` 语义检索那条（「该跑什么命令」在安装版下不成立，已改为如实描述两档）、
`tests/test_embedding_local.py` +1（`test_precheck_打包版不给跑不了的命令`，反向断言
`"pip install" not in reason`）。**行为变更**：本地引擎在安装版下从「报缺依赖 + 一条跑不了的命令」
变成「直说安装版补不了、请用云端」。

---

## 2026-10-26（同日续八） — 语义检索阶段 4：本地向量引擎

用户原话「用本地的插件吧这样方便一些」。落地结果是**本地引擎**，但**没有做成
`~/.limo/plugins/` 的 `EnginePlugin`** —— 判据是口子原则的**实质**（重依赖不进核心打包），
不是「文件放在哪个目录」。三条硬理由写在模块文档里：

1. **签名形状不对**：`EnginePlugin.execute(file_path, **kwargs)` 是「一个文件 → 一段文本」，
   embedding 是「一组文本 → 一组向量」，连「文件」这个概念都没有；
2. **查询侧扛不住进程启动**：进程外每次 `Popen` + 模型加载 3–5 秒，而
   `VectorSearcher.QUERY_TIMEOUT = 3.0` —— 第一次搜索必然超时；
3. **口子原则已经满足**：`onnxruntime` / `tokenizers` **不在 `pyproject.toml`**，
   模型 22.8MB 在 `~/.limo/models/`、**不随包分发**。

### 动了什么

| 条目 | 落点 | 变化 |
|---|---|---|
| 新引擎 | [`engines/embedding_local.py`](../backend/app/engines/embedding_local.py) | `Embedder`（ONNX 句柄：CLS pooling + 逐行归一化 + `max_length=512`）+ `LocalEmbeddingEngine`（`name=local-bge-small` / `model_id=bge-small-zh-v1.5-int8` / 512 维 / `is_local=True` / `display_name`） |
| 两个可选钩子 | 同一文件 + `services/vector_runtime.py` + `services/vector_worker.py` | `precheck() -> str \| None`（就绪自检）与 `warmup()`（加载模型）；**用 `getattr` 消费，不进 `EmbeddingEngine` Protocol** |
| 注册 | `engines/__init__.py` | 与云端**各自一个 `try`** —— 共用的话 dashscope 分支抛错会把本地一起吞掉，而「云端不可用」与「本地不可用」是两件无关的事 |
| 预热 | `services/vector_worker.py::run_once` | 每轮开跑前 `_warm()` 一次；失败写 `stats["stopped"]` 且**不** `mark_failed` |
| 前置闸门 | `services/vector_runtime.py::sync_vector_runtime` | `_engine_precheck` 排在 `ensure_identity` **之前** |
| 可切换 | `routers/search.py` | `POST /api/search/vector` 收 `model`；非法值 → 400 `VECTOR_NO_ENGINE` 且**零写盘**；`status.vector` 加 `model`（读**配置**不读 runtime）与 `models`（`label` / `modelId` / `dimension` / `local` / `ready` / `reason`） |
| 前端 | `api/search.ts` + `components/settings/SearchIndexCard.tsx` | 引擎下拉 + 成本文案按 `local` 二分 |
| 评测脚本 | `scripts/embed_local.py` | 算子早已在 `core/vector_index.py`，`Embedder` 改为**从产品引擎转出** —— 评测与产品共用同一份权重与同一份实现 |

配套用例：**新建 `test_embedding_local.py`（12 条）**、`test_vector_worker.py` +3、
`test_embedding_engine.py` +1、`test_search_api.py` +4。

### 三个判据，每个都对应一种「不报错、只是不成立」

**① `precheck` 必须排在 `ensure_identity` 之前。** 反过来的失效形态很隐蔽：引擎起不来
（没装 onnxruntime）时先把四元组身份**写成了新口径**，于是用户装完依赖回来，看到的是
「口径不符，需要重建」—— 而其实一个向量都还没编码过。顺序换了之后，引擎不可用时
`ensure_identity` 一次都不跑，装上依赖即恢复。

**② 预热失败不能 `mark_failed`。** `fail_count` 是「花钱的闸门」（连续失败 3 次停该篇），
而模型没加载起来**不是这篇文档的错** —— 标上去等于用掉 3 次重试额度里的一格，
且用户修好依赖后那批文档已经躺在 `failed` 里不会再自动重试。

**③ 不预热不是「慢」，是「第一次查询必定退回纯字面」。** 加载 3–5 秒 > `QUERY_TIMEOUT = 3.0`，
用户看到的提示是「语义检索超时」—— 读起来像网络问题，而他刚开的本地模型**根本不联网**。
所以预热挂在 worker 开跑前：开启向量检索必然产生一批 `pending`，模型一定在用户第一次
查询之前就绪。

### 两处刻意的不对称

- **云端引擎没有 `precheck`** = 视为**就绪**（不是「未知」）：云端确实没有「依赖装没装」
  这回事，报不可用会让用户去找一个不存在的开关。Key 的问题在第一次编码时才该显式报。
- **切引擎不改 `vector_enabled`、也不自动重建。** 两个是独立旋钮；切过去会因维度不符
  （1024 ↔ 512）被显式停用并给出「需要重建」的原因 —— 重建是花钱的动作，只能由用户点。

### 已知边界

- 本地引擎**首次**跑真实库需要用户自己装依赖（`pip install onnxruntime tokenizers`）
  并放好模型文件；两条缺失各有自己的文案（装什么 / 缺哪个文件），**不合并成一句**。
- 本轮**没做**真实库端到端实测（同前几轮的取舍：验证的是契约与编排，不是 ONNX 数值）。

---

## 2026-10-26（同日续七） — 第四轮代码审查的整改：7 条缺陷

审查对象是刚落地的向量检索（阶段 1–3），报告见 [code-audit-4.md](code-audit-4.md)。
七条里两条是用户真的会撞上的（删掉的文档在语义检索里继续命中；打开设置页**看一眼**
就可能把全文检索永久判坏），其余五条都是「不报错、只是某件事悄悄不成立」那类。
逐条的对照实验结果在报告文末，这里只记落点与理由。

### 动了什么

| 条目 | 落点 | 变化 |
|---|---|---|
| P1-01 | `routers/ingest.py` | 新增薄委托 `_drop_vectors(request, tx_id)`，单删与批量删两条路径各调一次 —— 与 `_index_for_search` **成对** |
| P1-02 | `core/search.py::status()` | 读状态失败改记局部 `status_error`（返回 dict 新增 `statusError` 键），`_last_error` 一个字节都不碰 |
| P2-01 | `engines/embedding_dashscope.py` | 删掉 `key_prefix=api_key[:7]` 的日志参数 |
| P2-02 | 同上 | 条数校验之后追加 `indexes != list(range(len(batch)))`，与「条数不符」分开报错 |
| P2-03 | `routers/search.py` | `await asyncio.to_thread(engine.rebuild, transcripts, _get_content, vacuum=True)` |
| P2-04 | `routers/search.py` + `services/vector_search.py` | docstring 改准：`semantic.hits` 是「按 `depth` 取、**参与融合**的篇数」，不是全库命中总数 |
| P2-06 | `routers/search.py::_vector_status` | 去掉两处永不触发（`if request else None`） |

配套用例：`test_delete_index.py` +3、`test_search.py` +1、`test_embedding_engine.py` +2，
**新建 `test_search_api.py`**（3 条）。

### 两条 P1 是同一形状：写入侧有人管，另一侧没有

- **P1-01**：写入侧打 `pending` 有三处（摄入 / 生成 / 编辑），删除侧**一处都没有** ——
  孤块留在 `vec.db` 里继续参与融合、挤占 `depth` 名额，要等到下次启动 `reconcile()`
  才清。而 `README.md` 与设置页都写着「删文档时对应向量一并清掉」—— **修复前这句话是假的**，
  且它读起来毫无异常（同 CLAUDE.md 里记的那类「文档骗人」）。桌面应用能开着好几天，
  这个窗口不是理论值。
- **P1-02**：`available` 的定义就是 `_last_error is None`，而 `status()` 又把一次瞬时
  读失败（磁盘忙、库被占）写进同一个 `_last_error` —— 于是「用户点进设置页看一眼」
  这个纯观察动作，能换来一个**只有手动重建才解除**的不可用状态，且界面上不会说
  发生了什么。

两条都不是「逻辑写错了」，是**一个副作用挂在了不该有的地方**。

### P2-03 值得单独说：改动一行，判据原本为零

`/api/search/reindex` 与 `/api/search/status` 此前**没有任何 HTTP 层测试**——
`test_search.py` 测的是 `SearchEngine` 类本身（模式 #51：能力存在 ≠ 该路径在用它）。
所以「把同步调用包进 `to_thread`」这种改动，传参对不对、端点还通不通，全靠读代码。
新建的 `test_search_api.py` 只做三件事：重建真的灌满（篇数 + 可搜性**两条都断**）、
陌生 Origin 被 403、向量未初始化时状态段给出明确原因。

### 影响范围

- 删除文档后语义检索不再命中它；`vector_store.delete_doc` 失败时**只记一条 warning**，
  不影响删除本身（文档已经删了、回滚不了，抛出去只是把「向量没清干净」变成用户看不懂的 500；
  残留由下次启动对账兜底）。
- `SearchEngine.status()` 变成**纯只读**：读失败只体现在 `statusError`。
  前端类型已加该字段，**刻意不展示** —— 用户对它没有可采取的行动（重试即可），
  展示只会让人以为索引坏了。
- `semantic.hits` 的**值一个都没变**，只是措辞改准（大库恒 ≤ `depth`）。
- 后端全量 `pytest`：**1065 passed**。

---

## 2026-10-26（同日续六） — 向量检索阶段 3：查询融合 + 开关 + 状态可见

worker 让索引长出来了，但没有任何查询路径读它 —— 花了钱、`vec.db` 里躺着几百个块，
搜索结果一个字都没变。这一步把那条线接上：**字面 ∪ 语义 → RRF 重排**，外加一个开关
和一张能看见「索引建到哪了」的状态卡。

### 动了什么

| 文件 | 变化 |
|---|---|
| `backend/app/services/vector_search.py`（新，~200 行） | `VectorSearcher`：`fuse()` / `_precheck()` / `_embed_query()`。**算子一个都不在这里**（切块、分组排名、RRF 全在 `core/vector_index.py`，与评测同一份） |
| `backend/app/services/vector_runtime.py`（新，~110 行） | `sync_vector_runtime` / `stop_vector_runtime`：开关的**唯一**维护点，worker 与融合层**成对**启停 |
| `backend/app/routers/search.py` | `/search` 响应改 `{hits, semantic}`；新增 `POST /search/vector`（开关）与 `POST /search/vector/rebuild`；`/search/status` 补 `vector` 段 |
| `backend/app/core/dependencies.py` | 新增 `get_vector_searcher`（直接读 `app.state.vector_search`，**不按配置现造**） |
| `backend/tests/test_vector_search.py`（新，20 条） | 融合排序 5 / 退化 8（每档都断言 `engine.calls == 0` 或 `== 1`）/ 四元组只读 2 / HTTP 端点 5 |
| `frontend/src/api/search.ts` | `SearchResponse` / `VectorStatus` / `VectorActionResult` 等类型；`searchDocs` 拿到不认识的结构**显式抛错** |
| `frontend/src/components/doc-library/{useDocQuery,DocList,DocLibrary}.tsx` | `semanticNote`（语义退化提示，与 `searchError` **分家**） |
| `frontend/src/components/settings/SearchIndexCard.tsx` | 向量段：开关 / 成本与隐私说明 / 状态一行 / 重建二次确认；三个状态与字面侧**各管各的** |
| `frontend/src/demo/mock.ts` | `/search` 形状跟进 + 补 `/search/status`（含 `vector` 段）与三个只读动作的明确答复 |

### 三条设计在落地时才看清

1. **两路必须同深度**（`depth = max(limit, DEFAULT_DEPTH=50)`）。RRF 的 `1/(k+rank)` 只有在
   两路基线一致时才可比 —— 字面取 20 条、向量取 50 条，会让「字面第 20 名」与「向量第 20 名」
   拿到不同的分数，排序取决于谁取了更深的那个巧合。看着也能排出一个顺序，只是那个顺序
   没有意义。
2. **前置校验必须拦在编码之前**（`_precheck` 三档：库不可用 / 没建过 / 口径不符）。
   对着一个明知不可用的索引去花一次 API 调用是纯浪费；而「先编码再发现不能用」的形态
   在日志里看着完全正常。测试逐档断言 `engine.calls == 0`。
3. **`load_matrix` 走 `asyncio.to_thread`**：同步读盘几十到几百毫秒，放在事件循环里会让
   同一请求周期内的其它协程（worker 的 `await`、SSE 心跳）一起停住 —— 表现为「搜一次，
   后台索引停一下」，两边都看不出因果。

### 顺带修的真缺陷：`isIndexUnavailable` 的判据恒假

`useDocQuery` 原来这样判「索引不可用」：

```ts
return raw.includes('503')      // raw = e.message
```

而 `apiFetch` 抛的 `ApiErrorException.message` 取的是后端 `message` 字段（「全文索引不可用 ——
可在 设置 → 通用 → 检索索引 里重建」），**从来不含 503**。于是分支永不进入 ⇒
`refreshSearchHealth(true)` 从不触发 ⇒ 顶部那条「索引坏了，去重建」的横幅**永远浮不出来**。
上一轮的横幅测试只断言「横幅能渲染」，测不到这条接线。

判据改为读 `apiError.status === 503 || apiError.code === 'QUERY_INDEX_UNAVAILABLE'`。
**对照实验**：把判据临时改回 `message.includes('503')`，新增的那条守卫单测变红
（`1 failed / 27 passed`），还原即复绿 —— 说明它专门守这一个性质，不是顺带绿的。

### 前端响应形状变更，两条既有测试随之适配

`searchDocs` 从返回裸数组变成 `{hits, semantic}`，三个文档库测试的 `vi.mock('../api/search')`
直接把桩的返回值透传出去，于是 `buildResultSet` 把对象当数组取 `.hits` 而崩（4 条红）。
**改法是把包装层写对、而不是逐条改桩**：桩仍返回 `hits` 数组（用例只关心命中与排名），
包装层补 `semantic`（默认 `null`）—— 5 处 `mockResolvedValue` 一处未动，且桩的语义
「我给的是命中列表」保持不变。

### 影响范围

**未开启语义检索的用户零行为变更**：`searcher is None` 时 `/search` 直接返回
`{hits: lexical[:limit], semantic: null}`，与改造前逐条相同（唯一区别是响应外壳从数组
变成对象 —— 前端、`test_query_contract.py`、`test_delete_index.py` 三处已同步）。

开启后：搜索会多出「意思相近但一个词都没出现」的文档，排序由 RRF 决定。语义路任何一环
出问题都**只降级这一次查询**，字面结果照给，并在列表底部给一行 muted 提示。

⚠️ **已知边界**：CLI 不接融合 —— `QueryService.search_resources` 保持同步纯字面，
融合发生在 HTTP 层的异步编排里。开关打开时 `limo search` 仍是纯字面（已写进 `AGENTS.md`）。

⚠️ **待做（2026-10-26 用户决定本轮先不做）**：**产品链路**的真实库端到端实测
（131 篇 ≈ 2 元 / 3 分钟，需在设置页手动开启）。**跳过的是产品链路，不是算法正确性** ——
切块 / RRF / 排名算子已在评测侧用**同一份** `core/vector_index.py` 在真实库上跑过 A/B
（§3.2 那批读数），本轮的 20 条单测覆盖融合编排与失败三档。缺的是「打开开关 → worker 建
索引 → 搜索多出语义命中」这条链在真模型上走一遍的观察，以及首次全库建索引的真实耗时。
另两项仍未做：`eval_search.py --vector` 的产品侧同轮对照；§七「陷阱题」那一维。

---

## 2026-10-26（同日续五） — 向量检索阶段 2 第 3 步：后台 worker 让索引自己长出来

写点只会打 `pending`，谁都还没去消费它。这一步补上「谁、按什么顺序、花几次钱」把
`pending` 变成 `ok`。

### 动了什么

| 文件 | 变化 |
|---|---|
| `backend/app/services/vector_worker.py`（新，~250 行） | `VectorWorker`：`run_once` / `_index_one` / `reconcile` / `loop`。**不持队列、不做 debounce**，只负责推进 |
| `backend/app/core/config.py` | `DEFAULT_CONFIG` 新增 `"search": {"vector_enabled": False, "vector_model": "dashscope-embedding"}` |
| `backend/main.py` | enabled 时校验四元组 → 起 `create_task(worker.loop())`；**新增 `shutdown` 事件**（`stop.set()` + `task.cancel()`） |
| `backend/tests/test_vector_worker.py`（新，18 条） | 首次索引 / 零调用跳过 / 只编码变更块 / 插入段落不全量重编 / 块变少删多余 / 空文档 / 连续失败停 / 本轮不重试同一篇 / 跨轮闸门 / 引擎违约 / 坏库不停 / 对账三条 / 循环退出 |

### 一条设计被实测推翻：`content_hash` 快速路径永不命中

§4.4 原设想「整篇 hash 相等就跳过」。推演下来它**一次都不会命中**：
`mark_pending` 会把 `content_hash` 清空，而 worker 只处理 `pending` / `failed` 的文档 ——
worker 看到的这个字段**恒为 None**。

写成代码就是一段永不执行的死分支，而且更坏：后人会以为「hash 一致就能跳过整篇」是一条
生效中的优化，于是在排查「为什么又编码了一遍」时去看一个根本没运行的地方。**故不实现**，
`content_hash` 只写不读（状态展示、将来批量对账用），增量判据只有块级 `text_hash` 一处。

### 一条实现比设计更强：按内容匹配，不按块下标匹配

§4.4 的伪代码是「逐块比 `text_hash`」，字面上读起来像 `旧[i] vs 新[i]`。落到代码时改成
`hash → vec` 的映射：

- 位置匹配在「在**开头**插入一段」时会把后面每一块错开一位 ⇒ **整篇重编码**；
- 内容匹配天然免疫插入 / 删除 / 段落移位，只编码真正变了的文本。

两种实现都不报错，差别只在账单上。**对照实验**：把实现临时退回位置匹配，
`test_insert_paragraph_does_not_reencode_everything` 变红（编码 3 块而非 1 块），
且**只有它**变红 —— 说明这条测试专门守这个性质；还原后 18 条全绿。

### 三处落地细节（设计文档里没有，但不写就会踩）

1. **`attempted` 集合是必需的** —— 失败但未到上限的篇目在下一批 `retry_ids` 里**仍然会出现**。
   不挡掉就会在同一轮里反复重试同一篇：一个看上去在推进、实际在烧钱的 worker。
2. **失败上限要两个机制** —— 逐篇 `fail_count ≥ 3` 永久放弃（跨轮次、落盘）＋ 本轮
   「连续 3 篇失败即停」。前者防单篇毒药文档，后者防把整库均匀烧一遍（Key 过期 / 断网
   时继续跑，只是把失败均匀地摊到每一篇上）。
3. **checkpoint 粒度比「每批一次」更细** —— 每篇 `mark_ok` 落盘。块与状态在同一事务里写完，
   「哪篇完成」本身就是最精确的进度单位，不需要另建批次记录。

`shutdown` 里 `set()` 与 `cancel()` **两个都要**：`set()` 让循环在下一个等待点退出；
`cancel()` 兜住「正卡在一次 60s 的 embedding 往返里」—— 否则关窗口要等它，
uvicorn 会先打印「等待后台任务超时」。

### 影响范围

**对未开启语义检索的用户为零影响**（`vector_enabled` 默认 False，与改造前逐字节相同）。

开了之后：索引会自己长出来，但**在查询融合（阶段 3）接上之前它只有成本、没有收益** ——
没有任何查询路径读 `vec_chunks`。所以这个开关的说明文案必须写清「现在打开只是在花钱」，
而那正是 UI 该说的事（阶段 4 的估算弹窗）。

---

## 2026-10-26（同日续四） — 向量检索阶段 2 第 2 步：`vec.db` 三表 + 写点打 `pending`

算子搬完（续三）之后，存储层才轮到落地。这一步的目标是**让「内容变了」这件事被记下来**，
但仍然**不做任何编码**。

### 动了什么

| 文件 | 变化 |
|---|---|
| `backend/app/core/vector_store.py`（新，~400 行） | `VectorStore`：三表建表 / 四元组校验（`ensure_identity` 三态）/ 文档级状态机 / 块读写。**唯一会碰盘的向量侧模块** |
| `backend/app/core/search.py` | `update_index` 加第 4 个参数 `vec=None`（默认 None = 改造前行为），并拆成两段**各自独立 `try`** |
| `backend/app/routers/{generate,ingest,markdown_edit}.py` | 四个写点各传 `vec=getattr(request.app.state, "vector_store", None)` |
| `backend/main.py` | `app.state.vector_store = VectorStore(data_dir)`，**只构造** |
| `backend/tests/test_vector_store.py`（新，17 条） | 身份三态 / 静默失效三条 / 块读写往返 / 坏库不炸启动 / 写点接线 |

### 三条写死的语义（都是「不这么写就静默错」）

1. **`mark_pending` 把 `content_hash` 置空** —— 留旧值会让 worker 的快速路径
   「hash 相等就跳过整篇」在内容已变时**错误命中**：索引悄悄停在旧内容，零报错。
2. **`mark_pending` 不删 `vec_chunks`** —— 块级 hash 增量要靠旧块比对，删了就只能整篇重编码。
3. **`mark_pending` 归零 `fail_count`** —— 内容变了是一次新机会；而闸门语义仍然成立
   （worker 反复失败、不经过写点时照样累到上限）。

`update_index` 里字面索引与向量标记**各自独立 `try`** 也是同理：共用一个 `try` 时，
`vec.mark_pending` 抛异常会把「已经写好的字面索引」报成失败 —— 用户看到「保存失败」
但字面索引其实好了，重试又覆盖一次。有对照测试钉住（`test_vector_failure_does_not_kill_lexical_index`）。

### 决策：`vec.db` 放 `{data_dir}/vec.db`，不搬去 `%LOCALAPPDATA%`

设计文档原先把这条挂着等决策（「10 万块 ≈ 400 MB，`~/.limo` 会被同步」）。
结论是**不拆**，理由只有一条但很硬：**向量必须与它描述的那批文档同生共死。**

固定放 `%LOCALAPPDATA%` 会让「用户配了两个 `data_dir` 并在其间切换」变成**静默错位** ——
A 库的向量被 B 库拿去检索，余弦照样算得出一个数、结果照常返回、界面上完全看不出来。
这比「四元组不符」更难查：后者至少还有判据，跨库错位连判据都没有。

反过来，「会被同步」是**不便**不是**正确性**：最坏是同步工具多传几百 MB，
且只对主动开启了向量检索的用户发生。⇒ `vec.db` 是**纯派生缓存、删掉只会导致重算**，
这句写进文档供用户自行排除同步。它和 `secrets.bin` 不构成反例：那个去 `%LOCALAPPDATA%`
是为了**安全**（明文密钥不该进同步目录），`vec.db` 里没有任何秘密。

### 影响范围

**对用户零影响。** 表建好了、写点在打标记，但**没有任何查询路径读它、也没有 worker
消费它** —— `vec_chunks` 永远是空的，语义检索仍是「引擎注册了但没人用」的状态。
四元组校验也还没接（它需要「当前配置期望哪个 embedding 模型」，那份解析归阶段 3）。

---

## 2026-10-26（同日续三） — 向量检索阶段 2 第 1 步：算子搬到 core，评测与产品跑同一份

阶段 2 的第一步不是建表也不是写 worker，而是**先把算子挪到产品侧能 import 的地方**。
理由：`chunk_flat` / `chunk_h2` / `vector_rank` / `rrf` 此前只住在 `backend/scripts/embed_local.py`
——那是**评测脚本目录**。后台 worker 一旦要用，只有两条路：让 `app/` 反向 import `scripts/`
（分层被打穿），或者抄一份（模式 #54）。抄一份的失效形态不是「多几十行」，而是
**评测与产品的切块规则悄悄分叉**，那时 93% / 83% 描述的是另一套实现，而两边都报成功。

### 动了什么

| 文件 | 变化 |
|---|---|
| `backend/app/core/vector_index.py`（新，145 行） | 六个纯算子 + `H2` 正则。零重依赖，只用 `numpy`（已在核心依赖里） |
| `backend/scripts/embed_local.py`（221 → 119 行） | 只剩**评测专属**的 `Embedder`（ONNX 句柄，惰性 import）与评测形状的 `build_vectors`；算子改为 `from app.core.vector_index import ...` 转出 |
| `backend/tests/test_vector_index.py`（新，17 条） | 切块 / 排名 / RRF 的行为契约 + **一条 AST 同源守卫** |

**转出而非直接改调用方**是一条刻意的软着陆：`eval_search.py` 与 `_experiments/02-embedding-rrf/eval_ab.py`
写的都是 `from embed_local import rrf, vector_rank ...`，一个字都不用改。等价性由
`test_embed_local_只剩转出没有第二份实现` 长期钉住（扫 AST，发现 `def` 了同名的就红）。

### 等价性验证（不是「测试过了」，是逐字节比对）

搬家前后各跑一次 `scripts/eval_search.py --vector --json`，除 `encode_sec`
（146.3s → 185.7s，后台并发负载）外两份 JSON **逐字节相同**，包括每道题的命中 id 列表：

| 字段 | 搬家前 | 搬家后 |
|---|---|---|
| 文档数 | 137 | 137 |
| 块数 | 35,031 | 35,031 |
| 字面 Hit@5 | 23/29（79%） | 23/29 |
| 纯向量 | 24/29（83%） | 24/29 |
| RRF | 25/29（86%） | 25/29 |

### 顺带抓到一个让旧结论失效的缺陷

`H2 = re.compile(r"^##\s+\S", re.M)` 的 `\S` 只匹配**一个**字符 ⇒ `m.group()` 拿到的是
`"## 标"` 而不是 `"## 标题一"`。也就是说 `chunk_h2` 的「**每块前置所属标题**」
**从未真正生效**，两个都以「标」开头的标题前缀完全相同。

后果：§3.2 那张 A/B 表里 H2 的两行，比的其实是「按 H2 边界切」而不是
「H2 边界 + 完整标题」。所以「H2 没跑赢是因为块长度不均匀」这个解释**暂时不成立**——
得先把正则改成 `^##\s+\S.*` 再重跑，才有资格谈原因。

发现时先按「不改行为、只钉住」处理（一条断言现状、一条 `xfail(strict)` 记期望）。
随后当场修了，因为重跑只要 3 分钟，没有理由留着 —— **修完的结论见下**。

### 修完发现：净效果为零（本轮最有价值的一条）

修复是两处一起改的：

```python
H2 = re.compile(r"^##\s+\S.*", re.M)        # ① 原来只匹配一个字符
...
if "\n" in body:                             # ② body 本身包含标题行
    body = body.split("\n", 1)[1].lstrip("\n")
```

② 必须跟着 ① 一起改：只改正则会让标题在块里**印两遍**（前置一次 + 正文里又一次）。

**同一库、同一批 137 篇、同一天，修正前后逐档完全一致**：

| 档 | 修正前（首字） | 修正后（完整标题） |
|---|---|---|
| H2 200 | 纯向量 79% · RRF 86% · 35,595 块 | 纯向量 79% · RRF 86% · 35,286 块 |
| H2 400 | 纯向量 76% · RRF 83% · 17,969 块 | 纯向量 76% · RRF 83% · 17,892 块 |

**为什么「明显有 bug」却没有损失**：`body` 是从标题位置切的、**本身包含标题行**，
所以那个 `"## 标"` 是「多塞了一个无意义短前缀」（噪音），不是「丢掉了信息」。
修完净增量只有「完整标题代替首字」这一点点，落在 29 题的噪声里。

**为什么这条比「修好了、变强了」有价值**：

1. **缺陷的严重性必须实测**。读代码时 `^##\s+\S` 看起来是教科书级的错，实际损失可以是零；
   反过来，修完就宣布「H2 提升 X%」的话，X 必然来自别处的波动。
2. **顺带排除一个解释**：「H2 没跑赢固定切」与标题缺陷无关（修正前后都比固定切 200 的 83% 低）。
   真正原因（块长极不均匀）**仍未做消融验证**，文档里如实标为推测。
3. **暴露了历史读数的口径问题**：`1095d67` 修掉的 `chunk_flat` 截断缺陷让本库此前只索引了
   开头约 22%，所以那张表里的 93% 是「只看开头」的 93%（**截断反而让指标更高**：
   开头常是主题句、噪声又少）。今天全量索引后的 86% 才是诚实读数。
   旁证：**两轮字面基线逐条一致**（23/29，六条失手相同）—— FTS5 不分块、不受影响。

同时改了 `chunk_h2` 的契约（`chunk_flat` 那条 T2 截断记录是同一个教训的另一半）：

| 测试 | 变化 |
|---|---|
| `test_chunk_h2_每块前置完整标题且不重复` | 新，判据是**逐字相等**（「以标题开头」对「印两遍」也成立，钉不住） |
| `test_chunk_h2_只有标题的节不丢` | 新，整节只有一行标题时 `chunk_flat("")` 返回空，早先连标题一起丢掉 |
| 原「记现状」+ `xfail(strict)` 两条 | 删除（它们的存在意义就是这次修复） |

原始输出存进 `_experiments/02-embedding-rrf/results/`：
`ab-chunk-2026-10-26-h2fix.txt`（修正后五档）、`ab-oldh2-2026-10-26.txt`（修正前两档）。

### 设计文档同步（这几条是「现在写错了」，不是补充说明）

- §3.2 存储：`同库不同表` → **异库 `vec.db`**。三条理由都是**现在就存在**的机制：
  `SearchEngine._recover_db()` 删整个文件（把「几秒重建」升级成「整库重花一次编码钱」）、
  SQLite 写锁是库级（`rebuild()` 大事务与 worker 批量写冲突）、`vacuum=True` 会搬几百 MB 向量表。
  并补：`vec.db` 的**落盘位置要单独决策**（10 万块 × 1024 维 ≈ 400 MB，不该进会被同步/备份的 `~/.limo`）。
- §4.2 触发点后三行：`整库失效重建` → **标 `needs_rebuild` + 向量侧停用 + 用户确认**。
  判据是「字面索引重建免费所以可以自动，向量重建**要花钱**」——自动执行等于程序替用户花钱。
- 补 **四元组任一不符 = 整体不可用，没有「降级用」这一档**：硬算照样返回一个数，
  检索照常出文档，只是排序毫无意义而界面看不出来。
- §4.5 补 **`fail_count` 是「花钱的闸门」**：必须持久化（只在内存里则重启即归零 ⇒ 无限重试）、
  逐次 +1、到上限真的停。LiMo 此前所有重试都免费，这是第一次「重试 = 花钱」。
- §3.2 切块表**整表重写**：换成同库同轮的五档读数 + 三条结论（看纯向量列不看 RRF 列 /
  编码成本 ∝ token 总量不 ∝ 块数 / 历史 93% 是残缺索引的读数）。并把已证实的三条
  （修复净效果为零、H2 落后与标题缺陷无关、跨轮不可比）写进去。
- §3.2.1 成本表**整表重算**：「10 块/篇」是错的，本库实测 **256 块/篇**（137 篇 / 35,031 块，
  平均 4.27 万字/篇），低估 25 倍。新增**向量体积**一列（1000 篇 ≈ 520 MB、1 万篇 ≈ 5.2 GB），
  它比耗时更要紧，也是「`vec.db` 不进同步目录」的量化依据。

### 影响范围

零行为变化：没有任何查询路径调用向量侧，`engine_bindings` 无新增条目。
`scripts/embed_local.py` 的老调用方全部零改动。后端门禁全绿（pytest 40s）。
`chunk_h2` 的行为**变了**（标题完整 + 不重复），但它目前只有评测与测试在调，
产品侧尚未接入 —— 故对用户仍是零影响。

---

## 2026-10-26（同日续二） — 向量检索阶段 1：引擎接口 + dashscope 内置

设计文档 [`search-retrieval.md`](search-retrieval.md) §六把云端这一支分成四阶段，每阶段独立可停。
本轮只做**阶段 1**：把「向量化引擎」这个概念接进现有引擎体系，**不改任何现有行为**——
没有任何查询路径调用它、没有 `engine_models` / `engine_bindings` 条目，它等于不存在。

### 新增两个文件

| 文件 | 内容 |
|---|---|
| `backend/app/engines/embedding_base.py` | `EmbeddingEngine` **Protocol** + `l2_normalize` |
| `backend/app/engines/embedding_dashscope.py` | `DashScopeEmbeddingEngine`（`text-embedding-v4`） |

**为什么用 Protocol 而不是继承 `core.protocols.Engine`**：那个 ABC 的 `execute()` 对本类是
「必须实现却永不被调用的空壳」，而空壳最坏的不是多写三行，是**下一个人会以为它有用**
（在那儿补一句 `return self.embed(...)` 就是同步/异步混用）。注册表本来就是 duck typing，
Protocol 只是把「长什么样」写下来。

### 三条契约（测试逐条钉住）

1. **返回顺序与入参一一对应** —— 必须按 `resp.data` 的 `index` 重排。OpenAI 兼容端点的
   `data` **带 index 但不保证有序**；按返回顺序 append 会让「文本与向量错位」，而症状是
   检索结果莫名其妙、零报错、单测全绿（假数据天然有序）。
2. **向量已 L2 归一化** —— 余弦退化成点积。归一化放协议模块而非各引擎里：两份实现
   （`math.hypot` vs numpy）在极小模长上分叉，失效形态是「两个引擎的余弦不可比」。
3. **空输入返回空且不发起调用** —— 否则空列表也白白要求一份 Key。

### 三个「照抄会出事」的数字（官方文档查证）

| 参数 | 值 | 不遵守的后果 |
|---|---|---|
| 单次 `input` 条数 | **≤ 10** | HTTP 400 `batch size is invalid` —— 是**服务端契约不是调优旋钮** |
| 单条 token | ≤ 8192 | 服务端**静默截断**：后半段从未被索引，而我们这边看一切正常 |
| `dimensions` 可选 | 2048 / 1536 / **1024（默认）** / 768 / 512 / 256 / 128 / 64 | — |

OpenAI 兼容端点**只支持 `float` 编码**；`output_type` / `text_type` 要走原生端点，
往兼容端点发 `output_type=sparse` 会拿到 **HTTP 200 + 空向量**（比 400 难查得多）。

### 三处接线

- `core/registry.py`：`ENGINE_CATEGORIES` / `ENGINE_CATEGORY_ORDER` 加 `embedding`（**末位**）、
  `ENGINE_CAPABILITY_LABELS` 加 `"embedding": "向量"`。⚠️ 它与前三类**性质不同**
  （asr/ocr/doc 都是「文件→文本」、受 `engine_bindings` 子场景管辖；embedding 吃文本吐向量），
  但仍进同一张表——**发现机制完全一样**，另建一张表才是模式 #54 的入口。前端各处都按
  具体 category 取（`engineOptions.doc` / `.asr` / `.ocr`），新增末位对现有界面零影响。
- `engines/__init__.py`：在 OCR 段之后**无条件注册**（与 OCR 同款，没 Key 也列得出）。
  ⚠️ 刻意**不写 `engine_models` / `engine_bindings`** —— 那是阶段 2；写了会让末尾那次
  `save_config` 判定变成有变更，本轮就变成「会改用户配置」的改动了。
- `tests/test_engine_categories.py`：`_CATS` 加 `embedding`（该文件是刻意的「第二双眼睛」）。
  ⚠️ 顺带修了它自己的对照实验：样例串原先手写 `{"asr","ocr","doc"}`，加到第四项时当场失效
  （样例不再等于完整清单 → 「守卫抓不到内联形态」为真而红）。改成**从 `_CATS` 现拼** ——
  对照实验的输入必须跟着被测清单走，否则它守的是**过去的**那一版。

### 失败语义：一律显式，不降级

Key 缺失 / 网络失败 / 条数不符 / 维度不符全部直接抛。两条刻意：

- **Key 的抛点在 `client` property，不在 `__init__`**：`init_engines` 无条件注册它，
  设置页需要在没 Key 时也列出并显示「未配置」；搬到 `__init__` 会被那里 `except Exception`
  吞掉 → 引擎整个消失，用户看不到「有这个东西、只是没配」。
- **`max_retries=0`**：重试纪律归上层（连续失败 3 篇停 worker，§4.5），SDK 层偷重试会让
  失败计数永远涨不上去。

### 测试

新增 `backend/tests/test_embedding_engine.py` 11 条：协议形状 / `l2_normalize` 归一化与零向量 /
无 Key 抛且不留半构造 client / 空输入不发起调用 / 已归一化 / **按 index 重排**（上游乱序返回）/
批 ≤10（11 条 → 10+1，且**跨批不错位**）/ 条数不符 / 维度不符 / `init_engines` 后注册可见。

⚠️ 注册那条用例**必须打桩 `save_config`**：`init_engines` 末尾会因「自动绑定 doc 引擎」判定
有变更而调它，而那条路径指着用户真实的 `~/.limo/config.yaml`。隔离不能靠「我传了空 dict
所以没事」这种直觉（那依赖 `save_config` 内部的防御性校验一直存在），要用断言回答
「这个进程有没有写盘」—— 打桩时记一次数，末尾断言它确实被调用过（模式 #58）。

⚠️ 批处理用例里的假上游**不能按批内下标造向量**：它拿到的是切片后的批，第 0 条永远是第 0 维，
于是「跨批内容与向量对应」在假数据上恒真、断言没有区分力（第一版正是这么写的，第 10 条当场红）。
改按**文本里的编号**（`t7` → 第 7 维）。

### 刻意没做

- 向量存储三表 / 分块 / 后台补索引 worker（阶段 2）
- 查询融合 RRF / 开关 / 状态可见（阶段 3）
- 本地模型插件口子（阶段 4，设计见 §6.1）
- **不 bump `PROMPT_VERSION`**（不进任何 prompt）

---

## 2026-10-26 — 索引坏了要能看见、能修（三态 + 设置页重建 + 顺手删死代码）

排查「用户能不能自主重建索引」时发现真正的缺口不是「缺少手动重建」，而是
**索引坏了用户不知道、知道了也没处修**。三件事都指向同一个界面：搜索框。

### 问题：三处「静默」

| 层 | 之前的写法 | 后果 |
|---|---|---|
| 后端构造 | `SearchEngine(...)` 在 `main.py` 的 try **之外** | 一个坏掉的 `search.db` 让整个 startup 崩 —— 知识库**打不开**，而文档全在磁盘上 |
| 后端重建失败 | `except: log.warning(...)` 后继续 | 后续写入往半坏的库走，用户只看到「搜不到」 |
| 前端搜索 | `catch { setHits([]) }` + `searchDocs` **不检查 `r.ok`** | 503 错误响应被 `Array.isArray` 判成空数组 ⇒「索引坏了」与「库里没有」逐字相同 |

### 改了什么

**后端**

- `core/search.py`：`__init__` 容错（失败记 `_lastError` 不抛）；新增 `available` /
  `last_error` / `note_error()` / `status()` / `_recover_db()`；`index` / `delete` / `search`
  在不可用时**短路**（写入端不抛 —— 让它们抛会把「检索坏了」升级成「文档导入失败」）。
- `rebuild(..., vacuum=False)`：`vacuum=True` 时才 VACUUM。实测本机 `search.db`
  **137 行 / 14.74MB，23% 是空闲页**；启动路径不回收（每次开应用多等一次全库重写），
  用户主动重建才回收。
- `routers/search.py`：新增 `GET /api/search/status` 与 `POST /api/search/reindex`
  （后者过 `ensure_trusted_request` —— 有副作用的写操作）。
- `services/query.py`：新增 `QueryError.index_unavailable()`（`QUERY_INDEX_UNAVAILABLE`,
  503, **recoverable**），与 `QUERY_NO_SEARCH_ENGINE`（500，不可恢复）**刻意分开**。
- `main.py`：重建失败时 `note_error()`，让状态端点读得到。
- `core/dependencies.py`：新增 `get_search_engine`（直接取属性，不用 `getattr` 兜底 ——
  健康端点报假 `available: true` 比 AttributeError 更糟）。

**前端**

- `api/search.ts`：`searchDocs` 检查 `r.ok`（之前 503 被吞成 `[]`）；新增
  `getSearchStatus` / `reindexSearch`；**修正过时注释**（原写「只搜原文」，实际
  2026-10-26 重做后 = rawText + 最新版笔记）。
- `doc-library/useDocQuery.ts`：搜索失败不再静默，返回 `searchError`；`hits` 仍清空
  （否则上一次关键词的结果会混进来），但另配一条提示说明「正文这一路没跑」。
- `doc-library/DocList.tsx`：列表底部显示该提示（截断提示上方，性质不同所以不合并）。
  顺带把「原文匹配结果」改成「正文匹配结果」（索引已含笔记）。
- 新增 `settings/SearchIndexCard.tsx`：插在 设置 → 通用 的「导出行为」之前 ——
  状态（可用/不可用 + 原因 + 篇数 + 体积 + 上次重建时间）+ 二次确认的重建按钮。

### 顺手删掉：`store.py::cache_put` / `cache_get`

`backend/app` / `backend/tests` / `backend/limo_cli` **零调用**（grep 只命中 `.venv` 第三方）。
路径 `{root}/cache/{hash[:2]}/{hash}.json`，**无 TTL、无上限、无清理**，`_rebuild_index`
还专门跳过 `cache` 目录 —— 一段会悄悄长胖死目录的代码。

### 一个只有实测才抓得到的坑

`test_rebuild_recovers_from_corrupt_db` 首次运行就红：
`WinError 32 另一个程序正在使用此文件`。根因是 `_conn()` 返回裸 `sqlite3.Connection`，
而 `with conn:` 只做 commit/rollback、**不关闭文件句柄** ⇒ **「删掉损坏的库重建」
这条路从来就走不通**（失败表现只是「重建后依然不可用」，没有任何线索指向文件占用）。
改成 `@contextmanager` + `finally: conn.close()` 后通过。
⇒ 这条印证了「坏库自愈」此前从未被真正走过，也说明**可见性**这一环缺了多久。

### 影响范围

用户可见：设置 → 通用 多一张「检索索引」卡；搜索失败时列表底部多一条提示。
零回归：索引正常时既不显示提示、也不多任何请求（设置页那张卡只在打开时取一次状态）。

### 刻意没做

- **对账入口 / 全量重算**：`docs/search-retrieval.md` §4.6 的三条 SQL 全打 `vec_*` 表，
  而向量侧零实现 —— 现在没有对象可对账。等阶段 2 落地再补入口。
- **`index.json.bak` 过期清理**：它存的是**损坏原文**（含标题/标签/sourceUrl），确实没有
  过期机制；但「自动删掉用户可能的最后一份线索」比留着一个大文件更糟。待定，已记入待办。
- **查询向量缓存**：留到阶段 3，但已在 §八 补上它的安全面（**查询词是搜索意图日志**，
  比索引正文更敏感）。

---

## 2026-10-26（同日续） — 从「能看见」到「主动看见」：文档库顶部横幅

上一条把索引状态做成了**可以问**（`GET /search/status` + 设置页那张卡），但它仍是
**拉取式**的：用户得先怀疑索引、再走进设置页。而索引坏掉时用户的第一反应是
「我的库是不是丢了」—— 这个顺序反了。

### 改了什么

- 新增 [lib/search-health.ts](../frontend/src/lib/search-health.ts)：模块级状态 +
  `useSyncExternalStore`。**刻意不轮询** —— 对照 `lib/health.ts` 的后端探活（进程真的会掉），
  索引的可用性只在三个时刻变化：启动重建、手动重建、`search.db` 被外部删。
  挂载时取一次（30s TTL 去重）+ 搜索失败（503）时强制刷新 + 重建后写回。
- 新增 [SearchIndexBanner.tsx](../frontend/src/components/doc-library/SearchIndexBanner.tsx)：
  挂在 `DocLibrary` 顶部（工具栏之上，两种密度都覆盖），带「重建索引」按钮与原因说明。
- `useDocQuery` 的 catch 里，503 时 `refreshSearchHealth(true)`：提示与横幅同源，
  不会出现「列表说索引坏了、顶部却没动静」。
- `SearchIndexCard` 重建成功时 `setSearchHealth(next)`：用户从设置页修完，横幅立刻消失
  （否则会以为没修好）。

### 两条判据

- **`available === false` 才渲染**，`null`（还没取到 / 取不到）一律不渲染。反过来写会在
  后端没起的几十秒里挂一条「去重建索引」，而那时按钮根本点不动 —— 净增一次误判。
- **刷新失败保持上一次快照**。「问不到」不等于「已经好了」，也不等于「坏了」。
  有对照断言：把 catch 改成「失败即清空」，用例会红。

### 测试

`searchHealth.test.tsx` 9 条（渲染判据 / 懒探测 / 重建成功与失败 / 关闭 / TTL / 失败保快照）
+ `docLibraryLayout.test.tsx` 加 **2 条接线守卫**（横幅真的挂在文档库里 / 索引正常时零渲染）。
后者做过对照实验：把 `<SearchIndexBanner />` 从 `DocLibrary` 摘掉，两条立刻变红 ——
否则「组件写好了却忘了挂上去」会全绿。

⚠️ 顺带修：`docLibraryFilters` / `docLibraryLayout` / `docLibrarySearch` 三份
`vi.mock('../api/search')` 都要补 `getSearchStatus`（`DocLibrary` 现在挂载即探测），
漏了是 `No "getSearchStatus" export is defined on the mock`。

---

## 2026-10-26 — worker 纪律按本库画像砍掉两条：五条 → 三条留、两条砍

上一条把「worker 五条纪律」写进了设计，同时如实标注它们**全是工程经验值、没有一个来自实测**。
本轮把那句备注变成了行动：**量自己的库**，然后按结果砍。

### 依据：`_experiments/04-library-profile/`（新沉淀）

只 `stat` 版本文件、不读正文，150 个目录 / 132 篇有版本 / 316 个版本文件 / **55 条间隔样本**：

| 读数 | 值 |
|---|---|
| 同 mode 相邻版本间隔 | **>1 天占 53%（中位 5.0 天）**，<1 小时只占 29%（中位 3 分钟） |
| 每篇版本数 | p50 2 · 均值 2.39 · 最多 6 · 单版占 2% |
| 单篇活跃跨度 | p50 5.3 天 · p90 50.5 天 |

⇒ 典型的「低频、天级、长尾」库，「坐下来连续改十遍」这个场景**不存在**。

### 改了什么（`docs/search-retrieval.md`）

- **§4.5 标题与表**：「五条纪律全是经验值」→「**三条留、两条砍**」。砍 debounce（要防的
  场景不存在）与积压阈值（**砍阈值、留状态**：不需要「超了才显示」，永远显示 `128/131 · 3 篇失败`）。
  留批量编码 / checkpoint / 失败上限 —— 后三条在低频库里**反而更重要**，因为唯一的高频时刻
  就是首次开启那一刻（58 万块跑一小时）。
- **§八 未决问题第 8 条**：剩下的魔数从 4 个收窄到 2 个（批量 64 块 / 失败上限 3 篇），
  并说明这两条仍无实测支撑。
- **§6.1 本地插件对照表与「何时触发」**：原写「云端 debounce 30 秒 / 本地 3 秒」——
  与 §4.5 直接冲突（那边刚砍掉它）。现改为「**没有 debounce 这个参数可改**」，
  并把本地能多做的两件事（生成完成立即排队 / 冷启动重排全部 failed）的理由从
  「省 debounce 的等待」改成「云端攒批编码时可能白等一个批量窗口」。

⚠️ **砍掉的那两条是这次最有用的产出** —— 不是「忘了做」，是**做过又删了**。
通用做法在这里的失效形态不是「多写了 20 行」，而是**给一个不存在的场景写了一套状态机**：
两个计数器都不会被触发，但每个读代码的人都要先理解它们，才能发现它们是空的。

### 顺带修正的一个数

同一条统计里「中位 **16.4 天**」是错的，重算是 **5.0 天**（pooled）/ 5.5 天（per-doc）。
其余读数（53% / 29% / 均值 2.39 / p90 50.5 天）逐值复现 —— 说明只有中位那一个数不是从
这份样本算出来的。已修 `docs/search-retrieval.md` 三处与 `_interview/09` 两处。
⚠️ **55 条样本只够支撑「天级」这个量级**，已在两处如实标注。

---

## 2026-10-26 — 向量索引生命周期定形：建索引门槛 / 增量机制 / 本地插件三问

`docs/search-retrieval.md` 此前只有「做不做向量」与「怎么融合」，**没有回答「什么时候建、
怎么维护」**——而那是落地时第一个撞上的问题。本轮把三块补齐，全部是设计，无代码改动。

### ① 建索引的门槛与规模上限（新增 §3.2.1 / §3.2.2）

**问题**：「到什么数据量才该建向量索引？」

**结论：这个问法前提就不太成立**——成本曲线太便宜。按实测编码速度（T2 的 90 块/秒，
最慢的长文档）推算：1000 篇 ≈ 2 分钟一次性，1 万篇 ≈ 18 分钟。于是真正存在的门槛是
**20 篇**（以下不建），而这个门槛没有决策价值。

**改问「字面够不够用」**——但 LiMo 没有查询日志，答不了。故**不做自动判断**，
开启时给诚实的估算（「当前 1,234 篇，预计 2 分钟，可随时中断」）让用户按体感决定。
硬猜「你的库属于术语同源还是异源」，猜错了比不猜更糟。

**规模上限**：暴力检索（384 维 float32）拐点在 **50 万块 ≈ 5 万篇**（100–200 ms），
个人库到不了 ⇒ **不需要 ANN**，不提前引入 `sqlite-vec`/HNSW 依赖。
⚠️ 该数是按内存带宽与余弦计算量推算，**不是实测**，已如实标注。

### ② 增量机制（新增 §4.4-4.6）

**核心决定：按「块」判增量，不是按「文档」判。**

最朴素的「文档变了就重编码整篇」会浪费 90% 计算——改一个错别字，整篇 15 块全重算。
改为 `vec_chunks.text_hash` 块级指纹：hash 没变的块直接复用旧向量，只编码变了的。
改一句话的成本从「整篇 200 ms」降到「10 ms」。

**表结构相应改三处**（§3.2）：
- `vec_meta` 加 `schema_version` —— 缺它的话，改切块规则后老索引会继续按旧规则算、
  两边都报成功（同 `_EMPTY_CONTAINERS` 那类静默失配）
- `vec_chunks` 加 `text_hash` —— 增量判定的唯一依据
- 新增 `vec_state` —— 文档级状态（`content_hash` / `status` / `fail_count` / `error`），
  供进度条与失败区展示

**同时纠正了 §4.1 的伪代码**：原写法是「vec_chunks 删除 + 待办表记一行」，
那等于每篇都全量重编码，与增量机制自相矛盾。现改为「只打 `pending` 标记，不删块」。

**worker 五条纪律**（debounce 30s / 批量 64 块 / 每批 checkpoint / 连续失败 3 篇停 /
积压 50% 显进度）**全部是工程经验值，没有一个来自实测**——已写进未决问题第 8 条，
要求落地后拿真实使用日志校准，别让它们变成「看起来有依据」的魔数。
⚠️ **同日稍晚已按实测砍掉两条**（debounce / 积压阈值），见上一条 —— 本条保留当时的样子。

**对账 ≠ 重建**：对账是「找出漏的补上」（三条 SQL，启动时跑），重建是「推倒重来」。
把重建当日常兜底，等于用高成本掩盖增量的漏写——问题还在，只是看不见了。

### ③ 本地插件的三个「什么时候」（新增 §6.1）

此前文档两处标着「待详细设计」，而用户早前就明确要求过这条。本轮定完：

| 问题 | 结论 |
|---|---|
| 索引何时建 | 模型下载挂在**安装插件**时（明确的用户动作）；首次开启时全库 `pending` |
| 何时用 | **worker 预热**加载并常驻（开启必然产生 pending ⇒ 必在首次查询前加载好）。启动时加载会拖慢每次冷启动，懒加载会让用户以为卡死 |
| 何时触发 | **与云端同一份触发点**，只把 debounce 30 秒改 3 秒（⚠️ **同日晚已作废**：debounce 整体砍掉，见上一条）|

⚠️ **「同一份触发点」是刻意的**：触发点是产品语义（「内容变了就该重算」），不是引擎语义。
按引擎分两套的失效形态是「换了引擎之后，某些更新路径悄悄不生效了」。

进程外的两个后果也写清了：好（模型崩了不拖垮主进程）、代价（每次 IPC）、
必须（插件进程常驻，不能每次重载模型）。**查询侧 IPC 只用来拿 query 向量**，
余弦由主进程在 sqlite 读出的矩阵上算——不把整个向量库搬进插件进程。

---

## 2026-10-26 — 三个「偶发」bug：沉浸阅读被旁人的任务拦住 / 追问产物丢掉 / 图表回边叠死

用户报的三条，共同点是**表现为「偶发」**——而偶发通常不是随机，是某个条件没被认出来。
三条分别属于「判据太宽」「时机太早」「几何只依赖层号」，都是同一个毛病：**结论用的输入
比它以为的少**。

### ① 别的文档在转录/生成时，无法对另一篇进入沉浸阅读

**现象**：文档 A 跑着转录，切到文档 B 点「沉浸」，直接被拦下并提示「操作进行中」。

**根因**：进入沉浸的守卫是 `processingTasks` **非空**就拒 —— 而那是**全库**的在跑任务表，
与「当前这篇」无关。用户的心智模型是「我看的这篇没在跑就行」，代码实现的是「整个应用空闲
才行」。

守卫当初的理由是「`SlotLayout` 真卸载 GenPanel ⇒ 正在跑的任务变僵尸」。**那个理由在当前
架构下已不成立**，三条依据**每条都与组件是否挂载无关**：
1. abort 控制器活在 `api/taskManager.ts` 的**模块级** Map，卸载 GenPanel 不 abort 任何请求
   （GenPanel 唯一的 cleanup 是摘 window 监听）；
2. SSE 回调直接往 store 写（闭包在卸载后依然有效）；
3. 收尾 `setProcessing(key, null)` 在 `finally` 里。

⚠️ 这个守卫**恰恰是靠「组件卸载」这条直觉活下来的**：它写在守卫成立的那个年代，之后
架构换了（任务管理搬到模块级），守卫却没人回头看一眼 —— 于是它的成本（真功能损失）一直
在付，收益（防僵尸）早已归零。**判据的成立条件会随架构漂移，而判据本身不会报错。**

**修法**：**整个进入守卫移除**（`toggleImmersive` / `setImmersive` 都只剩一行 set）。
不是「收窄到当前文档」—— 沉浸是纯视图切换，不写文件、不占执行槽，没有任何理由是它该被
任务拦住的。唯一残留代价是沉浸期间看不到任务进度与取消按钮，按 Esc 退出即可。
退出方向**仍然**不设限（否则用户会被困在沉浸里出不来）。

⚠️ **`Ctrl+G` 的同款守卫刻意没动**：那是「切换生成面板」，与正在跑的任务有真实的竞争
关系（面板要显示进度、要能取消）。两处**看起来一样**（同一句 toast），但语义不同 ——
一起改会顺手放开一个真需要拦的路径。

### ② 勾了「追问卡片」偶尔没有产物

**现象**：勾选后，追问卡片时有时无 —— 尤其「生成完先去看别的地方，再回来」。

**根因**：追问是**独立一次** LLM 调用，结果推在 `done` **之后**（几十秒）。回调里有两道
早退：
- `if (!isSameDoc(capturedId)) return`（切走了就丢）；
- `markdownMeta.mode === 'notes'`（切到整理版 tab 就丢）。

原本的理由是「数据已在盘上，切回来时走加载路径能拿到」。**这个理由不成立**：产物 tab
切回来时若正文已在内存里，走的是 ref 同步、**不再发 GET** ⇒ 那一版 meta 永远不刷新，
卡片永久缺席。于是「用户等的那几十秒里干了点别的」这件事，代价是**整批追问被丢掉**，
而界面上完全看不出发生过什么（没有 loading、没有报错）。

**修法**：加一条**晚到通道** `followUpsByVersion`（键 `docId::mode::version`），事件到达
时**无条件**写进去，与用户当前在看哪里无关；预览区渲染时优先用 `markdownMeta.followUps`
（后端权威、随版本一起返回），缺席才回退到这条通道。

三个必须的细节：
- **键必须带 version**：追问在后端是**按版本各存**的（`v{N}.followups.json`），不带版本会
  让「切到没有追问的旧版」显示新版那一批（而那看起来完全正常）。
- **`version` 来自 SSE 事件本身**（`followup` payload 带 `version`，后端从 index 带出），
  不是前端猜当前版本 —— 前端手上那版可能已经不是刚生成的那版。
- **不需要清理**：键含 docId + version，重新生成开新版本号，旧值自然不再被读到。

⚠️ 与「晚到」这个性质直接对齐：事件晚到是**设计**（不吃生成预算），那就得承认它晚到，
而不是假设回调执行时用户还站在原地。

### ③ 图表偶发复现旧 bug（如「环形表格」）

从 6 条失败产物反查出**两条独立成因**，各自都能单独让整张图降级成「📊 图表生成失败」占位：

**成因 A（4/6 条）— 同一节点的多条回边共用一条走廊**。`_state` 的回边路由里，
`corr_y = _below(rank[u])` 与 `entry` 都**只依赖层号**，于是同一个「半开」节点发出的
两条回边（熔断器三态：→ 关闭 / → 打开）水平段 y 完全相同、右侧沟槽 x 只差 6px，
画面上并成一条横线；它们的两个标签必然叠死 → `assert_layout` 判「标签遮罩重叠」→
**整张图失败**（而降级占位只说「布局失败」，用户看不出是标签的事）。

修法是把**走廊里的每条水平段**都放进统一车道池：前向边占 1 段，回边占 2 段
（下到源层走廊 + 从目标层上方走廊进入），走廊高度按段数增长。纯前向图的 lane 值与
改造前**逐值相同**（段数、顺序、均分公式都没变，既有硬编码路径 `M74 460 V496 …` 仍绿）。

顺带修了两处同批暴露的几何缺陷：
- `_trim_canvas` 的平移量 `dx` 被左侧突出的边标签拉成非 4 的倍数（标签宽 = 文字宽 + 20，
  比节点宽时越界）→ **整张图的节点跟着脱格**（实测 x=29），被 4px 网格不变式当场抓住。
  修法 `dx -= dx % UNIT`，最多让居中偏 3px（不可见）。
- `_relax_labels`（新增，**兜底**）——标签遮罩还是重叠时先上下让位（±20/±40/±60，4 的倍数），
  让不开就**丢弃这个标签**（连线仍在）。只处理「标签 × 标签」：原位置就压住节点的标签
  不在这里救，那是布局器把标签放错了地方，让位只会把它藏起来。

**成因 B（2/6 条）— 模型用 `flowchart` 表达往返路径**。`flowchart` 算子**结构性拒绝任何环**，
而原引导只写了「带分支与汇合的流程」。模型想画「请求下行 / 响应上行」这类往返时自然画出回边，
整张图直接失败。修法是在 `get_chart_format()` 的 flowchart 那一行加一句硬边界
（「**单向前进，不能有回边/自环**；有回环改用 state」），`PROMPT_VERSION` → `"20"`。
⚠️ 报错文案本来就已经告诉**用户**「有环请改用 state」，但模型看不到渲染结果，
一次生成内无从自愈 —— 这句话必须写在**给模型看**的地方。

⚠️ **`hchain`/`timeline`/`layers`/`tree`/`sequence`/`quadrant` 的引导已经不在 chart 里**
（前几轮陆续迁到语义块），本轮没有往回加任何布局 —— 只补边界，不加能力。

**范围**：`chart_layout/layouts.py`（走廊分道 + `_entry_corr`）、`chart_layout/engine.py`
（`_relax_labels` + `dx` 落格）、`generation/prompt.py`、`prompts/__init__.py`、
`test_chart_layout.py`（新增「回边分道」用例，含「让位只是兜底」的对照断言）。

---

## 2026-10-26 — 全文检索重做：29 题实测 41% → 83%，主因是两个静默截断

**起点**：用户反馈「几百篇文档就已经有些开始比较难找」。先造仪器再选方案 —— 从 131 篇真实库里
挑 29 篇，为每篇写一道期望命中它的查询，分**语义**（15，完全改写）/ **精确**（9，专有名词）/
**混合**（5，关键词+语序）三类分别计分，判据「目标进前 5」。只看总分会被「语义全崩但关键词全对」
这种系统蒙混过去，而用户多数时候是凭印象问的。

### 三个改动的实测贡献

| 改动 | 收益 |
|---|---|
| `_clean_for_search` 删掉 `text[:50000]` | +11pp |
| 中文分词：逐字 AND → **二字组 OR** | +17pp |
| 索引文本加上**最新版笔记** | +17pp |
| **合计** | **41% → 83%** |

前两项收益**可加**（55%+11pp→66%，再+17pp→83%），这本身就是「两个独立问题」的证据。

### 两条主因都是静默的

**① `_tokenize` 的 `uniq[:64]` 污染了索引侧**（本轮最隐蔽的一个）。该上限原为查询侧所写
（用户可能粘贴整段当查询，OR 串会膨胀到 FTS5 处理不了），但 `index` / `rebuild` 也调它且不传参
—— **每篇文档只索引了开头 64 个 token**，一篇 85k 字符的文档 `toks` 列只有 190 字符。

症状极具误导性：**单测 14 条全绿**（用例全是短文本，64 够用）、标题命中正常、语义题还能靠
「答案常在开头」蒙对几道，而精确题只有 1/9。四个表象分别指向四个不同结论，全都不指向截断。

修法是给 `_tokenize` 加 `limit: int | None = None`，**默认不限**，只有 `search()` 显式传 64。

**② 索引里没有笔记**。用户搜的往往是他自己在笔记里写下的词，而索引只有 rawText ——
等于「只搜用户没写过的那部分」。修法是把 `rawText + 最新版笔记` 一起索引。

### 落差 40pp：实验脚本 ≠ 产品

实验脚本跑出 83%，同样方案搬进产品实测 41%。根因是**实验脚本里我照着产品代码重写的分词函数
没抄那个 `[:64]`** —— 复刻品无意中修好了原件的 bug，于是复刻品的分数永远高于原件。
修掉后产品实测与实验脚本**逐值相等**（语义 11/15、精确 8/9、混合 5/5），两套独立实现交叉验证。

> 教训沉淀在 `_interview/07-search-eval.md` §八之二（⚠️ 该目录与 `_experiments/` 均不入库，
> clone 后看不到，教训本身已写在本节）：**单测全绿 + 实验全绿 + 产品坏掉，三者可以同时成立。**

### 索引文本收口成唯一判据

「索引是什么文本」原本在**五个写点各拼一份**：摄入 / 重新转录 / 生成完成 / 编辑保存 / 启动重建。
任何一个漏掉「笔记」这一项，症状是「生成后搜得到、编辑后又搜不到」—— 两次写都返回成功，
用户只会觉得搜索「时灵时不灵」。

现在收成两处：判据 `doc_text.search_index_text(store, tx_id)`、写入口 `core/search.py::update_index()`。
`update_index` **从 store 取**而不接收调用方手上的文本（调用方那份常常还没落盘）。

⚠️ 生成侧**只挂一次**（流式端点三个 `done` 出口的统一收尾处），不挂在每个 `save_markdown` 之后 ——
双产物会写两次，而索引只需反映最终状态；取消路径（partial）也汇到这里，同样该被索引到。

### 性能：刻意不引入线程池

最大的那篇（37 万字符）重索引实测 **103ms**（含读盘 + 分词 + SQLite 写），发生在 SSE `done` 之后，
用户感知不到；本地应用并发度低，改成 `asyncio.to_thread` 换来的是注释复杂度而非体验。

### 剩余失手（5 题）与下一步

字面检索的天花板都是**纯语义改写**（「画师担心被 AI 取代该怎么稳住」「现在靠拍照挣钱还干得下去吗」）
与**专有名词 embedding 不认**的两头。实测纯向量单独 86%，与字面失手题**几乎完全错开**
（交集只有 `1048`，那是数字规范化问题，与检索方式无关），RRF 融合理论上限 **97%**。

向量侧**未落地**，方向是 `EmbeddingPlugin`（走既有插件口子，不内置 —— 依赖重、有 API 成本、
跨厂商协议不一）。

**影响范围**：`core/search.py`（分词 / 清洗 / 三列结构 / `update_index`）、`generation/doc_text.py`
（新增 `search_index_text`）、`routers/ingest.py`、`routers/generate.py`、`routers/markdown_edit.py`、
`main.py`（启动重建走同一判据）。`tests/test_search.py` 新增 4 条（含「长文档中段可搜」那条，
它是唯一能在 `[:64]` 回归时变红的用例）。`bash scripts/check-all backend` 全绿（17 passed）。

---

## 2026-10-26（晚） — 评测脚本补上 `--vector`：向量与 RRF 不再只活在 `_experiments/`

**起点**：同日那条把字面检索从 41% 修到 83%，也实测了纯向量 / RRF 两组数（云端 86%/97%、
本地 83%/93%）。但这两组数**只有实验脚本能给** —— 产品侧的 `eval_search.py` 只支持字面检索，
于是「阶段 3 验收」这一项依赖一个**不入库**的私有目录（`_experiments/` 在 `.gitignore` 里）。

### 三件事

1. **抽出 [scripts/embed_local.py](backend/scripts/embed_local.py)** —— ONNX 句柄 / 切块
   （`flat` + `h2`）/ 向量排名 / `rrf` 的**唯一一份实现**。原先
   `_experiments/02-embedding-rrf/eval_ab.py` 自带一份；产品脚本要补 `--vector` 时就得抄第二份
   —— 而那正是同日 40pp 落差的成因（实验脚本自己重写一份实现，复刻品越写越对，原件从没被真跑过）。
   现在两边都 `import` 它，`eval_ab.py` 只留「多切块策略扫描」这一件产品脚本不做的事。
2. **`eval_search.py --vector`** —— 一条命令打出字面 / 纯向量 / RRF 三路。
   137 篇库上复现 **79% / 83% / 93%**，**逐题失手清单与实验脚本逐条一致**。
   不传 `--vector` 时输出与改造前逐字节相同（`--json` 的字段集也钉住了：
   `docs / hit / misses / per_kind / total`）。
3. **`onnxruntime` / `tokenizers` 刻意不进 `pyproject.toml`** —— 本地 embedding 按口子原则
   走插件口子（阶段 4），不该进核心依赖。缺失时抛 `MissingDependency` 并打印安装命令，
   **不静默退化**（否则表现为「--vector 跑出来和字面一模一样」，看不出是没装依赖）。

### ⚠️ 顺带修掉一个只在规模上才现形的坑

`vector_rank` 的分组最大值原是 `{tid: sims[owner == tid].max() for tid in uniq}` ——
每个文档做一次全数组布尔掩码，**`O(文档数 × 块数)`**。137 篇（6561 块）下几十毫秒，
看不出任何问题；换到 10 万条 corpus 的公开数据集上它要跑**几小时**（实测一次跑到 12 分钟
仍在 `vector_rank` 里，只能杀掉）。改成 `np.unique(return_inverse=True)` +
`np.maximum.at`（`O(块数)`），**137 篇上重跑三路数字逐题不变**（等价性已验证）。

> 教训：**同一个函数在 137 和 100,000 两个规模上不是同一件事。** 只在小库上跑过的实现，
> 「能跑」这个判据的覆盖范围也就到小库为止。

### ⚠️ 同一个教训的第二例：切块函数在长文档上静默截断

同一天在公开数据集上又撞到一个同族的问题：`embed_local.chunk_flat` 把单个超长段落
**截断**成前 `size` 字（`c[:size]`）而不是切分。它在三个短文档集上几乎不显形 ——
Ecom 丢 0.00% 的字、Medical 9.85%、Video 6.72%；到 T2Retrieval（p50 469 字、
正文不带空行分段）上，118,605 篇**全部**只索引了前 200 字。

它的失效形态是最难发现的那种：输出只多一行 `向量：118605 块`，而「一篇一块」是个
**完全合法**的结果，唯一的异常是那个数**恰好等于 corpus 条数**。读数 `Hit@5 91.1%`
（纯向量）比字面检索还高 11.7pp，**看起来没有任何理由怀疑** —— 而它量的是
「只看每篇开头 43% 的文本」。

> **判据**：`块数 == 语料数` 且 `文档长度 p90 > chunk_size` ⇒ 切块根本没发生。
> 通用形态：**产量类函数的输出不该恰好等于输入规模**。已写成 `run_ecom.py` 里的
> 运行时自检，一跑出来就报警，不再靠人盯那个数。

修法是改成连续切片（`c[i:i+size] for i in range(0, len(c), size)`）。修复后 T2 的块数
由 1 倍涨到 **4.9 倍（580,701 块）**，编码耗时约 **63 分钟** —— 这也是「成本按块数算、
不按篇数算」的实测量级：同样 11.8 万篇，切块策略一换，编码成本差 5 倍。

⚠️ **这条与上面的 `vector_rank`、以及本节前一条（2026-10-26 早）修掉的 `text[:50000]`
/ `uniq[:64]` 是同一族的**：代码在 137 篇的小库上完全正确，换到 10 万条上要么慢到
跑不完、要么**静默只处理了一部分数据**。共同点不是「写错了」，而是**验证规模与使用
规模不一致** —— 而三处的读数当时都很好看。

**影响范围**：新增 `backend/scripts/embed_local.py`；`backend/scripts/eval_search.py`
（抽出 `_text_getter` / `_score`，新增 `run_vector` 与 6 个参数）；
`_experiments/02-embedding-rrf/eval_ab.py` 改为 import 公共模块（行为与数字均不变）。
`docs/search-retrieval.md` §2.2 / §六 / §七 三处「`--vector` 尚未实现」的失效陈述同步改掉。
⚠️ `chunk_flat` 的截断修复同时影响公开集复核的向量读数：Ecom 零影响，Medical / Video
的既有读数偏低（分别有 9.85% / 6.72% 的字从未进索引；方向明确、量级未测）。
实测明细在 `_experiments/03-public-datasets/README.md` §5（⚠️ 该目录不入库，结论已写在本节）。

### 附带：在公开数据集上复核 29 题的结论（三个结论，两个打折）

29 题的 1 题 = 3.4pp，是最容易被戳的一点。用 `mteb/EcomRetrieval`
（**100,902 条**商品标题 × **1000** query × 1000 条 qrels）跑同一套判据：

| 判据 Hit@5 | 字面 | 纯向量 | RRF |
|---|---|---|---|
| Ecom（10 万条 × 1000 题） | 49.1% | 64.2% | 65.9%（1:2）/ 64.3%（等权） |
| LiMo 自建（137 篇 × 29 题） | 79% | 83% | **93%**（等权） |

- **向量 > 字面**：✅ 成立，且差距更大（+15.1pp vs +4pp）
- **融合有收益**：⚠️ **量级缩了一个数量级**（+1.7pp vs +10pp），**还要调权重才拿得到**
- **RRF 等权优于加权**：❌ **被推翻** —— 等权次优，且它的 nDCG/MRR **低于纯向量**
  （0.551/0.494 vs 0.565/0.517）。同一段代码上，给字面加权则单调恶化到 53.1%，
  与「偏向量最优」互为镜像
- **k=10 优于 k=60**：✅ 原样成立（唯一没被打折的一条）

**根因**：RRF 等权隐含「两路排名同等可信」。LiMo 上两路 79%/83%（差 4pp）且失手几乎不重叠
（交集仅 1 道）⇒ 假设成立；Ecom 上 49%/64%（差 **15pp**）⇒ 给低精度信号一半话语权就是损害排序。
**另一条硬约束**：融合的上限由两路召回池决定（Ecom 池子 89.1%），RRF 只重排、不召唤新文档。

⚠️ **`k` 与权重是耦合的**：等权时 k=10 最优，给字面加权后 k=60 反超 ——
因为 k 小会放大「某一路排第 1」的权威性，而低精度那一路的第 1 名经常是错的。

**顺带修了三个判据口径缺陷**（都在实验脚本里，不进产品）：
① `rank_of(ranked, gold, k)` 的 `k` 从未被使用 ⇒ `nDCG@10`/`MRR@10` 实际是 `@50`；
② 失手重叠标着「Hit@5」实际判 `@50`；③ 「任一路命中」用反了德摩根（得到的是「两路都命中」，
两者差 30pp）。**口径错了但数字看起来完全正常**，是这轮最值得记的一条。

**影响范围**：`docs/search-retrieval.md` §2.2 追加复核段、§八 问题 2 标为已回答。
`backend/scripts/embed_local.py` 的 `rrf` 加可选 `weights`（缺省全 1.0 = 原行为，逐字节不变）。
实验记录与脚本在 `_experiments/03-public-datasets/`（**不入库**）。

---

## 2026-10-22 — Linux CI 长期红：`test_config_io.py` 的 11 条契约改由假 DPAPI 顶替

**症状**：自 2026-10-17 起每次 push，GitHub Actions 上 `test_config_io.py` 固定 11 条失败：

```
app.core.secrets.SecretsUnavailable: DPAPI 仅 Windows 可用（当前平台 linux）
```

**这不是缺陷，是平台不支持**：`test_secrets.py` 早已用模块级 `skipif(not S.is_available())`
绕开，但这个文件没有 —— 而它的 11 条契约（抽 ↔ 填、磁盘形态、启动自检）恰好都要经过
`save_config → save_secrets → protect`。

**修法不是 skip 而是替换**（`tests/test_config_io.py` 新增 autouse fixture）：非 Windows 平台
把 `protect` / `unprotect` 换成可逆假原语，契约照常跑。理由是这个文件测的是**三张成对表的两侧**
与磁盘形态，**DPAPI 本身不是被测对象**（它的往返由 `secrets.selftest()` 负责）；skip 掉会把
7 条与加密无关的纯逻辑用例一并丢掉。

⚠️ **三条一起换，缺一不可**：只换 `protect`/`unprotect` 而 `_DPAPI_READY` 仍是 False 时，
`selftest()` 第一行就返回不可用 → `startup_check` 多报一条「DPAPI 不可用」→
`test_startup_check_*` 的 `== []` 继续红，**而红的原因看起来与加密毫无关系**。

**两条对照实验**（都在本地用 `cp` 备份还原，未用 `git checkout`）：
① 强制走假分支（模拟 Linux）→ 18 passed，证明假原语足以支撑全部契约；
② 强制 `_DPAPI_READY = False` 且**不**替换 → **11 failed / 7 passed**，与 CI 报的失败数
逐条吻合，证明修的是那个问题、且没有多跳。

**影响范围**：仅测试基础设施。Windows 上 `is_available()` 为真，fixture 原样返回，
真实 DPAPI 路径零改动。`bash scripts/check-all backend` 全绿。

---

## 2026-10-22 — 设置页吸顶横条被主题卡片的色环与文字穿透

**症状**（用户反馈：「设置页面有些元素比如外观的那些颜色色例会透过某些栏」）：设置 → 外观，
向下滚动时「推荐搭配」卡的色环与卡名直接画在吸顶的「外观」标题、副标题和「保存设置」按钮之上。

**根因是层叠顺序，不是背景透明**：吸顶横条 `sticky top-0 z-10`，卡片内的色环与文字容器带
`relative z-10`（本意只是浮在纹理背景之上）。两者**同处一个层叠上下文**（`main` 与
`max-w-3xl` 都没创建新的），z-index 相同 ⇒ 由 DOM 顺序裁决，而卡片在文档里更靠后，
于是画在横条之上；横条自己那层不透明 `bg-surface` 完全压不住。

证据取自 `document.elementsFromPoint`：y=75 处卡片内的文字 `span` 排在横条副标题 `p` **之前**；
而横条的 `backgroundColor` 实测 `rgb(244, 241, 238)`（不透明）。

**修复**（`frontend/src/components/settings/SettingsPage.tsx`）：横条 `z-10` → `z-20`，
并在原注释里把契约写死——**吸顶横条必须严格高于内容里的装饰层**（内容侧用 `z-10`）。
卡片那两处 `z-10` 不动：它表达的是「浮在纹理之上」，本身没错，错的是横条与它同层。

**影响范围**：仅设置页吸顶横条一层。`EpubChapterView` 的 sticky 目录头同为无层级的
`sticky top-0`，但那个容器内没有任何带 z-index 的元素，本轮不动（避免扩大回归面）。

**验证**：demo 构建后用 CDP 滚到 520px / 1500px 各截一张，标题、副标题、保存按钮全部干净；
`tsc -b --noEmit` 通过。临时脚本与截图用完即删，未提交。

**顺带**：README 补上**在线演示站**入口（此前 demo 上线但 README 没写，用户看不到）。

---

## 2026-10-21 — 0.5.14 启动即退：`startup()` 内的 import 遮蔽（UnboundLocalError）

**症状**（用户另一台机器，日志来自上一轮刚上线的失败屏）：

```
[后端]   File "main.py", line 267, in startup
[后端] UnboundLocalError: cannot access local variable 'logging' where it is not associated with a value
[后端] ERROR:    Application startup failed. Exiting.
```

后端进程退出码 3，界面停在启动失败屏。**这正是上一轮「日志可见化」要接住的那类故障** ——
先有失败屏与两段日志，才第一次看见它。

**根因**：`startup()` 里有两处 `import logging`（FTS5 全文搜索那次提交 `0ada1ef` 引入，
位于 SearchEngine rebuild 的 `try` / `except` 分支里）。Python 的规则是**函数体内任意位置的
赋值都会让该名在整个函数里成为局部变量** —— `import` 也是赋值。于是 `startup` 前半段
凭据自检分支里的 `logging.getLogger(__name__)` 在它被绑定前读取，抛 `UnboundLocalError`。

**为什么本地复现不出来**：那条分支（`if _secret_problems:`）只在
`config_io.startup_check()` 报出「磁盘配置里有明文凭据」时执行。开发机的配置早已迁移到
DPAPI 密钥库，自检永远返回空 ⇒ 根本走不到那一行。缺陷自 **0.4.12**（凭据自检上线，
`a831c26`）起就潜伏，只要那台机器的配置里出现过明文凭据，启动就必退。

**修复**（`backend/main.py`）：删掉两处 `import logging`（模块顶部第 5 行早已有），
原地留注释写明这个坑。顺带删掉 `startup()` 顶部冗余的 `import os as _os`（模块顶部第 6 行已有）
—— 它是「函数内可随手 import 模块」这一习惯的示范源，而这次事故正是那个习惯的代价。

**验证**：临时脚本把 `startup_check` patch 成报一条问题，用 `TestClient` 触发 lifespan ——
修复前抛 `UnboundLocalError`，修复后两条路径（报问题 / 不报问题）都能正常启动、`/health` 返回 ok。
脚本用完即删，未提交。

**守卫**（`backend/tests/test_closure_shadowing.py` 从「一种形态」扩到「一整类」）：

- 新增 `find_import_shadowing`：函数**自身**作用域内 `import` 绑定的名字，若在同一函数内更早
  被读取 → 违规。与既有的嵌套遮蔽判据**职责不重叠**（有断言钉住：给对方的违规样本，自己必须不响）。
- **扫描范围从 `backend/app/` 扩到 `backend/main.py` + `backend/limo_cli/`** —— 上次「全绿」
  与「看不见出事的地方」是同一件事；新增断言保证 `main.py` 在扫描清单里（否则文件被排除时照样绿）。
- 自证区分力（模式 #38）：两种违规形态（分支早读 + `try` 内 import、`from m import x` 别名遮蔽）
  必被抓到；四种安全写法（先 import 后用、别名、嵌套函数内 import、形参同名）不误报。
  **对照实验**：把 `import logging` 临时加回 `main.py`，守卫立刻变红并指出
  「读@序26 < import@序61」；还原后全绿（138 个文件）。
- 扩范围时抓到一处**误报**并修掉：`limo_cli/output.py::table()` 的 `line()` 里
  `[pad(cells[i], widths[i]) for i in range(...)]` —— 推导式的循环变量在 Python 3 是
  **推导式自己的作用域**，不会让外层同名变量变成局部变量。两个判据都改为跳过它，并补了负例。

**影响范围**：仅启动路径。修复前「配置含明文凭据」的机器 100% 起不来，其余机器行为不变。
0.5.13 安装包同样作废（它含此缺陷），以 **0.5.14** 为准 —— 本条目与上一条（日志回退链）
同属 0.5.14。

---

## 2026-10-21 — 启动失败「仍然没有任何日志」：日志目录回退链 + 失败屏自证 + timeout 不再静默放行

**症状**：上一轮的失败屏上线后，用户在**另一台电脑**上仍然拿不到任何日志；
控制台显示的是：

```
后端启动超时，强制加载
127.0.0.1:7413/api/capabilities:1  Failed to load resource: net::ERR_CONNECTION_REFUSED
```

**两个独立成因**（不是同一件事）：

| # | 成因 | 为什么上一轮没接住 |
|---|---|---|
| 1 | **日志目录写不进去** | 日志只往 `%LOCALAPPDATA%\LiMo\logs` 写。该目录不可写（安全软件拦截 / 磁盘满 / `LOCALAPPDATA` 缺失时 `user_data_dir()` 退到 `"."` 即只读的安装目录）时，**启动器与后端两段日志同时为空** —— 而失败屏渲染出一块空白，看起来像界面坏了，读不出「日志系统自己失败了」这层意思 |
| 2 | **timeout 路径仍然自动放行** | 「失败屏」只在进程已退出时显示；`alive=true`（进程还在、只是没在 45s 内响应）走的是 `console.warn` + `initOnce()` —— 诊断只进了控制台，用户界面上一句原因都没有 |

**修复**：

1. **日志目录回退链**（`lib.rs`）：候选依次为 `%LOCALAPPDATA%\LiMo\logs` → `%TEMP%\LiMo\logs` →
   安装目录旁 `logs\`，取第一个**真正写得进去**的（探针文件验证，`create_dir_all` 成功 ≠ 可写）。
   全部不可写时返回首选路径 —— 此时内容为空，但失败屏上的 `log_dir_writable=false` 就是答案。
   `app_log` / `open_backend_log` / `build_log_tail` / `spawn_backend` / `open_log_dir` 五处统一走它。
   ⚠️ **结果在会话内缓存**（`OnceLock`）：探针要写磁盘，`app_log` 一轮启动十几行，不缓存就是十几次
   写+删；更要紧的是**一致性** —— 启动器日志、后端 stderr、失败屏读回必须是同一个目录。
   诊断字段 `log_dir_writable` 仍现测（那一项要反映此刻状态，不是选目录时的快照）。
2. **失败屏自证**（`startupFailure.ts::logDisplayText`）：日志为空时区分三种成因 ——
   目录写不进去（本身就是故障）／目录可写但两份文件都没生成（DLL、运行库、杀软隔离的典型形态）／
   写了但没内容。旧版启动器不带这些字段（`undefined`）时**走原兜底，不误报**。
3. **timeout 不再静默放行**（`App.tsx`）：`backend-startup-timeout` 改为停在失败屏；
   硬兜底拉到任何诊断都停屏，只有**拉不到**才「强制加载」。放行能力保留为按钮「仍然继续」，
   由用户在**知道发生了什么**之后自己选。`alive=true` 时失败屏额外说明「进程仍在运行，
   可能只是启动慢」。

**顺带清掉两处**：

- `isFatalStartupFailure`（判据 `alive`）**删除** —— 它唯一的用途就是「什么时候自动放行」，
  而判据存在一天，就有人会把它当成权威。删除它的注释写明了这一点。
- `app_log(dir, msg)` 的 `dir` 参数**删除** —— 函数体早就不看它，十二个调用点都传 `exe_dir`，
  读签名会以为日志写在安装目录里。日志往哪写只有 `log_dir()` 一处判据。

**守卫**：
- **Rust 单测**（`lib.rs::tests`，5 条）：`pick_log_dir` 跳过写不进去的候选（用**假候选列表**，
  因为开发机上造不出「LOCALAPPDATA 只读」）、全不可写返回 `None`、`dir_writable` 真写探针并清理、
  「不可写」用**父级是文件**构造（chmod 在管理员身份下会静默变成可写，测了等于没测）、候选链至少两站。
- **前端单测**（`startupFailure.test.ts`）：`logDisplayText` 三个新分支 + 旧版字段缺失的对照实验。

**影响范围**：启动失败路径；正常运行零影响（日志目录首选仍是 `%LOCALAPPDATA%`，回退链只在它写不进去时生效）。

---

## 2026-10-21 — 修复 0.5.12 打包版启动即崩（`RuntimeError: sys.stderr is None`）

**症状**：0.5.12 安装包启动时后端秒退，日志只有一行：

```
File "main.py", line 17, in <module>
RuntimeError: sys.stderr is None
```

**根因**：本轮为「原生崩溃可定位」新加的 `faulthandler.enable()` 在模块顶部（第 17 行）
执行，而 `sys.stderr is None` 的兜底当时留在文件末尾的 `__main__` 段（第 379 行）——
**晚于它**。`faulthandler.enable()` 默认取 `sys.stderr`，打包版（`console=False`）
在桌面壳没有把 stderr 重定向过去时（用户直接双击 exe 验证；或 `open_backend_log()`
建不出文件、Rust 侧退回 `Stdio::null()` 之外的场景）拿到 `None` 就直接抛错。

**为什么源码读起来没问题**：两段各自都是对的，错的是**顺序**——兜底必须在所有
「假设 stderr 存在」的代码之前。这与「判据看的输入不是它以为的那个」是同一类失效：
代码存在、逻辑正确、执行时机不对。

**修复**：`backend/main.py` 顶部把 `sys.stdout` / `sys.stderr` 的 None 兜底提到
`faulthandler.enable()` **之前**，并删掉 `__main__` 段里的重复实现（同一判据不留两处）。

**守卫**（`backend/tests/test_main_entry.py`，2 条）：
1. **源码顺序**：第一处 `sys.stderr = open(` 必须早于第一处 `faulthandler.enable()` 代码行
   （排除注释——写这条时它先被自己的注释骗过一次）；
2. **运行时**：子进程里伪造 `sys.frozen = True` + `stdout/stderr = None` 后 `import main`
   必须成功退出——顺序对了但换个写法绕过去的，由它兜住。

**影响范围**：仅打包版（开发版有控制台，stderr 不为 None）。0.5.12 安装包作废，以 0.5.13 为准。

---

## 2026-10-21 — 后端启动失败可见化（用户界面直接给出原因）

**起因**：用户在另一台电脑上启动 0.5.11 打包版，前端满屏 `ERR_CONNECTION_REFUSED`，
界面只有一句「后端启动超时，强制加载」。用户的原话把它说透了：

> 如果用户一启动后端就挂，没法根据日志排查原因，因为日志是后端写的

**先纠正一半事实**：日志**一直在写** —— `lib.rs::spawn_backend` 早就把后端 stdout/stderr
重定向到 `%LOCALAPPDATA%\LiMo\logs\limo_backend_stderr.log`。真正断掉的是后半句：
**没有任何路径把它带到用户面前**。「有日志」与「够得到日志」是两件事。

**四个断点**：

| 断点 | 改造前 | 改造后 |
|---|---|---|
| `spawn_backend` 返回 Err | 只写 `limo_startup.log`，前端完全不知道 | 写入 managed state + emit `backend-startup-failed` |
| 进程起来后立刻退出 | traceback 躺在文件里，界面干等 45s | 探测循环每 500ms `try_wait`，**退出即报**（不再干等） |
| 45s 没就绪 | emit 一句 `后端 45s 内未就绪`（字符串） | emit `StartupFailure`：退出码 / stderr 尾部 / 日志与程序路径 / 端口占用推断 |
| 事件早于 WebView 加载完成 | emit 被静默丢弃 | state 兜底 + 命令 `get_backend_startup_failure` 主动拉取 |

**判据（刻意改的语义）**：`alive` 决定要不要放行 —— 进程**还活着**只是慢 → 保留强制加载
（至少能进界面看缓存）；进程**已退出** → 停在失败屏，不放行。放行只会把「一个原因明确的
失败」变成「一屏无意义的连接错误」。

**新界面**（`components/ui/BackendStartupFailure.tsx`）：一句话结论 + 退出码/路径 + stderr
尾部（8 KiB，可滚动）+ 三个动作（重试启动 / 打开日志文件夹 / 仍然继续）。⚠️ stderr **为空**
时显式写「后端没有留下任何错误输出 —— 常见于缺少 VC++ 运行库、被安全软件拦截，或安装目录
不完整」：一行都不打印正是这三类故障的典型形态，留白会让用户以为界面坏了。

**落点**：`frontend/src-tauri/src/lib.rs`（`StartupFailure` / `BackendStartupState` /
`read_tail` / `resolve_backend_exe` 抽取 / 探测循环 / 两个新命令）、
`lib/startupFailure.ts`（纯函数判据）、`components/ui/BackendStartupFailure.tsx`、
`App.tsx`（事件监听 + 渲染分支 + 硬兜底拉取）、`test/startupFailure.test.ts`（8 条，含
「进程存活不判致命」的对照实验）。

### 二轮：日志不丢 + 排障闭环（同日）

用户追问「后端一启动就挂如何记录日志以及让用户排障」，与「用户可能选非初始安装位置」
一并处理。第一轮解决了「把日志带到用户面前」，这一轮解决**日志本身靠不靠得住**。

**修掉的四个缺陷**（都在日志/诊断这条链上，全部静默）：

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| L1 | `spawn_backend` 用 `File::create` 打开后端日志 | **每次 spawn 截断**。用户看到失败屏、点「重试启动」→ 刚读到的崩溃日志被清空 → 重试往往又失败 → **屏幕上只剩空白，第一次的证据在用户眼前被抹掉** | `open_backend_log()`：追加 + 时间分隔行 + 4 MiB 轮转一份 `.1.log` |
| L2 | 失败屏只读 `limo_backend_stderr.log` | 启动器那份（在哪个目录、拉起哪个 exe、为什么拉不起来）**看不到**；而 DLL 缺失型故障后端**一行都不打印**，只读后端日志恰好会在最需要证据时空白 | `build_log_tail()` 拼两段，行首加 `[启动器]` / `[后端]` 前缀；启动器那段用 `SESSION_MARKER` 只取**本次**启动（文件是跨会话 append 的） |
| L3 | `spawn_backend` 返回的 Err 字符串被丢弃 | 「exe 在但起不来」的**真实原因**（拒绝访问 os error 5 / 被占用 / 杀软拦截）只在 `limo_startup.log` 里，界面上是一句光秃秃的「没能启动后端进程」 | `build_startup_failure` 加 `spawn_error` 参数，summary 带上原文 |
| L4 | 「找不到 exe」与「exe 在但起不来」共用一句文案 | 处置完全不同（重装 vs 改环境），用户只能猜 | summary 按 `exe_path.exists()` 分两支：「安装目录里找不到 LiMoBackend.exe —— 安装可能不完整，或被安全软件隔离了」 |

**让用户能排障的三处**：

- **NTSTATUS 退出码翻译**（`exitCodeHint`）：`-1073741515` 对用户毫无意义，它对应
  `0xC0000135`（缺 DLL / 没装 VC++ 运行库）—— 而这正是「一行日志都没有」的主力成因。
  六条常见码（0xC0000135 / 7B / 0142 / 0005 / 0409 / 0417）翻成人话，**普通退出码
  不硬套解读**（有对照实验：`1` 必须返回空串）。
- **复制诊断信息**按钮：结论 + 阶段 + 路径 + 退出码解读 + 安装路径警告 + 两段日志，
  一整块进剪贴板。用户不必在 `<pre>` 里跨行拖选（长日志很难拖准，而漏掉的恰是开头那几行）。
- **安装路径警告进失败屏**：复用 `installWarningText` 的判据（**不新建第二份文案**），
  输入是 `exe_path` + Rust 新回的 `install_dir_writable`（含空格 / 非 ASCII 前端当场算得出来，
  同一个事实不回两遍）。⚠️ **不看顶栏横幅的「已关闭」记忆** —— 那里关掉的是「每次开机
  提醒一次」，这里是**解释当前这次失败**的证据，关过横幅不该让失败屏失去线索。

**顺带**：`analyze_install_path` 的探针文件实现抽成 `probe_dir_writable`（`.dd_probe` →
`.limo_write_probe`），与失败屏共用一份；重试又失败时主动拉新诊断回填
（`onFailureUpdate`），否则屏幕上是上一次的退出码，看起来像「重试没生效」。

**字段改名**：`StartupFailure.stderr_tail` → `log_tail`（内容已不只是 stderr）。
`stderrDisplayText` → `logDisplayText`。两处都是内部字段，无外部消费方。

**为什么这些能解释「另一台电脑」的故障**：如果后端在解释器起来之前就被加载器判死
（缺运行库 / 路径含中文 / 被杀软拦截），**它的 stderr 一定是空的** —— 第一轮那条兜底
文案只提了「缺 VC++ 运行库 / 杀软 / 安装不完整」，漏了**中文路径**，而中文用户名会让
**默认**安装路径（`%LOCALAPPDATA%\Programs\LiMo`）就含非 ASCII。现在它被点名，且
失败屏会直接把路径打出来。

**落点**：`lib.rs`（`open_backend_log` / `fmt_ts` / `prefix_lines` / `read_session_log` /
`build_log_tail` / `probe_dir_writable` / `SESSION_MARKER` / `BACKEND_LOG_ROTATE_BYTES`）、
`lib/startupFailure.ts`（`logDisplayText` / `exitCodeHint` / `installPathWarning` /
`diagnosticsText`）、`components/ui/BackendStartupFailure.tsx`（警告条 + 复制按钮 +
`onFailureUpdate`）、`test/startupFailure.test.ts`（**8 → 22 条**）。

---

## 2026-10-20 — 代码审查（安全 / 前端 / 后端三路）后的整改

**起因**：用户「然后你再大致的去进行代码质量和漏洞审查」，三路审查列出 15 条问题，
本轮全部修掉。**没有一条是「新功能」** —— 这批的共同形态是「机制看着在、实际不生效」
或「同一份判据住了两处」。

### 安全（4 条 + 4 条纵深）

| # | 问题 | 修法 |
|---|---|---|
| S1 | **导出 HTML 直通原始正文**：`GET /export/{id}?format=html` 把模型/用户内容原样渲染，产物里若出现 `<script>` 或 `onerror=`，打开导出文件即执行。Tauri 的 PDF 路径走同一个 URL，且它在**同源**（`127.0.0.1:7413`）上下文里导航 —— 一段脚本就能 `fetch('/api/cli/resources')` 读出整库、或调写接口 | 加 **CSP + 一次性 nonce**（[export.py](../backend/app/routers/export.py)::`_csp_meta`）：`script-src 'nonce-<随机>'`，我们自己的脚本全带 nonce，正文里的 `<script>`（内联/外链/带 `onerror`）一律不执行。⚠️ **HTML 内容一个字都不改**（保「预览所见 = 导出所得」）；`style-src 'unsafe-inline'` 必需（KaTeX / Pygments / cards.css 全是内联）；每次导出新 nonce |
| S2 | **IPv6 完全绕过 SSRF 黑名单**：旧实现只列 IPv4 网段、DNS 也只查 `AF_INET` | [url_security.py](../backend/app/utils/url_security.py) 重写：`ipaddress.ip_address` 统一判 v4/v6（`is_private` 覆盖 ULA/link-local）+ `::ffff:a.b.c.d` **按内嵌的 v4 判**（否则一条 v6 写法绕过整张 v4 表）+ DNS 双栈解析 |
| S3 | **重定向不校验**：`requests.get(url, allow_redirects=True)` 让公网 URL 一跳 302 到 `127.0.0.1` | `safe_get` / `safe_get_httpx` **逐跳自己跟**（`allow_redirects=False` + 每跳 `validate_url`），次数上限 `MAX_REDIRECTS` |
| S4 | **有副作用的 GET 无来源校验**：CORS 只拦跨站**读响应**，`<img src="…/api/generate/{id}/stream">` 照样发出 —— 用户的钱和产物都花了，页面什么都看不到 | [core/origin.py](../backend/app/core/origin.py)：`X-LiMo-Client` 头（主判据，no-cors 请求带不了自定义头）+ Origin/Referer 白名单兜底；白名单正则与 `main.py` 的 CORS **同一份**。前端所有请求经 `CLIENT_HEADERS` 带上 |

纵深四条：`_val` 容器截断（见下）｜`card_vars` 拦 `expression(` / `url(`（字符集全合法、连起来是调用，见下）｜IPv6 日志语义改准（不再把「是 IPv6」记成「不是 IP」）｜未预期异常回显截到 300 字符。

### 前端（4 条）

- **F1 两个浮层可同时打开**：`@ 引用` 与 `回答依据` 都 `absolute bottom-full` 锚在同一块输入区，两个独立 boolean ⇒ 精确重叠，后渲染那张盖住前一张（症状是「点 @ 没反应」）。改为**一个 `'ref' | 'mode' | null` 选择器字段**，「同一时刻至多一张」由类型本身保证。⚠️ 改完漏删旧 `useState` 撞名，`tsc` 当场报 `Identifier 'modeOpen' has already been declared`。
- **F2 `cycle` 项数越界时全叠在环顶**：注释写着「内容一字不少」，但 7 个环节挤在同一个角度档上，**视觉上是一坨**。加 `:has()` 闸门：项数不在 3–6 → 退回**普通有序列表**（与 `timeline` 同出口，「内容永远不该因为渲染不认识而消失」）。
- **F3 `chat-prefs` 承诺跨标签页同步却无 `storage` 监听**：`useSyncExternalStore` 的快照以「磁盘原始串」为键，另一标签页写入后本页缓存永远命中。补 `storage` 事件订阅。
- **F4a 「回答依据」浮层无 Escape**：隔壁 `@` 浮层有（input 自带 `onKeyDown`），两隔壁行为不一致。Esc 收口到 `document` 监听那一处。**F4b `cardToggleWiring` 守卫射程只覆盖 `PreviewArea.tsx` 一个文件**：改开关未必发生在那里 —— 判据改成「整个 `components/` 里骨架写入点只允许设置页一处」。⚠️ 路径基准必须是 `resolve(root, …)`，写成 `resolve('settings', …)` 会按 **CWD** 解析、把设置页自己当成违规者。

### 后端（7 条）

- **B1 `_ANSWER_MODES` 与 prompt 层 `scopes` 是同一枚举的两份**：`routers/chat.py` 导入即体检（`_check_scope_keys()` 在模块级直接调用），`test_chat_answer_mode.py` 补「键集相等」+ monkeypatch 让体检真炸的对照实验。
- **B2 `CHART_ALTERNATIVE_BLOCK_TYPES` 名单在三处手抄**（prompt 文案 / `_chart_missing_warning` 判据 / 模块注释）：收口到 [core/artifact_blocks.py](../backend/app/core/artifact_blocks.py)，prompt 文案由它 `join` 拼出。⚠️ 用 **tuple 不用 frozenset** —— prompt 文案需要稳定顺序。失效形态：加第六种时漏改任一处 = prompt 教了而完成期 warning 不认，**用户照做反被报「图表未生成」**。
- **B3** `artifact_blocks.py` 注释「那 10 种」实际 12 种 → 改写为集合名（数量词是第二真相源，`"17"`/`"18"` 两轮各加一种都没人回头改它）。
- **B4** `_MARKER_COLORS`（Word 导出配色表）无键集守卫 → 加双向断言（缺项 / 多项都红）。
- **B5** `_LIST_BODY`（测试里的块示例表）缺 `sequence` / `quadrant`。
- **B6** `_chart_missing_warning` 注释把「双产物 / 单产物分支」误称「非流式 / 流式」。
- **B7** `agent/prompts/__init__.py` docstring 的「六层叠加」与「对话侧两种 type」都已过时（现在是三选一 / 二选一 + 七种 type）。

**另有两条判据收口**：

- **`logging._val` 的容器不截断**：`log.info("Tool", args={...})` 能把整段工具入参打进日志（1000 条环形缓冲 + 滚动文件），而 `GET /api/logs` 会把它们交给前端 LogPanel —— 用户贴日志排查时就顺带外泄了会话内容。字符串一直截断、容器不截断，**不是有意的例外，是那条判据当时只写了 `str` 分支**。现在字符串 80 / 容器 300（容器要给括号引号键名留位置）。⚠️ 顺带把 `bool` 分支挪到数字分支**之前**（`bool` 是 `int` 子类，写反时靠 `str(True)` 恰好也能对，所以一直没人发现）。
- **`card_vars` 允许 `expression(...)` / `url(...)`**：每个字符都在 `_CARD_VAR_VALUE_RE` 白名单里，**光看字符集判不出来**。值里含这两个调用直接丢弃该项（逐项丢弃、同串其余项照常 —— 判据同 `card_style`）。

### 测试与文档

- **`normalize_export_nonce`**（[conftest.py](../backend/tests/conftest.py)）：CSP nonce 每次导出都新，四条「逐字节相等」断言因此全红。归一**两种形态**——CSP 指令里的 `'nonce-…'` 与标签属性 `nonce="…"`。⚠️ 只归一前者是实测踩到的坑：diff 会吐 148 万字符「相同」+ 一行差异，因为差异点正好落在 `<script nonce="…">` 之后。归一后 meta 标签仍在串里，CSP 缺失照样被抓。
- **`PROMPT_VERSION` `"18"` → `"19"`**：B2 让 prompt 文案里手写的「这**五种**」变成由常量拼出的「这**几种**」。输出与 `"18"` 只差这一个词，判据/示例/触发条件全不变 —— bump 走的是「发给模型的文本变了就 bump」这条纪律，不是行为变了。
- 新增 `test_logging_val.py`；`test_export_cards.py` 补「字符全合法、连起来是调用」一组。

**影响范围**：导出 HTML 多一个 `<meta>` 与 nonce 属性（内容不变）；所有 HTTP 请求多一个 `X-LiMo-Client` 头；SSRF/重定向校验覆盖 IPv6 与跳转；其余为判据收口与注释修正，**无 UI 变化、无存储变更**。

---

## 2026-10-20 — 移除预览工具栏的「章节卡片」开关

**诉求**：用户「章节卡片那个按钮移除」。

**为什么移除**：骨架轴只在 `summary` / `polished` 的非 Excel 路径生效（唯一分派点在
[PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)），而按钮摆在所有
标准 Markdown tab 的工具栏上 —— 在转录 / 图片 / 思维导图 / Excel 数据摘要上切了**什么都不
发生**，是典型的死旋钮（模式 #53）。功能没删，只是把入口收回到它真正起作用的地方。

**改动**：

- 删按钮 + `setCard` 订阅 + 不再使用的 `LayoutGrid` import；**骨架分派点保留**
  （`skeleton === 'card' ? <CardSections …`）。
- 开关仍住 **设置 → 阅读与产物 → 产物骨架**。
- 连带删除 `TabDef.isStandardMarkdown` 字段 —— 它当时的**唯一消费者**就是那个按钮的门控
  表达式；只留字段不留读者会让下一个人以为它仍在生效。
- `cardToggleWiring.test.ts` 改写为反向断言：按钮不得回来 + 分派点必须仍在 +
  唯一写入口在设置页。

---

## 2026-10-20 — 设置页「外观」重排：三段分组 + 一键主题包提到最前

**诉求**：用户「外观设置的各种设置的排布你可以优化一下」。

**改了什么**（按渲染顺序）：

- 三个**视觉分组标题**（不新增导航层级）：**主题** / **阅读与产物** / **界面**。
- 「推荐搭配」（20 套一键主题包）**从最底部提到最前** —— 那是「换个好看的」最常走的
  路径，此前要滚过五张卡才能碰到。20 套由 2 列大卡改成 **4 列紧凑小卡**
  （圆点 + 名称 + 字体·纹理一行），每组一行，总高约减半。
- 拆掉「主色调卡里塞三个轴」：此前 `主色调 / 背景纹理 / 阅读字体` 三件不相干的事挤在
  同一张卡里、只靠内部小标题分隔（纹理看上去像主色调的子项）。现在**一轴一卡**。
- 「阅读字体」与「阅读版式」**合并为一张「阅读」卡**：一个管「用什么字」、一个管「怎么排」，
  同一件事的两半分居两处。卡级标题 15px + 两个 13px 小节（与「产物版式」卡同构）。
- 「内容块装饰」**并入「产物版式」卡**（卡名由「产物骨架」改准后，它已不只讲骨架）。
- 「侧栏布局」移到最末、归入「界面」。

**刻意没做**：

- 明暗（`data-theme`）**不加进这个 tab** —— 它已由顶栏 `ThemeToggle` 承担。本轮是排布
  优化不是加功能，多一个入口只会多一处要同步的状态。
- 只动 DOM 顺序与卡片分组，**零设置项改名、零存储 key 变更、零默认值变化** ⇒
  存量用户的取值与观感完全不变。

**影响范围**：仅 `frontend/src/components/settings/SettingsPage.tsx` 的外观分支。

---

## 2026-10-20 — 对话侧「回答依据」三档：只用知识库 / 允许发散 / 自由对话

**诉求**：用户要一个全局开关决定 AI 是「严格遵循知识库」还是「发散」，随后补充
「顺便问问的那些似乎需要极致的发散」。前两档都仍要求「先看库」，而「顺口问个事」
那条路上那恰恰是噪音 —— 于是**三档而不是布尔**。

| 档 | 用户看到 | 库里没有时怎么办 |
|---|---|---|
| `strict`（默认） | 只用知识库 | 说没找到，不用常识补 |
| `open` | 允许发散 | 可以补，但文档优先、标明哪句是补充 |
| `free` | 自由对话 | 按自己的知识答，不要求先查库 |

**判据**：

- **是枚举不是滑条**：三档并非「程度递增的同一根轴」（`open` = 文档优先 + 补充，
  `free` = 根本不围绕文档）。做成滑条会让「选到中间」这件事有意义，而它没有。
- **默认 `strict`**：改造前 `rules.md` 就是「只用知识库、无来源不说话」。与 `webSearch`
  默认开同一条判据 ——「默认值必须等于改造前的行为」，改默认就是替所有用户改回答纪律。
- **拆三份文件而不是占位符**：同联网层（`web_tools.md` / `no_web.md`）的先例 ——
  占位符要第二套标记解析器，失败形态是**用户看见 `{{ANSWER_SCOPE}}` 原文**。
- **哪些进共用层**：问「这个取值变了，模型该做的事会不会变」。「库里没有时怎么办」三档
  各不相同 ⇒ 拆；「禁止编造」三档都成立（**发散 ≠ 编造**）⇒ 留在 `rules.md`。
  ⚠️ 顺带把 `rules.md` 第 1 条的标题从「文档存在时」改成「**引用文档时**」：前者读起来是
  「先查库」，与 `free` 档正面冲突。源码守卫反向钉住它不得回来。

**实现**：

- 后端：三个新文件 + `PromptLoader.scopes` 字典 + `build(..., answer_mode="strict")`
  （未知档抛 `ValueError`，不许绕过路由的调用方拿到一份「悄悄按严格跑」的 prompt）。
  `routers/chat.py` 加 `_answer_mode()`：**入口严**，非法值显式报 `CHAT_BAD_ANSWER_MODE`
  （不静默退化 —— 用户以为开了发散而产物上看不出区别，零报错）；流式与非流式两个端点都接。
- 前端：`lib/chat-prefs.ts` 加 `answerMode`（`CHAT_DEFAULTS` 三项；`loadChatPrefs` **读回宽**，
  未知值退默认 —— 读的是磁盘上可能过期的值，与入口的严格刻意不同）；`api/chat.ts`
  只在**偏离默认**时带 `answer_mode`（同 `web_search` 的纪律：少一个参数是可被断言的形式）；
  `ChatInput` 加下拉选择器。
- **UI 用下拉不用三态按钮**：点一下循环一档，用户点两下就不知道自己在哪档了，且误点一次
  要多点两次才回来。下拉把三句话摆出来，描述直接写「库里没有时怎么办」——那才是三档
  **唯一**的区别，也是用户真正在意的那个差别（「严格 vs 发散」这个说法本身太抽象）。
  图标随档变化（`BookOpen` / `Sparkles` / `Wand2`）：光靠高亮分不出当前是哪一档。
- 顺带：`@ 引用`与回答依据两个浮层的「点外部关闭」**共用一个 document 监听**
  （各挂一个就是「什么时候该关」的两份实现）。

**设置页入口（同日补）**：用户问「要不要按入口区分」，选定**两处入口共享一份状态**
（设置页与输入框显示同一个值，改任一处另一处跟着变），随后要求设置页也有这三项设置。

- 设置 → 通用 新增「AI 对话（改动立即生效）」卡片：联网 / 追问建议 / 回答依据三项，
  与输入框那三个按钮**是同一份 `mbw_chatPrefs`**。
- ⚠️ **两处各自 `useState(loadChatPrefs())` 会静默分叉**：设置页改完之后，**已经挂载**的
  输入框仍显示旧档（它不会重新挂载），而发送那一刻 `dispatchSend` 读的又是新值 ——
  按钮说「只用知识库」、实际按「自由对话」跑，界面上完全看不出来。故 `lib/chat-prefs.ts`
  加模块内订阅 + `useChatPrefs()`（`useSyncExternalStore`），**不加 zustand slice**
  （判据：slice 是为了跨域共享 action，不为共享一个 localStorage 值）。
  ⚠️ `getSnapshot` 必须返回**稳定引用**（每次 `JSON.parse` 出新对象 = 无限重渲染），
  故缓存**以磁盘原始串为键**（哨兵 `undefined` 区分「没有存储」的 `null`）——
  一次性缓存会读不到 `localStorage.clear()` / 手改 / 另一标签页的变化。
- 三档**文案**收进 `ANSWER_MODE_OPTIONS`（`lib/chat-prefs.ts`）：第二个入口出现时，
  文案留在组件里就只能复制一份 —— 改了文案而另一处没改，两处说法打架。图标留在
  `ChatInput`（那是那一处的视觉细节，不是三档的定义）。

**顺带**：设置页标题行改成**吸顶**（`sticky top-0` + 不透明 `bg-surface` + 下边框）。
用户反馈「要保存得先滚回顶部」。⚠️ 刻意**不用 `backdrop-blur`** —— `backdrop-filter`
会创建包含块，把 `fixed` 后代锁进这条横条（模式 #67，文档库面板那边已踩过同款）。

**测试**：`test_chat_answer_mode.py`（19 条：三档两两不等有区分力 / 共用层不被污染 /
入口校验 6 个非法值 / build 未知档响亮失败 / 两个端点的接线守卫）+
`chatAnswerMode.test.tsx`（20 条：偏好默认与合并式保存 / 读回宽 / 参数只在偏离默认时发 /
store 发送时带当前档 / 下拉交互与外部点击关闭 / **两处入口共享一份**（设置页改→已挂载的
输入框跟着变、反向同步、快照引用稳定）+ 两条**源码守卫**（设置页必须 `useChatPrefs()`
且不得 `useState(loadChatPrefs`；`ChatInput` 不得内联三档文案））。

**影响范围**：只有对话侧。产物侧（生成 / 合成）的严格约束体系独立，未改动；
不 bump `PROMPT_VERSION`（对话 prompt 不产生产物，同联网层与 `formats.md`）。
存量用户不受影响（`strict` 就是他们现在的行为，且默认值不落盘）。

---

## 2026-10-20 — 图表收缩到三种 + 语义块补 `sequence` / `quadrant`：列表能说清的关系不再画图

**背景**：一条真实的失败文案 ——「📊 图表生成失败（布局失败：状态层数过深（8 层 > 7），
请拆分成多张图）」。追下去发现**不是模型的错，也不是图太高**：`layouts.py` 里写死了两条
层数闸门（`state` 的 `nrow > 7`、`flowchart` 的 `nlayer > 5`），它们在 `engine.expand()`
的「三轮压行距 → 按自然高度定稿 → 等比缩放（`_MIN_SCALE = 0.65`）」**之前**就抛错。
实测同一份 8 层纯矩形在 round2（行距 28）下总高只有 564px ≤ 640，`scale = 1.0` ——
本该一次画成，却被判「层数过深」。这是同一件事（装不装得下）的第二套判据，且**严格更严**。

**判据（本轮定形）**：卡片还是图，问的是「**文本结构（列表 / 表格）能否保真表达这个
关系**」—— 不是复杂度，也不是垂直 / 水平。同一层不会出现第二个节点、也没有回边的，
列表就够，画图是白付一整条渲染链（语义 JSON → 几何 → PIL 出 PNG）。

**实现**：

- **图表引导七种 → 三种**（`get_chart_format`）：只留 `grid`（依赖 / 调用网络）、
  `flowchart`（带分支与汇合的流程）、`state`（带判定、可含回边的状态迁移）。撤掉的四种
  全部有出口：`layers` / `tree` → **嵌套列表**（结构即语义，连块都不必标）、
  `sequence` → 新 `sequence` 块、`quadrant` → 新 `quadrant` 块。⚠️ **算子在 `_LAYOUTS`
  里一个都没删**（删的是引导不是能力，与 `hchain` / `timeline` 的先例同款）。
- **删两条层数闸门**：高度统一由缩放链裁决，真装不下（低于 0.65 下限）才报「图过高，
  请拆分成多张图」。自然上限约 13 层；测试把 6 / 9 层改成正向断言（能画、`scale = 1.0`），
  另补 18 层负向断言保住「真的缩不动才报」这条判据 —— 只删不补等于把防护一起删了。
- **新语义块 `sequence`**（消息流）：块内**有序列表**，每项 `**发送方 → 接收方**：内容`。
  ⚠️ **不是生命线时序图** —— 纯 CSS 无法从消息文本推导参与者列位，方向由**文字**承担，
  这是它不必结构分派的原因。
- **新语义块 `quadrant`**（二维评估）：块内**恰好 4 项**有序列表，顺序固定
  「左上 / 右上 / 左下 / 右下」（与 `chart_layout` 的 `quadrant` 算子同序），CSS
  `grid-template-columns: repeat(2, minmax(0, 1fr))` 排 2×2，**不画坐标轴**（象限名自己
  承担语义）。项数不对时自然排成两列多行，**内容一字不少**。
- 两侧同步：`ARTIFACT_BLOCK_TYPES` + 共享 fixture `limo-blocks.json`（`knownTypes` /
  `artifactTypes` + 两条解析用例）、`blocks.css`（含各自的 `decor=off` 归零规则）、
  Word 的 `_MARKER_COLORS` 两项。
- **完成期 warning 判据放宽**：勾了图表却只产出 `steps` / `timeline` / `cycle` /
  `sequence` / `quadrant`，是**照做了引导**，不该报「图表未生成」。两处逐字重复的判定
  抽成 `_chart_missing_warning()`，判据变为「既无图表围栏，也无任何替代块」；模型明说
  「内容不适合图表化」时文案也据实改写。

**补记（同日）· 窄图裁剪后只剩半边留白**：`expand()` 的宽度裁剪此前只按内容**右边界**
定宽（`ceil(maxx) + MARGIN`），而算子是按 640 宽排布的 —— 画布收窄了、坐标却没动，
于是「右边的一大片空白」被原样换成了「左边的一大片空白」（实测 flowchart：画布 412px、
左侧空 232px，观感比不裁更差）。修法有两半：宽度改按**内容跨度**算
（`maxx − minx + 2×MARGIN`），再用新增的 `_shift_space_x()` 把四类元素
（nodes / labels / shapes / edges）整体平移回 `MARGIN`；画布被 `_MIN_CANVAS_W` 或
标题宽度撑宽时，`dx` 让内容在最终画布里居中。⚠️ **连线必须跟着平移**——
`_shift_path_x()` 拆段重建 M/H/V 路径（与既有 `_shift_path_y` 对称），只挪节点而
不平移边的话连线会与节点错位。实测：state（带回边沟槽）左右各 16px、timeline
172px（16/16）、铺满的 hchain 仍 640（`dx = 0`，零漂移）。`_trim_width` 保留为
`_trim_canvas()` 的薄委托（公开契约不变）。测试新增「左右留白对称 + 连线起点/终点
仍落在节点上」，并做了负向对照（把位移强制归零 → `assert_layout` 当场报「节点越界」）。

**验证**：`test_block_prompt.py` + `test_prompt.py` 23 passed（含「集合驱动」的
「规范列出了每一个类型」「六种被撤布局的带引号形态不出现在任何 source_type 里」）；
`test_chart_layout.py` / `test_chart_renderer.py` 全 PASS；`test_export_blocks.py`
37 passed；前端 `markdownMarkers` / `markdownSections` / `chatMarkersWiring` 152 passed，
`artifactCardShape` / `markerPrefs` / `markerContent` 50 passed。

**影响范围**：`generation/prompt.py`、`chart_layout/layouts.py`、`routers/generate.py`、
`core/artifact_blocks.py`、`styles/blocks.css`、`docx_export.py`、fixture 与相关测试；
文档 `CLAUDE.md` / `README.md` / `design/cards.md` / `STATUS.md`。
`PROMPT_VERSION` `"17"` → `"18"`（改了发给模型的文本）。

---

## 2026-10-20 — 产物语义标记新增第十种 `cycle`：环形因果循环改由卡片承担

**背景**：用户在一批真实产物里发现图表「环形图」反复绘制失败。查下来的根因不是模型
不听话，而是**图表管道根本没有环形这个能力**：`chart_layout` 的 `_LAYOUTS` 十个算子
（hchain / layers / timeline / grid / branch / tree / flowchart / state / sequence /
quadrant）里没有环形的那个；模型唯一能表达「环」的 `flowchart` **显式拒绝任何环**；
`state` 虽然是唯一允许环的算子，但有 `nrow > 7` 的硬上限。于是模型只能硬套 `state`
去画一张环形图，实测失败产物里的报错集中在「state 层数过深」2 条、「flowchart 层数
过深」3 条、「flowchart 存在环」5 条。

**方向（用户定调）**：不是去给图表补一个环形算子，而是反过来看 —— **模型想要的
是一个「环节首尾相接」的结构，这件事根本不需要坐标**。用户原话：「用滑行图可能是因为
模型想用环形图来表示，我们用卡片做类似的结构，模型只负责填空不就行了吗」。这与图表
协议「模型只输出语义、几何由后端算」是同一个思路，只是粒度更粗：卡片固定形状，
模型只写环节名。

**实现**：

- **白名单**：`ARTIFACT_BLOCK_TYPES`（后端 `core/artifact_blocks.py` / 前端
  `lib/markdown-scan.ts`）加 `cycle`，两侧集合 + 共享 fixture
  `test/fixtures/limo-blocks.json` 的 `knownTypes` / `artifactTypes` 与新增解析用例同步。
  它属**七个语义强调块**一族（标「这段文字是什么性质」），与 `steps` / `timeline`
  同为「用列表代替画图」——但**语义不同不能互替**：`steps` 是一条线走完就结束，
  `timeline` 是按时间排列，`cycle` 是**真的闭合**（最后一个环节让第一个再次发生）。
- **prompt**：`get_markdown_block_format()` 新增 `cycle` 条目 —— 明确写「用有序列表
  （`1. ` 开头）、按因果顺序、每项一个短标签不超过 8 字、3–6 项」，并明说**环的形状
  由渲染自动排，不用写箭头、序号或任何排版说明**；同时给出**负向判据**：「如果一个
  环节不会回来，那是 `steps` 或 `timeline` 不是 `cycle`；超过 6 个环节时拆成两个环，
  或改用 `timeline`」。另补一句与图表的分界：「`steps` / `timeline` / `cycle` 用列表
  表达就够了，不要为它们画图；反过来有分支、分上下层级或需要二维排布的关系不要塞进
  这三种。」
- **`get_chart_format()` 同批改**（两处必须同批，否则同篇里一个说「画图」一个说
  「别画」）：把「几个环节首尾相接的因果循环」加入「不要画图」一侧，语义块列表由
  `steps` / `timeline` 扩成三种；围栏的职责收窄为「分支 / 二维排布 / 多参与者时序 /
  带判定的状态迁移」，`state` 的示例标题由「状态与回环」改称「状态机（带判定的迁移，
  可含回环）」—— 回环仍允许，但它不是「我有个循环」的首选表达。
- **CSS（`blocks.css`）**：容器 340×340、`--cyc-r: 120px`，节点 92×40 绝对居中，
  按 `transform: translate(-50%,-50%) rotate(θ) translateY(-R) rotate(-θ)` 摆位
  （后一个反向旋转让文字保持水平）；环中心用 `ol::before` 画 2px 虚线圆、`::after`
  放一个「↻」。
  - **角度档用 `li:first-child:nth-last-child(N)`**（3 项 0/120/240、4 项 0/90/180/270、
    5 项 0/72/144/216/288、6 项 0/60/120/180/240/300），**刻意不用 `:has()`** ——
    这份 CSS 同时被**导出 HTML** 在任意浏览器里消费，`:has()` 的浏览器门槛比预览区高。
  - 项数不在 3–6 时没有角度档，节点全部叠在环顶 —— **内容一字不少**，只是不排成环
    （与「零残留降级」同一个取向：宁可难看，不可丢字）。
  - `overflow-x: auto` **必配 `flex-shrink: 0`**（2026-10-18 表格块踩过的同一个坑：
    `.preview-content` 是 flex column，`.md-block` 是 flex item，有 overflow 后自动
    最小尺寸按 0 算会被压扁）。
  - **关装饰归零**：`[data-marker-decor="off"]` 下退回普通有序列表（`position: static`、
    无边框底色、伪元素 `content: none`），与其余九种同一条契约 —— 只收装饰、绝不吞内容。

**验证**：用无头 Chrome 在**真实 `blocks.css`** 下渲染 n=3/4/5/6 四组，逐项断言
`overlap=0`（节点间无重叠）、`outOfBounds=0`（无节点出界）、`textOverflow=0`（含 6 字
标签不折行），角度档取值与预期一致；关装饰态断言 `list-style=decimal`、`position=static`、
`transform=none`、无边框无底色、环的伪元素 `content=none`。

**影响范围**：后端（`artifact_blocks.py` / `generation/prompt.py`）、前端
（`markdown-scan.ts` / `styles/blocks.css` / fixture）、文档（`CLAUDE.md` /
`STATUS.md` / `design/cards.md`）。`PROMPT_VERSION` `"16"` → `"17"`（改了发给模型的
文本）。**不是替代某一张图**：环形图此前从来没成功过，`cycle` 是让这件事第一次有
可用的表达方式。

**导出侧**：HTML/PDF 由 `blocks.css` 自动继承（零代码改动）；Word 走既有 `.md-block`
分支（Python 对 type 无感知），**只需在 `_MARKER_COLORS` 补一项** —— 不配会回退成
`key-takeaway` 的配色，Word 里两种块长得一模一样（`docx_export.py` 的注释早就写着
「未知 type 回退是关键兜底」，但**兜底不是配色方案**）。`.md` / 纯文本按既有规则剥标记。
顺带修两处测试老化：`test_html_其余类型…` 的「八种」手写名单改为从
`ARTIFACT_BLOCK_TYPES` 派生（加类型时自动覆盖）；`_LIST_BODY` 此前是**死常量**（定义了
没有消费点）→ 补一条 `test_html_列表型块内的列表结构原样穿过导出链`，把「块内是
`<ul>` 还是 `<ol>`」这件事真正钉住（纯 CSS 方案的前提，压扁后文字一字不少、包装断言
照样绿）。

---

## 2026-10-18 — 修复：表格卡片被压成「很小的一行」，关掉装饰后整块消失

> **两个现象，一个根因。** 第二个形态是用户当天追加报告的：「之前被压缩的那个表格，
> 关掉装饰之后不见了」。

**现象**：笔记里被 `limo-block` `table` 围栏包住的表格，预览时高度只剩几条行高，
剩余部分要鼠标在卡片内部滚动才能看到（用户原话：「显示成了很小的一行，必须得翻动
用鼠标滚动内部卡片，不会全部显示」）。内容没丢、零报错。

**根因**：2026-10-17 给 `.md-block[data-block-type="table"]` 加的 `overflow-x: auto`
把 `overflow-y` 一并算成了 `auto`，而**预览区的内容容器是 `flex flex-col
overflow-y-scroll`**（`PreviewArea` 的 `.preview-content`），表格块是它的 flex item：

1. flex item 默认 `flex-shrink: 1`；
2. 没有 overflow 的 item 靠 `min-height: auto`（= 内容高度）挡住收缩；
3. `overflow` 非 visible 的 item 自动最小尺寸按 **0** 算 ⇒ 可被压到任意小，
   溢出内容进自己的滚动条。

实测（无头 Chrome，600px 高容器 + 20 行表格）：块高 **246px** / 内容 **834px**；
加 `flex-shrink: 0` 后 **834px**，纵向滚动条消失。

**修法**：`blocks.css` 那条规则里补 `flex-shrink: 0`，作为 `overflow-x` 的必要配套。
只对 `table` 块成立 —— `code` / `image` 块没有容器级 overflow，最小高度仍是内容
高度；导出侧是普通块流，该属性无副作用。

**第二形态：关装饰 → 块高恰好 0**（2026-10-18 追加定位）：`[data-marker-decor="off"]`
的归零规则把 `padding` 设为 0、`border` 设为 none。未修复时，被 flex 压缩的块原本还剩
`padding: 12px 16px` + 1px 边撑出的 **26px** 可视高度（即「很小的一行」）；关掉装饰后这
26px 也归零 ⇒ **块高 0，整块从页面上消失**，而内容完整躺在自己的 `scrollHeight` 里。
同一容器（600px / 24 行正文 + 5 行表格）下的四组对照实测：

| CSS | 装饰 | flex-shrink | 块高 | 表格内容高 |
|---|---|---|---|---|
| 修复前 | 开 | 1 | **26px** | 242px |
| 修复前 | 关 | 1 | **0px** | 242px |
| 修复后 | 开 | 0 | 268px | 242px |
| 修复后 | 关 | 0 | 242px | 242px |

⚠️ 所以「关装饰吞卡片」**不是装饰开关的问题**，缺的就是 `flex-shrink: 0` 这一个属性
（跟「关掉装饰后内容降级」的既定语义无关）。用户当时跑的是不含修复的构建（`dist/` 里
那条规则只有 `overflow-x: auto`）。

**守卫**：`artifactCardShape.test.ts` 加三条，均做过对照实验（改坏 → 红 → 还原）：
① table 专项 `flex-shrink: 0`；② **通用版** —— 任何 `.md-block` 本体上的非 visible
`overflow` 都必须配 `flex-shrink: 0`（防止将来给别的块加 overflow 时漏配套）；
③ 关装饰规则里**不得**出现 `display: none` / `visibility: hidden` / `opacity: 0` /
`height: 0`（开关语义是「隐藏装饰」，不是「隐藏内容」）。

## 2026-10-18 — 智能分类：细分程度交给用户选，默认只允许一级子文件夹

上一批把「已有更深结构不往回退」修好之后，暴露出一个更基本的问题：**细分这件事
用户全程没有发言权**——新建到几层由代码写死 3 层，模型想分多深就分多深。用户拍板
三档由用户在配置阶段选：

| 档位 | 语义 |
|---|---|
| 1 `不建子文件夹` | 只归类到主文件夹，名字里不出现 `/` |
| 2 `允许一级` | 主文件夹 + 一级子文件夹 —— **默认** |
| 3 `允许两级` | 主文件夹 + 两级子文件夹（绝对上限） |

⚠️ **默认档从 3 层改为 2 层，这是行为变化，零回归不成立**：同一批文档、同一句建议，
升级后会比升级前少建一层。用户明确要求了这个默认值。

**新增的东西**：
- `classifier.CLASSIFY_MAX_DEPTH_DEFAULT = 2` / `CLASSIFY_MAX_DEPTH_LIMIT =
  folder_tree.MAX_PATH_SEGMENTS`（3，不另写一个字面量——它就是 `ensure-path` 的拒绝线）
- `clamp_max_depth()`：非法值静默夹紧到 `[1, 3]`——错的是**量**不是性质，判据同
  `notesChars`（对比 `folder_mode` / `tag_mode` 错的是性质 → 显式报 `CLASSIFY_BAD_OPTION`）
- `_depth_rule(max_depth)`：prompt 规则第 4 条的**前半句**按档位插值（三档措辞各不相同；
  1 档明确要求「名字里不要用 `/`」）
- `max_depth` 贯穿 `classify_documents` → `build_classify_prompt` → `normalize_suggestions`
  → `_plan_new_paths` / `_normalize_one` → `resolve_new_target`
- 路由 `maxDepth` 字段 + `limits.maxDepth` / `limits.maxDepthLimit` 回显（回显**本次生效值**，
  与 `maxNewFolders` 同款判据——回显常量会让用户选了两级而摘要写着「允许一级」）
- 前端配置阶段「细分程度」三档段控（`DEPTH_LEVEL_OPTIONS` / `MAX_DEPTH_DEFAULT = 2`）

**两处刻意的语义分离**：
- **`maxDepth` 只约束新建**：`resolve_new_target` 用 `limit = max(max_depth, existed)` ——
  选「不建子夹」也不会把文档从已有的深层结构里赶回浅层（上一批那件事的延续）。warning
  文案用 `limit` 而非 `max_depth`，否则「已有恰好 N 层」会被说成「已截断」而实际是归入。
- **不建夹时传中性值**：`reuse_only` / `tags_only` 下 `start()` 传 `maxDepth: 1`（「不新建」），
  与 `maxNewFolders: 0` 同款——否则响应里回显一个用户根本没选过的层数。

**影响范围**：后端分类管线 + 路由；前端分类对话框。**零迁移**：该值不落库、不进产物，
历史分类结果不受影响。`ensure-path` 的 3 段上限不变。

测试：`test_classify_paths.py` 原「超 3 段截断」一条拆成四条（默认档 2 层 / 3 档 / 1 档 /
已有更深不受档位影响）+ prompt 三档措辞；前端补默认档、三档可选、请求体、零额度禁用四条。

## 2026-10-18 — 智能分类层数口径：已有更深结构时不往回退

用户拍板：智能分类新建最多到**主文件夹 + 两级子夹**（3 层）；用户自己已经建了更深的
文件夹时，**优先复用已有的那一层**。细分只发生在用户主动点「智能分类」时，别的流程
不会自己去建夹——这一条现状本就如此，无需改动。

改动只有一处逻辑：[`resolve_new_target`](backend/app/generation/classifier.py) 的截断点
从固定的 `MAX_PATH_SEGMENTS` 改为 `max(MAX_PATH_SEGMENTS, 最深已存在前缀长度)`。

此前模型建议 `A/B/C/D/E` 而库里已有 `A/B/C/D` 时会被截到 `A/B/C` —— 文档落进**更浅**
的层，而结果看起来只是一次再普通不过的归属，用户无从知道模型指的是 D 那一类。现在
归入 `A/B/C/D`（`reuse`）。

warning 文案随之按「**截断后落在哪**」二分：命中现有夹 → 「已归入已有的『…』」；否则
才是「已截断为『…』」。判据不能写成 `existed > MAX_PATH_SEGMENTS`——已有恰好 3 层时
截断点也是已有路径，那同样是一次归入。

prompt 规则第 4 条补一句「清单里已有更深的文件夹且合适时直接复用它的 id，不要在它下面
再新建」（「最多 3 层」不变）。

**影响范围**：后端分类管线；`ensure-path` 的 3 段上限**不变**（`new` 的路径长度仍然最多
3 段，因为更长时要么被截到第 3 层、要么命中已有夹），前端零改动。测试新增 2 条
（已有 3 层→第 4 层归入、已有 4 层→第 5 层归入且不倒回）。

## 2026-10-18 — 划选与文件夹多选：八项修补

上一批（划选三补）交付后自审出的缺陷与手感问题，逐条修完。按危害排序。

### 1（P0）区间的两端按**渲染顺序**算，不是数据顺序

`visibleIds` 是**筛选结果的数据顺序**，而列表在默认排列（按文件夹）下被 `groupDocs`
按文件夹树前序重排、未分类固定排最后 —— 两者必然不同。Ctrl 划选与 `Shift` 区间都拿
`visibleIds` 当区间基准，于是「从上往下拖第 2 行到第 4 行」选中的是另外三篇。

折叠起来的组更糟：那些行的 id 还在数据里，元素却根本不在 DOM 里 —— 划选会把**看不见的
文档**一起划进来，批量删除时一并删掉，而界面上没有任何异常。

修法是新增 `domOrder(fallback)`：区间两端一律从
`[data-doc-list] [data-doc-id]` 的实际渲染顺序取（找不到容器才回退数据顺序）。
折叠组由此天然被排除（它们不在 DOM 里），不需要再维护第二套「哪些组收着」的判断。

⚠️ **`Ctrl+A` 刻意不采它**：全选 = 全部匹配项（跨分批），那是独立语义，不是区间。

### 2（P1）清空文件夹选择的判据是 `arrangeBy`，不是 `grouped`

原判据 `if (grouped) return` 漏掉「按类型 / 按时间」排列：那时 `grouped` 同样非 null，
但那些组的 `editable` 是 false、复选框根本不渲染 —— 选择残留下来，批量条一直挂着一个
对**界面上不可见**的文件夹生效的删除按钮。改为 `arrangeBy === 'folder'`（它同时覆盖
「简略模式写死按文件夹」这一档）。

### 3 批量移动 / 删除**部分失败**时保留失败项

此前无论成败都 `clearSelection()`，失败的那个夹重新变回「在树上找不到」，用户得再找一遍。
现在收集 `failed`，清空后把它放回 `selectedFolderIds` 供直接重试。**不展开它的后代** ——
那会制造用户没选过的选项。

### 4 `onScroll` 每来一个事件就排一个 rAF

滚动一次会派发几十个事件 ⇒ 同帧内几十个 `requestAnimationFrame` 排队重算。
改为 `scrollFrame` 局部变量合并（有帧在排就跳过），`cleanup` 里 `cancelAnimationFrame`。

### 5 两个删除确认框认 Esc

它们是**模态遮罩**，此前只有「点取消 / 点确认」两条出路，键盘用户进来就没有出口。
文档删除框与文件夹删除框各加一个 `document` 级 `keydown`。

### 6 文件夹复选框列宽与文档行对齐

`GroupHeader` 的 `w-5`（20px）比 `DocRow` 的 `w-6`（24px）窄一档 —— 两者在同一个缩进流里
上下相邻，勾左移 4px，整棵树的竖线看起来是歪的。改成同值，注释写明这是成对常量。

### 7 「移到当前父级」不再发空请求

把文件夹拖回它已经在的父级，PATCH 是一次静默 no-op，而 toast 照样说「已移动 N 个文件夹」，
树上一个像素都没变 —— 用户会以为没生效，再点一次。

判据与后端同源（`parentId` 缺省视为根 `""`）：先摘掉已经在目标父级下的夹；
**全部都是 no-op 时连请求都不发**，只 toast 一句「已经在根级/X 里，无需移动」。
部分 no-op 时正常发请求，成功文案附带「（N 个已在该位置）」。

### 8 划选结束后的 click 抑制只绑**起点行**

原实现是一个纯时间窗（300ms 内任何 click 都吞掉）：划完选紧接着点开一篇文档看内容，
那一下毫无反应。浏览器真正会多派发的那一次 click 来自**起点行**（mouseup 后），
故抑制条件改成 `{ id: startId, until }` —— 起点行那一下仍然被吞（否则会被 toggle 掉），
其余行照常打开。

### 测试

[docSelection.test.tsx](../frontend/src/test/docSelection.test.tsx) 24 → 34 条。
新增用例都做了**回退对照实验**并确认当场变红（随后 `cp` 还原）：

| 回退成什么 | 哪条红 |
|---|---|
| 区间两端改回 `orderRef.current`（数据顺序） | 划选 / Shift / 折叠组 三条 |
| 清空判据改回 `grouped` | 「切成按类型清空」 |
| 抑制只看时间不看 id | 「紧接着点另一行仍能打开」 |
| 删掉「失败项放回选择集」 | 「部分失败保留」 |
| 去掉 no-op 预筛 | 「不发请求」 |
| 删掉任一 Esc effect | 各自对应的确认框用例 |

⚠️ 缺陷 1 的 fixture 特意让 `d1`/`d4` 归入文件夹、其余未分类（**旧 fixture 全部无
`folderId` ⇒ 全落未分类 ⇒ 分组不重排 ⇒ 数据顺序恰好等于 DOM 顺序**，正是这个缺陷当时
没被测出来的原因）。另加一条**前提断言**钉住「DOM 顺序确实被打乱了」，否则 `groupDocs`
哪天不再重排，后面三条会静默变成假绿。

---

## 2026-10-18 — 划选三补：可取消 / 滚动续选 / 文件夹多选

### 1. 划选方向由**起点行**决定（可取消）

此前 Ctrl 划选无条件加选 —— 手滑划多了只能一行行点掉。现在：

| 起点行按下前的状态 | 这一拖的作用 |
|---|---|
| 未选中 | 区间内全部**选中**（`base ∪ 区间`） |
| 已选中 | 区间内全部**取消**（`base \ 区间`） |

这是「Ctrl + 点击 = toggle」的直接延伸，不是另一套规则；用户不必先「取消选择」再重划。
**刻意不做逐行取反**：在一段既有已选又有未选的行上会得到「选中 / 取消交错」的结果，
而手势意图是「刷一段」。区间每次都从按下时的 `base` 快照重算 ⇒ 拖出去再拖回来结果一致（幂等）。

### 2. 划选途中滚轮翻页，选区续到指针下方那一行

`scroll` 不冒泡、且真正滚动的是列表容器 —— 监听走 `document` + `capture`，等一帧再按
指针最后位置取行（`scroll` 事件可能先于布局完成）。mousemove 与滚动后重算**共用同一个
`applyTo`**：两份各写一遍的话，「滚动后要不要重新算」迟早变成两套判据。

### 3. 文件夹多选（独立选择集）

- **入口**：分组头左侧的复选框列（点击 = toggle，`Shift` = 区间）。**不参与 Ctrl 划选** ——
  分组头与文档行在同一列流里交错，混进同一条区间后「第 3 行到第 9 行」取决于中间夹了几个分组头。
- `selectedFolderIds` 与 `selectedIds` **是两个集合**：合并成一个会让每个消费方都要按 id
  类型过滤，漏一处就是把 `fd_xxx` 当文档 id 发去 `/api/transcripts/batch`。
  批量条因此分开计数：「已选 3 项 · 2 个文件夹」。
- 只做了**两个动作**：**移动**（复用 `FolderPicker`，禁用选中夹自身与全部后代）与**删除**
  （二次确认）。打标签 / 合成 / 导出 / 智能分类对「文件夹」没有语义，不做。
- 删除是**整棵子树**级联：选中 `{A, A/B}` 时先做去后代化、只往后端发 A（否则第二次 DELETE 404）；
  本地回落范围**只认后端返回的 `deletedFolderIds`** —— 前端自己算一份 `descendantIds` 就是
  第二个真相来源，后端改删除语义时会静默停在旧规则上。
- 平铺（不分组）时文件夹不可见 ⇒ 自动清掉残留的文件夹选择；筛选变更时两个集合**一起清**
  （`noSelection()` 同源），否则批量条上会挂着一个对看不见的对象生效的删除按钮。

### 测试

[docSelection.test.tsx](../frontend/src/test/docSelection.test.tsx) 16 → 24 条。新增的判据都做过
**回退对照实验**：`deselect: false` / 去掉 scroll 监听 / `hasSelection` 只算文档集 /
`noSelection()` 只清文档集 —— 各自对应的用例当场变红，还原后全绿。

`beforeEach` 补 `selectedFolderIds: new Set()`：两个集合分开存放，只清一个会让上一条用例
选中的文件夹漏到下一条，症状是「某条用例莫名红了、单独跑又是绿的」。

---

## 2026-10-18 — 文档库分批加载「翻不到底」+ 行首勾选框弱化

### 两条根因（都是静默失效：界面无报错，只是列表再也不动）

| 现象 | 根因 | 修法 |
|---|---|---|
| 切过一次密度（详细 ↔ 简略）后，列表永远停在第一屏的 60 行，滚到底什么都不发生 | 两种密度是两条不同的 JSX 分支，切换时 `DocList` 整体重挂载 —— 哨兵与滚动容器都换成了新 DOM。而 observer 的 effect 只依赖 `[hasMore, limit]`，这两个值没变 ⇒ **不重跑** ⇒ 旧 observer 继续观察一个已从文档移除的元素，交叉事件永不触发 | 哨兵 / 容器改用 **callback ref + state** 持有元素本身，把「元素是谁」放进 effect 依赖（依赖里列 `density` 只是记住今天有这个分支，下次加分支照样漏） |
| 多个文件夹展开时，滚到底后画面毫无变化、要反复滚 N 次才到底 | 哨兵挂在列表**最末尾**（前面组吃满预算，后面是一串空分组头）。新行是插在**前面某个组内部**的（视口上方），配合浏览器的滚动锚定，眼前画面一个字不变 | 哨兵改挂在**截断点**（第一个还有行没画出来的组）之后 —— 新行就长在用户正盯着的位置下方。全组折叠时兜底回末尾，保证 `hasMore` 为真时一定有观察目标 |

### 交互

- **行首勾选框**：未选中时随行 hover / 行内有焦点才显示，选中后常显（VS Code 行首列行为）。
  整列热区与 `Ctrl` 划选不变；图标仍留在布局里（只改透明度），出现 / 消失不引起标题横跳。

### 测试

- 新增 [docListPaging.test.tsx](../frontend/src/test/docListPaging.test.tsx) 两条回归
  （切换密度后重新 observe 当前元素 / 分组模式下哨兵落在截断组内）。两条都做过**回退对照实验**：
  临时改回旧实现时两题皆红，还原后皆绿。

---

## 2026-10-18 — 审计修复：四条 P2 + 十条 P3（工作树自查）

### 起因

对当日工作树（文件夹嵌套 / 索引 sidecar / 标签建议三批改动）做一次代码质量与漏洞
自查，**写入语义从「新建文件」变成「就地覆盖 / 重建恢复」放大了旧假设的危害**：
几处「看起来只是少写一个字段」的地方，失效形态都是**静默**的（产物正常、界面无报错）。

### P2（用户可见或数据受损）

| 项 | 问题 | 修法 |
|---|---|---|
| **标题不进 sidecar** | `DOC_FIELDS` 不含 `title`，重建只能拿**有损**的目录名兜底（`_compute_dir_name` 截 30 字、剥 `<>:"/\\|?*`）→ 40 字标题或带斜杠的标题被静默改写，而用户从没编辑过它 | `title` 进 `DOC_FIELDS`（16 → 17）。老库缺该字段自然回落到目录名，**渐进兼容、无需迁移** |
| **删除确认计数口径** | `GroupHeader` 收的是直接子级数，而删除是**整棵子树**一起删（toast 用 `deletedFolders - 1`）→ 确认框少报 | `childCount` → **`descendantCount`**（`descendantIds().size`），与后端同源；`docQuery.test.ts` 断言 tech = 3 |
| **切文档竞态** | `TagSuggestions.accept` 在 `await` 期间用户切了文档 → 上一篇的标签记进新文档的本地记账，新文档同一词被 `existing` 过滤掉（只是「少一条建议」） | 回填前校验 `currentDocId` 仍是发起时那一篇 |
| **唯一后缀命中静默新建** | 模型漏写前缀、后缀在库里唯一 → 仍在根级新建，用户看到的是一条再普通不过的「新建文件夹」 | **行为不变**（自动复用是猜），但补一条 warning 点名已存在的路径 |

### P3（静默失效 / 死代码 / 重复实现）

- **`ensure_folder_path` 的 `segments` 双语义**：`existing` 分支是 `[整串名]`（不是逐层），
  `parent_id` 非空时靠它跳过短路、在指定父级下建出**单层**的「前端/后端」。docstring 写死，
  并补测试（含负向面：拆成两层时树看起来完全正常）。
- **`folderPathLabel` 死导出**：全仓无生产调用点（`useDocQuery` 只是再导出一次），删除；
  测试改为直接断言 `tree.pathOf`。
- **同一批 folders 建三次树**：`filterDocs` / `countByFolder` / `groupDocs` 各自
  `buildFolderTree(folders)`。三者加可选的 `tree` 参数，`DocLibrary` 把已有的那份传下去
  （`filters.tsx` 传自己的 memo）。新增「传 / 不传逐项等价」测试。
- **`_folder_section` 路径截断**：`sanitize_reference_name` 默认 80 字，3 层路径会被切在
  半路、末段丢失。展示路径改传 512。
- **标签清洗顺序**：`clean_tag_names` 必须先**截长**再去重。反过来的失效是两条只在前 30 字
  之外不同的长标签各过各的、再被截成同一串（库里出现两条一模一样的标签）。补测试 + 负向对照。
- **`limo folders` 的 O(n²)**：`ids.index(i)` 在循环里 → 建 `index_of` 字典；顺带让重复 id
  不再丢行。
- **`tagSuggest` 弱断言**：`not.toThrow()` 换成具体次数，并加「`XC++` 不算命中」证明词边界
  判据没被转义逻辑吃掉。
- **`GroupHeader` 依赖过宽**：`onActionConsumed` 每次父级渲染都是新闭包 → effect 在用户正在
  输入框里改名时重跑、`setDraft(group.label)` 覆盖已打的字。依赖只留 `[actionFor, group.ref]`。
- **划选边缘手势**：① Esc 取消后仍按着鼠标，松手那一下 click 会走 `toggle`（刚恢复的行又被勾上）
  → 设 `suppressUntilRef`；② 拖到一半切窗口收不到 `mouseup`，`mousemove` 与
  `body.userSelect='none'` 残留 → 监听 `window` 的 `blur` 按松手处理。
- **筛选 chip 撑宽**：三层路径把「文件夹范围」撑得很宽 → 路径 `truncate max-w-48`，
  完整路径仍在 `title` 里。

### 测试

- 新增：标题跨重建保真（含「目录名确实有损」的前提断言）/ 唯一后缀 warning /
  `ensure_path + parent` 的单层语义 / 截长后去重 / 预建树与现建树等价。
- 三条负向对照实测（移出 `title`、删 warning、反转清洗顺序 → 对应测试各自变红）。
- 门禁：`bash scripts/check-all` 六项全绿。

---

## 2026-10-18 — 文档库：文件夹嵌套 + 操作逻辑改造 + 智能分类长出层级

### 起因

两件事在长期使用里同时暴露：

1. **文件夹是单层平铺的**。文档涨到几百篇后，「技术」「技术/前端」「技术/后端」只能挤成
   三个同级名字，靠命名前缀假装层级；筛选也只能精确命中一个夹。而**文件夹在库里本来就
   只是 `index.json` 里的一个数组**（纯逻辑分类、永不映射磁盘目录），加一层父子关系
   不需要动任何存储布局。
2. **四处交互磨手**：移动文档只能从平铺列表里挑、文件夹没有就地创建入口、右键菜单九项
   连成一长条、勾选得瞄准那个 14px 的小勾（用户原话：「勾选的小勾那个操作逻辑有点反人类，
   可以考虑做成像 VS Code 那样点击那一列文件栏的那个地方能选中，然后按住 Ctrl 滑动可以
   快速选择」）。

**已拍板的语义**（实施时未改动）：点一个文件夹 = 看它**及其所有子文件夹**；
删一个文件夹 = **整棵子树一起删**，各层文档全部回落未分类、**绝不删文档**。

### 数据模型：零迁移

- `FolderEntry` 增加 `parentId: string`（`""` = 根级），不新增 `path` / `depth` / `order`，全部派生。
- **不 bump `INDEX_VERSION`** —— bump 会触发 `_rebuild_index`，而那个函数当时就会清空 folders 表。
  （该有损行为已于**同日晚些时候**修复：真相搬进 `meta.json` / `folders.json`，见下一条记录。）
- 读：一律 `f.get("parentId", "")`；写：`create_folder` 总写该字段，`rename_folder` 对旧条目
  `setdefault`（触碰即补）。**不在 `_load_index` 里重写 index 补字段**（那会把「读坏了还能回滚」变成默认）。
- 条目形状有三个落点（后端 `make_folder_entry` / 前端 `store/types.ts` / `api/core.ts`），
  后端抽 `folder_tree.make_folder_entry` 是为了让「构造」也只此一处。

### 新增 `folder_tree.py` / `folderTree.ts`（两端同构）

纯函数层：`normalize_parents` / `children_map` / `preorder` / `descendant_ids` /
`ancestor_ids` / `paths`（`父 › 子`）/ `total_counts` / `folder_scope` / `parse_path` / `path_key`。
五条不变量写在两份文件头（逐字同源）：

1. 缺 `parentId` / 悬空 id / 指向自己 → 视为根；
2. 环：在 **folders 数组下标最小**的节点处断开（确定性，不是随机）；
3. 兄弟顺序 = 数组顺序，前序保证父在子前；
4. `descendantIds` 不含自身，`totalCount` = 直接数 + 各后代求和；
5. 未分类不是实体、永不在树里、固定最后。

两份实现靠**共享 fixture** `frontend/src/test/fixtures/folder-tree.json` 钉住（后端测试直接读这个路径）。
⚠️ **前端不做路径解析**：`parse_path` 只活在后端一处（见下）。

### 后端

| 位置 | 改动 |
|---|---|
| `store.create_folder` | `parent_id` 非空须存在（`FOLDER_PARENT_NOT_FOUND` 404）；写入 `parentId` |
| `store.rename_folder` | 支持改 `parentId`；须存在、非自身、非自身后代（`FOLDER_CYCLE` 400）；**校验与写入同一把锁**（防 TOCTOU） |
| `store.delete_folder` | 收集 `{id} ∪ 后代`，**一次锁一次写盘**：命中文档 `folderId=""` → 删集合内全部 folder 行；返回 `{moved, deletedFolders, deletedFolderIds}` |
| `store.list_folders` | `count` **保持直接计数**（兼容 CLI/旧消费方），新增 `totalCount`（含子树）；返回规范化后的 `parentId`（原始脏值留在 index 里便于抢救） |
| `store.list_transcripts` | 新增内部键 `folderIds: set`；⚠️ **与 `folderId` 互斥**：`folderIds` 存在时忽略 `folderId`（入口注释写死 + 测试钉住，否则行为取决于哪条 `if` 在前） |
| `store.ensure_folder_path` | `parse_path` → 逐段在锁内 ensure（同父同级同名复用），**最后一次写盘**。幂等；任何一段非法 → `AppError` 且**一个字节都不写**。⚠️ **不复用 `create_folder`**（它每次写盘，逐段调既慢又让「全有或全无」失效） |
| `POST /api/folders/ensure-path` | 新增。**智能分类建夹的唯一写入口** —— 前端逐层调 `POST /folders` 是 N 次独立事务，任一层失败时前面几层已落盘 |
| `query.list_resources(include_descendants=)` | `folder_id == ""` **永远精确未分类**（不展开）；`True and 非空` → `folderIds = folder_scope(id)`；默认 `False` ⇒ 旧行为逐字节不变 |
| `query.stats()` | 未分类判据改成「空串**或悬空 id**」，与 `list_transcripts` 的归化同源。⚠️ **这是用户可见的数字变化**：悬空 `folderId` 的文档从未分类里「回来」了（此前两边都不算），见下方「已知偏差」|
| `GET /api/cli/resources` | 加 `include_descendants`；`limo list --recursive` 透传；`limo folders` 按层级缩进、列用 `totalCount` |

### 前端

- **树形渲染**（`DocList`）：折叠父级 → 后代 header 与 row **都不进 DOM**、也不消耗分批预算
  （`collapsed`/`hidden` 两个集合 + 前序保证父必先出现）；缩进 `min(depth, 6) * 16px`（简略）/
  `* 12px`（详细），完整路径放 `title`。26px 行高与分组头同高的成对常量**未动**。
- **自动展开祖先**：`activeFolderId` 变化时把自身与全部祖先移出 `collapsedGroups` —— 否则
  「筛到子文件夹而父级收着」就是空白列表。
- **删除回落只认后端返回的 `deletedFolderIds`**：不再用前端 `descendantIds` 自己算一份
  （那会让「哪些文档要回落」出现第二个真相来源，后端改语义时前端静默停在旧规则上）；
  `descendantIds` 只保留纯视觉用途（picker 禁用自身与后代）。
- **`FolderPicker`（新组件，三处共用）**：右键「移动到文件夹…」/ 批量条移动 / 右键文件夹
  「移动到…」。前序树形 + 本地折叠箭头 + `totalCount` 右列 + `excludeIds` 禁用；
  首行对文档是「未分类（移出文件夹）」、对文件夹是「根级」。⚠️ **不做成 `ContextMenu.submenu`**：
  子菜单 hover 展开、鼠标一动就关，而这里有折叠/输入框/长列表滚动三种交互都会把鼠标带出去。
- **右键菜单按用途分四段**（打开 / 生成 / 整理 / 输出 / 危险），文件夹另有四项；
  **批量条分三段**（产出 / 整理 / 危险）。
- **分组头就地新建子夹**：新建后把新建父级**及其祖先**移出 `collapsedGroups`
  （否则新夹藏在收起的父级里，看起来像没建成）。
- **VS Code 式勾选**：复选框热区从「图标大小」扩成**整列**（简略 `w-6 h-6.5` / 详细 `w-6 h-6`，
  图标仍 14/18px）；按住 `Ctrl`/`Cmd` 在行上按下并跨行拖动 = **整段加选**（`base ∪ 区间`，
  不是替换）；mouseup 后 300ms 内的 click 被吞掉（否则起点行刚划进选中集又被 toggle 掉）；
  `Esc` 恢复按下前的集合；编辑框内 Ctrl+按下仍走「选中文字」。⚠️ 普通拖动（无修饰键）仍是
  原生 DnD，划选与它互不干扰。
- **`filters.tsx`**：chip 用 `totalByFolder` 计数；非根文件夹显示路径。
- **`useDocQuery`**：`filterDocs(docs, q, folders = [])` 非空 `folderId` → `{id} ∪ 后代`，
  **`folders` 默认 `[]` 时退化为原严格等值**（既有纯函数测试零改动）；`countByFolder` 返回
  `{byFolder, totalByFolder, uncategorized}`；`groupDocs` 输出 `depth` / `parentKey` / `childCount`。

### 智能分类：让结果能长出层级

**已拍板**：允许模型建议嵌套路径；同名按**路径精确匹配**，歧义**明示**而不是静默取第一个。

- **路径的表达**：`newFolderName` 仍是**一个字符串**，模型输出 schema 不变，层级用 `/`
  表达（`技术/前端`）。**不新增 `parentId`/`parentPath` 结构化字段**（加字段放大编造面）。
- **解析优先级**（`parse_path`，唯一实现）：**整串先逐字匹配某个现有文件夹名**（保护历史里
  本来就叫「前端/后端」的夹）→ 匹配不到才按 `/` 切分、逐段 trim。`kind ∈ {existing, ambiguous, path, reject}`。
- **`path_key`** = strip + 空白折叠 + casefold，**不含结构替换、不含截断**。⚠️ **不用
  `sanitize_reference_name`**：它替换 `【】`、截 80 字，拿来做匹配会让这批「当年随手起的名字」
  永远对不上；后者继续只用于 prompt 展示。
- **prompt 清单**改为 `id=… 路径=技术 › 前端（含子文件夹共 N 篇）` —— 裸名在嵌套后无法区分
  （「前端」可能活在多个父级下）；规模用 `totalCount` 而非 `count`（只有 2 篇直接文档、下挂
  40 篇的三层夹按直接数看会被误判成冷门）。规则段补「用 `/` 分层、最多 3 层、已有层级不重复创建」。
- **六种情形**：单段唯一 → reuse；单段多个同名 → none + warning；整条路径已存在 → reuse；
  父存在末段不存在 / 中间层缺失 → new 带整条路径（前端调 ensure-path）；**省略前缀但后缀命中
  多个分支** → none + warning（否则会在根级造出第三个「长得完全正常」的结构）。
- **额度按「实际新建的文件夹行数」**（`_plan_new_paths`）：`技术/前端` 在「技术」已存在时算 1、
  两层都不存在算 2。⚠️ **同一条路径多篇只付一次**（`taken` 持久集合）；超限时**整条路径**进
  rejected，`candidate` 保留完整路径（对话框预填）。
- **超过 3 段 → 截断 + warning**（文档仍被分类，只是层级变浅，比整篇不归入有用）。
- **口袋夹按完整路径分组**：`技术/杂项` 与 `生活/杂项` 是两个独立口袋，各自判 1/3，互不连坐；
  **嵌套口袋整条路径不占额度**（含它要新建的父层）。
- **前端建夹改走 `ensure-path`**：收集本批**去重后的原串**（不解析、不再做本地同名预查），
  一次调用建完整条路径。⚠️ 前端刻意不实现路径解析 —— 自己拆 `/` 会同时犯两个错：建夹不再是
  「全有或全无」，且历史里本名就含 `/` 的夹会被拆开（后端的「整串优先」保护前端无从得知）。
- **复审下拉显示路径**（`技术 › 前端`，复用 `buildFolderTree.pathOf`），否则两个同名「前端」
  在界面上无法区分；新建输入框 placeholder 改「新文件夹名，可用 / 分层」。
- `POST /api/classify/preview` 的 `folders` 回显补 `parentId` / `totalCount`（画路径所必需）。

### 测试

- 新增 `test_folder_tree.py`（含 `parse_path` 的镜像 fixture ——「整串优先命中现有夹名」**依赖
  运行时 folders**，只写「输入串 → 期望切分」钉不住这条历史数据保护）。
- 新增 `test_classify_paths.py`（14 条）：prompt 路径与子树规模 / 精确复用 / 单段唯一复用 /
  同名歧义 + warning / 后缀歧义不造第三条分支 / 非法路径显式 / 3 段截断 / **额度按行** /
  同一路径只付一次 / 整条路径拒绝 / 口袋夹按路径分组 / 嵌套口袋不占额度。
  ⚠️ 关键判据都做了**对照实验**（去掉「口袋不占额度」→ 1 条红；口袋按末段名分组 → 2 条红）。
- 前端：`folderTree.test.ts`（镜像 fixture）、`docQuery.test.ts` / `docSelection.test.tsx` /
  `docLibrary.test.tsx` 扩、`classifyDialog.test.tsx` 扩（含「嵌套路径**原串**交给端点、
  前端不拆 `/`」与「复用下拉显示路径」两条，后者做了对照）。

### 已知偏差（刻意，不是遗漏）

| 项 | 说明 |
|---|---|
| **未分类计数会变大** | `stats()` 修正后，`folderId` 悬空的文档从未分类里回来了（此前 `stats` 只判空串、`list_folders.count` 也不含它们，两边都漏）。**这是修正不是回归**；验收按「Σ 各夹直接 `count` + 未分类 = 文档总数」对账（用 `totalCount` 求和会重复计数） |
| ~~`_rebuild_index` 仍会丢整个文件夹树~~ | **当日晚些时候已修**（真相搬进 `meta.json` / `folders.json`），见下一条记录 |
| 父子徽标「重复计数」 | 刻意：每个头回答「这个子树多少篇」，tooltip 标「含子文件夹」 |
| 深嵌套挤压标题 | 视觉夹紧 6 层，完整路径进 `title` |
| 拖到折叠子树内 | 需先展开父级（不做自动悬停展开） |
| 文件夹排序 / 拖拽调序 | 不做（顺序 = 创建顺序，不引入 `order`） |
| 触屏划选 | 不做（复选框仍是唯一触屏入口） |

---

## 2026-10-18 — 标签建议：从库里已有的标签中挑出正文真提到过的

### 起因

产物生成完之后，打标签是纯手工的：翻回文档库、选中、点批量条、手打。而**用户自己攒的标签词表
才是这篇文档最可能的归属**——正文里提到了 React，库里三十篇都打了「React」，这件事机器一眼就能看见。

原始设想是让模型在生成正文时顺手把标签也判了。否掉的理由不是钱：输出几十 token 确实便宜，
真正的代价是**注意力从正文上被分走**，而这件事在产物上完全看不出来——没有任何对照组能证明
「正文质量没下降」。同一个坑追问侧踩过一次（思考模式会把输出预算烧在推理链上，正文 0 字符）。

于是定为**只做第一层：本地词表命中**。零 LLM、零网络、零磁盘。

### 做法

| 层 | 内容 |
|---|---|
| 判据 | `lib/tag-suggest.ts::suggestTags` —— 纯函数，可单测 |
| UI | `components/preview/TagSuggestions.tsx`，挂在产物自证条**同一行**后面 |
| 采纳 | `POST /api/transcripts/batch` `{action:'tag', tagMode:'add'}` |
| 落盘 | **无** |

四条匹配规则，每条都对应一种「不做就全是噪音」的失败：

1. **ASCII 标签走词边界**：`AI` 不能命中 `MAIL` / `said`，`React` 不能命中 `Reaction`。
   库里最常见的标签恰好是英文缩写，漏了这条整排建议都不可信。
2. **中文走子串**：中文没有词边界，套 `\b` 会让「前端」这类正常标签永远匹配不上。
   ⚠️ 两支判据不同源是**刻意的**，不是漏改一处。
3. **标题命中优先于正文命中**：缓解「正文顺带提了一句 React」。
4. **去冗余**：`前端` 与 `前端框架` 同时命中、且前者的每次出现都在后者内部（出现次数相同）
   ⇒ 丢掉短的。否则一排 chip 里一半是彼此的前缀。

### 三条边界（都是刻意的）

- **不落盘**：建议是**可重算的派生视图**。正文一改（编辑就地覆盖当前版）、词表一变，
  缓存立刻过时且看不出来。判据与 `chars` / `edited` 现算、`followUps` 落旁路文件同源：
  **重算要花钱的才存**。
- **只能复用已有标签**：发现不了新词。这与既有产品判据一致（智能分类的 `tagMode` 默认就是
  「优先复用」），新词发现留给智能分类——用户主动触发、一次处理 N 篇、带审定环节。
- **不参与导出、不改正文、不改文件夹归属**。它是视图上的一次性操作入口。

### ⚠️ 采纳为什么不能走 `/classify/commit`

`commit` 走 `store.apply_classifications`，那里是
`e["folderId"] = a.get("folder_id") or ""` —— **无条件覆盖**。只带 tags 不带 `folder_id`，
会把这篇文档从它所在的文件夹里踢回未分类；而界面上只看到「标签加上了」，看不出它还顺手
改了归属。`batch` 的 `tag` action 走 `update_transcripts_tags(..., 'add')`，只动标签。

### 测试

`test/tagSuggest.test.ts`（16 条）：词边界正反例、中文子串、大小写、正则元字符（`C++` / `C#`）、
排除已有标签、标题优先、次数排序、去冗余（含「短标签还有独立出现时保留」的对照）、
词表去重、`limit`、空词表/空正文。

---

## 2026-10-18 — 索引重建不再有损：分类真相搬进 `meta.json` / `folders.json`

### 起因

`index.json` 一直被称作「缓存」，但它此前**是**文件夹归属、标签、创建时间、来源信息的
**唯一**存放处。于是 `_rebuild_index`（index 缺失 / 损坏 / 旧格式时自动触发）只能把它们
清空：

```python
self._index = _empty_index()          # folders 整表归零
"tags": [], "createdAt": "",
"folderId": "",                       # 每篇的归属归零
```

而这条路径**不需要用户做错任何事** —— 一次断电、一次异常退出就够。上一轮做文件夹嵌套时
把它记进了「已知偏差」，但真正的判断是：这不是「有损」，是**把不该它保管的东西交给了它**。

### 做法：让不可重建的字段变成可重建的，而不是「重建时补救」

真相搬回各自的自然归属地，于是重建从「补救」退化为「读文件」：

| 数据 | 落点 | 为什么是这里 |
|---|---|---|
| `folderId` / `tags` / `createdAt` / 来源字段 | `{doc_dir}/meta.json` | **这篇文档**的属性，跟着文档走（搬目录即搬分类） |
| `folders` 树 | `{root}/folders.json` | **整个库**的属性，只有库根有资格放 |

`index.json` 仍保留这些字段作为**运行时缓存**（`folderId` / `tags` 参与过滤与计数，
每次列 N 篇都读文件太慢）；只有 `_rebuild_index` 才回落到真相文件。

⚠️ **主键是 `dir_name` 而不是文档 id**：重建时 id 由正文内容 sha256 重推，必然变 ——
按 id 关联等于什么都对不上。这也是 sidecar 必须住在**目录**里、而不是一个集中清单里的原因。

⚠️ `transcript.md` **不能**加自证头（它会进模型 Prompt，加头 = 改生成输入），故文档侧
必须是独立文件。

### 新增文件

- **`core/atomic_io.py`**：`atomic_write_text` —— 全项目唯一原子写。`Store._atomic_write`
  改为委托它（`mkstemp + os.replace` 的坑只要有一份漏了，另一份的失效方式这边看不出来）。
- **`storage/doc_meta.py`**：`read_doc_meta` / `write_doc_meta` / `read_folders` /
  `write_folders` + `DOC_FIELDS`（16 个字段：分类 3 + 来源 13）。`SCHEMA = 1`，
  读到不认识的版本按「没有可恢复信息」处理（不猜、不报错 —— 它必须能走完）。
  - `metadata` 整个 dict **不存**（可能很大），只摘其中四个会被当顶层字段消费的值。
  - `read_folders` 的 **`None` 与 `[]` 刻意区分**：前者 = 文件不存在/读不出来，后者 =
    库里确实没有夹。
  - **失败策略两边刻意不同**：`write_folders` 抛（库级、低频，失败意味着下次重建丢整棵树）；
    `write_doc_meta` 记 warning 不抛（批量时逐篇调用，一篇写不进去不该让整批失败）。

### `Store` 改动

- `_save_index()` 追加 `doc_meta.write_folders(...)`，**顺序固定：先 folders.json 后
  index.json** —— 两者不一致时让**真相领先缓存**。空库不凭空造文件（文件已存在才写，
  于是「删光所有夹」会落一个空表而不是删文件）。
- `_rebuild_index()`：`folders` 从 `read_folders()` 取，每篇的字段从 `read_doc_meta()` 取，
  再按 `DOC_FIELDS` 覆盖到条目上。⚠️ **刻意不从 `index.json.bak` 回落**：那份备份存的是
  **损坏原文**（拷它是为了给人抢救，不是给代码读），从中捞不出东西 —— 写一条「有兜底」的
  分支只会让人以为这条路径有救。
- **新增 `_persist_doc_meta(entry)`**，六个写点接入：`save_transcript` /
  `save_transcript_full` / `set_transcripts_folder` / `update_transcripts_tags` /
  `apply_classifications` / `delete_folder`（批量操作收集命中项后统一落盘，仍是**一次**
  index 写盘）。
- **新增 `_migrate_classification_sidecars()`**（启动时、`_load_index` 之后）：把 index 里
  已有的分类补写成 sidecar。**只写 sidecar、一个字节都不碰 index.json**（正常启动时索引
  文件必须逐字节不变）。幂等判据用「**读的结果**」而不是「文件在不在」—— 损坏的 sidecar
  同样返回 `None` / `{}`，一并重写。

### 已知边界

升级到本版本**之前**索引就已经坏了的老库，那部分分类确实回不来（那时 sidecar 还没诞生，
`.bak` 里是损坏原文）。正常升级路径是「索引还好时打开一次」，那一刻迁移已经把真相写好。
这条写在 `test_rebuild_without_sidecar_degrades_to_empty` 里 —— 记录「放弃在哪」，不粉饰。

### 测试

`test_store_index_recovery.py` +6 条：分类重建后全在（含来源与 createdAt）/ 无 sidecar 时
如实降级 / sidecar 损坏被启动迁移修好 / 迁移不碰 index.json 且幂等 / 全新库不产生 sidecar /
`folders.json` 随建改删三条路径同频。`test_folders.py::test_rebuild_keeps_schema_complete`
从「断言分类必然丢失」改为「断言分类必须回来」。

---

## 2026-10-18 — 仓库清理：一次性文档与构建残留

| 对象 | 处理 |
|---|---|
| `pi-docs/`（18 份设计稿，368K） | 删除。8–10 月的设计过程文档，未被 git 跟踪；用户确认清理 |
| `dist/`（799MB） | 删除。7 月 9 日旧项目名 `DeepDraftBackend` 的构建产物，PyInstaller 可重建 |
| `_pyi_build.log`（42K） | 删除。PyInstaller 打包日志 |
| `backend/__pycache__/_*.pyc` ×4 | 删除。`_chart_stress` / `_old_export` / `_render_compare` / `_vision_check` 的孤儿字节码，源脚本早已删除 |
| `backend/_export_probe.py` | 删除。本轮导出探针，按「`_` 前缀、用完即删」约定移除 |
| 仓库根 `_*.md` ×5 | **保留**。送外部模型评审的材料，属用户资产（见 `.gitignore` 中 `/_*.md` 的注释） |
| `backend/_type_demo_gen.py` | **保留**。CLAUDE.md 明示的保留例外 |
| `调研/` | **保留**（用户选择） |

顺带修两处指向 `pi-docs/` 的 markdown 死链：CLAUDE.md 图表围栏协议收紧的出处、CHANGELOG 视频直传方案链接 —— 改为纯文本并注明目录已于本日清理。`.gitignore` 的 `pi-docs/` 与 `调研/` 条目保留，防止将来同名目录被误提交。

---

## 2026-10-18 — 多篇合成：产物形态契约 + 证据折叠卡 + 导出线性化

### 起因

三个问题都是**结构性的**，不是措辞不够好：

1. **读者意图只换措辞、不换组织单位**。骨架只有一套（`_build_synthesis_output`，六节固定），
   意图只改 `<task>` 段一句「读者目的」。于是选「找争议」和选「理解主题」拿到的正文**结构
   逐字相同** —— 「比较材料」本该以比较轴为骨架、「提炼行动建议」本该以决策与条件为骨架，
   现在全塞进「## 读者问题」的 3–6 个 `###` 里。用户看不出自己所选的意义。
2. **证据没有去处**。正文只有 `[文档 i]` 一种锚点，而方法 / 样本 / 范围 / 局限 / 反例这些
   读者**需要**但**不想每段都读**的东西，模型只能二选一：揉进正文（打断阅读）或干脆不写
   （丢掉依据）。后者更常见，也更危险 —— 产物读起来流畅，但结论无从检验。
3. **导出无法承载折叠**。Markdown / 纯文本里没有 `<details>`，若证据块只做预览侧折叠，
   导出就是无解的。

**用户已定的产品决策**（实施未改动）：主题驱动的连续文章（不是逐篇摘要拼接）｜证据按需展开｜
来源与论点的关联不能事后再补（编号由结构派生）｜导出统一降级、**Markdown 也一样**｜
区分「原文引用」（逐字原话，留正文）与「证据」（可折叠）｜可追溯性放轻为兜底，
硬指标是理解效率 / 结构连贯 / 信息完整 / 阅读负担。

### 做什么

| # | 改动 | 落点 |
|---|---|---|
| 1 | `OUTPUT_SHAPES` 五种形态（label / unit / description）+ `resolve_output_shape()` 唯一入口 | `generation/synthesis.py` |
| 2 | `READER_INTENTS` 每项加 `shape`，新增「**梳理观点**」（`claims`，排在 `understand` 之后 ⇒ 前端默认项不变）；`custom` → `article` | 同上 |
| 3 | `_SHAPE_SKELETONS` 五套骨架（article 的节标题与顺序**逐字保留**）+ `_SHAPE_TASK_BASE` 五条形态基串 | `generation/prompt.py` |
| 4 | 每套骨架**各有降级出口**（材料不支持该形态时直说、不得硬造） | 同上 |
| 5 | `_SYNTHESIS_UNIVERSAL_RULES` 四条（连续优先 / 分歧显式 / 结论可溯 / 完整性） | 同上 |
| 6 | `_SYNTHESIS_EVIDENCE_FORMAT` 证据块规范（紧跟论断 / 块内 2–5 条 / **不写编号** / 块内不用 `##`/`###` / 只在正文主体放） | 同上 |
| 7 | 共用尾 `_synthesis_tool_layer()` 去掉「读者问题」硬引用 → 「正文主体」；文风第 9 条同步中性化 | 同上 |
| 8 | `SYNTHESIS_BLOCK_TYPES = {"evidence"}` 第四类集合（**不进** `ARTIFACT_BLOCK_TYPES`），`KNOWN_BLOCK_TYPES` 取并集 | `core/artifact_blocks.py` |
| 9 | `linearize_evidence(md)`：原位 `[证据 N]` + 文末 `## 证据附录`；幂等（无围栏原样返回入参） | 同上 |
| 10 | 导出四个入口接线性化：`GET /export/{id}` / `_build_export_zip` / `_collect_doc_products` / PDF（走 HTML） | `routers/export.py` |
| 11 | capabilities 下发 `shape` / `shapeLabel` / `shapeDescription` | `routers/capabilities.py` |
| 12 | 前端 `SYNTHESIS_BLOCK_TYPES` + `linearizeEvidence()`（与后端共享 fixture） | `lib/markdown-scan.ts` / `markdown-markers.ts` |
| 13 | `EvidenceBlock`：预览渲染成 `<details><summary>依据 · 文档 2</summary>` 折叠卡 | `preview/MarkerContent.tsx` |
| 14 | `.md-evidence` 样式 + `[data-marker-decor="off"]` 归零规则（**只去装饰，折叠保留**） | `styles/blocks.css` |
| 15 | MD 下载与**复制**：`stripMarkers(linearizeEvidence(...))`（顺序不可反） | `ExportMenu.tsx` / `DocLibrary.tsx` |
| 16 | 合成对话框：意图组下方**只读**形态提示（含「自定义 → 综合文章」的说明） | `doc-library/DocLibrary.tsx` |
| 17 | `PROMPT_VERSION` `"15"` → `"16"` | `generation/prompts/__init__.py` |
| 18 | 测试：`test_prompt.py` 新增 7.10 节（契约自洽 / 五套互斥 / 共用尾各一次 / article 顺序 / **两条负向对照**）；`test_export_blocks.py` 扩写；`test_block_prompt.py` 的合成判据改为集合驱动 | 两侧 |

### 为什么这么设计

- **形态由代码与 prompt 契约确定，不靠模型自由发挥**：`READER_INTENTS[*].shape` →
  `_SHAPE_SKELETONS` 是**代码侧映射**，用户看不到形态选择器（两个旋钮必然出现「意图说理解、
  形态选比较」的错配）。⚠️ 入口严、读回宽：请求入口对非法意图**报错**，读历史 meta 时对
  `None` / 未知 key **回退 `article`**（旧文档没有该字段，读了不能崩）。
- **降级出口是每套骨架的必需部件**：没有它，用户选「找争议」而材料高度一致时，模型会
  **硬造对立** —— 这是本功能最容易实机翻车的一处，且产物上看不出异常。
- **编号由结构派生**：模型不写 `[证据 N]`，编号按围栏出现顺序在代码侧生成。「生成完再补
  引用」与「保留来源与论点的关联」是两回事，前者会漂移（材料有增删就对不上）。
- **导出统一线性化、含 Markdown**：「原位 `[证据 N]` + 文末附录」在纯文本里也能读到依据，
  而预览保持折叠（连续阅读优先）。这是本轮**唯一**允许「预览与导出不同」的维度，矩阵写在
  [export-pipeline.md](export-pipeline.md) §1 与本文末。
- **证据块不进 `get_markdown_block_format()`**：那一份是 `build_notes_prompt` **无条件**注入给
  所有 notes 的（九种产物侧块 + `card` 节修饰符），混进去等于单篇笔记也开始学证据块。
  归属用集合表达（三集合不相交），有守卫钉住。
- **刻意不新增用户开关**（证据块 / 工具层 / 形态选择器 / 篇幅档位）：证据「按需自动出现」
  才是它存在的理由，开关化退回「模型不判断、全靠用户猜」；篇幅档位与文风第 9 条冲突。

### 修复的既有缺陷

- **「复制」从未剥离协议标记**：`DocLibrary.tsx` 的 `copyMarkdown` 直接复制磁盘原文，用户
  粘出去的是一段带 `<!-- limo-block:v1 … -->` 的文本。本轮接上
  `stripMarkers(linearizeEvidence(raw))`，顺手修掉。⚠️ 顺序不可反 —— 反了围栏已被剥掉、
  附录永远不生成，而两条链都「跑成功了」。
- **`test_prompt.py` 的旧判据用裸子串定位节**：正文里**合法提及**节名（如骨架里那句
  「同一批维度在文末『## 各篇速查』里逐条成节」）会让 `.index()` / `count()` 命中早期提及，
  造成误红。本轮新增 `_heading_pos()` 用「独立成行」正则 —— 这类误红容易被误读为「实现坏了」。

### 影响范围

- **只影响多篇合成**（`build_synthesis_prompt` 与合成对话框）；单篇笔记 / 整理版 / Excel
  数据摘要零变化（`test_block_prompt.py` 有守卫）。
- 导出：四种格式的证据块处理变了（线性化），**无 evidence 围栏的产物逐字节零回归**。
- `PROMPT_VERSION` `"16"`；不碰 `VERSION`；无数据格式迁移（历史产物无证据块，读取路径
  对未知/缺失字段一律宽容）。
- 门禁：`bash scripts/check-all backend`（pytest 全绿）/ `frontend`（tsc + lint + vitest）/
  `docs` 三层全绿。

### 已知偏差（刻意不做）

- **四套新形态尚无实机样本**：本轮只保证「契约正确、结构在场、降级出口有」，观感要靠真实
  重跑（选「比较材料」「梳理观点」各合成一次）后再调。
- **`claims` 与 `controversy` 没有共用生成器**：计划原定 `_build_positions_skeleton(focus, …)`
  参数化，实施时改为各写一份完整骨架 —— 两者分组单位（按观点 vs 按冲突）、收尾节（关系 vs
  假分歧）、每项步骤都不同，参数化后占位符只剩一句话，两份半成品比两份完整文本更难维护。
  理由写在 `_synthesis_claims_body` 的 docstring 里。
- **预览不编号**（导出才编）：`CardSections` 对每节各调一次 `MarkerContent`，组件内部按局部
  顺序编号会跨节重号；空间上紧贴论断，关联足够。
- **PDF 不能折叠**：走 HTML 端点，拿到的是线性化版本。已知代价，导出菜单与文档写明。
- **证据块不做机械校验**：「没有证据块」不是失败（三种场景写进 prompt，由模型判断）。

---

## 2026-10-18 — 卡片体系形态整合 + 格式选项自报块类型 + 判据污染修复

### 起因

三套卡片体系（产物章节卡片 `cards.css` / 产物语义块 `blocks.css` / 对话语义块
`chat-blocks.css`）各自演进到能用后，留下了六处可观测的漂移：

1. `blocks.css` 的 metric 数字固定 `--tk-success`，**不随主题色变化** —— 而同文件的
   `steps` 注释自己写着「accent 留给 key-takeaway / metric 的数字」，是落地时写偏。
2. 对话侧五个 `.chat-block` 全用**左边框**表达语义 —— 左边框是引用/强调的语言，
   带图标的卡片应该是四边简单边框（用户已明确）。
3. `.chat-block` 的 `border` 跟随 `--card-border`，而 `apple` / `ink` 两套把它设为
   `none` ⇒ 语义块**整块没有边界**，只剩一个图标悬空。
4. `.chat-cards` 按**宽度**定列数（`auto-fit minmax`）⇒ 同样 3 张卡，浮窗宽窄不同
   列数就不同，同一内容在两台机器上长得不一样。
5. `.chat-card` / `.md-card` 缺 `overflow-wrap`，长 URL 撑破卡片。
6. 极端篇幅（整组只有标题 / 单张只有标题 / 4+ 块 / 7+ 列表项）没有任何变种。

两侧 prompt 另有两处「注释说限制了、正文没写」：`steps` 注释声称已限项数，正文只限了
**每项字数**；`metric` 只说「少量」，没有可执行的量。

最后一条是本轮审查新代码时抓到的 P0：`ChatCard` 的复制按钮是 `.chat-card` 的
**第一个子元素**，而长内容卡 / 整组退列判据写的是 `:nth-child(4)` —— 实际含义是
「3 个内容块」，规范**鼓励**的「标题 + 两段」会提前一格触发变种并整组退列。更糟的是
按钮当时是**条件挂载**（`{!streaming && …}`），同一个判据在流式与结束时数出不同的
子节点数，卡片在流式结束那一帧突然变身。

### 做什么

| # | 改动 | 落点 |
|---|---|---|
| 1 | 对话侧 `.chat-block` 由左边框改**四边框**：`border: 1px solid var(--block-edge)`，五个变体各自定义 `--block-edge`（accent / warning 与 `--tk-border` 混色）。**自持边框、不跟随 `--card-border`** | `chat-blocks.css` |
| 2 | `.chat-cards` 列数改**按张数**（1 / 2 / 3 / 2×2 / 5+ 单列），删 `auto-fit`；`.chat-card-group` 加 `container-type: inline-size`，`@container (max-width: 320px)` 兜底退单列 | `chat-blocks.css` |
| 3 | 篇幅变体六组（整组仅标题 / 单张仅标题 / 两段 / 4+ 内容块转长内容卡 / 5–6 项列表 / 7+ 项退列）+ `overflow-wrap` + `text-wrap: balance`，全部纯 `:has()` 结构判据 | `chat-blocks.css` |
| 4 | **P0 修复**：长内容卡 / 退列判据 `:nth-child(4)` → **`(5)`**（4 个内容块 + 常驻复制按钮）；内缩规则改 `> :not(h3):not(.chat-card-copy)`（代码块 / 表格也要内缩，按钮是 `absolute` 必须排除） | `chat-blocks.css` |
| 5 | 卡片复制按钮**流式期间隐藏但不卸载**（`.chat-card-copy--streaming`，`display: none`，特异性 (0,2,0)）—— 两态子节点数一致，消除流式结束跳变 | `ChatMarkerContent.tsx` / `chat-blocks.css` |
| 6 | 产物侧 metric 数字 `--tk-success` → **`--tk-accent`** + `font-variant-numeric: tabular-nums`；宽表 `overflow-x: auto` + 首列 `sticky`；大图 `max-height: 70vh` + 等比；`.md-card` 补 `overflow-wrap` / `text-wrap` | `blocks.css` / `cards.css` |
| 7 | `get_summary_format()` 教模型给 `## AI 总结` 写 `card` 节修饰符（唯一能对上骨架轴的格式选项）；顺带修「整个视频」→「整份材料」（该函数被 7 种来源共用） | `generation/prompt.py` |
| 8 | `get_markdown_block_format()`：`metric` 补「1–4 个指标、数字带单位」；`steps` 补「不超过 6 个、更多改用 `timeline`」；`image` 补反向指路（图表管道生成的图不要再包壳） | `generation/prompt.py` |
| 9 | 对话侧 `formats.md`：`card-group` 补篇幅（每张一两段 / 3–5 个要点）；`digest` 与 `card-group` 加交叉分野（要比要选 → 卡片组；只要整体印象 → 摘要卡） | `agent/prompts/formats.md` |
| 10 | 安装路径告警**关过就不再提示**（`limo_install_warning_dismissed`，跨会话）；读取失败退化为「每次都提示」而非「视为已关闭」 | `App.tsx` / `lib/installWarning.ts` |
| 11 | 测试：新增 `chatCardShape.test.ts` / `artifactCardShape.test.ts`；`chatBlocks.test.tsx` 的流式断言改为「按钮在、带 `--streaming`」；新增源码守卫钉住 `ChatCard` 按钮不得条件挂载 | 前端 |

`PROMPT_VERSION` 由 `"13"` → `"14"`（metric / steps 补量）→ `"15"`（格式选项自报块类型）。

### 为什么这么设计

- **复杂度留在样式层**：篇幅变体全部是结构判据（`:has()` 看有没有 `p` / 第几个子节点），
  不看字数、不数行 —— 规范于是不必新增篇幅硬约束，模型也不用猜「多长算长」。
- **`.chat-block` 自持边框、只跟随质感**：卡片组里的 `.chat-card` 是页面级卡片，跟随
  `--card-border` 是对的；气泡内的语义块在 `apple` / `ink` 下消失是**缺陷**不是风格，
  故 `border` 自持，而 `border-radius` / `background` / `box-shadow` 继续跟随。
- **按张数分列**：数量与 prompt 的「2–4 个对象」一致，且同一内容在任何窗口宽度下
  列数相同；窄到放不下时由 `@container` 兜底退单列，不再回到宽度驱动。
- **按钮隐藏不卸载**：`display: none` 不参与布局与无障碍，视觉上等价于不渲染，但**仍
  计入 `nth-child`** —— 判据在流式与结束时同源。刻意不用 Tailwind `hidden`：它与
  `.chat-card-copy` 的 `display: flex` 同为 (0,1,0)，胜负取决于样式表加载顺序。
- **产物侧 CSS 是导出单一真相源**：`blocks.css` / `cards.css` 被 `export.py` 读盘内联，
  改样式即两侧生效（Word 不读 CSS，零影响）；`chat-blocks.css` 仍**不进导出**。

### 影响范围

- 前端对话浮窗与预览区观感；导出 HTML / PDF 随之生效（metric 色、宽表滚动、首图限高）。
- 产物 prompt 输出形态（`PROMPT_VERSION` `"15"`）；对话侧 prompt 不 bump。
- 不碰 `VERSION`，不涉及数据格式迁移。
- 门禁：`bash scripts/check-all frontend` / `docs` 全绿（1096 项前端测试）。

### 已知偏差（刻意不做）

- **`overflow-x: auto` 管不到打印/PDF**：打印媒体没有滚动机制，长宽表右侧列仍会被裁切。
  不在 `_PRINT_CSS` 里改 `visible` —— 那只是把裁切从容器边界换成纸张边界，且溢出内容会
  改变分页、可能凭空多出空白页。待实机导出一篇长表 PDF 后再定方案（缩字号 / 横版 / 拆表）。
- 对话侧表格横向溢出是改造前就有的行为，`display: block` 方案会破坏列宽分配，本轮不做。
- `@container` 的窄浮窗兜底依赖实机验收（TS 测不到布局）；即便不生效，数量分列规则独立成立。

---

## 2026-10-16 — 主题系统优化：JS 里只剩 label + 轴注册表 + 导出跟随应用主题

### 起因

六轴主题系统（明暗 × 26 套配色 × 14 种基调 × 6 种纹理 × 5 种字体 × 强度滑条）**用户看得见的
部分是好的**，但底下积累了三类问题，且已经漂移出可观测的错：

**一、色值有三份副本，其中两份和真相不符**

| 副本 | 位置 | 现状 |
|---|---|---|
| `BASE_OPTIONS` 56 个 hex | [lib/theme-base.ts](../frontend/src/lib/theme-base.ts) | 与 CSS 一致，纯重复 |
| `ACCENT_SWATCH` 52 个 hsl | [lib/accent.ts](../frontend/src/lib/accent.ts) | hue/sat 逐项一致，**L 散在 13 种取值（32~60）**，而实机恒为 50%（浅）/ 62%（暗）—— 色块亮度**不是**实机亮度 |
| `TEXTURE_SWATCH` 6 个 hex | [lib/texture.ts](../frontend/src/lib/texture.ts) | 与 [lib/texture.css](../frontend/src/lib/texture.css) 的 `background-color` **6/6 全不符** |

这三份表**零守门人** —— 主题域约 95 个用例全在测**行为**，没有一条测**数据一致性**，所以漂移了也静默。

**二、加一条轴要改 5~6 处，且启动链靠手写**

`store/index.ts` 有 12 行手写 `apply*(getSaved*())`，注释里记录着**已踩坑三次**。层叠顺序是**隐含约束** ——
`[data-base]` 必须住在 `:root` 与 `[data-theme=dark]` 之间（三者特异性同为 (0,1,0)、作用在同一元素，
顺序即裁决），**这条此前被写反过，整轴静默失效**（所有预览都是对的，因为预览把 `data-base` 设在非根元素上
—— 继承值永远输给直接声明）。

**三、导出完全不跟随应用主题 —— 直接违背项目写死的核心约束**

`HTML_THEMES["limo"]` 是自包含裸色值 CSS，零 `var()`、零 `--tk-*`；两个导出入口一个 accent/base 参数
都不传，恒用硬编码蓝 `#2563EB`。**骨架 / 卡片方案 / 逐维度微调 / 标记装饰全都跟随了，唯独颜色不跟随。**
顺带暴露更要命的一条：limo 主题用 `@media (prefers-color-scheme: dark)` 而非 `data-theme`
⇒ **导出文件的明暗不由用户决定，由打开文件的读者机器的系统偏好决定**。

### 做什么

| # | 改动 | 落点 |
|---|---|---|
| 1 | 新增**主题层** `styles/theme-layer.css`：六轴的**纯数据层**（全部是变量声明，零消费方选择器）。`index.css` 用 `@import` 引入 | 前端新建 |
| 2 | 新增**轴注册表** `lib/theme-axes.ts`：key / attr / storageKey / options / fallback / apply 一处收口 | 前端新建 |
| 3 | 删 `ACCENT_SWATCH` / `TEXTURE_SWATCH` 整表、`BASE_OPTIONS` 的 4 个色值字段；色块改 `data-*` + `var()` 渲染 | `accent.ts` / `texture.ts` / `theme-base.ts` |
| 4 | `store/index.ts` 12 行手写启动链 → `THEME_AXES.forEach(a => a.apply(a.saved()))`；`themeSlice` 5 个 setter 收敛为 `setAxis` | 前端 |
| 5 | `[data-accent]` **自己重跑一次派生**（新增 `:root, [data-accent] { --tk-accent: hsl(…) }`） | `theme-layer.css` |
| 6 | 导出跟随：`_build_selfcontained_html()` 加 6 参数 → 过字符白名单 → 拼 `<html>` 属性 + 行内 `--texture-opacity` | `export.py` |
| 7 | 主题层注入闸门：**有任一轴参数才注入**，且排在 `HTML_THEMES` **之前**（变量先定义、后消费） | `export.py` |
| 8 | `_CARD_TOKENS_CSS` → `_EXPORT_NEUTRAL_TOKENS_CSS`，**仅在未跟随时**注入（跟随路径由主题层接管） | `export.py` |
| 9 | `HTML_THEMES["limo"]` 重写：配色类换 `var()` + 今天裸值作 fallback，排版结构类原样；**删 `@media print` 还原段** | `export.py` |
| 10 | 两个暗色块（主题层的 `--tk-*` 与 `--md-*`、limo 主题的暗色段）**全部包进 `@media screen`** | `theme-layer.css` / `export.py` |
| 11 | `_PRINT_CSS` 补 `body::before { display: none }`（纹理叠加层在纸上会把正文压成灰底） | `export.py` |
| 12 | 前端两个导出入口补 6 参数（query / POST body）；`ExportPanel` 的 `limo` 项文案改「**跟随应用主题**」（key 不变） | `ExportMenu.tsx` / `ExportPanel.tsx` |
| 13 | `LiMoBackend.spec` 的 datas 加 `theme-layer.css` | 打包 |
| 14 | 测试：新增 `test_export_theme.py`（18 条）+ `themeAxisSync.test.ts`；改 `test_export_print.py` / `test_export_blocks.py` / `themeTokens.test.ts` / `onAccent.test.ts` / `exportMarkers.test.ts` / `store.test.ts` | 两侧 |

### 为什么这么设计

1. **「JS 里只剩 label，一个色值都不留」是档 A 的唯一目标**。文档此前就写着「色块与实机是同源数据」，
   但同源的办法一直是**抄一份值**——两份值只要有一次没同步就静默漂移（`TEXTURE_SWATCH` 已经 6/6 全错）。
   改法是**让色块消费与实机完全相同的那些变量**（`data-accent` + `var(--tk-accent)`），
   于是「不一致」从结构上不可能发生 —— 不需要守卫，因为**值只有一份**。
   这正是 [onAccent.ts](../frontend/src/lib/onAccent.ts) 早就写着的做法（「本模块一个色值都不存」），
   只是此前没推广到另外三条轴。
2. **`[data-accent]` 必须自己重跑派生**（`#5`）—— CSS 自定义属性的**计算值**是「指定值经 `var()` 替换后的
   结果」，**继承传递的是计算值**。故 `:root { --tk-accent: hsl(var(--accent-hue), …) }` 在 `<html>` 上就算完了，
   **子元素上设 `data-accent` 不改变它**。不补这条，删掉 `ACCENT_SWATCH` 之后设置页 26 个色块会
   **全部显示成应用当前配色** —— 比现状（26 个硬编码但至少互不相同）**更糟**。
   ⚠️ 这也解释了为什么同批要落到 `:root, [data-accent]` **两条腿**：`:root` 那一半是必需的 ——
   首帧（JS 未执行）与固定主题导出时 `<html>` 上没有该属性，只写 `[data-accent]` 会让主色整条消失。
3. **同批还修掉一个既存 bug**：[OnboardingWizard](../frontend/src/components/ui/OnboardingWizard.tsx) 的
   4 张预设卡在子元素上消费 `var(--tk-accent)` —— 于是 4 个圆点与标题色**全都等于应用当前配色**，
   而同一块的 `var(--tk-surface)` 是对的（它是直接声明）。`#5` 落地后它自动正确，**组件零改动**。
   决策前**未实机确认**（用户已看到效果），如实记录。
4. **明暗块的 `@media screen` 是「暗色是屏幕概念，纸永远是浅的」这一条判断的落地**。
   改前的做法是「先铺暗色、再写一段 `@media print` 还原成浅色」，判据是「还原段必须排在暗色段之后」
   —— 因为 `@media (prefers-color-scheme: dark)` 与 `@media print` **不互斥，会同时匹配**，
   靠源顺序裁决。改后**没有还原段**：暗色块整个包进 `@media screen`，打印时**根本不匹配**，
   落下来的是该配色自己的浅色档。**一处改动让三个宿主同时正确**（应用内 / 导出屏幕 / 导出打印），
   且打印拿到的不再是一份写死的蓝（选「胭脂」的暗色用户打印出来终于会是胭脂的浅色）。
   ⚠️ 代价如实记录：**应用内打印**（对应用本身 Ctrl+P）也会打印成浅色 —— 可接受（那套样式本来
   就没适配过，且方向是「更可读」）。
5. **`_EXPORT_NEUTRAL_TOKENS_CSS` 不能删，只能收窄条件**（`#8`）。原先的判断（「固定主题不消费
   `var(--tk-*)`，删了无害」）只对 `HTML_THEMES` 那段 CSS 成立 —— `cards.css` / `blocks.css` 是
   **按 `skeleton=card` / 有标记无条件注入**的，消费者身份与 `html_theme` 无关。删掉它 + 恒注入主题层，
   会让 **paper/github/minimal 的卡片与语义块底色从 `#ffffff` 变暖白**（视觉回归），
   连 `@media print` 的纯白强制也一并丢失。
   ⚠️ 两者取值 6/6 不同（`--tk-surface`: `#ffffff` vs 暖白 `#F4F1EE`；`--tk-accent`: `#2563eb` vs
   `hsl(211,100%,50%)`）—— 这正是「跟随」的**语义**差别，不是 bug。
6. **白名单只做字符，不持有第二张表**（`#6`）。后端**刻意不维护 key 列表** —— 那是 CSS 那份真相源的
   第二份副本。五个选择型轴用 `^[a-z0-9-]{1,24}$`，`texture_opacity` 用 `^(?:0|1|0?\.\d{1,4})$`。
   非法值**静默丢弃该项**（判据：某个轴值不对**不改变产物的性质**），其余轴照常。
   ⚠️ 字符集里绝不能有 `"` `<` `>` 空格 `=`：`data-accent="{v}"` 是**原样拼接的注入面**。
   ⚠️ 小数位限 **4** 与长度上限同一条理由 —— 字符白名单**不限制长度**，一次请求可以带几 MB 的合法数字进 `<style>`。
7. **「刻意不跟随」的最后三处，且这是判据不是遗漏**（与 `TYPE_COLORS` 同款）：时间戳徽标
   （`#007aff` / `#ff9500` 是**类别标识色**，跨主题保持可辨识）、Pygments 屏幕段与打印段（代码高亮
   主题是独立的产品决策，正交于 `--tk-*`）、以及**未跟随 + 卡片**这一档的明暗仍由读者系统决定
   （固定主题本就该固定）。三处**各加一句注释说明这是刻意分界**，否则下一个人会当漏网之鱼来「修」。
8. **零回归的两条落点**：① 无轴参数 ⇒ 一个属性都不写、主题层不注入 ⇒ 与改造前**逐字节相同**
   （第三方脚本与历史调用方零回归）；② `limo` 的配色写成「变量 + 今天裸值」的 fallback 形式
   （`var(--tk-foreground, #1C1C1E)`）—— 零回归靠 **`var()` 兜底**而不是靠分支。
   `HTML_THEMES["paper"/"github"/"minimal"]` **一字未动**。
9. **不选「构建期生成 CSS」**（档 C）：那会把 CSS 变成生成物、调试体验变差，属「预建大教堂」。
   主题层用 `@import` 接入 —— 实测构建产物里 `@import` 内容**插入在语句位置**，
   故主题层落在 texture 宿主规则之后、消费者规则之前，**相对顺序不变**；
   且主题层规则与消费者规则**都不带 `@layer`**，移动位置不改变优先级。

### 影响范围

- 前端：新增 `styles/theme-layer.css`、`lib/theme-axes.ts`、`test/themeAxisSync.test.ts`；
  改 `index.css`、`lib/accent.ts`、`lib/theme-base.ts`、`lib/texture.ts`、`lib/texture.css`、
  `store/index.ts`、`store/themeSlice.ts`、`components/settings/SettingsPage.tsx`、
  `components/ui/ExportMenu.tsx`、`components/ui/ExportPanel.tsx`
- 后端：`app/routers/export.py`（`_load_theme_layer_css` / 6 参数 / 白名单 / 注入闸门 /
  `_EXPORT_NEUTRAL_TOKENS_CSS` / `HTML_THEMES["limo"]` / `_PRINT_CSS` / 三处传参）
- 打包：`backend/LiMoBackend.spec` 的 datas **必须**含 `theme-layer.css`
  —— ⚠️ 少了这一行**开发版全绿而打包版导出直接 500**（读不到文件时按纪律显式抛错，不静默输出无主题的 HTML）
- 测试：后端 +18（`test_export_theme.py`），前端新增一致性守卫 + 6 处判据更新
- ⚠️ **不动 `VERSION`**（本轮未要求打包）；**不改 `PROMPT_VERSION`**（不涉及发给模型的文本）

### 已知偏差与残余风险

1. **导出页纹理叠加层的四边 40px 无纹理**：`body` 有 `margin: 40px auto`，而叠加层 `inset: 0`
   覆盖的是 padding box。可接受（应用内那条 `::before` 是同款形态）。
2. **导出卡片底色变化**：未跟随时是 `#ffffff`，跟随时是用户基调色（暖白系）—— 这是「跟随」的语义，
   需要真人看一次观感。
3. **测试里解析 CSS 文本必须处理 `@media` 嵌套**：`([^{}]+)\{([^}]*)\}` 遇到
   `@media screen { [data-theme="dark"] { … } }` 时 `([^}]*)` 会一路吃到**内层的** `}`，
   于是「选择器」被算成 `@media screen`、`[data-theme="dark"]` 掉进「声明体」里 ——
   症状是 `darkAt()` **恒返回 -1**（判据不再测任何东西，且不报错）。见 patterns #71。
4. **`themeSlice` 的明暗轴参与注册表但 `apply` 带特化**（还要设 `style.colorScheme` + 调
   `applyOnAccent`）；**纹理强度不进注册表**（它是数值轴不是选择型，启动链里单独一行 + 注释说明理由）。
5. **`data-texture` 的变量值里嵌 `var(--second-hue)`**，替换在**声明元素**上完成 ⇒ 导出侧若某子树带
   `data-accent`，会用到 `<html>` 的 accent 算好的图案。应用内无此场景，导出页也没有。

---

## 2026-10-12 — 文档 id 唯一：重建不再按内容撞 id + 启动自愈已有脏索引

### 起因

用户贴来浏览器控制台的告警（同一句出现两次）：

```
Encountered two children with the same key, `tr_336b128e8316`. Keys should be unique so that
components maintain their identity across updates. Non-unique keys may cause children to be
duplicated and/or omitted — the behavior is unsupported and could change in a future version.
```

查库确认：`index.json` **95 条 / 94 个唯一 id**，撞的是 `tr_336b128e8316` 两条。两条
`dir_name` 分别是 `Herman Melville - Moby-Dick-94882f` 与 `…-cbcbd1`，两份
`transcript.md` **长度 3566、sha256 前 12 位都是 `336b128e8316`** —— 内容一字不差。
全库 95 条里 90 条 `createdAt` 为空 ⇒ 这个库被 `_rebuild_index` 整库重建过。

**根因链**：磁盘上有两份内容相同的文档目录 → `index.json` 缺失/损坏触发
`_rebuild_index` → 它按 `transcript.md` **内容**派生 id（无可靠方法，只能这样）→
同内容必然推出**同一个 id**。而 id 是文档的主键：前端 React key、`get_transcript` /
`update_title` / **`delete_transcript` 的定位**全用它。

⚠️ **比控制台告警严重得多的后果**：`delete_transcript` 用 `find` 定位，只命中**第一条**
—— 第二条无论点多少次「删除」都还在列表里。前端那句 warning 是这件事**唯一**的可见征兆。

### 做什么

| # | 改动 | 落点 |
|---|---|---|
| 1 | 新增模块级 `_unique_tx_id(seed, taken)`：**首次尝试与旧行为逐字相同**，撞了才加盐，16 次仍撞退化为随机 id（绝不返回重复值） | `store.py` |
| 2 | `_rebuild_index` 记 `taken_ids` 集合，id 派生改走 `_unique_tx_id` | `store.py` |
| 3 | 新增 `_dedupe_transcript_ids()`：启动时定点修**已经落盘**的重复 id（幂等），挂在 `_load_index()` 之后 | `store.py` |
| 4 | 回归测试 5 条（含 1 条对照实验） | `tests/test_store_index_recovery.py` |

### 为什么这么设计

1. **首次尝试必须逐字等于旧行为**（`f"tr_{sha256(seed)[:12]}"`）。绝大多数库没有重复目录，此时重建结果与改造前**一字不差** = 零回归。若上来就换个派生方式，升级一次全库 id 全体漂移，历史引用（`sourceDocIds` 锚点、对话里的 docRefs）全断。
2. **盐用 `dir_name`**：同内容目录之间**唯一**的区别就是目录名，内容派生的值已经相同了，只有目录名能打破。`_dedupe_transcript_ids` 的种子是 `f"{tid}\x00{dir_name}"`，同理。
3. **16 次仍撞退化为随机 id，而不是抛错**：数据已严重异常，但「重建/自愈中途崩掉」会把「索引有点脏」升级成「应用起不来」。退化为与正常导入路径同款的 `uuid4` 是安全出口，**且绝不返回重复值**。
4. **两道网，职责不同**：`_unique_tx_id` 管**将来**（下一次重建不再产生重复），`_dedupe_transcript_ids` 管**已有的脏索引**（改代码不会让旧数据自愈——用户库里的重复条目就在那儿，每次刷新都会发给前端）。缺前者则每次重建复发，缺后者则存量用户永远修不好。
5. **自愈是「定点改字段」不是「重建」**：只改 `id` 一个键，**不重扫磁盘、不碰 tags / folderId / 来源 / createdAt、不删任何目录**。这是第 3 轮审计 P0 的直接教训——启动迁移里整库 `_rebuild_index` 会把分类与标签**不可逆抹掉**（磁盘上根本不存在这些字段）。
6. **`markdowns` / `processes` 的 key 保持在原 id 上**：那两份目录里只可能一份有产物，而 dict 本来就只存得下一份——让产物留在「第一条」，与 `get_transcript` / `delete_transcript` 取 `find` 首条的行为一致。⚠️ 反过来「把产物字段也搬过去」是凭空发明「哪份目录生了这篇笔记」这个事实，故不做。
7. **不选「输出前静默去重」**：那违反本项目「失败显式化 / 不做静默降级」的原则，且掩盖了「磁盘上真有两份目录」这件事——用户有权知道并自行决定删哪份。

### 影响范围

- 后端：`app/storage/store.py`（`_unique_tx_id` + `_rebuild_index` 唯一化 + `_dedupe_transcript_ids` + `__init__` 挂载）
- 测试：`tests/test_store_index_recovery.py` +5 条（`_unique_tx_id` 的零回归/加盐、两份同内容目录重建后唯一、`_dedupe_transcript_ids` 修脏索引与幂等、**对照实验**）

⚠️ **对照实验有个坑，已写进测试注释**：只掐掉第一道网（`_unique_tx_id`）时，第二道网
（`_dedupe_transcript_ids`）会当场把刚产生的重复修好 ⇒ 对照实验**反而全绿而结论是错的**。
验其中任何一道都必须把另一道按住。
⚠️ **本次只修代码，不动用户磁盘上的任何目录** —— 那两份 `Moby-Dick` 目录仍在原地，
删哪份由用户自行决定。
⚠️ **不改 `VERSION` 之外的任何标识**（无 `PROMPT_VERSION` 变更：本次不涉及发给模型的文本）。

---

## 2026-10-12 — 对话侧卡片：补三种形态 + 视觉跟随卡片方案 + 复制 / 换一批

### 起因

对话侧 `limo-block` 已有四种（`card-group` / `follow-up` / `info` / `digest`），但
① **形态缺三块常见**——注意事项、操作步骤、引用出处，模型想说这些时只能退回普通段落；
② 对话卡片与产物侧章节卡片**各长各的**，用户在设置里挑的那套「卡片视觉方案」在对话里
完全不生效；③ 卡片是**只读**的——一段比较结论、一个引用，想搬走只能手动划选；
④ 追问卡给了一批就定死了，**想换一批没有办法**（二次调用的能力早就有，缺的是入口）。

### 做什么

| # | 改动 | 落点 |
|---|---|---|
| 1 | **新增三种块**：`caution`（注意事项）/ `how-to`（操作步骤）/ `quote`（引用出处）→ 对话侧 **7 种** | 两侧常量表 + `formats.md` 三节 + `ChatMarkerContent` 三分支 + fixture 三处 |
| 2 | **视觉跟随卡片方案**：容器的 `border` / `border-radius` / `background` / `box-shadow` 改消费 `--card-*`（与产物侧章节卡片同一套变量、同一套 8 方案） | `chat-blocks.css` 重写 |
| 3 | **`.markdown-chat` 基础元素搬进 `chat-blocks.css`**（表格 / 代码 / 引用 / 列表），与语义块作用在同一批节点上 | `index.css` 删节（留指路注释） |
| 4 | **代码块与表格加壳**（纯 CSS + `ChatPre` 扩展）：语言标签 + 复制按钮；表格斑马纹 + 表头底色 | `ChatMarkerContent.tsx` / `chat-blocks.css` |
| 5 | **卡片整体复制**：`card-group` 每张卡、`caution` / `how-to` / `quote` 外壳各一个复制按钮 | `ChatCopyButton` 泛化 |
| 6 | **追问「换一批」**：新端点 `POST /api/chat/followups` + `ChatFollowUpList.onRefresh` + `chatStore.refreshFollowUps` | 后端 / 前端各三处 |

### 为什么这么设计

1. **三种新块的 type 名刻意不与产物侧重叠**（用 `caution` 而非产物侧的 `alert`、用 `how-to` 而非 `steps`）。两侧集合本就**刻意不相交**（由 `limo-blocks.json::meta` 钉住），名字撞上就等于「同一套渲染器服务两种语义」——而它们的降级判据、CSS 归宿、导出待遇都不同。
2. **视觉跟随是零 JS 改动、零中间变量层的**：`--card-*` 的初值写在 `cards.css` 的**裸 `:root`**（值 = gpt 风），`paper`（默认骨架）下 `applyCard()` 只 `removeAttribute` 两个 data 属性、**不删变量**，且 `CARD_VARS` 只覆盖 `--card-bg-base` / `--card-lift` / `--card-border` 三项。⇒ 对话侧直接 `var(--card-*)` 即可，默认拿初值、用户切方案后自动跟随，**不需要知道当前是哪套**。
3. **只跟随「容器级」属性，尺寸级保持 em**：`--card-padding` 是 `22px 26px`，那是给**宽栏长文**的值，浮窗里会把卡片撑爆；对话的间距必须跟对话字号档一起缩（`lib/chat-font.ts` 的倍率）。
4. **代码块 / 表格加壳走纯 CSS，刻意不走 `limo-block`**：标记驱动意味着「模型漏标 = 静默失效」，而加壳这件事**没有任何需要模型判断的东西**；纯 CSS 还让**全部历史消息立刻受益**。与产物侧 `table` / `code`（标记驱动）的差异是有意的——那边要保证「预览所见 = 导出所得」，这边**对话不导出**。
5. **复制按钮的落点克制**：只给有明确边界的内容卡。`info` / `digest` 内容短、复制价值低于它占的注意力，**刻意不加**（有一条反向断言守着，防「顺手补齐」把这个取舍悄悄改掉）。
6. **「换一批」端点做成无状态**（入参就是生成所需的原文，不碰 `SessionManager`）：按消息定位的两个可用坐标**在各处都不成立**——① 前后端消息 id **不同源**（前端 `nextMsgId()`，服务端 `msg_…`，一次会话进行中必然不同）；② 改用下标则多一层「前端数组与服务端数组同步」的隐含前提，而 `editMessage` / `deleteMessage` / `regenerate` 三个动作都在改它。代价是落库由前端承担（与那三个写动作同款）。
7. **失败时保留原有那批**：替换语义管的是「成功时换成新的」。生成失败还把用户手上正能用的一批清掉是**纯粹的净损失**，且与首次生成失败的形态不同（那里本来就没有可保留的东西）。
8. **`onRefresh` 缺省不渲染按钮**：产物侧（预览区笔记末尾）复用同一个 `ChatFollowUpList`，它没有可重跑的上游；默认渲染的话那边会多出一个点了没反应的控件——而**那种缺口没有任何断言会红**。

### 影响范围

- 后端：`app/core/artifact_blocks.py`（`CHAT_BLOCK_TYPES` 4 → 7）、`app/routers/chat.py`（`POST /api/chat/followups` + SSE 侧改用 `followup_payload`）、`app/agent/followup.py`（`FOLLOWUP_FAILED_MSG` + `followup_payload`）、`app/agent/prompts/formats.md`（三节 + 闸门量词）
- 前端：`lib/markdown-scan.ts`（`CHAT_BLOCK_TYPES`）、`components/ai-chat/ChatMarkerContent.tsx`、`components/ai-chat/MessageItem.tsx`、`store/chatStore.ts`、`api/chat.ts`、`styles/chat-blocks.css`（重写）、`index.css`（删 `.markdown-chat` 节）、`styles/blocks.css`（注释订正）
- 测试：新 `backend/tests/test_chat_followups.py`（16 条）；`frontend/src/test/chatBlocks.test.tsx`、`chatFollowUp.test.tsx`、`chatMarkersWiring.test.ts`、`fixtures/limo-blocks.json`

⚠️ **不改 `VERSION` / `PROMPT_VERSION`**：对话侧没有产物版本号，`formats.md` 是**对话** prompt（不是产物 prompt），bump `PROMPT_VERSION` 会让产物侧的自证头凭空变化。
⚠️ **`formats.md` 由 `PromptLoader` 单例在导入时读一次并缓存**，只改 `.md` 不重启后端**不生效**（零报错，开发期陷阱）。
⚠️ **待实机确认**（风险已知）：默认 `paper` 骨架下 `--card-bg` == `--tk-surface` == assistant 气泡底色，对话卡片与气泡**同底**、只靠 `1px solid var(--tk-border)` 分层。若观感变糟，回退 `--card-bg-base` 取值或给 `.chat-*` 单独兜底——**不改跟随机制本身**。

---

## 2026-10-12 — 联网检索审查修复：整体闸门 + 取消透传 + 死分支（同日续）

### 起因

对话侧联网落地后做的一轮代码/漏洞审查，在**产物侧新代码**里抓到几处不对称，逐条修复。

### 做了什么

| # | 问题 | 修法 |
|---|---|---|
| P2-1 | `_search_all` **无整体闸门、也收不到 `cancel_event`** —— `search_web` 是三级降级**串行链**（Tavily 15s → SearXNG 10s → DDG），各层只兜得住单次请求；「三轮都慢」时生成一直吊在这里，**用户点了取消也不动** | 新增独立常量 `SEARCH_TIMEOUT = 60.0`；`_search_all(queries, cancel_event=None)` 用 `asyncio.wait(FIRST_COMPLETED)` 同时等「搜索完成」与「取消置位」，谁先到算谁；起跑前再查一次取消 |
| P2-2 | `agent.py::_tool_summary` 的 `isinstance(data, list)` 分支**不可达**（三家 provider 恒返回 dict），且与 `_extract_sources`（只认 dict）形状判据不对称 | **删**分支（向收紧一侧对齐）；**不**反向给 `_extract_sources` 补 list 兼容 —— 形状判据要收在同一侧 |
| P2-3 | `_record_tool_result` 用 `not tc["summary"]` 配对 —— 依赖「`_tool_summary` 恒返回非空串」这个**跨模块**不变量 | 加注释点明该依赖 + 失效形态（同名 result 反复覆盖首条、后面的气泡永远空着，**零报错**） |
| P3-1 | 气泡「找到 N 条」与来源列表条数可不等（`_extract_sources` 封顶 5） | 注释定性：N 是**搜索结果总数**（工具返回了什么），5 是**展示上限**（展开列表画多长），**刻意允许不等** |
| P3-2 | 前端 `isSafeSourceUrl` 无长度上限（后端 `_safe_http_url` 有 ≤2000），注释却自称「与后端同条件」 | 前端补 `MAX_SOURCE_URL_CHARS = 2000`（与后端同值），注释措辞对齐 |
| P3-3 | 非流式 `POST /chat` 无联网开关 | **不修、加注释记一笔**：前端 `chatStore` 只导入 `streamChat`，`api/chat.ts::chat()` 与本端点**均无消费者** ⇒ 当前不构成用户可见问题；但记下「将来被 SDK / 脚本调用时，『关掉开关后不能上网』这句承诺在它上面不成立」 |

### 为什么这么设计

1. **`SEARCH_TIMEOUT` 与 `WEB_SEARCH_TIMEOUT` 刻意分成两个常量**：前者管「一次 LLM 抽词调用」、后者管「N 路并发 HTTP 搜索」。合用一个，将来为其中一件事调值时就会**静默改掉**另一件（同源漂移最典型的入口）。
2. **超时 / 取消 / 零命中三条都返回 `[]`，共用同一条下游路径** —— 对用户而言三者能采取的行动完全相同（重试 / 换材料），差别只对读日志的人有意义，故只在日志里分开记。**不新增失败态**（分类粒度按「用户能采取的行动」划分，同模式 #53）。
3. **取消 ≠ 失败**：`run_web_search` 在 `_search_all` 之后补一次取消判断 —— 否则用户自己按的取消会被报成「联网检索没有返回结果」（把用户的动作报成我们的失败）。`hits=0` 照留（自证头记的是事实）。
4. **超时不保证掐断底层请求**：DDG 走 `asyncio.to_thread`，线程取消不了；这里能保证的只是「调用方不必再等」。既有约束（导出侧同款），非本轮引入。

### 影响范围

- `backend/app/generation/web_search.py`（`SEARCH_TIMEOUT` + `_search_all` + `run_web_search`）
- `backend/app/llm/agent.py`（删死分支 + 注释）、`backend/app/routers/chat.py`（注释）
- `frontend/src/lib/chat-sources.ts`（长度上限）
- `backend/tests/test_web_search_reference.py`（34 → 39 用例，新用例各带对照实验，并逐条做过「回退修复 → 变红」验证）
- `frontend/src/test/chatWebSearch.test.tsx`（`isSafeSourceUrl` 补长度边界断言）

⚠️ **不改用户可见行为**：README / `VERSION` / `PROMPT_VERSION` 均未动（闸门只影响「多久放弃等待」，产物形态不变）。

---

## 2026-10-12 — 对话侧联网：一个开关 + 把「搜到了什么」露出来

### 起因

产物侧联网落地后，用户明确「我们后面还要进行对话侧的联网工具」。经一次 AskUserQuestion
定下两件：**加联网开关**（能在面板上关掉）与**强化结果呈现**（把「搜了什么、来源在哪」
露出来）。

现状是**一半有一半没有**：对话侧 `search_web` / `fetch_url` 一直可用，但 ① 用户**关不掉**
（想让它只查本地库做不到）；② 搜到的 URL **一条通路都没有** —— `tool_result` 只带
`refs`（库内文档），前端全程只见得着「正在搜索网络: xxx」这句状态文案。

### 做什么

| 改动 | 落点 |
|---|---|
| **开关（后端）** | `routers/chat.py::_build_registry(store, *, allow_web=True)` 整组摘掉两个联网工具；`chat_stream` 加 `web_search: int = Query(1)` |
| **开关（prompt）** | 新建一对互斥文件 `agent/prompts/web_tools.md` / `no_web.md`，`PromptLoader.build(..., web_search=True)` 二选一注入；原 `rules.md` §3 的联网措辞整节搬走（§4/§5 重编号） |
| **开关（前端）** | `lib/chat-prefs.ts` 加 `webSearch`（默认 `true`）+ `ChatInput` 的 Globe 按钮 |
| **来源（后端）** | `llm/agent.py::_safe_http_url`（白名单）+ `_extract_sources`（抽 title/url）；`tool_result` 事件新增 `sources` |
| **来源（落盘）** | `_record_tool_start` / `_record_tool_result` 两个模块级函数收口白名单：`{tool, display, summary, sources}` |
| **来源（前端）** | 新建 `lib/chat-sources.ts`（`toChatSources` / `isSafeSourceUrl` / `sourceHost`）+ `ToolCall.sources` + `MessageItem` 的 `ToolBubble` 展开列表 |

### 为什么这么设计（六条决策）

1. **一个开关管两个工具**（`search_web` **和** `fetch_url`）：两者都是「能不能联外网」，
   拆成两个开关会造出「能抓网页但不能搜」这种半开状态，而它没有任何使用场景。
2. **开关驱动两处、且必须同源**：工具表（`_build_registry`）与 system prompt 的联网层
   （`prompt_loader.build`）由**同一个 `allow_web` 局部变量**驱动。只改一处就是模式 #54
   的典型形态 —— 工具摘了而 prompt 还写着「你可以搜索互联网」（模型会去调一个不存在的
   工具），或反过来（明明能搜却被告知不能）。测试 §6 有两条接线守卫钉住这两行在同一函数里。
3. **prompt 层用「一对互斥文件」而不是占位符**：占位符要引入第二套标记解析器，而它的
   失败形态是用户看见 `{{WEB_TOOLS}}` 原文。文件读一次即缓存，代价为零。
   → 连带把 `base.md` / `rules.md` 里所有联网措辞清空（§10 的源码守卫钉住「工具名只许
   出现在那两个文件里」）—— 那两份文件**两种模式下都注入**，漏一句就等于开关只关了一半。
4. **默认值是「默认开」，与产物侧相反且都对**：产物侧 `web_search: bool = False`（**新增**
   能力 → 默认关 = 零回归），对话侧 `Query(1)` / `webSearch: true`（给**已有**能力加开关 →
   默认开 = 零回归）。判据同一条 —— **默认值必须等于改造前的行为**。连带地，参数只在偏离
   默认时才发：`follow_up` 是「开着才带 `=1`」，`web_search` 是「**关着**才带 `=0`」。
5. **`sources` 与 `refs` 是两条路，不合并**：`refs` 是**库内文档**（`{docId, title}`，点了跳
   预览区），`sources` 是**公网来源**（`{title, url}`，点了开浏览器）。形状、跳转语义、
   消费方都不同，合并成一个列表 = 前端分不出该跳哪，且两个功能同时坏（测试 §7 有对照）。
   ⚠️ 「C 端不暴露完整工具结果」那条约束**不管来源列表** —— 它管的是 `fetch_url` 的 4000 字
   正文、`lookup_doc` 的 2000 字全文；来源的 title+url 与 `refs` 已是同一种粒度。
6. **URL 白名单：后端主防线 + 前端纵深防御**：搜索结果来自公网（Tavily / SearXNG / DDG
   返回什么就是什么），直接进 `<a href>` 就是一条 `javascript:` / `data:` 通道。后端
   `_safe_http_url` 在**抽取阶段**就丢掉不合规的整条（不是留个没链接的标题 —— 标题单独存在
   无从核查）；前端 `toChatSources` 在**不可信数据的两个入口**（`api/chat.ts` 的 SSE 流 +
   `chatStore.openConversation` 的磁盘读取）再判一次。渲染层直接渲染并注明「不是忘了判」——
   判据住在入口，重复判会让它看起来像有两个真相源。

### 顺带修的两个既有缺陷

- **落盘 `tool_calls` 只存 `tool` + `display`**（连 `summary` 都丢）⇒ 流式完成后落盘的气泡
  没有摘要，而重新打开会话从 `toBackendMsg` 拿回来的却带着摘要 —— **同一个东西两种状态**。
  本轮补齐 `summary` + `sources`，并把配对逻辑抽成 `_record_tool_start` / `_record_tool_result`
  两个模块级函数（照 `_append_refs` 的先例：调用点在嵌套 async generator 里，测试够不到，
  复刻表达式正是同源漂移的来源）。⚠️ 配对判据是「**同名且尚未填过**的第一条」—— 一轮内
  同名工具被调用两次也能各归各位（有对照实验：改成「总是填最后一条」则第一条永远空着）。
- **`saveChatPrefs` 是整对象覆盖**：加第二个开关的那一刻就会「切 A 开关把 B 开关重置回默认」，
  而那件事**既没报错也没提示**（用户关掉联网、顺手开追问，联网又自己开了）。`ChatInput.tsx`
  的旧注释恰好预言过这一幕。已改为**合并式**（签名收 `Partial<ChatPrefs>`，内部
  `{ ...loadChatPrefs(), ...p }`）—— 从根上消灭这类坑，而不是在每个调用点记得带全字段。

### 影响范围

- **零回归靠「默认开 + 不关就不发参数」**（不靠取值相同）：`web_search` 只在 `=== false`
  时设成 `'0'`；`undefined` 视为默认开（与后端 `Query(1)` 同源）。
- **非流式 `POST /chat` 端点刻意不改**：它调 `_build_registry(store)` 与
  `prompt_loader.build(doc_text)` 都用默认 = 联网可用（零回归），前端也根本不用这个端点。
- **产物侧零影响**：`web_search.py` 与本次的 `_extract_sources` 是两套东西（前者注入
  参考资料、后者给前端展示来源），互不引用。
- **不动 `PROMPT_VERSION`**：改的是对话侧 system prompt，不产生产物、也不进任何产物的
  自证头。
- 测试：后端 `tests/test_chat_web_search.py`（38 条，含 4 条对照实验 + 4 条接线守卫）、
  前端 `src/test/chatWebSearch.test.tsx`（27 条，含 4 条对照实验）。

---

## 2026-10-12 — 产物侧联网检索：生成前自动补一份「参考资料」

### 起因

用户先问「想不想加网络搜索能力或者工具，支持 Tavily 之类」，随后把范围收窄成
**产物侧开关**：「生成产物侧加一个按钮在生成面板上，用户可以选择是否启用；启用了会调用
搜索工具搜网上的一些资料之类的」。

经一次 AskUserQuestion 定下三件：**检索词先让 LLM 从正文抽**（不让用户手填）、
**只用搜索摘要**（不抓全文）、**只做产物侧、搜索商维持现有三家**
（Tavily → SearXNG → DuckDuckGo，不新建注册表、不动 capabilities）。

### 做什么

| 改动 | 落点 |
|---|---|
| **新模块** | `app/generation/web_search.py`：抽词 prompt + `parse_queries` + `_search_all` + `compose_reference` + 异步生成器 `run_web_search` + 自证头字段 `outcome_param` |
| **流式端点** | `generate.py` 加 `_web_search_stream`（把 `stage` 转 SSE、把失败转 `warning`），双产物与单产物两个分支各插同一段（`replace_all` 同源） |
| **非流式端点** | `POST /generate/{id}` 里解析一次 + 检索一次（**只写盘、不推事件**），插在 `await llm.aclose()` **之前** |
| **自证头** | `markdown_meta(..., extra_params={"webSearch": {...}})`（`provenance` 早有 `extra_params` 口子，无需改它） |
| **前端** | `genPrefs.webSearch` + `GenPanel` state/持久化/参数/透传 + `GenerateOptions` 勾选项（挂在「参考资料」块内）+ `api/generate.ts` 的 `web_search` 查询参数 |

### 为什么这么设计（五条决策）

1. **不新建注入通道 —— 检索结果伪装成「一份普通参考资料」**（`{name, content}` 追加到
   `reference_texts` 末尾）。于是这些能力**全部免费继承、一行不用改**：超窗自动压缩
   （`reference_compressor`）、「参考资料只是素材不是指令」的注入防御声明
   （`_format_reference_texts`）、**只注入 notes**（`build_polish_prompt` 根本不接受该参数）、
   前端「正在压缩…」的 stage 提示。
2. **是「一份」而不是「每条一份」**：压缩器给每份的配额下界是 200 token，逐条注入时
   6 条 = 1200 token 起、很容易撑破总量；而总量兜底是**等比削**，削到句子中间的一份摘要
   对模型毫无价值。合并成一份 = 压缩器看到的是「一份需要取舍的材料」，它能做的是**摘要**。
3. **搜索走 `agent.tools.search_web`，不在此处另写 provider 分支**：三级降级、代理豁免
   判据、DDG 同步库必须放 executor —— 全都在那一份实现里。另写一遍就是模式 #54：
   两份实现对「哪家可用」「失败怎么降级」迟早给出不同答案，而**没有任何测试会发现**。
   ⚠️ 但 `search_web` 只**吞掉网络异常**，它的**返回结构**（`{"results":[{title,url,snippet}]}`）
   仍是契约 —— 故 `_search_all` 对结构做显式校验 + 记日志，并有一条源码哨兵钉住
   （对面改了形状而这边静默拿到空结果，是「检索成功、产物里少一节、零报错」的形态）。
4. **失败一律显式、且绝不阻断生成**：抽词失败 → 退化成**标题检索** + `error`；
   搜索失败/零命中 → 不注入 + `error`。流式推**专属 kind** `web_search_failed`
   （前端 `onWarning` 对它走 info toast 且**不标 partial** —— 检索没生效不等于笔记生成失败）；
   非流式只落自证头 `webSearch.error`。⚠️ `hits: 0` **必须留在自证头**：它是
   「开了检索但一条都没搜到」与「压根没开检索」之间唯一的区分点（`compact_params`
   用 `is not False` 判，数值 0 会保留）。
5. **抽词那一次调用独立超时（60s）+ 按能力位关思考**：它发生在生成**之前**，共用生成总超时
   会把整个生成拖死。思考档位复用 `followup.thinking_extra`（有 `reasoning` 能力位 → 关闭、
   否则不传）—— 抽两个检索词是纯生成、推理无产出。

### 影响范围

- **零回归靠「后端默认 `web_search=False` + 前端不勾时不传参数」**，不靠取值相同。
- **只注 notes**：单产物 `mode=polished` 时整段检索代码在外层 `if stream_mode != "polished"`
  之外 ⇒ 一次都不发生（`polished` 的自证头也没有 `webSearch` 键）。
- **不产生产物版本语义变化、不改 `PROMPT_VERSION`**：检索结果进的是**已有**的
  `<reference_texts>` 节，不是新的 prompt 规范文本。
- **不 bump 版本号、不动 `VERSION`**（未获发布授权）。
- **测试**：后端 `tests/test_web_search_reference.py`（34 条，纯函数 / 检索层 / 编排 / 端点四段，
  含 6 条对照实验与一条 `search_web` 返回结构的源码哨兵）；前端 `genParams.test.ts` 加
  `web_search` 序列化 3 条。
- **对照实验已实测**：注释掉 `params.set('web_search', …)` → 前端 2 条红；去掉 `_search_all`
  的 URL 去重 → 后端 1 条红；把单产物分支的 `stream_mode != "polished"` 破坏 → 「整理版不检索」
  那条红。

---

## 2026-10-12 — 对话浮窗的三条交互：独立字号缩放 / 回到最新 / 代码块复制

### 起因

用户：「ai 对话的那一个对话框也应该像预览区那样可以 ctrl 滚轮放大缩小，就是其他一些 agent
常见并且容易加进来的机制也可以考虑加入一下」。

拆成三层：① **主** —— 对话消息区支持 `Ctrl+滚轮` 调字号；② 顺带看有哪些「同类 agent 常见、
实现成本低」的机制值得加；③ 可上网查参考。经一次 AskUserQuestion 定下两件事：
**字号范围 = 对话独立一份**（不与阅读区共用）、**附加机制 = 「回到最新」+「代码块一键复制」**
（明确**不要**「↑ 键编辑上一条」与「Ctrl+F 对话内搜索」）。

### 做什么

| 改动 | 落点 |
|---|---|
| **对话字号缩放（独立一份）** | 新 `lib/chat-font.ts`（key `mbw_chatFont`，`applyChatFont` 写 `<html>` 的 `--chat-font-scale`）；`index.css` 加 `--chat-font-scale` 默认值 + `.chat-msg-sm/md/lg` 三条（`calc(<基准px> × var(--chat-font-scale))`）；`ai-chat/scale.ts` 的 `msg` 字段由 Tailwind 字面量改这三个类名；`store/index.ts` 模块副作用区加启动 `apply(chatFont)` |
| **滚轮监听** | `MessageList` 的滚动容器挂**原生** `addEventListener('wheel', …, { passive: false })`（照抄 `PreviewArea` 的既有写法与理由） |
| **「回到最新」浮层按钮** | 同文件加 `atBottom` state + 右下角 `ArrowDown` 按钮，点击 `scrollTo({ behavior: 'smooth' })` |
| **代码块一键复制** | `ChatMarkerContent` 的 `ChatText` 加 `components={{ pre: ChatPre }}`；新增 `ChatPre`（`useRef` + `textContent` 复制、hover/focus 悬浮按钮、流式中隐藏） |

### 为什么这么设计（三条决策）

1. **字号「独立一份」而不是与 `--read-font-size` 共用**：两者是不同宿主上的不同排版 ——
   预览区是宽屏长文（基准 16px + 版宽/行高/段式一整套），气泡是窄浮窗里的短回复
   （基准 13–15px，还随浮窗宽度切 sm/md/lg 三档）。共用一个倍率意味着「调大阅读字号」
   会把气泡一起撑大，反之亦然 —— 而这对用户是两件不同的事（阅读要疏朗、对话要紧凑）。
   ⚠️ **档位换算与区间仍复用 `reading-prefs.nextFontScale` / `FONT_SCALE_RANGE`**（`toBe`
   身份断言钉住）—— 共享的是「倍率怎么算」，分家的只是「算出来写给谁」。
   ⚠️ 与 `chat-prefs.ts`（`mbw_chatPrefs`）**刻意不合并**：那一个控制「下一次请求带什么
   参数」，是纯瞬时读取值、不写 DOM；字号是「正文怎么显示」，与 `card-prefs` / `marker-prefs` 同族。
2. **「回到最新」按钮的显隐与自动跟随同源**（同一个 `< 40px` 判据、同一个 `onScroll` 里更新）
   —— 各写一套判据就会在阈值附近互相矛盾（按钮在说「你不在底部」而自动跟随认为在）。
   按钮用 `opacity` 而非 `hidden` 显隐：后者会让代码块/内容在 hover 时高度跳动。
3. **复制按钮挂 `pre` 而不是 `code`，且不复用产物侧的 `CodeBlock`**：`react-markdown` v10
   已移除 `inline` prop，块级是 `pre > code`、**行内是裸 `code`** —— 挂 `code` 会给每个
   `` `x` `` 也配一个复制按钮。而产物侧 `markdown-components.tsx::CodeBlock` 带
   `SyntaxHighlighter`（语言注册 + 主题）+ 标题栏 + 行号，是**产物**的渲染路径，挂进来
   = 把全部历史消息的代码块换成另一套观感（与 `ChatMarkerContent` 文件头那条「不复用
   `preview/MarkerContent`」是同一类静默回归）。这里只要「原样的 `<pre>` + 一个按钮」，
   于是代码文本就是 `textContent`，不必从 React children 递归提取。

### 影响范围

- **零回归是「默认值 = 现状」**：`CHAT_FONT_DEFAULTS.fontScale = 1.0`，且 `--chat-font-scale`
  的 `:root` 默认值也是 `1` —— 没调过的用户看到的字号与改造前逐字相同。对话侧的
  `input` / `btn` / `icon` / `avatar` 等档位字段**一律不动**（只 `msg` 一个字段改了取值）。
- **键盘路径不动**：`Ctrl+=` / `Ctrl+-` / `Ctrl+0` 仍只调阅读字号（用户拍板「对话独立一份」，
  键盘是「阅读」这条链的入口）。对话字号只有滚轮一条入口。
- **不进导出**：三者全在对话渲染路径，产物/导出侧零改动。
- **测试**：新增 `chatFont.test.ts`（13 条，含「两份字号互不干扰」的双向断言）与
  `chatMessageList.test.tsx`（14 条，含「连滚两次真的再进一档」「行内代码不配按钮」
  两条对照实验）。既有的 21 个测试域全绿。

### 明确不做

- 「↑ 键编辑上一条」与「对话内搜索 Ctrl+F」（用户在这轮问答里未选）
- 设置页暴露对话字号（滚轮已够；少一个入口就少一处漂移）
- 会话列表 / 输入框的字号缩放（只作用于消息区 —— 那是「读」的地方）

---

## 2026-10-11 — 内置模型预设表更新（48 → 58 条）

### 起因

用户：「更新一下模型表」。先跑了 `scripts/check-models.py`（数据源 models.dev）作为**问题清单**，
再逐条去官方页核对——**脚本的产出是「该打开哪一页」，不是答案**（本轮又一次印证：
它报的 `qwen-turbo` 差 7.6 倍，打开官方页一看是它自己混进了 Qwen2.5-Turbo 的 1M，我们的
131072 是对的）。

### 做什么

| provider | 改动 | 依据 |
|---|---|---|
| `anthropic` | **+3**（`claude-fable-5-1` / `claude-opus-5-5` / `claude-sonnet-5-5`，均 1M）；**订正 2**（`claude-sonnet-4-6` / `claude-opus-4-8`：200000 → **1000000**） | 官方模型总览页（2026-10-11 抓取）：**4.6 及以后全系 1M / 128K 出**，且 1M 窗口内**无长上下文附加费**（此前填的是 4.5 时代口径）。`haiku-4-5` **保持 200000**——它仍是在售最低档，Haiku 5.5 官方口径尚不一致，不预设未 GA 的型号 |
| `openai` | **+3**（`gpt-6-astra` / `gpt-6.1-sol` / `gpt-6-luna`，均 1050000） | 官方 models 页（同上）。⚠️ 与 Claude 不同：**超过 272K 输入有长上下文附加费**（整单 2× 输入 / 1.5× 输出），已写进注释 |
| `google` | **+1**（`gemini-3.8-flash`，1048576） | 官方 latest-model 页（同上）：GA 稳定名、**无 `-preview` 后缀**。⚠️ 它是 2 的幂口径（1MiB），与 dashscope 的 1000000 **是两种口径，已注明「不要互相抄」** |
| `dashscope` | **+1**（`qwen3.8-omni-flash`，1M） | 官方 `/qwen3-8-omni-flash`（同上）：2026-09-17 发布，输入 text/image/audio/video、**输出仅文本** |
| `zhipu` | **+2**（`glm-5.3` / `glm-5.3-flash`，均 1M） | 官方 `zhipuai.cn/zh/news/163`（同上）：5 系首个原生多模态是 Flash 那条；⚠️ 多来源把「128K」说成上下文，**实为最大输出**那一列（同 dashscope 的坑） |
| `docs/qianwen-models.md` | 修正一句**已过时且与代码不符**的话 | 原文「内置预设 `context_window` **保守设为 131072**」是 2026-10-02 之前的旧口径，而 config 里 dashscope 早已逐条实测（多为 1M）；同句「kimi-k3 未内置预设」也已不成立（它在 `moonshot` 里） |

**覆盖率**：`check-models.py` 从 48 条 / 26 条有记录（54%）→ **58 条 / 36 条（62%）**。

### 为什么这么设计（这一轮的取舍）

- **能标的才标，不能标的一律不标**（能力位铁律）：
  - `qwen3.8-omni-flash` **只标 `image` / `video`** —— `audio` 在 LiMo **没有对应能力位**（只有
    text/image/video/document/reasoning）；`document`（PDF 直传）**刻意不标**：那份名单是
    2026-09-23 逐条查证后的**收窄名单**（只有 `qwen3.8-max` / `flash` / `27b`），**同代 ≠ 同名单**；
    `reasoning` **也不标**：它确有 thinking 模式，但那走 DashScope 自有参数，认不认 OpenAI
    兼容层的 `reasoning_effort` **未查证**。
  - `glm-5.3` 两条同理**不标 `reasoning`**（官方说「思考始终开启、low/high/max 三档」，
    但参数名与兼容层是否透传未查证）。
  - `gemini-3.8-flash` **不标 `document`** —— 页面写「支持 PDF 输入」不等于兼容层认 `file` 块，
    理由见 `services/doc_understand.py` 的渠道表。
- **按既有风格保留旧模型、不做静默删除**：Claude 4.6/4.8、GPT-4o 系都留着（老用户已启用的配置
  不该凭空消失），只在**确认下架**时才标 `retired`。
- **`zhipu` 新增条目不写 `capabilities` 字段**：与同段既有条目（`glm-5.2` / `glm-4.7`）保持一致，
  避免在「未查证能力位」和「风格一致」之间只挑一边。

### 待核（本轮**刻意没改**，别当成遗漏）

| 项 | 为什么不动 |
|---|---|
| `qwen-vl-ocr-latest` **可能已于 2026-10-10 下线** | 搜到一条下线通知（蚂蚁数科 DTMaaS 平台），但阿里云**计费页仍列着它**（0.3 元/M）——**两条证据冲突**。⚠️ 它是 **OCR 默认绑定模型**（未绑定时自动绑），动它等于动功能行为，**必须由官方页确认后再改**（或直接问用户） |
| `qwen3.8-27b` / `qwen3.5-ocr` / `qwen3-vl-flash` 在 models.dev「查无」 | 这四条都是 2026-10-02 **逐条打开官方页确认过**的；models.dev 覆盖率仅 54%、**缺口正落在最新/最冷门模型上** —— 「查无」不构成下架证据 |
| `gemini-3.1-pro` 同样查无 | 同上，无其他证据，不动 |
| `qwen-turbo` / 4 条 MiB 口径差 / `glm-4.7` 的 204800 | 前者已核实官方就是 131072（**脚本错**）；后几条是 1000000 vs 1048576 的 2 的幂口径差，**不是错** |
| 价格 / `max_tokens` 字段 | 项目既定决策：预设不填价格（预置模型看不到费用）、`max_tokens` 硬编码 65536 不分模型 |

### 影响范围

- **零代码逻辑改动**：只动 `DEFAULT_CONFIG["llm_models"]` 数据。既有用户配置经
  `_sync_builtin_models` 增量补入新条目（**不覆盖**用户已改过的字段）。
- 设置页/`capabilities` 的模型列表变长（+10），新增条目的上下文窗口按上表取值。
- 守卫：`tests/test_check_models.py`（`PROVIDER_MAP` 同步）+ `test_config_io.py`（`llm_models`
  类型守卫）等 **69 条全绿**。
- ⚠️ **本条目不含版本号变更**（那是 `sync-version.py` 的事，不因「改了数据」自动发生）。

---

## 2026-10-11 — 追问那一次调用：思考档位按模型分两支（有档位的关闭，没档位的不传）

### 起因

用户两条：

> 「关于追问的思考强度直接关闭或者是开最低」
> 「其他模型的如果有的话就搞最低没有的话就默认」

合起来即：**按模型分两支** —— **有思考档位的压到最低，没有档位的走默认（什么都不传）**。

### 做什么

| 位置 | 改动 |
|---|---|
| `app/llm/service.py` | 新增模块级常量 **`DISABLE_THINKING_EXTRA = {"thinking": {"type": "disabled"}}`** —— 「关闭思考」的 extra_body 形状**全仓唯一一份**；`stream()` 里的 `if disable_thinking:` 分支改为 `_extra.update(DISABLE_THINKING_EXTRA)`（形状一字不变，只是搬了家） |
| `app/llm/__init__.py` | 导出 `DISABLE_THINKING_EXTRA`（加进 import 行与 `__all__`） |
| `app/agent/followup.py` | 新增模块级判据 **`thinking_extra(llm)`** → 有档位返 `DISABLE_THINKING_EXTRA`，无档位返 `None`；`_run_followup_llm` 的 `create(...)` 加 `extra_body=thinking_extra(llm)` |
| `tests/test_followup.py` | 新增 3 条：有档位关闭且**不传**档位（含 `is` 身份断言）／无档位一个字段都不塞／**源码守卫**（`'"type": "disabled"'` 全仓只该出现在 `llm/service.py`）；`FakeLLM` 加 `supports_reasoning` 参数 |
| `tests/test_artifact_followup.py` | 同一条判据在**产物侧**再钉一次；替身改为记录**完整实参**（`followup_calls`），既有断言仍读 `followup_prompts` |

**判据是模型能力位 `reasoning`**（= 认 `reasoning_effort`），内置预设里**只有 DeepSeek V4
两兄弟**带它 —— 与项目既有原则一致（能力位由**模型**标注，不按 provider 判断）。

### 为什么这么设计

- **「有档位的搞最低」落地成「关闭」而不是 `reasoning_effort="low"`**：两者在服务端
  **互为替代**（同传被校验拒绝），而「最低档」的**彻底形式**就是关闭。依据是分类管道的
  实测教训 —— `low` 照样可能把整个输出预算烧在推理链上（90 篇时正文 0 字符、`finish=length`，
  见 `classifier.py` 的 `CLASSIFY_BATCH_SIZE` 注释）：**降档救不了，关掉才行**。
- **追问恰好是「关掉无损失」的那类任务**：它是「把读过的内容列成几个问题」——**纯生成、
  推理无产出**，而代价是双份的：token 预算 + **首 token 的等待**（用户刚看到 `done`，
  这张卡越晚到越像卡住）。
- **没档位的模型**（qwen / gpt / claude…）**不传**：它们本就没有「思考强度」这个概念，
  塞未知字段通常被忽略，但**没有的东西不该假装有**；而且 `None` 逐字等于改造前的行为
  （**零回归**）。
- **为什么把形状抽成 `llm/service.py` 的模块级常量**：`_run_followup_llm` **直调
  `client.chat.completions.create`**，走不到 `LLMService.stream()` —— 那里 `disable_thinking=True`
  的机制**够不着它**。把形状抄一份过去正是模式 #54（一份判据住多处，改一处漏一处，
  且两处行为分叉时**都不报错**）。`test_followup.py` 的源码守卫就是在钉这一点。
- **为什么把判据抽成 `thinking_extra(llm)` 函数**：让测试**直调真实实现**，而不是在测试里
  复刻 `DISABLE_THINKING_EXTRA if llm.supports_reasoning else None` 这个表达式（同
  `should_attempt` 的纪律 —— 复刻的表达式正是「改了一处、另一处还在旧假设上」的来源）。

### 测试

- `pytest tests/test_followup.py tests/test_artifact_followup.py` → **82 passed**（原 78 + 4 新）
- `python tests/test_effort.py` 全 PASS（`disable_thinking` 的 canonical 形状与
  `reasoning_effort` 互斥两条既有断言仍绿 ⇒ `_extra.update()` **没有改变形状**）
- **对照实验 1**：`thinking_extra` 改成无条件返回 `DISABLE_THINKING_EXTRA` →
  「无档位的模型一个字段都不塞」两条红 ✓
- **对照实验 2**：调用点换成手写字面量 `{"thinking": {"type": "disabled"}}` →
  **4 条红**（两条身份断言 + 无档位那条 + 源码守卫）✓；还原后 82 passed，
  且 `grep -c '"type": "disabled"' app/agent/followup.py` = **0**（副本清零）

### 影响范围

- **产物字节零变化**：只改**调用参数**、不改发给模型的正文 prompt ⇒ **不 bump
  `PROMPT_VERSION`**（同 `_run_followup_llm` 的既定纪律）。
- **无档位模型逐字零回归**（`extra_body=None`）；**有档位模型**（DeepSeek V4）的追问
  从「默认开启思考」变成「关闭思考」—— 这正是本轮要的效果（更快出卡、更省 token）。
- **对话侧与产物侧同时生效**（两侧共用 `_run_followup_llm`，一处改两条路都得）。
- **文档**：`prompts-inventory.md` §9.6 的「参数」行 + 附录 B 温度表（新增「追问」一行，
  它是**唯一不经 `LLMService`** 的调用）+ 附录 A 清单行补 `thinking_extra`。

---

## 2026-10-11 — 产物侧追问卡片：生成前勾选，笔记末尾长出可点的问题

### 起因

上一条把**对话侧**的追问升级成了「用户可选 + 选了必定有」。用户随即提了三条：

> 「有个问题就是按钮你放在那不应该和生成图表那些的话一起吗」
> 「其实除了 ai 对话的，生成产物侧也可能需要做这个」
> 「就是用户生成之前是否勾选生成追问卡片，剩下的应该和我说的之前的机制差不多」

即：**勾选项该在生成面板的「笔记格式」区**（与「图表」同排），而不是只长在对话输入框上；
产物这一侧同样需要。**机制照搬对话侧**（独立第二次调用、失败显式、独立存放），
**落点全新**（产物有版本、有导出、有文档库 —— 对话那些约束一个都不适用）。

### 做什么

一个勾选项 + 一次独立调用 + 一个旁路文件。

| 位置 | 改动 |
|---|---|
| [agent/followup.py](../backend/app/agent/followup.py) | 把 8 条条款抽成 `FOLLOWUP_RULES`（两个 prompt 共用，避免「改一条要记得改两处」）；新增 `ARTIFACT_FOLLOWUP_PROMPT` / `build_artifact_followup_prompt` / `_run_followup_llm`（超时 / 不 aclose / 取消不发起 / 吞失败 —— 两侧共用）；`generate_followups` 改为薄封装 + 新增 `generate_artifact_followups`。⚠️ **与对话侧同文件**：分家会把「共享条款与解析层」重新变成跨目录的两份 |
| [generation/prompt.py](../backend/app/generation/prompt.py) | `AVAILABLE_OPTIONS` 七类各加 `"followup"`（`xlsx` 保持 `[]`）；**刻意不进 `_ALL_FORMAT_FNS`**（见下） |
| [storage/store.py](../backend/app/storage/store.py) | `markdown_followup_path()` + `save_artifact_followups()` + `read_artifact_followups()`；`get_markdowns_by_transcript` 逐版搭车读（**非空才给键**）；删掉 `save_markdown` docstring 里已作废的「追问（未来）→ 覆盖 v{N}」一行 |
| [routers/generate.py](../backend/app/routers/generate.py) | `_do_followup`（返回 payload）+ `_emit_followup`（payload → SSE 行），**三条路径共用**：双产物插在三个 `done` 出口汇到的统一收尾、单产物插在 `yield done` 之后、非流式插在 `finally: aclose()` **之前** |
| [genPrefs.ts](../frontend/src/components/gen-panel/genPrefs.ts) / [GenPanel.tsx](../frontend/src/components/gen-panel/GenPanel.tsx) / [GenerateOptions.tsx](../frontend/src/components/gen-panel/GenerateOptions.tsx) | `formatFollowUp` 状态 + 持久化 + `formatStr` 拼装 + 勾选项（**两处**「笔记格式」区各一行，`isPolishedOnly` 时 disabled 并说明「追问仅笔记产出」） |
| [api/core.ts](../frontend/src/api/core.ts) / [store/types.ts](../frontend/src/store/types.ts) / [lib/markdownMeta.ts](../frontend/src/lib/markdownMeta.ts) | `MarkdownView.followUps` / `MarkdownMeta.followUps` / `toMarkdownMeta` 一行透传 —— **只改这一处，8 个调用点全部自动获得**（那正是该文件存在的理由） |
| [api/generate.ts](../frontend/src/api/generate.ts) | `onFollowUp` callback + `data.type === 'followup'` 分支，**必须排在 `else if (data.mode)` 之前**（见下） |
| [ai-chat/ChatMarkerContent.tsx](../frontend/src/components/ai-chat/ChatMarkerContent.tsx) | `ChatFollowUpList` 加两个**可选** prop（`label` / `onSelect`），缺省时行为逐字不变 ⇒ 对话侧零回归 |
| [preview/PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx) | 笔记 tab 末尾挂 `ChatFollowUpList`；`onSelect` = 打开 AI 浮窗 + **显式带 `docRefs`** + 发送 |

### 为什么这么设计

**① 追问由独立第二次调用产生，不新增产物侧 `limo-block` 类型。** 三条理由，逐条都独立成立：
（a）「勾了必定有」是**代码承诺**，模型自主写块是概率、且失败静默（用户分不清是没写还是坏了）；
（b）产物侧九种块判的是「**这段内容该不该被强调**」，而追问是**用户开关驱动**的 ——
性质不同，混进来只会让那份「无条件注入的块规范」再长一截；
（c）追问不进正文 ⇒ **导出这一整类问题不存在** —— `.md` / 纯文本 / 复制 / Word / PDF
一条链路都不用改（对话侧那条「导出要剥离追问」的教训在这里直接消掉，不是再解一次）。

**② 旁路文件 `{doc_dir}/notes/v{N}.followups.json`，与 `v{N}.md` 并排。**
四个候选落点逐个排除：**写进正文** → `.md` 导出会把候选问句带走；**进 `index.json`** →
违反「头是真相、index 只是缓存」（删一次 index 重建就丢）；**进自证头** → `.md` / `.html`
导出**刻意保留**自证头，一样带走。旁路文件四项全免：**版本天然跟随**（v2 有 v2 的）、
**删文档天然清理**（跟着 `rmtree` 走）、导出一行不改、且 `get_markdowns_by_transcript`
**本来就要逐版读盘**，读它是搭车。

**③ 推在 `done` 之后，且不吃生成预算。** `asyncio.timeout(_gen_timeout(...))` 只包住
**流式循环**，不包落库与收尾；`parseSSEStream` 读到 reader 关闭才 `break`（不因 `done`
类型停止）⇒ 晚推收得到，而 `done` 一到工具栏即可用，用户不必盯着「还在生成」等那几秒。
**先写盘、再推事件**（顺序不可颠倒 —— 反过来是「界面有卡、刷新后没了」）。

**④ 登记为 format 项，但 prompt 层零注入。** `AVAILABLE_OPTIONS` 必须登记（它是
`genOptions` 的唯一来源：不登记则勾选项根本不显示，且 GenPanel 提交时会
`.filter(f => availableFormats.includes(f))` **静默抹掉**这个值 —— P0-01 的成因）；
但**刻意不进 `_ALL_FORMAT_FNS`**：`_build_hard_constraints` 里 `if not fn: continue`
会对它静默跳过，**这正是要的**（追问不由 prompt 驱动）。两处都写了「不是漏了」的注释。
⚠️ **已知副作用（接受并记录）**：`format_list` 会喂给 `markdown_meta(..., formats=)`
⇒ 自证头 `generated.params.formats` 里会多出 `"followup"`，读它的人可能以为「模型真被
要求产出追问」。接受（它确实登记成了一个 format 项），并在
[prompts-inventory.md](prompts-inventory.md) §9.6 写明「登记但零注入」。

**⑤ 复用 `ChatFollowUpList`，不建第二份 chip。** 加两个**可选** prop
（`label` / `onSelect`），chip 的 `onClick` 变成
`onSelect ? onSelect(q) : sendMessage(q)` —— 对话侧那个「裸 chip 点了直接发」的行为
逐字保留，产物侧则走自己的入口（**必须显式传 `docRefs`**：对话气泡里那个刻意不传，
它是当前会话的延续；而这里是从预览区发起，「就这篇文档发问」才是真实意图）。
⚠️ 残留风险（如实记录）：`.chat-followup-*` 用 em 单位、宿主本是对话气泡，挂进正文流后
会跟随 `--read-font-size` 缩放 —— **实机观感待确认**，若明显不协调则抽公共变量层
（**不是**新增第二份 `.chat-followup-*` 副本）。

### 两处「静默失效」的守卫（本轮最容易踩、且都不报错）

**`api/generate.ts` 的分支顺序。** 本事件的 payload 恰好带 `mode`（产物侧要按版本落位），
而 `else if (data.mode)` 那行的本意是「带 mode 的**正文 chunk**」—— 顺序写反，追问就被
当成正文 chunk 塞进笔记（`text` 缺失 ⇒ 空 chunk），**零报错**。既有的 `stage` / `reasoning`
两支注释里已写明同款陷阱，照它写在前面。

**`refreshSaved` 与 `followup` 事件的竞态。** `done` 一到就会 `refreshSaved('notes')` →
`getMarkdown` → `setMarkdownMeta`；若该 fetch 在 `followup` 事件**之后** resolve，会把刚
写进去的 `followUps` **抹掉**（卡片闪过即消失）。用一个 `followUpsRef` 做双向合并：
事件里先写 ref 再写 meta，`refreshSaved` 写 meta **之前**把 ref 里的值补回去。

### 顺带修的一个测试隔离缺陷（本轮实测踩到）

后端新测试**单跑 22 条全绿、放进 `tests/` 全量跑则 8 条红**，且红的形态是「追问返回空」
而非断言写错。根因不在被测代码：`generate.LLMService` 是**模块级共享全局**，
`test_generate_gate.py` 等文件也在**模块级**替换它，而 pytest 在**收集阶段** import 全部
测试模块 —— 每个文件都在 import 时执行 `G.LLMService = <自己的假类>`，**最后 import 的
那个赢**。故替换必须放进 function-scope `monkeypatch` fixture（用例结束自动还原）。
⚠️ 这类缺陷的形态值得记住：**它不是「假绿」而是「单跑绿」** —— 局部看一切正常，
只有把整个套件跑起来才现形；反向也一样成立，任何"只在全量下红"的用例都该先怀疑全局污染。

### 测试

- 后端 [tests/test_artifact_followup.py](../backend/tests/test_artifact_followup.py) **22 条**
  （`def test_*` 形态 —— 本仓另有一批顶层裸断言的脚本式测试，在 `pytest tests/` 下
  **收集 0 个、静默空跑**，新增一律不用那个形态）。覆盖：勾选落盘 + 内容一致 / 不勾对照 /
  composite 与 polished-only 不产 / 失败显式报错不阻断 / 事件推在 `done` 之后 /
  **先写盘再推事件**（流未读完时断言）/ 读取层**有才给键**（缺 ≠ 空数组）/ 坏文件不抛 /
  prompt 带标题且已剥 `limo-block` / 两侧共用同一份条款常量（源码守卫）/ 不 aclose llm /
  异常与超时返回 `None` / 取消置位不发起 / 双产物挂在 **notes 那一版**（先造出 polished v2
  再验，防「碰巧取对」）/ 双产物单路失败仍产出（三个 `done` 出口）/ notes 失败不抛
  `NameError` / `_ver == 0` 显式报错且不落盘 / 非流式同样写盘且插在 `aclose` **之前**。
  **5 条对照实验全部如期变红**（去 mode 过滤 / 勾选判据恒真 / 追问挪到 aclose 之后 /
  不写盘 / 追问挪到 done 之前）。
- 前端 [src/test/artifactFollowUp.test.tsx](../frontend/src/test/artifactFollowUp.test.tsx) **22 条**：
  SSE 分发（走 `onFollowUp` 且**一个 chunk 都不产生**）/ **分支顺序的源码守卫**（按文本
  index 比较）/ `toMarkdownMeta` 透传（并钉住 **`[]` 与 `undefined` 必须可区分**，不合并）/
  可见性（**最小构造**，不用整表镜像）/ 勾选项渲染与 `isPolishedOnly` 禁用 / `ChatFollowUpList`
  的两个新 prop + **对话侧零回归**两条。
  **2 条对照实验如期变红**（分支挪到 `data.mode` 之后 / chip 改成无条件 `sendMessage`）。
- 六项门禁（tsc / oxlint / vitest / pytest / check-cli / check-doc-refs）**全绿**。

### 影响范围与已知取舍

- **编辑正文后追问仍基于编辑前的正文**：`PUT /markdown/{id}` 就地覆盖 `v{N}.md` 时
  `v{N}.followups.json` 保留旧内容。取舍：追问是「当时生成的建议」。
- **关掉勾选重新生成 → 新版本无追问、旧版本仍有**（旁路文件按版本各存）。这是**预期语义**，
  与前一条是两类不同现象。
- **多花一次计费调用**：勾了就必然发生（是承诺的一部分）。
- **产物侧追问不做**：整理版原文（用户选定只 notes）、重新生成 / 「换一批」、「首次打开时现算」。
- **`_ver` 取自 `list_markdown_entries` 按 `mode == "notes"` 过滤降序第一**：`save_markdown`
  **不写回** `mv.version`（它只写进文件头 `identity` 与 index），不能读 `mv.version`；
  不过滤则双产物（两条几乎同刻写入、版本号相同）可能取到 polished 那版，**且不报错**。
- **导出侧零改动**：导出只读 `.md` 正文，追问天然不在其中；`docs/export-pipeline.md`
  核对后确认无落点（本条即那个结论的出处）。

---

## 2026-10-11 — 追问卡片：用户可选开关 + 选了必定有

### 起因

对话侧已有的四类回答形态卡里，用户点名要做 `follow-up` 这一类，但提了三条它当时不满足
的要求：「我想把它做成用户可选，如果选择了一定会有，这个是交互式卡片」。

核查后「交互式」**本来就有** —— [ChatMarkerContent.tsx](../frontend/src/components/ai-chat/ChatMarkerContent.tsx)
的 chip 点了直接 `sendMessage(q)`。缺的是另两条：「用户可选」（写不写只能靠模型自主
决定）与「选了必定有」（模型自愿 ⇒ 遵循率是概率，**而失败还是静默的**：用户开了开关
什么都没看到，分不清是模型没写还是功能坏了）。用户自己在设计稿里写过「这个功能能不能
生效**取决于模型愿不愿意用，代码保证不了**」—— 本轮把这句话消掉。

**这不是「补一个漏掉的功能」，是「把有条件的自主行为升级为可承诺的能力」。**

### 做什么

一个开关 + 一次补调。

| 位置 | 改动 |
|---|---|
| [agent/followup.py](../backend/app/agent/followup.py)（新） | `FOLLOWUP_PROMPT` + `digest_answer` / `build_followup_prompt` / `extract_followup_items` / `has_usable_followup` / `should_attempt` / `generate_followups`；常量 `FOLLOWUP_TIMEOUT=60` / `MAX_QUESTIONS=6` / `MIN_QUESTION_CHARS=4` |
| [core/artifact_blocks.py](../backend/app/core/artifact_blocks.py) | 新增 `has_block(md, type_name)` —— 在既有 `has_blocks`（只判有无）之外补**按类型**的判据，复用 `parse_blocks`（**不写 `"follow-up" in md` 子串判**：正文里讨论本协议时也会出现这个词） |
| [routers/chat.py](../backend/app/routers/chat.py) | `follow_up` query 参数；stream 循环里记 `saw_error` / `cancelled`；循环后按 `should_attempt` 补调，**先落库、再推 `followup` 事件**；完成日志加 `blocks` / `followups` 两个计数（本轮唯一不靠肉眼判断的模型行为信号） |
| [agent/session.py](../backend/app/agent/session.py) + [core/types.py](../backend/app/core/types.py) | `append_assistant_message(..., follow_ups=)`；`ChatMessage.to_dict()` 白名单加 `follow_ups` |
| [lib/chat-prefs.ts](../frontend/src/lib/chat-prefs.ts)（新） | `ChatPrefs { followUp }`，localStorage `mbw_chatPrefs`，**默认关** |
| [api/chat.ts](../frontend/src/api/chat.ts) | 六个位置参数收成 `StreamChatOptions` 对象 + `onFollowUp` callback + `ConvMessage.follow_ups` |
| [store/chatStore.ts](../frontend/src/store/chatStore.ts) | `ChatMsg.followUps` / `followUpError`、`patchBot(id, fn)`、`toBackendMsg` 透传、`openConversation` 映射 |
| [ai-chat/ChatMarkerContent.tsx](../frontend/src/components/ai-chat/ChatMarkerContent.tsx) | 拆出 `ChatFollowUpList`（纯渲染 + 折叠），`ChatFollowUp` 改为「解析块内容 → 调 List」 |
| [ai-chat/MessageItem.tsx](../frontend/src/components/ai-chat/MessageItem.tsx) | 气泡内部末尾挂 `ChatFollowUpList` / 失败小字 |
| [ai-chat/ChatInput.tsx](../frontend/src/components/ai-chat/ChatInput.tsx) | 发送行加开关（lucide `MessageCircleQuestion`） |
| [styles/chat-blocks.css](../frontend/src/styles/chat-blocks.css) | `.chat-followup-label` 由 `<div>` 变 `<button>` → 补一串 UA 默认值覆盖（`font: inherit` 等，否则字体族与字重回落到按钮默认）+ `:hover` 反馈 |

### 为什么这么设计

**① 「一定会有」靠独立第二次调用，不靠 prompt 强制。** 遵循率给不出「一定」，且失败
是静默的 —— 正好违反项目的失败显式化原则。也不是「无条件调用」：会与模型自主写的块
**同时**出两张卡。业界主流的 Dify `suggested_questions_after_answer` 走的就是这条独立
调用的路。

**② 判据是「有块 **且** 块里抽得出问题」（`has_usable_followup`），不是「有块」。**
模型写了个空壳块（有标记、块内没有列表）时，「有块」会让后端跳过补调，而前端
`parseFollowUpItems` 抽不出项、整块退化成普通正文 → **开了开关却一张卡都没有**（正是
① 要消灭的那种静默失败）。故后端判据必须**比前端更严格或等价**才安全 —— 这是一条
**单向包含**，**不是**「两侧对同一输入给同一批项」的双向一致（后者不成立：服务端多做
了去重 / 过短 / 截断，见 ④）。

**③ 事件推在 `done` 之后。** `parseSSEStream` 读到 reader 关闭才 `break`（不因 `done`
类型停止），所以晚推收得到；而 `done` 一到 `streaming` 就已复位、输入框立即可用，用户
不必盯着「还在生成」等那几秒。⚠️ `done` 有**四处**发出（取消 / 错误 / `CancelledError` /
正常走完），只有「正常走完且未取消」才补调 —— 五条准入判据收口在 `should_attempt`
（抽成模块级函数是为了让测试**直调真实实现**，而不是在测试里复刻那五个条件）。
⚠️ **本设计有一个隐含前提必须写下来**：[api/chat.ts](../frontend/src/api/chat.ts) 那个防
「连接意外断开」的 `receivedDoneOrError` 守卫在 `done` 到达时就已置真 ⇒ 追问事件晚到
**不会**被误报成断线。将来若有人把它改成「以流关闭为准」，本设计会当场变成「每次追问
都多弹一条连接错误」。

**④ 追问建议独立存放，绝不写进 `msg.content`。** 走 SSE 事件 → `ChatMsg.followUps` →
会话消息 `follow_ups` → chip，与正文完全分离。于是「导出要剥离追问」这一整类问题
**从根上不存在**：导出只读 `content`，「被采纳的追问」是普通 user/assistant 消息、天然
按正常文章排布（正是用户要的），「没被采纳的候选」压根不在产物里。

**⑤ 展示形态：气泡内部末尾 + 默认展开 + 可折叠。** 位置在正文内容块之后、操作栏之前
—— 视觉上是回答的**收尾**，不是挂在气泡外的独立 UI。默认展开：「选了一定有」的承诺
意味着用户要看得到；折叠是给「这一轮不关心」用的。折叠状态**刻意不持久化**（瞬时的
阅读偏好，组件本地 state）。

**⑥ 数量 4～6 个 + 反凑数条款。** 放宽到 6 是**与折叠配套**（能收起就不占地方），且
上限与前端既有 `MAX_FOLLOW_UPS = 6` 对齐（不用动前端常量）。**反凑数**（prompt 第 8 条）
是与放宽**直接配套**的对冲：上限一松，最容易被换来的就是模型为凑数编问题 —— 而凑出来
的（复述性 / 客套性 / 预设结论）恰恰是最该避免的那类。下限同时从「3～4」松成「有没有
3 个都行」：**「有」由代码保证，不该由数量保证**。

**⑦ 追问的追问天然成立。** 补调发生在**每一轮**流结束（`sse_stream` 的通用收尾），所以
点 chip 产生的那一轮回答末尾一样会补追问，可以无限追下去；chip 点击走 `sendMessage(q)`
→ `dispatchSend` 读 `loadChatPrefs().followUp` → 新请求照带 `follow_up=1`。**刻意不设
「连续 N 轮后停止」** —— 用户选的就是「一定会有」，限制它等于把承诺打折。⚠️ 正因为它靠
「机制天然成立」而非专门代码，实机必须**走两轮亲验**。

### 顺带记录在案（本轮不改）

- ⚠️ **模型自主写的追问块在导出里是一段裸无序列表** —— `strip_blocks` 剥的是**标记行、
  保留内容**（自述供 md / 纯文本导出用），所以那几条候选问题会以无标记、无 chip、无上
  下文说明的形式出现在导出的 `.md` 里。这是**本轮之前就有的**隐藏偏差（「预览看到
  chips、导出看到一串问题」），本轮**不动 `strip_blocks` 对 follow-up 的行为**（改它要
  触及它对全部四种对话块的现有语义），但**写进文档**，不装看不见。
- ⚠️ **`deleteMessage` 在追问链上是个陷阱**：追问链第 3 轮基于第 2 轮的回答生成，摘掉
  第 2 轮 ⇒ 第 3 轮在回答一个**已不存在的问题**（界面上读成「AI 答非所问」）。**追问链
  上的清理走截断类**（`regenerate` / `editMessage(user)`），不走删单条。⚠️ 这与
  `clear_context_at` / `last_compress_index` 等**按索引定位**的字段在 rebuild 后不跟着
  调整是**两个**既有缺陷，都影响所有对话、非本轮引入，列入实机观察点。

### 影响范围

- 后端 5 文件（2 新：`followup.py` / `test_followup.py`）+ 前端 10 文件（3 新：
  `chat-prefs.ts` / `chatFollowUp.test.tsx` / `fixtures/follow-up-parse.json`）；无新增
  依赖、无新增快捷键、不动 Rust。
- **开关默认关 ⇒ 零回归**：关闭时 `follow_up` 参数**根本不传**（不是传 `=0`），后端
  `should_attempt` 第一条件即返回，行为与改造前逐字相同。
- `mbw_*` localStorage key **18 → 19**（新增 `mbw_chatPrefs`）。
- ⚠️ **四个持久化透传点漏一个就静默丢卡**（症状都是「刷新或编辑后卡片消失」）：前端
  `ChatMsg.followUps` → `toBackendMsg`（**白名单转换**，且**四处调用点**：regenerate /
  delete / edit-user / edit-assistant）→ `ConvMessage.follow_ups` + `openConversation`
  映射 → **后端 `ChatMessage.to_dict()`**（**最容易漏** —— 它不在「前端透传」这个心智
  模型里，不加就是在**写盘那一刻**把字段丢掉）。
- **不改** [formats.md](../backend/app/agent/prompts/formats.md)（follow-up 段一字不动：
  开关关闭时零回归，打开时两条路互斥且天然去重）、**不 bump `PROMPT_VERSION`**（对话侧
  不产生产物）、非流式 `/chat` 不动。
- 两组对照实验自证判据有区分力：① `patchBot` 换回按末尾定位 → 竞态测试红；
  ② `toBackendMsg` 白名单删掉 `follow_ups` → 四条 rebuild 测试全红。

---

## 2026-10-11 — 产物自证条：元数据渲染成一横排带图标的小标签

### 起因

上一轮修掉「HTML 注释漏进渲染」后（同日本文件上一条），产物首行的
`<!-- limo-meta {…} -->` **不再显示**了 —— 但它承载的信息（哪一版 / 何时生成 /
谁生成的 / 有没有被改过）也随之从视野里消失。用户提出的诉求是「前端元数据的美观
显示」，并两次明确形态：「渲染成一横排小标签」→「**我的意思是像有这种小图标的**」。

### 做什么

新增 [ArtifactMetaBar.tsx](../frontend/src/components/preview/ArtifactMetaBar.tsx) ——
正文上方一横排**图标 + 短文本**的小标签，每位各配一个 lucide 图标：

| 位 | 图标 | 数据来源 |
|---|---|---|
| `v{n}` | `History` | `markdownMeta.version` |
| 生成时间 | `Clock` | `markdownVersions` 里那一版的 `createdAt` |
| 字数 | `AlignLeft` | `markdownMeta.chars` |
| 模型 | `Cpu` | `markdownMeta.source` |

挂载点：`PreviewArea.tsx` 的 notes/polished **只读态**（编辑态不挂）+ `!isStreaming`。

### 为什么这么设计

**① 不是把 JSON 全量铺开** —— 自证头的 `params` / `promptVersion` / `sourceRefs`
是**给 agent 读的**，铺给用户只是噪音（设计规范 A 信息卡：「不要为了填满卡片而添加
无关字段」）。故只取四项，且**缺就不给、绝不补位**。

**② 判据收口到 [artifactMeta.ts](../frontend/src/lib/artifactMeta.ts)** —— `fmtTime` /
`fmtChars` 原先只住在 `VersionSwitcher.tsx` 里。自证条要用同一套渲染，各写一份就是
模式 #54：两处的时间格式或字数进位会慢慢漂移而**无人报错**（两边都只是「显示得不太
一样」，没有断言盯着）。函数体逐字搬移，`VersionSwitcher` 改为 import。

**③ `chars` 的 `undefined` ≠ `0`** —— `undefined` = 后端没给（旧产物），**不显示**；
`0` = 确实 0 字，显示「0字」。两者都渲染成「0字」是在伪造数据。

**④ 版本行按 `(version, mode)` 双键取** —— notes / polished **各自从 v1 起**编号，
同 version 必然同时存在；只按 `version` 匹配会让「笔记 v2」显示成**整理版 v2** 的
时间和字数，而两个数都是合法日期，**没有任何东西会报错**。

**⑤ 刻意不含「已编辑」chip** —— 同一屏的工具条已经用 `markdownEdited[mode]` 显示
「已手动编辑」（warning 色、紧邻编辑按钮，语义是「你再点编辑会覆盖」），自证条就在
它正上方；再加一个是同一信息相邻两行重复，而它并不比工具条多说什么。

**⑥ 流式生成中不显示** —— 此刻 `markdownMeta` 还是**上一版**的数（store 只在生成
完成时更新），配着新流入的正文显示是错位读数。

### 顺带修掉一个静默失效（本轮实际踩到）

第一版写了 `hover:border-border`，而项目里的 token 真名是 `--color-border-default`
（**没有** `--color-border`）。**Tailwind 对不存在的工具类静默不生成任何 CSS** ——
不报错、渲染测试全绿、编辑器里看着类名「挺对的」，只是 hover 时边框纹丝不动。
已改 `hover:border-border-default`，并补一条守卫（正则抽组件里所有 `border-/text-/bg-`
类名，逐个查 `index.css` 的 `--color-<name>:` 是否存在）**附对照实验**（拿写错的字面量
跑同一判据，证明它有区分力）。

### 测试

- `test/artifactMeta.test.ts` —— 展示判据，含 4 条对照实验（`chars` 补 `?? 0` /
  版本行不带 mode / 顺序随字段变 / 加回「已编辑」）+ **同源守卫**（`VersionSwitcher`
  不得再出现私有的 `fmtTime`/`fmtChars` 实现）
- `test/artifactMetaBar.test.tsx` —— 渲染（每个标签一个图标、无数据整条不出现）+
  **接线源码守卫**（`PreviewArea.tsx` 里只挂一次、挂载点前必须有 `!isStreaming`、
  不出现在 `MarkdownPreview.tsx` 里）+ 颜色 token 守卫

### 影响范围

- 新增：`frontend/src/lib/artifactMeta.ts`、`frontend/src/components/preview/ArtifactMetaBar.tsx`
- 改：[PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)（接入 + import）、
  [VersionSwitcher.tsx](../frontend/src/components/preview/VersionSwitcher.tsx)（删本地两份
  `fmt*`，改 import；行为逐字不变）
- **不动后端**：所需字段后端早已从产物文件头**现算派生**（`chars` / `edited` / `source`），
  `markdownVersions` 也已带 `createdAt` —— 故**不在前端新写一份首行解析**（那会是
  Python `core/transcript_meta.py` 的第二份实现，正是模式 #54 要避免的）
- **不参与导出**：纯视图装饰，与版本切换器、编辑按钮同类，不属于「用户在预览栏看到
  的**内容**」。导出物里的元信息仍由 `.md` / `.html` 保留的自证头注释承载
  （`export.py::_pick_artifact` 用 `raw_content`）
- 不 bump `PROMPT_VERSION`（不产生产物、不改任何发给模型的文本）

---

## 2026-10-11 — HTML 注释不再漏进渲染（前端全部渲染路径 + Word 导出）

### 起因

用户实机反馈「有一些标记会被展示出来」，贴出两处**形态不同**的存活证据：

1. 产物首行的自证头 `<!-- limo-meta {"type":"artifact",…} -->`
2. 模型写进正文的 `<!-- 无闭合标记，本节按卡片展示 -->`

出现位置是**主预览区 + Word 导出**。两条**根因互相独立**，只是症状同形。

---

### 根因一：前端 —— 注释被**转义**成可见文本

`react-markdown` 在**不启用** `allowDangerousHtml` / `rehype-raw` 时，HTML 注释
**不会被当成注释丢弃**，而是以 `raw` 节点一路走到渲染阶段、在那儿被**转义**成
`&lt;!-- … --&gt;` **当普通文本显示**。

⚠️ **`skipHtml` 不是解法**：它删的是**所有** HTML —— 用户正文里的合法 HTML 会被一并删掉。

⚠️ **本项目的剥离链当时只覆盖两种**：`parseMarkers` 剥 `limo-block` **标记行**（字符串层）、
`strip_meta` 剥**首行**自证头（后端）。**正文中间的任意 HTML 注释没有任何一层管** ——
用户报的正是这一类。`markerContent.test.tsx` 的对照实验 C 早就写着「标记行没被剥掉 →
页面上出现一行可见文字」，但那是 `limo-block` 的专用判据，覆盖不到「其余注释」。

---

### 修法 [markdown-plugins.ts](../frontend/src/lib/markdown-plugins.ts) — 共享 rehype 插件 `rehypeStripComments`

挂进 `REHYPE_PLUGINS` + `REHYPE_PLUGINS_PLAIN` **两个插件集**：前端 7 个渲染点
（主预览 / 产物编辑器 / 对话 / EPUB 章节 / 图片结果 / 转录 / 卡片内各段）全部走这两个
常量，故**一处修改全覆盖**，没有第二份实现。

⚠️ **判据建立在实测形态上**（先用探针把节点打出来，再写代码）：

| 注释位置 | 节点类型 | 值 |
|---|---|---|
| 正文（块级 / 句中 / 句末 / 多行） | **`raw`** | 注释原文 |
| 代码块内 | `text` | `"<!-- … -->\n"` |
| 行内代码内 | `text` | `"<!-- … -->"` |

—— 正文注释是 `raw`，而**代码块里的注释是 `text`、值与正文注释长得几乎一样**。那正是
用户**本来就要展示**的代码，故必须**显式跳过 `code` / `pre` 子树**；不跳就会静默删掉
用户的代码。判据对 `raw` / `text` / `comment` 三种类型都匹配（而非只认实测到的 `raw`），
落在「值是不是注释」这个语义上，不依赖 react-markdown 把注释归进哪一类。

⚠️ **判据是「整个值就是一个注释」**（`/^<!--[\s\S]*?-->$/`）：实测句中的注释会把前后文字
切成各自独立的节点（`"句中 "` / `"<!-- ZZ -->"` / `" 文字"`），整节点删除即可；
**不做文本中间替换** —— 那会把正文里的空格改掉。

---

### 根因二：Word —— bs4 把注释当正文段落

`export.py::_pick_artifact` **刻意**返回不剥自证头的 `raw_content`（设计如此：产物离开
`.limo` 之后靠那一行自己说明来历）。这条设计在 **md / HTML 导出上没有问题** —— 前者是
文本文件、后者里它**本来就是 HTML 注释**（浏览器不显示）。

问题在 docx：`bs4` 的 **`Comment` 是 `NavigableString` 的子类**，会命中 `docx_export.py`
遍历里的 `isinstance(node, NavigableString)` 分支，被当普通文本 `add_paragraph()` 写进
正文 —— Word 里就表现为孤零零一行 `<!-- limo-meta {…} -->`。

**修法**：解析出 `soup` 之后、**取 `body` 之前**统一 `extract()` 掉所有
`isinstance(t, Comment)` 的节点。⚠️ 顺序不能反 —— 无 `<body>` 标签时 `soup.body` 会退回
`soup` 本身，先取 body 再剥会漏掉那些直接挂在根上的注释。

### 修完之后的分工（三处各不相同，都是有意的）

| 出口 | 注释 | 为什么 |
|---|---|---|
| 主预览 / 编辑器 / 对话 | **不显示** | 渲染层，用户不该看到协议内部物 |
| `.md` 导出 | **保留** | 文本源；离开 `.limo` 后靠它自证来历 |
| `.html` 导出 | **保留** | 它是真 HTML 注释，浏览器本就不显示（源里仍可供 agent 读） |
| `.docx` 导出 | **不写进正文** | 纯渲染格式，没有「源码」可留 —— 与「预览所见 = 导出所得」一致 |

**影响范围**：全部前端渲染路径的显示结果；Word 导出的正文内容。`.md` / `.html` 导出
与产物落盘内容**零变化**。

---

### 顺带订正两条互相矛盾的注释

`markdown-markers.ts` 说「HTML 注释会被替换成普通文本节点……会在预览里**原样显示成一行字**」，
`TextareaMarkdownEditor.tsx` 说「预览区**不渲染** HTML 注释」—— **后者在修好之前是错的**，
用户证词站在前者那边。前者描述的现象本轮已被消除，故改写成「**为什么在字符串层解析**」的
真实理由：标记是**成对**的、要在两者**之间**生成一个块级容器 —— 那是**结构变换**，不是
「删掉两行」。「不让它显示」用剥的，「让这两行之间成为一个块」只能靠字符串层解析。

---

### 测试

- 前端 [htmlComments.test.tsx](../frontend/src/test/htmlComments.test.tsx) 10 条：块级 / 自证头形态 /
  模型写的说明性注释 / 句中 / 多行 / **代码块保留** / **行内代码保留** / 纯文本插件集 /
  合法 HTML 不受影响 / **反向对照**（去掉插件 → 注释确实漏出，证明前几条不是假绿）
- 后端 `test_docx_export.py` 追加 2 条（注释不进正文 + 代码块注释保留），另加一条**根因对照
  实验**（直验 `isinstance(bs4 Comment, NavigableString)` 为真、未剥时确实落进那个分支）。
  实现层另做过一次反向验证：把 `extract()` 换成 `pass` → 用例**确实变红**，且报错信息正是
  「产物自证头漏进了 Word 正文」
- 门禁 `bash scripts/check-all` 六层全绿

---

### 本轮不做（用户已明确划范围）

- **不清理用户产物里已有的注释** —— 模型写进正文的那行**仍在文件里**，只是不再显示。
  「让模型别写它」是 prompt 侧的另一件事（`card` 提到规范最前带来的副作用）。
- **元数据小标签**（用户已定方向「渲染成一横排小标签」）留待后续轮次。

---

## 2026-10-11 — 文档交叉引用机器校验（新门禁层 `docs`）+ 模式 #68

### 起因

一份**主题恰恰是「文档与代码漂移」**的待评审报告，被外部审查后暴露出自己的两类缺陷：
正文里 16 处「第 N 节」有 **13 处**指向不存在的章节（审查者只发现 1 处），以及整张
规模数字表停在旧时点（还出现 `204 ≠ 140+63+5` 这类自相矛盾）。两者的根因是同一个：
**文档里手写了「别处的状态」**——节号本可以从标题派生，数字本可以从命令现取。

用户要求先做其中最值的一条：**交叉引用机器校验**。

---

### 新增 [scripts/check-doc-refs.py](../scripts/check-doc-refs.py)

**判据**：本文档的编号标题集合 ⊇ 本文档所有「第 N 节」里的 N。
本项目里「第 N 节」一律指**本文档内**章节（跨文件引用会写明文件名），故**无需解析任何
链接**——这一条把实现从「链接解析器」压到了「集合包含」。

⚠️ **准入判据是「有 h2 整数编号标题」而非「有编号标题」**：CHANGELOG 用
`### 1. 修复：…` 表示「同一天做了哪几件事」，拿它当章节编号会让整篇 CHANGELOG 被纳入，
而它里面的「新增第 8 节」指的是**测试文件**里的第 8 节 —— 实测误报 2 处。只看恰好两个
`#` 就能关掉这一整类。

⚠️ **代码块（围栏 + 行内反引号）一律视为字面文本**，这同时给出了一条**零成本的豁免
通道**：勘误表要记录「旧版这里写的是 `第 3.1 节`」时，包进反引号即可。

**已知边界**（写进 docstring，刻意不覆盖）：跨文件引用（`见《X.md》第 5 节`）、并列写法
（`第 4.1 / 6.3 节`）。

**退出码三态**（与 `check-secrets.py` 同款）：`0` 干净 ｜ `1` 有失效引用 ｜ `2` 脚本自身
出错 —— 少了 2 这一档，「脚本崩了」与「查出问题」在 CI 上是同一个 1，无法区分。

**`--selftest`（9 条合成样本）**：项目的纪律是每条防御性断言都要能「改坏实现 → 断言
变红」证伪。本脚本是门禁项本身而非被测模块，故把区分力的自证做进自测，每条样本对应
判据里一处**真实取舍**（准入 / 豁免通道 / 日期标题不误判…）。另做了一次性探针文件的
端到端对照（LF 与 CRLF 两种行尾都验过，探针用完即删）。

⚠️ **为什么不做成「改坏真实文档」的对照**：那样会在验证过程中动用户资产。合成样本
+ 一次性探针两个通道已覆盖「判据有区分力」与「扫描器扫得到真实文件」。

---

### 新门禁层 `docs`

```bash
bash scripts/check-all docs       # 约 0.2s，全部层里最快
bash scripts/check-all            # 全部六项（原五项 + docs）
```

⚠️ **不并进 `quick` / `frontend`** —— 那两层的语义是「前端代码没问题」，而文档引用
失效与前端类型毫无关系。这是**域**的划分，不是速度的划分（`check-all` 的分层纪律）。

⚠️ **`stage_docrefs` 不加 `cd`**：脚本用 `__file__` 自定位仓库根，在哪个目录跑都一样。
⚠️ **不给 `run_stage` 的失败信号 grep 加特例**：编排脚本若要去认被编排脚本的语法，
就是同源漂移的入口（模式 #54）。该脚本输出很短（失效不超过 4 处时 `tail -15` 即全量），
现有「关键行 + 输出尾部」双通道够用。

---

### 文档同步

| 文件 | 改什么 |
|---|---|
| [CLAUDE.md](../CLAUDE.md) | 门禁命令段加 `docs` 一行（「全部五项」→「六项」）+ 一段说明；文档索引表 patterns 条目标注 `#68` |
| [patterns.md](patterns.md) | 新增 **#68**（含同批修掉的「手抄规模数字」形态，及它为何**没有**机器判据） |

**不改**：版本号、`PROMPT_VERSION`、任何打包产物。

---

## 2026-10-11 — 快捷 MD 导出可内嵌图片（新设置项）+ 详细导出系统审查六条

### 起因

用户两条要求：

> 可以加个设置是否依赖后端，如果依赖的话就可以内嵌图片全量导出，
> 还有目前的详细导出系统得测试检查一下，默认依赖。

前一条接的是上一轮的问题（快捷「下载 MD」不带图片）；后一条要求把**详细导出**
（`ExportPanel` + `/export/custom` / `/export/batch` / `/export/{id}/all`）审一遍。

---

### 一、快捷「下载 MD」可以连图片一起带走

**问题**：快捷「下载 MD」是**纯前端**的（`downloadText(stripMarkers(exportContent))`），
从不读图片文件；而正文里的图片引用是 `/api/assets/{id}/x.png` 这种**根相对路径**，
只在 LiMo 内部有效 —— 文件一离开应用就整篇裂图。带图的路径本来只有 HTML / Word / PDF
（内嵌）与 ZIP（`{标题}/images/`）。

**新设置项**：设置 → 通用 → 「Markdown 导出时把图片一起带走」，**默认开**。

| 开关 | 实际走的路 | 代价 |
|---|---|---|
| 开（默认） | `GET /export/{id}?format=md&image_md=inline`，后端把图片 base64 内嵌 | **依赖本地后端** |
| 关 | 纯前端 `downloadText` | 零依赖，但图片是只在本应用内有效的路径 |

**链路**：`lib/export-prefs.ts`（localStorage `mbw_export`）→ `uiSlice.exportPrefs`
→ `SettingsPage` 复选框 → `ExportMenu::handleDownloadMD` 双路径。**没有 DOM 副作用**，
故 `store/index.ts` 的启动链不需要对应的一行（对照 `card-prefs` / `marker-prefs`）。

⚠️ **只管「下载 MD」这一件事**：不管「复制 Markdown」（往剪贴板塞 base64 不合理），
也不管详细导出面板（那边本来就有 独立/inline/不含 三选一）。

后端 `export_content` 加 `image_md`（默认 `"link"` = 改造前行为，**零回归**），md 分支内
复用 HTML/docx **同一套** `_find_image_refs` / `_collect_images` / `_inline_images` ——
不产生第二份「图片怎么找、怎么读、怎么写」的判据。

⚠️ **顺序是不变量**：先内联、**后**绝对 URL 改写。`_inline_images` 对「没拿到数据」的引用
原样保留（如文件缺失），正好被后面那四条改写兜底；颠倒过来则缺失图会停在 `assets/x.png`。

⚠️ **两条路径必须给出同一个文件名**：后端命名是 `{标题}-{mode}.md`（不含版本后缀与日期），
与前端 `makeFileName` **不同源** —— 故走后端时显式传 `preferSuggestedName: true`
（`lib/download.ts` 新增的可选项）。不这么做的话「同一个按钮，改个开关文件名也跟着变」。

**测试** `test_export_md_images.py` 6 条：默认路径零回归（**对照实验已验**：默认值改 `inline` → 红）、
内嵌后原引用必须消失（双向断言 —— 少了这一半，「既内嵌又保留引用」也能绿）、
内容不丢、缺图退回绝对 URL、**越界引用不得被内嵌**（新入口再验一次）、
该参数不影响 HTML。另有 `exportPrefs.test.ts`（读写 + 跨语言成对守卫）。

---

### 二、详细导出系统审查：六条，逐条都有实跑证据

| # | 症状 | 用户会看到什么 |
|---|---|---|
| 1 | 跨类型 tab 的**并集**被套到每一篇上 | 「一键导出」的 zip 里凭空多出名字对不上的文件 |
| 2 | `original` / `ocr` 两个 tab 进了导出清单 | 勾「原图」导出，得到的是**一段文字** |
| 3 | `/export/batch` 空结果照发空 zip | 「1 篇报错、2 篇静默成功」 |
| 4 | 一键导出的 md 不剥 `limo-block` 标记 | 同一份产物，两条导出链给出相反结果 |
| 5 | `_inline_images` 在定制导出路径上没收到 `doc_dir` | （当前不暴露，见下） |
| 6 | 只勾 Word + 独立图片时多写一份 `images/` | zip 里一个没人引用的目录 |

**#1 + #2 是同一个根因的两张脸**：`ExportPanel` 界面上 tab 是**按文档类型分组**勾选的，
但发请求时 `Array.from(new Set(typeGroups.flatMap(...)))` 把各组的 tab **拍平成并集**，
丢掉「哪个 tab 属于哪组」的信息；后端拿到并集后套到每一篇上，落到 `_resolve_tab_content`
的兜底分支，**把 `rawText` 当成那个 tab 的内容**产出来。实跑（网页 + 图片两篇，
默认 tab 全选）：**9 个文件里 6 个是重复的原文**。

修法分两处、判据仍各只有一份：

- **前端**（`getTypeMeta`）：`tabs` 滤掉 `contentSource === 'none'` 的项 ——
  「原图」「OCR」自己声明了**没有可导出的内容源**（`CONTENT_GETTERS.none` 恒返回 null），
  而 `TypeMeta.tabs` 唯一的消费方就是导出面板。**不是硬编码黑名单**：测试按
  `TabRegistry` 逐类型派生期望值，下次加 tab 不会静默过期。
- **后端**（`_resolve_tab_content`）：**删掉 else 分支的 `rawText` 回退** ——
  查不到对应处理结果就返回空串（调用方据 `if content and content.strip()` 不产文件）。
  「不认识就退回原文」产出的恰恰是**名字对不上的假产物**，比少一个文件坏得多。

**#3**：`export_batch` 的 `continue` 之后没有任何计数或校验。加 `total_files` 与
`if total_files == 0: raise EXPORT_NO_CONTENT(404)`，与 `/all`(404)、`/custom`(400) 统一。
⚠️ 这是 P1-11 的**第三处**，而 `_build_export_zip` 的注释当时就写着「已统一」——
同一件事写三遍，代价总是在漏掉的那一份上出现。用例
`test_all_and_batch_agree_on_empty_result` 的对象不是「404 对不对」而是**成对**。

**#4**：`_collect_doc_products`（「一键导出所有产物」的唯一实现）直接取 `md["content"]`
（磁盘原文，含标记行），而定制导出在 md/plain 两处都 `_strip_blocks`。同一个约束
（「预览所见 = 导出所得」，标记在预览里不显示）在两条链上给出相反结果。
两条链各有一条用例 —— **只测一边时，另一边被改回原样不会有任何提示**。

**#5**：`_inline_images` 的 docstring 宣称与 `_collect_images` 是**成对守卫**，而
`_build_export_zip` 的调用点没传 `doc_dir`，那半个守卫在定制路径上永不生效。
当前**不暴露**（`imgs` 已由收集侧全量填好，走 `image_data.get(fn)` 命中，不依赖兜底），
但那是**巧合不是保证**：收集侧一旦发生裁剪，内联侧就会静默保留 `assets/x.png`。

**#6**：docx 用**原始 content**（引用仍是 `assets/x`）+ `imgs` 字典内嵌，**不读**
`images/`。写 `images/` 的条件补上 `any(f != "docx" for f in data.formats)`。

**顺手**：`export_batch` 里 `for img_name, data in imgs.items()` 遮蔽了同名入参
`data: dict` —— 当下无害，但是「改天有人在循环后接着用入参」的现成陷阱，改名 `img_bytes`。

**顺带记录（未改）**：`_resolve_tab_content` 里的 `report` / `skeleton` 是**后端认、
前端从不发**的死分支（`TabRegistry` 无这两个 key：`report` 从未注册、`skeleton` 已随
骨架面板移除）。保留形状不改，已在 docstring 注明它们当前取不到。

---

### 三、三个导出测试在门禁里**空跑**（假绿）

`test_docx_export.py`（18 项）/ `test_export_print.py`（6 项）/ `test_export_formula.py`（3 项）
全是**脚本式**的（模块级顺序断言 + `print("PASS: …")`）——`pytest` 收集数为 **0**：
文件被当成普通模块，既不红也不计数。而 `scripts/check-all` 跑的正是 `pytest tests/ -q`，
所以「导出测试全过」是个**幻觉**，实际一项都没跑。

（与 2026-10-06 在另外四个文件上踩的是同一个坑：那次改写成 `def test_*`，这三个漏网。）

修法：保留脚本形态（模块级断言在**导入期**执行，任一失败即中断导入 → 收集错误 → 门禁非零退出），
**加一层适配**：末尾一个 `_SUITE_COMPLETED` 哨兵 + 一个函数体为空的 `def test_*_suite()`，
让 pytest 看得见它。哨兵防的是「哪天有人把整段包进 `def main()` 却忘了调用」——
那种情况下哨兵未定义、占位测试 `NameError` 变红。

⚠️ **已实测适配层真的会红**（临时把一条断言改成 `assert False` → `1 error during collection`），
否则它就是一个更精致的假绿。

---

### 已知缺口（如实记录，不写成已可用）

- **「原图」/「OCR」没有导出内容源**：本轮把它们从导出清单**摘掉**，不是补上「导出原图」
  的能力。用户现在看不到这两个选项（而不是勾了却拿到文字）。要做真「导出原图」是另一件事。
- **`ExportPanel` 的「按上方设置导出 N 个文件」是前端本地估算**，与后端实际产出
  （哪些产物真的存在、哪些 tab 不适用）**不同源**，只是个近似。修完 #1/#2 后它更准了，
  但仍不等于后端计数。
- **前端仍无 `ExportPanel` 组件级行为测试**：本轮补的是 `getTypeMeta` 的 tabs 单测
  + `ExportMenu` 的源码级守卫（与既有 `exportMarkers.test.ts` 同款）。渲染 ExportPanel
  需要铺一整套 store mock，不在本轮。
- `POST /export/custom/stream`（SSE 进度）**前端仍零调用**，本轮未动（见待办表）。

### 未改

- **不 bump `PROMPT_VERSION`** —— 它标记「产物生成的 prompt 版本」，本轮不产生产物。
- **不动版本号**（`0.5.3`）—— 未打包。

---

## 2026-10-11 — 智能分类五轮：候补保留 + 杂项口袋夹（+ 全文审计两处真缺陷）

### 起因

把 `_classify-review.md`（时点快照，2026-10-26 已归档到 `_archive/`）交给外部审查后，收到六条意见；用户同时提了一条产品意见：

> 可以加一个默认兜底策略，假如模型感觉这些不够用的话，可以再来加一个**杂项的口袋文件夹**；
> 但这个文件夹必须要**整体判断之后**再确定需不需要，尽量保持少的文件进入这一个文件夹。

六条意见里四条当场落实（保留空库文案拆分 / 重写判据 / 补矩阵测试 / 改注释），两条要求
**先通读全文再回答**（Q2「同族缺陷是否排清」、Q6「还有哪些未验证」）——通读的结果是抓出
**两处新的真缺陷（F1 / F2）**，外加一条已知但未修的产品问题（F7）。

### F1：`limits.maxNewFolders` 回显的是默认常量，不是用户设的值

`preview` 响应里的 `maxNewFolders` 直接取 `MAX_NEW_FOLDERS`（5），而**实际执行**用的是请求
里的 `maxNewFolders`。用户把上限调到 8 时：分类照 8 跑，对话框却仍写「最多新建 5 个」。

**判据与执行不同源**，两侧各自看起来都正常 —— 这正是模式 #54 的标准形态：同一件事在两个
投影上被各自算了一遍。

修法：回显**生效值**。守卫 `test_preview_echoes_effective_max_new_folders_not_the_default`，
对照实验（改回常量）已验证变红。

### F2：标签名清洗有三份实现，commit 那份漏了截长

标签清洗散在三处 —— preview 的模型输出、用户手填的 `seed`、commit 的写入。其中 commit
那份**没有截长**（`MAX_TAG_LEN = 30`）→ 同一条 40 字标签，**走模型建议进来的是 30 字，
用户直接手打进对话框的却是 40 字**，一个库里并存两种长度，谁也不报错。

修法：抽 `clean_tag_names` 作为**唯一内核**，三处共用（条数上限 `MAX_TAGS_PER_DOC` 属于
策略特有约束，仍留在各调用点，不上浮）。守卫两条：
`test_clean_tag_names_is_shared_by_model_and_user_sites` /
`test_commit_cleans_tag_names_with_the_same_kernel`，均已验证变红。

⚠️ **F2 正是本轮定形的那条判据的「正面形态」** —— 见下。

### 判据定形：「一段共享文案只有在与引用它的每一个策略的完整契约都不冲突时才能共享」

上一轮我写的判据是「两种取值下该做的事是否相同」。审查指出它需额外限定取值点、不可机械
检验。改用上面这条之后，两处**看似同族的拆分**立刻显出分野：

| | P2-2 空词表兜底（`seed` / `vocab`） | 空库文案（`reuse_only` / `reuse_or_new`） |
|---|---|---|
| 那段文字讲的是 | 一个**跨策略不变量**（库里没有标签这个事实） | 一个**策略特有结论**（该全 `none` 还是该新建） |
| 共享后果 | 无 | `reuse_or_new` 被静默改写成 `tags_only` |
| 处置 | **共用同一份** | **必须拆两份** |

**共享的是跨策略不变量，凡携带策略特有约束的句子都必须上浮到该策略自己的段里。**
F2 是同一判据的正面例证：什么算合法标签名是跨策略不变量（下沉），一篇最多几个标签是策略
特有约束（上浮）。落地形式是一条表驱动的策略矩阵测试，而不是一句注释。

### F7 + 杂项口袋夹（本轮主功能）

**问题**：新建上限用尽时，模型原本给的夹名被**静默丢弃**。91 篇实测中模型共提出 14 个新夹名，
上限只放 5 个 → **60+ 篇落 `none`**，且它们在对话框里只剩一句「不归入」，模型本来想把它放进
哪**整个丢失**。

**两条产品决策**（用户选定）：

1. **候补保留**：超限被拒时不再只写 `none`，而是带上 `candidate`（模型原本的夹名）。
   对话框多出一个「新建：XX（模型原建议）」选项，**采纳时把夹名预填**进输入框 —— 等于给用户
   一个「手动多开一格」的手柄，而不是让他照抄模型建议。
2. **口袋夹不占额度 + 机械闸门**：新增 `MISC_FOLDER_NAME = "杂项"`。
   - **不占 `MAX_NEW_FOLDERS` 额度**。占用的话上限 5 时它会挤掉一个真实分类，**恰好救不了
     超限这个场景** —— 它是兜底，不是分类。
   - **「尽量少放」由收口层机械保证，不写进 prompt 就完事**：整批结束后若进杂项的篇数超过
     总篇数的 1/3（`MISC_MAX_DENOMINATOR = 3`，用整数比较 `len(misc) * 3 > total` 避开浮点），
     说明它已经不是在兜底、而是在吸走本该分开的内容 → **整夹撤下**、逐篇退回 `none` + 候补，
     并给一条全局 warning 说明原因。「尽量少」如果是 prompt 里的一句请求，模型完全可以不听。
   - 只在 `reuse_or_new` 下注入这条规则（`reuse_only` / `tags_only` 两支不出现「杂项」）。
   - 库里已有「杂项」时走既有的同名归一化转 `reuse`，不会重复建。

**落点**（后端 6 处 / 前端 3 处）：

| 处 | 改动 |
|---|---|
| `classifier.py` | 新增 `MISC_FOLDER_NAME` / `MISC_MAX_DENOMINATOR` / `is_misc_folder_name()` |
| `_folder_section` | `reuse_or_new` 的 rules 加第 5 条（**只此一支**） |
| `_normalize_one` | 被拒分支带出 `candidate`；新增杂项通道（名为「杂项」→ 规范成常量 + 标 `misc`） |
| `normalize_suggestions` | `new_order` 计数循环排除杂项；末尾加机械闸门 |
| `api/classify.ts` | `ClassifyFolderSuggestion` 加 `candidate?` / `misc?` 两个可选字段 |
| `ClassifyDialog.tsx` | `Draft.candidate` + 采纳时预填 + 候补 `<option>` |

⚠️ **杂项通道排在 `rejected_new` 判定之后也不会被它误伤** —— 它压根不参与 `new_order` 计数，
两支互不相干。这个顺序不是巧合，是「不占额度」那条决策的直接推论。

### 测试

后端 47 → **57 条**（新增 10 条，含策略矩阵 + 4 条本轮的），前端 23 → **25 条**（候补选项在场 +
**反向对照**「本来就无归处的 `none` 不给候补选项」，两者不得混同）。

**两轮对照实验实跑验证**（不是推理）：
- 第一轮（矩阵 + 归一化 4 条）：改坏 3 处 → **5 failed**；还原 → 52 passed。
- 第二轮（候补 + 口袋夹 5 条）：改坏 3 处 → **3 failed**；还原 → 57 passed。
- 前端两条：删候补 `<option>` → 红；删采纳时的预填 → 红；还原 → 25 passed。

门禁五项全绿（`bash scripts/check-all`，54s）。

⚠️ **本轮不 bump `PROMPT_VERSION`**（不产生产物）；⚠️ **不动 `MAX_NEW_FOLDERS = 5`**（改它等于
重新定义「一次分类最多留几个夹」，属于另一次产品决策）。

---

## 2026-10-11 — 实机验收（91 篇真实库）：空库那句 prompt 把 `reuse_or_new` 静默改写成 `tags_only`

### 起因

用户请求「就拿这台机器上的那 90 多篇笔记来做个分类测试，只用标题」。拿本机真实库（**91 篇**）
跑 `POST /api/classify/preview`，`notesChars=0`，**只调 preview（零写入）**。

### 现象

13 秒跑完、5 批、91 篇全 `title_only` —— 但 `folderAction` **91 篇全是 `none`**，
即用户明明选了默认的 `reuse_or_new`，实际拿到的是 `tags_only` 的结果。

### 根因：空库文案是一句**命令**，而不是一句**陈述**

`_FOLDERS_EMPTY_NOTE` 原本是**单份常量**、`reuse_or_new` 与 `reuse_only` 共用：

```
（当前库里没有任何文件夹，本次请只使用 none，不要使用 reuse 或 new）
```

两档在「库里没有文件夹」这个事实上的**结论恰好相反**：

| 策略 | 空库意味着 | 共用那份文案的后果 |
|---|---|---|
| `reuse_only` | 没有可复用的 ⇒ 结论就是「全 none」，**这是对的** | 无 |
| `reuse_or_new` | 没有可复用的 ⇒ **正是该新建的时候**（首次使用 / 刚丢完分类，恰是最常见的两个场景） | 用户明确选的策略被静默改写成 `tags_only` |

写这份常量的**动机是正当的**（防止模型凭空编造 `fd_xxx`），但手段过界：把「不许复用」
扩写成了「只许 none」。

### 对照实验（同一份输入 91 篇，只换那一句文案）

| | 原样（命令式） | 中性陈述 |
|---|---|---|
| `new` | **0** | **23** |
| `none` | 91 | 68 |
| 耗时 | 12.8s | 14.1s |

对照组新建名分布：`{"求职与面试":3, "绘画与创作":4, "情感与心理":6, "科技与AI观察":5, "创作与审美":5}`。
**判决判据就落在这一句 prompt 上** —— 模型其余部分（标签质量、逐篇判断）两版完全一致。
（该常量是 0.5.2 工作树里**未提交**的改动，不在同日「7 条修复」清单内。）

### 修法

拆成按策略分派的**两份常量** + `_folder_section` 里 `reuse_only` / `else` 两支：

```python
_FOLDERS_EMPTY_NOTE_REUSE_ONLY = "（当前库里没有任何文件夹，本次请只使用 none，不要使用 reuse 或 new）"
_FOLDERS_EMPTY_NOTE_REUSE_OR_NEW = "（当前库里还没有任何文件夹，请为这批材料新建合适的分类）"
```

⚠️ **与同日 P2-2（空词表兜底）是一对反例，判据由此定形**：

- P2-2 的 `seed` / `vocab` 两档**共用同一份** `_TAG_SECTION_NO_VOCAB` —— 对的，因为
  「都没有词表可挑」时两档语义完全一致；
- 这里**必须分两份** —— 因为「没有文件夹」在两档下的结论相反。

**判据是「两种取值下该做的事是否相同」，不是「两种取值是否都在同一个函数里」**。
把两者混为一谈，就是模式 #54（同源漂移）在 prompt 层的形态：共用一份的理由看起来
同样充分（「都是空库嘛」），而失效方式是**用户选的那一档被悄悄换成另一档**。

### 测试

⚠️ 既有的 `test_prompt_says_no_reuse_when_library_has_no_folder` **本身就是这个 bug 的守卫** ——
它在**默认的 `reuse_or_new`** 下断言 prompt 含「不要使用 reuse」，等于把当时的实现固化成契约。
改写为 `test_prompt_empty_library_note_follows_folder_mode`，按策略分列断言，并含**反向对照**：
同一句禁令在 `reuse_only` 下**确实在场**（否则「不在场」的断言可能只因该措辞从模板里整体消失而通过，
两条都绿却什么也没验）。

### 附带观察（如实记录，本轮未处置）

1. **`MAX_NEW_FOLDERS = 5` 对主题极散的库严重不足**（plan 风险 3 被实例证实）：91 篇覆盖
   绘画 / AI / 职场 / 情感 / 社会评论 / 编程六大簇，模型跨 5 批共提 **14 个**不同新名，
   全局收口保留 5 个 → **60+ 篇因上限被拒而落 `none`**（这部分篇的 warning 明写「已改为不归入」）。
   用户可在对话框把上限调到 `MAX_NEW_FOLDERS_LIMIT`(10)，但 14 > 10，仍会掉一批。
   模型给出的分类本身是准的（如「游戏设计/机制/方法论」「AI/影视/短剧」），问题在上限这个旋钮。
2. **冷启动时标签不收敛**（plan 风险 2）：91 篇产出 **223 种**标签、绝大多数只出现一次。
   词表为空时「优先复用」无从谈起，这是冷启动的必然；**需第二轮才有收敛证据**。
3. 模型对同一篇返回了多条结果，被 `normalize_suggestions` 取第一条并给 warning —— 兜底按设计生效。

---

## 2026-10-11 — 智能分类 + 卡片微调的代码审查：7 条修复

### 起因

对 0.5.2 工作树上未提交的两批改动（智能分类 / 卡片逐维度微调）做了一轮代码质量与漏洞审查。
7 条发现**逐条回读源码验证**（不采信静态扫描结论），**未发现可利用的安全漏洞**。全部修复 + 4 条新守卫。

### 修复清单

**① P1 分批与预算不同源**（`classifier.fit_to_budget`）
分批把「一次请求 N 篇」变成「≤20 篇的多次请求」，而预算仍按**全量 N** 均摊 —— 每篇被砍到
分批后本可容纳值的约 1/批数（90 篇时 159 字，而单批本来放得下 600 字），分批「把每批输出量压下来」
的收益被反向吃掉。改为按**单批**篇数均摊（模式 #54：判据必须与「谁在花这份预算」同源）。
⚠️ **副作用如实记录**：按批均摊后每篇配额下界 = `_input_budget` 地板价 4000 ÷ 单批 20 = **200**，
恒大于 `MIN_USEFUL_CHARS`(120) —— `fit_to_budget` 的 `title_only` 分支在**默认常量下变得不可达**。
分支保留（常量组合变化时的纵深防御），其测试改为**显式抬高 `MIN_USEFUL_CHARS`** 来行使它，
并在 docstring 注明「测的是行为契约，不是某组真实输入恰好触发它」（模式 #38）。

**② P1 审定阶段重跑失败后「应用」被禁用**（`ClassifyDialog.canConfirm` 的 `!error`）
父组件 `loadClassifyPreview` 的 `.catch` 只设 error、**不碰 preview**（注释明写「旧的留在屏幕上 +
顶部一条错误行，用户可以先用手上的」）—— `!error` 把这条设计意图当场推翻：建议明明摆在眼前，
按钮却是灰的。「从没成功过」由 `!!preview` 挡住，`!error` 多余且有害。

**③ P2 新建上限每批声明全量**（`classifier.per_batch_new_limit`）
每批 prompt 都声明「最多新建 5 个」，而模型跨批无法协调 → 5 批各提 5 个、全局收口拒掉约 80%，
用户拿到一堆 `none` + 一条 warning。改为**按批分配**（`ceil(上限/批数)`；⚠️ 上限为 0 时仍是 0，
写成无条件 `max(1, ·)` 会把「用户不许新建」抬成「每批可以建 1 个」）。**声明按份、收口按总量**，
两者是同一次策略选择的两个投影。

**④ P2 空词表兜底只做了一半**（`classifier._tag_section`）
`seed` 档用户没填标签、或 `vocab` 档而库里还没有标签（**新用户首次分类的常见情况**）时，prompt 会
输出「请优先从中选择：」+ 空白 / 「优先从上面选」+「（库里还没有标签）」—— 同一句里自相矛盾。
两档**共用同一份兜底常量** `_TAG_SECTION_NO_VOCAB`，配「整篇逐字相等」的同源断言 +
「有词表时两档必须不同」的反向断言（防顺手把刻意分开的措辞也抹平）。

**⑤ P2 配置阶段段控另写了一份表达式**（`ClassifyDialog.activeDepth`）
`activeDepth` 三元里 `phase === 'review'` 恒真（只在审定段控消费），配置阶段的段控却另写
`cfg.notesChars` —— 于是三元的一支永不被行使。改为**两处段控共用 `activeDepth`**，死代码消失。

**⑥ P2 两处 `fixed inset-0` 遮罩被锁进面板**（`FilterPopover` / `ChatInput`）
两处浮层的祖先都带 `backdrop-blur`，它**创建包含块**把 `fixed` 后代锁进面板自身大小，遮罩实际只盖住
面板那一条 —— 点面板外关不掉浮层（模式 #67）。⚠️ 这两处**不能**照其他浮层的做法 Portal 到 body：
它们是 `absolute` 定位于触发按钮的**下拉**，脱离 `relative` 父就失去基准。改法取最小的那个：
**删遮罩、监听到 document**，用 `contains` 划内外界（原注释担心的「点按钮也算外部」由它解决）。

**⑦ P3 + 纵深防御**：`DocLibrary` 的 `?? 5` → `MAX_NEW_FOLDERS_DEFAULT`（字面量会在常量调整后静默
留在旧值）；`ClassifyDialog` 的 `notesChars: 600` → `DEPTH_OPTIONS[0].value`；`_CARD_VAR_VALUE_RE`
加长度上限 `{1,200}`（字符集白名单不限制长度，一次请求可带几 MB 合法字符进 `<style>`）。

### 新增守卫（4 条，每条都有对照实验）

预算按**单批**均摊（改回 `len(docs)` → 红）｜审定阶段重跑失败仍可应用（`!error` 加回 → 红）｜
`DEPTH_OPTIONS` 首项 = 600（钉「调序 = 静默改默认档」）｜筛选浮层点浮层外关闭（改回透明遮罩 → 红）。

### 影响范围

后端 `generation/classifier.py`（4 处）、`routers/export.py`（1 处）；前端 `ClassifyDialog.tsx` /
`DocLibrary.tsx` / `FilterPopover.tsx` / `ChatInput.tsx`。**不改版本号、不 bump `PROMPT_VERSION`**
（分类 prompt 措辞变了，但分类**不产生产物**、没有自证头）。

---

## 2026-10-11 — 智能分类三轮：分批 + 关思考（一次调用撑不住大 N）

### 起因

用户在 90 篇文档上跑智能分类，问题不是「结果不好」，是 **`0 chars`**：

```
app.llm.service  LLM 流式响应: 0 chars, model=deepseek-v4-flash, finish=length, round=0
POST /api/classify/preview status=200 duration_ms=27986.0 slow=True
```

模型思考了 28 秒、**一个字正文都没吐**、`finish=length` 说明输出预算被耗尽 —— 而耗尽它的是推理链。
更糟的是**报错措辞把根因指反了**：`parse_json_object("")` 返回 `None` → 抛 `CLASSIFY_BAD_JSON`
「模型返回的内容不是合法的 JSON」。用户看到这句会去查 JSON 格式，真相却是「模型只思考、没输出」。

### 根因

一次性调用时 `max_tokens = min(max(2048, 90×120), 8192) = 8192`，而 DeepSeek V4 **思考默认开启**。
90 篇各产一条 JSON 的负载下，推理链把 8192 全部吃掉；`max_continuations=0`（分类不需要续写）
让 `finish=length` 直接 break，于是拿到空串。

### 关键决策

**① 分批（`CLASSIFY_BATCH_SIZE = 20`）—— 这不是为了省事，是被实机逼出来的。**
每批独立组装 prompt（同一份文件夹 / 标签上下文），拿回各自的 `docs` 数组，
**合并成一份再交给同一个 `normalize_suggestions`**。于是新建上限的**全局计数**、同名归一、
幻觉 id 剔除全部仍走那一个出口，**分批不引入第二套收口逻辑**（模式 #54）。
`max_tokens` 跟着**本批**篇数走（`min(max(2048, len(batch)×120), 8192)`），下限 2048 是给「篇数少但模型多话」留的余地。

**② 关思考，不是降档。** 用户中途说「我觉得可以不用思考模型」，这个判断是对的：分类是
**结构化抽取**，答案由输入决定，推理没有增值空间。实机也证明**降档救不了** —— low 档同样把预算烧光。
改走 `extra_body={"thinking": {"type": "disabled"}}`（canonical 形式；`reasoning_effort="none"`
亦可，但中间层如 LiteLLM 不总翻译它）。⚠️ **两者在服务端互斥**，同传会被校验拒绝，故代码里是 `elif`；
测试同时钉住「关思考时 effort 不出现」与「同传时 effort 被丢弃」两条 —— 对照实验（把 `elif` 改回 `if`）
可让后者当场变红（实测：`{'thinking': …, 'reasoning_effort': 'low'}` 同现）。

**③ 全失败与部分失败的语义分家。**
- **全部批都没成** → 整次显式失败。分类方案要么完整、要么说清为什么没有，**不降级**。
- **部分批失败** → 结果照给，但全局 `warnings` 点名「第 N 批（原因）」。少了这条，用户会把那些篇的
  「不归入」当成**模型的判断**，而它其实**根本没被分析过** —— 静默降级的典型形态。

**④ 空输出有了自己的错误码**：`CLASSIFY_EMPTY_OUTPUT`（「模型未返回内容（可能仍耗尽了输出预算）」），
与 `CLASSIFY_BAD_JSON`（真返回了东西但不是 JSON）分开。前端 `classifyPreview` 本就按 `body.code`
显示 `message`，**无需前端改动**。

**⑤ 双层超时**：每批 `CLASSIFY_TIMEOUT = 120s`（独立成层，理由同 `COMPRESS_TIMEOUT` ——
不设它就只有 SDK 请求超时兜底，模型挂起时用户干等到那儿）+ 整次 `CLASSIFY_TOTAL_TIMEOUT = 600s`
（分批是**串行**的，没有总闸门时极端篇数会累积成不可接受的等待）。

### 顺带修：TOC 的 React 重复 key

同一条日志里还有 `Encountered two children with the same key, '后果'`。根因是 `headingId()`
对中文**保留原字**，两个 `## 后果` 算出同一个 id。[TOC.tsx](frontend/src/components/preview/TOC.tsx)
的 key 改为 `${i}-${item.id}`。⚠️ **id 本身仍是重复的**（`document.getElementById` 只命中第一个，
点第二个同名标题会滚到第一个）—— 那是渲染侧 `headingId` 去重的问题，**本轮不解决**，只在注释里标注。
扫库确认来源：`平台避雷…-e81093/notes/v2.md` 等 3 个文件有同名标题。

### 影响了什么

- [generation/classifier.py](backend/app/generation/classifier.py)：新增 `CLASSIFY_BATCH_SIZE` /
  `CLASSIFY_TOTAL_TIMEOUT`；`classify_documents` 的调用段改为**分批循环 + 失败收集 + 合并后统一归一**。
- [llm/service.py](backend/app/llm/service.py)：`disable_thinking` 贯通 `stream()` 与 `call()`，
  落点为 `_extra["thinking"] = {"type": "disabled"}`（与 `reasoning_effort` 互斥的 `elif`）。
- [preview/TOC.tsx](frontend/src/components/preview/TOC.tsx)：key 加序号。
- 测试：[test_classify.py](backend/tests/test_classify.py) 新增 4 条（含 `_ScriptedLLM` 逐批脚本替身）；
  [test_effort.py](backend/tests/test_effort.py) 新增第 5 节 3 条 extra_body 形状断言。

**不 bump `PROMPT_VERSION`** —— 它标记的是「产物生成的 prompt 版本」（写进产物自证头），
分类不产生产物。

---

## 2026-10-11 — 卡片逐维度微调：底 / 抬升 / 边（三根旋钮 + 导出跟随）

### 起因

用户在看过 8 套卡片方案后提出：

> 「就之前视觉方案里面不是有很多选项可以调设置吗那些设置其实也可以考虑加进来」

指的是 `pi-docs/卡片视觉方案-样板.html` 里那排「逐项微调」控件（7 个：底 / 抬升 / 边 / 圆角 / 投影 / 内边距 / 间距）。用「**预设覆盖不到的组合才值得开成旋钮**」这一条筛完，用户拍板**只做前三个**。

### 为什么是这三个

| 旋钮 | 值 | 为什么值得 |
|---|---|---|
| 底（基色） | 页面底 / 悬停面 / 主色淡底 / 透明 | 与抬升是**同一根轴的两端**（`--card-bg = color-mix(base, elevated lift)`），预设只覆盖了 base 的两端 |
| 抬升 | 0–100% 滑条 | **8 套里只有 `0%` 与 `100%` 两个取值**，中间一整段一格都没有 —— 而用户 2026-10-10 的原话是「在背景颜色基础上稍微做点变化」，那正是中间段 |
| 边 | 极细浅 / 标准 / 粗描边 / 主色细边 / 主色粗边 / 无 | 多出「粗中性边」「主色细边」两档预设没有的组合 |

**不做的四个**：圆角、投影（8 套已铺满常见档，边际收益小）；内边距、间距（三档已被 8 套方案占满，**零新增价值**）。

### 关键决策

**① 微调 = 同一批 CSS 变量的第二个入口，不是第二套机制**（模式 #54）。旋钮直接写 `--card-bg-base` / `--card-lift` / `--card-border`，与 8 套方案写的是同一组变量、同一份 `cards.css`。落点是 `<html>` 上的 **inline 变量** —— 特异性天然压过方案块（非 inline），故「调过的覆盖方案给的，没调的落回方案取值」。

**② 「跟随方案」是每组的第一个选项（默认）**。三个字段的默认值是 `null`，表示**一个 inline 变量都不写** —— 于是新增旋钮**不改变任何既有用户的默认观感**（零回归靠「不写」而不是「写一个与方案相同的值」，后者会在方案取值演进时静默脱节）。

**③ 切方案时清空微调**（样板 `setVars` 的第一步同款）。语义上微调是「在方案之上的一层覆盖」，换地基就该重新决定要不要覆盖；不清的症状是「调完再切预设，微调看着还在、实际被新方案压住」，零报错。

**④ 透明基色下抬升被强制归零**（`isLiftDisabled`）。`color-mix` 里的 `transparent` 等价于 `rgb(0 0 0 / 0)` —— 抬升把它混出来的是**半透明黑**，不是「在背景色上做变化」。这是拉不出正确结果的旋钮（模式 #53），处置是**主动写 `--card-lift: 0%` 覆盖方案值**（不写的话 `apple` 方案自带的 100% 照样生效）+ UI 禁用滑条。⚠️ 判据读**运行时** `--card-bg-base`：`outline` 方案本身就是透明底，而「跟随方案」时前端并不知道 —— 所以 `applyCard` 的第一步（无条件清空 inline 变量）必须排在这次读取之前，否则会读到自证循环（一旦选了透明就再也回不去）。

**⑤ 导出跟随 —— 这是本轮唯一会静默分叉的地方**。前端把微调**解析成 CSS 值**（`--card-lift:40%`）经 `card_vars` 参数发出；后端只做**字符白名单**过滤、**不持有第二张「档位 → 取值」的表**（否则格式演进时漏改一侧的症状是「预览对了、导出还是老样子」，而导出照样成功）。

- **顺序即优先级**：微调 `<style>` 必须排在 `cards.css` **之后** —— 方案块 `[data-card-style="x"]` 与微调的 `:root` **特异性相同**（都是 0,1,0）。放到前面会被方案原样压住。有专门的用例钉住这个索引关系。
- **白名单拦住的是「从变量值逃逸出去」**：值里不许出现 `;` `{` `}` `<` `>` `"` `'` `\` `/` `*` —— 合起来正是「写新规则 / 闭合注释 / 折断 `<style>` / 引外链」需要的全套字符。逐项丢弃、不报错（判据同 `card_style`：微调出错不改变产物的**性质**）。
- **两条传输各拼一次**：`ExportMenu`（GET query）与 `ExportPanel`（POST body），但变量串本身都出自 `serializeCardVars` 一处。

### 影响了什么

- [frontend/src/lib/card-prefs.ts](frontend/src/lib/card-prefs.ts)：`CardPrefs` 加三个字段 + `CARD_BG_OPTIONS` / `CARD_BORDER_OPTIONS` / `CARD_VARS`（受控变量**唯一一份**清单）/ `isLiftDisabled` / `serializeCardVars`；`applyCard` 补「先清空 inline」这一步。
- [settings/SettingsPage.tsx](frontend/src/components/settings/SettingsPage.tsx)：新增微调区（含 `TuneChip`），仅在骨架 = 卡片时显示。⚠️ 设置页的 8 个迷你小样**自动反映微调**（它们消费同一批变量，继承自 `<html>`），等于免费拿到实时预览。
- [ui/ExportMenu.tsx](frontend/src/components/ui/ExportMenu.tsx) / [ui/ExportPanel.tsx](frontend/src/components/ui/ExportPanel.tsx) / [routers/export.py](backend/app/routers/export.py)：`card_vars` 一路贯通（含 `_parse_card_vars` + `_CARD_TUNE_VARS` 白名单）。
- 测试：[cardPrefs.test.ts](frontend/src/test/cardPrefs.test.ts) 26 条（含 2 条跨语言同源守卫）、[test_export_cards.py](backend/tests/test_export_cards.py) 31 条（含 5 条逃逸字符参数化对照）。

**零回归**：三个字段默认 `null` ⇒ 渲染路径一个字节不多（有用例断言「没调过时三个变量全空」与「`card_vars` 不传时导出 HTML 逐字节等价」）。

---

## 2026-10-11 — 智能分类二轮：从「打开即分析」改成两阶段（配置 → 分析）+ 对话框脱困

### 起因

上一条把「建议 vs 结果」讲明白了，用户接着提了三条：

> 「点那个智能分类它因为初始就选择了 600，是它在点进去的一瞬间就会以 600 字为基准开始分类」
>
> 「关于是否利用现有的文件夹，我如果没有文件夹要不要建文件夹，备注文件夹还是什么的，**这些都得先选好最后一步才用户点击确认才开始**」
>
> 「这一个界面还是卡在那一个文档栏里面，我认为这个应该像 ai 对话界面一样可以悬浮在外面」
>
> 「文档可能没有标签，那么也可能得让用户提供几个基础的或者是全部交给大模型」

第二条与第一条是**同一个根因的两个面**：`handleOpenClassify` 在打开对话框的那一次事件里就 `loadClassifyPreview()`，于是「打开」与「花掉一次计费调用」是同一个动作。用户在对话框里看到的每一行，是**他还没同意就已经发生过**的结果——「初始就是 600」只是那件事最显眼的读数（600 是默认档，所以默认档就是实际被执行的档）。

### 根因二：`backdrop-filter` 会创建**包含块**

对话框写的是 `fixed inset-0`，但它挂在 `DocLibrary` 的 `<aside className="… backdrop-blur-[20px] …">` 里。**带 `backdrop-filter` 的元素会成为后代的包含块**——`position: fixed` 因此不再相对视口定位，而是相对那个面板。面板多宽，遮罩就多宽；用户看到的就是「对话框被关在文档栏里」。

这**不是** DocLibrary 一处的问题：`FloatingAI` 的根 `<aside>` 同样是 `backdrop-blur-[20px]`，对话侧的**重命名 / 删除 / 消息编辑**三个弹窗一直有同样的症状，只是没人报（它们出现得少、且通常浮窗本身就够大）。

### 改了什么

**① 两阶段交互（前端）** — `ClassifyDialog` 拆出 `phase: 'config' | 'review'`：

| 阶段 | 内容 |
|---|---|
| `config` | 取材深度（600 / 200 / 仅标题）、文件夹策略、新建上限滑条、标签策略（+seed 时的手填框）。**一个请求都不发** |
| `review` | 上一条已经落地的审定界面（逐行改夹/改标签 → 应用 N 篇） |

`onStart(opts)` 是**计费调用的唯一出口**——只有用户按「开始分析」才会走到 `loadClassifyPreview`。`preview` 一到就 `setPhase('review')`（后端是真相源，不等前端计时）。

`handleOpenClassify` 现在只做 `setClassifyOpen(true)` 与清场，**不发请求**——这条有专门的用例钉住（「打开对话框一个请求都不发」）。

**② 两项策略后端化** — 用户第二条问的「要不要建文件夹 / 怎么建」与第四条问的「标签从哪来」被收成两个显式枚举，**全部在开始分析之前选定**：

| 策略 | 取值 |
|---|---|
| `folderMode` | `reuse_or_new`（默认，优先复用、必要时新建）/ `reuse_only`（只复用，无合适的不归入）/ `tags_only`（本次不动文件夹，只打标签） |
| `tagMode` | `vocab`（默认，喂全库现有标签词表）/ `seed`（用户手填基础标签，**替代**词表而非叠加）/ `free`（不给词表，完全交给模型） |

「全库无标签」是现实场景（用户第四条的原话），前端据此把默认档从 `vocab` 落到 `seed`——**词表为空时喂一个空词表等于没喂**，而用户此时最需要的是「我给你几个起点」。有标签时仍默认 `vocab`。

**③ 判据的粒度分两档**（这是本轮最该被记住的一条）：

- `folderMode` / `tagMode` 非法 → **显式报错** `CLASSIFY_BAD_OPTION`，**且在校验处就返回**（不发起计费调用，有用例断言 `fake.calls == []`）
- `notesChars` / `maxNewFolders` / `seedTags` 非法 → **静默夹紧/清洗**

分野是「**这个值错了会不会改变产物的性质**」：选错「只打标签」与「动文件夹」会让产物性质完全不同而从结果上看不出异常；看 600 字还是 200 字只是量。与 `synthesis` 的读者意图/原料模式同一条判据。

**④ 同源：策略的两个投影** — prompt 侧（`_folder_section` / `_tag_section`）与收口侧（`_normalize_one`）必须收到**同一次策略取值**。所以两段整段由函数生成、模板只留 `${folder_section}` / `${tag_section}` 两个占位符——写死在模板里就是把一个投影钉住、另一个散在函数里（模式 #54）。「不动文件夹」时连分类空间都不喂（喂了却要求别用 = 诱导违反），端点响应里 `folders == []` 有用例钉住。

**⑤ 六处浮层 Portal 到 body** — DocLibrary 的分类/合成/删除确认三处 + ChatSidebar 的重命名/删除两处 + MessageItem 的消息编辑一处，全部改 `createPortal(…, document.body)`（项目先例：`ui/ContextMenu.tsx`）。并在 `DocLibrary` 的 `<aside>` 上加常驻注释声明「本面板内的任何浮层都必须 Portal」——**根因在祖先的样式上，不在浮层自己身上**，不写在会有人再犯。

### 实机抓到的一处（单测绿而功能全废）

`LLMService(config, …)` 的**第一参数是整份 config，不是 `resolve_llm_config` 的返回值**——后者是子字典，`LLMService.__init__` 自己会做 `config.get("llm", {})`，传子字典进去取到空 dict → `api_key` 恒为空 → 每次预览都报「LLM API Key 未配置」。

**而单测照样全绿**：端点用例注入的是假 `LLMService`，压根碰不到这个构造。全项目其余 10 处调用点（chat / generate / process / synthesis / epub …）传的都是顶层 config，只有新写的这处传错了。教训与项目既有纪律同款：**单测绿不等于功能对，新端点要走一遍实机**。

### 对照实验（三条，全部实测有效后还原）

| # | 实验 | 结果 |
|---|---|---|
| E | `handleOpenClassify` 里加回 `loadClassifyPreview` | 红 1 条（「打开对话框一个请求都不发」） |
| F | `startClassify` 丢掉 opts | 红 1 条（「配置阶段选的三项策略原样随请求发出」） |
| G | `activeDepth` 改回读本地 `cfg` 而非后端回显 | 红 1 条（「切档后段控高亮跟随后端回显」） |

⚠️ G 钉的是一个**真相源划分**：审定阶段的档位高亮读 `preview.limits.notesChars`（后端夹紧后的真实值），不读本地草稿——用户选了 5000 而后端夹到 2000 时，界面该显示 2000。

### 踩到的坑（改测试时）

对话框 Portal 到 body 之后，`container.querySelector('[data-synth-…]')` **恒为 null**，`fireEvent.click(null)` 报的是「provide a DOM element」——看起来像测试写坏了，实际是组件改了挂载位置。修法是新增 `inDialog()` helper 从 `document.body` 查，并在 helper 上方把原因写清楚（否则下一个人会以为是随手加的）。

顺带一个自伤：批量替换时「先切出 helper 定义再对 body 做替换」的写法会**命中定义自身**（`const allInDialog = (sel) => document.body.querySelectorAll(sel)` 被替换成自己）→ 无限递归。同源替换要避开定义体。

### 影响范围

- 后端：[classifier.py](backend/app/generation/classifier.py) 加两组枚举 + `clamp_*` / `validate_mode` 四个夹紧函数，`build_classify_prompt` 改收策略参数；[routers/classify.py](backend/app/routers/classify.py) 请求模型加四项 + 回显 `folderMode`/`tagMode`/`limits`；[test_classify.py](backend/tests/test_classify.py) 41 条
- 前端：[api/classify.ts](frontend/src/api/classify.ts) 加 `ClassifyOptions` 与两个策略类型；[ClassifyDialog.tsx](frontend/src/components/doc-library/ClassifyDialog.tsx) 重写为两阶段；[DocLibrary.tsx](frontend/src/components/doc-library/DocLibrary.tsx) 接线改造（`classifyOpts` 取代 `classifyNotesChars`）+ 三处 portal；[ChatSidebar.tsx](frontend/src/components/ai-chat/ChatSidebar.tsx) / [MessageItem.tsx](frontend/src/components/ai-chat/MessageItem.tsx) 各补 portal
- 前端 829 passed (63 files) / 后端 562 passed
- **无版本号变动、无打包**（随下一次打包带上）

---

## 2026-10-11 — 智能分类后续：把「建议」写明白（**不是写入 bug**）

### 起因

落地当天用户反馈：

> 「我点智能分类只是点进那个界面还没开始选确定分类他就直接给我分类了」

听起来像是「打开对话框就写库了」——与「preview 零写入、只有 commit 才写」的设计正面冲突。

### 排查：先把「有没有真写」证死

三条独立证据都指向**零写入**：

| 证据 | 结果 |
|---|---|
| `~/.limo/app.log` 里 classify 相关请求 | 只有 2 条 `POST /api/classify/preview`（11:37，status 200），**没有任何 `commit`、没有任何 `POST /api/folders`** |
| `~/.limo/index.json` 的 mtime | **09:31:47**（用户当天导入一篇文档的时刻），11:37 的 preview 之后**未变动** |
| 库内实际字段 | `folders` 0 个 · `folderId` 非空的文档 0 篇 · 带标签的文档 0 篇 |

⚠️ 日志里另有一条 `11:21:15 POST /api/classify/commit status=400`——那是**当天打包后验证 exe 时自己发的空 assignments 探测**（返回 `CLASSIFY_NO_ASSIGNMENTS`，同样零写入），不是用户操作。**读日志定位用户行为时，先排除自己留在里面的探针请求**，否则会把「自己 9 分钟前戳的那一下」算到用户头上。

### 根因：建议被读成了结果

preview 返回后，对话框里**每一行都被预填**——文件夹下拉已经选中了某个夹、标签输入框已经填满、行首徽标写着「**归入现有**」。整页读起来就是一份**已经保存好的分类**，而「这是建议、还没写库」在界面上**一个字都没说**。徽标那三个字尤其致命：它是**完成时**措辞。

修法是三处落点（同一件事的三个说法，**缺一即复发**）：

| 落点 | 改动 |
|---|---|
| 头部常驻声明 | 新增 `data-classify-notice`：`以下为模型建议，**尚未写入文档库** —— 确认后点右下角「应用这 N 篇」`。放头部而非主体，**不随逐篇列表滚动** |
| 行首徽标 | `归入现有` / `新建` → **`建议归入` / `建议新建`**（`不归入` 保持——它是中性态，加「建议」反拗口） |
| 确认按钮 | `应用` → **`应用这 N 篇`**（既写明确认范围，也让「还没确认」更显眼）；标题同步为「智能分类建议 · N 篇」 |

### 顺带建立的东西

`classifyDialog.test.tsx` 新增 `建议态被显式声明：头部提示 + 徽标带「建议」+ 按钮写明篇数`，**三条断言各配一次对照实验**（撤掉 notice 属性 / 徽标改回完成时 / 按钮改回「应用」→ **各自单跑只红这一条**，其余 14 条全绿）。这条用例钉的不是文案美观，而是**「建议 vs 结果」的语义不被下一次改版抹掉**。

### 影响范围

- 只动 [ClassifyDialog.tsx](frontend/src/components/doc-library/ClassifyDialog.tsx) 的呈现层与它的用例；**后端、API、store 零改动**（它们本来就是对的）
- **无版本号变动、无打包**（纯前端文案与结构，随下一次打包带上）

---

## 2026-10-11 — 智能分类：选中 N 篇 → 模型建议「文件夹 + 标签」→ 用户审定 → 写入

### 起因

用户报告「这几次更新导致原有的文件夹和分类全丢了」，接着提出：

> 「我觉得我们可以加一个功能就是自动分类？根据文件夹和文档的标题？」

丢分类的**根因已单独修完**（`P0-01-migration-rebuild-wipes-index`，2026-10-10 `24b6097`，
随 0.5.1 打包；那次丢的数据不可恢复）。本轮的定位因此不是「修丢失」，而是**把重新分类的成本降下来**。

### 做了什么

**主线**：文档库选中若干篇 → 一次 LLM 调用产出「每篇 → 建议文件夹 + 建议标签」→
用户在对话框里审定/编辑 → 确认后一次写入。**模型永不直接写库**。

```
选中 N 篇 → BatchBar「智能分类」
   → POST /api/classify/preview   （一次 LLM 调用，零写入）
   → 对话框审定（改夹 / 改标签 / 换取材深度）
   → 前端先建缺失的夹 → POST /api/classify/commit（一次加锁、一次写盘）
```

| # | 文件 | 动作 |
|---|---|---|
| 1 | [generation/classifier.py](../backend/app/generation/classifier.py)（新） | `CLASSIFY_PROMPT` + 取材三态 + 预算缩减 + 归一化边界 + `classify_documents()` |
| 2 | [routers/classify.py](../backend/app/routers/classify.py)（新） | `POST /api/classify/preview` / `POST /api/classify/commit` |
| 3 | [storage/store.py](../backend/app/storage/store.py) | `_merge_tags`（从 `update_transcripts_tags` 抽出，两处共用）+ `apply_classifications` |
| 4 | [generation/doc_text.py](../backend/app/generation/doc_text.py)（新） | `latest_notes_text` —— 「最新版」判据全项目只此一份 |
| 5 | [core/text_utils.py](../backend/app/core/text_utils.py) | `parse_json_object` 提升为公共（原 `chart_renderer._parse_json_object` 一行委托） |
| 6 | [api/classify.ts](../frontend/src/api/classify.ts)（新） / [ClassifyDialog.tsx](../frontend/src/components/doc-library/ClassifyDialog.tsx)（新） | 前端域 + 审定对话框 |
| 7 | [BatchBar.tsx](../frontend/src/components/doc-library/BatchBar.tsx) / [DocLibrary.tsx](../frontend/src/components/doc-library/DocLibrary.tsx) | 「智能分类」入口 + 建夹→写入编排 |

**四条产品决策**（用户逐条选定）：

| 决策 | 取值 | 为什么 |
|---|---|---|
| 产物 | 文件夹 **+** 标签两者 | 单层文件夹是项目既有能力，标签管跨夹主题 |
| 姿态 | 先建议、**确认后写入** | 模型分类错了在界面上看不出来，必须有人过一遍 |
| 分类空间 | **优先复用**既有文件夹，必要时新建且**设上限 5** | 不加复用压力 = 每跑一次多五个夹，文件夹比标签更容易失控 |
| 取材 | 标题 + 笔记开头，**深度由用户选**（600/200/仅标题） | 「看多少」是成本与准确率的权衡，该由用户决定；后端再按预算兜底缩减并**逐篇显式标注** |

### 为什么不扩 `POST /api/transcripts/batch`

它的形状是「**一个**动作、**一个** folderId、**一组**统一 tags」——表达不了「每篇各自的
folder + 各自的 tags」。硬塞进去会让 `BatchTranscriptsRequest` 七成字段只对某一个 action 有效。
而**它与 `batch` 存在的理由逐字同源**（[ingest.py:147](../backend/app/routers/ingest.py#L147)
的「一次写盘而非 N 次」）：走现有端点意味着最坏 N×2 次全量写盘。

### ⚠️ 实机验证抓到一个单测抓不到的缺陷（本轮最有价值的一条）

`classify.py` 最初写的是：

```python
llm_config = resolve_llm_config(config, req.modelId)
llm = LLMService(llm_config)          # ← 错
```

`LLMService.__init__` 的第一参数是**整份 config**（它内部自己做 `config.get("llm", {})`），
传 `resolve_llm_config` 的结果（llm 子字典）进去 → 取到空 dict → **`api_key` 恒为空** →
**每次预览都报「LLM API Key 未配置」**。

- 失效形态：**功能 100% 不可用**，而 34 条单测**全绿** —— 端点用例注入的是假 `LLMService`，
  压根不经过构造，`api_key` 是替身自己的字段。
- 全项目其余 **10 处**调用点（chat / generate / process / synthesis / epub / agent …）
  都是 `LLMService(config, model_id=…)`，只有这一处新写的传了子字典。
- 修法：`LLMService(config, model_id=req.modelId)`。
- 守卫：`test_classify.py` 的端点用例新增一条断言 —— **替身记下构造时收到的入参**，
  断言其中有 `"llm"` 键（`resolve_llm_config` 的返回**没有**这个键，判据因此精确）。
  对照实验：改回 `LLMService(llm_config)` → **只此一条红、其余 33 条全绿**，
  如实复现了「坏掉而没人发现」的形态。

### 端到端实测（隔离库，非真实库）

临时 `data_dir` + 沿用真实配置（密钥库回填 key），4 篇文档（3 带笔记 / 1 篇只有标题）：

| 观察项 | 实测结果 |
|---|---|
| 真实模型 JSON 解析 | ✅ `deepseek-v4-flash`，6.6s |
| 顺序不变式 | ✅ `suggestions` 顺序 == 请求 `docIds` |
| 无笔记篇降级 | ✅ `source="title_only"`、`chars=0`（**显式**，不假装看过） |
| 优先复用 | ✅ tr_v1/tr_v2 → 既有「论文」夹，tr_v3 → 既有「工程实践」夹；只有无笔记的 tr_v4 落到 `new: 行业观察` |
| 切档重跑 | ✅ `notesChars=0` 时全篇 `title_only`、标签从 4–5 个收到 2 个 |
| 写入 | ✅ 落库 `folderId` 与 `tags` 均正确；混合 1 有效 / 2 无效 → `affected:1` + `failed` 逐条明细 |
| 失败显式化 | ✅ 全失败 400 `CLASSIFY_ALL_FAILED` / 空 assignments 400 `CLASSIFY_NO_ASSIGNMENTS` / 无 key `CLASSIFY_NO_API_KEY` |
| **零副作用** | ✅ 只跑 preview 前后，库 `index.json` 的 size 与 mtime **逐字节一致** |
| **隔离自证** | ✅ 全程结束后，**真实库** `~/.limo/index.json` 的 size/mtime 与开始前**逐字节一致**（模式 #58：隔离要由断言回答，不靠直觉） |

### 同源与成对判据（本轮的三处）

| 判据 | 家 | 若各写一份的症状 |
|---|---|---|
| 标签 add / replace 合并 | `store._merge_tags` | add 语义在两处慢慢漂移，无人察觉 |
| 「最新版笔记正文」 | `generation/doc_text.latest_notes_text` | 版本序判据出现第二份实现 |
| JSON 对象抽取 | `core/text_utils.parse_json_object` | 逐 `{` 试 `raw_decode` 的容错逻辑各写各的 |

三处都有守卫：`test_classify.py` 里有一条专测「改 `_merge_tags` 会让
`update_transcripts_tags` 与 `apply_classifications` 的用例**同时红**」；
`parse_json_object` 的身份断言（`chart_renderer.parse_json_object is parse_json_object`）
钉住委托关系。

### 影响范围

- **不 bump `PROMPT_VERSION`** —— 它标记的是「产物生成的 prompt 版本」（写进产物自证头），
  本轮**不产生产物**（分类只改文档级元数据）。
- **不新增 capabilities 常量** —— 分类空间是「用户数据 + 模型输出」，不是固定枚举，
  无需「后端注册 / 前端列举」的同步；上限经 preview 响应的 `limits` **回显**给前端。
- **零回归面**：`update_transcripts_tags` 行为逐字不变；`chart_renderer` 的 JSON 解析行为
  逐字不变（原有测试仍绿）；`synthesis` 的取材行为逐字不变。
- 前端**不本地重算任何判据**：切取材深度**重调 preview**（与合成「切原料模式重拉预览」同款）。
- 折叠左栏 = 卸载 `DocLibrary`，而分类是**计费调用** → 持 `AbortController`，
  cleanup 里 abort，触发后端 `disconnect_watcher` → `CLASSIFY_CANCELLED`。

---

## 2026-10-11 — 两类「卡片」的实机修正：面板边框提亮 + `card` 提到规范最前（PROMPT_VERSION `"12"` → `"13"`）

### 起因

上一轮刚落地「章节卡片改由标记决定」。用户实机生成一篇后反馈：

> 「现在有一个问题就是其他的那些卡片边框会显示吗，目前我生成的感觉显示效果不太好」

排查先扫全库产物，拿到两条事实：

| 观察 | 数据 |
|---|---|
| 语义块**在用** | v12 那篇写齐了 5 种：`key-takeaway` / `alert` / `timeline` / `metric` / `recommend` |
| 章节卡片**没在用** | 全库 6 篇含标记的产物里 **`card` 标记 0 个**（v12 只跑过 1 篇文档 —— 样本小，但现象干净） |

于是拆成两个独立问题：**视觉强度**（面板族容器看不见）与**引导位置**（`card` 被略过）。

### 做了什么

**① 中性面板族边框 `--tk-border-light` → `--tk-border`**

`metric` / `table` / `code` / `image` 这一族的容器在浅色主题下**等于没有**。以默认 `warm`
基调实测：边框 `#EBE7E2` 离正文底 `--tk-surface`（`#F4F1EE`）**只差 9 个灰阶**，
底色 `--tk-elevated`（`#FAF8F5`）离正文**只差 6 阶** —— 两层叠加后，用户看到的约等于
「几行没有容器的大数字」。换成 `--tk-border`（`#D6D3CE`，离正文 **30** 阶，3.3 倍对比度），
与章节卡片默认方案 `gpt`（`--card-border: 1px solid var(--tk-border)`）是**同一套语言**
—— 两者本来就是嵌套关系（卡片里包面板），强度同级才不会看起来像两种东西。

| # | 文件 | 动作 |
|---|---|---|
| 1 | [blocks.css](../frontend/src/styles/blocks.css) | `metric` 与 `table`/`code`/`image` 两条规则块的 `border` 换 `--tk-border`（把「为什么不复用 light 档」与具体灰阶差写进注释） |
| 2 | [markerPrefs.test.ts](../frontend/src/test/markerPrefs.test.ts) | 新增守卫「中性面板族的边框用 `--tk-border`，不是 light 档」 |

⚠️ `timeline` 的**竖连**线仍用 light 档（细线本就该淡），故守卫限定在两条**容器**规则内
—— 写成全局断言会把它一起误判为红。

**② `card` 从列表末项提到规范最前**

`get_markdown_block_format()` 按**层级**重排：先讲节级的 `card`（管整节，粒度最大），
再讲段级的九种；`card` 的示例紧跟它的说明，不再与九种挤在整段末尾。
⚠️ **判据与类型一字未改** —— 改的只是位置与结构，与上一轮刚立的「不堆劝退话术」不冲突。

| # | 文件 | 动作 |
|---|---|---|
| 3 | [prompt.py](../backend/app/generation/prompt.py) | `get_markdown_block_format()` 重排为「① 整节成卡 / ② 段内标记」两节 |
| 4 | [prompts/`__init__`.py](../backend/app/generation/prompts/__init__.py) | `PROMPT_VERSION` `"12"` → `"13"` |

**推论**（写下来免得后人重新推）：九个块级标记是**同一形态的反复**（只有 `type` 值不同），
模型扫一眼就抓住了模式；`card` 是**唯一形态不同的那一个**（不包裹内容、写在标题之下、
没有闭合标记），吊在长列表第 10 项、示例还单独放在末尾时，最容易被当成「又一个 type」略过。

### 影响范围

- **预览与导出同步**：`blocks.css` 是导出侧 `_load_block_css()` 读的**单一真相源**，
  改这一处两侧同时变；`--tk-border` 在 `_CARD_TOKENS_CSS` 里**已有三态定义**，无需补令牌
- **零回归不受影响**：`--tk-border` 是既有令牌，`paper` 骨架下同样生效（面板族在两种骨架
  下形态一致，刻意不是卡片形态）
- **历史产物**：无标记的逐字节不变；有标记的**面板会更明显** —— 这正是本轮目的

### 验证

- `bash scripts/check-all` → **5 通过 / 0 失败**（前端 808 tests / 62 文件）
- **对照实验 3 条**（每条都改坏一次、确认真的变红、再还原）：
  - 改坏 prompt 的「下面一行」→ `test_规范教了节级修饰符_card` 红（还原后 19 passed）
  - **整段删掉 `card` 引导** → 同一条红（模拟「`card` 从未写进规范」的真实失效场景）
  - `blocks.css` 的面板边框改回 light 档 → 新守卫红（还原后 16 passed）
- ⚠️ **待实机验收**：`card` 的新位置是否真能提升遵循率 —— 需再生成 2–3 篇不同体裁的确认
  （这是本轮唯一没有自动信号的部分）；以及面板提亮后的整体观感

---

## 2026-10-11 — 章节卡片改由标记决定：`card` 节修饰符（PROMPT_VERSION `"11"` → `"12"`）

### 起因

上一轮（2026-10-10）把卡片骨架轴做成了**每个 H2 无差别一张卡**。用户当天判定这不对：

> 「章节卡片主要是太简单粗暴了,卡片形式用在需要强调的地方就行了」

澄清后用户定下实现方式：

> 「卡片由标记决定,然后只给标记过的章节成卡，8种视觉方案可以适当的去引导使用」

随后追加一条默认值决定：

> 「还有就是默认使用的视觉方案就是加重边线以及背景颜色与卡片颜色相同」

### 做了什么

骨架轴 `card` 的**判据从「每个 H2」换成「被模型标过的节」**：模型在 `## 标题` 的**下一行**
写 `<!-- limo-block:v1 type="card" -->`，这一节才渲染成卡；其余章节保持普通正文
（于是卡片在页面里是**混排**的，不再是整齐的一列）。8 套视觉方案全部保留。

| # | 文件 | 动作 |
|---|---|---|
| 1 | [markdown-sections.ts](../frontend/src/lib/markdown-sections.ts) / [markdown_sections.py](../backend/app/services/markdown_sections.py) | 切分结果多一个 `card` 字段；H2 之后紧跟的那行 `card` 标记决定它 |
| 2 | [markdown-markers.ts](../frontend/src/lib/markdown-markers.ts) / [artifact_blocks.py](../backend/app/core/artifact_blocks.py) | 新增 `SECTION_CARD_TYPE` / `is_section_card`；**`match_open` 对 `card` 返回 `null`** |
| 3 | [limo-blocks.json](../frontend/src/test/fixtures/limo-blocks.json) | `meta` 加 `sectionTypes` + 5 条 `h2Cases`（标记 × 切分的交叉） |
| 4 | [cards.css](../frontend/src/styles/cards.css) | 删 `.md-cards` flex 容器（卡片与普通节是兄弟节点，套容器会把普通节的**每一行**拆成 flex item）；间距改由 `.md-card { margin-block }`；删 `.md-card:last-child` 特例；工具条补「章节卡片」开关 |
| 5 | [cards.css](../frontend/src/styles/cards.css) | 默认方案 `gpt` 改为用户定的「**加重边线 + 背景与正文同色**」（`--card-lift: 0%` + `border: var(--tk-border)`） |
| 6 | [cards.css](../frontend/src/styles/cards.css) | 新增 `.card-preview` 迷你视觉小样 + [SettingsPage.tsx](../frontend/src/components/settings/SettingsPage.tsx) 用它，`hint` 由 hover `title` 提升为可见文字 |
| 7 | [prompt.py](../backend/app/generation/prompt.py) `get_markdown_block_format()` | 多出第十种 `card`，写法与其余九种**不同**（见下） |
| 8 | [prompts/`__init__`.py](../backend/app/generation/prompts/__init__.py) | `PROMPT_VERSION` `"11"` → `"12"` |

**零改动**：`MarkerContent.tsx`、`render_blocks_html`、`parse_blocks` / `parse_markers` 的
主路径、`export.py` 的全部导出分支（卡片容器由既有的 `md-card` 分支产出）、Word 侧 `_render_block`。

### ⚠️ `card` 定性为**节修饰符**，不是块 —— 一个决定解决两个隐患

前九种 type 都是**块**（包裹式，有闭合标记），`card` **根本不是块**：它标的是「这个 `##` 标题
及其全部后续内容」，写法是**写在标题之下、没有闭合标记**。前端 `isSectionCard` / 后端
`is_section_card` 与 `match_open` **是两个判据**（`match_open('…type="card"…')` 返回 `null`），
`test_对照实验_两个判据不是同一个函数的别名` 钉住它俩方向恰好相反。

把 `card` 当块处理会同时引爆两件事：

1. **吞标题**。模型的本能是把标记写在标题**之前**（其余九种都是那样）。行分类器一旦进入
   块内状态，那个 `## 标题` 会被整行跳过 —— **整节从文档结构里消失**，卡片、目录、导出全线受影响。
   现在 `card` 标记行对标题扫描**完全透明**，写在前面也只是「不成卡」（宽容降级在**内容侧**：
   标记行照剥、标题照留、一个字不少，有断言钉住）。
2. **吞同一节里的块标记**。`card` 若开块，节内第一个真正的块（`alert` / `table`…）的闭合标记
   会被当成 `card` 的闭合。现在节内块照常解析。

**零残留**是免费拿到的：`match_open` 返回 `null` 之后，`parse_blocks` 里紧跟的**标记行剥除**
正好接住 `card` 标记行，**无需新分支**。

### ⚠️ `strip_break` 挂在 `card` 上，不是挂在骨架轴上

「成卡的节剥掉尾部的 `---`」这条（卡的下边界已承担分隔功能）此前挂在「开了卡片骨架」上。
现在它挂在**这一节是否成卡**上 —— 两种输入的表现因此不同：

| 输入 | 结果 |
|---|---|
| `## 甲` + `card` 标记 + 内容 + `---` | 剥（卡底挂一条分隔线是噪音） |
| `## 甲` + 内容 + `---`（**无标记**） | **保留** —— 它没有卡边界，`---` 是**唯一**的分隔手段 |

漏掉后者就是「开了卡片模式、普通章节之间的分隔线却消失了」—— 一个与骨架轴无关的损失。
三个方向（成卡的剥 / 不成卡的留 / **章内**的 `---` 恒留）在 `test_export_cards.py` 里各有一条。

### ⚠️ 卡片 CSS 不再有外层容器

`.md-cards { display: flex }` **已删除**。混排之后卡片与普通章节是**兄弟节点**，套一层 flex
容器会把普通章节里**每一行**内容变成一个独立 flex item，布局直接散架。卡片间距改由
`.md-card { margin-block: var(--card-gap) }` 承担 —— 普通流里相邻块的外边距会**折叠**，
两张卡之间是 `--card-gap` 而不是它的两倍。`.md-card:last-child { margin-bottom: 0 }`
这条特例随之删除（同一个 margin 两端对称，特例只会让它变怪）。

### ⚠️ 设置页的迷你小样：让 8 套方案的差异**看得见**

用户要「引导使用」，而此前 8 套方案**只有名字和一句 hover 提示**，看不出差异也就谈不上引导。
落地方式是给每个方案按钮加一个 42×28 的空 `<span>`，用**与 `.md-card` 完全相同的那批
`--card-*` 变量**画一个缩微卡片（`.card-preview`）：

- **依据**：`[data-card-style="x"] { --card-*: … }` 这批规则**不带** `[data-skeleton="card"]`
  前缀（那个前缀只在渲染规则上），故小样元素自己写一个 `data-card-style` 就能取到变量 ——
  **没有第二份真相源**，方案块里改任何取值，卡片与小样同步变；
- 圆角与内框按 **0.5** 缩放（真实卡片是 16–36px 留白衬出来的，小样只有几十像素，原值会让
  圆角吃掉整个方块）—— 缩放的是**同一个变量**，不是新写的一份取值；
- 小样是空元素、不进导出（`cards.css` 会被导出侧读走，但导出 HTML 里没有 `.card-preview`）。

### 影响范围

- **默认观感变了**：`gpt`（默认方案）由「无边框 + 抬升底 + 阴影」改为「**加重边线 + 与正文同底色**」。
  这是用户明确定的默认值，不是回归。
- **历史产物**：无标记的产物在 `card` 骨架下**一张卡都不出**（内容一字不少）—— 这是本轮**有意**
  的行为改变。想要卡片的旧产物需重新生成。
- **`paper` 仍然零回归**：`applyCard()` 对两个属性都 `removeAttribute`，`cards.css` 每条规则带
  `[data-skeleton="card"]` 前缀 —— 零回归靠**选择器不匹配**而非取值相同。

### 验证

`bash scripts/check-all` → 5 通过 / 0 失败（前端 807 tests）。
跨语言守卫：`limo-blocks.json` 的 `sectionTypes` + 5 条 `h2Cases` 由两侧测试共同消费
（`markdownMarkers.test.ts` / `test_artifact_blocks.py`），`h2-sections.json` 保持「**纯切分**」
定位（`card` 恒 `false`），交叉场景一律住 `h2Cases` —— 同一批形状不在两处各写一遍。

**设置页小样**另加两条守卫（`cardPrefs.test.ts`）：① `.card-preview` 块真的消费那四个
`--card-*` 变量、且**不带**骨架前缀（带了就永远不匹配 ⇒ 小样是个看不见的空方块）；
② `SettingsPage.tsx` 里那个类名**真的用上了**（防「CSS 已就位、JSX 还没用」这个零报错的
中间状态）。⚠️ 第 ② 条第一版写成裸子串 `toContain('card-preview')`，**对照实验发现它恒真**
（`card-previewX` 也包含它）—— 改成 `className="…\bcard-preview\b"` 后重做对照实验才真的变红。
两条对照实验（改坏 JSX / 删掉 CSS 规则）均实测可捕获。

⚠️ 实机验收（模型遵循率：会不会写 `card` 标记、会不会滥用）**待用户操作**。

---

## 2026-10-11 — 内容卡片三种：表格 / 代码 / 图片（PROMPT_VERSION `"10"` → `"11"`）

### 起因

用户先点明要做的三种：

> 「三个：表格 + 代码 + 图片」

随即给出了**实现方式的定义**，这一句定了整轮的形状：

> 「其实可以考虑作为就是同种类型的表现形式只是在原有的基础上再加上了围栏而已然后他就按照那种卡片显示」

即：**不是**把表格改写成卡片、把代码拆成散文 —— 它们**本来就是**真表格 / 真代码块 / 真图片，
只是外面多了一层 `limo-block` 围栏，于是按卡片显示。**块内一个字节都不动。**

最后补了一条：

> 「还有一点就是咱们原来没有卡片的时候显示相关的那个也可以做成一个可勾选项当做用户需可能需要保留的形式」

→ **不新造开关**，见下。

### 与前六种「性质不同族」—— 这是本轮最要紧的一条

| | 前六个（`key-takeaway`/`alert`/`recommend`/`metric`/`steps`/`timeline`） | 后三个（`table`/`code`/`image`） |
|---|---|---|
| 标的是什么 | **普通段落** —— 「这一段文字是什么性质」 | **内容本身** —— 表格 / 代码 / 图片，本来就与正文不同质 |
| 块内原本是 | 散文 | 富结构（`<table>` / `<pre><code>` / `<img>`） |
| 加标记的作用 | 换形态（色条 / 淡底 / 横排 chip / 时间线） | **只是外面多一层壳**，块内原样穿过 |
| CSS 做什么 | 换 `display`、换子元素排布 | **不换 `display`、不换子元素**，只做容器装饰 |

⚠️ 所以这一族**刻意不是「把表格/代码/图片改造成卡片的形状」**。图鉴对代码面板有明确判词
（[cards.md](../design/cards.md) 附录 B 第 4 条）：「不要为了统一视觉而把代码改成普通文字卡片，
否则会影响可读性和可用性。」保住等宽、缩进、复制、横向滚动，是这三个 type 能成立的**前提**。

### 改了什么

| # | 文件 | 动作 |
|---|---|---|
| 1 | [markdown-scan.ts](../frontend/src/lib/markdown-scan.ts) / [artifact_blocks.py](../backend/app/core/artifact_blocks.py) | 产物集合 6 → 9（`table` / `code` / `image`），两侧镜像 + 一段「性质不同族」注释 |
| 2 | [limo-blocks.json](../frontend/src/test/fixtures/limo-blocks.json) | `meta` 两数组 + 3 条 case（各带**富结构 body**：真表格 / 真围栏代码 / 真图片引用） |
| 3 | [blocks.css](../frontend/src/styles/blocks.css) | 一族中性容器规则（四边浅边框 + `--tk-elevated` 底 + 圆角，**不带语义色**）+ 首尾 margin 归零 + `image` 居中；末尾各配 `decor=off` 归零规则 |
| 4 | [prompt.py](../backend/app/generation/prompt.py) `get_markdown_block_format()` | 6 种 → 9 种；补「块内**照原样写**」「`image` 地址**原样照抄**」两条硬约束 |
| 5 | [docx_export.py](../backend/app/services/docx_export.py) `_MARKER_COLORS` | +3 组**中性**配色（不是语义色 —— 染色等于替它加一层它没有的意思） |
| 6 | [prompts/`__init__`.py](../backend/app/generation/prompts/__init__.py) | `PROMPT_VERSION` `"10"` → `"11"` |

**零改动**（由「形态差异全在 CSS」这条约束保证，逐处确认过）：`MarkerContent.tsx`、
`ChatMarkerContent.tsx`、`artifact_blocks.py` 的 `parse_blocks`/`strip_blocks`/`render_blocks_html`、
`markdown-markers.ts`、`export.py` 的全部导出分支、前端导出侧。

### ⚠️ 卡片外壳的选择器只能落在 `.md-block` 自己身上

调研时发现**预览侧与导出侧的 DOM 本就不同**（这层差异**先于本轮就存在**，不是本轮引入的）：

| 内容 | 预览（React） | 导出（`markdown_render.py`） |
|---|---|---|
| 表格 | `.md-table-wrap`（Tailwind `overflow-x-auto my-4 rounded-lg border`）> `<table>` | **裸 `<table>`** |
| 代码 | `.group.relative`（工具条：语言标签 + 复制按钮）> `SyntaxHighlighter` | `<div class="codehilite"><pre><code>`（Pygments） |
| 图片 | `<img>` | `<img>` |

故卡片规则**只能**写成 `.md-block[data-block-type="…"]` 或作用在块内**通用元素**
（`table` / `pre` / `img`）上 —— **绝不能碰 `.md-table-wrap`、`.group` 或任何 Tailwind class**
（导出侧根本不存在，会当场分叉）。代价如实记录：预览侧是「面板里嵌一层容器」，导出侧只有面板。
`blocks.css` 里这一族的注释写明了这一点。

### ⚠️ 用户要的「保留原来的形式」= 既有装饰开关的射程扩张，不是新开关

既不新造 UI，也不新造字段 —— 既有的 `lib/marker-prefs.ts`「**内容块装饰**」（原「重点块装饰」）
语义逐字就是用户要的：**关掉装饰、内容一字不少**。把这一族纳入它的射程即成立：

- 关掉后表格退回裸 `<table>`、代码退回带工具条的代码块、图片退回靠左 ——
  **与从未写过围栏在渲染上逐字相同**；
- 卡片自己的 `margin: 16px 0` 在 `decor=off` 时**刻意保留**（恰好等于预览侧 `.md-table-wrap`
  的 `my-4`），故「关掉装饰」与「从未包过」的**间距一致**；
- 代价只是 `blocks.css` 末尾多三条归零规则 + 三处文案改准（「重点块装饰」覆盖表格/代码/图片后
  不再准确 → 统一为「**内容块装饰**」）。

⚠️ **漏写某一种的 `decor=off` 归零规则，症状是「开关对那个 type 无效」** —— 不报错、不崩溃，
只是那一种块永远带着装饰。`marker-prefs.ts` 文件头已把这条写进纪律。

### 代价（如实记录）

- **Word 侧的 `table` 卡是「表格套表格」** —— 外层单单元格表格（所有 type 在 Word 里都是这个
  形态，只有配色不同）+ 内层真表格。`code` / `image` 同理只有外框，没有工具条 / 居中表达。
  这是**能力边界不是实现省略**：想复刻预览形态就得让 Python 复刻 React 的结构（模式 #54）。
  已写成断言钉住（`test_word_内容卡片三种_块内富结构原样穿过且_table_是表格套表格`），
  免得后人以为「Word 侧也是并排的」。
- **`image` 卡只覆盖图鉴媒体卡的「外框层」** —— 贴边大图 + 加粗标题 + `--tk-dimmed` 元信息
  那一整套没做（那需要块内出现「标题 + 元信息」的结构）。故 13 媒体卡在 [cards.md](../design/cards.md)
  附录 A 里**同时**留在「🆕 真正全新」桶，注明「部分落地」。

### 测试

- `test_export_blocks.py`：HTML 八种新 type 都包 `md-block`；**新增一条专断「富结构穿过导出链」**
  （`<table>` / `<pre><code>` / `<img>` + `alt`）；Word 侧同理 + 表格套表格的结构断言。
  ⚠️ 断「文字在不在」时加了 `_text_of()` helper（剥标签）—— **Pygments 会把 `cfg = load()`
  拆成一串 `<span>`**，字面匹配会假红；图片的说明文字在 `alt` **属性**里（不在文本节点），
  故它归**结构层**断言。这两处都是「断言不该受实现细节影响」的实例。
- 新增 `_RICH_BODY` 常量：用**纯文字 body** 测这三个 type 是**假绿**（「块内富结构原样穿过」
  这件事一件证据都没有）。
- `test_block_prompt.py`：`test_产物规范列出了白名单里的每一个类型` 由集合派生，**自动覆盖**；
  ⚠️ 顺带**收紧**了 `test_两侧规范不串味` —— 反串味改判 `type="xxx"` 而非裸子串
  （`table` / `code` / `image` 都是英文常用词，裸子串会因 `formats.md` 里随手一句英文而**假红**，
  而那并不是串味）。这是**顺带修的真缺陷**，不是本轮必需。
- `markerContent.test.tsx`：新增「内容卡片三种：块内那份富内容原样穿过渲染层」。
- `setup.ts`：补 `ResizeObserver` stub —— ⚠️ jsdom 没有它，而 `react-medium-image-zoom`
  在 mount 时要用，症状是**渲染含 `<img>` 的 Markdown 整条用例崩在 commit 阶段**
  （`ReferenceError: ResizeObserver is not defined`），与标记解析毫无关系。
- 门禁：待跑 `bash scripts/check-all`。

### 明确不做

- **不加勾选项**（同前几轮：标记的价值全在「模型自主判断」，要求用户先勾选 = 把功能取消）；
- **不给 `image` 内层结构**（贴边大图 / 标题 / 元信息）—— 那需要产物侧 DOM 结构变化；
- **不做轮播卡组**（16，容器形态，非待办）；
- 不改版本号、不打包、不发布。

---

## 2026-10-11 — 横向步骤条 / 竖向时间线：把「简单线性图」从图表管道挪到语义块（PROMPT_VERSION `"9"` → `"10"`）

### 起因

用户先提了一串「哪些图该由卡片承担」的设想，我梳理出边界清单后他给了**真实频率**这一关键输入：

> 「简单的一条线横向的线性竖向的线性可以考虑使用卡片替换因为这两个出现的比较频繁」

随即拍板「横竖各一个 type」，并批准实施。

### 判据：Markdown 列表能保真的关系，不值得付一整条渲染链

单链（一条线走到底 / 一串先后阶段）过去要付 **语义 JSON → `chart_layout` 算几何 →
`chart_svg` → PIL 出 PNG** 一整条链，而它表达的恰恰是**列表本身就能表达**的东西。
留在线上的都是列表表达不了的关系：**分支 / 回环 / 二维 / 多参与者时序**。

故本轮：**迁移** `hchain`（横链）与 `timeline`（竖列）→ 语义块 `steps` / `timeline`；
`tree` / `layers` 按同一判据也在迁移候选里（嵌套列表能表达），但用户点名的是这两个，
**本轮只做这两个**，其余留待需要时再说。

### 改了什么

| # | 文件 | 动作 |
|---|---|---|
| 1 | [markdown-scan.ts](../frontend/src/lib/markdown-scan.ts) / [artifact_blocks.py](../backend/app/core/artifact_blocks.py) | 三集合各加 `steps` / `timeline`（都进 `ARTIFACT`），两侧镜像 |
| 2 | [limo-blocks.json](../frontend/src/test/fixtures/limo-blocks.json) | `meta` 两数组 + 2 条 case（20 条） |
| 3 | [blocks.css](../frontend/src/styles/blocks.css) | `steps` = 块内**无序列表**横排 chip + `→` 串联；`timeline` = 块内**有序列表** + 自定义 counter 圆形编号 + 竖连线（末项不画）；各配一条 `decor=off` 归零规则（`steps` 退回竖排圆点列表、`timeline` 退回默认数字编号） |
| 4 | [prompt.py](../backend/app/generation/prompt.py) `get_markdown_block_format()` | 4 种 → 6 种；⚠️ **必须写明「用无序列表 / 用有序列表」**——形态差异就靠块内子元素类型 |
| 5 | [prompt.py](../backend/app/generation/prompt.py) `get_chart_format()` | **删 `hchain` / `timeline` 两种引导**（九种 → 七种）+ 开头补指路语 + `diff` 段四处跟着改 + 两个示例里的 `hchain` 换成 `flowchart` |
| 6 | [docx_export.py](../backend/app/services/docx_export.py) `_MARKER_COLORS` | +2 组配色（`steps` 蓝 / `timeline` 紫） |
| 7 | [prompts/`__init__`.py](../backend/app/generation/prompts/__init__.py) | `PROMPT_VERSION` `"9"` → `"10"` |

### ⚠️ 最容易漏的一处：两处必须同批改

语义块规范是**无条件注入**的（无勾选项，这是它的设计），而图表规范**勾选后才注入**。
若只加语义块、不删图表引导，那么**勾了「图表」的那批产物**会在同一篇 prompt 里同时收到
「线性步骤用列表表达」与「流程/步骤 → `hchain`」两条**互斥指令** —— 模型只能违反一边，
而产物上看不出异常。这是本轮最需要对偶修改的地方，`test_prompt.py` 已改成**反向断言**
（`'"hchain"' not in cf`）+ 一条「来源引导段里也不能残留」（diff 段是第二个落点，短、最易被跳过）。

⚠️ **`_NOT_EXPOSED` 里的 `hchain` / `timeline` 是「引导名单」不是「算子名单」**：两个算子
**仍在** `chart_layout._LAYOUTS` 里（历史产物、插件、将来别的入口照样渲染），删的只是 prompt
引导。测试同时钉住「引导里没有 + `_LAYOUTS` 里有」，防止有人顺手把算子也删了。

### 代价（如实记录）

**Word 侧 `steps` 与 `timeline` 长得一样** —— 都渲染成一个单单元格表格 + 竖排列表，只有配色
不同。预览侧的横排靠 `display:flex` + 伪元素、时间线靠 counter + 伪元素，Word 的 XML 没有
对应表达。想区分只能改成多单元格表格 = 让 Python 复刻预览的结构，那正是 `blocks.css` 文件头
明确否掉的方向（模式 #54）。故**不修**，改为在 `test_export_blocks.py` 里**写成断言**钉住现状
（哪天有人以为「Word 侧也是横的」而按此改代码，这条会先红）。HTML / PDF 侧正常。

### 测试

- `test_artifact_blocks.py`：用例下限 18 → 20；关键词守卫加 `steps` / `timeline`（它们与既有
  几条**不同类** —— 那几条是降级行为，这两个是「块内列表结构原样保留」的形态判据）。
- `test_export_blocks.py`：HTML 五种新 type 都包 `md-block`；**新增一条专门断「列表结构穿过
  导出链」**（`steps` 出 `<ul>` 且不出 `<ol>`、`timeline` 反之）—— 那是纯 CSS 方案**唯一依赖
  的 Markdown 层前提**，塌了的话两条 CSS 规则静默不命中，而包装断言照样绿；Word 配色守卫的
  名单改从 `_MARKER_COLORS` 的**键**派生（`== 4` → `== len(types)`），加 type 时自动跟上。
- `markerContent.test.tsx`：表驱动扩到五种 + **新增「块内列表类型」结构守卫**（反面对照放在
  块内 `querySelector`，只断「有 `<ul>`」抓不到「两者都渲染成同一个标签」）。
- `test_prompt.py`：`_exposed` 改成从 `_NOT_EXPOSED` 排除集派生（7 种）、「九种结构」→
  「七种结构」、两条反向断言用**带引号形态**（裸词 `timeline` 会被指路语命中而恒红 ——
  这是「判据得自证有区分力」的一个实例）。
- 门禁：`bash scripts/check-all frontend` 3 通过、`check-all backend` 1 通过。

### 明确不做

- **不加勾选项**（与既有纪律一致，`test_block_prompt.py::test_没有勾选项` 钉着）；
- **不迁 `tree` / `layers`** —— 同一判据下它们在候选里，但用户点名的只有横竖两种；
- **不删算子**（见上）；
- 不改版本号、不打包、不发布。

---

## 2026-10-11 — 语义块一次扩到四种（产物 `alert`/`recommend`/`metric` + 对话 `info`/`digest`）（PROMPT_VERSION `"8"` → `"9"`）

### 起因

上一轮落地的 `limo-block` 表达手段各只有一两种（产物侧 `key-takeaway`、对话侧 `card-group` /
`follow-up`）——模型想标一个风险提示、给一条明确建议、列几个关键数字，都只能写成普通段落，
**读者看不出这几段与别段性质不同**。用户看过 [design/cards.md](../design/cards.md) 附录 A 的
分桶后拍板：**「可以把 a 选项的线全做了」**——把「🆕 真正全新」里**适合由模型产出**的 5 种一次做掉。

### 落点按真实场景分（用户拍板）

| type | 中文 | 侧 | 为什么落这侧 |
|---|---|---|---|
| `alert` | 提醒 / 异常 | **产物** | 唯一有**现成内容在等它**的形态——扫描件 OCR 失败占位、合成管道 8 态降级标注，这些失败说明现在就是普通段落；其两条硬约束与项目「失败显式化」逐字同源 |
| `recommend` | 推荐 | **产物** | 与 `alert` 同族（结论型），产物是「读完给结论」的场景 |
| `metric` | 指标 | **产物** | Excel 分析 / 数据类材料的数字落在这里 |
| `info` | 信息卡 | **对话** | 「XX 是什么」是对话高频问题；对话正文最缺组织手段 |
| `digest` | 摘要卡 | **对话** | 长回答开头的整体印象 |

### 核心设计约束（本轮全部依赖它）

> **产物侧的形态差异只允许用 CSS 表达，一行业务 DOM 结构都不许变。**

依据是 `blocks.css` 文件头那条既有纪律：**它是单一真相源，导出侧 `_load_block_css()` 读的就是
这一份**，导出 HTML 由 **Python** 的 `render_blocks_html` 生成。产物侧一旦出现「某个 type 的 DOM
结构与别的不一样」，Python 就得复刻 React 的结构 —— 那是同一份判据的第二份实现（模式 #54），
且「预览所见 = 导出所得」会退化成靠人工对齐维持。

**收益是连锁的**：`MarkerContent.tsx` 零改动、`render_blocks_html` 零改动、`blocks.css` 只加
选择器、导出与预览**天然一致**。**代价如实记录**：`metric` **不做并排**——并排需要网格容器，
那就是结构变化；改为纵向的「`### 名称` 收成小标签 + 紧随其后的段落放大成数字」，靠 `h3 + p`
相邻兄弟选择器实现，纯 CSS。

⚠️ **`alert` 用 `--tk-warning` 而不是 `--tk-error`**，这是刻意的：图鉴自己写了「**不应该只用
颜色制造紧张感**，而应当明确告诉用户出了什么问题 / 有什么影响 / 下一步如何处理」。既然严重度
该由**文字**承担，颜色就不该替它分级 —— 用覆盖面更广的 warning（风险 / 重要前提 / 内容不完整 /
异常都在射程内），避免把一个「重要条件」误渲染成「失败了」。

### 改了什么

| # | 文件 | 动作 |
|---|---|---|
| 1 | [markdown-scan.ts](../frontend/src/lib/markdown-scan.ts) / [artifact_blocks.py](../backend/app/core/artifact_blocks.py) | 三集合各加 5 个 type（`ARTIFACT` +3 / `CHAT` +2），两侧镜像 |
| 2 | [limo-blocks.json](../frontend/src/test/fixtures/limo-blocks.json) | `meta` 三数组 + 5 条新 case（18 条） |
| 3 | [blocks.css](../frontend/src/styles/blocks.css) | 3 种本体形态 + `decor=off` 归零规则（`metric` **只收容器、字号保留**—— 开关语义是「收起装饰性容器」，字号是内容呈现；强行退回要复刻 vendor 的 `h3` 字号 = 第二份真相源） |
| 4 | [chat-blocks.css](../frontend/src/styles/chat-blocks.css) / [ChatMarkerContent.tsx](../frontend/src/components/ai-chat/ChatMarkerContent.tsx) | `ChatInfoCard` / `ChatDigestCard`（lucide 图标，对话侧**不受**纯 CSS 约束——`chat-blocks.css` 明确不进导出） |
| 5 | [prompt.py](../backend/app/generation/prompt.py) `get_markdown_block_format()` | 1 种 → 4 种，只给触发判据、不堆劝退话术 |
| 6 | [formats.md](../backend/app/agent/prompts/formats.md) | 2 种 → 4 种 + **新增总量闸门「四种块合计不超过两块」** |
| 7 | [export.py](../backend/app/routers/export.py) `_CARD_TOKENS_CSS` | 三态各补 `--tk-success` / `--tk-warning`（**实施前发现的必做项**：这两个令牌前端有、导出页没有，新 CSS 一引用就会让成对常量守卫红） |
| 8 | [docx_export.py](../backend/app/services/docx_export.py) | 新增 `_MARKER_COLORS` 表，`_render_marker_block` 按 `data-block-type` 取色 |
| 9 | [generate.py](../backend/app/routers/generate.py) | 新增 `_block_stats()`，三个完成点记 `blocks=` / `block_types=`（产物侧此前**零统计**，对话侧早有） |
| 10 | [prompts/`__init__`.py](../backend/app/generation/prompts/__init__.py) | `PROMPT_VERSION` `"8"` → `"9"` |

**零改动（由核心约束保证，逐处确认过）**：`MarkerContent.tsx`、`artifact_blocks.py` 的
`parse_blocks` / `strip_blocks` / `render_blocks_html`、`markdown-markers.ts`、`export.py`
的全部导出分支、`chat.py` 的 SSE 协议、对话导出路径（**用户拍板「维持现状」**：`.md` 剥离、
`.json` 保留，故本轮**零导出工作**）。

### 测试

- **跨语言守卫**：`test_fixture_覆盖了两个新类型` 重写为**集合驱动**的
  `test_每个白名单类型都至少有一条解析用例`（判据从 `segments[].type` 派生），前端
  `markdownMarkers.test.ts` 加对称一条。⚠️ 人肉维护的「新类型名单」是同一件事实的第二份
  副本（模式 #54），加 type 忘了改它会让守卫**绿着失去意义**。
- `test_block_prompt.py` 加**两条集合驱动**的「规范列出白名单里每一个类型」（产物 / 对话各一，
  这是「新类型漏写规范 → 模型永远不知道有这个 type」的直接防线）+ 对话侧总量闸门断言；
  `test_两侧规范不串味` 改由 `ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` 派生。
- `test_export_blocks.py`：HTML 三种新 type 都包 `md-block`；Word 侧**从产物读回** (底纹, 竖线)
  断**四种两两不同**（bool 式的「import 私有表比对」证明不了「渲染时真查了表」——漏查表的
  症状正是全走兜底色，而表本身完全正确）。
- `markerPrefs.test.ts` 新增「**每个有本体形态的 type 都有对应的 `decor=off` 归零规则**」
  （判据从 CSS 自身派生）——漏写某一种的症状是「开关对那个 type 无效」，其余 type 都乖乖
  变干净，零报错。
- **三组对照实验全部确认有区分力**：① 给白名单加一个 fixture 里没有的 type → 覆盖守卫红；
  ② 把 `alert` 的 Word 配色改成与 `key-takeaway` 同值 → 三色守卫红；③ 把 `recommend` 从
  `decor=off` 组合选择器里摘掉 → CSS 守卫**精准单红**。

### 已知的未知（不靠测试解决，靠实机）

5 种新 type 一次性铺开，**prompt 遵循率与滥用率都没有先例数据**。看两头的方向已经写进
`docs/prompts-inventory.md`：**该用的时候用了吗**（判据够不够清楚）、**不该用的时候用了吗**
（闸门够不够紧）。调优方向一律是**改判据**，不是加劝退话术（上一轮刚删掉那批）。
产物侧这轮补的 `blocks=` 统计就是为这个观察服务的——**不靠版本号判断**。

---

## 2026-10-11 — 卡片取向调整：从「稀有的强调」改为「常规呈现手段」（PROMPT_VERSION `"7"` → `"8"`）

### 起因

用户看过上一轮落地的两侧规范后提出：**「卡片是附加在 MD 上的一种显示语法，不用过于克制」**。

### 改了什么

删掉两侧规范里**反复出现的劝退话术**，保留**触发判据**与**数量参照**：

| 位置 | 删掉 | 保留 |
|---|---|---|
| 产物侧 `get_markdown_block_format()` | 「多数笔记一处都不用 —— 不要为了用而用」「不加标记的文章完全正常，不加不减分」 | 判据「读者跳过就会损失关键信息」+ 数量参照（1–3 处） |
| 对话侧 `agent/prompts/formats.md` | 「一个都不用是正常且常见的，不用不减分」「不要为了『用上新功能』而勉强凑出一块」「拿不准是不是同级同维度就不要用」「想不出值得问的，就不写这一块」 | 判据「同级 + 同维度」「从读者视角出发的下一步问题」+ 数量（2～4 张卡 / 2～4 个问题） |

### 判断依据（不只是顺从）

原来那批话术防的是「模型为用上新功能而硬凑」—— 但**模型并不知道存在「新功能」**，它只看到一段
规范。真正决定它用不用的，是**触发判据够不够清楚**。叮嘱压的是**使用率**，判据调的是**使用质量**。

### 有意保留的部分

- **数量参照**（产物 1–3 处 / 对话 2～4）：强调块的视觉强度决定了它一多就不再是强调；
- **卡片组「同级 + 同维度」判据 —— 本轮把它写得更实**：原文只有「同级同维度」五个字，
  现在补了「只要对象之间不是并列关系、或者比的不是同一组东西，就不用卡片组」。
  这是防滥用真正的机制，放开语气**不等于**放开判据；
- **追问「从读者视角出发的下一步问题」判据**；
- **过渡语禁令**（工具调用前后的过程话术不是最终回答，不要在那里加块）。

### 测试

- `test_block_prompt.py` 的两条判据由「断逃生口文案」改为「**断触发判据在场**」
  —— 守卫的对象换成了真正需要守的东西；
- **新增反向断言** `test_取向_两侧规范都不再有反复劝退的措辞`（`不减分` / `不要为了用` /
  `一处都不用` 三个词都不许回来），把这次的产品决策钉死。真要治「用得太多」，
  修法是**收紧判据**而不是重新堆叮嘱。

### 影响范围

`PROMPT_VERSION` `"7"` → `"8"`（改的是发给模型的文本，产物自证头会如实标出新版本）。
两侧规范文本**仍不共用**（防串味断言未动）。历史产物不受影响，不会重写。

---

## 2026-10-11 — 引用 chips 重复 key 修复 + 预览区「章节卡片」开关（0.5.0，未升版）

### 1. 修复：`Encountered two children with the same key, tr_xxx`

**症状**：真实使用中冒出 React 重复 key 警告，指向对话的**引用文档 chips**。

**根因是两处，缺一不成**：

| 位置 | 问题 |
|---|---|
| 后端 `chat.py` | `refs.extend(...)` 累积 `tool_result.refs`，**不去重**。而同一篇文档被多个工具轮次引用是**常态**（`search_docs` 命中 → `lookup_doc` 打开） |
| 前端恢复路径 | `openConversation` 直接 `docRefs: m.doc_refs`，**没有去重** |

⚠️ **为什么症状与根因隔了一次会话切换**：前端**流式路径**（`chatStore.onRef`）顺手做了去重
—— 它是**无意的**，却把 bug 藏了起来：实时对话期间同一 `docId` 进不去。只有落盘数据在
**下次打开会话**时才把重复暴露出来，于是表现为「刚才还好好的」。

**修法**：真相源在后端，就地**保序去重**。逻辑抽成模块级 `_append_refs(refs, seen, incoming)`，
好让测试**直调真实实现** —— 调用点在一个嵌套 async generator（`sse_stream`）里，测试够不到，
只能复刻表达式，而复刻正是「改了一处、另一处还在旧假设上」的经典来源。前端抽出模块级
`dedupeRefs` 供**两条消费路径共用**（模式 #54），恢复侧的去重同时兼任**历史数据自愈点**
（后端修复只影响新会话）。

**影响范围**：纯对话侧，无协议 / 存储格式变更。

⚠️ **两条对照实验是实跑过的**（不是写在注释里就算）：
- 把调用点改回 `refs.extend(...)` → `test_chat_refs.py` 的接线守卫**变红**（已复现）
- 关键点：**纯单测拦不住这种漂移** —— `_append_refs` 本身测得再对，调用点被换掉时那几条
  用例**照样全绿**。故补了源码级接线守卫（读 `chat.py` 断言用的是 `_append_refs(` 且无
  `refs.extend(`）。

### 2. 预览区工具栏补「章节卡片」开关

**诉求**（用户原话）：「卡片式的切换按钮删掉了吗，我认为前端应该留着」。
**核实**：`git log -S"setCard" -- PreviewArea.tsx` **为空** —— 预览区**从未**有过该按钮，
骨架开关此前**只在设置页**。所以这不是「删掉了要加回来」，是「补一个此前不存在的快捷入口」。

**做法**：工具栏（装饰开关左侧）加一枚 `LayoutGrid` 按钮，点击在 `paper` ↔ `card` 间切换。

⚠️ **门控必须与正文里的骨架分派同源**（`activeTabDef?.isStandardMarkdown && !typeMeta.isExcel`）：
骨架只在 notes/polished 的非 Excel 路径渲染，其余 tab（数据预览 / 转录 / 思维导图 / 图片 /
Excel 数据摘要）切了**没有任何效果** —— 那种旋钮摆在设置页可以接受，摆在正文工具栏上就是
「调了没反应的死旋钮」（模式 #53）。

⚠️ 高亮语义沿用右侧装饰开关：**高亮 = 已改变默认**（默认 `paper` 不高亮）。这里只切开关；
8 套视觉方案仍在 设置 → 阅读与产物 里选。

⚠️ **对照实验（已实跑）**：去掉 `&& !typeMeta.isExcel` → `cardToggleWiring.test.ts` 的门控
断言**变红**。另有一条注册表守卫钉住「`isStandardMarkdown: true` **恰好**是 summary +
polished」—— 门控的语义完全依赖这条事实，给第三个 tab 标上它必须有人复核（那时门控表达式
一个字没改，前两条断言都不会红）。

---

## 2026-10-11 — 对话侧卡片组 + 追问 chips（`limo-block` 扩展到对话）（0.5.0，未升版）

### 做了什么

把上一轮（`b7df321`）搭好的「**正文协议元素**」架构**扩展到对话侧**，新增两个形态：

| 形态 | 触发条件 | 渲染 |
|---|---|---|
| `card-group` 卡片组 | 回答要**并列比较 2–4 个同级对象**、比同一组维度 | 块内 `###` 各成一张**并排卡片**（可横着扫） |
| `follow-up` 追问 chips | 回答末尾有 2–4 个自然的下一步问题 | 每项一颗**可点击**的 chip，点了直接发送 |

**为什么**：对话是这个项目里**读得最多**的产物，也是**最难读**的 —— 信息密度大、成段成段，
用户的原话是「看着累」。而上一轮的架构只在产物侧生效，对话正文走的是一条完全独立的渲染
路径（裸 `ReactMarkdown`），模型想组织回答也没有手段。

**四个已拍板的决策**（不再论证）：① 两个都做；② 卡片组内容格式 = **结构化 Markdown**
（块内 `###` 切卡），不是 JSON；③ 追问承载 = **正文标记**（复用 `limo-block`），不是新增
SSE 事件；④ 流式半截标记 = **整行抑制**（不显示写到一半的协议文本）。

**明确不改**：SSE 协议 / 消息存储格式 / `PROMPT_VERSION`（对话侧本就没有）/ 版本号。

### 协议层：正则一行都没改

`BLOCK_OPEN_RE` 的 type 段是 `[a-z][a-z0-9-]*`，已覆盖两个新 type；且 `matchBlockOpen` /
`match_open` **只校验协议版本、不查白名单** —— 解析器对 type 无感知是本协议的关键设计。
**加一个 type 的成本 = 改白名单常量 + 写渲染器**，不是改语法。

⚠️ **「协议知道这个 type」与「某一侧实现了渲染」拆成三个集合**
（`ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES` / `KNOWN_BLOCK_TYPES = 前两者之并`，
前后端各一份）。混为一谈的失效方式是**静默**的：把 type 加进白名单就等于宣称「有人处理它」，
而某个渲染方其实没有分支 → 该 type 被原样当正文吐出来，**零报错**。拆开后由断言机器核对
「并集相等 + 两两不相交 + 各等于 fixture 的子集」（`limo-blocks.json` 的 `meta` 三处）。

### 对话侧渲染：**只共享判据，不共享渲染器**

核实过两条渲染路径的确切差异：插件集**完全相同**，但 `MessageItem.tsx` 是**裸** `ReactMarkdown`
（无 `components` prop），而 `MarkdownPreview` 传了 `createMarkdownComponents(...)`（代码高亮 /
图片 zoom / 标题锚点 id / 时间戳 badge / 链接安全校验）。让对话复用产物侧的 `MarkerContent`
（内部硬编码 `MarkdownPreview`）**等于把整个对话正文换成产物渲染路径** —— 语法高亮凭空出现、
每条历史消息的间距与表格全变，是一次**波及全部历史消息的静默回归**。

故新增 `ai-chat/ChatMarkerContent.tsx`：内部 `ChatText`（就是原来那段 `ReactMarkdown`）+
`ChatCardGroup` / `ChatFollowUp`；**其余 type（含产物侧的 `key-takeaway` 与未知 type）一律走
`ChatText`**（内容原样，零残留）。共用的是 `parseMarkers` 与新增的层级化切分器
`splitByHeading(md, level)`（`splitByH2` 改为委托它，行为逐字不变）—— 与后端
`render_blocks_html(md, render_fragment)`「渲染交给调用方」同一纪律。

⚠️ 对话样式刻意**不共用 `.md-block`**（那是 `blocks.css` 的产物强调块，左边框 + 淡底）——
对话要紧凑，`chat-blocks.css` 用独立的 `.chat-*`、尺寸一律 em（跟随对话三档字号）、色值零硬编码。

### 流式：半截标记行的抑制（本轮最大的新问题）

`parseMarkers` 是为**完整字符串**设计的，从没在 partial 上跑过。chunk 会把开标记切在中间
（`<!-- limo-block:v1 type="card-`），这种半截行**既不匹配 `BLOCK_OPEN_RE`（缺 `-->`）
也不匹配 `MARKER_LINE_RE`** → 落到「普通正文」分支被渲染出来，用户看着原始协议文本飘过。

新增 `isPartialMarkerLine` / `suppressTrailingPartialMarker`（`markdown-scan.ts`，判据的唯一家）：

- ⚠️ **判据必须命名空间感知** —— 朴素的「以 `<!--` 开头且没有 `-->`」会把**正在输入的合法
  HTML 注释**（`<!-- 这是一段说明`）一起吞掉，正文瞬断。测试里有一条**对照实验**专门证明
  两个判据在这里行为不同（否则命名空间感知形同虚设）。
- ⚠️ **只削最后一行** —— chunk 只向后追加，半截行必定在末尾；削中间行会在正文里挖出空洞。
- ⚠️ **无需抑制时原样返回入参引用** —— 这个函数在流式的每一帧都被调用，无谓的字符串重建是纯浪费。
- **只在对话正文且 `streaming === true` 时调用**：产物侧、历史消息、重开会话一律不调用 → 与现状零差异。

### prompt：`formats.md` 作为第四层（无条件注入）

`app/agent/prompts/formats.md`（新）+ `PromptLoader` 三层 → **四层**
（`base + rules + formats + doc_context`）。`chat.py` 的非流式与流式两个调用方**共用 `build()`**，
故自动覆盖。

三条写作纪律（照图表管道那套）：**只教语义不教外观**（不提颜色/边框/圆角一个字）/
**约束尽量少**（堆规则与遵循率是直接 trade-off）/ **显式逃生口**（「一个块都不用是正常且
常见的，不用不减分，也不要求每条回答都用」，以及「不要为了用上新功能而勉强凑出一块」）。
另有一条挡「模型在工具调用前后的过渡语里加块」——那些过程话术**不是最终回答**。

⚠️ **两侧规范刻意不共用一份文本**：产物侧走 `get_markdown_block_format()`（只教 `key-takeaway`），
对话侧走 `formats.md`；类型集合不相交，措辞与触发条件也各不相同。共用会让某一侧被迫接受
另一侧的纪律。测试里有「不串味」断言（产物规范不含对话类型、对话规范不含 `key-takeaway`）。

### 消费点清单（每加一个消费点都要回来看，模式 #54）

| 场景 | 处理 | 落点 |
|------|------|------|
| 对话渲染 | 解析并渲染 | `ai-chat/ChatMarkerContent.tsx`（**新**） |
| 持久化 | **原文一字不动落盘** | `chat.py` 存 `"".join(full_answer)`；重开会话重新解析即复原，**零额外字段** |
| 消息编辑 | 随文本保留（改坏了走既有零残留降级） | — |
| MD 导出 | **剥离**（两处：单条 + 批量） | `export.py` 的 `_strip_blocks` |
| JSON 导出 | **刻意保留**（可回灌格式，剥了再导入卡片结构就没了） | `export.py`（有注释写明这是决策） |
| 复制按钮 | **剥离**（复制的是用户看到的内容） | `MessageItem` → `stripMarkers` |
| LLM 历史 | **刻意保留**（模型看到自己写过的格式，强化一致性；标记 token 极少） | `chat.py` |

⚠️ 两个 strip 名字别混：`stripMarkers` 是**前端**函数（复制用）、`strip_blocks` 是**后端**函数
（导出用）—— 跨语言必然各一份，测试两侧各钉。

### 顺带修：实时流里的引用 chips 根本不出现

后端 `tool_result` **确实在发** `refs: [{docId, title}]`，但 `api/chat.ts` 只取了 `data.summary`、
**丢掉了 `data.refs`**；紧接着又在等一个后端**从不发**的 `ref` 事件（死分支）。后果是
**实时流里 assistant 的引用 chips 根本不出现**，只有重开会话后从落盘 `doc_refs` 复原。
修法：`tool_result` 分支遍历 `data.refs` 调 `onRef`（`chatStore.onRef` 已带去重），删掉死分支。
**不改 SSE 协议**（字段本就在发），`docs/对话协议.md` 随之改对。

### 独立信号：模型到底用没用

`chat.py` 的完成期 `log.info` 补 `blocks=` / `block_types=` —— 用 `parse_blocks` 统计而**不是数
子串**（否则模型在正文里讨论本协议时会误计）。没有它，「模型遵循率/滥用率」只能靠翻聊天记录猜。

### 测试

- 前端 4 新 + 2 扩：`chatBlocks.test.tsx`（组件级行为，含「点第 i 颗 chip → 以该项文本发送」）/
  `chatMarkersWiring.test.ts`（**接线守卫**，含 `export.py` 的两个函数都得剥、prompt 写了得拼进
  `build()`）/ `partialMarker.test.ts`（含朴素判据的**对照实验**）/ `chatBlockContent.test.ts`
  （围栏内的 `- x` 不算追问项）/ `markdownSections.test.ts`（`splitByHeading` 层级参数）/
  `markdownMarkers.test.ts`（三集合关系）
- 后端 1 新 + 2 扩：`test_conversation_export.py`（`.md` 剥、`.json` 留，单条 + 批量，含**夹具自证**
  ——先断言 store 里的原文真含标记，否则那组 `not in` 断言会退化成恒真）/ `test_artifact_blocks.py`
  （三集合关系 + 对话类型在产物侧零丢失）/ `test_block_prompt.py`（对话侧 prompt 门控）
- ⚠️ **接线守卫里剥注释是必须的**：那两份文件的注释**故意**写着 `md-block` / `limo-block` 提醒
  后来者别这么干，不剥的话断言测的是注释的措辞（注释一改就变绿）。

### 明确不做

- **不接入 `marker-prefs` 装饰开关** —— 那个开关的语义是「隐藏**装饰**、内容照留」；对话卡片与
  chips **不是装饰，是内容组织与可点击能力**，隐藏它等于砍功能
- **不引入 `streamBuffers` 流式节流** —— 聊天答案量级（几 KB）下 `parseMarkers` 的线性扫描可忽略；
  收益不抵「引入新的 flush 生命周期」的复杂度。**若实测卡顿**再接，设计可随时叠加
- 不做第三种卡片形态、不改 SSE 协议、不改存储格式、不升版本号

### 影响范围

- 新增：`ai-chat/ChatMarkerContent.tsx`、`lib/chat-block-content.ts`、`styles/chat-blocks.css`、
  `agent/prompts/formats.md` + 对应测试
- 改动：`lib/markdown-scan.ts`（三集合 + 两个抑制函数）、`lib/markdown-sections.ts`（泛化）、
  `ai-chat/MessageItem.tsx`（正文 + 复制）、`api/chat.ts`（refs 缺口）、`main.tsx`、
  `core/artifact_blocks.py`（三集合）、`agent/prompts/__init__.py`（四层）、
  `routers/export.py`（对话 MD 剥标记 + 注释）、`routers/chat.py`（完成期统计）
- **零回归**：无标记的对话走**快路径**（产出与改造前逐字相同的树）；产物侧完全不受影响
  （新 type 不会被产物 prompt 教）；`splitByH2` 行为逐字不变

---

## 2026-10-10 — 产物语义标记 `limo-block` v1（重点块）（0.5.0，未升版）

### 做了什么

**起因是用户对「与内容无关的东西混在正文里」的不满**，原话：「解析失败剥掉的标记应该只是
在前端显示，导出的话不应该有，也可一键隐藏不再显示」。追问后收敛到**两个诉求**：① 产物正文
里那些**协议元素**不该被当成内容（导出要干净、要能一键不显示）② 顺带解决「产物是连续文章、
模型没有表达『这段是重点』的手段」。

⚠️ **两个诉求本轮只做后者**（用户拍板：失败标记的隔离**只留口子、不实现**），但**架构是按
两者共用设计的** —— 这是本节最重要的一条：`limo-block` 不是「重点块功能」，而是**产物正文里
协议元素的第一位住户**。下一批（失败标记接入）不需要重新论证架构，只需往同一张消费点表上加行。

| 决策 | 结论 | 理由 |
|---|---|---|
| 谁生成标记 | **模型直接写进 Markdown** | 只有模型知道「这句是结论」；前端推断做不到 |
| 第一版类型数 | **1 个：`key-takeaway`** | 加类型的成本不在代码，在 prompt 每轮重新教育 |
| 渲染形态 | **轻量强调块**（左边框 + 淡底，非卡片） | 与骨架轴正交 → 天然无「卡中卡」 |
| 隐藏开关 | 预览区工具栏小图标 + 持久化 | — |
| md 导出 | **剥离**（导出文件干净） | 用户拍板 |

**三条核实结论改变了方案**（都是**先核实、后动手**的产物）：

1. ⚠️ **`<!-- comment -->` 在预览里会原样显示成文字** —— 我此前判断「会被丢弃」是**错的**。
   react-markdown 10 内部**默认** `allowDangerousHtml:true`（`lib/index.js:123`）且 `skipHtml`
   默认 false → 裸 HTML 节点在 `post()` 里被替换为**普通文本节点**（同文件 `:360-366`）。
   ⇒ 标记**必须在字符串层预解析**（与 `splitByH2` 同层），且**不能用 `skipHtml`**（它删的是
   所有 HTML，用户正文可能合法含 HTML）。
2. ⚠️ **Word 导出会把注释泄漏成可见段落** —— `BeautifulSoup` 把 `<!-- … -->` 解析成 `Comment`
   （`NavigableString` 子类、**不是 `Tag`**）→ 走「非 Tag 即文本」兜底 → 排成可见段落。
   **这是本轮之前就存在的缺陷**，顺带修（回归守卫
   `test_word_标记行不出现在文档正文里`）。
3. ⚠️ **合成管道会把注释当模型输入并计入 token 配额** —— `_latest_notes_text` 只 `strip_meta`
   （只剥自证头那一行）。**同样是既有缺陷**，顺带修。

**协议**（`<!-- limo-block:v1 type="key-takeaway" -->` … `<!-- /limo-block -->`，各独占一行，
块内是普通 Markdown）与**三条不变量**：

- **专用标记隔离** —— 只认专用围栏/标记，用户内容零触碰（照图表围栏协议的三件套：专用语法 /
  零残留降级 / 协议隔离）
- **零残留降级** —— 未闭合、未知 `type`、嵌套一律**只剥标记行**，块内文字一字不丢；剥完与
  「从未写过标记」**逐字相同**（这是「无标记 = 正常文章」的机器可验证形式）
- **围栏内零触碰** —— 代码块里的标记文本是用户内容

⚠️ **一处刻意不照搬图表**：图表有「勾选了但没出图」的**完成期 warning**，标记**不加** ——
无标记是**正常文章**，不是失败。这正是用户要的「模型没生成标记时文章仍然正常可读」。

**四层落点**：

- **前端解析**：`lib/markdown-scan.ts` 扩展（**唯一的行分类器**，`inFence` + 新增 `inBlock`）
  → `lib/markdown-markers.ts`（`parseMarkers` / `stripMarkers`）→ `preview/MarkerContent.tsx`
  （每段各跑一个现有 `MarkdownPreview`，**绝不新起第二个 Markdown 渲染器**）。接入点**只有两处**
  （`PreviewArea` 的 paper 分支 + `CardSections` 每张卡的 body），**绝不放进 `MarkdownPreview`
  内部**（会静默扩散到 Excel 数据摘要与原始转录两处非 LLM 产物）。
- **前端开关**：`lib/marker-prefs.ts`（`mbw_markers`，默认 `decor: true`）。⚠️ **语义是「隐藏
  装饰」不是「隐藏标记」**——块内文字一个不少，只是左边框与淡底消失。⚠️ **落点是纯 CSS 层**
  （`[data-marker-decor="off"] .md-block { border:none; background:none; padding:0 }`，`margin`
  刻意与默认态同值），**不是「JS 分支不渲染包裹层」**——后者会让后端导出侧再实现一遍同样的
  分支（同一份判据两处实现）。DOM 落点 `data-marker-decor="off"`（**默认不写该属性**，与
  `data-skeleton` 同款）；带前缀的规则**永远不匹配** ⇒ 零回归靠「选择器不匹配」而非「取值相同」。
- **后端协议**：新增 `app/core/artifact_blocks.py`（与 `transcript_meta.py` 并列 —— 都是「产物
  正文里的协议元素」的家；一个管自证头、一个管正文标记）。⚠️ **`strip_meta` 是前车之鉴**：它
  只剥第一行，本模块**必须真的按行扫**。
- **prompt**：`get_markdown_block_format()` **只入 notes**（`_POLISH_FORMAT_FNS` 不加 —— polished
  是「忠实整理」，不该塞进模型的主观「这是重点」判断）。⚠️ **不新增勾选项、无条件注入**：标记的
  价值全在「模型自主判断重点」，要求用户先勾选就退回了「用户自己知道哪里有重点」的旧问题。
  规范**不教外观**（颜色/边框/圆角一个字不提 —— 模型管性质、CSS 管长相，与图表协议「只教语义、
  坐标全由后端算」同一纪律），并写了**数量上限**（1–3 处）与**逃生口**（不加标记不减分）。
  `PROMPT_VERSION` `"6"` → `"7"`。

**消费点清单**（这张表就是「留口子」的具体形态：每加一个消费点都要回来看有没有漏，模式 #54）：

| 消费点 | 动作 |
|---|---|
| HTML / PDF 导出 | **转换**为 `<div class="md-block" data-block-type="…">` |
| Word 导出 | **转换**为单单元格表格（左边框 + 淡底）+ 修泄漏缺陷 |
| 思维导图 | 显式 `strip_blocks`（替换掉对通用 `<[^>]+>` 规则的隐式依赖） |
| md / 纯文本导出 | 前端 `stripMarkers`（客户端直读 store，后端碰不到） |
| 合成管道 | `strip_blocks`（修既有缺陷） |
| 字数统计 | `strip_blocks`（`chars` 是「正文字数」） |
| H2 切分 | 跳过块内 `##`（与前端同源） |

**刻意不动**（数据层保留原文是有意的架构选择）：`query.py` 读路径（返回原文让前端解析）、
`markdown_edit.py`（原样回写，数据层真相不动）、FTS 索引 / Agent 工具（已安全 —— FTS 只索引
`transcript.rawText`，Agent 工具只读 rawText / 元数据）。

### 为什么

- **「模型管性质、CSS 管长相」是这一轮的主线** —— 规范里出现外观词就等于两处都在描述同一件事，
  改一处就漂移（而产物上没人能说清哪个是错的）。`test_block_prompt.py` 用词表断言钉住。
- **零回归不是靠「小心」，是靠结构** —— 默认路径（`paper` + 无标记）满足三个「不可能」：
  `data-marker-decor` 属性不写、`blocks.css` 不注入、`render_blocks_html` 无标记时**逐字返回**
  `render_fragment(md)`。于是「无标记产物的导出 HTML 与改造前逐字节相同」是一条可写出来的断言。
- **Word 侧形态刻意不复用卡片** —— 复用 `FILL_CARD` + `Table Grid` 会让 `card` 骨架下的「卡内
  标记块」看起来像**一张卡里套着一张卡**。故另立 `FILL_MARKER` / `BAR_MARKER`。

### 顺带修掉的 4 个缺陷（都是先建守卫、缺陷随之暴露）

1. ⚠️ **`_CARD_TOKENS_CSS` 注入闸门漏了一整类**（**本轮最有价值的一条**）：该常量是导出页
   **唯一的** `--tk-*` 提供者（`HTML_THEMES` 一个 `--tk-*` 都不提供），它原先只在
   `skeleton == "card"` 时注入。于是 **`paper` + 有标记**时 `blocks.css` 的
   `border-left: 3px solid var(--tk-accent)` 与 `background: color-mix(…)` 整条声明作废 ——
   **导出 HTML 里强调块一点样式都没有，而应用内预览完全正常、零报错**。修法：
   `if skeleton == "card" or blocks_present:`（无标记 + paper 仍不注入，零回归保持）。
   守卫先建（`test_blocks_css_引用的每个界面令牌在导出页里都有定义`），缺陷随之暴露。
2. **Word 导出注释泄漏**（见上「核实结论 2」）。
3. **合成管道把标记当输入**（见上「核实结论 3」）。
4. **`chars` 把标记字符算进正文字数** —— 派生字段由 `get_markdowns_by_transcript` 读文件头现算，
   改为 `len(strip_blocks(strip_meta(text)))`。

### 影响范围

- **新增 12 个文件**（5 源 + 7 测试）：源 = `backend/app/core/artifact_blocks.py`、
  `frontend/src/lib/{markdown-markers,marker-prefs}.ts`、`frontend/src/components/preview/MarkerContent.tsx`、
  `frontend/src/styles/blocks.css`；测试 = `backend/tests/test_{artifact_blocks,export_blocks,block_prompt}.py`
  + `frontend/src/test/{markdownMarkers,markerPrefs,markerContent,exportMarkers}.test.{ts,tsx}`
  **+ 共享 fixture** `frontend/src/test/fixtures/limo-blocks.json`（前端 vitest 与后端 pytest
  **各读同一文件、各跑各的断言**，与 `h2-sections.json` 同款 —— 跨语言同源的机制所在）。
- **改动**：`export.py`（渲染下沉 / 令牌闸门 / md+plain 剥离 / `marker_decor` 参数）、
  `docx_export.py`（标记块 → 单单元格表格）、`synthesis.py`、`store.py`、`prompt.py`、
  `prompts/__init__.py`、前端 `ExportMenu.tsx` / `ExportPanel.tsx` 等。
- **prompt 改动影响所有后续生成**：`PROMPT_VERSION` `"6"` → `"7"`。**存量产物不受影响**。
- **打包**：`LiMoBackend.spec` 的 datas 加 `('../frontend/src/styles/blocks.css', 'styles')`
  （与 `cards.css` 两行分开 —— 一份管骨架、一份管标记）。⚠️ **打包后需再验 `_load_block_css()`
  能读到**（单测已覆盖该分支，真机待验）。
- ⚠️ **实机验收待用户操作**：① 真实生成一次笔记看遵循率与「无标记照样可读」② `paper` / `card`
  两种骨架下强调块都成立 ③ 开关一键隐藏后文字一字不少、刷新记住 ④ Word 导出读回看（有边框
  底纹的块、无可见的 `limo-block:v1 type=` 文字）。**测试全绿不等于实机通过**（本轮后端 28 条
  新测试全绿，但它们证明的是「代码按设计工作」，不是「模型真会照写」）。

---

## 2026-10-10 — 产物章节结构：一节一张卡（0.5.0，未升版）

> 设计文档见 [design/cards.md](../design/cards.md)（两根新轴 + 8 套视觉方案 + 19 种样板）。

### 做了什么

**起因是呈现层诉求，不是功能诉求**：用户原话「以 DeepSeek 这类偏重逻辑的模型为例，
回复常常信息密度过大、不易阅读；做成卡片，既易于阅读，也方便追问」。于是新增两根与
既有六轴**正交**的轴 —— **骨架**（`paper` 一整张纸 / `card` 每节一张卡）× **卡片视觉
方案**（8 套：`gpt` / `apple` / `compact` / `outline` / `paper` / `guofeng` / `seal` / `ink`）。

**探索先挖出两件事，它们决定了「先动 prompt」是必要的**：

1. **默认路径恰好是最反对章节结构的组合**：`plain` 是 2026-10-05 起的全局默认档，第 4 条
   逐字为「**不堆小标题和列表**：能用一段话讲清就不拆成三条」（[notes_styles.py](../backend/app/generation/prompts/notes_styles.py)）；
   而**视频/音频的 `_NOTES_TASK` 是五个 NOTES 分支里唯一没有标题要求的**（[prompt.py](../backend/app/generation/prompt.py)，
   由 `else` 兜底命中）。两者叠加 ⇒ 默认产出的视频笔记大概率一个 `##` 都没有，切卡只会得到**一张巨卡**。
2. **视频支的「章节」要求今天只由「格式勾选」条件性提供**：五个 NOTES 分支里四个都写了
   「层次结构：使用标题层级（## → ###）」，唯独视频/音频那支什么都没有；而「章节」这个概念
   在同一支里至少被五处引用（`_NOTES_TASK` 第 6 条的分割线 + 三条格式指令：`toc` / `link` /
   `screenshot`），但那三条**只在用户勾选对应格式、才注入 `<hard_constraints>`** ⇒ 不勾任何
   格式时一条都不生效。修法：把**条件性要求变成无条件基线**。

**⚠️ 实施期更正：上面两件事的**后果**部分被基线实测证伪。** 第 1 部分建成后立刻跑了基线
（198 份产物 / 90 篇文档）：勾选过 `toc`/`link`/`screenshot` 的产物 **0 份**；有自证头的
23 份 **H2 中位 7、无 H2 占比 0%**；**视频来源 18 份全部「未勾选」，却全部写出了 `##`**
（H2 中位 7）；`plain` 档 5 份同样全部分节。**模型没等 prompt 要求就自己分了节。** 于是
第 2 部分的**性质改变**（用户拍板：两处仍都做）——从「修一个已存在的缺陷」降为「把已经
发生的事实写成显式要求」，是**加固**（行为更确定、与另外四条路径一致、换模型时不易丢失
结构），不是修复。⚠️ 「相邻 H2 之间 max 80,223 字符」证实「一张巨卡」**真实存在**（至少 1 例），
这部分支撑 card 功能的价值不受影响。

**第 0 部分 · 顺带修复 `--tk-surface-hover`（独立于卡片轴）**
- **事实**：该变量在原 `index.css` **9 处引用、0 处定义**。按 CSS 规范 `var()` 引用未定义变量
  且无 fallback ⇒ 声明在计算值阶段失效 ⇒ **代码块底色 / 引用块底色 / 表格表头底色 / 斑马纹
  四处实际落到 `transparent`**。正确名字是 `--tk-hover`。
- ⚠️ **这是视觉行为变更，不是零回归** —— 四处底色一起从透明变成有底。故验收时与「骨架轴
  零回归」**分开断言**。修法收口在 [index.css](../frontend/src/index.css)，并在
  [cards.css](../frontend/src/styles/cards.css) 文件头留一条注释（记下这个名字是坏的，
  防后来者照抄）。`cardPrefs.test.ts` 有一条守卫：**剥掉注释后** `cards.css` 不得出现它。

**第 1 部分 · 只读统计脚本**（[scripts/check-artifact-structure.py](../scripts/check-artifact-structure.py)）
- **定位是可重复的验收工具，不是门禁**（**不进 `scripts/check-all`**）：prompt 改动前后各跑
  一次，看「按文风档分组的 H2 计数分布有没有上移」。形态照 `check-secrets.py`：`--root` /
  `--json` / `--selftest` + **三态退出码**。
- 回答四问：① 按 `generated.params.style` 分组的 H1/H2/H3 计数 ② 按 `source.sourceType`
  分组 ③ 相邻 H2 之间正文字符数分布 ④ 表格/代码块/引用/图片/列表出现率（为「19 种里哪些
  值得做」提供第一份真实数据，此前是零数据）。
- ⚠️ **输出绝不回显任何标题文本、正文或文件名** —— 只给计数与分布；这份报告可能被贴进
  issue、传给外部讨论，产物内容属用户隐私（与 `check-secrets.py`「不回显凭据值」同一条纪律）。
- ⚠️ **H2 判定走 `markdown_sections.iter_headings`，与导出切分器同源** —— 各写一份的症状是
  静默的：统计说「3 个 `##`」而导出只切出 2 张卡，两边都不报错。
- `--selftest` 自证扫描器有区分力：造两段合成 Markdown（一段 3 个 `##`、一段 0 个，各带一句
  **围栏内的假标题**），断言计数正确且不被围栏内的 `## ` 骗到（后者是 `extractHeadings`
  已经踩过的真坑）。

**第 2 部分 · prompt 收敛**（两处改动 + `PROMPT_VERSION` `"5"` → `"6"`）
- **改动一**：`plain` 档第 4 条 `- 不堆小标题和列表：…` → `- 不硬凑结构：…`（**只换前半句**，
  冒号后一字不动）。原句的「不堆**小标题**」在视频路径上会被读成「连标题也不要有」，
  「**和列表**」是第二类误伤。「不硬凑结构」保留「别为凑而凑」的原意，**又不再出现「标题」
  这个词** —— 歧义从字面消失，而不是靠加解释去压。⚠️ **刻意不加**「内容有几个话题时才用
  `##`」这类许可句：`##` 的组织要求由 `<task>` 无条件承担，风格档不该重复定义它。
- **改动二**：`_NOTES_TASK` 补 `"2. **层次结构**：使用标题层级（## → ###）组织内容，反映
  内容的逻辑结构。"`（插在第 1 条之后，其后编号统一 +1）。与另外四处**逐字同源**，唯一
  有意差异在句尾：它们说「反映**原文**的逻辑结构」，这里说「反映**内容**的」——视频是口播
  转录，没有成文的「原文结构」可反映。⚠️ **刻意不加**「单一主题的短内容不必强行分节」：
  三条格式指令无条件要求 `##` 章节，而 `<hard_constraints>` 优先级**高于** `<style_preference>`
  ⇒ 加这句等于**用一条低优先级的软偏好去授权违反高优先级的硬约束**。
- ⚠️ **写这两条文案前过了一道闸**：任何写进风格档的「可以不要标题」都会给模型一张违反
  hard_constraints 的通行证 ⇒ 两处改动都刻意不提「要不要标题」以外的条件。改完逐层核过
  同一份 prompt 里**没有残留方向相反的两条指令**（`<hard_constraints>` 三条要求 `##` 的
  指令与新条款同向；`<style_preference>` 除 `plain` 第 4 条外没有第二个档表达「不要章节」，
  两处 `minimal` 反而**以「章节存在」为前提**）。
- ⚠️ **「不要卡片」这个诉求不该由 prompt 档位承担**，由**骨架轴的 `paper`** 承担（切回 paper
  时 `##` 仍是普通标题、渲染成一整篇）。这让两根轴分工自洽：**prompt 管「产物有没有结构」，
  骨架管「结构长什么样」**。
- 连带：`notes_styles.py` 注释块里逐字引用旧句的那句、以及它「单篇笔记没有骨架约束」的**已
  被推翻的前提**一并改；`common.py` 的两处过期 docstring（漏列 `<output_language>` /
  `<reference_texts>`；`<role>` 误写「含语言要求」）改成与 `prompts-inventory.md` 附录 C 同序
  同措辞；`prompts-inventory.md` §13 版本表加 `"6"` 行 + §12.1 加一行。
- ⚠️ **改动点是 `prompt.py` / `notes_styles.py` 的常量**（流式与非流式两条构建路径都读同一份
  常量），**不是** PromptBuilder 层 —— 后者会漏掉流式。

**第 3 部分 · 前端骨架**
- **新文件 [lib/card-prefs.ts](../frontend/src/lib/card-prefs.ts)**（localStorage `mbw_card`），
  形态照 `reading-prefs.ts`：逐字段校验、单项坏只退该项。启动链
  [store/index.ts](../frontend/src/store/index.ts) 补一行 `applyCard(getSavedCard())`。
- ⚠️ **`paper` 时必须 `removeAttribute` 而不是写 `="paper"`** —— 用「**属性缺失即 paper**」
  让卡片规则**永远命不中**。这是「零回归」从「取值相同」升级为「**选择器不匹配**」的更强
  保证（写 `="paper"` 时，任何一条漏写 `[data-skeleton="card"]` 前缀的卡片规则都会漏出来）。
- **切分器抽成唯一真相源**：[lib/markdown-scan.ts](../frontend/src/lib/markdown-scan.ts) 的
  `scanHeadingLines` 把**围栏扫描**收口，`markdown-components.tsx` 的 `extractHeadings` 改为
  消费它（**不是复制一份** —— 代码块里的 `## ` 被当成标题是已经踩过一次的真坑）。
  [lib/markdown-sections.ts](../frontend/src/lib/markdown-sections.ts) 的 `splitByH2` 返回
  `{ preamble, sections }`（无 H2 时回落长文；H2 前引言单独成块不丢弃；卡内 H3/H4 留在卡内）。
- **[components/preview/CardSections.tsx](../frontend/src/components/preview/CardSections.tsx)**：
  切分后**每张卡各跑一个现有的 `MarkdownPreview`** ⇒ 表格/代码/引用/公式/图片/时间戳 badge/
  标题锚点与今天逐一致（**绝不新起第二个 Markdown 渲染器**）。**接入点只有一处** ——
  [PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx) 的 notes / polished
  只读态。⚠️ **刻意不在 `MarkdownPreview` 内部按骨架轴分派** —— 那样会**静默扩散**到另外两处
  调用点（Excel 数据摘要 / 原始转录）。⚠️ `paper` 分支与改造前**逐字相同**。
- **卡片 CSS 独立成 [styles/cards.css](../frontend/src/styles/cards.css)** —— 19 个 `--card-*`
  + 4 个 `--sh-*`（**全项目唯一允许出现裸色值的地方**，影子本质是黑且必须随明暗换值），
  其余零硬编码色值（单测断言：除 `--sh-*` 等级层外不出现任何 `#`/`rgb(`/`hsl(`）。⚠️ **切换
  预设必须先清空全部受控变量再套新值**，且受控变量清单**从 `PRESETS` 派生、不手写第二份** ——
  只写差异而不清，上一套独有的键会残留（国风的 `--card-frame` 漏到 `gpt` 上，界面像「预设
  切换有 bug」，实际是静默串味）。⚠️ **国风双框内框必须与外框同色**（曾用 `--tk-border-light`，
  像素实测与卡片底只差 9 个灰阶 —— 线画出来了、断言全过、**肉眼等于没有**）。
- **设置页**：骨架（两选一）+ 卡片视觉方案（8 选一）；⚠️ 骨架 = `paper` 时**隐藏**方案块
  （而非灰置），否则是「调了没反应」的死旋钮。

**第 4 部分 · 导出跟随**（用户拍板：明知「禁止导出与预览不一致」是核心约束，选择**不让
卡片成为例外**）。详见 [export-pipeline.md](export-pipeline.md)「卡片骨架跟随」段。
- 参数贯通：`GET /export/{id}` 加 `skeleton`/`card_style` 查询参数（**默认 `paper`**）、
  `ExportCustomRequest` 加同名字段、前端唯一拼装点 `ExportMenu.tsx::backendParams()`
  （HTML / Word / PDF 三条路径共用，`lib/print.ts` 无需改）。⚠️ **`paper` 时一个参数都不发** ——
  URL 与改造前逐字一致。⚠️ PDF **自动跟随，零后端改动**（复用 HTML 那份自包含 HTML）。
- **HTML 侧**：`split_by_h2` 切分 → 每节包 `<section class="md-card">` 装进 `<div class="md-cards">`。
  ⚠️ **`<html>` 必须同时带 `data-skeleton="card"` 和 `data-card-style`** —— 前者是卡片规则的
  **主作用域**，只带后者的话规则**永不命中** ⇒ 导出的是**没有样式的卡片**，而「section 数与
  H2 数一致」「CSS 里有卡片规则」这类断言**全绿**（本项目点名的「断言全过、肉眼等于没有」）。
- ⚠️ **本轮挖出的关键缺陷（不是报错，但等价严重）**：导出 HTML 的主题（`HTML_THEMES`）是
  **自包含裸色值 CSS，一个 `--tk-*` 都不提供** —— 而卡片 CSS 有 27 处 `var(--tk-*)` 引用
  （8 个令牌），`var()` 无 fallback 时整条声明作废 ⇒ 卡片**没有底色、没有边框、没有圆角**，
  且**应用内预览里一切正常**（那边 `--tk-*` 由主题系统提供）。修法：`export.py` 补一份
  `_CARD_TOKENS_CSS`（**三态**：浅色 / `prefers-color-scheme: dark` / `@media print`，
  后者还原浅色）—— ⚠️ **三态都要给全**：`@media print` 与暗色媒体查询**会同时匹配**。
- **`_load_card_css()`** —— 卡片 CSS 的**单一真相源在前端**，后端**从磁盘读**并内联，
  **零重复定义**。候选：开发环境 `仓库根/frontend/src/styles/cards.css`；打包环境
  `sys._MEIPASS/styles/cards.css`（[LiMoBackend.spec](../backend/LiMoBackend.spec) 的 datas
  收集源文件本身）。⚠️ **刻意不往 `backend/static/` 放副本** —— 副本就是第二份拷贝。⚠️
  **读不到显式抛 `AppError("EXPORT_CARD_CSS_MISSING", recoverable=False)`**，不静默输出无样式
  卡片。⚠️ **不缓存**（对照 `_get_katex_assets()` 的缓存：KaTeX 是只读第三方资产，而
  `cards.css` 是**我们会改的源文件**，缓存会让改动静默不生效）。
- **Word 侧**：解析**同一份 HTML**，识别 `<section class="md-card">` → **单单元格表格**包裹
  （`Table Grid` 边框 + 底纹 `FILL_CARD`）。⚠️ **docx 无圆角无投影**，Word 侧的「卡片」=
  「有边框和底纹的块」，是**能力边界不是实现省略**；⚠️ **刻意不接收 `card_style`**（8 套皮肤
  会退化成第二份硬编码色表）。⚠️ **`_render_block` 的 elif 链必须加在中间**（`blockquote` 之后）
  —— 链尾的 `else` 是「未知块 → 递归子元素」，排在它后面的分支**永不可达**；也**正因那个
  `else` 兜底，漏加分支不会丢内容**（section 被当未知块递归、正文照旧渲染）⇒「读回断言内容
  还在」这种断言**在漏加时照样绿**，断言必须落在**结构层**。
- ⚠️ **不跟随的**：纯 Markdown / 纯文本导出（md 就是 md，没有视觉层）；`/export/{id}/all`
  （只打包原始 md 产物，不渲染 HTML）。

**跨语言同源**：前后端**各有一份** H2 切分实现，靠**共享 fixture**
[frontend/src/test/fixtures/h2-sections.json](../frontend/src/test/fixtures/h2-sections.json)
钉住一致 —— 前端 vitest 与后端 pytest **各读同一文件、各跑各的断言**。⚠️ **切分点必须在
`_protect_latex` 之前**（前端 `splitByH2` 看到的就是原始 md，两侧输入同源才谈得上切出同一
批卡片，否则含跨行公式的文档会在预览与导出之间分叉）。⚠️ **章末的 `---` 在切分器里剥**
（`_NOTES_TASK` 第 6 条要求章间写分割线，切卡后由卡片间距承担；前后端都要剥，放切分器
避免同源漂移），章节**内部**的分隔线保留。

### 为什么

- **产物是给人读的，而「一节一张卡」是呈现层的事** —— 拆成卡片既降单屏信息密度，又让每节
  成为可独立指认的对象（为将来的追问留好位）。
- **零回归靠「选择器不匹配」而非「取值相同」** —— 默认 `paper` 时两个属性都被移除，`cards.css`
  里所有规则带 `[data-skeleton="card"]` 前缀 ⇒ 一整套新样式**在结构上不可能**影响既有渲染。
- **导出必须跟随**（用户决定）—— 项目核心约束是「用户在预览栏看到什么，导出就应该得到什么」，
  用户选择不让卡片成为例外。代价是本轮必须克服两处静默失效（`data-skeleton` 属性、`--tk-*`
  令牌），都在上面如实记了。
- **prompt 先动而不是只做前端** —— 若产物没有 `##`，切卡只会得到一张巨卡。⚠️ 但基线实测
  证伪了这个推论，故 prompt 改动的定位从「修复」下调为「加固」（见上「实施期更正」）。

### 影响范围

- **新增 7 个源文件**：`scripts/check-artifact-structure.py`、`backend/app/services/markdown_sections.py`、
  `frontend/src/lib/{markdown-scan,markdown-sections,card-prefs}.ts`、
  `frontend/src/components/preview/CardSections.tsx`、`frontend/src/styles/cards.css`
  **+ 5 个测试文件**（`backend/tests/test_{markdown_sections,export_cards}.py`、
  `frontend/src/test/{markdownSections.test.ts,cardSections.test.tsx,cardPrefs.test.ts}`）
  **+ 共享 fixture** `frontend/src/test/fixtures/h2-sections.json`。
- **prompt 改动影响所有后续生成**：`PROMPT_VERSION` `"5"` → `"6"`，之后生成的产物自证头会带
  `"6"`。**存量产物不受影响**（它们已落盘）。⚠️ 真实验收（用视频素材生成一次、确认产物真有
  `##` 且 `promptVersion === "6"`）**待用户实机操作**。
- **导出行为**：`paper`（默认）路径**逐字节未改**（有单测 `test_显式传_paper_与不传参数逐字节
  等价` 钉住）；只有用户主动切到 card 才走新分支。
- **打包**：[LiMoBackend.spec](../backend/LiMoBackend.spec) 的 datas 加了
  `('../frontend/src/styles/cards.css', 'styles')`。⚠️ **打包后必须再验一次** `_load_card_css()`
  能读到文件（单测已覆盖该分支，真机待验）。
- **门禁**：`bash scripts/check-all` 五项全绿（tsc / oxlint / vitest / pytest / check-cli）。

---

## 2026-10-10 — 第三轮代码自查：P0 迁移抹索引 / P1 前端版本滞后（0.5.0，未升版）

> 完整报告见 [code-audit-3.md](code-audit-3.md)（10 条 + 「记录不修」5 条 + 对照实验 6 条）。

### 做了什么

**起因**：上面那批「产物版本管理重做」刚落地，而它把**写入语义从「新建」改成了「就地覆盖」**
—— 同一个小笔误，旧模型下是「多一版」，新模型下是**覆盖掉用户没在看的版本、原文不可恢复**。
语义升级带来了新的风险面，故对这批改动做一轮专项自查（三个并行 Agent 分域 + 逐条复现）。

**① P0 — 启动迁移抹掉全库文档级字段**（[store.py](../backend/app/storage/store.py)）
- 迁移函数末尾调了 `self._rebuild_index(save=True)`「刷新一下索引」。而 `_rebuild_index` 从
  **磁盘**重扫，磁盘上没有 `tags` / `folderId` / `sourceUrl` / `createdAt` / `id`，也没有 `folders`
  整张表 —— 它们**只存在于 `index.json`**。`save=True` 让它**扫完立刻落盘**。
- **每个升级用户首次启动必现、不可逆、且日志写着「迁移成功」**。写法是迁移只重算**受影响的
  `(文档, mode)`** 产物条目，新增 `_markdown_entries_desynced`（判据是**版本集合比较**，
  天然幂等，已迁移的库上恒 False = 正常启动零副作用）。写盘失败**不阻断启动**
  （`StoreError` 是 `AppError` 子类而非 `OSError`，只 catch 后者会让磁盘满时后端起不来）。
- ⚠️ **对照实验**：把 `_rebuild_index(save=True)` 注入回去 → 新增 3 条守卫全红，
  **而原有 6 条迁移测试全绿** —— 后者才是这条缺陷能活下来的原因。

**② P1 — 跨 mode 切 tab 后编辑覆盖「用户没在看的那一版」**（[PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)）
- `markdownMeta` 是**单值**，而 notes / polished 两个产物位的版本号**各自从 v1 起**。
  此前加载文档只写 notes 的 meta、切到 polished **什么都不做**，而 `startEditing` 直接取
  `markdownMeta.version` → **拿笔记的版号去覆盖整理版的第 N 版**。旧模型（编辑 = 新建版本）下
  这只是「多一版」；就地覆盖语义下它是**改错文件**。
- 修法：`perModeMetaRef` 按 mode 分桶（四处写入点）+ 一个切 tab 的同步 effect + **自举分支**
  （生成完成时 GenPanel / 恢复态 `App.tsx` 直接写 store、不经过本组件，不认领就会读到更早的陈旧值）
  + 切文档清空桶。顺带修掉「切到整理版，版本下拉显示的是笔记的版本列表」这个继承性缺口
  （同根，只是危害小到没人报过）。
- ⚠️ **差点修错，值得留档**：effect 守卫初版写成 `activeView !== 'notes'`，而**笔记 tab 的 key
  是 `'summary'`** —— 是 tsc 报 `TS2367`（两类型无重叠）才暴露。更关键的是：我为它写的源码断言
  **恰恰断定了那个错误字符串**。教训是**结构守卫断得住「有没有」，断不住「对不对」**，
  故补了一条行为断言（`TabRegistry.get('summary')?.htmlMode === 'notes'`）钉住前提。

**③ P1 — 版本条目 `chars` 缺失被填 `0`**（[services/query.py](../backend/app/services/query.py)）
- `artifact_versions` 对缺失的 `chars` 填 `0`，而同一个函数的 docstring 说它是**读时从文件头现算
  的派生值**（派生值**可能算不出来**）。前端 `undefined`（没给）与真 `0` 字是严格区分的两种语义
  （`VersionSwitcher` 的测试正是在断这个），填 `0` 是在**后端主动伪造数据**，且伪造值落在
  「用户会当真」的那一侧。改为**缺失时不给这个键**。

**④ P1 — 版本切换的归属守卫漏了 `version` 这一维**
- 守卫只比 `docId`。`versionLoading` 只是**异步生效**的按钮禁用，拦不住已经发出去的请求 ——
  「连点 v1 再点 v2」，先发的后返回就会用 v1 覆盖 v2，且旧请求的 `finally` 还会放开 loading。
  修法：守卫带全 `docId + mode + version`，解除 loading 的权利归**最新**那次请求（模式 #38）。

**⑤ P1 — 新导出测试是脚本式（假绿）+ handler 与生产分叉**
- `test_export_by_version.py` 原是一串模块级裸调用 + `main()`：`python tests/x.py` 能跑，
  `pytest tests/` **收集 0 条而一片绿**。且它自带一个忽略 `_explicit_status` 的异常处理器 ——
  测试断言的状态码可能与**线上真正的**状态码不同（模式 #54：复刻一份表达式，分叉了不抛错，
  只是静默判错）。改为 pytest 风格 7 个 `def test_*` + `tmp_path_factory`，并把状态码判据抽成
  [errors.http_status()](../backend/app/core/errors.py)，**`main.py` 的异常处理器与测试共用同一份**。
  ⚠️ 判据必须是「有没有**显式指定** `status_code`」而不是「等不等于默认 400」——
  `status_code=400 + recoverable=True`（`export_custom` 就是这个组合）用后者会被误压成 200
  → 前端 `if (!r.ok)` 当成功 → 下载到损坏的 zip。

**⑥ P2 — 四处同源漂移 / 无机制**
- `version` 判据三个入口三种写法（`> 0` / 非 0）→ `version=-1` 在 `generate` 拿到最新版、
  在 `export`/`markdown_edit` 报 404。统一为「**非 0**」：越界值该显式报错，不该退化成「最新」。
- `save_markdown` **就地改写调用方传入的 `file_meta`** → 同一个 dict 连写两版时，两版自证头会是
  同一个版本号（零报错）。改为浅拷贝。
- `MarkdownView → MarkdownMeta` 的收口函数本轮新写了，但 `PreviewArea.tsx` 内部**两处仍是内联
  字面量** —— 收口做了一半，剩下这一半正好在本轮最关键的链路上（`chars`/`source` 静默丢失）。
- 代理豁免表（见下）缺保留私有域名后缀。

**⑦ 顺带修复：代理豁免表**（新模块 [app/core/net.py](../backend/app/core/net.py)）
- 启动时把 `fetch.proxy` 注入成**全局**环境变量，但**没有豁免表** —— 本地 SearXNG
  （`http://127.0.0.1:18080`）与自建 LLM 网关（`http://localhost:8000`）也一并被塞进代理。
  多数代理软件默认对回环直连，所以平时看不出问题 —— **那是运气不是设计**。
- 两条通道**刻意不同源**（被第三方库的表达能力限制）：`NO_PROXY` 环境变量只列字面量
  （各家后缀匹配语义不一致，押在它上面等于把 bug 埋进依赖库），Python 侧的 `proxy_for()` 承担
  任意判据（回环 + 私网 + link-local + 保留私有域后缀）。
- ⚠️ 后缀**只收有标准依据的**（`.local` RFC 6762 / `.home.arpa` RFC 8375 / `.internal` ICANN 2024）：
  「绕过代理」是**猜错也静默**的判据（少了代理的症状是连不上而不是报错），靠印象扩大豁免面会把
  真正要走代理的网关一并豁免掉。反面对照写进测试（`notlocal.com` / `api.localstack.com` /
  `internal.example.com`）。

**⑧ 顺带修复：YouTube 字幕 API 1.x 适配**（[adapters/youtube_subtitle.py](../backend/app/adapters/youtube_subtitle.py)）
- `pyproject` 写的是 `youtube-transcript-api>=0.6`，实际装到 1.x —— 而 1.x 是**破坏性重写**
  （`list()`/`fetch()` 变实例方法、片段变 dataclass、代理走 `proxy_config`）。代码仍按 0.6.x 写，
  于是 `YouTubeTranscriptApi.list(vid, cookies=...)` **每次必抛 TypeError**，又被宽 `except`
  吞掉 → **字幕路径从未成功过**，静默回退 yt-dlp 下载（用户感受为「油管导入特别慢 / 像是不通」）。
- 修法按 1.x 适配 + 依赖下界提到 `>=1.0` + **失败分型按「用户能采取的行动」划分**（模式 #53）：
  「视频确实没字幕」静默降级走 ASR；「被反爬 / 网络拦下」**必须显式冒泡** —— 伪装成「没字幕」
  会让代理失效表现为「导入变慢」，用户永远查不到原因。异常类用 `getattr` 收集而非直接 import，
  免得依赖漂移时缺一个类就把**不依赖字幕**的视频下载路径一起打挂。
  `tests/test_youtube_subtitle.py::test_api_shape_contract` 钉住三条形状契约，依赖漂移即红。

### 为什么

1. **「就地覆盖」把同一个笔误的后果放大了。** 版本重做之前，「前端以为用户在看哪一版」算错了
   只是多一个版本（无害）；之后它就是**覆盖掉另一份不可恢复的原文**。所以本轮的重点不是
   「再检查一遍」，而是**针对新语义重新评估旧代码的每条假设** —— `markdownMeta` 的单值假设、
   `chars` 填 `0` 的无害假设、归属守卫只比 docId 的充分性假设，全都是在这一批里才变成错的。
2. **P0 那条的教训与代码无关**：「刷新一下索引」是个听上去无害的动作，而 `_rebuild_index`
   的真身是「把索引丢回磁盘从头来」。**它的名字没有说谎，是调用位置错了。**
3. **测试的绿要问它是怎么绿的。** 同一批缺陷里，一个是「6 条迁移测试全绿」（覆盖了文件搬家，
   没覆盖元数据幸存），一个是「pytest 收集 0 条」（根本没跑）。两者都会让门禁全绿。

### 影响范围

| 层 | 文件 |
|---|---|
| 存储 | [store.py](../backend/app/storage/store.py)（迁移只重算受影响 mode + `_markdown_entries_desynced` + `save_markdown` 不再就地改入参） |
| 核心 | [core/errors.py](../backend/app/core/errors.py)（`http_status` 唯一判据）、[core/net.py](../backend/app/core/net.py)（新）、[main.py](../backend/main.py)（handler 改调 `http_status`） |
| 路由 | [generate.py](../backend/app/routers/generate.py)（`version` 判据非 0）、[adapters/youtube_subtitle.py](../backend/app/adapters/youtube_subtitle.py)、[agent/tools.py](../backend/app/agent/tools.py)（`_searxng_search` 改走 `proxy_for`） |
| 服务 | [services/query.py](../backend/app/services/query.py)（`chars` 缺失不给键） |
| 前端 | [preview/PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)（分桶 + 同步 effect + 竞态守卫）、[lib/markdownMeta.ts](../frontend/src/lib/markdownMeta.ts)（收口落实） |
| 依赖 | [pyproject.toml](../backend/pyproject.toml) `youtube-transcript-api>=1.0` |
| 测试 | [test_store_legacy_migration.py](../backend/tests/test_store_legacy_migration.py)（+3 元数据幸存守卫）、[test_export_by_version.py](../backend/tests/test_export_by_version.py)（改 pytest 风格）、[test_proxy_exemption.py](../backend/tests/test_proxy_exemption.py)、[test_youtube_subtitle.py](../backend/tests/test_youtube_subtitle.py)、前端 `previewVersionSync.test.ts`（新，源码结构守卫） |
| 文档 | [code-audit-3.md](code-audit-3.md)（新）+ 本文件 |

- **不升版本号、不打包、不发布。** `PROMPT_VERSION` **不 bump**（没改任何发给模型的文本）。
- **不做**（明确记录，非遗漏）：`MarkdownVersion` 两处声明（`api/` 不 import `store/`，分层所限）、
  ingest 8 位 hex 哈希（理论级，与 `chart_{sha1[:8]}` 同款取舍）、404 文案、`VersionSwitcher` 的
  `Esc`/`aria-haspopup`。
- ⚠️ **已知缺口**：`P1-01`（前端版本滞后）**没有组件级行为测试**，只有源码结构守卫 + 一条前提
  行为断言。整树渲染 `PreviewArea` 要 `vi.mock` 掉一整个依赖图，脆弱且慢，本轮未做 —— 故这条
  修复靠**结构上消灭它**（分桶 + 单一同步点）而不是靠测试兜底。

---

## 2026-10-10 — 产物版本管理重做：两版制 → 版本归档（0.5.0，未升版）

### 做了什么

**起因**：用户要「把历史版本做好」。现状是**两版制**（`{mode}.md` + `{mode}.prev.md` 两个位置，
index.json 只留 2 条），有三个按伤害排序的问题：

1. **编辑也在造版本**。[markdown_edit.py](../backend/app/routers/markdown_edit.py) 走的是同一个
   `save_markdown`，于是「编辑一次」就把 AI 生成的那一版挤成 prev，**编辑两次 AI 那版被物理覆盖**。
2. **只有 2 版**。`save_markdown` 主动裁掉第 3 条往前的 index 条目，磁盘上 `os.replace` 也直接覆盖。
3. **「每个版本保留好它的各种信息」根本做不到**。`_rebuild_index` **显式跳过 `.prev.md`**，
   重建出的条目 `version` 恒为 1、`createdAt` 恒为**空串**、`style` 写死 `"detailed"`
   —— **删一次 `index.json`，版本历史连同时间全丢**。

而答案其实早就在文件里：产物首行的自证头有 `generated.generatedAt` / `model` / `params`
—— **只是重建路径从来没去读它**。

**用户拍板的产品模型（不可改）**：版本 = **AI 生成出来的**东西，每版独立归档；
**编辑不产生版本**（「编辑只是用户在那个对应版本上做更改，我们不负责」）；
**用「切换」而非「回滚」**（→ 切换是纯视图操作、零写入，因此**不需要「当前版指针」**这个新持久概念）；
**全部保留、不设上限**；要看时间/字数/来源/编辑标记，要用切换/按版本导出；**不做** diff 对比视图。

**① 磁盘布局：`{doc_dir}/{mode}/v{N}.md`**（[storage/store.py](../backend/app/storage/store.py)）
- **一版一文件**，版本序写进文件名（`^v(\d+)\.md$`），重建可解。
- 选目录而非平铺 `{mode}.v3.md`：平铺会与遗留的 `{mode}.md` / `{mode}.prev.md` 挤在同一命名空间，
  而现有的 `.prev` 特判正是这类坑；且 `process/`、`epub_chapters/`、`assets/` 已是「按概念分子目录」的既成风格。
- 顺带消灭[store.py](../backend/app/storage/store.py) 里「`os.replace(latest → prev)` 失败被静默吞掉、
  随后照样覆盖 latest」的降级舞步 —— 新布局没有需要保持同步的第二个指针文件。

**② 头是真相，index 只是缓存**
- `identity.version` 由 store **写盘前注入**（= 文件名 N）。此前 `provenance.py` 注释说「写盘那刻不知道
  版本号」—— 那是旧模型的理由（版本号在加锁区算、头在 generator 早装配好），新布局下版本号**就是文件名**。
- `_scan_markdown_versions()` 成为**条目构造的唯一落点**（消灭裸字面量，模式 #44）：遍历 `v*.md` →
  `parse_artifact_meta` 取 `generatedAt`/`model`/`style` 还原 `createdAt`/`source`/`style`。
  无头的文件才回退占位值，且 warning 文案如实说明「重建有损、能还原什么、不能还原什么」。
- **派生字段不进 index**：`chars` / `edited` / `source` 由 `get_markdowns_by_transcript` 读文件头现算
  （它本来就要逐版读盘，零额外 I/O）→ index/header 双真相源漂移从根上消失。

**③ 三种写入语义**（一张表定死）

| 语义 | 入口 | `replace_version` | 文件动作 | 版本序 |
|---|---|---|---|---|
| 生成 | `generate.py` 各产物 + `epub_pipeline.py` | `None` | 新建 `v{max+1}.md` | +1 |
| 编辑 | `markdown_edit.py` | `= 前端正在看的版本 N` | 覆盖 `v{N}.md` | **不变** |
| 追问（未来，本轮不实现） | — | `= 正在看的版本 N` | 覆盖 `v{N}.md`，body 追加 | **不变** |

- ⚠️ **`max` 扫磁盘取、且与写盘同处一把 `self._lock`** —— 此前文件写在锁外、index 写在锁内，
  新布局下版本号由磁盘现状决定，分开会让并发生成算出同一个 N。
- `replace_version=N` 时**文件必须已存在**，否则显式报错（失败显式化，不静默新建）。
- `index_extra` 参数退休：那个「meta 进了 index / extra 进了文件头，接错了两边都不报错」的陷阱从根上消失。

**④ 启动时迁移遗留布局**（`_migrate_legacy_markdown_layout()`，`Store.__init__` 里调一次）
- `notes.prev.md` → `v1.md`、`notes.md` → `v2.md`（无 prev 则 → `v1.md`）。
  **只 `os.replace` 改名，内容与自证头一字不动**；幂等；崩在半途的「顶层仍有 `{mode}.md`」状态
  接在最大版本号之后 + warning。迁移失败**不拒绝启动**（磁盘脏了但应用该照常可用）。

**⑤ 编辑标记搬到文件头**（`transcript_meta.mark_edited`）
- 「已编辑」此前在 index 条目里，删一次 `index.json` 重建就永久丢失。现在打 `edit` 键进自证头。
- **生成路径不写这个键** → 新版本无 `edit` → 「重新生成自动清除 edited」这条既有语义**无需任何额外逻辑**即成立。

**⑥ 按版本导出**（[routers/export.py](../backend/app/routers/export.py)）
- `GET /export/{id}` 加 `version: int = Query(0)`（0 = 最新，与 `get_markdowns_by_transcript` 同语义）。
- ⚠️ **指定版本查不到时显式报错、不回退** —— 两条既有回退链（`notes → 无 mode 时的 markdown`、
  `→ process_results`）在 `version` 存在时都被禁掉：它们会把「notes 的第 2 版」悄悄换成别的东西。
- ZIP 内文件名带 `_v{N}` 后缀（仅 >1 版时加），全部产物导出**自然包含全部版本**。

**⑦ 前端**
- [VersionSwitcher.tsx](../frontend/src/components/preview/VersionSwitcher.tsx)：一行 `v{n}` 按钮 →
  **下拉**。触发按钮显示当前版 + 字数；每行两行式（`v{n}` + 「已编辑」徽标 + 当前项勾 /
  时间 · 字数 · 来源）。≤1 版整块隐藏的既有行为保留。
- **编辑跟随当前版本**：`saveMarkdownContent` 带上 `markdownMeta.version`，后端就地覆盖那一版。
- [ExportMenu.tsx](../frontend/src/components/ui/ExportMenu.tsx)：三条后端导出路径（HTML/Word/PDF）
  **共用** `backendParams()` 一处拼版本参数；文件名加 `_v{N}`。
- ⚠️ **`markdownMeta` 只在加载文档时设一次（notes 优先）**，切 tab **不会**更新它 →
  编辑 / 导出 / 高亮三处**都先校验 mode**，否则「切到 polished 导出却带上了 notes 的版本号」。
- ⚠️ 新模块 [lib/markdownMeta.ts](../frontend/src/lib/markdownMeta.ts)：`MarkdownView → MarkdownMeta`
  的映射此前**在 5 处各写一份**（App 恢复态 / GenPanel×2 / EpubInterpretPanel / PreviewArea），
  加字段时只有一处会记得改，其余静默少字段、零报错 → 收口为 `toMarkdownMeta()`。

**⑧ 配图不再被覆盖**（用户要求「顺手修」）
- 排查后范围**远小于预期**，如实收窄：图表 `chart_{sha1(svg)[:8]}.png` 与抽帧截图
  `screenshot_{i}_{uuid4[:8]}.jpg` **已经是内容哈希 / 天然唯一**；视频网格图走 `mkdtemp` 从不落 assets；
  `original.png` / `cover.*` **刻意不改**（它们是文档的固有身份，不属于任何一版，被覆盖是正确的）。
- **唯一真需要改的是 [ingest.py](../backend/app/routers/ingest.py) `_persist_media_images`**：
  `img_{count:04d}` → `img_{sha1(bytes)[:8]}`。真正的失效场景是 **retranscribe**（复用同一 doc_dir
  原地覆盖）——旧版本的笔记引用 `assets/img_0000.png`，而文件已换成新转录的第一张图。
  同图同名（幂等、不累积孤儿），不同图不同名（不覆盖）。引用改写就在同一函数内，闭环不外溢。

**⑨ 测试 +4 文件 / +25 用例（另有 1 个脚本式端到端，16 断言）**
- 后端 [`test_store_version_layout.py`](../backend/tests/test_store_version_layout.py)（8）/
  [`test_store_legacy_migration.py`](../backend/tests/test_store_legacy_migration.py)（6）/
  [`test_export_by_version.py`](../backend/tests/test_export_by_version.py)；
  `test_query_contract.py` 扩版本条目字段集断言（`chars` 是**纯正文**字数、`edited`、`source`）。
- 前端 [`versionSwitcher.test.tsx`](../frontend/src/test/versionSwitcher.test.tsx)（11）：
  断言的是**信息有没有真的到用户眼前**（时间/字数/来源/编辑标记四列），不是「组件渲染了」。
- ⚠️ 测试里一个反复踩的坑：`_rebuild_index` 会按正文 sha256 **重推 transcript id** →
  删 index 重建后必须用**新 id** 查产物，否则得到空列表，「版本丢没丢」这条断言会**以错误的理由失败**（假红）。

**⑩ 顺带清理**
- `markdown_edit._EDITABLE_MODES` 改为 `set(MARKDOWN_MODES)`（**与 store 同源**）——
  此前另写了一份 `{"notes","polished"}`，两侧漂移的症状是「新 mode 有产物、UI 上有编辑入口、后端却 400」，
  而**没有任何报错指向这里**（模式 #54）。
- `_rebuild_index` 里的死 import `import re as _re` 删除；自愈 warning 文案订正为如实说明重建的有损范围。
- `generate.py` 与 `markdown_edit.py` **各写了一份几乎相同的 `versions` 列表推导** → 收口到
  `QueryService.artifact_versions(entries)`。
- [capabilities.py](../backend/app/routers/capabilities.py) 的「本机是否已有该文档产物」判据改调
  `store.markdown_dir_has_version()`，不再内联拼 `notes.md` 路径（漏改的症状是「已完成的生成一直被误报成中断残留」）。

### 为什么

1. **「版本 = 位置」是病根**。旧模型里「一个版本读到哪份文件」取决于**别人**的版本号
   （要先算同 mode 的 `max_version`、再决定读 `.md` 还是 `.prev.md`）。这既是「只有 2 版」的成因，
   也是「编辑会挤掉生成版」的成因 —— 一个写入动作的效果取决于当时有几版别的产物。
   改成「版本 = 一个文件」后，读、写、删、重建四条路径都不再需要互相对齐。
2. **重建必须能还原真相**。用户要的是「每个版本保留好它的各种信息」，而信息的持久家本来就在文件里
   （自证头），index 只是加速定位的缓存。此前重建路径不读头 → 缓存一丢信息就丢，与「头是真相」的
   既有纪律自相矛盾。
3. **只有 2 版是历史包袱不是需求**。用户明确要全部保留。代价是 assets 与版本文件单调增长 ——
   本地 md 体量小，可接受。
4. **「切换」这个选择消掉了一个新概念**。若用「回滚」（写操作），就必须引入「当前版指针」这个新持久状态，
   以及它的一整套同步/漂移问题。切换是纯视图操作 → 不需要指针。

### 影响范围

| 层 | 文件 |
|---|---|
| 存储 | [store.py](../backend/app/storage/store.py)（新布局 + 版本扫描 + 迁移 + 派生字段）、[transcript_meta.py](../backend/app/core/transcript_meta.py)（`edit` 键）、[provenance.py](../backend/app/generation/provenance.py)（版本号注入） |
| 路由 | [markdown_edit.py](../backend/app/routers/markdown_edit.py)（`replace_version`）、[export.py](../backend/app/routers/export.py)（`version` 查询参数）、[generate.py](../backend/app/routers/generate.py)（版本列表收口）、[capabilities.py](../backend/app/routers/capabilities.py)（产物存在判据）、[ingest.py](../backend/app/routers/ingest.py)（图片内容哈希命名） |
| 服务 | [query.py](../backend/app/services/query.py)（`artifact_versions`） |
| 前端 | [VersionSwitcher.tsx](../frontend/src/components/preview/VersionSwitcher.tsx)（重写为下拉）、[lib/markdownMeta.ts](../frontend/src/lib/markdownMeta.ts)（新）、[ExportMenu.tsx](../frontend/src/components/ui/ExportMenu.tsx)、[PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)、[api/core.ts](../frontend/src/api/core.ts)、[store/types.ts](../frontend/src/store/types.ts) + 5 处 `toMarkdownMeta` 调用点 |
| 文档 | CLAUDE.md（管道总览 + 「产物版本」段）、architecture / AGENT-ONBOARDING / STATUS / export-pipeline / 本文件 |

- **迁移**：旧库首次启动自动迁移，**只改名不改内容**，幂等；存量文档的正文引用照旧可用（不重写正文）。
- **不升版本号**（沿用只推 patch 的惯例，本轮未要求升版）；**不打包、不发布**。
- `PROMPT_VERSION` **不 bump** —— 改的是自证头与存储布局，不是发给模型的文本。
- **不做**：diff 对比视图、回滚、追问功能本身、版本上限与清理、改 `original.png`/`cover.*` 命名、
  orphan GC（assets 里未引用文件不影响任何东西——导出**按 markdown 引用收集**，不扫目录）。

---

## 2026-10-10 — 左侧活动栏 + 左侧面板互斥（0.5.0，未升版）

### 做了什么

**起因是一句担忧**：「后面会有新工具和更自由的插件，现在栏位已经开始挤、再往里塞就没地方放了」。
要的是 VS Code 左侧那种竖排图标列 —— **每加一个功能只加一个图标，不动现有布局**。

**① 新增左侧活动栏**（[ui/ActivityBar.tsx](../frontend/src/components/ui/ActivityBar.tsx)）
- 常驻最左的竖排图标列。**面板项从 `SlotRegistry.grouped().left` 派生**（顺序即注册顺序）
  → 加一个 left 面板 = 活动栏自动多一个图标，零改动。
- 两个**动作项**（AI 对话 / 设置）在 store 之外硬编码 —— 它们不是 slot，没有 `SlotDef` 可派生。
  ⚠️ 动作项**不写 `aria-pressed`**（写了会被读屏念成「切换按钮（未按下）」）。
- **顶栏的生成 / 文档库 / AI / 设置四个按钮全部删除**。刻意不留第二套入口 ——
  两处各写一遍必然漂移（模式 #54）。

**② 左侧两面板默认互斥**（[lib/layout-prefs.ts](../frontend/src/lib/layout-prefs.ts)）
- 同一时刻只展开一个，由活动栏图标切换；**再按一次是关闭、不露出另一个**（VS Code 语义）。
  改回「允许并排」在 设置 → 外观 → 侧栏布局（`mbw_layout`）。
- ⚠️ **实现是「一个选择器字段」，不是「打开甲时遍历同侧把乙收起」**。后者**是可绕过的** ——
  直接改单个 slot 的调用点有四处：`openDocLibSearch`（Ctrl+K / 顶栏搜索）、DocLibrary 右键
  「重新生成」、GenPanel 与 DocLibraryToolbar 的面板自关闭。任何一处漏改，互斥模式下就会
  露出两个面板。改用 `uiSlice.activeLeftPanel: string | null` 后，**结构上不可能出现两个开**，
  且 `uiSlice` 不需要知道「有哪些左侧面板」（→ 不必 import `slotRegistry` → 环不存在）。
  四处调用点全部改走统一入口 `openPanel` / `closePanel` / `togglePanel`
  （`toggleGenPanel` / `setGenPanelOpen` 保留为**薄委托别名**）。
- 互斥模式下被关掉的面板**连 `PanelStrip` 都不渲染**（一次只该有一个面板，34px 窄条没有意义）；
  并排模式保留现状（收起 = 窄条）。全部关闭 = 只剩活动栏。

**③ `SlotDef.component` 补成必填**（[lib/slotRegistry.ts](../frontend/src/lib/slotRegistry.ts)）
- 组件本体此前硬编码在 [SlotLayout.tsx](../frontend/src/components/ui/SlotLayout.tsx) 的
  `COMPONENTS` 映射里 —— **注册表登记一个面板、布局层才知道它长什么样，加面板要改两处**。
  删掉映射后，**加一个面板 = 只改注册表一段**。
- ⚠️ **环的判据是「`uiSlice` 值引用 `slotRegistry`」，不是「注册表持有组件」**。
  `slotRegistry → GenPanel → store → uiSlice → slotMetrics` 这条链无环，因为 uiSlice 引的是
  零依赖的 `slotMetrics`。这条线由新模块 [lib/panelToggle.ts](../frontend/src/lib/panelToggle.ts)
  （开合判据纯函数，type-only 引用 `SlotState`）继续守住。
- `slotMetrics.ts` 的依赖图注释同步改写（原先那句「将来会 register 面板组件」现已成真，
  而结论不变）。

**④ 沉浸阅读补上独立入口**（[preview/PreviewArea.tsx](../frontend/src/components/preview/PreviewArea.tsx)）
- 预览区工具栏加「沉浸」按钮（`Expand` 图标），位于「阅读宽度」与「日志」之间。
  此前只有 `Ctrl+\` 一个入口 —— 而沉浸态下工具栏本身不渲染，故这个按钮只承担「进入」，
  退出仍由既有的常驻角标 / `Esc` / `Ctrl+\` 承担。
- 活动栏在沉浸态下一并隐藏（沉浸的语义是「去噪」）。

**⑤ 测试 +4 文件 / +60 用例**（44 → 48 文件，503 → 563 用例）
- `panelToggle.test.ts`（三个纯函数真值表）/ `layoutPrefs.test.ts`（校验 / 损坏退默认 / 往返）/
  `activityBar.test.tsx` / `slotLayout.test.tsx`，另有 `store.test.ts` 扩两组。

### 为什么

1. **入口散在顶栏**：生成 / 文档库 / AI / 设置四个按钮硬编码在 header 里，**不由注册表驱动**，
   新增面板要手工加按钮 —— 注册表的「注册即得」名存实亡。
2. **「注册即得」缺了一半**：`SlotDef` 有 id / 图标 / 宽度上下限 / 折叠行为，唯独没有组件本体。
3. **互斥无处安放**：左侧两个面板的关闭语义本来就不一致（genPanel 用 `visible` 彻底关、
   docLib 用 `collapsed` 收成窄条）。

⚠️ **`Ctrl+G` 的语义随布局偏好变化**：互斥下从「切换」变成「**切到**生成面板」，
再按一次是关闭且**不露出文档库**（并排下与改造前一致）。这处语义变化写进了
[App.tsx](../frontend/src/App.tsx) 的注释，不是顺手改的。

⚠️ **测试断言一律走「派生比对」而非硬编码名单**：`SlotRegistry` 是可变单例且**没有
`unregister`**，测试里注册的假面板会一直留着 —— 硬编码 `['genPanel','docLib']` 的断言会在
「加注册项」那条用例之后自己变红。而「加一个面板只改注册表一处」的可执行版本，
正是「新注册的面板能渲染出来 / 活动栏自动多一个图标」这两条。

⚠️ **布局层加了 `data-panel` 测试钩子**：本轮最容易出现的缺陷不是「状态算错了」，而是
「状态算对了、布局层没用它」—— 那种缺陷下只断 store 字段的测试**全绿**，而用户看到的是
两个面板并排。故互斥断言全部落在**渲染结果**上。

### 影响范围

- 前端 11 文件改动 + 7 文件新增，**后端零改动**，无新增依赖、无新增快捷键、不动 Rust。
- `mbw_*` localStorage key **16 → 17**（新增 `mbw_layout`）。
- 面板开合语义变化会影响 `openDocLibSearch`（Ctrl+K / 顶栏搜索）的观感：现在它会**接管**
  左侧（生成面板让位），而不是与文档库并排。
- 设置页「外观」新增「侧栏布局」卡片（枚举按钮，照「段落区隔」范式）。
- `slots` 仍**不持久化**（面板宽度与开合是会话态，维持现状）—— 只有「互斥 / 并排」这个
  偏好落盘。重启后回到 `docLib` 激活。

**明确不做**（勿当遗漏）：
- **不动版本号、不打包、不发布** —— 本轮到「改完 + 门禁全绿」为止。
- **不处理「文档库面板内部挤」**：那是面板内的信息架构问题（搜索 + 五个筛选小节 + 排列 +
  排序 + 密度 + 批量条），与布局层改造不共用任何代码，单独排队。
- **不做插件的前端契约**：本轮只把「官方新面板只改一处」这条打通；插件要往 UI 塞什么
  （整面板 / 面板内小工具 / 命令）是另一个量级的问题，**它的答案决定入口列要不要可分组 /
  可搜索**，先不动。

---

## 2026-10-06 — 外部审查 35 项修复（0.4.16 → 0.5.0）

### 做了什么

来自项目**外部**的一轮代码质量审查（报告 583 行 / 35 条，快照 `6c2ef28`）**全部处置**：
**32 条修复** + **3 条明确不做**（用户决定）。台账与纠偏见 [code-audit-2.md](code-audit-2.md)。

**P0（5 条，唯一对外分发型安全问题 + 两条数据完整性）**

- **路径穿越**：抽 `core/path_guard.py::resolve_within` 收口四处调用点。⚠️ 报告的「`assets` 分支」
  只是其中一个 —— 四个来源分支**全都能穿越**；且条目名另做 basename 净化堵 zip-slip
- **测试污染真实配置**：`test_e2e_smoke.py` 的隔离三件套（`LIMO_CONFIG` + `LIMO_SECRETS` +
  **临时 config 内写 `data_dir`**——只设前两个时 Store 仍落 `~/.limo`）。⚠️ 报告之外另查出
  **两条同源泄漏**：① 7413 被占用时会**连上用户正在跑的真实后端**（所有断言照样全过）；
  ② 文件名匹配 `test_*.py` 而顶层是裸执行体 → **每跑一次 pytest 就静默起一个后端 + 写一次配置**
- **index.json 自愈**：三种「不可用」（缺失/损坏/旧格式）统一先 `copy2` 出 `.bak` 再全盘重建，
  warning 逐项列出降级内容
- **xlsx 绕过 prompt 分派 + 落双份盘**：xlsx 分支提到 `build_notes_prompt` 顶部 + 两条端点统一
  单产物闸门。⚠️ 顺带查出**非流式此前也在落两份内容相同的盘**，而第二份前端够不到
- **ingest 错误信封**：`api/ingest.ts` 两处（主路径 + `fallbackJson`）。⚠️ **判据与取值是两件事**
  ——判据 `data.error || data.code`，取值才带 `data.message`（拿 `message` 判会把**去重成功**报成失败）

**P1（19 条，静默失效与同源漂移为主）**

- 插件配置重启即丢（`init_plugins` 从不回填 `plugin_configs`）；默认配置被浅拷贝污染；
  原子写固定 `.tmp` 名互相截断（**不加锁**——非重入锁会死锁）；Excel 幻影 `Sheet1` 让**每一份**
  Excel 都走 except 兜底；双产物单路失败仍标 `complete`；压缩预算跳过用户自定义窗口；
  上传临时文件泄漏（**消掉机器上 `~/.limo/tmp` 的 221MB**）；两份导出实现已分叉；
  ZIP 打包阻塞事件循环；19 个设置处理器只有 4 个有 catch；chatStore 三处竞态；
  标题 id **三处**同源（渲染侧 `children.join('')` 对 React 元素产出 `[object Object]`）；
  图表资产名撞车（改**内容 hash**，不是版本前缀）；chart 节点归一化提到分派前；
  **删自研抖音路径**（`douyin_helper/abogus.py` 头部自称 GPLv3，与「主 exe 零 GPL」的对外声明正面冲突）
  + 连带清掉 `gmssl`/`pycryptodomex` 两个只服务于它的死依赖

**P2（做 8 条）**：三处死代码（含一条**注释自称的调用方根本不存在**、一个字段漂移 7 个的零引用模型）；
`_write_agents` 的失败文案不再套用「无法打开本机库」；对话三端点补 404（**前后端同批**——
前端三个 handler 此前不 await 不 catch，直接改 404 会把静默失败换成未捕获 rejection）；
`cloud.alibaba` 回填补齐（成对表唯一漏项）；`StoreError` 按 errno 细分（**winerror 优先**：
Windows 共享冲突的 errno 也是 EACCES）；Rust 侧**一接一删**（接上 `backend-warning` 前端横幅 /
删两个同步命令的冗余第二出口）。

**纠偏（本轮最承重的部分，先纠偏再动手）**：**8 条照报告改会引入新问题**（如「产物写盘纳入同一把锁」
会死锁、「给 hchain/branch 补归一化」会把崩溃换成静默错图、「正则禁 `text-white`」会假阳性一片）
+ **6 条描述与现状不符** + **6 处报告漏项**（第三条标题 id 实现、`rehype-slug` 死依赖、
Rust 另两个无监听事件、测试污染的另两条路径）。

### 为什么

报告的结论 **35 条无一假阳性**，但**修法**与**描述**有相当比例不能照做 —— 这是本轮最花时间的部分：
「结论对」不等于「照它改就对」。8 条改判里有 3 条会造成**新增缺陷**（死锁 / 静默错图 / 大面积假阳性），
2 条会**删掉仍在用的契约**（`plugin_config` 的 stdin 契约）。

缺陷的主形态是项目已反复沉淀的那一条：**判据只覆盖了一种写法，而旁边那份漏了**（模式 #54 同源漂移）。
故本轮多处修法不是「补上漏的那个」，而是**抽公共入口**（`path_guard.py` 收口四处穿越检查）。

### 影响范围

- 版本 **0.4.16 → 0.5.0**（用户明确要求推 minor；本项目惯例是只推 patch）
- **行为变更**：抖音导入改走 yt-dlp 通用路径（**功能不丢，只是要下整段视频**，不再只取音频流）；
  `~/.limo/tmp` 的失败残留会正常清理
- 文档：新建 [patterns.md](patterns.md)（关键模式 65 条从 `CLAUDE.md` 拆出，184KB → 141KB ——
  ⚠️ **未达计划写的 <64KB**，拆分前的前 693 行本身就有 110KB，如实记录）；
  `STATUS.md` 11 处统计校正；`CLAUDE.md` 的四个测试命令改 `pytest` 形式（脚本式调用已不产生用例）；
  导出「进度反馈」行改标 🟡（**后端就绪、前端未接线**，如实描述）
- 未做（用户决定）：ruff 接入、CHANGELOG 切分、字体子集化、STATUS 生成脚本

---

## 2026-10-06 — 打包合规补口子：第三方总清单随包

### 做了什么

- `backend/LiMoBackend.spec` 的 `datas` 新增一项 `('../THIRD-PARTY-NOTICES.md', 'licenses')`
  → 总清单落到安装目录 `_internal/licenses/THIRD-PARTY-NOTICES.md`
- `licenses/README.md` 补全三处：① 新增「先看总清单」指路节；② 文件表补上此前**漏列**的
  `README-ffmpeg.txt` 与新随包的 `THIRD-PARTY-NOTICES.md`；③ 新增「安装目录中其他许可证位置」
  表 —— `_internal/static/licenses/`、`_internal/*.dist-info/licenses/`、`ffmpeg/` 三处，
  含相对本目录的路径写法
- `THIRD-PARTY-NOTICES.md` 第 5 节第 1 条改写：写明本文件自己的随包位置（原文「各许可证文本
  随发行包附带（**本文件** + …）」在它自己不随包时**是不成立的**）
- `docs/BUILD.md` 第 38 行同步说明两项由 `datas` 一并收集

### 为什么

用户提出「似乎没有把那些开源项目需要的协议文件写进来」。实查后**后端侧是完整的**——
`_internal/licenses/` 5 文件、29 个 dist-info 里 24 个带 `licenses/`、`static/licenses/` 4 文件、
`ffmpeg/` 内 GPL 全文 + 构建说明，全都在包里。

真正断掉的是**清单与读者之间的那一段**：`THIRD-PARTY-NOTICES.md` 是唯一的总清单（组件名 /
许可证 / 全文位置，四类），**但它没进 `datas` → 只在 GitHub 上**，而它的读者（装了包的人）
手里只有安装目录。装机用户打开 `_internal/licenses/` 看到的是 5 个文件 + 一份只讲
ffmpeg/magika/markitdown 三个组件的 `README.md`，**看不出「还有 16 个前端依赖、4 份静态资源许可证」**。

这是模式 #51（「能力存在 ≠ 该路径在用」）的合规版本：**清单存在 ≠ 读者拿得到**。
修法不是再写一份清单（那会变成第二份真相源，模式 #54），而是把唯一那份送到读者手上，
并让目录内的 `README.md` 只做**指路**（讲这个目录 + 指向总清单），不复制组件表。

### 影响范围

- 产物：`_internal/licenses/` 由 5 文件变 6 文件，安装包体积 +3.4KB
- 仓库：`licenses/README.md` 读者从「仓库访客」正式转为「安装目录内的访客」——
  里面的相对路径以**安装目录**为基准书写，仓库里看会对不上（这是刻意的：仓库读者该看的是
  根目录 `THIRD-PARTY-NOTICES.md` 与 `docs/ACKNOWLEDGMENTS.md`）
- 未做（用户本轮未选，**仍缺**）：① 前端 16 个依赖的许可证**全文**收集（现在只有
  `THIRD-PARTY-NOTICES.md` 里的名字表，无版权声明原文）；② 安装向导的许可页
  （Tauri `bundle.license` / `licenseFile` 未配，NSIS/MSI 安装时不显示任何协议）

---

## 2026-10-06 — 公共常量索引表 + 决定不做同源扫描器

### 做了什么

`CLAUDE.md` 新增「**公共常量与判据的「家」（写新代码前先查，别复制）**」一节：一张 10 行
的表，列出本轮审计抽出来的公共模块 + 既有的单一真相源（`model_caps` / `markdown_assets` /
`resource_id` / `url_utils` / `provider_defaults` / `token` / `transcript_meta` / `imageModes` /
`slotMetrics`），外加「集合类一律经 capabilities 下发」的边界。

### 为什么

本轮 25 项里有 **8 项**（P2-18/19/20/22/23、P0-01、P1-11/12/14）根因都是「同一份东西住在
多处」。而它们**全都不是「不知道要抽公共」，是「抽出来的家没人知道在哪」**——
`markdown_assets.py` 收的那六份资产正则里四份逐字同构，写的人显然不是故意的，是
「找不到 / 想不起来有这么个东西」。

所以降低这类缺陷的方向不是「事后扫描」，是「**让家可查**」。成本 30 分钟。

### 决定：**不做**同源扫描器（曾拟名 `scripts/scan-dupes`）

曾考虑写一个扫全仓「同一字面量出现在 ≥2 处」的脚本。**评估后放弃**，三条理由：

1. **最危险的那类它抓不到**。本轮最该抓的是漂移型——P1-11 id 长度上限两处不一致、
   P1-12 文件名截断 80 vs 60、P0-01 前后端门控不对称。它们的特征是**值不一样**，
   而扫描器找的是「相同的重复」——**值不同的东西在它眼里根本不是一组**。
2. **它能抓的，grep 也能抓**。P2-18 那六份正则逐字相同，缺的不是发现能力，是「**想到
   去 grep 它**」。扫描器补的是一个没做过的动作，不是补一个做不到的能力。
3. **调教成本没有终点**。第一版必然几百条误报（`"utf-8"`、`"error"`、`"id"`、API 路径
   片段满地都是），要设阈值、加白名单、逐条判断「这个重复合法吗」；而项目每长一块就
   可能冒出新的合法重复。最后得到的是一份**需要维护的清单** —— 正是本项目想消灭的
   东西。且它的已知失效方式是「误报高 → 被忽略 → 等于没有」（与「永远绿的检查」同族）。

若将来「同一个东西被写了两遍」再次成规模出现，**那时再做窄版**（只扫正则字面量，约
100 行）—— 带着真实误报样本去调，比现在凭空设计准得多。

### 影响范围

`CLAUDE.md`（+1 节）；`docs/CHANGELOG.md`。**无代码改动**。

---

## 2026-10-06 — 门禁总入口 `scripts/check-all`（一键分层）

此前 5 条门禁命令（`tsc -b` / oxlint / vitest / pytest / check-cli）散在文档各处，**靠人记**。
新增编排脚本，一条命令跑完，或按域跑一部分。

### 分层的轴是「域」不是「快慢」

原本的想法是切「快档 / 全档」，**实测后被否掉**：全套跑完只要 **40s**（tsc 5 + lint 0 +
vitest 9 + pytest 15 + check-cli 10），并不慢；而按快慢切会切出一层「5 秒但几乎不验证行为」
的假安全 —— 本轮 25 项审计发现里**没有一项**是 tsc / oxlint 抓得到的（全是静默失效、同源
漂移、死代码，必须跑起来才现形）。按域切则每层都是该域的**完整验证**，且域的边界正好是
开发者思考的边界（「我在改前端」）。

```
bash scripts/check-all            # 全部，约 40s
bash scripts/check-all frontend   # 改了前端
bash scripts/check-all backend    # 改了后端
bash scripts/check-all cli        # 改了 limo_cli / QueryService
bash scripts/check-all quick      # 5s，仅 tsc + lint（⚠️ 不验证行为）
```

### 三个设计决定

- **不重新实现任何检查**：`cli` 那层就是 `bash scripts/check-cli`。编排脚本复刻被编排的
  脚本是「同源漂移」最经典的入口（模式 #54）—— 写一个「简化版 check-cli」等于埋下第二份真相。
- **串行、每项独立、最后汇总**：一项失败不影响后续项照跑，一次看到全部问题；输出保持串行
  是为了可读（并行能快 ~20s，但交错输出没人看得下去）。
- **失败时先打「关键行」再打尾部**（实测驱动）：pytest 失败时尾部 20 行里 **18 行**是 FastAPI
  的 deprecation 堆栈，真正的 `FAILED tests/…` 只占一行且被压在最后 —— 只给 `tail` 等于给了
  一屏噪音。改为先 grep 通用失败信号（`FAILED|ERROR|error TS|Error:`）再附尾部兜底；
  grep 不到也不影响。

### 对照实验（模式 #38）

注入一条必失败的测试 → `✗ pytest (exit 1)` + 汇总「1 失败」+ 脚本 exit 1；还原后工作区干净。
未知目标 → exit 2。两处均实测，非推断。

### 顺带修正文档里的门禁说明（两处实际是错的）

- `docs/AGENT-ONBOARDING.md` 写着「全部是脚本式，**不是 pytest 用例**，别指望
  `pytest --collect-only` 数得准」—— **实测 `pytest tests/ -q` → 197 passed**，这句是错的
  （那些文件既支持直跑、同时也是合法 pytest 用例）。改为「两种跑法都行，优先 pytest」，
  并删掉那个已被 pytest 取代的手写 `for` 循环。
- 同文件 7.2 的「**384 tests / 29 文件**」已过期（实际 467 / 39）。**改为不再写死计数**
  （CLAUDE.md 同处一并处理）：写死的计数必然过期，而**写错的计数比不写更糟** —— 它给出
  确定感，让下一位读者不再核对（模式 #54）。

### 排查记录：CRLF —— **结论是不需要处理**（记此以免重复排查）

提交时 git 提示 `LF will be replaced by CRLF`（`core.autocrlf=true` 且仓库无 `.gitattributes`），
而 bash 脚本带 CRLF 通常会坏。**实测三个场景，均不触发**：

- **当前工作区**：`git ls-files --eol scripts/` → `i/lf w/lf`，本来就是 LF；
- **Windows clone**（autocrlf 生效，`file` 确认 `with CRLF line terminators`）：**Git Bash 会剥离
  CR** —— 最小复现脚本里函数收到的参数经 `od -c` 验证是纯 `bar` 而非 `bar\r`；clone 出的
  `scripts/check-all bogus` 正常输出并 exit 2；
- **Linux（CI）**：`core.autocrlf` 默认 `false`，检出即 LF。

故**刻意不加 `.gitattributes`** —— 那会为一个实测不存在的病加防护，而防护和判据一样需要
区分力（模式 #38）。本条记录在此，是为了下次看到同一条 warning 时不必重新排查一遍。

### 影响范围

新增 `scripts/check-all`（+1 文件）；`CLAUDE.md` / `docs/AGENT-ONBOARDING.md` / `AGENTS.md` /
`docs/CHANGELOG.md` 各更新门禁说明。**无代码逻辑改动** —— 各层跑的就是原来那五条命令。

### 门禁

`bash scripts/check-all` → **5 通过 / 0 失败 · 40s**。

---

## 2026-10-06 — 代码审计 25 项修复（4 批）+ 报告纠偏

对 `docs/code-audit.md`（24 项，快照锚定 `4174166`）执行**全量修复**，分 4 批 5 个 commit。
动手前先做了一轮报告纠偏——**审计报告本身不能当可信基线**。

### 报告纠偏（动手前 + 实施中累计 8 处）

动手前查出 3 处「照着修会修坏」：`engines/docling.py` 被报成死文件，实为**PDF 默认引擎**的
栖身处（删了 = 删掉主引擎，剔除）；P0-04 建议的 `search.py::rebuild` 是「DELETE 全表 + 全量重插」，
且启动时**已在调用**（拿全量重建干单条删除的活，改用更小的修法）；P0-03 的「切到 KaTeX 图片
管线」前提不成立（`_render_formulas_katex` 是给 HTML 导出在 **WebView 里**渲染的，服务端拿不到
位图）→ 用户重新拍板「移除 UI 选项 + 完整删 matplotlib 分支」。

实施中又证实 5 处不符，其中两条会**直接修坏功能**：

- **P2-17 的「接回 `clear_epub_task`」是反的**——`epub_pipeline.py:251-254` 逐字写着「取消＝
  **保留进度**，不入阶段 2」，三态 UI 的「继续上次解读」正靠它读非 `done` 状态。在取消路径清
  ＝ **摧毁续跑**。改为**保留不接**（它是与 `cache_put`/`cache_get` 同类的多余门面方法）。
- **P0-01 半 A 不配半 B 会制造同款新 bug**：`AVAILABLE_OPTIONS` 只有 8 个键，epub/document
  走的是「后端有兜底、前端没有」的不对称路径。只把 `formulas` 改成走 `availableFormats`，
  这两类的公式勾选框会**直接消失**（后端支持、UI 隐藏）——正是本项要修的那个形状。

⚠️ **最普遍的一课**（记入模式沉淀）：**「功能缺少调用点」有两种，处置完全相反**——一种是
**本该被调用而漏了**（真缺口），一种是**本来就不该有调用者**（多余公开方法）。区分二者的唯一
办法是**把候选落点逐个打开看它的语义**，而不是看名字像不像。

### 批次 1 — P0 用户可直接触发（4 项）

- **P0-01 公式集门控**（含审计漏项）：`formulas` 与 `references` 拆开改由 `availableFormats`
  驱动 + 两端兜底同源。
- **P0-02 风格档跨类型**：**条件重置**——「仅当档位不在新类型的可用集里才重置」。⚠️ 无条件
  重置会冲掉跨类型**共享的合法档**（plain/academic/business），是本条最易踩的坑。
- **P0-03**：移除「公式渲染为图片」UI 选项 + **完整删除** matplotlib 分支（半删比不删更糟：
  `_latex_to_png` 里的 `plt` 会失去定义 → `NameError`）。MD 保留 `$...$`。
- **P0-04 搜索幽灵**：`ingest.py` 的静默 `except Exception: pass` 拆开记 warning。

### 批次 2 — P1 静默失效与死代码（11 项）

`coerce_style` 收口（此前非法 style 在流式/非流式两条路落**不同档**且都不报错）/ 合成维度来路
落 `dimensionsSource` meta / 截图失败 marker 零残留 / 删 `llm_presets` 死配置 / 删 `resvg-py`
死依赖（⚠️ `ACKNOWLEDGMENTS.md` 的行**保留**——那是三方许可 + 沿革记录，只更新 ⚠️ 措辞，
**不在合规文件上做删除**）/ id 校验与导出文件名阈值跨端同源 / 前端筛选项补 `audio` /
`PROVIDER_DEFAULTS` 下沉 `core/` / `has_capability` 收口 6 处内联（**收敛，不补报错**——后端
gate 本就在跑，补报错会把既有降级变成失败 = 行为倒退）/ `docling.py` 六处静默 `except` 逐处
triage（三修三留，保留项由**反向断言**钉住不产生 warning）。

### 批次 3 — P2 重复实现收敛（6 项）

`assets/` 正则四份逐字副本 → `core/markdown_assets.py`（与 tx 反向正则**同模块**，成对反向）/
样式别名表两份合并 / OCR 四模式标签七处收敛（报告漏了 `PreviewArea` 那处）/ 三组 fallback 改由
capabilities 驱动 / `ENGINE_CATS` 三处收敛（AST 守卫另抓出**两处报告漏掉的副本，实为五处**）/
面板默认宽 260 三处下沉 `lib/slotMetrics.ts`。

### 批次 4 — P2 收尾（4 项）+ 实施期新发现 1 项

`engineLabels` 前端接入（后端补引擎**能力位**标签表，与模型的 `modelCaps` 是两套词汇，
刻意不合并）/ 硬编码色值 → token（`TYPE_COLORS` 12 处与两处 fallback 按判据**刻意保留**）/
死函数清理 / **新增 CI**。

#### ⭐ D1（不在报告的 24 项里）：FTS5 的 `title` 列从未做 CJK 拆字

`index()`/`rebuild()` 只对 `content` 调 `_clean_for_search`（内含逐字拆空格），`title` 存原文；
而查询侧 `_prepare_query` **一律拆字**。后果：**凡「只出现在标题、正文里没有」的中文词永远
搜不到**——查询成 `使 AND 用 AND 说 AND 明`，而 title 里是整 token「使用说明」，四个字只中两个。

修法两个取舍：① 标题拆字版**并入 `content` 列**（**不能写回 `title` 列**——`search()` 直接返回
该列供显示，写回会让用户看到「✨ 使 用 说 明（示 例）」）；② `rebuild` 守卫 `if content:` →
「标题与正文**都**空才跳过」（原写法让「只有标题」的文档整条不进索引）。零 schema 变更，
启动时 `rebuild()` 自动重写旧库。

**怎么发现的**：加 CI（P2-24）时必须回答「这条门禁假设了什么环境」——本地 81 篇真实库让它
看起来无条件成立；搬到**空库沙箱**立刻红（首篇是内置示例文档，`使用说明` 恰好只写在标题上）。
⚠️ 既有 `test_title_hit_still_matches` 的名字正是「标题命中同样要能搜到」，但它用 `needle`
—— **用例名声称覆盖中文路径，用例体只走了英文路径**。

⚠️ 此时「调 seed 让关键词落进正文」等于**把真红伪装成假红**（正是 P0-04 要消灭的那类静默），
所以选择修实现。修完沙箱 **16 通过 / 0 失败**。

### 影响范围

- 后端：`core/search.py` / `core/registry.py` / `adapters/image.py` / `downloaders/douyin_helper/abogus.py` /
  `routers/capabilities.py` / `core/config.py` / `app/engines/docling.py` / `app/routers/*` 等
- 前端：`EngineSelect` / `ImageModeCards` / `PluginSettings` / `SettingsPage` / `ExportPanel` /
  `LogPanel` / `index.css` / `lib/imageModes.ts` / `lib/slotMetrics.ts`（新增）
- 新增：`.github/workflows/ci.yml`（**CI 只负责「记得跑」，命令与本地逐条相同**，不另写判据）
- 存量用户：搜索索引在下次启动时自动重建，无需干预

**门禁**：后端 **197** passed / 前端 **467** passed（39 文件）/ `tsc -b` ✓ / lint 零 error /
`check-cli` 16 项（真实库 + 空库沙箱各一遍，均 16/0）。

---

## 2026-10-05 — 预览区：文档多标签页 + 正文字号缩放

两条独立需求，同一处落脚（预览区的阅读 / 切换体验）。

> 「像 vs code 一样的快捷文档，打开与切换」
> 「再顺便加一个 ctrl 加滚轮或者是 ctrl 加 + - 放大缩小显示的字号？」「这个只对预览栏生效就行了」

### 一、文档多标签页（`DocTabBar`）

形态是 **VS Code 双标签模型**：单击 = **预览标签**（斜体、可被下一次单击替换，全组**至多一个**），
双击 = 钉成**常驻标签**，已打开的文档直接切过去。刻意**不做** Ctrl+P 快速打开面板、最近打开
列表、Ctrl+Tab 轮转、溢出下拉、拖拽排序、标签组持久化——本轮是**基础版**（溢出走横向滚动）。
也不做「取消固定」（与 VS Code 一致：只有 Keep Open，没有 Unpin）。

**`currentDocId` 仍是激活的唯一真相源**，不另存 `activeTabId`（两个真相源必漂移）；标签栏的
激活态由 `tab.docId === currentDocId` 派生。

⚠️ **两层 tab 必须分清**：`lib/tabRegistry.ts` + 全局 `activeView` 是一篇文档**内部**的视图
切换（笔记 / 整理版原文 / 转录…）；本轮加的是**文档之间**的标签栏。新代码一律
`docTab` / `DocTabBar`，且 `unsavedDialog.type === 'tab'` 这个**现有值指的是内容视图**，没去动它。

#### 核心机制：延后提交（deferred commit）

未保存编辑的守卫是**事后拦截**的（`PreviewArea` 的 effect 在 `selectDoc` 已改掉 `currentDocId`
之后才弹窗）。若点击时就改标签组，用户点「取消」回退后 `currentDocId` 回到 A、标签却已是 B。

故：**导航意图先进 `pendingOpenMode` / `pendingClose`，只有真正的 `loadDocContent` 才提交**。
于是「取消」天然等于「什么都没发生」，**不需要任何回滚**（那要快照被替换的预览槽，是 bug 温床）。

`commitTabChange` 必须挂在 `loadDocContent` 的**首行、`if (!docId) return` 之前**——「关闭最后
一个标签」走的正是 `docId = null` 那条路径。

#### ⚠️ `pendingClose` 的消费前提：三个 close 动作各分两支

`pendingClose` **只**在 `commitTabChange` 里被消费，而后者**只**在 `loadDocContent` 跑时才调。
故一个**不改 `currentDocId`** 的关闭操作（典型：关一个**非激活**标签）若也进了 pending，就永远
没人消费它——**标签关不掉，且零报错**。

判据统一为「**该操作是否改变 `currentDocId`**」（改变才可能有守卫、才需要延后；不变则立即执行）。
三个动作各分两支，`docTabs.test.ts` 用「关非激活标签 → 组**立即**变化 + `pendingClose` 仍为
`null`」把这条钉住。

#### ⚠️ 双击守卫：`closest()` 而非枚举

`DocRow` 行内有 6 个 `<button>` + 3 个 `<input>`，逐个补 `onDoubleClick={e => e.stopPropagation()}`
**枚举必漏**；最典型的漏网是**双击标题输入框**（「双击选一个词」是最自然的输入动作）。而 `onClick`
已补过 `stopPropagation` 的地方反而更危险——`dblclick` 是**独立事件**，完全不受 `click` 的
handler 影响。改为在行根做一次判定：`closest('button,input,a,select,textarea')`。

**双击天然幂等**（浏览器派发 `click → click → dblclick`，第二次 click 时文档已在组内 → no-op），
**不需要防抖**。

#### 🐞 顺带修掉的真实缺陷：简略模式下双击完全没反应

`DocRow` 的**紧凑分支根 div 缺 `onDoubleClick`**——补接线时用了 `replace_all`，而两个密度分支的
缩进不同（紧凑 8 空格 / 详细 6 空格），只匹配到了详细分支。症状是**简略模式双击钉标签完全没反应**、
详细模式却正常。已用 `describe.each(['detailed','compact'])` 的双密度测试钉住（对照实验：删掉后
恰好 compact 的 2 条红、detailed 不受影响）。

#### 其他落点

- **删除后的归属只由 `purgeDocArtifacts` 负责**——`DocLibrary.runDelete` 里那句 `selectDoc(null)`
  已删。不删的话 purge 读到的 `currentDocId` 已被置空，`droppedCurrent` 恒为 false，
  **标签组清了却不会激活邻居**。
- 沉浸模式隐藏标签栏（与工具栏一致：沉浸已隐藏文档库，没有打开新文档的入口，留着只是噪音）。
- `Ctrl+W` **保持现状**（仍是「切换阅读宽度」），不加关闭标签的全局快捷键。
- 标签栏**不持久化**：启动恢复走既有 `selectDoc(lastId)` → `commitTabChange` 兜底追加
  **一个常驻标签**，正好是「重启后只有上次看的那一篇」。未新增任何 `mbw_*` 键。

### 二、正文字号缩放（Ctrl+滚轮 / Ctrl+± / Ctrl+0）

**这是一个真实缺口，不是加了个新旋钮**：预览区**早已**有一个 `fontSizeScale`（0.92/1.0/1.06em，
跟着 Ctrl+W 的宽度档走），但 vendor（`github-markdown-light.css`）给 `.markdown-body` 硬写了
绝对 `font-size:16px`，而 `index.css` 的「阅读版式」只覆盖了颜色与行高 / 缩进——**所以此前按
Ctrl+W 时正文的字号纹丝不动**。本轮补的正是这一维。

归入既有的「阅读版式」族（`reading-prefs.ts` 加第四项 `fontScale`，默认 1.0 = 现状的 16px，
共用 `mbw_reading` 同一个 key，**不新增 localStorage 键**）。作用域 `.preview-area .markdown-body`
（特异性 0,2,0 压过 vendor 的 0,1,0，**不用 `!important`**——项目里 `!important` 是「颜色覆盖」的
做法，排版这一组走特异性）。

⚠️ **三个必须**：

1. **必须写 px，绝不能写 em**——`.preview-content` 自带内联 `fontSize: …em`，em 会**相乘**
   （1.06 × 1.25 = 1.32），字号随宽度档漂移。
2. **滚轮必须用原生 `addEventListener('wheel', h, { passive: false })`**，不能用 React 的
   `onWheel`——React 把 wheel/touchstart/touchmove 按 **passive** 注册在 root 上，在里面
   `preventDefault()` 只得到一条控制台警告，**拦不住** WebView2 缩放整个应用。监听挂在**预览区
   容器**而非 window，字面落实「只对预览栏生效」，也避免鼠标停在文档库时误触。
3. **`measurePx` 必须与字号同源**——「每行字数 → 版宽上限」的换算原本写死 `× 16`，字号可变后
   那个 16 不再是实际字号：调到 160% 时「限制每行 40 字」算出 640px，而实际每行只放得下 25 字，
   **越放大越挤（方向正好反了）**。抽成纯函数 `measurePxOf(measure, fontScale)`，让「同源」
   从「只能肉眼核对」变成可断言（同 measure 不同 fontScale 必须得到不同版宽）。

⚠️ **同批重写了 `READING_BASE_FONT_PX` 的注释**——它原本写着「必须写死 16，不能取当前渲染
字号」，那是「vendor 固定值」时代的事实。字号可变后该前提不再成立，而不改注释的后果很具体：
下一位读者会把「乘 fontScale」当成 bug **修回去**，「放大后每行字数算错」的坑原样复活。

⚠️ **已知后果（符合预期，非遗漏）**：作用域是 `.preview-area .markdown-body`，而 `.preview-area`
下有 6 个使用点——**EPUB 章节视图 / 图片理解结果 / 转录视图 / 欢迎页示例 / 编辑态预览**的字号
也会跟着变（行高与缩进当年也作用于它们，是既有事实；字号是其中最容易被立刻察觉的一维）。

### 三、打包版 vs 网页版：Rust 侧放行宿主加速键

**这是「同一个功能在两种运行形态下行为不同」的落点**，也是本轮唯一需要 Rust 侧介入的地方。

问题：WebView2 的浏览器加速键（`AreBrowserAcceleratorKeysEnabled`）**默认为开启**，而
`Ctrl+Plus` / `Ctrl+Minus` 正落在它的管辖范围内（同列表还有 Ctrl+P 打印 / Ctrl+F 查找 /
F5 刷新 / DevTools）。按键的传播顺序是：

```
① 宿主 AcceleratorKeyPressed 事件
② WebView2 按浏览器语义消费加速键     ← Ctrl+± 死在这一步
③ 网页内容 —— 只有 ①② 都没拦下的组合才会到这里
```

于是 `handleAppKeyDown` 里那些 `preventDefault()` 在**打包版根本不触发**，而浏览器开发模式
（`npm run dev`）下一切正常 —— 两端行为从根上分叉，且从代码里看不出来。

做法（`lib.rs::webview_keys`）：在 `.setup()` 里对主窗口注册 `AcceleratorKeyPressed` 回调，
只对**我们自己在用的那几个键**设 `IsBrowserAcceleratorKeyEnabled = FALSE`（浏览器跳过自己的
处理，事件照常往下传播，余下交给既有前端逻辑）。

⚠️ **三条不可越界**：

1. **不要设 `Handled = TRUE`** —— 那是「吞掉、连网页也收不到」，与目的正好相反。
2. **不要动全局 `AreBrowserAcceleratorKeysEnabled`** —— 会连带废掉 Ctrl+P / F5 / Ctrl+F /
   DevTools，排障时很麻烦；精准放行只多这几行。
3. **放行的键集合必须与 `handleAppKeyDown` 的 case 集合相等** —— 新增前端快捷键时若漏改
   Rust，症状是「开发模式好用、打包版那个键没反应」且**零报错**。故补了跨语言源码守卫
   `appShortcutKeys.test.ts`：解析两边的键集合断言相等（对照实验：删掉 Rust 的 `VK_W`
   → 精确变红）。

清单注释里逐键写明了「哪些是**必须**放行的」：`=` / `-` 是（页面缩放正在管辖范围内），其余
（W/G/I/K/`\`）当前**不在** WebView2 的加速键列表里，一并列出属防御性（对不被消费的键设
FALSE 零副作用），换来「清单与 `App.tsx` 等长」—— 新增快捷键时不必再逐个判断要不要放行。

⚠️ 老运行时（< 1.0.1108）cast 不到 `EventArgs2` 时**优雅退回现状**（该键仍由浏览器加速键
接管），不影响其它任何功能。

**待实测（唯一无法自动化的一环）**：打包版里 `Ctrl+=` / `-` / `0` / `W` 是否真的生效。
Rust 侧只做到「设了这个标志」，而**事件是否如预期继续传播到网页，只能由实测回答** ——
与滚轮那条并列列入人工验收。

### 影响范围

前端 **21 个文件**（7 新建：`DocTabBar.tsx` + 6 个测试文件；14 修改：store 两个 slice /
`PreviewArea` / `App` / `SettingsPage` / `DocRow` / `DocLibrary` / `useDocSelection` /
`reading-prefs` / `index.css` / `ContextMenu`（导出 `MenuItem` 类型供复用）/ 测试 setup 两个）
+ Rust **1 个**（`src-tauri/src/lib.rs`：`webview_keys` 模块 + `.setup()` 一处调用）。

**测试**：274 → **384**（22 → 29 文件）。四次对照实验（关非激活标签 / 紧凑分支双击 /
键盘 `+` 键 / Rust 清单少一个 VK）均精确命中且已恢复实现。`tsc` 干净、`lint` 无 error、
`cargo check` 零 warning。

⚠️ **接线单独测了**，不只测 store 动作：`fontScaleKeys.test.ts` 直接喂假事件给 `handleAppKeyDown`
（那是 export 出来的纯函数），`docTabsWiring.test.ts` 对 `PreviewArea` 的三处接线做源码级断言
（`commitTabChange` 的位置在早退之前 / `cancelTabChange` 在 `handleDialogCancel` 内 /
`DocTabBar` 带 `!immersive` 守卫）。理由是模式 #51「能力存在 ≠ 该路径在用它」——动作写得再对，
没接上去用户按下去就是没反应，而全部 store 测试照样全绿。

---

## 2026-10-05 — 合成产物形态重做（读者问题驱动骨架）+ 默认文风改「通俗」

两条产品需求合批落地。原话留档在
[pi-docs/默认文风与合成骨架-落地方案.md](../pi-docs/默认文风与合成骨架-落地方案.md)：

> 「多篇文章对比，如果有表格，那表格上的内容多的话，就会影响显示。**因为我们的产物
> 第一要义是要给人看的**，至少在多篇文档对比这里。」
> 「核心需求就是让多文档对比它产出的内容更加的通俗易懂，**一眼能看到这种点**，并且
> 也符合**人类阅读的舒适习惯**。」
> 「**大部分用户一般默认文风就是要偏通俗**，除非是论文那种要求严谨的才要求详细更多。」

### 一、五段骨架 → 读者问题驱动五节（`_build_synthesis_output`）

**根因不是文风条款不够，而是旧骨架与已写入的文风条款正面冲突**：文风第 3/4/5 条要求
「同一结论不得无新增信息地重复」，而旧骨架同时要求「分维度对比 → 共识与分歧 →
综合评价」把同一份对齐结果再走三遍，并硬写「不得省略任何一段」。**两条硬指令互斥，
模型只能违反一边**——这就是实测「后四段占 96% 篇幅」的结构性来源。

新骨架（每份信息只在最合适的位置出现一次）：

| 节 | 作用 |
|---|---|
| `## 这批材料放在一起说明了什么` | 1–3 句直接给判断（不得以篇数/材料类型等元数据开场） |
| `## 分歧在哪` | 只列真正不一致的点；材料间无分歧时**不写这一节** |
| `## 读者问题` | 3–6 个 `###`，每节三步：结论 / 材料怎么说（`[文档 i]` 锚点）/ 什么条件下不成立 |
| `## 材料作用不同` | 实测 / 观点 / 二手转述不得互相验证 |
| `## 材料索引` | **全文唯一允许表格处** + 表下一句话说明输入覆盖情况 |

**维度降为「读者问题的候选来源」**（用户拍板：模型按读者目的自行推导问题）。
⚠️ 但**逐条注入保持不变、也不能删**——「模型倾向保留**出现次数最多**的内容，而不是
**最重要**的内容」，显式列出即显式告诉它什么重要。删了 = 「维度可控」彻底失效。

⚠️ **连带三处，漏一处就自相矛盾**：

1. **事实约束第 3 条改写**：「某维度某篇未提及时写『未提及』」是**表格单元格的填空
   规则**，表格移出正文后它就失去了落点 → 改写为「材料不足以判断」（语义不变：
   不知道就说不知道）。⚠️ **抽取阶段（`synthesis.py` 两处）仍用「未提及」不动** ——
   那是喂给最终模型的中间产物，不是给读者看的正文。验收脚本的泄漏词表同步移除
   `'未提及'`，否则模型忠实执行 prompt 反被报成泄漏。
2. **覆盖说明第三次搬家**：「## 综合评价」末尾 → 「## 材料索引」表下。它每次都跟着
   骨架走（概览段 → 综合评价 → 材料索引），判据始终是「还在，只是不在开场」。
3. **文风约束续写第 8/9 条**（说人话 / 不设篇幅上限但按读者问题数量定）。
   ⚠️ 第 8 条**刻意不含**单篇 `plain` 档文案里的「不堆小标题和列表」——那句与要求
   3–6 个 `###` 小节的新骨架正面冲突，**正是本轮要治的病换个骨架复现一次**。
   两侧不共用同一份文案。

### 二、新增 `plain`（通俗）风格档并设为全局默认

此前**风格清单里根本没有「通俗」档**，而全局默认是「详细模式」（尽可能覆盖全文）——
与用户预期正好相反。

落点 **8 处，缺一处就不生效**：`Style` 枚举 / `COMMON_NOTE_STYLES`（VIDEO、DOC 经
`**COMMON` 自动带出）/ `EXCEL_STYLE_MAP`（**同值引用 detailed**——数据摘要要的是准确
完整，「通俗」在此无意义）/ `styleLabels`（**手写 dict，不会随三张 MAP 自动带出**）/
前端 `genPrefs` 默认值 / `GenPanel` 三处 fallback 数组 / 后端 6 处签名默认值。

⚠️ **两条必须显式处理的副作用**：

- **polish 会开始刷 warning**：豁免元组原为 `("detailed", "none")`，默认档改 plain 后
  **每生成一次整理版就记一条「Polish style 参数已废弃」**（无害，但日志里看起来像 bug，
  会淹掉真问题）→ 加入 `"plain"`。
- **存量用户不会变**：`genPrefs` 的 `{ ...DEFAULTS, ...raw }` 让已存的 `raw.style` 覆盖
  新默认，故「全局默认通俗」只对**新装 / 清空 localStorage** 的用户生效。
  **刻意不加迁移**——用户当前的 detailed 可能是他显式选的，静默改掉是替他做决定。

### 三、顺带修三个「静默改写风格档」的既有缺陷

同一形态 × 3：**枚举值与风格表 key 不同形 → 白名单（= 枚举值集合）不含实际发来的值
→ 被静默改回 detailed**（零报错，用户选 A 得到 B）。

| # | 输入 | 旧行为 |
|---|---|---|
| 1 | `meeting`（前端实际发的） | 枚举值是 `meeting_minutes` → 被拒 → 变 detailed |
| 2 | `none`（前端「无」按钮） | 不在枚举里且白名单没放行 → 变 detailed（而上一行注释白纸黑字写着「可能传 none」） |
| 3 | `meeting_minutes`（旧调用方）——**修 #1 时引入的反向回归** | 白名单排在 `normalize_style` **之前**，一改枚举值就把它拒掉，而 `STYLE_ALIAS` 存在的全部意义就是兼容它 |

**修法**：归一与校验都走 `normalize_style`，`"none"` 显式放行。抽
`_normalize_and_validate_style()` 到**模块级**，让测试**直调真实实现**——写在函数体里
时测试只能复刻一遍表达式，而复刻的表达式测的是它自己（模式 #51）。

### 四、验收修订（同日，真实重跑之后）

上面三块落地后**真实重跑了一次**（`~/.limo/` 那 6 篇样本，付了费）。产物并排人工
通读 + 静态形态检查（pi-docs §3.1）后，三条读者判据**基本达成**——「材料作用不同」
真的在拒绝伪互证（「属于不同路径殊途同归，不能算作独立验证」）、没有换着说法重复、
篇幅 7295 → 4046（−45%）。但实测抓出两处未达标，本次收紧。

**① 锚点密度几乎没改善（2.7 → 2.5 个/段，判据 ≤1）**

`[文档 i]` 是本轮新产物里**单一最大的视觉噪音源**，直接稀释「一眼看到点」。用户
补充的「**排版上同时出现的信息密度也不需要过大**」与它是**同一个病**。三处收紧：

| 位置 | 旧 | 新 |
|---|---|---|
| `## 分歧在哪` | 一方/另一方各标一次（同条 2 个） | **来源在每条末尾集中标一次**，不在句中重复 |
| `## 读者问题` 第 2 步 | 「只引最相关的 1–2 处」（无总量约束） | **整节 ≤3 个**，且只在「这句是谁说的」读不出来时才标 |
| `## 读者问题` 第 3 步 | 无说明（模型顺手又标一遍） | **明说不再标来源**——它谈的是上面同一批材料 |

文风第 8 条同步补排版密度：「**一段只讲一件事**，写满五六行就分段；锚点不要每句都挂」。

**② 产物末尾写出了内部术语「完整输入」——而这是模板自己要求的**

模板原文写「表下**用一句话说明本次综述的输入覆盖情况**（共几篇、其中几篇是**完整
输入**）」，模型忠实照做 → 写出「本次综述输入共 6 篇，均为完整输入」。而验收的静态
检查把「完整输入」列为**泄漏词**。

这是「**模型忠实执行 prompt，验收却把它当错**」的第 4 次复现（前三次：`'未提及'`
被列为泄漏词、`meeting_minutes` 被白名单拒掉、骨架与文风条款互斥）。**修法一律是改
prompt，不是改验收**——判据说「产物里不该有内部术语」是对的，错的是模板自己示范它。
改为读者语言：**「如实交代有没有哪篇被删节过——有就点名说是哪几篇」**。⚠️ **信息本身
保留**（「材料不完整会让结论带上条件」），只换措辞——这正是 CLAUDE.md「失败显式化」
要的东西，去掉它才是真错。

**③ 同一术语的另一个落点（顺带）：`_STATUS_LABEL["full"]`**

「✓ 完整输入」还出现在 `## 来源文档` 的逐篇标注里。它虽是给用户核对材料用的，但那段
正文**就是模型输入**（6 篇全标「完整输入」，模型很难不学）。它列的是**结果标注**而非
处理方式，读者问的是「这篇有没有被动手脚」→ 改「**✓ 未删节**」。前端预览对话框的
`SYNTH_PLAN_LABEL.full` 同步改（同一件事在两处该用同一个词）。
⚠️ **不改历史文档**：`## 来源文档` 是拼进 rawText 的**字面文本**，旧产物正文里已经
写死旧文案，改代码不影响它们——无需迁移。

### 五、新增「## 各篇速查」工具区（同日，真实场景驱动）

用户转述了一个真实使用场景：

> 「我的老师用这个软件看论文，他觉得单篇论文做得**很不错**，但他有时候要**同时看
> 多篇论文**就还是有些不方便。」

追问「不方便」具体指什么，得到三个诉求（多选）：**看不过来、要来回翻** / **比不了、
得自己列** / **写不出归纳文字**（第四项「分不清谁更靠谱」未被选中）。

**关键发现：这三个诉求落在两类要求相反的产物上。**

| 诉求 | 要的产物 | 要求 |
|---|---|---|
| 看不过来 / 比不了 | **速查（工具）** | **高密度**、结构优先、能扫 |
| 写不出归纳 | **归纳文章** | **低密度**、节奏优先、能读（用户前几轮的反馈全是说这一半） |

把它们揉进一份产物再要求「折中」，就是第四轮那个病的重演——只不过对手从「骨架 vs
文风」换成了「速查要密度 vs 文风要稀疏」。**分区才能并存，不是折中。**

⚠️ 顺带推翻一个此前的判断：上一轮我把「对比」定义为**过程**（对比是模型内部的
对齐动作，产物是文章）。在论文场景下这**可能是反的**——论文读者**必须自己读原文**
（要引用、要复现），你的产物对他是**入口**不是终点，所以「替我读完」的价值打折，
他要的是「快速定位 + 横向对照」。故「对照」在这里本身就是要交付的东西。

**形态（用户拍板：同一份文档，文章在前、速查在后；速查按维度组织）**：

```
… 文章四节 … → ## 材料索引（唯一表格 + 覆盖说明）→ ## 各篇速查
                                                     ### 方法
                                                     - 文档 1：432 份问卷，横断面调查
                                                     - 文档 3：实验室实验，n=45
```

⚠️ **速查区用竖排清单，不是表格**：用户最初的问题是「**表格上的内容多**会影响
显示」，根因是**横向挤压**（一篇一列，5 篇就是 6 列；实测坏样本 8 列 × 109 字单元格）。
改成「维度作 `###`、节内每篇一行」后，每行是一句完整的话，眼睛在「文档 i」之间跳着
扫——**「比」办到了，「表格不行」也没违反**。正文里唯一的表格仍只有材料索引那一张。

⚠️ **本轮最大的回归风险是「速查区把文章区说第二遍」**（那正是第四轮治过的病）。
故 prompt 里写了两条**显式分工禁令**，且测试**必须断它们在场**——只断「节在不在」
对这个风险没有任何守卫：

1. **不复述前面几节的分析**——文章区负责「说明什么」，速查区负责「各自是什么」；
2. **只列每篇各自有取值的维度**——「关键差异」「共同点与一致信息」这类**关系性
   维度**在逐篇取值上没有落点，直接跳过，不为凑小节硬写。

某篇在某维度上确实没有内容时写「（未涉及）」——**不用表格时代的「未提及」**，
且必须带「不要编」。

⚠️ **维度因此有了两个落点**（同一条 `dims_lines` 喂两处）：读者问题段的**候选
来源**（软）+ 速查区的**小节标题**（硬）。这不违背「维度降为候选」——恰恰补上了它
此前的薄弱处：降为候选后维度**在产物上没有硬落点**，「维度可控」有被架空的风险，
速查区把它接住了。**同一条注入，删哪一处都不行**。

### 六、真实重跑验收（两批，计费）——自适配成立、不按场景区分

速查区的设计假设是「**维度分化即场景适配**」：`paper` / `tech` / `product` 的维度全是
逐篇取值型 → 小节全留；`general` / `media` 混着**关系性维度**（「关键差异」「共同点与
一致信息」）→ 被「只列每篇各自有取值的维度」跳过。用户的判据是「**如果有问题的话，
就得按场景区分**」，故**两种场景各跑一批**（只跑论文样本验不到「缩水」这半边，
只跑通用样本验不到「缩水后是否还能用」）：

| 场景 | 材料 | 产物 | 速查区 |
|---|---|---|---|
| 论文 | 3 篇同栈技术报告（沙箱平台 / 模型 / 投机解码） | 6891 字符 | paper 五维**全留**，5 小节 |
| 通用 | 6 篇情感观点文 | 5773 字符 | general 七维 → **5** 小节（恰好去掉「关键差异」「共同点与一致信息」两个关系性维度） |

**结论：自适配成立，不需要按场景区分**——实测与设计预测逐条吻合。两条分工禁令亦
生效（速查区给原始取值、文章区给判断，零复述）。两批的额外收获：论文批抓出「三篇
作者群高度重叠 → 不能当作独立证据源来加权」，通用批指出「两者结论相反，恰好说明
这类材料不能靠『多篇说同一件事』来提高可信度」——都是新骨架逼出来的判断，旧版
（分维度对比三遍）没有落点。

**残余观察点**（未到需要动作的程度，记录备查）：锚点密度 0.56（论文）/ **0.94**
（通用，逼近 ≤1 上限），且通用批有一节标了 5 个锚点（规则是每节 ≤3）——密度总量
在限内，单节数超了。

### 七、修「模型交代依据时泄漏内部标签名 `source_content`」

两批实跑**稳定复现**：材料索引表下那句覆盖说明，模型照做了，但写成「这六篇在
`source_content` 中都标注为未删节」。`source_content` 是 prompt 的 XML 标签名，
读者拿到的是一个陌生英文词。

修法（**改 prompt，不是改验收**）：在那句后补「**直接给结论，不要解释你是怎么知道的**
（不要出现字段名、标签名或内部标记，读者不需要知道这些）」。

⚠️ 它与上一轮的「完整输入」是**同一个信号的不同表现**：模型「想显得有据可依」——
上次表现为**用错词**（把实现视角的词当读者词用），这次表现为**说多余的话**。两次
静态检查都拦不住（产物里没有错误信息，只是多了个词 / 多了一句）。这是「模型忠实
执行 prompt、验收却把它当错」的**第 5 次复现**。

- **`PROMPT_VERSION` 4 → 5**。
- `test_prompt.py` §7.9 增两条断言（「不要解释你是怎么知道的」「不要出现字段名、
  标签名或内部标记」）。⚠️ 静态检查的泄漏词表抓不到这个名字（它不在词表里），
  只能靠 prompt 自己收口。

### 影响范围

- **合成产物形态改变**：历史合成文档重新生成会与上次不同（骨架换了）。与
  `DEFAULT_DIMENSIONS` 换锚同类——「同一个按钮、行为变了」，UI 不暗示结果相同。
- **默认文风改变**：单篇 notes 默认走通俗（Excel 显式例外）。
- **`PROMPT_VERSION` 1 → 5**：骨架 / 文风约束 / 风格表三处都影响输出，按 bump 纪律；
  验收修订（锚点稀疏规则 + 覆盖说明换措辞 + 排版密度）再 bump 一档；新增速查区又
  一档；材料索引表下的措辞收紧（禁提内部标签名）再一档。⚠️ **"2"/"3"/"4" 与 "5"
  不共存**——它们的产物从未对外发布，本地重跑即被替代。
- **合成产物变长**：多了速查区（N 篇 × M 维行）。仍是**一次 LLM 调用**（用户选了
  「同一份」，不是两个 tab），但输出 token 与生成耗时都会增加。用户对此的原话是
  「多篇文章对比可以不限输出，只要效果能达到」。
- **`## 来源文档` 的 `full` 标注文案改变**：只影响**新**合成文档。
- **前端用户可见文案**：合成对话框「维度」的说明由「将作为**对比总表**的行标签」改为
  「作为**读者问题**的候选来源」——产物里已经没有总表了，旧文案在教用户一个不存在的
  概念，而它恰是「维度可控」这个功能**唯一的用户侧解释**。

### 验证

- `test_prompt.py` 新增第 8 节共 5 组断言：骨架只保留一张表 / `plain` 在枚举 + 三张风格
  表 + 排首位 / polish 不刷废弃 warning（**含 academic 对照实验**——没有它，「零命中」与
  「捕获器根本没工作」无法区分）/ 风格档归一白名单**直调真实实现** / meeting 风格块真的
  注入 prompt。⚠️ 这是**全仓唯一**守护默认风格档的断言——此前每个调用点都显式传
  `'detailed'`，默认值改成什么都不会有测试变红。
- 后端 16 个测试文件全绿；前端 287 tests / 23 文件全绿。
- 实测确认：`plain` 真的进了 `<style_preference>` 段（不是空串）、省略 `style` 参数走
  新默认档、Excel 传 `plain` 命中 detailed 文案、`meeting` 取到会议纪要风格块。
- **真实重跑一次**（6 篇样本，计费）：走完预览（零 LLM）→ 建文档（Stuff 路径，**零
  LLM 调用**）→ 生成全链，产物落盘核对。三条读者判据在真产物上逐条对照过（见「四」）。
- 验收修订后 `test_prompt.py` 第 2b 组新增 6 条断言，**判据刻意精确到示范句式**：
  `'完整输入' not in sp` 会恒为红（该词仍在**禁令**里，是「别这么写」），故断的是
  `'其中几篇是完整输入' not in sp`。同批 7.9 的判据由旧词「覆盖情况」换成读者语言的
  那句——**判旧词等于把措辞焊死**，要断的是「信息还在」。
- 速查区新增第 2c 组 5 条断言，其中两条断的是**分工禁令在场**（「不要复述前面几节
  的分析」「只列每篇各自有取值的维度」）——本轮最大的回归风险是速查区把文章区说
  第二遍，只断「节在不在」对这个风险零守卫。另有一条 `_dims_block` 的**切点跟着
  模板改**（模板改了引导句的措辞，切点字符串不同批改会 IndexError——那是「切点
  过期」而非「测试挂了」，注释里写明了）。
- **两批真实重跑验收**（论文 3 篇 / 通用 6 篇，计费）：两组静态形态判据全过（六节
  在场 / 唯一表格 / 无「完整输入」「未提及」泄漏 / 速查区按维度成节），锚点密度由
  旧版 2.5 个/段降到 0.56 与 0.94。**唯一稳定复现的缺陷是 `source_content` 泄漏**
  （见「七」），修法为改 prompt；`PROMPT_VERSION` 随之 4 → 5，`test_prompt.py` §7.9
  增两条断言钉住措辞。

---

## 2026-10-04 — CLI + 契约文档 + 门禁（「让产物能被 agent 复用」三期）

三份 pi-docs 之《CLI 设计约定》落地：**只读**命令行 `limo`，把「读库」做成一条
不依赖库内格式的稳定接口。

> **驱动这轮的是实测代价**：把一篇产物交给外部模型评审时，手工复制文件 3 次、
> 手工补上下文 2 次（「这 6 篇是什么、走的哪条路、来源多杂」全靠手写）。
> **不是没人能读产物，是读的人不知道读了什么。**

### 一、三层架构（CLI 不解析库格式）

```
limo_cli/（命令解析 / 渲染 / 退出码）
    ↓
app/services/query.py::QueryService（契约成形）
    ↓
Store / SearchEngine（数据真相）
```

⚠️ **CLI 不读 `index.json`、不读 `search.db`、不拼磁盘路径**。库内文件布局是实现
细节，照着复刻一遍就是**第 6 个副本**（现有 5 处：`store.py` / `export.py` /
`ingest.py` / `generate.py` / `files.py`），而副本的失效方式是「格式演进时漏改一处，
它静默读错」。

**七命令**：`status / list / search / get / export / folders / doctor`。
**不做写操作**（import / generate / edit / delete）——成本、并发、超时、幂等、
agent 误触发的风险都在写侧，那份账还没算。

两个传输层暴露**同一契约**：`HttpSource`（默认，`GET /api/cli/*`）与 `LocalSource`
（仅 `doctor --local`，后端没起时直开本机库）。形状由 `QueryService.to_dict()` 定义，
`limo_cli/sources.py` 只负责搬运。

### 二、输出契约（三条硬约定）

- **`--json` 时 stdout 只有 JSON**，诊断与错误全走 stderr（错误也是 JSON）。**不用 TTY
  检测** —— 管道里跑是最常见的用法，检测会把脚本场景误判成非交互。
- **顶层恒有 `schema_version`**；主版本不匹配直接失败，不按老字段读（那会得到
  「结构完整、值全错」）。⚠️ CLI 侧的 `EXPECTED_SCHEMA` **刻意不 import 后端常量** ——
  同仓 import 会让「用户机器上跑的旧 exe」永远检测不出来。
- **「没给全」分两种，`Page` 上并排两个字段组**：`has_more`+`next_cursor`（**能继续翻**）
  与 `truncated`+`continuation`（**到头了但没给完**）。把后者伪装成前者（给一个续不动的
  游标）等于让调用方的翻页循环静默少拿数据。

### 三、退出码（「空」与「坏」必须分开）

| 码 | 含义 | 码 | 含义 |
|---|---|---|---|
| 0 | 成功（**含空结果**） | 5 | 数据或响应异常（坏 ID / 坏游标 / 版本不匹配） |
| 2 | 用法错误 | 6 | 内部错误 |
| 3 | 未找到 | 130 | 被中断（SIGINT 惯例，不在主表） |
| 4 | 后端不可达 | | |

⚠️ **错误码 → 退出码按 `code` 判，不按 HTTP 状态**：`main.py` 对 `recoverable=True`
且**未显式**传 status 的 `AppError` 返回 **HTTP 200**（`_explicit_status`），按状态判
会把「资源不存在」读成一次成功响应，然后拿错误对象当数据用。

### 四、门禁 `scripts/check-cli`（16 项）

自启**独立端口 7419** 的后端（借用户的 7413 会让「后端不可达 → exit 4」那项**永远
绿，而它绿的原因是错的**）。每项同时查三样：退出码、结构化标记（错误 `code` /
`truncated.continuation` / `next_cursor`）、以及**是不是假成功**。含三条负向对照。

### 五、修掉的真缺陷（三条，全部由门禁抓到）

1. **`get ../../etc/passwd` → exit 3（应为 5）**：`/` 经 URL 编码成 `%2F`，又被 Starlette
   **解码回 `/` 用于路由匹配** → 落到「无此路由」→ 404 → CLI 报「未找到」。而同一个
   `..` 换成不含 `/` 的形态能正常到达 handler → 400 → exit 5。**同一类坏输入因字符集
   不同得到两种退出码**，且 404 那条会诱导调用方「换个 ID 重试」。修法：`require_ident()`
   在客户端用与后端**同源**的 `is_valid_resource_id` 预校验。
2. **`limo status --base-url X` → exit 2 `unrecognized arguments`**：`--base-url` /
   `--timeout` 只注册在主 parser 上，而「选项跟着它修饰的命令」是人的自然写法。修法：
   移进 `common` parents（子命令用 `default=argparse.SUPPRESS`）。
3. **`list --all` 在 HTTP 路径静默退化成「第一页 50 条」**：两个 Source 表达「一次取全」
   的方式**不同** —— 本地是 `limit=None`（`QueryService` 语义），HTTP 是 `all=true` 且
   **不传** `limit`（后端默认值是 50，不是「全部」）。归一成 `limit=None` 让 HTTP 路径
   少拿了 26 篇却看起来完全正常。**第 9 项门禁（并集 == 全集）抓到的就是它**。

### 六、契约文档两处

- **库内 `~/.limo/AGENTS.md`**（`limo doctor --write-agents` 安装，模板在 `agents_md.py`）：
  读者是**站在库前面**的 agent，故 usage 放最前（能做什么 → 返回什么 → 怎么省事 →
  哪条花钱 → 最后才是贡献与开发）。⚠️ **默认不覆盖**：已存在且内容不同 → exit 2，
  `--force` 才覆盖且先备份 `AGENTS.md.bak`。
- **仓库根 `AGENTS.md`**：读者是**改这条链的开发者**，内容与库内那份刻意不重叠。

### 影响范围

- 新增：`backend/limo_cli/`（9 文件）、`backend/app/routers/cli.py`（7 端点）、
  `backend/app/services/query.py` 的 `stats()` / `list_artifacts()`、
  `store.list_markdown_entries()`、`scripts/check-cli`、`AGENTS.md`（仓库根）。
- 改：`pyproject.toml` 加 `[project.scripts] limo`。
- 前端**零改动**（`/api/cli/*` 是新路径，既有端点形状不变）。
- ⚠️ **打包产物不含 CLI**：PyInstaller 从 `main.py` 出发做依赖分析，不会收集
  `limo_cli`。故「桌面版用户也能敲 `limo`」**尚未决策**（加进 spec / 只面向仓库开发机
  两条路各有代价）—— 从仓库跑 `python -m limo_cli` 或 `pip install -e backend/` 均可用。
- **契约测试扩到 `/api/cli/*`**（`test_query_contract.py` §8，七端点逐字段比对 +
  `schema_version` 守卫 + 续读拼接）：两条对照实验自证区分力，其中「续读拼接」
  第一版曾给出**假绿灯**（测试数据用 `"行" * 300` 是周期性的，忽略 `offset` 的实现
  拼出的字符串恰好相等）—— 已改用编号内容并把这个教训写进文件头。

---

## 2026-10-04 — 密钥与凭据卫生（「让产物能被 agent 复用」二期）

三份 pi-docs 之《密钥与凭据存储约定》落地：**LiMo 自己落盘的文件不得出现明文凭据**。
与一期技术正交（文件交集为零），但叙事相接 —— doc2 说「agent 不直读 `.limo`，走导出视图
或接口」，而那个视图要过滤的正是 config/secrets，故**必须不晚于接口开放**。

> ⚠️ **定位：卫生，不是安全边界。** DPAPI 挡的是「配置文件被复制走」的被动泄漏
> （同步进网盘、贴进反馈附件、拷给别人），**挡不住同账户下的主动恶意进程**。
> 不要当安全边界宣传。

用户库实测：`config.yaml` 与 `config.yaml.bak` 各 6 处明文，共 **12 处**。

### 一、先去重（根因修复），再加密封存

**同一把 key 被写多份才是根因** —— 加密多份同一 key 仍暴露结构，且两份迟早漂移。
本轮先消除重复存放点，再谈加密：

- **`llm.api_key` / `cloud.dashscope` 下沉进 provider 列表**（`fold_to_providers`）：
  这两个是旧版字段，但仍有读取点（`resolve_model` / `capabilities` / `engines` 的 key 探测），
  而 `routers/model.py` 保存厂商 Key 时**两个地方都写**。现在落盘前把值**折叠**进
  provider 条目（真相源），密钥库里就只剩一份密文；旧字段由 `load_config` 的
  `_hydrate_provider_keys` 启动时镜像回来。
  ⚠️ **「跳过抽取」⇔「已确认下沉成功」严格成对**：目标条目不存在、或源值本身是脱敏值时
  **不跳过** —— 那种情况下该字段是这枚 key 的**唯一**存放处，跳过 = 用户的 key 无声消失。
- **cookie 从三写回到一写**：历史三写指 `auth.bilibili` 原始串 + `_parsed.cookies` +
  `_parsed.netscape`，而 netscape 那份就是 **yt-dlp 可直接使用的 cookie 文件内容**，
  是三份里最敏感的形态。`auth.*_parsed` 现由 `strip_derived` 落盘前剥掉，读侧改为
  **实时派生摘要**（`routers/config.py::_summarize_parsed` 只回 `{platform, cookieCount}`）。

### 二、DPAPI 外移（零新依赖）

- `core/secrets.py`：**ctypes 直调 `crypt32.dll`** 的 `CryptProtectData` / `CryptUnprotectData`
  —— `pyproject.toml` 无 `keyring` / `pywin32`（CHANGELOG 记 pywin32 已移除），不为此引入新依赖。
- 密钥库落 **`%LOCALAPPDATA%\LiMo\secrets.bin`**，**刻意不放 `~/.limo/`** ——
  那是会被同步/备份/整个打包分享的数据目录（有测试钉住这个位置）。
- `config.yaml` 里只留 `secrets_ref: dpapi:limo`；`load_config` 解析回填到内存，**不回写**。
- ⚠️ **失败显式抛**（`SecretsUnavailable`），**不静默退回明文落盘** —— 静默退回等于二期白做，
  且用户从 YAML 上看不出任何异常。非 Windows 平台同理（显式抛，不降级）。

### 三、成对表：`config_io.py` 是唯一真相源

四张表各自管一件事，**每张的两侧必须同源**，对不上的症状全都不抛错：

| 表 | 一侧 | 另一侧 | 对不上的症状 |
|---|---|---|---|
| `SECRET_SCALARS` + `SECRET_LISTS` | `extract_secrets`（抽走） | `inject_secrets`（填回） | 保存后 Key 消失 |
| `DERIVED_AUTH_SUFFIX` | `strip_derived`（剥） | `auto_parse`（重算） | 派生副本反复写回盘 |
| `fold_to_providers` | 返回成功集 → `skip=` | 下沉失败**不跳过** | 同一把 key 存两份 / key 无声丢 |
| `_EMPTY_CONTAINERS`（本轮新增） | 键名 | 类型 | **启动即崩**（见下） |

⚠️ **`save_config` 的隐藏前提**：`elif disk.get(SECRETS_REF_KEY)` 分支（删库 + 撤 ref）
**要求入参是回填过的内存配置** —— 它无法区分「用户清空了凭据」与「调用方把磁盘形态的 YAML
喂了进来」。拿磁盘 YAML 直调 `save_config` = **密钥库被无声删除且 ref 被撤**（凭据彻底失联）。
迁移脚本为此必须先把库里的值 inject 回来再 save。已在该分支上写明。

⚠️ **`llm_models` 类型写错 = 启动即崩**（本轮验证时意外炸出）：`save_config` 原把
`llm_presets` / `llm_models` / `engine_*` 归成同一组补 `None → []`，而 `llm_models` 是 **dict**
（`_sync_llm_model_caps` / `model_resolver` 都拿它 `.get()`）→ 该键缺失时写出 `[]`，
**下一次启动 `AttributeError`**。改为 `_EMPTY_CONTAINERS`（键名与类型绑在同一张表里），
并加测试钉住「表里的类型 == `DEFAULT_CONFIG` 的真实类型」。

### 四、启动自检 + 迁移工具

- **`config_io.startup_check(cfg)`** 挂在 `main.py`（配置已载、logger 就绪、早于任何业务写盘）。
  查两件事：① DPAPI 能不能用；② **磁盘原貌**里还有没有非空敏感字段（含 `.bak`）。
  ⚠️ 第②条**必须读磁盘** —— 入参是 `load_config` 回填过的内存 cfg，处处非空，拿它判等于恒绿。
- **`scripts/check-secrets.py`**（重构）：遍历数据目录做**内容侧**扫描（能发现「凭据被写进了
  日志」这类泄漏），与启动自检是**分工不是重复** —— 启动路径上跑全量会让冷启动等一次目录遍历，
  而它每天给出的结论都一样。重构为 `Backend` dataclass（`config_io` / `secrets` / `keystore`
  三字段 + `ref_key` / `scalars` / `lists` / `config_file()` 四成员），删掉三处各自重算真相源的副本。
- **`scripts/migrate-secrets.py`**（新建）：`--check`（不写任何文件）/ 默认迁移 / `--rollback`。
  - **幂等**：已迁移的配置读进来时值先**从密钥库填回**，再交给 `save_config` 重新抽取。
    ⚠️ 少了这一步，第二次运行时内存配置里敏感字段全空 → `save_config` 判定「用户清空了凭据」
    → **删掉密钥库**。同理，密钥库**解不开时必须中止**，不能「当作空库继续」。
  - **刻意不写明文备份**（偏离原计划）：`config.yaml.pre-migrate.bak` 等于在磁盘上新增一份
    **静默的**明文副本 —— 正是本次要消灭的东西。回滚由 `--rollback` 承担（从密钥库填回 YAML，
    是这次迁移**真正的逆操作**，比恢复备份更精准）；需要额外保险请手动复制到库目录之外。
  - 回滚**走 `write_yaml_atomic` 绕过 `save_config`**（语义正相反：那个函数的职责正是把这些值再抽走）。
- **退出码三态**：`0` 完成/干净 ｜ `1` 需要迁移/发现明文/失败 ｜ `2` **脚本自身出错**。
  少了 2 这一档，「脚本崩了」与「查出问题」在 CI 上是同一个 1，无法区分。

### 五、`.bak` 的处置：同生同灭，无需特殊处理

原计划把它列为「三选一必须显式决定」。结论：**不用选**。`load_config` 每次启动
`shutil.copy2(config.yaml, config.yaml.bak)` —— 复制的是**磁盘文件本身**。config.yaml 一旦干净，
`.bak` 自动跟着干净（**同生同灭**）；既存的旧明文 `.bak` 由 `migrate-secrets.py` 迁移时主动刷新一次，
否则它只活到下次启动。故 `startup_check` 与 `check-secrets.py` **照查** `.bak` 即可。

### 六、顺带修的四个真缺陷

| # | 缺陷 | 落点 |
|---|---|---|
| 1 | **`GET /api/config` 脱敏缺口** —— `cloud`（含 dashscope/alibaba key）**原样返回**、`auth.*_parsed`（含明文 cookie 与 netscape 内容）、`fetch.proxy`（含 `user:pass@`） | `routers/config.py`。新叙事下更严重：**接口本身就是泄漏通道** |
| 2 | **`merge_sensitive` 调用是死代码** | `engines/__init__.py`。判据不是「异常被吞」（`is_masked` 对 dict 不抛）——而是**调用处没有接收返回值**，纯函数无副作用，**即使「成功」也是 no-op** |
| 3 | **cookie 明文临时文件** —— `_cookie_to_file()` 往系统 TEMP 写 Netscape 明文供 yt-dlp 用 | `adapters/video.py` |
| 4 | **`llm_models` 类型守卫写错**（启动即崩，见上） | `core/config_io.py` |

⚠️ **缺陷 1 的判据设计**：每个哨兵值都设计成**能在响应体里直接 grep 到**，并保留一份
`_legacy_response`（照抄旧实现）做**对照实验先证明旧实现真泄漏**，否则新实现的 PASS 可能只是假绿灯。
另含 tab 的哨兵对字符串匹配是**盲的**（JSON 把真实 tab 转义成 `\t`，子串永不命中 —— netscape
就是这么漏掉的），故另配一条**结构层面**的对照，它不依赖匹配。

### 测试与验收

新增 `tests/test_secrets.py`（16）/ `test_config_mask.py`（16）/ `test_config_io.py`（18），
共 50 条。每条判据都配了**对照实验**（把实现换成「看起来对、其实错」的变体，确认测试真会红）：
`inject_secrets` 空操作 / `strip_derived` 空操作 / `save_config` 抽了但不清空 / `startup_check`
读内存而非磁盘 / `fold` 无条件报成功 / `llm_models` 类型写错 —— 六条全部实测判红，正确实现下全绿。

跑法：
```bash
python scripts/check-secrets.py --check --root ~/.limo --root "$LOCALAPPDATA/LiMo"
python scripts/migrate-secrets.py --check     # exit 1 = 需要迁移
python scripts/migrate-secrets.py             # 执行（建议先关掉 LiMo）
python scripts/migrate-secrets.py --rollback  # 还原
```

> ⚠️ **验收口径修正**：「重扫 408 个文本文件 0 命中」不可复现（408 出自 doc1 实测，本仓库任何
> 口径都数不出），且**扫错了侧** —— doc1 的判据是「LiMo 自己落盘的文件」，目标是**用户库实例 +
> 运行期产物**，不是仓库源码。写成上方的具体命令，仓库源码另扫（防「开发时把 key 写进代码」），
> **但这是另一条判据，不要混计**。

---

## 2026-10-04 — 产物自证头 + 共享查询层（「让产物能被 agent 复用」一期）

**驱动是一次实测的代价**：把一篇产物交给外部模型评审，手工复制文件 3 次、手工补上下文
2 次（「这 6 篇是什么、走的哪条路、来源多杂」全靠手写）。**不是没人能读它，是读的人
不知道读了什么。** 三份 pi-docs 约定（产物复用 / CLI 设计 / 密钥卫生）描述的就是这件事，
本轮落地其中的地基：**产物自证 + 一个 HTTP 与 CLI 共用的只读查询层**。

### 一、产物自证头：把「它是什么」写进产物自己

一条 HTML 注释放在 `{mode}.md` **第一行**（`<!-- limo-meta {...} -->`），四条信息：
**① 从哪来**（docId / sourceType / sourceUrl）**② 怎么来的**（model + promptVersion +
params + generatedAt）**③ 是什么**（mode + version）**④ 可信度边界**（`trust` 结构先留）。

- **载体必须是文件自身**：`index.json` 删掉重建时 id 由 sha256 重推、`sourceType` 写死空串，
  一切非内容派生字段都保不住——只有正文扛得住重建。这条与合成出处头（`transcript.md`）同源。
- **`core/transcript_meta.py` 拆出信封层**：原 `parse_meta` 把 schema 校验
  （`sourceType == "composite"`）写在同一段里，于是**任何一种新头都会被它判成「损坏」**。
  现拆为 `_parse_envelope`（只认「第一行是合法 limo-meta 注释」）+ `parse_meta` /
  `parse_artifact_meta` 两个薄包装。⚠️ **「不是产物头」≠「损坏」**——历史无头产物是
  `(None, False)`，若写成 `True` 会让**全部历史产物被标脏**（测试钉住布尔值）。
- **`strip_meta` 改走信封层**：它此前依赖 `parse_meta`，拿产物头喂它会被判「坏」；而它
  正好是本轮第一次进生产（笔记当合成原料前剥头、思维导图剥头）。
- **装配层单独成模块** `generation/provenance.py`：产物有多个落点（generate 双产物 /
  epub 逐章 / process），各拼一遍 JSON 必然漂移——而自证信息的漂移是**静默**的。
- **`identity.version` 刻意留空**：真实版本由 `store.save_markdown` 在加锁区内算
  （`top + 1`），写盘那一刻调用方还不知道。**填预估值就是写假数据**；改由读路径
  （`QueryService._build`）用 index 里的真版本补全。
- **`save_markdown` 的两个 dict 参数改名**：原 `extra`（进 index 条目）旁边新增文件头参数，
  两个 dict 并排且名字都不带方向 —— 最容易接错的恰是「meta 进了 index / extra 进了文件头」，
  **两边都不报错**。改名为 `file_meta`（→ 文件首行）/ `index_extra`（→ index 条目）。
- **`_latest_notes_text` 出参前 `strip_meta`**：它把**已生成的笔记**当新合成的原料，
  加头之后那条注释会进模型 prompt 并占 token 配额——正是「不给普通 `transcript.md` 加 meta」
  要避免的同一条链路。
- 新增 `PROMPT_VERSION`（从 `"1"` 起），进 meta 的 `generated.promptVersion`。
  **bump 纪律**：判据是「这次改动会不会让同一输入产生不同输出」（prompt 模板串 /
  风格定义 / 图表规范 / 合成与压缩 prompt 都算，只改调用逻辑或参数不算）；
  **不给历史产物补版本**——它们的真实版本已不可追溯，**假数据比缺席更坏**
  （与 `identity.version` 留空同一条理由）。详见
  [prompts-inventory.md §13](prompts-inventory.md)。

### 二、共享查询层：HTTP 与 CLI 走同一条只读路径

新增 `app/services/query.py`（**零 FastAPI import**，CLI 可直接 import）。三层架构
（CLI adapter / QueryService / Store）的中间层，负责**契约成形**（分页 / 游标 / ID 解析 /
版本编排 / meta 装配 / 截断标记），**只调原语不重算**——版本选择只调
`store.get_markdowns_by_transcript` 取 `[0]`，本地重算 max version 等于多一份判据。

- **`SCHEMA_VERSION = "1"`** 第一天就定：先做无 meta 的查询层 = 把 schema 冻成缺自证字段的形状。
- **游标是 keyset `(createdAt, id)` 且不透明**（base64）：不透明是刻意的——暴露成明文
  迟早有人自己拼一个，然后漏掉并列时的 id 兜底。
- **两套「没给全」不混用**：`has_more`/`next_cursor`（可以继续翻）vs
  `truncated`/`continuation`（到头了但没给完）。把后者伪装成前者 = 让翻页循环静默少拿数据。
- **`QueryError.no_search_engine`**：搜索引擎缺失时**显式报错**而不是返回空页——
  静默空结果看起来完全正常（`main.py` 先建 Store、后建 SearchEngine，在 Store 旁边顺手
  构造 service 就会拿到 `None`）。故 `get_query_service` **惰性构造**并写明理由。
- **读路径迁移 5 处**：ingest（list/get）/ search / folders / generate（GET /markdown）/
  export（`_resolve_tab_content` 三分支）。**响应形状一律不变**（列表仍是裸 list），前端零改动。
- **ID 判据只统一读路径**：抽 `core/resource_id.py::is_valid_resource_id` 供读路径用，
  `store._sanitize_id`（归一化器，永不失败）与 `export._safe_filename`（文件名清洗器）
  **保留不动**并在 docstring 标注职责区别——三者硬合是**双向的坏**（要么写路径开始拒绝，
  要么读路径继续放行 `..`）。

### 三、顺带修复：`/api/search` 实际是 `/api/api/search`

`search.py` 自带 `prefix="/api"`，而 `main.py` 又 `include_router(..., prefix="/api")`
→ 真实路径 `/api/api/search`，而前端 `apiFetch(\`${BASE}/search\`)` 打的是 `/api/search`。
**两侧测试都绿**（前端测试 mock 掉了 `api/search` 模块；后端 `test_search.py` 测的是
`SearchEngine` 而不是 HTTP 路径；e2e 冒烟从没覆盖搜索）——所以它活了四个月。
`test_query_contract.py` 是全仓第一个能发现它的东西。修法：删掉 `search.py` 的 prefix
（全仓另外 16 个 router 都是裸 `APIRouter()`）。同类事故 2026-07-10 已发生过一次（那次在前端侧）。

### 四、顺带修复：思维导图导出泄漏自证头残片

`_build_mindmap_html` 的清洗规则里有 `r'<[^>]+>'`（去 HTML 标签），它**按第一个 `>` 断句**。
meta 的 JSON 里只要有一个字符串值含 `>`——最真实的一例是用户自定义维度 `成本 > 收益`——
注释就被从中间截断，` 收益"]},"model":"m"}...` 作为**可见文本**混进脑图节点。

⚠️ **实测判据必须是「值含 `>`」那一例**：只测「含注释」（值不含 `>`）会得到**假绿灯**
（实测：A 组干净、B/D 组泄漏）。修法是 `_build_mindmap_html` 内 `strip_meta`——
思维导图是从产物**派生的视图**不是产物载体，它**不承载**自证头；且剥头必须收口在这个
函数里（输入有两个来源，其中 `rawText` 是合成文档的三位一体、必须带头）。

### 验证

- `test_query_service.py`（新增，10 节）：分页并集 == 全集 / 版本与 store 同源 /
  无头不判损坏 / meta 往返（`-->` 与换行）/ 读路径拒 `..` **且写路径行为不变**（对偶）/
  截断与 `continuation` 同时存在 + offset 续读无缝 / 两正文域恒等 / 缺引擎显式报错 /
  `folderId=""` ≠ 不传 / 单资源白名单。
- `test_query_contract.py`（新增，7 节）：HTTP 响应逐字段 == `QueryService` 输出，
  并成对断言 HTTP **该做**的那一步（`assets` → 绝对 URL）没被搬进 service。
- `tests/conftest.py`（新增，全仓第一个）：临时 Store + 塞文档的共享工厂，
  **写成普通函数而非只写 fixture**（三期 CLI 门禁脚本要 import 它）。
- **对照实验共 8 条全部实测判红**（每条判据一个「看起来对、其实错」的实现）。
  ⚠️ 第 1 节的对照实验做过两版：第一版**完全没有区分力**（同秒项的写入顺序恰好等于
  id 降序，于是「单键排序」的错误实现照样全绿），修法是让同秒项按既非升序也非降序的
  顺序入库——**「已实测判红」这句话本身也得被验证**。
- 既有回归：`test_transcript_meta` / `test_artifact_meta` / `test_markdown_edit` /
  `test_store_composite` / `test_synthesis` / `test_generate_gate` / `test_docx_export` /
  `test_export_print` / `test_closure_shadowing` + pytest 风格 4 文件 37 项。

- 内置示例文档**刻意不加头**（`seed_sample_doc` 不传 `file_meta`）：它是示例不是模型产物，
  给它标一个假的 `promptVersion` 才是错。⚠️ 顺带核对了它那条「内容没变就不重写」的幂等判据
  （`_latest_md` 拿读回的内容与源文件比相等）——**这类判据在加头之后必然不等**，若哪天有人
  给示例文档也加上 meta，每次启动都会追加一版。无头产物被编辑时也不会被安上假头
  （`markdown_edit` 取库中已有的头，无头则 `head = None`）。

**影响范围**：读路径的**实现**换了但**响应形状零变化**（前端零改动）；产物文件新增首行注释
（导出物随之带上自证，这是设计目标）；修复的搜索路径缺陷为对外可见的行为变化
（`/api/search` 从 404 变为可用）；编辑器工具栏加一句「首行 limo-meta 注释由系统维护」
（预览区不渲染 HTML 注释，用户只在源码视图看得到它——提示是为了防止「顺手删掉」
与「改了却发现保存后又变回去」两种困惑）。

**版本**：0.4.9 → **0.4.10**。文档同步：`CLAUDE.md`（读路径唯一入口 + 关键模式 #56/#57 +
待办新增「🧩 产物复用」域）、`STATUS.md`（数字按实测重算：后端 113 py / 28,801 行、
测试 37 文件、合成域 14 项）、`prompts-inventory.md`（新增 §13 提示词版本）。

---

## 2026-10-04 — 合成产物形态重做（评审报告 → 给人读的文章）

多篇合成已上线（0.4.8），但产物实测是**评审报告而非文章**。6 篇材料 7295 字符实测：

| 指标 | 实测 | 问题 |
|---|---|---|
| 后四段占比 | **96%** | 同一份对齐结果铺了四遍（总表 22% / 分维度 31% / 共识分歧 25% / 综合评价 18%） |
| 加粗 | **40 处** | 表格首列 5 个字段名全加粗 |
| 引证 `[文档 i]` | **80 处** | 平均每 91 字符一个——读者每读一句被打断一次 |
| 首句 | 「本次综述覆盖 6 篇文档，全部为完整输入」 | 系统元数据开场 |

**根因不在模型**：三处症状在提示词里逐句有源头（`:946` 强制「如实说明覆盖几篇」、
`:992` 第 2 条「每处涉及具体篇目的论断必须标注来源」、`:952` 五段固定结构），
模型是在**忠实执行一个错误的任务定义**。本轮改动全部落在提示词结构与参数链路上。

### 一、默认对比套：paper 5 维 → general 7 维

- 新增 `DIMENSION_PRESETS["general"]`（核心主题 · 主要结论 · 关键差异 · 共同点与一致信息 ·
  重要限制/条件/风险 · 适用场景或行动影响 · 仍无法从材料确定的信息），
  **并放在字典第一位**——前端用 `dimensionPresets[0]` 预填，排后面 = 后端换了默认、
  前端仍静默预填 paper，且**零报错**（本轮计划风险 2）。
- `DEFAULT_PAPER_DIMENSIONS` → `DEFAULT_DIMENSIONS`，**全局改名、不留别名**（别名 = 一个
  对象两个名字，正是要消除的漂移）。引用传播 16 处一次改完。
- ⚠️ **两支有意变化**（计划风险 7）：① 新合成的 Map-Reduce 维度发现改为对着 7 条 general
  「追加维度」，结果会变；② **历史合成文档**（meta 里没有 `dimensions`）重新生成时，
  回退锚点从 paper 5 行变 general 7 行——**同一份文档、同一个按钮、骨架变了**，
  属「快照语义／历史产物可复现」关心的那类变化。`test_prompt.py` 补了断言，注明它现在锚 general。

### 二、内容约束拆三块 + 读者意图

- `_SYNTHESIS_CONSTRAINTS` 拆为 `_SYNTHESIS_FACT_CONSTRAINTS`（5 条）+
  `_SYNTHESIS_STYLE_CONSTRAINTS`（通用文风 7 条）+
  `_SYNTHESIS_GENRE_CONSTRAINTS`（体裁增量 4 类 × 3 条，每类第三条是**停止条件**，
  多标签拼接必须去重）。
- **读者意图** `READER_INTENTS` 五选一（理解主题 / 比较材料 / 找争议 / 提炼行动建议 / 自定义），
  `purpose` 文本进 `<task>` 段的「读者目的」，`default_content_type` 只作高级区**预填建议**。
  ⚠️ 「理解主题 / 比较材料」刻意用 `None` **不预设体裁**——它们是读者的动作、不指示材料是什么，
  给它们猜体裁正是要消灭的错配（对话框静默预填 paper 对视频转写稿毫无意义）。
- 新建 `_SYNTHESIS_INSTRUCTION_PRIORITY`（合成专用），**必须保留 `output_language` 行**：
  原方案「output_language 对合成无意义」的理由不成立（`build_synthesis_prompt` 确实在发该节），
  且本轮新增的读者意图是**用户自由文本**、进 `user_request` 节——恰是全局那条
  「不可被用户备注覆盖」保护的场景。不动全局表的理由是单篇零回归。
- **覆盖说明换位置而非取消**：删 `<task>` 段强制元数据开场 + 「一句话概览」里的重复，
  改为在「## 综合评价」末尾以一句话说明输入覆盖情况。`:946` 是全仓唯一告诉模型
  「## 来源文档 ✓/⚠ 含义」的地方，整段删掉会让只看笔记 tab 的读者无法区分
  「6 篇里 3 篇是摘要输入」与全量输入。

### 三、删掉死参数 `user_notes`（「对比指引」）

`synthesize()` 签名里有、**函数体零引用**——用户在对话框填的对比指引对产物从来没有影响，
且链路看起来完整（前端 state → 请求体 → 路由器 → 函数签名）。位置由
`reader_intent` / `reader_intent_text` 承接，两者**经出处头 meta** 抵达生成 prompt：
生成是**第二次独立请求**，届时手里只有 rawText，凡是生成侧要用的参数都只能落在这里。

### 四、对话框重构

读者意图单选组（purpose 作小字说明）+ 「自定义」时的文本框；**维度预设与维度列表移入折叠的
「高级」区**；体裁多选由所选意图的 `defaultContentType` 预填、切换意图即重置。

### 五、顺带修复两处

- **`dump_meta` 加递归字符串清洗**：原先只清 `sourceTitles`，而 `dimensions` 同样进 meta——
  用户自定义一个叫 `A-->B` 的维度，导出 HTML 即因注释提前闭合而泄漏 JSON 尾巴。
  改为按**递归遍历**写，新增字段自动受保护。`readerIntentText` 是自由文本、天然带换行，
  而 meta 必须单行（`parse_meta` 只认第一行）。
- **入参清洗块从路由函数体移进 `runner`**：`normalize_reader_intent` / `normalize_source_mode`
  在非法值上**显式抛错**，写在外面就等于在 `StreamingResponse` 构造之前抛出 → FastAPI 转成
  HTTP 500，而前端拿 `fetch` + reader 解这条流，**500 只表现为「连接中断」**。
  ⚠️ `source_mode` 比意图早上线，该路径在 API 层**一直是 500**（直调测试覆盖不到这一层）——
  本轮一并修复并钉住。

### 验证

- 后端：`test_synthesis.py`（新增 §13 端到端：意图/体裁经 meta 抵达 prompt）、
  `test_synthesis_api.py`（新增 §8：路由透传 / 非法值走 SSE / 限长 / 零回归）、
  `test_prompt.py`（§7 十条）、`test_transcript_meta.py`（§8.5 三段）、`test_capabilities.py` 全绿。
  每条新判据都配了**对照实验**并确认真会判红（只在 map-reduce 写 → 红；意图文本死链 → 红；
  清洗写回函数体 → 500）。
- 前端：`tsc -b` 无错；vitest 23 文件 287 测试通过（原 274，+13）。
- ⏳ **端到端验收待跑**（需真实计费调用）：计划分**批次 A**（默认套 general 7 维）/ **批次 B**
  （同批材料手动选「论文综述」5 维，零代码改动）分离「提示词结构」与「默认维度数」两个自变量；
  指标 = 加粗 ≤21 / 引证显著下降且改段落级 / 后四段占比下降 / 每行平均字符数下降 /
  首句直接进入内容 / 表格首列 0 处加粗。**负向对照必做**：喂 1–2 篇短材料，**对偶断言**
  「未提及」仍出现、且没有引入材料外事实。

### 明确不做

10 项自检（显著增加输出 token 与延迟，先拿前两阶段实测数据）、改五段骨架（属「路线 2」）、
`_POLISH_TASK` 编号重复等三条（影响单篇产物，混进来会模糊「零回归」的验证边界）。

**影响范围**：仅 `sourceType="composite"` 的合成产物与其生成 prompt。单篇 notes / polished /
其他管道的提示词零改动（`_INSTRUCTION_PRIORITY` 全局表未动）。

---

## 2026-10-02 — 前端按钮精简（删重复入口 + 主题切换器收窄）

回应「前端有一些按钮可能多余、重复」。全仓扫描 **192 个 `<button>` / 43 个文件**，
范围收在**主界面常驻可见的入口**上——设置页内部的按钮（`ProviderSettings` 23 个等）各有独立职责、
只在专门页面出现，不是精简对象。

### 一、判据：先分清「刻意的双入口」与「真正的重复实现」

项目里**明文记载**的刻意双入口一律不动，这是本轮最重要的克制：

| 保留项 | 依据 |
|---|---|
| `ExportMenu`（工具条，快捷单文件）vs 顶栏「导出」（`ExportPanel`，批量多文档） | CLAUDE.md「两条路径设计」 |
| 「文件夹范围」（过滤）vs「排列：按文件夹」（归类） | CLAUDE.md 明文「刻意保留两个入口」 |
| 顶栏 `SearchBar` vs `Ctrl+K` | 已是「按钮 → 唯一实现」的正确形态（曾**是**两份实现，已修） |
| 面板内的「收起面板」按钮 | 省的是鼠标移动，是合理便利；与顶栏开关撞车但不等价 |

**只删「同屏、同动作、同一个 store action」的重复**。

### 二、删：预览工具条的 AI 按钮

`PreviewArea.tsx` 工具条右侧的纯图标 `MessageSquare` 按钮与顶栏「AI」按钮**同屏同时可见**，
`toggleAI` 全仓的 UI 入口恰是这两个（+ 浮窗自己的关闭 X）。删工具条那个，顶栏「AI」成为唯一入口
（`Ctrl+I` 不变）。同步移除 `MessageSquare` import，并把工具条顶部注释里列的入口清单改准。

### 三、改：`ThemeToggle` 收成三格图标分段控件

原实现是三个**带文字**的按钮（「浅色」「深色」「跟随系统」），在顶栏占近三倍宽度。
改为 **icon-only 分段控件**（`w-7 h-7`，仍是三格、仍是一键直达），文字降为 `title`，
并补 `aria-label` / `aria-pressed`。

**刻意不用「单按钮循环切换」**——那要点两次才能从「浅色」到「跟随系统」；
三格形态保住了直接跳转，同时拿到省宽度的收益。

### 四、顺带发现（本轮**未改**）

`markdown-components.tsx` 的 `ChartCodeBlock`（`:52`）与 `CodeBlock`（`:119`）各有一份
**逐字相同的复制逻辑**（`writeText(...).then(setCopied, addToast)` + `setTimeout(2000)`），
连 `copied` state 都各声明一份。**用户可见层面这不是重复**（每个代码块本就该有自己的复制按钮），
只是实现可抽 `useCopy()` 收敛——列入后续可选打磨。

### 影响范围

- 前端仅两文件：`components/preview/PreviewArea.tsx`、`components/ui/ThemeToggle.tsx`
- **零后端改动**；`toggleAI` / 主题 store 逻辑均未动，纯入口与呈现层
- 验证：`npx tsc --noEmit` 0 错；`npm test` 278 全过；`npm run lint` 无新增告警
- ⚠️ 删按钮时在 JSX 注释块内误写了 `*/` 导致**注释提前闭合**（后面的注释文本变成裸文本）——
  被 `npm test` 的解析阶段立刻抓到。往 `{/* ... */}` 块里追加内容时留意结尾。

---

## 2026-10-02 — 模型预设核对脚本（开发期变更哨兵）

回应「更新各项模型的信息」与「需要一个稳定的渠道来查询这些东西」。考察参考项目 **pi** 的做法后，
**推翻了原本「照搬其生成式模型目录」的方案**——不是因为它写得不好，而是因为**数据源不可靠**。

### 一、pi 的做法与它成立的前提

pi 的模型目录是**生成的**：`scripts/generate-models.ts`（2602 行）从 `https://models.dev/api.json` 拉取，
产出 `src/providers/{provider}.models.ts`（结构）+ `src/providers/data/{provider}.json`（值）+ `.manifest.json`，
并用 sha256 挂进 `build:offline` 做构建期校验。它还有一条明确的克制原则（注释原文）：
*"Keep this to narrow corrections over models.dev metadata instead of snapshotting Copilot's catalog"*——
**只做窄修正，不整份快照**。

这套东西成立的前提是：**数据源足够权威，值得让它生成进源码**。

### 二、实测：models.dev 不满足该前提（三类问题，逐条比对 48 条预设）

| 问题 | 实测证据 |
|---|---|
| **与官方页冲突** | `alibaba/qwen-turbo` 标 **1000000**，而我们 config.py 旁的人工注释写着「官方 `/qwen-turbo`：131072（2026-10-02 复核无误）」 |
| **内部自相矛盾** | anthropic 的 `claude-haiku-4-5` 标 200000，同家 `claude-sonnet-4-6` / `claude-opus-4-8` 标 **1000000**——而 Claude 的 1M 需 beta header，默认仍是 200K，**自家数据都对不上** |
| **覆盖率仅 54%** | 48 条预设中只有 26 条有记录，**缺口正落在最新与最冷门的模型上**——恰是最需要核对的那批 |
| 口径混用 | 8 条「不一致」里 4 条只是 MB/MiB 差（1000000 vs 1048576） |

⇒ **结论：models.dev 是社区聚合数据，不能当生成源。** 若照搬 pi 的做法，会把错误数据写进源码，
并且**冲掉 `config.py` 里每条预设旁那段人工查证注释**（官方页 URL + 抓取日期 + 陷阱说明，如「取【上下文长度】不是【最大输出长度】」「OCR 两条是**高估**必须改小」）——
那是本项目两年积累的**资产**，生成式覆写是净损失。

### 三、改为「变更哨兵」：产出是**问题**，不是**答案**

新建 `scripts/check-models.py`（开发期脚本，不进打包、不被后端 import）。报告四类：

| 类 | 含义 |
|---|---|
| **A** | 数据源有、预设没有（多数是我们**刻意不预设**的老模型；按发布日期降序，只看头部即可） |
| **B** | 预设里有、数据源查无（可能已下架；聚合网关 `opencode` 无上游目录，**跳过**） |
| **C** | 双方都有但 `context_window` 不同（⚠️ **不代表预设错**——历史上 8 条里 4 条只是口径差） |
| **D** | 预设**完全没有**的字段：`max_tokens`（我们硬编码 65536）/ 价格（预设一律不填）——新增能力的候选，非核对项 |

**唯一用途是决定「打开哪一页」**，一切仍以官方页为准。脚本 docstring 把这段定位写在最前面，
避免下一位读者把它当权威结论用。

### 四、三处刻意的设计

1. **墓碑必须同源**：A 类会消费 config.py 已有的 `RETIRED_MODEL_IDS`——否则每次都会把「我们刻意移除的旧模型」当成新模型报出来（实测 `deepseek-v4-flash` 两条噪声）。这是「**成对逻辑必须同源**」（模式 #54）：我们已有那张表，脚本不该另起一套判断。
2. **能力位不参与核对**：models.dev 的 `modalities` 里 anthropic 全标了 `pdf`，但那说的是「模型能读 PDF」，与我们走 OpenAI 兼容端点认不认 `file` 块是**两回事**（模式 #51）。该位仍只能逐家查官方文档后手标（项目铁律：「能力位只标查证过的」）。
3. **自身完整性守卫**：`PROVIDER_MAP` 漏映射 = **静默少核对一整个厂商**（报告照常输出、只是从不提及它，看起来像「那家没有变更」）。脚本在出报告前把这些喊到 stderr，测试另有守卫钉住。

### 影响范围

- **运行期零影响**：脚本只在开发期手动跑，不进 PyInstaller、不被后端 import；产出的是一次性缓存 JSON，**用完已删**（约 26MB，需要时随时重下）
- **测试**：新增 `backend/tests/test_check_models.py`（14 用例，含「守卫真会红」的对照实验）
- **文档**：`CLAUDE.md` 常用命令区新增「模型预设核对」小节
- **本轮范围**：只做数据层。**不碰计费**——不采集真实 usage、不改 SSE 协议、不动前端费用显示（那里目前是字符估算，系统性高估）

---

## 2026-10-02 — Google 链路打通 + base_url 归一化三副本收敛

承接上一轮「把剩下的做完」中的第三项。诊断出的是**两处独立缺陷**，修完顺带发现**同一个判据在本仓有第三份副本**。

### 一、Google 的 base_url 是**原生 API 形态**，整条 LLM 链路必然不通

`PROVIDER_DEFAULTS["google"]` 原为 `https://generativelanguage.googleapis.com/v1beta`——这是 **原生 API** 的根，而本项目全链走 OpenAI SDK。SDK 会据此发 `POST .../v1beta/chat/completions`，**原生端点不存在该路径** → 404。也就是说：**该 provider 自加入起，每一个请求都打到不存在的路径上**，与上一轮查出的 opencode 是同一类（自配 day one 起就没通过）。

改为**官方 OpenAI 兼容层** `https://generativelanguage.googleapis.com/v1beta/openai`（无尾斜杠）。取三条独立来源交叉验证，而非单一页面转述：

| 来源 | 内容 |
|---|---|
| hex.pm `ex_llm` 文档 | 逐字转录 Google 官方页的 `base_url="https://generativelanguage.googleapis.com/v1beta/openai/"` |
| `docs.api7.ai` 的 ai-gateway 配置 | `"api_base": "https://generativelanguage.googleapis.com/v1beta/openai"`（无尾斜杠） |
| `googleapis/python-genai#2868` | 官方 SDK 仓库称该路径为 "hosted OpenAI-compatibility layer" |

⚠️ **顺带订正一条错误的历史注释**：代码里原写「Google 鉴权走 `?key=` 查询参数而非 `Authorization: Bearer`」——那是**原生 API** 的性质。兼容层接受标准 Bearer（官方 REST 示例 + Azure 文档均如此），**这正是选兼容层的理由**：不必为鉴权做任何按 provider 的特判分支。

### 二、拼接启发式「以 /v1 结尾」对两家都不成立

两处（`test_connection` / `refresh_models`）各写一份的

```python
if not base_url.endswith("/v1"):
    base_url += "/v1"
```

对多数 provider 成立，但**不是普适规则**：

- **google**：`.../v1beta/openai` 不以 `/v1` 结尾 → 拼成 `.../v1beta/openai/v1/models` → 404（**路径层再错一次**）
- **zhipu**：`.../api/paas/v4` → 拼成 `.../v4/v1/models`，官方是 `.../v4/models`。⚠️ 智谱**两条路径都返回 401**（路由宽松、不报 404）→ **状态码区分不了对错**，这类问题只能靠官方文档裁决

判据改为**「路径是否已含版本段」**（正则 `/v\d+[a-z]*(?:/|$)`，刻意不加 `re.I`——URL 路径大小写敏感，加 I 只会让 `/VIP/v1` 误命中）。**逐个验算过全部内置 provider，除 google / zhipu 两处（均为修正）外行为逐值不变**：`api.deepseek.com`、`api.anthropic.com` 仍拼 `/v1`；`api.openai.com/v1`、`compatible-mode/v1`、`api.moonshot.cn/v1`、`zen/go/v1` 仍不拼。

### 三、同一个判据在仓库里有**三份**副本，已收敛到唯一入口

`grep` 时发现第三处在 `routers/config.py` 的 `POST /llm/models`（此前只当它是「两处同源」）。正是项目铁律「**成对逻辑必须同源**」的活案例——改一处忘另一处不会报错，只会让不同入口对同一 provider 得出不同结论（「测试连接」说通、「刷新模型」拉不到）。

抽出 **`app/core/url_utils.py::api_root()`**，三处共用。该端点目前**前端零调用**（`api/settings.ts` 的 `detectModels` 定义了但无调用方），仍一并收敛——**死代码若被复活，不该带着一份不一致的判据回来**。

### 四、对既有用户是否生效（已核实）

`_get_providers` 只在「config 里没有该 provider 条目」时补默认值。检查了本机 `~/.limo/config.yaml`：`providers` 段只有 `dashscope` / `deepseek` / `bcut` / `doc-local`，**没有 google 条目**（虽然 `llm_models` 里有 gemini 系模型）→ 走默认补全 → **本次修复对其直接生效，不需要迁移**。

> 📌 记录一个未处理的缺口：**内置 provider 默认 base_url 变更，对「已保存过该 provider 的用户」无效**（其 config 里存着旧值）。opencode 上一轮校正 base_url 时同样没有迁移机制。`api_root` 只修「拼接」，不修「存量值」。是否加「仅当 base_url 精确等于某个历史默认值时才升级」的迁移，留待有第二个用户时再定。

### 五、已知偏差（记录，不改）

`llm/service.py::_warn_key_mismatch` 用 `"openai" in url` 判「key 是不是错配到 OpenAI」——google 的新 URL **含 "openai"**，于是「拿 DashScope Key 配 Google」会报「被用于 **OpenAI**」。触发条件是**真配错了**（warning 的实质正确），只是厂商名说错；改判据为 `api.openai.com` 会连带漏掉用户自配的含 "openai" 的中转地址，得不偿失。已就地留注释。

### 影响范围

- **后端**：新增 `app/core/url_utils.py`；改 `routers/model.py`（base_url + 两处拼接 + 注释）、`routers/config.py`（第三处拼接 + import）、`llm/service.py`（注释）
- **前端**：零改动
- **测试**：新增 `tests/test_url_utils.py`（7 组，含**两个对照实验**：把判据改回 `endswith("/v1")` → 3 条判红；把 google base_url 改回原生形态 → 1 条判红；另有 AST 源码守卫防第四份副本长出来 + 守卫自证「真代码命中、注释与字符串不命中」）。后端 23 个测试套件全绿
- **打包**：新增模块走静态 import，PyInstaller 自动收集，spec 无需改

---

## 2026-10-02 — 多篇合成三补：超窗分层 / 维度可控 / 原料可选

承 2026-10-01 的落地，用户追问三个具体问题：「窗口不够怎么办」「对比的是哪些东西、是不是只针对论文」「对比的是转入产物还是已生成的产物」。三个问题各自揭出一处**设计说了但代码没做**、或**只做了最表层**的地方。

### 一、超窗不是一个问题，是四个

此前被当成一个来回答，所以怎么答都觉得不完整。拆开看：**①** 总量超窗、单篇不超 → 逐篇结构化抽取（**已解决，且这不是妥协**——系统综述本就按维度对齐抽取，不是读全文）；**②** **单篇自身超窗** → 标注了「⚠ 已截断」，但约 78% 内容永久丢失，而用户只看到一个与「超时」「模型抽风」共用的标签；**③** 一篇病态长文拖垮整次合成（勘察中才发现）；**④** 用户点合成前什么都不知道。

②③ 互为因果：分块增加调用次数 → 更容易触发整体超时，**必须一起修**（只做分块会让 ③ 恶化）。

#### 单篇超窗 → 篇内分块归并

**关键洞察：分块不增加该篇的配额，而是让它在配额内覆盖全文。**

- 现状：从**头部**截断 → 后 78% 根本没进过模型
- 分块：K 块各抽一次 → 归并成 quota 大小 → **信息来自全文**，每块都贡献

价值不在「塞进更多」，而在「同样的配额，取材从『只有开头』变成『全文』」。取 `q = ceil(quota / K)` 让块产出恰好够归并填满 quota、不超产。

**三条预算约束必须同时成立**（缺一则静默超窗）：单块调用装得下 → 归并调用装得下 → 归并后仍 ≤ quota。

⚠️ **每块必须推一条 `stage`**——这不是进度条的打磨，是**不改会让正常路径被判挂死**：SSE 循环以「上次事件距今 > `SYNTH_QUIET_TIMEOUT`」判挂死，而该阈值成立的前提正是「每次 LLM 调用前都有 stage」。现状「一篇 = 一次调用」≤ 180s < 900s 所以安全；分块后一篇 = K 次调用串联，只在篇级推 stage 则**篇内静默 = Σ 各块耗时**，K ≥ 6 就冲破 900 —— 用户在**正常**路径上收到「合成长时间无响应」。

复用既有 `split_into_chunks`（`text_utils.py`，全仓唯一的句边界定义）+ EPUB 的 `_summarize_book`（`epub_pipeline.py`，同一模式已在跑通）；overlap 由 200 字符提到约 5%（数万 token 的块上 200 只覆盖一句话）。

#### 一篇病态长文拖垮整次 → 软 deadline

`asyncio.timeout(900)` 包住整个 `synthesize`，而**落库在 Map 之后** —— 超时 = 前面所有篇的抽取成果全部作废（可能已跑十几分钟、烧掉真实 token）。改为把 `deadline` 透传给 `run_map_reduce`，到点则剩余篇逐个硬截断 + 标 `truncated_timeout`，让流程正常走到落库：用户拿到「M 篇完整 + K 篇因超时截断」的**可用产物**，而不是空手。

⚠️ 顺带订正一处「两个常量同值」的缺陷：`SYNTH_QUIET_TIMEOUT` 与 `SYNTH_TOTAL_TIMEOUT` **都是 900** → 「挂死」与「整体超时」同刻到期，用户收到哪条错误**由竞态决定**；且原注释写着「30s 无事件判挂死」而值是 900（30s 若真采用会与 `EXTRACT_TIMEOUT=180` 冲突——**正常调用就会被判挂死**）。静默阈值改为 `300`，并把三者关系写成**断言**而非注释：

```python
assert max(EXTRACT_TIMEOUT, CHUNK_EXTRACT_TIMEOUT) < SYNTH_QUIET_TIMEOUT < SYNTH_TOTAL_TIMEOUT
```

两个常量一并迁进 `synthesis.py`、router 改为 import（避免两处各写一个 900）。

#### 降级原因不可区分 → 状态 3 态扩到 8 态

`_STATUS_LABEL` 的 `truncated` 是**垃圾桶**——超窗、超时、模型抽风全标它，但用户能采取的行动完全不同（换大窗口模型 / 重试 / 检查 Key）。

新增 `chunked` / `chunked_partial` / `truncated_over_window` / `truncated_timeout` / `truncated_llm_error`。**零 schema 迁移**（status 只进人类可读标注、无机器读取者，已全仓确认）；加**基础状态回退** `get(status) or get(status.split("_", 1)[0])`，历史 `truncated` 与将来未知状态都不崩、不丢行。

> **措辞纪律（本轮新立）**：凡描述「降级行为」的措辞，**先对一眼同一份文档里讲透明度的那一节**。初稿把这里写成「**静默**硬截断」，而透明度那节的整个论点恰恰是「它**不**静默、只是原因不可分辨」——同一份文档内自相矛盾。与 2026-10-01 修掉的「兜底失效」是同一类错误：措辞偏差不会以报错出现，只会让实现者按错的模型去补。

#### 用户点合成前什么都不知道 → 预览端点

`POST /api/synthesis/preview`：**零 LLM 调用、零副作用**，返回每篇 token + 预计处理方式 + 「让全部 N 篇原样装下所需的窗口」。

⚠️ **判据必须同源**：新增纯函数 `_classify()`，**预览与执行调同一个它**——预览自写一套就会出现「预览说会压缩这篇、实际压缩了另一篇」（项目铁律「成对逻辑必须同源」）。

⚠️ **`stuff_window` 语义**（初稿叫 `required_window`，**改名**）：它读起来像「做这次合成所需的最小窗口」，但那不是它的意思——**总量超了照样能做，只是走压缩**。准确语义是「让全部 N 篇**原样装下**（即走 Stuff 路径）所需的窗口」。用户看到 banner 时不该以为「窗口不够就不能合成」。

⚠️ **换模型 banner 的触发集必须收窄**：初稿写成「存在 `chunked` / `over_window` / `reject` 就出 banner」，这会让它在**最常见的情形下每次都弹**——`chunked` 的定义正是「总量超窗、单篇都不超」，即「篇数多」这个**设计认可的终态**（用户无需行动）。若它也触发「换个大窗口模型吧」，就等于在一条设计说「不用换」的路径上不断劝人换。**判据**：banner 的语义是「你该做个动作」，只有用户**真的该动**的情形才配出现。

⚠️ **token 只能由后端算**：`docs` 是白名单元数据、**不含正文**，前端根本没有 rawText 可估——这正是需要预览端点、而非前端自算的原因。

### 二、维度就是产物骨架（但 Stuff 路径上它一次都没生效）

`_SYNTHESIS_OUTPUT` 的「## 对比总表」是**行 = 维度 × 列 = 各篇**的矩阵。改维度 = 改产物骨架，比 `<user_request>` 节里的任何一句话都强（后者优先级**低于** `output_format`）。

**查出的偏差**：设计写「Stuff 路径下模板维度直接进 prompt」，实现里**没有任何维度注入**——`build_synthesis_prompt` 只把 rawText 塞进 `source_content`。而 Stuff 是**多数情况**（设计自己说的）。后果很具体：3 篇都短的产品评测走 Stuff → 唯一的补救机制（维度发现）**根本不跑** → 用户花笔墨设计的维度机制在最常见路径上一次都没生效。

| 路径 | 修复前的维度来源 |
|---|---|
| Map-Reduce | 硬编码论文维度 + 模型追加 ≤3（只在超窗时生效） |
| **Stuff**（多数） | **零** —— 模型自己定 |

**修**：`DIMENSION_PRESETS` 四套（论文 / 技术资料 / 产品评测 / 媒体内容）→ capabilities 下发（前端不硬编码，同「引擎前端后端驱动」原则）→ 用户给定则**跳过维度发现调用**（省一次 LLM 调用）→ 两条路都注入 prompt。

⚠️ **维度必须落在出处头 meta 里**：合成是两步走，**生成是第二次独立请求**，届时手里只有 rawText。落 index.json 会在重建后丢失（项目已有教训），落**可见正文**会与原始材料混淆、用户编辑时易误删——meta 是「系统管理、不承诺可编辑」区，正好。

（与 `sourceTitles` 的处理**刻意不同**：后者在 meta 与「## 来源文档」列表**两处都有**，因为它需要人眼可读。）

### 三、对比原料可选（原文 / 已生成的笔记）

默认原文；切到「笔记」时读最新版（两版制下 `notes.md`，**不用** `.prev.md`）。

**「只能选一项」是刻意的设计，不是限制**——混合（部分篇给原文、部分篇给笔记）有三个独立缺陷：① **等比配额的前提被打破**（笔记的 token 密度与原文差一个数量级，同配额下给笔记的篇覆盖全、给原文的篇只剩开头）；② **产物不可解释**（`✓ 完整输入` 会让人以为是原文，其实可能是笔记）；③ **降级语义冲突**（笔记本身已是压缩品，再叠 `summarized` 会让刚做好的状态细分失效）。

**整批同源 ⇒ 三个问题一并消失。**

⚠️ **前端拿不到「这篇有没有笔记」**：`DocEntry` 无任何产物字段，而产物在 index **顶层**的 `markdowns` 映射里、不在 transcript 条目上。**解法是不新增字段、不新增端点**——预览端点本来就在逐篇读文档，天然知道，顺带返回 `hasNotes` / `notesTokens` 即可。

⚠️ **切模式要重调预览，不能前端本地重算**：notes 的 token 数 ≠ rawText 的 token 数，`plan` 必须重算；本地重算 = 把 `plan_synthesis` 复刻一遍 = 「成对逻辑必须同源」明令禁止的形态。重调是一次无 LLM 调用的请求，代价可忽略。

⚠️ **后端执行时必须再校验一次**：预览与执行之间隔着用户操作（可能删了文档、也可能压根没看预览就点了合成）。前端提示是 UX，**后端必须自己判定**：无笔记的篇排除、剩余 < 2 篇 → 业务错误走 SSE error 事件（**不是 500**）。

`sourceMode` 落 meta 是**为产物自解释**（三个月后仍看得出基于原文还是笔记，直接影响对信息完整度的判断），**不是为生成**——生成阶段读的就是 rawText，不需要知道原料是什么。

### ★ 落地时抓到的一个真实 bug：`excluded` 是标题，消费方却按 id 用

前端拿 `done.excluded` 从选中集里做差集，决定 `sourceDocIds` 写哪几篇；而后端填的是**标题**——**永远匹配不上**，于是被排除的篇仍被写进来源清单，「N 合 1」徽章指向一份产物根本没读过的文档。预览侧的 `excludedSet.has(id)` 同病。

**两边都错、两边看起来都对**，且失败完全静默（不报错、不抛异常，只是差集恒为空）。

统一语义为 **id**（标题经 `sanitize_reference_name` 清洗、且重名合法，做身份判定必然错——同设计 §4.2.2「标题是展示字段，不是身份凭据」）；`not_enough_notes` 的 detail 在调用点转回标题（那是给人读的）。⚠️ **这个 bug 由测试抓到，且「只断言有 2 条」的写法抓不到**——必须断值本身。

### 影响范围

| 文件 | 改动 |
|---|---|
| `backend/app/generation/synthesis.py` | 分块 / deadline / 维度预设与注入 / 原料模式 / 预览门面 / `_classify` / 常量迁移与断言 |
| `backend/app/core/transcript_meta.py` | 8 态文案 + 基础状态回退；`dimensions` / `sourceMode` 入 meta |
| `backend/app/generation/prompt.py` | `build_synthesis_prompt` 注入实际维度名；两个调用点 `parse_meta` 取值、缺失回退默认（历史文档不崩） |
| `backend/app/routers/synthesis.py` | `POST /api/synthesis/preview`（无信号量、无 SSE）；`dimensions` / `source_mode` 入参 + 清洗与枚举校验 |
| `backend/app/routers/capabilities.py` | `dimensionPresets`（单一真相源，前端不硬编码） |
| `backend/app/core/errors.py` | `too_many_calls` / `not_enough_notes` 等 |
| `frontend/src/components/doc-library/DocLibrary.tsx` | 对话框：逐篇 token + 处理方式 / 窗口 banner / 原料单选组 / 维度预设下拉 + 列表 |
| `frontend/src/api/synthesis.ts`、`api/core.ts` | `previewSynthesis` + 类型 |

后端测试在 `test_synthesis.py` 增分块 / deadline / 维度 / 原料四组，另改 `test_transcript_meta.py`（维度与 sourceMode 往返）、`test_prompt.py`（维度注入）、`test_budget.py`、`test_capabilities.py`。

**测试基建也修了一处**：文档库测试的 fetch stub 原本对所有 URL 返回 SSE，而对话框现在会**先打一次 preview（JSON）**——不按 URL 分派的话，预览拿到 SSE body → `canRun` 恒为 false → 测试变成在测一个**永远点不动**的按钮。

**每条判据仍配「看起来对、其实错」的对照实现**——例如「每块都有 stage」那条必须**真造出 K ≥ 6**（K=2 的实现碰不到阈值，照样通过）。

---

## 2026-10-02 — 沉浸阅读（去噪容器 + 阅读版式）

用户的问题原话：「沉浸式阅读应该怎么做比较好，假如去网上看看各种开源项目最好拼的是哪一种，各种方案优缺点又如何，怎么适配到我们这个里面」。

调研结论是**三类不拼**，**抄规范不抄代码**——依据是 W3C《中文排版需求》(clreq) 与 GB/T 15834-2011，不是某个库的实现：

| 不拼的 | 代表 | 为什么 |
|---|---|---|
| Reader Mode 的**抽取层** | Readability.js / Trafilatura / Firefox 阅读视图 | 这些项目的主要工作量在「从混乱 HTML 里找正文」，而 LiMo 的正文**导入时已被适配器抽成结构化 Markdown**——这一层免费跳过。拼进来只剩它最不值钱的部分 |
| 两个 **focus mode** 变体 | Obsidian Typewriter Mode / iA Writer 段落聚焦 | 业界的 focus mode 是三个东西：Zen Mode（藏 UI）/ Typewriter Scrolling（当前行钉中央）/ Paragraph Focus（当前段高亮）。**后两者是写作辅助**，依赖「光标定义的当前行/当前段」——LiMo 是**阅读**场景，没有光标，拼进来会得到一个**没有指针可跟随的高亮** |
| **heti** 整体引入 | heti（赫蹏） | 优秀的全量中文排版库，但服务于「一篇连续的中文散文」，而 LiMo 产物是结构化技术笔记（标题/列表/表格/代码块/公式/图表），90% 与它无关；且它的 `autoSpacing()` 用 JS 遍历 DOM 插空格，与 React 重渲染打架 |

分三层落地：**R0** 修既有 bug → **R1** 去噪容器 → **R2** 版式（「纸感」的实质）。

### R0 — `--texture-opacity` 冷启动（既有 bug）

六轴里唯独纹理**强度**不在 store 里，应用逻辑留在设置页组件的滑块 `onChange` 里，启动链从未调用它 → 冷启动时 `data-texture` 恢复了、强度却回落到 CSS 兜底 `0.5`。

**零报错、零视觉异常**（0.5 是个完全合理的值），只有用户明确设过非 50 的强度并重启才看得出来。`store/index.ts` 启动链补 `applyIntensity(getSavedIntensity())`。

> ⚠️ 这只是补了一行，**根因未除**（它仍是唯一不在 store 的主题轴）。并入 store 列为 R3。

### R1 — 沉浸容器（3 个文件，零后端改动）

`immersive` 状态 + 藏两侧面板/顶栏/预览工具条 + `Ctrl+\` 开关 + `Esc` 退出。

| 决策 | 选择 | 理由 |
|---|---|---|
| 快捷键 | **`Ctrl+\`** | ❌ `Ctrl+Shift+F` 双重失效：`e.key` 在 Shift 下是**大写 `'F'`**（现有 case 全是小写，写 `'f'` **永不触发**）；且 Windows 上 `Ctrl+Shift` 是**默认的输入法切换前缀**，对中文用户会被输入法吞掉。`Ctrl+\` 与 LobeChat Zen Mode 同键 |
| Esc 监听 | **独立 `useEffect` + 捕获阶段** | 它进不去 `handleAppKeyDown` —— 那里开头的 `if (!(e.metaKey \|\| e.ctrlKey)) return` 会把裸 Esc 直接吞掉。沉浸隐藏了**所有**可见出口，快捷键万一失效用户就被困住，故 Esc 是硬需求 |
| 进入守卫 | **复用 Ctrl+G 的 `processingTasks` 判据** | `hideSlots` 是**真卸载**（SlotLayout 命中即 `return null`），不是 CSS 隐藏 —— 正在跑的任务会变僵尸。项目对同一件事早已做过选择，沉浸绕开它就是绕过保护 |
| 退出方向 | **不设限** | 否则有任务时用户被永远困在沉浸里 —— 比僵尸任务更糟（看得见却出不去） |
| TOC | **刻意保留**（偏离计划） | 计划原文写「工具条与 TOC 在沉浸下不渲染」。TOC 是**阅读导航**（长文跳章），与 tab/导出那些操作入口性质不同；且它本就能折叠成 28px 竖条，空间成本由用户自己控制。隐藏它是功能损失而非去噪 |
| FloatingAI / ExportPanel | 隐藏 | 它们是操作入口 |
| Toast / 后端 down 横幅 | **保留** | 瞬时反馈与关键系统状态，藏了会让操作变成无声 |

### R2 — 阅读版式（「纸感」的实质）

三个旋钮：**行高** / **每行字数** / **段落区隔**。

⚠️ **默认值 = 改造前的现状**（行高 `1.5` = vendor `github-markdown-css` 现值；段落式 `web` = `text-indent:0 + margin-bottom:10px`；每行字数 `null` = 沿用比例制）。所以「用户不调」时渲染**逐值不变** —— 零回归是被 `test/readingPrefs.test.ts` 钉住的，不是靠自觉。

**沉浸 ≠ 纸感**：沉浸只负责去噪，纸感来自用户主动调参，两者刻意解耦。

- **段落区隔是二选一，不是两个旋钮**：clreq 明写「首行缩进」与「加大段距」是两套并行传统、**不建议混用**（又缩排又加大段距，两种区隔信号叠加不加乘，只显累赘）。做成两个自由旋钮就能拼出被禁止的组合
- **版宽在 JS 里算，不做 CSS 变量**：`contentMaxW` 应用在带内联 `fontSize: fontSizeScale[…]em` 的 `.preview-content` 上，写成 `34em` 会随宽度档漂移（版宽跟着「紧凑/宽屏」变，**方向反了**）；且 `.markdown-body` 里 vendor 硬写 `font-size:16px`，是**另一个** em 基准。改用固定 16px 换算（`computeContentMaxW` 纯函数，可单测）
- **不受 `THEME_PRESETS` 影响**：版式关乎阅读生理，不该被「换一个主题包」连带重置

⚠️ **前置改动（不做会让整个 R2 静默半失效）**：`MarkdownParagraph` 有两个分支 —— `components.a` / `.code` / `.img` **都是函数**，所以**段落里只要有链接、行内代码或内联图片就渲染成 `<div>` 而不是 `<p>`**（技术笔记里这是绝大多数段落）。排版钩子必须挂在一个**两个分支都带**的类名（`md-para`）上；只给 `<p>` 挂的话会出现「纯文本段落缩进了、带行内代码的段落没缩进」的随机抖动，**看起来完全像渲染正常**。`>` 子选择器天然排除 `li > .md-para`、表格与引用内的段落，不需要 `:not()` 体操。

⚠️ **`.md-para` 还顺带解决了一件事**：Prism 用的是 `PreTag="div"`，项目里代码块容器**根本不是 `<pre>`** —— 任何「排除 pre」的规则都会漏掉真实代码块。代码块靠「行高只下放到 `.md-para` / `li`、不下放到 `.markdown-body`」来保护。

**未加 `text-autospace`**（计划 §16.2 的「块 C」）：MDN 记其 initial value 即 `normal`，很可能是 no-op；本机无法证实 WebView2 的实际行为，故**不加** —— 不加它不影响版式，加了却可能把既有行为当新功能交付。

### 影响范围

| 文件 | 改动 |
|---|---|
| `frontend/src/lib/reading-prefs.ts` | **新建**：三件套（getSaved / apply / save）+ `computeContentMaxW` 纯函数 + `CONTENT_WIDTH_RATIO`（与 PreviewArea 的 `WIDTH_RATIO` 单源） |
| `frontend/src/store/themeSlice.ts` | `reading` 字段 + `setReading(patch)`（patch 形式，三个值合成一个 JSON key 落盘） |
| `frontend/src/store/index.ts` | 启动链补 `applyIntensity`（R0）与 `applyReading`（R2） |
| `frontend/src/store/uiSlice.ts` | `immersive` + `toggleImmersive` / `setImmersive`（进入过守卫、退出不设限） |
| `frontend/src/lib/markdown-components.tsx` | `MarkdownParagraph` 两个分支都挂 `md-para`（保留 `props.className`） |
| `frontend/src/index.css` | `:root` 的 `--read-line-height: 1.5` + `.md-para` 的段落式/行高规则 |
| `frontend/src/App.tsx` | `Ctrl+\` / Esc 监听 / 顶栏与浮窗受 `immersive` 控制 / `hideSlots` |
| `frontend/src/components/preview/PreviewArea.tsx` | 工具条隐藏 + 退出按钮 + 版宽改走 `computeContentMaxW` |
| `frontend/src/components/settings/SettingsPage.tsx` | 外观节新增独立的「阅读版式」卡片（**不并入「推荐搭配」**） |

### 已知代价（记录在案）

- **进出沉浸会重新挂载 GenPanel**（`hideSlots` 是真卸载）→ 参考文件句柄（`refFile`，session-only 不持久化）与**导入中的文件/进度**会丢。守卫拦不住这个（用户没在跑任务也能进沉浸）。进出是低频操作，暂接受；若实测代价明显，R1.5 改「CSS 隐藏而非卸载」
- **每次进出 2 个 API 调用**（GenPanel 挂载打 `/api/models` + `/api/providers`，无缓存）
- **导出不跟随沉浸版式**（用户已决策「先不跟随」）→ 违反项目铁律「预览所见 = 导出所得」。导出件维持 `HTML_THEMES["limo"]` 的 `max-width:860px; line-height:1.75`。这是**已知偏差**，避开的是项目记录的「打印 CSS 三处落点不可合并」历史坑

### 验证

对照实验全部实测判红后恢复（每条判据都配了一个「看起来对、其实错」的实现）：

| 判据 | 对照实现 | 结果 |
|---|---|---|
| 默认 = 现状（零回归） | 默认行高写成 1.7 | 红 |
| `measurePx === null` 分支 | 分支漏掉返回 0 | 红（2 条） |
| `measure` 的 `> 0` 判据 | 改成 `!= null`（0 被当有效值 → 版宽 0px） | 红 |
| 单项坏字段只退该项 | 整包丢弃 | 红（2 条） |
| `md-para` 覆盖富段落 | 只给 `<p>` 分支挂类 | 红 |
| `text-indent` 不泄漏 | 挪到 `.markdown-body` | 红 |
| 启动链应用版式 | 删掉 `applyReading` 那一行 | 红（2 条） |
| 沉浸进入守卫 | 拿掉守卫 | 红 |

前端 22 文件 / 265 测试全绿，`tsc --noEmit` 无输出，lint 无新增告警。

---

## 2026-10-01 — 多篇文档合成（跨文档对比综述）

用户需求（原话）：「为什么要多个转入（转录）文章统一产出——根据我老师的实际需要，他有时候一次需要看多篇 PDF 论文。」即：**一次性把 N 篇论文交给模型读完，产出一份跨论文的对比 / 综述**，而不是自己逐篇读、逐篇做笔记。

设计经三轮外部评审（V1 → V2.3），本节记录**落地了什么**，以及落地时**推翻了设计文档的哪几处说法**——后者比前者更值得留痕。

### 三条生命线

| # | 主张 | 落地后它换来了什么 |
|---|---|---|
| 1 | **合成产物 = 一个普通文档**（`sourceType="composite"`） | 文档库 / FTS 搜索 / 标签 / 文件夹 / 批量操作 / 导出 / 对话 `@` 引用 / 两版制版本管理 **全部免费获得**——每条链路都没有新增类型分支 |
| 2 | **两步走** | `POST /api/synthesis/stream` 只建文档；综述由用户自行点「生成对比综述」走**现成的** `GET /generate/{id}/stream`。生成域**零批量概念** |
| 3 | **单真相源** | 合成文档的 rawText **就是**模型输入（按 multi 预算恰好装满），生成时不许再读原文 |

> **D7 已拍板：合成完成后不自动触发生成**。理由不是省事——用户需要先在预览区看到「## 来源文档」清单（选了哪几篇、哪几篇被压缩），确认之后再花 token。故合成后默认停在**转录 tab**（`:449` 的 `setActiveView('transcript')` 分支刻意含 `composite`），而不是自动跳到还没有内容的笔记 tab。

### 出处头嵌进正文（抗「删了 index.json 就全丢」）

出处（来源 id / 标题 / 各源内容 hash）写在 `transcript.md` 的**第一行**，而不是只存索引：

```
<!-- limo-meta {"v":1,"sourceType":"composite","sourceDocIds":[…],"sourceTitles":[…],"sourceHashes":[…],"sources":[…] } -->
（空行）
## 来源文档
1. 论文A ✓ 完整输入
```

**为什么必须是第一行**：删掉 `index.json` 重建时，`_rebuild_index()` 会用 `sha256(rawText)` **重推 id**、把 `sourceType` 硬写成空串——一切非内容派生的字段都保不住。让正文自身成为身份的单一真相源，索引退化为缓存。

新建 `app/core/transcript_meta.py` 收口编解码（`dump_meta` / `parse_meta` / `build_composite_raw_text` / `strip_meta`）。**单独成模块的理由是「写方读方必须同源」**——两处各自手拼 JSON 必然漂移。

- ⚠️ **头不剥离**：`rawText == 模型输入 == 用户所见` 是三位一体前提，把头搬到索引或 sidecar 就等于制造第二个真相源
- ⚠️ `parse_meta` **只认第一行**：否则正文里偶然出现的 `<!-- limo-meta` 就能伪造出处（正文级注入）
- ⚠️ `dump_meta` 对标题复用 `sanitize_reference_name()` **并额外替换 `-->` 与换行**：否则标题能闭合 HTML 注释、伪造成第二行 meta
- ⚠️ **无头 = 普通文档，不是损坏**：判据写错会让所有历史文档被标 `metadataCorrupt`。损坏（有头但 JSON 坏）与无头是**两种状态**，必须分开

### Step 0 分叉：装得下就 Stuff，装不下才 Map-Reduce

```
total = Σ 各篇 rawText token
  ├─ total ≤ multi 输入预算 ──→ 【Stuff】N 篇原样组装，**零 LLM 调用**
  └─ total >  预算          ──→ 【Map-Reduce】维度发现(1) + 逐篇结构化抽取(N)
```

**「篇数越多越慢」这个直觉只在超窗时成立**：装得下时 N 篇与 1 篇的额外调用数都是 0（综合由后面的 notes 生成一次做完）。Map 阶段是**按固定维度结构化抽取**（研究问题 / 方法 / 数据 / 主要发现 / 局限），不是自由摘要——自由摘要 = 另一台 LLM 在最终模型看到之前就决定性地筛掉了信息。

### 预算同源（`app/generation/budget.py`）——顺带查出两处既有缺陷

| profile | 等式 | 状态 |
|---|---|---|
| `single` | `int(cw×0.8) − 2000 − 16384` | 与既有实现**逐值等价**，含一处**已知低估** |
| `multi` | `min(int(cw×0.5), cw − 65536 − 2000)` | 新路径，等式**必须闭合** |

两条上限取小：`int(cw×0.5)` 是可靠度上限（交叉文档对比比单篇摘要对长度更敏感），`cw − output − margin` 是输出预留上限。`cw < 65536+2000+35000`（≈102,536，实务上即「需要 128k 窗口」）时**显式拒绝**——`min()` 在窗口不足时会静默取到负值。

**为什么必须抽出这个模块**：此前有两套互不相同的算法（对话/压缩 `×0.7`，`ContentPreparer` `int(cw×0.8)−2000−16384`）。创建侧按 A 装满、生成侧按 B 截断，会在小窗口模型上**二次截断、后几篇凭空消失**。

**落地时查出的两处既有缺陷**（均已记录，只修了与本方案相关的那一处）：

1. ⚠️ **`cw` 在生成路径上根本没走四层解析**。全仓 `resolve_context_window()` 只有 2 个调用点（`chat_context.py` / `reference_compressor.py`），生成路径走的 `resolve_llm_config()` **只查第 1 层**（用户自定义），第 2 层预设从不流入 → 实际落到 provider 兜底表。dashscope/qwen3.8-max 预设 **131,072** vs 兜底表 **1,000,000**，**同一请求两个预算差 7.6 倍**。→ **已修**：`generate.py` 两处构造 `ContentPreparer` 前先调 `resolve_context_window()` 并把 `cw` 传进去。不修的话，创建侧与生成侧会**再次不同源**（只是换了个位置复发）。
2. ⚠️ **`single` 的输出预留是低估的**：真实 `max_tokens` 是 **65536**（`generator.py:24`），而 `content_preparer` 只预留 16384，相差 49152。128k 窗口下可用输入 86,473 + 实际可产出 65,536 = **152,009 > 131,072**——**现有单文档路径本身就可能超窗**。→ **D8 拍板：保持现状**（改成 65536 会让既有用户单文档可用输入从 86,473 掉到 37,321，**−57%**，是独立于本方案的行为变更），但用 `xfail(strict=True)` 把「不闭合」**钉成一条有名字的测试**——注释会烂，测试不会；哪天有人改对，它会立刻 XPASS 失败逼着反转断言。

### 逐篇降级必须落进产物，不能只靠 SSE

被压缩/截断的篇在「## 来源文档」列表里**逐条标注** `✓ 完整输入 / ⚠ 已压缩为摘要 / ⚠ 已截断`。SSE 提示是一次性的——「用户以为看到的是完整论文、其实是摘要」是知识系统里危害最大的误解。

### 资产搬运：复制到新文档 `assets/` + 路径守卫

源正文里的 `![图表](assets/chart_1.png)` 是**相对路径**，写进新文档后会指向新文档自己那个空的 `assets/`。故组装阶段把被引用的图片复制过去并按源序号加前缀（`d1_` / `d2_`——**两篇论文都可能有 `img_0001.png`**，不加前缀必然互相覆盖）。

⚠️ **每个匹配路径都要做 `relative_to(源文档目录)` 守卫**，越界则跳过。现存同款正则已有 5 个副本，新副本不带守卫 = **新开一条读取任意文件的通道**（导入内容里写 `assets/../../../../.ssh/id_rsa` 会被复制进产物，再随预览 / 导出包 / 模型输入外泄）。

⚠️ **最高危的失败模式是「复制了但忘了改写引用」**：文件躺在 `d1_chart_1.png`，正文仍写 `chart_1.png`——结果与完全不搬运一模一样（还是裂图），而日志里一切正常：复制成功、无异常、文件数正确。故测试**逐条断言引用与新文件名对应**。

### 生成闸门（`generate.py`）

composite 请求下：`want_polished=False`（后端 `build_both()` 对 composite 返回 `(sp, sp)`，前端也不给 polished 入口，两层一致）、`stream_mode="notes"`、`format_list=[]`（合成不支持图表/截图）、跳过 `_auto_title`（标题是用户给的合并名，不该被生成正文首行覆盖）、`metadataCorrupt` 时推 `warning` 但**不阻断生成**。

### 前端

- **入口在文档库而非 GenPanel**：输入的 N 篇是**已入库文档**，用户此刻的注意力在文档库；GenPanel 是「当前单篇」的工作台（`currentDocId` 单值语义），塞入多文档会污染单篇主线
- `BatchBar` 加「合成」按钮（`disabled = busy || selectedCount < 2`，1 篇没有可对比的对象——**不是参数错误，是语义不成立**）；确认对话框含已选清单 / 标题 / 对比指引
- ⚠️ **`onDone` 走 `useStore.getState()` 而非组件闭包**：合成可能持续数分钟，期间用户完全可能折叠文档库——而**折叠 = 卸载组件**（`SlotLayout`），卸载后组件回调全部失效，文档其实已落库、列表里却看不到，用户以为失败了
- `tabRegistry`：**只**给 `summary` 与 `transcript` 加 `'composite'`，**绝不动 `DOC_TYPES`**（那会被 `ALL_TEXT` 展开，白送 polished / mindmap 给一个「没有原文可言」的 N 篇综述）；`summary.defaultFor` 也要加——`forSourceType` 与 `defaultFor` 是**两件事**（列出哪些 tab ≠ 生成完停在哪个 tab），只加前者会让用户点完生成停在「转录」tab
- `getTypeMeta` 加 `isComposite` 分支：`coreProducts=[notes]`、`hasPolish=false`、按钮文案「生成对比综述」。⚠️ composite **不在** `_ALL_DOC_TYPES` 里，故 `isDoc === false`——GenPanel 里所有以 `isDoc` 门控的块（文档引擎选择 / EPUB 解读）对它自动不渲染
- `DocRow` 加「N 合 1」胶囊（**判据是篇数 ≥ 2**，不是 `sourceType === 'composite'`——N=1 是噪音）+ 紧凑模式一个 Layers 图标；`docMeta` 加 `composite` 筛选项（靛蓝，与 11 个原始输入类型区分开）

### ⚠️ 落地时推翻 / 修正设计文档的三处

| 设计文档原话 | 落地改判 |
|---|---|
| §13.6「复用现有 compressor 的骨架、只替换 Map 的产出形态」 | **共享的是工具，不是骨架**。compressor 的输入是散文、产出是自由摘要；本模块 Map 产出是固定维度结构，控制流与形态都不同。真该共享的只有 `budget.py` 与 `estimate_tokens` / `sanitize_reference_name` 三个工具 |
| §1.2「本项目早已是四层解析、按 model_id 优先」 | **对生成路径不成立**——它根本没调 `resolve_context_window()`（见上「预算同源」第 1 条）。该说法只对对话 / 压缩侧成立 |
| §5.2.1「输出预留 = max(16384, …)」 | 真实 `max_tokens` 是 **65536**。初稿那句「产物体量大，高于单文档的固定 16384」在小窗口模型上是**反的** |

> 三处的共同形态：**「函数存在」≠「该路径在用它」**、**「文档说的流程」≠「代码做的流程」**——与本项目「断言存在 ≠ 断言有效」同源。

### 测试

后端新增 6 个文件：`test_transcript_meta.py`（编解码往返 / 标题含 `-->` 与换行 / 无头≠损坏 / 只认第一行）、`test_budget.py`（逐值等价 / 取小 / 闸门 / **single 不闭合的 xfail(strict)** / cw 来源 / 构造点对称性）、`test_store_composite.py`（删 index.json 后重建字段仍在 / `save_transcript_full` 守卫 / 去重豁免 / 损坏标记）、`test_synthesis.py`（分叉与调用数 / 预算收敛 / 资产搬运含**路径穿越对照** / 降级显式化）、`test_generate_gate.py`（产物裁剪 / 格式清空 / corrupt 提示 / 跳过 auto-title）、`test_synthesis_api.py`（SSE 协议 / 业务错误走事件 / **五种结局后信号量均归还** / 异常哨兵）。

前端新增 4 个文件：`synthesis.test.ts`（body 本体而非「传了 options」）、`getTypeMeta.test.ts`、`tabRegistry.test.ts` 的 composite 分组、`docLibrarySynthesis.test.tsx`（按钮门控 / body 的 doc_ids 逐项相等 / **done 之后零 `/generate/` 请求**）。

**每条判据都配了「看起来对、其实错」的对照实现并实测判红**（项目铁律「断言存在 ≠ 断言有效」）。

---

## 2026-09-25 — 文档库前端交互重设计（IDE 式）

用户判断：「从用户角度看，我认为文档库的前端交互逻辑需要优化与重新设计」「可以参考编程 IDE 文件显示」。追问后锁定两个痛点——**搜索与筛选**、**文件夹的组织与移动**。

前置诊断（均为代码事实，不是感受）：

- 文件夹关在一个 `max-h-[150px]` 的小滚动框里，列表是另一个独立滚动区 —— 两个小窗口各自滚动，用户来回找
- 筛选器常驻三行（类型 11 键 + 排序 3 键 + 标签 facet）吃掉纵向空间，而**没有「清除全部筛选」**（`hasFilter` 只用于空态文案，不渲染任何按钮）
- 标签 facet 只取前 12 个 —— 第 13 个标签是「看得见概念、永远够不到」的隐性缺失
- 批量选择是「模式」：点「批量操作」后**整条标题栏被 BatchBar 替换**，「新建文件夹」「收起面板」在批量态下消失；无 Shift 区间、无 Ctrl+A
- 「当前预览」与「批量勾选」**视觉完全相同**（`selected || active` 走同一 className）
- 折叠面板 = **卸载组件**（`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`），多选 / 分批深度 / 编辑态全在组件本地 state —— 收起再展开，一切归零

### 三条「删除」是本轮的核心（而非新增）

| 删掉的东西 | 为什么必须删 |
|---|---|
| **`selectMode`** | 它把选择变成「模式」，于是所有批量操作都要先切换状态；而它的读取点散落在 `DocRow` 的 `draggable`、操作组显隐、工具栏替换分支三处。删掉它，批量条由 `hasSelection = selectedIds.size > 0` **派生** —— 需要"原子修改"来防的状态漂移，因为漂移的载体本身没了 |
| **「正文匹配（N）」独立分组** | 三个确认缺陷：**不受筛选约束**（用户筛了 PDF，它照样列出网页和视频）、行是裸 `<button>`（无复选框 / 右键 / 拖拽）、与「排列方式」旋钮直接打架（第三个维度的系统分组）。但它有一项必须保住的性质 —— FTS 的 `ORDER BY rank` 相关性序 |
| **`FolderTree.tsx`** | 文件夹不再是独立树，而是「排列方式」的一个取值，与类型 / 时间共用 `GroupHeader` |

### ⚠️ 单击语义（本轮最该先定死的一条）

原实现是 `onClick={() => selectMode ? onToggleSelect(doc.id) : onSelect(doc.id)}` —— **单击行 = 打开文档，这是文档库唯一的主流程**。若按「常态多选」字面实现成「点即选中」，用户再也打不开文档。故定死：

| 操作 | 行为 | 改选择集 |
|---|---|---|
| 单击行的任意空白处 | **打开文档**（主流程不变） | 否 |
| 单击复选框 / Ctrl·Cmd + 单击行 | toggle 该行 | 是 |
| Shift + 单击行 | anchor 到该行的区间 | 是 |
| Ctrl+A（焦点在列表内） | 全选当前结果集 | 是 |
| Esc | 清空 | 是（清空） |

单击打开**不动选择集**，让「预览目标」与「批量操作目标」成为两个真正正交的维度 —— 这与 `active` / `selected` 视觉分离是同一件事。复选框常驻不只是为鼠标：它给触屏和键盘用户一条「只选不开」的显式路径。

`anchorId` 是操作过程态，留组件本地（跨折叠退化为单点 toggle，可接受）；`selectedIds` 必须进 store。

### 排列 ⊥ 排序（两个独立旋钮）

**排列**（`groupBy`：平铺 / 按文件夹 / 按类型 / 按时间）管**横向归类**，**排序**管**组内纵向顺序**。刻意**没有「筛选时退化成扁平」的特殊模式** —— 筛了 PDF 而排列仍是按文件夹时，列表按文件夹分组、每组只显 PDF、空组自动隐藏，用户一眼看到「这些 PDF 分别在哪些文件夹里」。那种退化正是「树和筛选打架」的来源，不是解法。

分组头泛化到三维度共用（`GroupHeader`），但**增删改只对文件夹组开放**（`group.editable` 显式标志，而不是让每个调用点各自判 `groupBy === 'folder'`——判据散开几处后必然漏一处）：类型 / 时间的组名是派生值，「把 PDF 组改叫视频」没有语义。空组的去留按维度区分——**文件夹组保留空组**（它是拖拽的放置目标，隐藏它 = 用户既没法把文档拖进去、也没法确认文件夹真建好了），类型 / 时间组隐藏空组（展示「视频 (0)」只是噪音）。「未分类」固定最后：它不是一个用户建的容器，排中间会把真实文件夹挤散。

### 相关度排序键 —— 承接那个分组的唯一价值

取消「正文匹配（N）」独立分组后，若不做补偿，同样的搜索会按「时间」排，最相关的那篇可能沉到第三行：**功能没坏，只是变难用了**。故把 FTS 的相关性序升格成一等排序键：

- rank 取自 `hits` 的**数组索引**（后端 `ORDER BY rank` 已经排好），**零后端改动**；非命中最末（`??` 而非 `||` —— 索引 0 是合法且最相关的值）
- 搜索词**从空变为非空**时自动切过去（= 原分组「最相关的在最前」的默认体验）；用户手动改回后**本次搜索内不再回切**（每敲一个字都重置他的选择，比不自动切更烦人）；清空搜索词回落到时间（否则「相关度」键消失，留下四个键都不高亮的空档）
- 该键**只在本次搜索真有命中时出现**：没有命中时所有 rank 都是 MAX，排序退化成保持原序，点了看不到任何变化 —— 那是个死键

结果集也一并单一化：`eligible`（类型 / 标签 / 文件夹 / 日期 / 标题关键词，**不含 query**）∩（关键词内存命中 ∪ FTS 命中）。关键词**刻意不参与 `eligible`** 是关键——FTS 命中的文档也要受筛选约束。命中行统一走 `DocRow`（有复选框 / 右键 / 拖拽，不再是不能选的裸按钮），措辞标「**原文**」角标而非「正文」（FTS5 索引的是 title + content 两列，一次查询无法判定命中来自哪一列，而「原文里有匹配」对所有命中都成立）。FTS 结果限 100 条，取回**恰好**上限条数即提示细化关键词（判据是 `>=` 不是 `>`：后端按 limit 返回，上限就是 limit 条，写 `>` 等于这个提示一次都不会出现）。

### 筛选区：两级折叠 + 五个小节

文件夹范围 / 文件类型 / 标签 / 标题关键词 / 日期范围，**整区可折叠 + 各小节独立可折叠**；小节收起时标题行仍显示**激活摘要**（`文件类型 · PDF ×`）——否则收起后用户不知道在筛什么；区头显示「已激活 N 项 · 清除全部」。

- **标签取消 12 上限**：全量在 `max-h` 内滚动 + 已选置顶且永不隐藏
- **标题关键词纯内存、零网络、不触发 FTS**：与搜索框的分工是「筛标题」vs「找内容」（搜索框会打 `/api/search`），二者可同时生效（AND）
- **日期范围**：「本周」= 本自然周（周一起），**不是**最近 7 天；比较在 ISO 字符串的日粒度上做（`new Date()` 再取本地日期会让「今天 00:00 导入的文档」落到昨天）；没有 `createdAt` 的文档在设了范围时**被排除**——「拿不准就留下」在这里是错的，用户设了「本周」却看到一堆无日期的文档只会以为筛选坏了
- **文件夹刻意保留两个入口**：「文件夹范围」小节（筛选「只看这些」）与「排列：按文件夹」（归类呈现）。不采用「排列=folder 时隐藏该小节」——切换排列时筛选区突然多 / 少一节，布局突变比多读一个词更困惑
- 筛选区与列表区之间是**可拖拽分隔条**（配比持久化，双击复位，钳制在允许区间内）

### 状态归属：由「折叠即卸载」判定

`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>` —— 组件本地 state 收起即归零。故凡「重开后不该重置」的一律离开组件本地：

| 状态 | 归属 |
|---|---|
| `selectedIds` / `listLimit` / `folderBusy` / `docsLoading` / `expandedFolders` / `titleFilter` / `dateFrom` / `dateTo` | `docsSlice` |
| `density` / `groupBy` / `sort` / `expandedSections` / `filterAreaHeight` | `docLibraryPrefs`（localStorage `mbw_docLibPrefs`） |
| `docLibSearchFocusNonce` | `uiSlice`（**自增 nonce 而非 boolean**：boolean 表达不了「已经是 true 时再按一次」，而用户第二次按 Ctrl+K 时依然预期「再聚焦一次」） |
| `anchorId` / `draggingIds` / `ctxMenu` / 标题与标签编辑 / `deleteTargets` | 保持组件本地（纯瞬时，跨折叠无意义） |

**刻意不改 SlotLayout 为 CSS 隐藏**（那是一处改动就能保住所有面板本地 state 的路）：本轮的 store 需求不止「折叠别丢」，还要跨组件共享（工具栏 / 批量条 / 行）+ 与筛选 setter 原子发生修改（切换筛选时清空选择必须在同一个 `set` 内完成，否则有时序窗口）。纯 CSS 隐藏解决不了这些。

另外 `countByFolder`（本地派生）是**文件夹计数的唯一来源**——不能读后端 `folder.count`：那是上一次拉取的快照，而 `moveDocs` 是乐观更新（移完立刻改 `docs` 里的 folderId，不 refetch），两边各读各的会在移动瞬间互相矛盾（文档行已经进去了，文件夹后面的数字还是旧的）。

### Ctrl+K 与顶栏搜索：从两份实现收成一份

顶栏 `SearchBar` 曾是一整套**自己的**下拉搜索（限 10 条、无 AbortController、自带一份 `SearchSnippet` 渲染分支、有自己的开合状态机、以及那个假的「(Ctrl+K)」tooltip）。它与文档库的搜索是**两份实现**：取回条数不同、命中结果集的组织方式不同、连片段高亮都是两条路 ——「搜文档」被做成了两遍，只会越走越远。现在它退化为**入口按钮**，与 `Ctrl+K` 共用同一个 `openDocLibSearch`。

⚠️ 宽度必须一起复位：折叠态下宽度可以是 40px（用户拖窄时 `setSlot` 会顺手折叠它），只设 `collapsed: false` 会展开出一条 40px 宽的缝 —— 用户按 Ctrl+K 是要搜索，不是要一条缝。**注意 `setSlot` 只在显式传 `width` 时才检查折叠阈值**，所以「只改 collapsed」这种写法连自动兜底都不会触发。

### 顺手修掉的真缺陷

| 缺陷 | 后果 |
|---|---|
| 右键「复制 Markdown」改为按 `ctxDoc.id` 现取（此前取的是当前预览那一篇） | 复制的是**另一篇**的内容 |
| 新增 `docsLoading`，首帧不再渲染「还没有文档」 | 冷启动时用户真能看到那句**假空态** |
| 删除文档后清理 `currentContent` / `currentRawText` / `markdownMeta` / `imageResults` / `streamBuffers` | 删完仍显示已删文档的产物 |
| `selectDoc` 签名改 `(id: string \| null)`，仅真值才写 `mbw_lastDocId` | `''` 哨兵与「没有」语义漂移，刷新后读到空串 |
| `ContextMenu` 的 `divider` 补渲染分支 | 两个「分隔项」渲染成两个空文案的可点按钮 |
| 哨兵 `IntersectionObserver` 绑 `root: scrollRef.current` | 以 viewport 为基准，树态 + 密度切换后偏差放大 |
| 文件夹加载失败从 `.catch(() => {})` 改为 toast（模块级 flag 去重一次） | 静默吞掉会让「文档全挤在未分类里」看起来像数据损坏 |
| 拖拽的 `onDragOver` 改判 `dataTransfer.types.includes(...)` | 依赖父级 `dragging` state 时，快速拖入读到旧值 → 不 `preventDefault` → drop 永不触发；新判据同时把「从资源管理器拖文件进来」挡在外面 |
| 文件夹新建 / 重命名失败保留草稿（`onCreate`/`onRename` 返回 `Promise<boolean>`，成功才清） | 失败时把用户刚打的文件夹名弄丢 |
| 移动的部分失败**只回滚失败的那几条** | 整体回滚会把已经成功移动的也退回去 |

### 简略模式 = IDE 文件树（同日续，用户看过实现后推翻原设计）

原设计是「简略模式**列表平铺不分组**」（上面的决策基线第 7 条），理由写在代码注释里：那是「一屏看更多」的模式，分组头会吃掉它省下来的空间。用户回复：**「简略应该像 ide 文件树一样」**。追问两处岔路后定死：**固定按文件夹分组**、**复选框常驻显示**（后者已是现状，无需改动）。

| 文件 | 改动 |
|---|---|
| `DocLibrary.tsx` | `grouped` 的 `density === 'compact' ? 'none' : groupBy` → **固定 `'folder'`** —— 简略模式本就不渲染「排列方式」旋钮，也就没有「跟随那个偏好」的必要 |
| `GroupHeader.tsx` | 新增 `compact` prop：行高 26px（`h-6.5`）、按钮 `h-full` 而非 `py-1` |
| `DocList.tsx` | 新增 `treeIndent` prop：子行包一层 `pl-4` 容器 + `left-2` 的 1px 缩进参考线（`aria-hidden` 纯装饰），**只给有行的组画**（空组画一条线却没有孩子，是纯噪音）；骨架屏换成单行条 |
| `DocRow.tsx` | 紧凑行高 30px → **26px**（与分组头同高），`rounded-lg` → `rounded-md` |

**为什么推翻**：平铺省下的确实是纵向空间，但省掉的是**结构**——一个文件夹里 30 篇时，平铺列表除了一列标题什么信息都不给，用户在文件堆里翻找的成本远高于几行分组头的高度。

⚠️ **26px 是成对常量**（`GroupHeader.compact` 与 `DocRow` 紧凑分支），改一处必须改另一处；两处文件头都写明了这是「父节点与子节点同高」的契约。不共用一个导出的常量，是因为它们分别落在两个组件的 Tailwind 类里，抽出来反而要绕过 Tailwind 的静态扫描。

**顺带修掉的假空态**（沉淀为模式 #50）：`DocList` 的骨架判据是 `docsLoading && !rendered.length && !groups?.length`，而**分组模式下 `groups` 永远非空**（按文件夹分组时至少有一个 0 篇的「未分类」组）→ 这条判据在默认排列下**一次都没命中过**：冷启动不显示骨架，显示的是「未分类 0」+ 空列表。这正是 #19 要杀的假空态，只因默认排列不是平铺而一直没暴露。判据改为「**有内容可显示**」（`rendered.length > 0 || groups.some(g => g.docs.length > 0)`），并在骨架期间、以及真的没有可显示行时**连分组头一起跳过**（`!emptyText`）——否则「还没有文档」旁边会杵着「未分类 0」。

**验证**：新增 5 条用例 + 改写 1 条（原「简略模式强制平铺：即使偏好是『按文件夹』也不渲染分组头」这条断言被本次设计变更直接推翻，改写为「简略模式**固定按文件夹**分组，不跟随『排列方式』偏好」——偏好故意给「平铺」）。每条均做对照实验自证，**六次全部精准命中且互不串味**：

| 对照（一个错误但合理的替代实现） | 命中了哪些用例 |
|---|---|
| 改回 `? 'none' : groupBy`（简略一律平铺 = 原设计） | 3 条（分组头 / 缩进 / 同高） |
| 分组头不传 `compact`（父节点矮 6px，截图上看不出来） | 仅「父子同高」 |
| 缩进分支写死 `false`（行照样「看着缩进了」，只是没有容器） | 仅「缩进容器」 |
| 骨架判据改回 `!groups?.length` | 仅「冷启动骨架」 |
| `treeIndent` 恒 `true`（详细模式也缩进） | 仅「两种密度各长各的」 |
| 去掉 `!emptyText` | 仅「空态不画空组头」 |

### 影响范围与验证

- **后端零改动**：文件夹 CRUD / 批量 delete·tag·move / FTS 完全够用；标题关键词、日期范围、相关度排序都是纯前端。
- 前端拆 **17 文件**（原 8），纯函数单独导出以便表驱动单测：`filterDocs` / `sortDocs` / `buildResultSet` / `groupDocs` / `countByFolder` / `collectTagFacets`。测试选择器 `data-doc-row` **未改名**（既有测试依赖它）。
- 测试 130 → **209**（17 文件）。新增 `docLibraryLayout.test.tsx`（19）/ `docLibraryFilters.test.tsx` / `docSelection.test.tsx` / `docLibrarySearch.test.tsx` / `contextMenu.test.tsx`；改写 `docQuery.test.ts`（`mergeHits` 三条用例被单一结果集 `buildResultSet` 取代）；`docLibrary.test.tsx` 改用 `setState({ selectedIds })` 构造。
- 每条新判据都做了**对照实验自证**（模式 #38「断言存在 ≠ 断言有效」）：先写一个「错误但合理的替代实现」→ 确认变红 → 还原 → 全绿。本轮共 28 条（22 + 「简略模式」一节的 6 条），其中三条**第一次没红**，暴露的是判据本身的问题而非实现：
  1. compact 分支传 `groups={null}` 与 `grouped` 内的 `density === 'compact' ? 'none'` 是**两道防线**，破坏任一都不红 → 删掉一道，只留语义层，判据才有区分力
  2. 标签 facet 按「频次降序 + 同频字典序」排，而 13 个标签各出现 1 次 → 纯字典序，`标签12` 排在 `标签1` 与 `标签2` **之间（第 5 位）**，`slice(0,12)` 根本砍不到它 → 测试数据改零填充 `标签01`..`标签13`
  3. 「Ctrl+K 展开时宽度一并复位」的原判据只断言 `collapsed === false` —— 而只传 `collapsed: false` 会留下一个 40px 的面板，判据照样绿 → 补 `expect(slot.width).toBeGreaterThanOrEqual(80)`
- 安全网：`docLibrary.test.tsx` 的「批量删除的 `cancelTask` 必须早于 `clearDocTasks`」用例**原样保留**并通过（迁 `selectedIds` 时最容易顺手把清选择写到取消任务之前）。
- 门禁：`npx vitest run`（209 passed）+ `tsc --noEmit` + `npm run lint` 全绿。

**本次打包 0.4.6**（版本号默认只推 patch —— 这是发行惯例而非 SemVer 判断，见 [CLAUDE.md](../CLAUDE.md) 「版本号管理」：此前 `5c7c6a1` 按「新功能 → minor」升到 0.5.0，随即被降回 0.4.5 再打包）。

---

## 2026-09-23 — Tauri 冷启动的 ERR_CONNECTION_REFUSED 刷屏修复

用户报打包版冷启动时控制台稳定刷出多条 `Failed to load resource: net::ERR_CONNECTION_REFUSED`（`/health`、`/api/models`、`/api/providers`，health 重复 6 次），要求定位。

### 三条独立根因（都指向同一个窗口期：前端比后端早动手）

| # | 根因 | 为什么会产生请求 |
|---|---|---|
| 1 | `uiSlice.backendReady` **初值为 `true`** | `useState`/store 初值在 render 时就生效，而 effect 在 render **之后**才跑。初值 `true` → **首渲染直接挂载完整 UI**，GenPanel 一挂载就拉 `/api/models` + `/api/providers`，那一刻后端必然还没监听；随后 `setBackendReady(false)` 才换回加载屏 —— 既刷一屏报错，界面还要闪一下 |
| 2 | `startHealthPoll` **无门控** | 运行期探活（`lib/health.ts`，10s 间隔）在挂载时立刻 tick 一次，冷启动阶段同样打空 |
| 3 | 前端 `healthCheck` **与 Rust 侧探测完全重复** | 原来是「90 × 500ms」的主动轮询，而 `lib.rs::spawn_backend_ready_probe` 早已在做同一件事（500ms 间隔、45s 超时、就绪 emit `backend-ready`）。**代价极不对等**：Rust 的探测在独立进程用裸 `TcpStream` 手写 HTTP，不碰 WebView 网络栈、零控制台日志；前端每失败一次就糊一行 |

### ⚠️ 关键认识：WebView 里的失败请求日志**无法在 JS 层抑制**

`fetch` 打到未监听端口产生的 `ERR_CONNECTION_REFUSED` 是**浏览器/WebView 自动打印**的，与是否 `catch`、是否 `console.error` 无关。因此「加 try/catch」「降日志级别」都无效 —— **唯一能消除它的办法就是那条请求根本不发出去**。这决定了修法方向不是「让失败安静」，而是「别在必然失败的时刻发」。

（用户中途补充「可以前几次静默后面然后再」，正是这个方向：静默期内零请求，之后再探。）

### 修法（纯前端，三处）

1. `frontend/src/store/uiSlice.ts` —— `backendReady` 初值改为**同步**按环境判定：`!(typeof window !== 'undefined' && window.__TAURI_INTERNALS__)`。Tauri 环境首渲染即加载屏，浏览器环境没有后端启动过程直接 `true`。
2. `frontend/src/App.tsx` —— `startHealthPoll` 加 `if (!backendReady) return` 门控。
3. `frontend/src/App.tsx` —— Tauri 分支整体重写为**事件优先 + 静默期**：
   - 新增 `BACKEND_PROBE_DELAY_MS = 2500`：静默期内**一条请求都不发**（后端实测 ~2s 就绪），由 Rust 的 `backend-ready` 事件唤醒；
   - 监听 `backend-startup-timeout`（Rust 探测 45s 超时也会 emit，此前前端没监听、等于白发）；
   - 静默期后**只探测一次**（覆盖「页面刷新时后端已在运行、`backend-ready` 早已错过」），**刻意不做循环轮询** —— 那正是噪音来源；
   - 新增 `BACKEND_HARD_TIMEOUT_MS = 50_000` 硬兜底（窗口句柄取不到时 Rust 的 emit 会静默丢弃）。

**为什么不动 Rust 侧**：Rust 的探测本就是无噪音的那一侧，问题全在前端重复轮询；改 Rust 要动 emit 时序与 launcher 逻辑，收益为零。

### 影响范围与验证

- 冷启动控制台从「一屏报错」变为 **0 条**；加载屏不再闪。
- 新增守门测试 `frontend/src/test/store.test.ts`（+2）：`vi.resetModules()` + 动态 import 验证 Tauri / 浏览器两种环境下 `backendReady` 的初值。
  **自证有区分力**：临时把初值改回 `true`，确认测试变红（`expected true to be false`），再还原。
  这条测试有必要是因为**回归后功能完全正常**（后端起来后一切照旧），只表现为控制台红字 + UI 闪一下 —— 而 `backendReady: true` 恰恰是更「直观」的写法，很容易被改回去。
- 前端 130 tests 全绿（128 → 130），`tsc -b` 通过。

---

## 2026-09-23 — 文档库组织能力（文件夹 / 全文搜索接入 / 排序筛选 / 大列表 / 批量）+ DeepSeek 打磨 + PDF 直传

> 三条用户诉求彼此独立：「文档多了你有时候要放半天这样也不好」（要组织能力）、查 DeepSeek 官方文档更新、以及「直传与经过我们链路是不是应该改成用户可选择式的」。
> 分五阶段落地（0 独立小修 → 1 文档库后端 → 2 文档库前端 → 3 DeepSeek → 4 PDF 直传），本文按方向而非阶段组织。

### 阶段 0：四项独立小修（零接口变更、零迁移风险，先落地）

| 修的是什么 | 为什么 |
|---|---|
| `DocLibrary.tsx` 的 `animationDelay: ${i * 50}ms` | 第 1000 篇文档要**等 50 秒**才显示。这不是「性能不够」而是**功能性错误**（延迟随索引线性增长），不是优化项 |
| `core/search.py` 的 `snippet(transcripts_fts, 1, ...)` 列号 1 → **2** | FTS 表列序是 `(id, title, content)`，列号 1 是**标题**。不修则正文命中的摘要显示的是标题 —— 功能一上线看起来就是坏的 |
| `routers/model.py` 的 `"*"` 分支**跳过 exclude 检查** | `if "*" in include:` 与 `elif any(...)` 互斥，导致「默认全标 + 排除某几类」的规则里**排除表是死代码**。今天 google 用 `{"include":["*"]}` 且无 exclude 所以看不出来；照抄给 deepseek 配排除就会踩（模式 #39：判据存在但看错了输入） |
| `core/token.py` `DEFAULT_CONTEXT` 补 4 家 | 缺 google / moonshot / opencode / zhipu。**zhipu 刻意取 131072 而不拉平 glm-5.2 的 1M**：provider 默认是第四层兜底，第二层预设已能给出 1M，兜底层取保守值才安全 |

顺带修 `deepseek` 的图片检测规则为 `{"include": ["*"], "exclude": ["pro", "reasoner", "coder", "embed"]}` —— 旧规则 `{"include": ["vl","vision"]}` 对 `deepseek-flash` 是**彻底 no-op**（名字里没有这两个词），此前能识别为多模态**完全依赖预设里手写的 `capabilities`**。

### 方向一：文档库组织能力

**1. 单层文件夹**（新增 `routers/folders.py` + store 层 folders 段）

**文件夹是纯逻辑分类，永不映射为磁盘目录。** 每个文档已有独立目录 `{title}-{hex6}/`（含 `transcript.md` / `assets` / `process` / 两版制产物），把文件夹做成目录会**同时**破坏目录命名规则、assets 相对引用与两版制三样东西。故 folders 只活在 `index.json` 里。

- **三处必改**（漏一处即丢数据）：`_empty_index()` / `_transcript_index_entry()` / **`_rebuild_index()`**。最后一处是手工逐字段构造的字面量 —— 漏了会让「删 index.json 后重新扫描」丢掉**全部**分类，而正常读写路径完全看不出问题。
- `INDEX_VERSION` 保持 3：`_load_index()` 对 v3 数据自动回填缺失顶层键，bump 反而让未来迁移分支误判格式。
- 未分类**不是实体**（不存在哨兵文件夹），是 UI 派生视图。
- 允许重名（id 是身份，名字只是标签）。
- **悬空 `folderId` 双向容错**：读路径归化为未分类；`delete_folder` 在**同一锁区内先搬移文档再删行**（防崩溃留下悬空引用）。删除文件夹**绝不删文档**，返回 `moved` 供前端提示。

**2. 全文搜索接入 —— 混用，不是二选一**

`docs` 数组里**没有正文**（白名单序列化器只存元数据），所以正文搜索在客户端**不可能**实现 —— FTS5 不是优化而是唯一可行解。但内存那层也不能撤：标题/类型/语言的匹配是 0ms、无网络，是「边打边筛」的体验来源。故：内存结果**立刻**渲染 → 停手 250ms 后打 `GET /api/search` → 合并。

**分组与排序的优先级必须明确定义**（否则行为不可预测）：

| 区域 | 内容 | 排序 |
|---|---|---|
| 主列表 | 内存命中的文档 | 跟随用户选定排序（时间/标题/类型） |
| 「正文匹配（N）」分组 | **仅**列表里没有的（纯正文命中） | 按 FTS `rank`，不参与用户排序，固定追加在下方 |

**标题+正文都命中的文档只出现在主列表，但卡片加「正文」角标** —— 否则用户会以为「这篇正文没匹配」。

顺带把 `<mark>` 摘要的 `dangerouslySetInnerHTML` 渲染抽成 `ui/SearchSnippet.tsx` 给两处复用（**不复制第二份**，否则两处 XSS 姿态会漂移）。

**3. 排序 + 标签筛选**：此前 `activeTag` 过滤的其实是 `d.sourceType` —— 把**来源类型**与**用户标签**拆成两组独立筛选，标签行从 docs 聚合出频次 facet，多选可交叉。

**4. 大列表**：只渲染前 60 条 + `IntersectionObserver` 哨兵每次 +60（**不引入新依赖**；真窗口化待「第二步后仍可感知卡顿」再做 —— 行高不固定，真窗口化易错）。**全选 = 当前筛选结果的全部匹配项（跨分批）**，工具栏始终显示「已选 N 项」，`N > 已渲染条数` 时追加「（含未显示项）」：分批渲染只是「少画 DOM」，不是「数据只有 60 条」。

**5. 批量操作**（`POST /api/transcripts/batch`，delete/tag/move）：**按 action 做显式枚举校验**（未知 action / tag 缺 tags / move 缺 folderId 一律 400，不能静默返回 `affected:0`）；逐条 try/except 收集 `failed:[{id,error}]`（删文件无法原子回滚，失败必须显式化）；**全部失败 → 400**（避免前端把「一条没成」当成功）。store 侧 `set_transcripts_folder` / `update_transcripts_tags` **一次加锁一次 `_save_index()`** —— 相对 N 次 PATCH = N 次全量写盘，这是本设计的核心收益。批量删除复刻既有的顺序约束：**先 `cancelTask(id)` 再 `clearDocTasks(id)`，顺序不可颠倒**。

前端拆为 8 文件（`DocLibrary` / `DocRow` / `FolderTree` / `BatchBar` / `useDocQuery` / `docLibraryPrefs` / `docMeta` / `entryDelay`），`useDocQuery` 的 `filterDocs`/`sortDocs`/`mergeHits` 单独导出以便表驱动单测。拖拽用原生 HTML5 DnD（零新依赖），**并配 ContextMenu「移动到文件夹 ▸」子菜单** —— 只做拖拽会让键盘用户无法移动文档。

### 方向二：DeepSeek 打磨

**1. 思考档位可配**（新增 `generation/effort.py` —— 全系统唯一真相）

能力位新增 `reasoning`，**按模型标注，不按 provider**：写成 `provider_id === 'deepseek'` 会与「能力按模型标注」的原则自相矛盾，且将来加一个支持 reasoning 的渠道要改前后端两处。

| user_tier | notes | polished |
|---|---|---|
| `""`（未指定/老客户端） | `None`（= 服务端默认） | `"low"` |
| `"low"` / `"high"` | 跟随用户 | **`"low"`** |
| `"max"` | `"max"` | `"max"` |

- **整理版恒 `low`（除非选 max）**：它是忠实整理任务而非创作，深度思考换不来质量却要付 90s+ 静默期。**这条必须在 UI 上明示**（GenerateOptions 档位说明写了「整理版固定快速整理；选『最强』才会深度思考」），不能是隐规则。
- **单独成文件**是因为此前 `"low"` 字面量散落在 generate.py / generator.py 四处 —— 正是「改一处漏一处」的现场。现在有 AST 结构断言把守：全库不得再出现硬编码档位字面量。
- **静默期必须分档**（`chunk_timeout_for`）：max 档思考更久，仍用 150s 会让「**选最高档反而更容易失败**」。分档值必须严格小于生成总超时，否则总超时先到、分档成死代码。
- 前端默认档 `'high'`（= 今日服务端默认行为，零回归），复用现有 `genPrefs` 键（`{...DEFAULTS, ...raw}` 展开，新字段自动向后兼容，无需迁移代码）。

**2. 多模态检测修复 + 3. `DEFAULT_CONTEXT` 补 4 家**：见阶段 0。

**4. 旧名清理**：判据是**改「会被当作值使用的字面量」，不改「解释历史的叙事」**。`deepseek-v4-flash` → `deepseek-flash` 等已在上一轮完成；本轮清理注释/文案里的旧模型名，保留「当时为什么这样设计」的理由。
⚠️ **opencode 网关的两条同名模型名本轮不擅自改** —— `enabled_models` 的 id = `md5("{provider}:{model_name}")`，改名即改 id，会在模型下拉里留下新旧重名条目；而若该网关其实只认旧名，迁移等于**直接把可用模型改坏**。记入待查证项（`~/.limo/config.yaml` 里无 opencode 条目，本地无从验证）。

### 方向三：PDF 直传（Qwen 优先）

#### 探针先行（先实测再定值，顺序不能反）

| 结论 | 数据 |
|---|---|
| **20 MiB 上限约束的是 base64 之后的字符串**，不是原始文件 | 实测 19MB PDF 被拒：`Exceeded limit on max bytes per data-uri item : 20971520`。按 `len("data:application/pdf;base64,") + 4*ceil(N/3) ≤ 20971520` 反解，原始字节上限 ≈ 15.0 MiB |
| **闸门取 14 MiB 而非贴着理论边界** | 15 MiB（15728640 字节）算出的 data-uri 比上限**多 25 字节**，照样被拒 —— 贴边的常量等于一个随机失效的开关 |
| **首包耗时次线性** | 12KB/1 页 → 3.4s · 4.4s · 4.7s；9.5MB/100 页 → 131.5s · 142.8s；13.7MB/140 页 → 149.3s。体积 ×1.44 只涨 6s —— 服务端逐页渲染，首包由**页数**主导而非字节数 |
| **Chat Completions 与 Responses API 在 dashscope 兼容层上都能送达** | 与 OpenAI 官方「Responses API 返回 200 但文件不会传给模型」的记载**不同**。但仍锁定 Chat Completions：官方明写的那个失败模式**没有任何客户端可见信号**（产物是一篇看不出异常的泛泛而谈），而 Chat Completions 的 `file` 块是唯一逐次自证过的通道 |
| **探针判据要宽松** | 造图片型 PDF（内容以像素承载、无文本层）+ 页内唯一随机串令模型复述：**能复述 = 文件真的到了**。⚠️ 模型 OCR 会把 `0` 读成 `O`、`1` 读成 `l`，逐字符严格比较会得到**假阴性**（首版就栽在这，险些把「已送达」误判成「不支持」）——随机串字符集须排除易混字符，比对前做归一化 |
| **Files API 不作为本轮方案** | 它能突破体积限制，但**首包耗时才是真瓶颈**（≈10s/MB，**次线性、由页数主导**；官方给的最坏值是「首包最长 300 秒」——150MB 量级没有任何用户等得起） |

#### 形态：用户可选的模式切换，不是叠加

```
本地 Doc 引擎照跑（本地库、不花 token）→ rawText，供预览/导出/搜索/两版制
                          ↓
   送进 prompt 的内容 ← 用户在这里二选一
     ├─ 文本模式（默认）：抽出的 Markdown        ← 现状，零回归
     └─ 直传模式：PDF 原文件（file 块）          ← 只发原文件
```

**为什么只发原文件**：叠加会让同一份内容 token **双花**（PDF 视觉计费 + Markdown 文本各占一遍），大 PDF 尤其明显。

**为什么 Doc 引擎仍必须照跑**：`rawText` 是转录 tab 渲染、全部导出、FTS 索引、assets 引用与两版制的真相源，跳过它 = 转录 tab 空白 + 导出丢内容。好在它是本地库、**不花 token**，所以「照跑但不进 prompt」既不断链也不双花。

#### ⚠️ 双真相源（本模式的固有代价，不是可修的 bug）

| 场景 | 模型看到 | 转录 tab / 导出 / FTS 搜索看到 |
|---|---|---|
| 扫描版 PDF、本地无 OCR | 读懂了原件，笔记翔实 | rawText 近乎空 → 转录空白、搜不到、导出无内容 |
| 图片型表格 / 图表 | 能看到 | rawText 里没有 |

故直传成功但 `rawText` 过短（**< 100 字符**）时，生成完成时给一条**显式 warning**（复用图表「未生成」那条通道，前端走 info toast **不标 partial** —— 生成本身是成功的）。判据取 100 而非 0：扫描件 + 无 OCR 时 rawText 不是空串而是**占位文本**（原图 markdown + `_所有 OCR 引擎均失败_`，约 50 字符），只判空串会漏掉这种「看起来有内容、实际没内容」的典型形态。

#### 超时：三层独立，且定位与原方案不同

plan 原方案把 `DOC_DIRECT_TIMEOUT` 定位为「`prepare()` 内的直传调用超时」。**实现中改掉了这个定位**：既然语义是「只发原文件」（方案 B，文件块随生成消息发出），`prepare()` 内就**不发起任何 LLM 调用**（只读文件 + base64，百毫秒级），没有超时可包。真实的静默期是**模型解析原件、一个 chunk 都不推**的那段时间 —— 故 `DOC_DIRECT_TIMEOUT` 落在**生成调用的首个 chunk 静默超时**上（`effort.chunk_timeout_for`，与思考档位用 `max()` 合并而非覆盖：哪天它被调小，直传不该反而比普通档更早被判挂起）。

- `DOC_DIRECT_TIMEOUT = 330s`、`DOC_DIRECT_GEN_TIMEOUT = 660s` —— **这两个值在同日续修中从 240/450 改过**，理由见「方向四」：240 是按实测样本 × 1.6 余量算的，而官方文档明写「首包超时时间最长为 **300 秒**」，240 < 300 会把该区间内的**正常请求**掐断（表现仍是静默降级，日志里只看到「超时」，看起来像厂商不配合）。**实测证明不了上界，上界只写在官方文档里。**
- 最坏路径（直传 + 生成）≈ **11 分钟**，故流式路径先推 `stage` 事件「正在直传 PDF 原件给模型解析…」，否则看起来就是卡死。

#### 实现要点

- 新增 `services/doc_understand.py`：`build_pdf_document()` 只做「文件 → 消息块」，**不发起任何请求**；失败一律 `return None` + `log.warning` 带原因与尺寸。
- `llm/service.py`：`stream()` 三路互斥消息分支（document / image_urls / 纯文本），新增 `supports_document` —— **刻意不复用 `image` 能力位**：`image` gate 着「AI 分析图片」「OCR 模型选择」「图片处理用途筛选」三处入口，拿它代指 file 块会把这些入口一起错误放开。
- `prepare()` 返回 **4 元组**（`document` 放末位）：刻意用位置返回而非实例属性 —— 漏改调用点会立刻 `ValueError`，而不是静默拿到一个空的 document（模式 #16 的教训）。
- 直传时**正文被替换**为一句占位说明（不是追加），且**不发图片**（prompt 里已无正文可供嵌入）；能力门 + 尺寸闸门 + 扩展名闸门（纵深防御：前端只在 isPDF 时显示开关，但 API 可直调）。
- 前端：开关放在文档区块内、**仅 `isPDF` 时显示**（Word/PPT/Excel/EPUB 的厂商支持未查证，不给假入口），无 `document` 能力时禁用 + tooltip，`genPrefs` 默认关 + 记忆。
- 能力位 `document` **只标查证过的**（见「方向四」：首版只标 `qwen3.8-max`，同日逐家查官方文档后扩到 dashscope 官方名单的另外两个）；`routers/model.py` **不做名字自动检测** —— 按名字猜 PDF 能力必然误标。

### 方向四：其他渠道的文档能力 —— 逐家查官方文档（同日续修）

起因：能力位 `document` 当时只标了 `qwen3.8-max` 一个，用户提出「其他模型的文档能力我们可能也需要去官网文档调查然后再进行针对性的修改」。**不实测**（成本高），只查官方文档。

#### 结论表（唯一沉淀点，模块文档 [backend/app/services/doc_understand.py](../backend/app/services/doc_understand.py) 有同一份）

| 渠道 | 官方结论 | 标注 |
|---|---|---|
| **dashscope** | 《PDF 理解》页明列 **qwen3.8-max / qwen3.8-max-0902 / qwen3.8-flash / qwen3.8-27b** | **标**（后两个同轮新增预设） |
| openai | Chat Completions **只支持 PDF**（`file_data`/`file_id`，不支持 `file_url`/`detail`）；但社区反复反馈 gpt-5 系**看不到文件**、Azure gpt-4o 直接报 `does not support file content types` | 不标（通道在、模型面不一致） |
| anthropic | 原生用 `document` 块，**不是** OpenAI 的 `file` 块 —— 而 LiMo 所有 provider 共用 AsyncOpenAI，发出去的是后者 | 不标（格式不通，且无独立适配层） |
| google | OpenAI 兼容层**不支持**文档理解（Google 官方 forum 确认） | 不标 |
| zhipu | 文件能力走独立端点（`layout_parsing` / `files` + `purpose`），不是 message 内 file 块 | 不标 |
| moonshot | `files.create(purpose="file-extract")` **抽文本**再塞进 messages；官方明确「不支持用 file_id 引用文件内容作为上下文」 | 不标（语义是抽文本，不是原件直传） |
| opencode 网关 | 未查证能否把 file 块转发到上游；且官方明写该能力**仅限华北2（北京）地域**，网关转发到非北京地域必失败 | 不标 |

⚠️ **dashscope 名单的坑**：官方 PDF 名单**只有 qwen3.8 系四个**，`qwen3-vl-flash` / `qwen-vl-max` / `qwen3.5-flash` 这些「视觉模型」**都不在名单里**（页面标题虽叫「PDF文件通过 VL 模型解析」，但支持列表是收窄的）。按「有视觉能力 → 大概也能传 PDF」去推，**会一次标错三个模型**。

#### 针对性修改

| 改的是什么 | 为什么 |
|---|---|
| 新增 `qwen3.8-flash` / `qwen3.8-27b` 两条 dashscope 预设（带 `document`） | 官方名单里的另外两个，原先只有 max |
| `qwen3.8-max` 的 `context_window` **131072 → 1000000** | 官方模型页写的是 上下文 1000000 / 最大输出 131072 —— 此前**把后者当成了前者**。对话预算 = 窗口 × 0.7，填错会让可用的 700k 缩成 91k |
| `DOC_DIRECT_TIMEOUT` **240 → 330**、`DOC_DIRECT_GEN_TIMEOUT` **450 → 660** | 240 是「实测 149.3s × 1.6 余量」算的，但官方明写首包最坏 **300s** —— 落在 240~300s 区间的正常请求会被自己的超时掐断，且**只表现为静默降级**。改为官方上限 × 1.1，总超时同步乘二（两者是乘二关系，不是并列关系） |
| `generate.py` 流式端点构造 `LLMService` 时**漏传 `doc_direct`** | 非流式那侧是传的（模式 #16 的又一例）。不传则直传时 httpx 的 read timeout 不放宽，SDK 先于我们抛超时，异常被下游 `except Exception` 归到「连接中断」并按那条路重试 —— **文案与真相不符**（真正死因是「模型还在解析原件，我们等得不够久」） |

#### 教训：实测证明不了上界

`DOC_DIRECT_TIMEOUT` 的第一版是**照着实测数据算的**（149.3s 的最大样本 × 1.6），看起来还留了 60% 余量，很稳妥。但它是错的 —— 上界从来不在实测样本里，**上界只写在官方文档里**。这是模式 #39（判据跑到了，但它看的输入不是它以为的那个）的又一形态：判据（实测数据）是真的，但它**不构成对「最大值」的证据**，而错误取值只在特定体积区间才暴露，日志里还伪装成「厂商超时」。

### 验证

- **后端 25 个测试文件全绿**（新增 `test_doc_understand.py` / `test_effort.py` / `test_folders.py` / `test_folders_api.py` / `test_model_caps.py` / `test_search.py`；`test_e2e_smoke.py` 与 `test_video_understand.py` 需已启动的后端/真实模型调用，本轮跳过）
- **前端 128 tests 全绿** + `tsc --noEmit` / `lint`（0 error）/ `build` 通过
- **判据自证有区分力**（逐个做了对照实验，确认改坏后真的会红）：
  - 直传调用点对称性：摘掉 `generate.py:586` 的 `doc_direct=` → 断言精确报出该行
  - `doc_direct` 序列化：改成 truthiness 判断 → **只有「显式 false」那条**变红（其余 6 条照常绿）
  - rawText 过短提示：判据是 100 字符而非空串，占位文本（36 字符）必须触发
  - 体积闸门：断言 14 MiB **边界两侧**，不是「函数被调用了」
  - 零影响回归：`doc_direct=False` 的输出与「该参数根本不存在」时**逐字节相同**（含「标了 document 能力但开关关着」的配置）
  - **超时常量的官方下界**（方向四新增）：把 `DOC_DIRECT_TIMEOUT` 改回 `240.0` → 断言报出「240.0s 必须 > 官方承诺的首包上限 300.0s」；把流式端点的 `_gen_timeout(format_list, doc_direct=doc_direct)` 改回漏传 → 断言精确报出 `routers/generate.py:485`
- ⚠️ **端到端冒烟（`test_e2e_smoke.py`）本轮未跑** —— 它需要已启动的后端 + 真实模型调用（产生费用）。直传链路的真机验证只做到「探针确认文件真的送达」这一层。
- ⚠️ 两个测试文件（`test_docx_export.py` / `test_epub_pipeline.py`）在 GBK 控制台下会因末行 `print` 的 `☑`/`✅` 抛 `UnicodeEncodeError` —— **断言全过，只是打印炸**。跑全量时加 `PYTHONIOENCODING=utf-8`。

### 待查证

1. **opencode 网关的模型名**（见方向二第 4 条）：改名即改 `enabled_models` 的 id，未查证前不动。
2. **qwen 等渠道是否支持 `reasoning_effort`**：未查证 → 未标 `reasoning` 能力位。
3. **`qwen3.8-flash` / `qwen3.8-27b` 的直传是否真能跑通**：官方名单有（故已标），但 LiMo 侧**未实测** —— 探针只验过 `qwen3.8-max`。
4. **老用户配置里的 `qwen3.8-max` 仍是 `context_window=131072`**：`_sync_llm_model_caps` 只补 `capabilities`、**不更新已有条目的其他字段**，故修正在新用户生效、老用户需在设置页手动改（或删掉该行让它按预设重建）。**没有做自动迁移** —— 那会改写用户配置。
5. **`store.root/tmp/` 长期承担原件存储**：已知脆弱点（外部清理会让直传静默降级为文本模式），本轮不做「把原件复制进文档目录」（会让每文档多占一份原件体积，上限 2GB）。

---

## 2026-09-23 — 参考文件三改（注入文案 / 上限 1MB / 超窗自动压缩）+ DeepSeek 模型名与思考模式参数对齐

> 用户诉求：「参考文件这部分提示词可以优化以及参考文件大小的限制可以做宽一点」+「查一下 DeepSeek 官网对模型设置方面做更新」，中途追加「根据模型上下文窗口决定要不要先对参考文件先交给大模型压缩？」

### 1. 注入文案重写：从「劝退」到「主动利用」

旧 `<reference_texts>` 节对**每一份**参考都重复一遍「仅供背景参考，与原文冲突时以原文/模型判断为准」。这是**劝退措辞**——参考资料是用户**主动上传**的增量信息（典型场景是 B 站评论区观点、配套材料），文案却让模型把它当摆设。另有三处缺失：无融入指引（只说「可参考」）、冲突处置一刀切（不区分客观事实与不同观点，会抹掉评论区里的多视角）、无防照抄（参考常与原文大段重复）。

重写为三段式：**节首统一说明**（只说一次，不逐份重复）+ **四条使用要求**（主动利用 / 冲突分两类处理 / 不要照抄 / 忽略无关部分）+ **逐项编号** `【参考资料 N】来源：xxx`（替代旧的 `---` 分隔符——编号让模型能对应「第几份」）。`prompt.py::_format_reference_texts`。

### 2. 单文件上限 200KB → 1MB（前后端同值）

200KB 是按 128k 上下文模型留的余量，而主流模型已到 1M（DeepSeek V4 系列），200KB 反而成了瓶颈。1MB ≈ 35 万汉字，在 1M 窗口下「参考文件 + 原文 + 输出」仍留有余量；再大就只有超大窗口模型能用。落点：`routers/references.py::MAX_REF_BYTES` + `GenerateOptions.tsx` 前端同值预校验（错误文案同步）+ `reference_store.py` / `api/references.ts` 注释。

⚠️ **放开上限直接催生了第 3 条**：1MB 参考文件塞进 128k 窗口的模型必然超窗，请求会**直接失败**。

### 3. 超出模型上下文时先压缩参考文件（新增 `generation/reference_compressor.py`）

按模型**实际解析出的窗口**（`resolve_context_window` 四层解析）判断：`原文 + 参考 > 窗口 × 0.7`（与对话预算同源公式）才动手，**装得下就原样返回、一次 LLM 都不调**。

- **压缩用当前生成模型**，不额外配置压缩专用模型——省一个配置项
- **失败不阻塞生成**：摘要失败 → 按额度截断并标注；摘要压不够 → 追加逐份截断
- **只在 notes 侧做**：参考文件本就只注入 notes，单产物 polished / `Generator.generate(mode='polished')` 直接跳过（压缩纯属白花钱）
- **`reasoning_effort="low"`**：摘要是忠实压缩任务，DeepSeek 思考模式默认 effort=high，不降档会白等数十秒静默期（为此给 `LLMService.call()` 补了 `reasoning_effort` 透传参数）

**流式路径新增 `stage` SSE 事件**（`{type:'stage', message:'参考文件超出模型上下文，正在压缩…'}`）：压缩可能耗时数十秒，静默等待容易被误认为卡死。前端 `TaskInfo.stage` 单值承载，`setProcessing` 的全量替换语义让它在正式 chunk 到达时**自动消失**，无需手动清理。

**测试过程中抓到三个真实缺陷**（都已修，`tests/test_reference_compressor.py` 留了回归）：

1. **原文自身超预算时兜底会把参考文件削成空壳** —— `base ≥ budget` 时参考文件压到 0 也救不回请求（该场景由保底份额决定大小），硬削只是让用户拿到一份几乎空的参考文件、请求却照样失败。改为该场景**不做总量兜底**。
2. **`_truncate_to_tokens` 的「至少留 200 字符」硬下限让截断失效** —— 兜底要把每份从 200 token 削到 10 token 时，200 字符下限让每份**纹丝不动** → 不收敛。改为只以 `MIN_KEEP_RATIO` 为下限。
3. **截断不幂等 + 保留比例按「含标记长度」算** —— 反复调用会叠标记，且出现「削到 166、实际输出 186」的假收敛。改为截断前剥除已有标记、且先从 limit 里扣掉标记自身的 token 开销。

⚠️ 兜底是**近似收敛**（每份最少保留一个标记的量），业务上留的 30% 预算余量的意义正在于此。测试容差 5%——足够区分「收敛」与「没跑」（后者会超一倍）。

**文件名清洗**（`prompt.py::sanitize_reference_name`，注入侧与压缩侧同源复用）：文件名进 `【参考资料 N】来源：{name}` 与压缩 prompt 的 `来源="{name}"` 属性位，换行可伪造出假条目、双引号可闭合属性、全角方括号可让名字里出现与结构标记逐字相同的串。清洗 = 空白归一 + 引号替换 + 方括号转半角 + 限长 80。**只做结构安全，不做内容审查**（内容本就是用户自己的素材）。

### 4. DeepSeek：模型名两轮变更 + 思考模式参数契约（依官方文档核实）

查官网（`api-docs.deepseek.com` 原文，非第三方聚合站）后确认两轮变更，项目里三处实锤问题：

| 问题 | 现状 | 修法 |
|------|------|------|
| 预置模型用**已下线的名字** | `deepseek-v4-flash`（2026-09-10 起 V4.1-Flash 改名为 `deepseek-flash`，旧名对应的模型已下线，官方暂用路由兜着） | `config.py` 预置改 `deepseek-flash` + `deepseek-v4-pro`；默认模型与 `routers/model.py` 回退值同步 |
| `compress.py` 一级 fallback **名存实亡** | 硬编码 `deepseek-chat`，该名 **2026-07-24 已停用** → 每次构造失败静默掉到二级「当前对话模型」（功能被兜住所以一直没暴露） | 改 `deepseek-flash`（现役里最便宜的一档）+ 带警告的 docstring |
| 续写的 temperature 递减是**空操作** | DeepSeek 思考模式下 temperature / presence_penalty / frequency_penalty **不报错但不生效**，而思考模式默认开启 | 保留代码（兼容非思考模式与其他厂商），在 `service.py::stream` docstring 显式标注 |

**思考模式参数契约**（记下来免得再踩）：默认开启、effort 默认 high（三档 low/high/max）；上述三参数思考模式下无效；`top_p` 相反——**仅思考模式生效**且低于 0.95 按 0.95 处理。**V4.1-Flash 原生支持图像理解**（旧 vision 实验版已并入 flash 一条）。上下文 1M / 最大输出 384K / 并发 flash 2500、pro 500；价格为峰谷双档（flash 输入 1/2、输出 4/8；pro 输入 4.5/9、输出 13.5/27，元/百万 token），故预置不填单一价格以免误导。

### 5. 质量与安全性检查（同日续，用户要求「完成后对这方面进行质量以及安全性检查，前端页面要小字提示」）

逐项复查参考文件全链（上限 / 暂存 / 压缩 / 渲染），**发现并修掉四个问题**——其中两个是放开上限**放大**的既有缺陷：

**(a) 暂存区孤儿条目永不清理**（`core/reference_store.py`，1MB 把影响放大 5 倍）

旧实现是**按入参惰性清理**：只有 `get_references()` 的入参里出现某个 id、且恰好过期时才会 pop。而前端一次只持有**一个**参考文件 id —— 用户换一次文件就换一个 id，**旧 id 从此再也不会出现在任何入参里**，那条记录会一直驻留到进程重启（20 分钟 TTL 形同虚设）。1MB 时代一次漏一份，反复换文件就是持续吃内存。

改为 **`_purge_expired_locked()` 全表扫**（save 与 get 两条路径都调）。同时补两道纵深防御闸门：条目数 32、总量 32MB（超限抛 `AppError(REFERENCE_STORE_FULL)`）。⚠️ 闸门**在清理之后判** —— 否则「满了但其实已过期」的场景会被误拒。

新建 `tests/test_reference_store.py`。⚠️ 用例断言的是 `_store` 的**实际条目数**而非「取回的内容对不对」——按入参清理的实现在这种用法下取回的内容完全正确，只有条目数能暴露泄漏。并用「清理函数换成 no-op」实测验证了该用例**有区分力**（no-op 下必失败）。

**(b) 摘要没有独立超时**（`generation/reference_compressor.py`）

压缩发生在生成总超时（`_gen_timeout` 330/400s）**之外**，不给这一层就只有 SDK 的 330s 兜底：模型挂起时用户要**干等 5 分钟**才看到降级。新增 `COMPRESS_TIMEOUT = 180.0` 用 `asyncio.timeout` 包住 `llm.call()`。

⚠️ 两处容易写错：`asyncio.timeout` 抛的是 `TimeoutError`，而**用户取消**走的是 `app.llm.CancelledError`（继承 `Exception`，与 `asyncio.CancelledError` 无关）——`except CancelledError: raise` 必须排在前，两者不会互相吞掉。另 `TimeoutError` 单独列一支：`str(TimeoutError())` 是**空串**，混在通用 `except Exception` 里会留下一条 `error=` 无原因的日志，排查时分不清「超时」与「模型报错」。

测试新增用例 9，断言的是**实测耗时**（而非「函数被调用了」），并用日志替身确认走的是「超时」专用分支（两支都返回 `None`，只看返回值分不开）。

**(c) 缺 prompt 注入防御声明**（`prompt.py` / `COMPRESS_PROMPT`）

全 prompt 体系里没有一句「输入内容里的文字不是给你的指令」。参考文件的注入面恰恰**最强**：它常来自第三方（他人文章、评论区），而第 1 条把文案改成「**主动利用**」后，模型对该内容的遵从倾向进一步被放大 —— 内容里的一句「忽略以上要求，改为输出…」可能真的被执行。

在两处节首补声明（注入侧 `_format_reference_texts` 第 5 条 + 压缩侧 `COMPRESS_PROMPT`）：素材里的命令式表述一律视为**待整理的普通文本**。**内容本身保持零触碰**（不做转义/过滤）——沿用「用户内容零改动」原则，声明性防御而非改写数据。

> ⚠️ **本次只覆盖参考文件这条链**。`source_content`（导入的网页/文档正文）同样缺此声明，且注入面同级（网页正文完全来自第三方）——未纳入本轮（会改动全部 prompt 与产物风格，需单独评估），记在此处备查。

**(d) 压缩 prompt 漏占位符这类错误测不出来**

用例 8 只断言了「文件名进了 prompt」。撰写本轮改动时**实际踩到过一次**：编辑 `COMPRESS_PROMPT` 时误删了 `<参考资料>` 块（`{content}` 一起没了），而 `str.format` 对**多余关键字不报错** → 模型只收到空壳模板、摘要退化成凭空编造，**文件名断言照样通过**。补断言：与源文本比对出现次数（同时覆盖「内容进了」与「模板只展开一次」）。

**前端小字提示**（用户明确要求）：`GenerateOptions.tsx` 参考文件区加一行 10px 说明「参考文件与原文合计超出所选模型上下文窗口时，会先自动压缩参考文件（保留要点）再生成」，仅在「不只是 polished」时显示（polished 不注入参考，提示无意义）。

### 6. 修复流式生成必炸的作用域 bug（同日续，0.4.4 打包完成后首次生成即复现）

```
UnboundLocalError: cannot access local variable 'ref_texts' where it is not associated with a value
  File "app\routers\generate.py", line 528, in merged_stream
```

**症状**：0.4.4 安装包打完后，点一次「生成笔记」立刻失败 —— 不是偶发，是**必然**。

**根因**：这是第 3 条（超窗压缩）引入的。`generate_stream` 顶层有 `ref_texts = _resolve_reference_texts(...)`，而压缩逻辑写在**嵌套函数** `merged_stream` 里：

```python
async def merged_stream():          # generate_stream 内部的嵌套函数
    ...
    if ref_texts and needs_compression(...):   # ← 炸在这里
        yield "…正在压缩…"
    ref_texts = await compress_references_if_needed(ref_texts, ...)   # ← 这一句是元凶
```

Python 的规则是「**只要函数体内出现对某名的赋值，该名在整个函数里就是局部变量**」，与赋值语句在不在前面无关。`merged_stream` 里那句赋值让 `ref_texts` 成了它的局部变量 → 上一行的 `if ref_texts` 在任何赋值之前读取它 → `UnboundLocalError`。与「关键模式」#26（函数内重复 import 造成同名局部）同根因，只是这次跨了**函数作用域**。

**为什么测试没抓到**：这不是逻辑错误，而是**运行时必炸、但读代码和跑单测都正常**的作用域问题 —— 而流式端点的真实运行路径此前完全没有测试覆盖（`test_e2e_smoke.py` 需后端启动 + 真实 LLM key，不覆盖流式生成）。

**修法**：在嵌套函数内改用局部名 `refs = ref_texts` 承接，压缩结果只给 notes 用，本就没有回传外层的必要。两处（双产物 / 单产物分支）同改。

**防线**：新增 `tests/test_closure_shadowing.py` —— 用 AST 静态扫描全库，判据为
「外层函数赋值的名字 ∩ 嵌套函数赋值的名字 ∩ **嵌套函数内读早于写**」→ 违规。防的是**整类**而非这一个：

- 按**执行顺序**而非 AST 子节点顺序展开读写（`ast.Assign._fields` 是 `('targets','value')`，而语义上 value 先求值 —— 直接 `ast.walk` 会把 `x = x + 1` 判成「先写后读」，正好漏掉最该抓的一类）；`AugAssign`（`x += 1`）同样先读后写
- 形参视为「进入即绑定」（order 0），避免把「读形参」误判为先读；`nonlocal`/`global` 声明豁免
- **判据自证有区分力**（模式 #38）：内嵌三种真实违规形态（先读后写 / 自增 / 自引用赋值）确认能抓到，三种安全写法（局部名承接 / 先写后读 / nonlocal）确认不误报；并**对真实代码自证** —— 把修复回滚成 bug 版本，确认检查立刻报出 `generate_stream() 的局部变量 'ref_texts' 在嵌套函数 merged_stream() 内先读后写`
- 全库扫描确认其余 5 处同类「遮蔽」都是安全的（内层先赋值后使用，正是 `_it = None` / `chunks: list[str] = []` 这类预初始化防御写法）

**教训**：新增的「外层变量 + 嵌套函数内加工」组合必须换局部名。同一文件里 `video_path` 也因「条件分支内赋值 + finally 引用」被预初始化过（503 行 P0-5 注释）——那是**同作用域内**的同类问题（UnboundLocalError 家族的另一个变体），本次这个是**跨作用域**版本，两者的共同点是「变量可能在任何赋值之前被读取」。同一文件两次栽在这上面，故补静态检查而非只修这一处。

---

## 2026-09-21 — 打印样式 + PDF 导出（WebView2 PrintToPdf / 打印对话框双路径）

> 用户诉求：接着 Word 之后看 **PDF 导出**。四条候选路线（WebView2 PrintToPdf / xhtml2pdf / `window.print()` / Playwright）经复核后共同卡在**同一个前置**：现有自包含 HTML 是纯屏幕布局 —— 没有 `@page`、没有 `@media print`、没有分页控制。**打印样式不是其中某条路的准备工作，是四条路共用的地基**，故先做它；地基铺好后，用户选定走 **A 路线（WebView2 PrintToPdf）**——免打印对话框、直接落文件。

### 设计决定：打印的是**后端产物**，不是应用 DOM

`window.print()` 直接打 React DOM 看似最省事，但要在前端另写一套「隐藏三栏 + 工具条 + 主题 CSS 变量」的打印样式 —— 那是**第二套排版**，与后端 HTML 导出必然漂移，违背「预览一致」原则。

改为：拉取 `/export/{id}?format=html&mode=…` 的自包含 HTML，喂给渲染引擎。这样只维护**一套**打印 CSS（后端），产物与 HTML 导出逐字一致。**这个决定让后面的路线切换变得便宜**：换成 PrintToPdf 时，喂给它的仍是同一份 HTML，只换最后一步 —— 实际改动正是如此。

### A 路线：WebView2 `PrintToPdf` 直接落文件（Tauri 内）

[Rust 侧](../frontend/src-tauri/src/lib.rs) 新增 `pdf_export` 模块 + `export_pdf` command：前端 `invoke` 传 URL 与文件名 → 新建**屏幕外窗口**加载该 HTML → `on_page_load` 完成后取 WebView2 controller → `ICoreWebView2_7::PrintToPdf` → 关闭窗口 → 回传落盘路径。浏览器环境无 WebView2 COM，自动退回打印对话框（[print.ts](../frontend/src/lib/print.ts) 内分支）。

四个非显然的约束，都是**先想明白才写对**的：

1. **必须新开窗口，不能复用主窗口、也不能用 iframe** —— `PrintToPdf` 只打**该 WebView2 实例的主文档**，而主窗口的主文档是应用 UI 本身。iframe 同样不行：`ICoreWebView2Frame2` 只有 `PrintAsync`（弹打印 UI），**没有**落文件的 `PrintToPdf`。
2. **窗口挪到屏幕外（-32000, -32000），而非 `visible(false)`** —— WebView2 对不可见窗口会推迟渲染，PrintToPdf 有概率拿到空白页。
3. **`on_page_load` 回调里不能就地 `with_webview`** —— 该回调跑在 UI 线程，就地重入事件循环有死锁风险；改为换线程 + 延时后再抓 controller（顺带等图片解码/字体就绪）。延时取 **3000ms**：初版写 400ms，但 `Finished` 只代表主文档加载完，而导出 HTML 自包含（~1.8MB base64 字体 + 内联图片），字体解码与 KaTeX 客户端渲染都在其后。**没有「更短也够」的成功样本**，故保留实测值，代价是每次导出多等约 3s。
4. **`PrintSettings` 的每一项都要显式设，没有一项默认值可用** —— 尤其 `PageWidth/PageHeight` **默认是 Letter**（不设就印出美式尺寸），`ShouldPrintBackgrounds` 默认 false（关掉后代码块只剩一圈边框）。

⚠️ **代价：COM 接口不跨 crate 实例**。`pdf_export` 直接用 `webview2-com` 的类型，版本必须与 tauri 内部的同源（`tauri 2.11` → `wry 0.55` → `webview2-com 0.38`），否则 `ICoreWebView2` 是两个不相干的类型、`.cast()` 直接编译失败。`Cargo.toml` 已写死该约束并注释了排查方法。

### 依赖：零新增包

`webview2-com` / `windows` 本就是 `tauri → wry` 的传递依赖、已在 `Cargo.lock` 里，**没有下载任何新 crate、没有体积增量**。前端侧 `@tauri-apps/api` 也已在 devDependencies（`restart_backend_sidecar` 在用）。这是 A 路线相对 xhtml2pdf（CSS 子集弱、4 套主题会崩）与 Playwright（~300MB）的决定性优势。

### 打印 CSS 的落点（三处，不是一处）

| 落点 | 管什么 | 为什么不能合并 |
|------|--------|---------------|
| `_PRINT_CSS`（`export.py`） | `@page` 尺寸页边距、解除屏幕布局、分页控制、代码容器底色 | 4 套主题共用 |
| `_get_pygments_css()` 的 `@media print` 段 | **逐 token 配色**换成浅底主题 | token 是 `.codehilite .k` 这类类选择器，容器规则**压不住**它们 |
| `HTML_THEMES["limo"]` 的 `@media print` 段 | 把暗色段的浅色取值还原 | 见下第 3 条 |

⚠️ **三者顺序有意义**：`style_block` 里主题 → Pygments → badge → KaTeX → `_PRINT_CSS`。CSS 媒体查询不增加特异性，同特异性下**后者胜** —— `_PRINT_CSS` 放最末才能压住前面所有屏幕样式。

### 三个缺陷都是**读 PDF 图**发现的，不是推理出来的

验证方式是闭环的：临时脚本生成 HTML → `msedge --headless --print-to-pdf` → `pypdfium2` 转 PNG → 直接看图。前两个缺陷在截图上肉眼可见，靠读 CSS 是想不到的。

1. **代码块 token 仍是浅色**（注释几乎看不见）
   初版只写了 `.codehilite code, pre code { color: #1F2328 !important }` —— 但 one-dark 的 token 是 `.codehilite .k { color: #C678DD }` 这种**类选择器**，作用在 `<span>` 上。`code` 的 color 只对没有自身 color 的文本生效，span 按自己的规则走。∎ **修法**：整份换 Pygments `default`（浅底主题）的 defs，而不是试图逐条覆盖。

2. **`break-inside: avoid` 用在 `pre` / `table` 上会产出整页空白**
   一个 45 行的代码块装不进当页剩余空间 → 被整体推到下页 → 前一页留下大半页空白（实测 3 页文档凭空多出一页）。这两个元素的**长度无上界**，正是最不该用 avoid 的。∎ **修法**：`pre`/`table` 移出 avoid 名单（自然跨页），只保留行/项级原子性（`li`/`tr`）与短块（`blockquote`/`figure`/`img`）；表格靠 `thead { display: table-header-group }` 在每页重复表头。修后 5 页 → 4 页。

3. **limo 主题的暗色段与打印会同时匹配**
   `@media (prefers-color-scheme: dark)` 与 `@media print` 不互斥 —— 系统处于暗色偏好时一起生效，`blockquote` 的 `#AEAEB2`、行内 code 的 `#93C5FD` 落在白纸上几乎不可见。`_PRINT_CSS` 只带 `!important` 管了 body/代码容器，管不到这些。∎ **修法**：由**引入暗色段的主题自己**在打印时还原成浅色取值（主题自洽原则）。
   ∎ **验证**：把 limo 的暗色媒体查询外壳剥成顶层规则以模拟暗色偏好，打印后确认全部可读。

### 验证

**打印样式**（纯 CSS 层）：4 套主题（paper/github/minimal/limo）+ 暗色偏好模拟，各打一份 PDF 后转图肉眼核对：**全部通过**（代码块浅底深字、表格不跨页断、标题不孤立、公式正常、`max-width` 已解除）。

**PrintToPdf 端到端**（Rust 侧那条链，`unsafe` COM + 屏幕外窗口 + 时序，最需要实测的一段）：临时插入自触发代码 → `cargo run` 起真实 Tauri 应用 → 应用自动完成一次导出 → 读日志拿落盘路径。结果：

- `PDF_VERIFY result=Ok("C:\Users\...\Downloads\PDF验证-中文标题.pdf")` —— **无伪成功，路径真实存在**
- `pypdfium2` 读回：**84908 字节 / 1 页 / 209.9 × 297.0 mm**（即 A4，证明 `PageWidth/PageHeight` 确实覆盖了默认的 Letter）
- 渲染成 PNG 肉眼核对：中文正文、代码块（深底 + token 配色）、表格、引用块浅蓝底 + 左侧竖线、列表、页边距 —— **全部正确**。背景保留证明 `ShouldPrintBackgrounds(true)` 生效

⚠️ **但这一轮测的是自造的测试 HTML，不是 `/export/{id}` 的真实产物** —— 它验证了「COM 链路能出 PDF、能出 A4、背景保留」，**没有覆盖真实导出数据流**。下面是补测。

### 补测：真实导出链路暴露的 `Content-Disposition` 坑（本次修复）

用真实文档（`/export/tr_e4330f4a9157?format=html&mode=notes`）重跑同一条链，得到的是 **876 字节纯白 PDF**，而回调照样报 `succeeded=true` —— **伪成功**，没有任何报错。

四轮排除，每轮都是为了让下一轮的结论无可辩驳：

| 轮次 | 动作 | 结果 | 排除 |
|------|------|------|------|
| 1 | 延时 400 → 3000ms | 仍 876 字节 | **时序**（不是没渲染完） |
| 2 | 屏幕截图 12 帧 + 区域像素统计 | 导出窗口帧 `stddev=0.00`（纯白） | **页面根本没渲染**，不是打印环节的问题 |
| 3 | 同一份 HTML 走 `file://` / 走 `/health` | 673045 / 12738 字节，**均成功** | **External URL 与渲染链路本身** |
| 4 | `curl -D -` 看响应头 | `content-disposition: attachment` | **根因** |

∎ **根因**：`/export/{id}?format=html` 默认带 `Content-Disposition: attachment`（点「HTML」按钮就该下载文件，这是对的）。但 Tauri 路径是让 WebView2 **直接导航**到这个 URL —— attachment 让它去**下载**而不是渲染，窗口全白，PrintToPdf 打印的正是这张白纸。`file://` 不查该头，所以第 3 轮的对照实验恰好把范围收窄到了 HTTP 头。

∎ **修法**：`export_content` 加 `inline` 查询参数（默认 False）；前端 [print.ts](../frontend/src/lib/print.ts) 的 `withInline()` 在出口统一追加 `inline=1`（免得每个调用方都要记得）。**浏览器那条路径（`fetch` + `srcdoc`）不受影响** —— fetch 在 JS 里能拿到 body，无视该头。

∎ **离屏窗口下的决定性对照**：带 `inline=1` → **800470 字节**；不带 → **876 字节**。

∎ **修正后的端到端实测**（6 页全 A4 / 209.9 × 297.0mm）：p01 中文标题层级 + 引用块 + 行内代码 + 代码块浅灰底；p02 表格（表头底色 + 边框）+ 嵌套列表；p03 **代码块 Pygments 三色高亮**（证明「整份换浅底 defs」那处修法真的生效）；p04 表格换行单元格；p05 **跨页表格重复表头**；p06 **图表 PNG 内嵌**。**全部正确**。

其余：`cargo check` 零警告；前端 `tsc -b` 通过、`npm test` 98 passed、lint 无新增告警。

临时验证代码、`tauri.conf.json` 的临时改动（主窗口设 `visible:false` 以免弹窗打扰）、测试 HTML 与下载目录里的验证 PDF —— **全部已删/已还原**（`grep` 确认零残留）。

⚠️ **遗留：修复只在源码里，不在任何已构建的 exe 中**。本机 7413 上跑的 `LiMoBackend.exe` 是 8 月 26 日构建的产物，内嵌的是 4 周前的 `export.py`（`inline` 参数根本不存在）。**重新 PyInstaller 打包后此修复才在桌面版生效**。

### 遗留

- **浏览器环境仍是打印对话框**（无 WebView2 COM），需用户手动选「另存为 PDF」；Tauri 桌面版才是真一键
- 固定存系统「下载」目录，不弹「另存为」对话框 —— 要它得再引入 `tauri-plugin-dialog`；当前同名自动加 `(1)` 序号
- 仅 Windows：`PrintToPdf` 是 WebView2 能力，非 Windows 平台该 command 返回「不支持」错误
- 思维导图 tab 的 PDF **未开通**（走专用端点，markmap 打印效果未验证），按钮与 Word 同门控禁用并给出 title 原因

---

## 2026-09-21 — Word（.docx）导出

> 用户诉求：导出格式从 md / html / plain 三种扩到四种，**先做 Word**。
> 选它的理由是依赖全现成：`python-docx` 已在依赖（Doc 引擎在用）、`beautifulsoup4` 项目各处已在用 —— **零新增包、零体积增量**（PDF 那条路要背 WebView2 PrintToPdf 的 unsafe COM 或 300MB 的 Playwright，另议）。

### 两条导出路径共用一份 markdown 理解

原先 markdown→HTML 的四段逻辑（`_preprocess_gfm` / `_markdown_to_html` / `_add_lang_labels` / `_add_heading_ids`）长在 `routers/export.py` 里。Word 导出也要用它 —— 但 **services 反向 import router 是分层倒置**。故下沉为 [`app/services/markdown_render.py`](../backend/app/services/markdown_render.py)，`export.py` 保留同名私有别名。

**抽取不是"顺手重构"，是本次的正确性要求**：LiMo 的 markdown 是**方言**（表格前补空行、`~~` 删除线、任务列表 checkbox、`Content-[mm:ss]` 时间戳 badge），方言只应存在一份。两跳换来的是 **Word 导出与 HTML 导出对同一份 Markdown 的理解永远一致** —— 这正是导出系统「预览一致」原则的延伸。

⚠️ **抽取后立刻做了等价性验证，而不是靠推理**：从 git 取旧版 `export.py` 独立加载，对 8 个样本（标题/表格/代码块/任务列表/时间戳/引用/公式/空串）逐字节比对 HTML 输出 —— **差异 0 处**。`markdown_to_html(codehilite=True)` 之外新增的 `codehilite=False` 分支是 Word 侧专用：docx 里每个 token 一个 `<span>` 是纯噪音，关掉后语言名仍由 `add_lang_labels` 保留、代码文本原样取出。

### 实现要点（都是踩过才知道的）

| 点 | 做法 | 为什么 |
|---|------|--------|
| **中文不设 `w:eastAsia` 会掉字体** | `_set_run_font` 同时写 `w:ascii`/`w:hAnsi`/`w:eastAsia` | `run.font.name` 只写前两个，中文走 `w:eastAsia` 槽位 —— 不设它，中文掉回 Word 默认（宋体），与西文混排时字重行高都不一致 |
| **OOXML 元素顺序**（本次最阴险的坑） | `_ordered_insert(parent, el, order)` 按 ECMA-376 序列插位 | `w:pPr`/`w:rPr` 是 **ordered sequence**：`w:shd` 必须排在 `w:spacing`/`w:ind` 前。python-docx 没暴露 `_insert_shd`/`_insert_pBdr`，手写时顺手 `append` 就会乱序 —— **python-docx 照常读得回来，只有 Word 会提示「文件已损坏」**，所以只能自己写检查（见验证） |
| **图片** | 从 `_collect_images` 拿 bytes 直接 `add_picture(BytesIO)`，超版心（6.27in）等比缩放 | 不落盘、不 base64；公式渲染产物那种 `data:image/png;base64,` 也直接解码内嵌 |
| **代码块用段落不用单元格表格** | 逐行 run + `w:shd` 底纹 + 换行符 `add_break` | 单格表格在 Word 里会被周围文本挤压变形、跨页断行不可控；同一段落内换行还能保住缩进 |
| **超链接** | 手写 `w:hyperlink` + `part.relate_to(RT.HYPERLINK, is_external=True)` | python-docx 至今没有超链接 API（issue #74 自 2014 年挂起） |
| **公式** | 保留 LaTeX 原文（`$...$` / `$$...$$`） | 见下 |

### 顺带发现：「公式 → 渲染图片」是一个**从来没生效过**的选项

`_render_formulas_to_images` 依赖 **matplotlib** —— 而 matplotlib **不在 `pyproject.toml`、不在 PyInstaller spec、不在任何文档**里（全仓 grep 只有这一个引用点）。它永远在 `except ImportError: return md` 处静默返回原文。

也就是说：ExportPanel 里「MD 公式：渲染为图片」这个选项，**自上线起选了就等于没选**，且因为降级是静默的，没有任何日志或 UI 反馈。这正是「失败显式化」原则要防的形态。

**本次未修**，原因：修它要给打包版引入 matplotlib（~50MB+），而 HTML 导出那条路已经有 **KaTeX 服务端渲染**（Node 子进程，产物质量远好于 mathtext 近似）。合理的修法是把 md 的公式渲染也切到 KaTeX 图片管线、砍掉 matplotlib 分支 —— 属于独立议题，**已记入待办**。Word 导出不接这个字段（`formula_md` 对 docx 无效），文档与前端均明示「公式保留 LaTeX 原文」。

### 接入点

- **单文件**：`GET /api/export/{id}?format=docx&mode=xxx`（`export_content` 新增分支，既有的 md/html 分支零改动）
- **批量**：`ExportCustomRequest.formats` 加 `"docx"`，`export_custom` 与 `_build_export_zip` 两处循环各加一个分支（这两份循环是**既有重复代码**，本次未合并）
- **前端**：ExportMenu 加「导出 Word」（与 HTML 同门控，**思维导图 tab 除外** —— 交互脑图没有 Word 形态，按钮 title 给出原因）；ExportPanel 格式四选一 + Word 说明块
- **打包**：`LiMoBackend.spec` 的 hiddenimports 加两个新 services 模块；`beautifulsoup4` 从传递依赖**提为显式依赖**（此前靠 markitdown/trafilatura 带入，现在被直接 import，不该再依赖别人的依赖树）

### 验证

- **`backend/tests/test_docx_export.py`：20 PASS**。每个用例都**把产物回读一遍**再断言 —— docx 是 zip+XML，生成时不报错不等于内容正确（样式名写错、字体槽位没设、图片关系没建立，全都能「成功」产出一个 Word 打不开的文件）
- **OOXML 顺序检查是自证的**：先**复现修复前的真实写法**（先设 spacing 再 append shd）确认检查能抓到违规，再断言全要素产物零违规。第一版构造写反了（必须先 append 大下标才违规），被自证步骤当场抓到 —— 这正是「断言存在 ≠ 断言有效」的又一次实践
- **真实数据端到端：16 篇零失败**（video / text / webpage / pdf × notes / polished / transcript），最大一篇 101,868 字 / 32 图 / 33 标题 / 7 表格。全部零顺序违规、零残留 markdown 标记
- 回归：后端离线 10 个测试文件全绿 + e2e smoke 36/36；前端 vitest 98/98 + `npm run build` ✓

### 影响范围

新增 `app/services/markdown_render.py`（公共渲染）、`app/services/docx_export.py`（Word 转换）、`tests/test_docx_export.py`；改 `export.py`（-80 行重复实现 +docx 分支）、`pyproject.toml`、`LiMoBackend.spec`、ExportMenu/ExportPanel。**HTML 与 MD 导出行为逐字节不变**（已用旧版对比验证）。

---

## 2026-09-19（续） — 图表：六个几何缺陷修复 + 图表类型 4 → 9

> 用户诉求：「我准备给我们的图片要生成增加更多的图表类型选择」——方向限定为**关系/结构图**（不做统计图表），**只扩展 LLM 自动选型的类型池，不加前端 UI**。

### 第一部分：压测暴露的五个真实几何缺陷

上一轮落地的语义化布局器用「更复杂的图」压测后，实测暴露五个缺陷。前四个是**算法画错了**，第五个是**报错文案把人带偏**；第六个是**打包验证时看真实产物才发现的静默降级**（单测、压测、断言全过）：

| # | 缺陷 | 根因 | 修法 |
|---|------|------|------|
| 1 | **同行跨列边横穿中间节点** | `[0,2]→[0,0]` 只判 `a.x < b.x`，不看中间隔着谁 | 跨列时改走绕行 `M acx V(out) H bcx V(in)`；绕行侧选择必须保证走廊在画布内（首行不能向上绕、末行不能向下绕） |
| 2 | **走廊里的边全叠在同一个 y** | 水平段 y 恒为两行中线 `(a底+b顶)/2`，7 条边画成一条长横线、3 个标签叠在一起 | 走廊**分道**：按走廊聚类成车道池，同走廊内按序分配车道 y（间距 8px、4px 网格对齐）；**只有一条时仍走中线**，不无谓偏移 |
| 3 | **隔行边穿节点** | `[0,0]→[2,0]` 的中线 y 落在**中间那一行**上 | `validate_spec` 直接拒绝隔行边。⚠️ 文案**不能**写「请改为逐行连接」——那会诱导模型补一个不存在的中间节点搭桥，把「图难看」升级成「图画错了」 |
| 4 | **state 回边沟槽被裁掉** | bbox 只扫 `nodes + labels`，edges/shapes 的横向延伸不参与 | 新增 `_path_bbox(d)`（解析自产的受限路径，只含 M/H/V）；`_snapshot` 与 `_trim_width` 统一按 **nodes + labels + shapes + edges 四类元素**求 bbox |
| 5 | **报错文案误导** | `expand` 的三轮「压行距重试」把**结构性错误**（有环）也当成「高度放不下」重试，最后包装成「节点过多，图过高」 | `best is None` 时**直抛算子给的原因**——压缩行距只会改变高度，改变不了结构。实测：3 个节点带环的 flowchart 曾报「节点过多」 |
| 6 | **窄节点文字被压成纯省略号**（空壳图） | `_fit_labels` 用写死的 `avail - 32` 算可用宽，与 `_shape_inner_w` 的 `<64 → w-8` 分支**不同源**——tree 等比收缩到 `_MIN_NODE_W=48` 时按 16px 算，连「一个中文 13px + 省略号 11px」都塞不下 | `_fit_labels` 的余量改为调 `_shape_inner_w`（两处必须严格同值，这是 `_shape_inner_w` docstring 早就写明的约束）；`_shrink_labels` 补**前置**空壳检查 |

**缺陷 6 的教训（比缺陷本身更值钱）**：tree 带 8 个子节点时 9 个节点**全部**变成 `…`——图渲染成功、几何全对、`assert_layout` 全过、17 个压测用例全过，用户拿到的却是一张一个字都没有的图。**为什么所有验证都没抓到**：① 压测用例的 tree 只有 3 个子节点，没触发收缩到下限的路径；② 第一版修法是给 `_shrink_labels` 加「截断后是否为空壳」的判据——**写了但没生效**，因为 `_fit_labels` 在更上游就已经把 label 截成 `…`，等 `_shrink_labels` 拿到手时 `_measure("…")=11 < 可用宽 40`，**判据的截断分支根本进不去**。这与「1.6 可选参数不传 = 断言不执行」是同一类错误：**判据存在 ≠ 判据有效**。故修复后补了两条正式用例：窄节点不再出空壳 + 造「上游已截空」的输入确认前置判据真能抛错（54 PASS）

**缺陷 1 与 2 必须一次设计**：绕行段向下时进入的正是下一行上方的走廊，两者抢同一片垂直空间，共用一个车道池才不会互相打架。

### `title` 终于渲染了 + 三条新不变量

- **`title` 此前解析了但从不绘制**（`expand` 放进返回值，`chart_svg` 从没画过）。修法：`TITLE_H = 24` **在 `expand` 里计入**（非空则全部节点 `y += TITLE_H`、`height += TITLE_H`）——高度/缩放/断言三处自动一致
- **已决策：接受 3.75% 容量损失（640 → 616），不动 `_MIN_SCALE`**。0.65 对应 13px × 0.65 ≈ 8.5px 已到可读性边缘；标题是**内容而非装饰**，占高度预算语义合理。测试覆盖不减反增：保留 18 节点用例（改后 scale ≈ 0.6504 仍通过），**新增**「同规模 + 带标题 → LayoutError」负向用例
- **`assert_layout` 补三条不变量**：边折线段不穿非端点节点 / 标签遮罩互不重叠 / 标签遮罩不压节点（顺带 edges·shapes bbox 也在画布内）
- **⚠️ 可选参数的陷阱**：`assert_layout(nodes, W, height, edges=None, labels=None, shapes=None)` —— 若 `expand()` 内部那**两处**调用不传全量，新加的三条**永远不会执行**。「断言存在」≠「断言有效」，故额外造了故意违规输入验证它真能抛错

### 第二部分：`chart_layout.py` → 包 + 5 个新算子

**模块重组**（648 行 → 约 1150 行，拆成包，`app.services.chart_layout` 这个 import 路径**完全不变**，测试与 chart_renderer 一字未改）：

```
app/services/chart_layout/
  __init__.py   # 公开契约：显式 re-export（不靠 import *）
  geometry.py   # 常量 · 文本测量 · 网格分布 · 形状几何 · 路径与箭头 · 标签遮罩 · _path_bbox
  layouts.py    # 10 个算子 + _NODES_RUNNERS/_SPEC_RUNNERS/_ROW_GAP
  engine.py     # validate_spec / assert_layout / expand
```

- **节点形状层**：新增可选字段 `shape ∈ {rect, diamond, stadium, circle, bar}` 与 `decor`。`{x,y,w,h}` 始终是**外接矩形**，所有形状内切于它 → **AABB 不重叠断言无需改写**（对外接矩形判不重叠比判形状不重叠更强，对 diamond/circle 依然充分）
- **形状感知文字宽度**：`_shape_inner_w(n)` 取代硬编码的 `inner = w - 32`（rect 分支与原来完全等价）。菱形内接矩形是 `w/2 × h/2`，故 `_shape_min_w` 对 diamond 取 `2 × 节点最小宽`——**用公式保证菱形文字不溢出**，而非靠调参
- **`shapes` 通用通道**：算子可选返回第 4 项，`chart_svg` 在**背景之后、连线之前**渲染。统一原则：**有几何范围但不属于「内容主体」的一律走 shapes**（不占节点数、不进 `_count_nodes`、不参与重叠校验）——象限轴线框、sequence 生命线、分组框全按此归类
- **两张注册表**：`_NODES_RUNNERS = {hchain, timeline, branch}`（入参 nodes）/ `_SPEC_RUNNERS = {tree, flowchart, state, sequence, quadrant}`（入参整份 spec）

**五个新算子**（全部正交路径、零曲线，守住「连线只含 M/H/V」不变量）：

| 算子 | 要点 |
|---|---|
| `tree` | 父子语义**内嵌在结构里**，不需要 `id`/`parent`/`edges`——对 LLM 少一个字段就少一类出错面。新增 `_elbow` 总线连线（`M 父底 V bus H 子心 V 子顶`），tree 默认无箭头 |
| `flowchart` | **字符串 id 而非 grid 的 `[行,列]`**——判定会跳转/汇合，行列索引正是最易写错的字段；id 让越界错误精确定位到 `edges[2].to`。Kahn 最长路径分层 → 可产生跳层边 → 走右侧纵向沟槽 `_GUTTER_W = 20` 绕行 |
| `state` | BFS 分层 + **允许环与自环**。回边走右侧沟槽；自环走右缘 `_LOOP_W × _LOOP_H` 环路。`type=="start"/"end"` 在上下各插一个空 label 的 `circle` 圆点 |
| `sequence` | 参与者等宽同行（解 `avail ≥ n·w + 12(n−1)` 定 cap，超了就报「参与者过多，请拆图」）；生命线/分组框走 `shapes`；消息即时间顺序，自动加序号 |
| `quadrant` | `quadrants` **固定 4 个、按 TL→TR→BL→BR 顺序**（唯一零字段且无歧义的方案）；十字轴线与象限框走 `shapes`——**靠 shapes 参与 bbox 保住了全宽**，不再需要「轴标签贴死右边界」那个一改就失效的 workaround |

**一个设计修正**：`_grid_edge_label` 新增 `nodes` 参数。它默认的「按两心跨度钳制」是给**同层内水平相邻**的边用的近似；state 的前向边是**上下层**，两心 x 常只差十几像素、跨度判据直接判死，而它的水平段其实落在走廊里（周围根本没有节点）。传了 `nodes` 就跳过跨度启发式、改用 `_overlap` 逐个查真实节点——**用事实判断代替几何近似**（实测：state 的三个边标签从只放下 1 个变成 3 个全放下）。

### 第三部分：prompt 从 4 种 → 9 种全展开

`get_chart_format` 由「4 行 schema + 2 个示例」扩成**九行一类型、各带可直接改的最小 JSON**（tree 只到两层、state 只含一条回环）。

**为什么不只是列名字**：flash 模型对没见过的结构**不会自己拼 schema**，最后仍然全用示例里最眼熟的 `hchain`——「扩展类型」也就白做了。示例取**最小可用**即可，够照改就行，不堆字段。

- **约束仍只有 4 条**：是**引导**变长（告诉它有什么可画），不是**约束**变多（要求它必须画成什么样）。2026-08-16 的教训是「堆规则 ↔ 出图率是 trade-off」，本次刻意保持约束面不变
- **来源差异段语义变化**：从「决定展开哪几种」变成「在九种里优先推荐哪几种」
- **新增一句关键约束**：`flowchart / state / sequence 一律用字符串 id 互相引用（不要用下标）`

### A/B 门禁实验（这是本次唯一预设了回退条件的决策）

样本 5 份真实文档（论文 PDF ×1 + 视频转录 ×4，各取前 8000 字符）× 2 组 × 3 次 = 30 次调用（deepseek-flash）：

| 组 | 出图率 | 类型分布 | 平均 prompt |
|---|---|---|---|
| old（4 种） | **11/11 = 100%** | `chart(手写SVG)` × 11 —— **fig 布局 0 次** | 4377 tok |
| new（9 种） | **13/13 = 100%** | hchain 12 / tree 11 / layers 6 / timeline 5 / grid 4 / sequence 2 / flowchart 1 = **7 种** | 4825 tok |

**结论：不触发回退**——出图率零下降（100% → 100%），types 从 1 种扩到 7 种，prompt 只贵了约 450 token（+10%，远小于字符数 +108% 的增幅，因为新增的主要是短 JSON 行）。

**⚠️ 实验副产物（比主结论更值得记）**：旧 prompt 组 **11/11 全部输出手写 SVG 围栏，一次都没走 `fig`**——即旧 prompt 在这一模型上「语义 JSON」协议实际遵守率为 **0**。新 prompt 的九行 JSON 示例反而把「输出 JSON 而不是 SVG」这件事强化了。这是单模型单次实验（且与上一轮「6 张图」实测走 fig 的观察不一致，可能是模型版本/采样差异），记为观察而非定论，但足以说明**示例的具体程度直接决定协议遵守率**。

**实验设计踩坑（记下来免得重蹈）**：首版实验设 `max_tokens=4000`，得出「出图率 7%」——**纯属截断假象**。deepseek-flash 是推理模型，思考段先吃 token，4000 时 `finish=length` 且**正文一个字都没吐**。改为 16000 后 `finish=stop`。统计时按 `finish_reason` 剔除截断样本。

### 影响范围与验证

- ✅ `test_chart_layout.py` **54 PASS**（新增 flowchart / state / sequence / quadrant 四段 + 形状层段 + 空壳图回归段）+ `test_chart_renderer.py` + `test_prompt.py` 全绿；`test_prompt.py` 的布局断言改为**与 `_LAYOUTS` 同源**（将来加算子忘了改 prompt 会立刻挂）
- ✅ 端到端压测 17 个用例（9 类型正常路径 + 8 个边界：有环 / 隔行边 / 参与者超标 / 空象限 / 树太宽 / 超长 label / 未知 layout / 坏 JSON）：**零失败、零围栏残留**，失败路径全部给出可操作中文提示
- ✅ 渲染图肉眼核对：flowchart 菱形文字居中、state 回边与自环绕行、sequence 生命线对齐、tree 总线、象限轴线
- ✅ 缺陷 6 修复前后实测（同一份打包版产出的 `chart_2.json`）：`['…', '…', '…', '…', '…', '…', '…', '…', '…']` → `['大模…', '材料…', '邮件', '笔记…', '注释', '目录', '格式', '参考…', '图表']`——节点压到 48px 仍保留至少一个字，母线连接与等宽对齐正常
- ✅ 打包验证（`chart_layout` 单模块变包后 PyInstaller 模块发现路径变化）：PYZ 归档含 `app.services.chart_layout` / `.engine` / `.geometry` / `.layouts` 四模块；exe 启动 `/health` 200 + `/api/capabilities` 200；**真实生成产出 3 张图**（hchain / tree / state 三布局，`.json` 旁路文件存在即证明走的是 fig 语义路径而非 legacy 手写 SVG）

### 已知遗留（未处理）

- **`_trim_width` 只裁右不裁左**：布局阶段按 640 宽居中，裁剪后画布变窄而**内容坐标不变** → 窄图内容整体偏右、左侧留大空白。实测 flowchart 案例留白 232px（画布 412px，节点中心 312 vs 画布中心 206）。这是**既有行为**（grid/layers 同样，只是新算子内容更窄所以更刺眼）；修法是在裁剪后把内容整体平移回居中，但会改变既有 4 算子的全部产物坐标，未纳入本轮范围
- `quadrant` 与 `state` 在 A/B 实验中一次未被模型选中（样本里没有「二维评估」「状态机」语义，属内容驱动，非缺陷）
- flow chart **拒绝任何环**（含「退回修改 → 重新提交」这种真实流程极常见的形态），需改用 `state` 布局

---

## 2026-09-19 — 图表生成优化：语义化布局 + 多图 + 自然尺寸缩放

> 用户诉求：「画图其实还可以再优化一下，也可以不仅限于只有一张图片。不过多张图片的话生成时间肯定会延后，那么假如是有多张图片必须得考虑适当的延长生成时间」。

### 核心：把「画图」从生成问题变成渲染问题

旧协议让 LLM **手写 SVG**（含坐标、样式、引号），几何错误（重叠/越界/斜线/文字超框）只能靠 prompt 堆规则压制——而 2026-08-16 的教训是：**堆规则与出图率是直接 trade-off**（28 行硬规则让 deepseek-v4-flash 宁可跳过不画）。

新协议让 LLM 只答**语义 JSON**（有哪些节点、怎么排），坐标/宽度/连线/标签遮罩全由后端确定性布局器算——**几何错误从结构上不可能发生**，几何规则也就不必进 prompt。

- **`app/services/chart_layout.py`**（新增，纯几何零依赖）：4px 网格对齐 / 文字宽度注入测量 / 正交路径（禁斜线）/ 掩码定位 / 5 种算子（hchain 内置自动折行 / layers / timeline / grid / branch）。`grid` 是唯一能表达「谁连谁」的算子，故必须保留；`branch` 已实现并单测但**不写进 prompt**（防用上未文档化算子）
- **`app/services/chart_svg.py`**（新增）：面向 PIL 自绘子集生成 SVG，箭头用 `<polygon>` 手绘（PIL 不支持 `<marker>`）。**出口 `_FORBIDDEN_RE` 自检**——`<marker>`/`class=`/`url(#)`/`<defs>` 不是被校验拦住的而是被**静默忽略**的（用了它们照样通过校验、照样落盘，只是箭头无声消失），所以防线在工具内而非测试里
- **⚠️ 字体宽度必须注入**：布局器不可硬编码「CJK 13px/字」——LiMo 的字体是**运行时探测**的（`_detect_chinese_font`），不同字体字宽差 10–20%，硬编码会系统性低估 → 文字超框。解法是 `measure` 参数注入 `_load_font(...).getlength`，与渲染用同一个字体对象

### 多图支持（不再限一张）

- 旧 prompt 明写「**必须**画 1 张最有代表性的图」——硬性 1 张。渲染链路本无数量上限，**瓶颈纯粹在措辞**
- 新 prompt：每个 ` ```fig ` 围栏 = 1 张图，可按需输出 **1~N 张**；单个围栏节点 ≤9，超过**必须拆成多个围栏**
- **协议并存**：` ```fig `（语义 JSON，现行）/ ` ```chart `（手写 SVG，legacy 兜底）。并存三理由：协议隔离原则不变 / legacy 是**免费回退路径**（回滚只需改 `get_chart_format` 一个函数）/ 已落盘的 `chart_N.png` 不受影响
- **按内容形态容错分流**：fig 围栏内若含 `<svg`，直接按 chart 分支处理——模型改用 fig 后 legacy 兜底会自然失效，JSON 一错就是白丢一张图
- **`chart_N.json` 是协议切换的确定性信号**：两条路径都出 PNG 且张数可能一样，只有 `.json`（仅 fig 分支解析成功时落盘）能区分走的是哪条路

### 实测数据（推翻计划的定值公式）

计划原要求「跑 3 图场景实测，取 P95×1.5 定超时常量」。实测（deepseek-v4-flash，约 900 字文档）：

| 指标 | 实测值 |
|---|---|
| 总耗时 | **9.8s** |
| 首 chunk | 5.7s |
| 输出 | 2890 字符 / 1315 chunks |
| **fig 围栏数** | **6 张**（hchain×3 / grid×2 / layers×1）|

**6 张图只用 9.8 秒**，比基础超时 330s 小 33 倍——**P95×1.5 公式的前提（多图显著拉长生成）被推翻**（按公式算得 15s，比基础档还短，显然无意义）。真实原因：新协议下模型只写语义 JSON（每张 130~240 字符），不再手写数百行 SVG，出图几乎不增加成本。真正的耗时主导项是**正文字符数**，图表只在其上叠加约 18% 输出增量。

- 故 `_GEN_TIMEOUT_CHART = 400`（`330 × 1.21`，基础档线性外推 + 安全余量），非实测直接命中值
- **两个入口都要放宽**：`generate()`（非流式，无 `asyncio.timeout`，只受 SDK 约束）与 `generate_stream()`（流式）
- **⚠️ `request_timeout` 必须改两处**：`AsyncOpenAI` 是**惰性构造**的（`__init__` 只置 `self._client = None`，真正构造在 `client` property 里），只改 `__init__` 会「存了没用」——值进了实例属性但 property 里仍写死 330，**超时一点没变且单测照样 PASS**，只有端到端才暴露。已加测试直接盯住 client 构造实参
- 单 chunk 静默期的 `wait_for(anext, 150)` 是**独立**保护，不随此值变化（「330+150」式的推导没有物理意义）

### 实测中发现并修复的两处真实缺陷

- **grid 同行边标签 100% 显示不出来**：列间距固定 `GAP_X=48`，而边标签遮罩需要「文字宽+20+12 间隙」，连 2 字标签都刚好卡住 → 4 字以上**整条消失**（`_grid_edge_label` 返回 None）。prompt 引导模型写的 edge label 白写。修复：`_grid` 用剩余宽度把列间距撑到 `_GRID_GAP_MAX=120`（顺带让网格图铺开，不再挤在画布中间）
- **边标签截断无提示**：截到 8 字后「很长的边标签会被」读起来像模型没写完，而非渲染器截的 → 补省略号

### 用户提议落地：自然尺寸绘制 + 等比缩放

用户中途提议：「我们可以把图片完整的绘制出来然后再缩放到对应的比例」。

- **高度**：超 640 时不再直接失败报「请拆图」，改为按**自然高度**定稿 + 返回 `scale`，由 `render_spec_svg` 落到根 `<g transform="scale(k)">`。原来只能压缩行距然后失败（24 节点 timeline 自然高 960px，改造前直接 LayoutError）
- **`_MIN_SCALE = 0.65` 下限**：缩放会让字号同步变小（13px × 0.65 ≈ 8.5px 已是可读性边缘），低于下限宁可报错让模型拆图，也不画一张糊的
- **宽度也按内容裁剪**（`_trim_width`）：timeline/layers 内容只占画布左侧，只缩高度等于「把空白一起等比缩小」。裁剪后 timeline 640→196px、layers 640→356px。**边标签必须计入裁剪边界**，否则只按节点裁会把标签切出界
- **背景必须画在 scale 组之外**：`width="100%"` 的语义是「铺满画布」，放进组内会被缩成 100%×k，四边留出未填充空白带

### 影响范围与验证

- **导出零影响**：所有导出路径都**只按 markdown 引用收集图片**（`_find_image_refs` → `_collect_images`），从不扫描 assets 目录 → 多图 = 多个 `![](assets/chart_N.png)` 自然带多张；`chart_N.json` / `chart_N.svg` 是纯旁路文件，不写进 markdown、不被收集、不进任何产物（已实测复核）
- ✅ 后端 11 个测试文件全绿（新增 `test_chart_layout.py` 22 断言 / `test_chart_svg.py` 12 断言，扩展 `test_chart_renderer.py` / `test_prompt.py`）
- ✅ 五种 layout **真实渲染**（不 mock）肉眼核对：文字/箭头/蛇形折行/边标签遮罩/缩放后清晰度
- ✅ 前端 `npm run lint` 无 error、`npm run build` exit 0
- ✅ 后端启动 health 200 + capabilities 200

### 既有缺口（本次未动，备查）

`chart_N.svg` **用户实际够不到**：渲染器写它，但 ① 资产白名单 `_ALLOWED_IMAGE_EXTS` 明确拒绝 svg（`/api/assets` 返回 400）② 导出不扫目录所以不会打包。CLAUDE.md 记它「供深度使用时导出矢量图」，但链路上没有入口。若今后要做矢量图导出，是在 `export.py` 加一个「图表源码」选项，与本次正交。

---

## 2026-08-26 — 打包合规 + AGPL/GPL 许可证替换（主 exe 零 AGPL/GPL 库）

> 用户：「打包合规 + AGPL/GPL 许可证替换计划」。核心诉求：**不想源码被公开** → 消除主 exe（PyInstaller PYZ「合并作品」灰色地带）中的 AGPL/GPL 库。义务边界：ffmpeg 等 subprocess 独立进程无传染（附全文即可）；AGPL/GPL 库进主 exe 是风险点 → 替换。分三阶段全部完成。

### 阶段 1 — 合规补文件（零风险）
- **审计发现**：ffmpeg 实为 **GPLv3**（gyan.dev 官方声明，原文档误标 LGPL）；ebooklib（AGPL-3.0）与 html2text（GPL-3.0）全文未随包；magika/markitdown wheel 无 LICENSE；static/katex + markmap 无 LICENSE；前端无 NOTICES
- **新增 `licenses/`**（随包 `_internal/licenses/`）：GPL-3.0.txt（ffmpeg 用）+ Apache-2.0.txt（magika）+ MIT-markitdown.txt（markitdown）+ README；spec `datas` 收集
- **`bin/ffmpeg/` 补** GPL-3.0.txt + README-ffmpeg.txt（gyan.dev essentials 8.1.1 GPLv3、独立进程无传染、源码 ffmpeg.org）
- **`static/licenses/`**：KaTeX（MIT）+ markmap-lib/view（MIT）+ d3（ISC）LICENSE 复制随包
- **根目录 `THIRD-PARTY-NOTICES.md`**：后端 Python 组件 + 前端 16 个运行时依赖 + 离线静态资源 + 独立进程组件四类清单
- **文档修正**：ACKNOWLEDGMENTS ffmpeg 改 GPLv3、CLAUDE/BUILD searxng 明确不随包（AGPL，Rust launcher 检测到安装目录才启用）

### 阶段 2 — html2text（GPL-3.0）替换
- **webpage.py 兜底提取** html2text → **bs4 `get_text()`**（bs4 已是依赖，零新增）：剥离 script/style 后取纯文本，长度 <10 才回退正则去标签
- pyproject.toml + spec hiddenimports/collect_all 移除 html2text

### 阶段 3 — ebooklib（AGPL-3.0）自研替换
- **新增 `app/engines/epub_container.py`**（纯标准库 zipfile + xml.etree + posixpath，~260 行）：
  - container.xml → OPF（metadata/manifest/spine）→ 资源；TOC 优先 EPUB3 nav（properties=nav）回退 EPUB2 NCX
  - `_norm_href` 相对 OPF 目录规范化；**TOC 锚点 key 冲突修复**（keep_fragment 保留 #fragment，同文件多锚点不互相覆盖）
  - **`_parse_nav` 只遍历根 ol**（`_find_first_ol`），修复 nav.iter() 嵌套重复 + level 覆盖 bug
  - EpubItem/EpubContainer 对齐 ebooklib 接口子集（get_metadata/get_items/spine/get_item_with_id/get_toc_map）
- **`epub.py` 改单解析器**：移除 ebooklib fallback（失败显式报错，符合「引擎绑定只取第一个」）；删除 `_build_toc_map` 死代码
- **`_detect_sections` 跳过空白文本节点**：body.children 首 child 是换行缩进 → 每章前产生空 section（修复：`not str(child).strip()` continue）
- **引擎重命名 ebooklib → `epub-container`**：注册表 + `_DOC_DEFAULTS` + config.py builtin_models + capabilities fileTypeMap + 引擎 ID（DocResult.engine）全量替换
- **`_migrate_remove_ebooklib` 启动迁移**：清理旧 config 残留（builtin_models/engine_models/绑定指向旧 MD5）→ 回退默认 epub-container
- pyproject.toml + spec + uv.lock 移除 ebooklib；删除 `licenses/AGPL-3.0.txt`

### 终态
- **主 exe 零 AGPL/GPL 库**；GPL 仅剩独立进程的 ffmpeg（随包附全文合规）；AGPL 仅剩进程外插件 pymupdf4llm + 不随包的 SearXNG
- 验证：自研容器 EPUB3/EPUB2 全链路 ALL_PASS（章节/图片/元数据/TOC）+ 后端 8 个测试文件全绿 + test_epub_pipeline 全通过

### 修复 — 保存编辑产物 bug（打包版实测发现）
- **现象**：点击编辑器「保存」按钮，请求路径变成 `/api/markdown/[object Object]`，后端返回 `MD_EDIT_NOT_FOUND`（文档不存在）
- **根因**：TextareaMarkdownEditor 保存按钮 `onClick={onSave}` **把 MouseEvent 注入** `handleSaveEdit(saveDocId?: string)`——React 调用 onClick 处理器时总是传事件对象，`saveDocId` 收到 truthy 的 MouseEvent → `docId = saveDocId` 变对象 → 模板字符串 `${docId}` 得 `[object Object]`。TS 无参函数（`() => void`）赋给 `onClick` 逆变兼容不报错，静态检查无法发现（同类风险：UnsavedChangesDialog 三个按钮同样直接绑定 props）
- **修复**：① TextareaMarkdownEditor + UnsavedChangesDialog 按钮改 `onClick={() => onSave()}`（不传 event）；② `handleSaveEdit` 加 `typeof saveDocId === 'string'` 守卫（纵深防御，未来任何非 string 注入直接回退编辑态归属文档，不再拼脏 URL）
- **全量排查**：前端 74 处 `onClick={标识符}` 绑定逐一核对签名——仅此一处「handler 第一参数是可选业务参数」，其余均为无参 handler（注入的 event 被忽略，无数据污染风险）
- 影响：编辑产物保存正确落到归属文档（含切文档「保存并离开」路径）

---

## 2026-08-25 — 日志/错误系统全面修复（P0/P1/P2）

> 用户：「检查一下我们目前的日志系统以及错误系统是否足够全面尤其是日志方面」「我建议全部做完」。审查发现：export/ingest 残留 20 处 `HTTPException`（未统一 AppError 体系）、epub 路由零日志、日志风格混用（仅 4 文件用 log. helper，49 文件 stdlib）、chat.py 仅 3 条日志、LogPanel 无搜索。分 P0/P1/P2 三批修完，全部验证通过。

### P0 — 错误体系统一 + 清理
- **export.py 9 处 + ingest.py 11 处 `HTTPException` → `AppError`**（EXPORT_DOC_NOT_FOUND/EXPORT_NO_CONTENT/EXPORT_MISSING_IDS/EXPORT_CONV_NOT_FOUND/EXPORT_TEMP_EXPIRED/INGEST_DOC_NOT_FOUND/INGEST_INVALID_TX_ID/INGEST_UNSUPPORTED_EXT/INGEST_PATH_TRAVERSAL/INGEST_INVALID_FILENAME/INGEST_FILE_NOT_FOUND），移除 `HTTPException` import
- **epub 链路补日志**：路由三端点（generate/task/cancel）+ `epub_pipeline.py`（跳过已成功章、空章节占位、阶段 2 归并开始、map-reduce 分组进度）
- **print 清理核查**：abogus.py 三处 + agent.py 一处 print 均在 `__main__` 自测块/docstring 示例（非业务路径），保留

### P1 — 关键链路日志结构化
- **logging.py helper 增强**：`_emit` 用 `inspect` 取**调用者模块名**注入 logger——此前所有 helper 日志 name 固定 `app.core.logging`，来源模块信息丢失，迁移 stdlib→helper 反而退化。现在每条 helper 日志 name 即来源模块（如 `app.routers.ingest`），配合 rid 可精确定位
- **ingest.py（21 处）/ process.py（14 处）** stdlib logger → `log.` helper 结构化 kwargs（cookie 注入、持久化、封面下载、流式写入、重转录、LLM/Engine 图片处理、Engine 回退链）
- **chat.py 补对话链路日志**：预算分配（C2）、历史压缩触发（C3）、文档注入、历史裁剪、SSE 完成统计（answer_len/tools/refs）；移除不再使用的 stdlib logger
- **agent.py 补工具循环日志**：无工具直接回答、工具轮次、达到 max_rounds 强制整理（warning）、Tool 调用/失败结构化

### P2 — LogPanel 可检索
- **后端 `/api/logs` 加 `search` 参数**：在切片前对全量 1000 条 buffer 过滤（msg 子串，覆盖 request_id/关键字），非最近 N 条
- **前端 LogPanel 加搜索框**：关键字 / request_id 检索，输入变化即时刷新

### 附带修复 — AppError recoverable 与 status_code 解耦（P0 引入的 404 回归）
- **根因**：P0 迁移把 `HTTPException(404)` 改成 `AppError(status_code=404, recoverable=True)`，但 main.py 异常处理器用一维映射 `200 if recoverable else status_code` 把显式 404 也压成 200 → 删除文档后 GET 返回 200 而非 404，e2e「deleted doc -> 404」失败
- **修复**：处理器改为「显式 status_code（≠默认 400）优先尊重，未显式时 recoverable→200」。recoverable（是否预期业务状态）与 status_code（HTTP 语义）是两维独立概念，不再耦合。前端 `isRecoverable` 对 recoverable 404 仍静默处理，行为兼容

### 验证
- 后端 9 个测试文件全绿（重点 test_e2e_smoke 36/36，含 deleted doc -> 404）
- 前端 tsc 通过 + lint 零 error（存量 exhaustive-deps warning）+ vitest 98/98

---

## 2026-08-25 — 提交后代码质量/健壮性审查（开发者视角）

> 用户：「提交然后再进行一次代码质量和健壮性审查，我们需要从开发者的角度去看」。审查覆盖 5 个域：视频直传、编辑未保存保护、日志系统、错误体系、全局模式（并发/取消/资源/边界）。两个并行审查 agent（编辑保护 / 日志错误）+ 主线程人工核查，共确认并修复 3 个高危数据污染/损坏 bug + 若干中低项。

### 🔴 高危修复
- **H1 export_custom 显式 400 被压成 200 → 前端下载损坏 zip**（export.py:1258 `status_code=400, recoverable=True`）：main.py 处理器用「≠默认 400」判断是否显式指定，显式 400 落入 `recoverable→200` 分支 → 前端 ExportPanel `if (!r.ok)` 把 200 当成功 → `r.blob()` 拿到 AppError JSON → 下载损坏 zip + 成功 toast。**根治**：AppError 默认 status_code 改用哨兵 `_UNSET` 并记录 `_explicit_status` 标记，处理器只对「未显式指定且 recoverable」压 200，显式 400 必须返回 400。这是「≠默认值」判断法的设计盲区（M1），未来新增显式 400 不会再踩
- **H2 切文档弹窗打开时 Ctrl+S 把原文档编辑内容保存进新文档**（PreviewArea）：旧实现 window.confirm 是同步模态阻塞事件循环，弹窗期间 Ctrl+S 不派发；改 React 异步弹窗后该出口暴露——`selectDoc(B)` 把 `currentDocId` 切成 B，但编辑态仍属 A，Ctrl+S 无参保存用 `currentDocId=B` 把 A 的内容写入 B。**根治**：编辑态 `editing` 绑定来源 `docId`（`{mode, docId}`），所有无参保存目标 = `editing.docId`（非 currentDocId），弹窗打开时拒绝无参保存（Ctrl+S 属编辑器快捷键，弹窗「保存并离开」显式传参不受影响）
- **H3 编辑态不绑定来源文档 id，弹窗期间二次切文档错存**（PreviewArea）：弹窗 `prevDocId` 来自 `prevDocIdRef`（上一次文档 id），弹窗打开期间经 AI 浮窗（z-index 同级且 DOM 后渲染）再切文档 C 时，effect 用「中间文档 B」覆盖弹窗 → 保存/回退错指。**根治**：弹窗 `prevDocId` 用 `editing.docId`，保存目标 = `editing.docId || prevDocId`，取消回退也用编辑来源
- **H4 视频直传 prompt="" bug**（content_preparer.py:254 传空串覆盖默认画面理解指令 → 模型收到空指令）：`understand_video_direct` 加 `prompt or VIDEO_UNDERSTAND_PROMPT` 防御 + 调用方改传 None
- **H5 视频直传流式单产物路径静默失效**（generate.py 流式端点单产物分支漏传 `video_direct` → 只选 notes/polished 时直传失效走截帧，模式 #16「prepare 参数对称」同类复发）：补 `video_direct=video_direct`

### 🟡 中低项修复
- **非流式生成端点漏 `llm.aclose()`**：每请求新建 LLMService，请求结束不关 AsyncOpenAI client → httpx 连接池常驻累积（流式端点 P1-7 已覆盖，非流式遗漏）。finally 补 aclose
- **`effective_fps` 长视频帧数超限**：DashScope fps 有 0.1 下限，视频 >1280s 时 `max(0.1, ...)` 使总帧数超 MAX_FRAMES——SDK 硬约束下的尽力而为，注释澄清 + 超限 warning
- **`/api/logs` lines 非数字 500**：裸 `int()` 抛 ValueError，try 兜底默认 200
- **video_understand.py stdlib logger → log. helper**（新文件违反模式 #35）；agent.py 残余 stdlib logger 混用清理
- **process.py 重复 `router = APIRouter()` 声明**清理；`_to_file_uri` 注释笔误（实现 `file:///D:/` 三斜杠，注释两斜杠）

### 审查确认无问题的项（未改动）
- AppError 子类（TranscribeError/GenerateError 等）默认 400 + recoverable 语义不受 sentinel 影响；e2e「deleted doc -> 404」保持通过
- 视频下载/截帧临时目录清理、Cookie 临时文件删除、LLM 连接池关闭（process.py 全出口）、流式双产物 gather 顺序解包、信号量释放逃逸防护均正确
- dashscope `MultiModalConversation.call` 无 per-call base_url（走全局 `dashscope.base_http_api_url`），video_understand 的 base_url 参数是合理的防御设计

### 遗留建议
- ✅【中】**编辑中未保存 × 重新生成拦截**（已实施 2026-08-25）：GenPanel `handleGenerate`（全部生成/重新生成/继续生成入口的唯一汇聚点）检查 `dirtyEditingDocId`（PreviewArea 将编辑态暴露的归属文档 id，仅标记不提升编辑态）→ 匹配当前文档时两态 confirm「放弃编辑并重新生成？（可先保存后再生成）」，确定 → 清标记 → PreviewArea effect 自关闭编辑态（生成完成后编辑框不再开着旧值，杜绝旧编辑静默写回新产物）。轻量桥方案：uiSlice 加 `dirtyEditingDocId` 字段 + PreviewArea 两个同步 effect + GenPanel 一处拦截，零跨组件状态提升
- 【低】「取消」回退后 activeView 可能被 tab 有效性 effect 跳走，点回原 tab 再弹窗（跨 sourceType 切文档才触发）
- 【低】logging._val 对 dict/list 不截断（`agent.py` Tool 调用 args 可能夹带长文本/隐私）；`_caller_name` 帧栈遍历开销建议长期改用 LoggerAdapter 注入

---

## 2026-08-25 — 编辑未保存保护：三态弹窗（保存并离开/不保存/取消）

> 产物编辑（2026-08-15 textarea 版）原有 `window.confirm` 覆盖保护，但同步阻塞模态掩盖了「弹窗期间 Ctrl+S / 切文档」的危险出口。改 React 异步三态弹窗 + 编辑态绑定来源 docId，根治跨文档错存。

### 实现
- **三态弹窗**（`UnsavedChangesDialog`）：编辑中未保存离开 tab/文档 → 「保存并离开 / 不保存 / 取消」；保存失败留在当前可重试
- **编辑态绑定来源 docId**（`{mode, docId}`）：所有无参保存（Ctrl+S/保存按钮）用 `editing.docId` 而非 `currentDocId`，弹窗打开时拒绝无参保存——根治切文档弹窗期间 Ctrl+S 把 A 的内容存进 B
- **弹窗期间二次切文档**（经 AI 浮窗）：prevDocId 用 `editing.docId`，保存/回退不错指中间文档
- `beforeunload` 拦截关页；三态弹窗为 PreviewArea 独立组件

---

## 2026-08-25 — 视频直传模型 阶段 A（Qwen 打通，DashScope SDK 原生通道）

> 主流模型原生支持视频输入，服务端抽帧比本地 ffmpeg 截帧更稳定（自动选帧），省截帧调参/拼图/质量依赖。方案 `pi-docs/视频直传模型方案.md` 已定稿（该目录已于 2026-10-18 随一次性文档清理删除）。本地视频 >7MB 无法走 OpenAI 兼容（base64 不实用），必须走 DashScope SDK `MultiModalConversation` 原生通道（file:// ≤100MB）。

### 实现（增量，不替代）
- **视频直传 = 独立「视频画面理解」预处理**，不混入生成调用：视频(≤100MB) → SDK 视频理解 → 描述文本注入 `clean_text` → 生成调用照常（流式保留）
- 触发条件：开关开启 ∧ 模型 capabilities 含 `video` ∧ 本地视频 ≤100MB；失败/超限 → **回退截帧链路（原封不动）**
- 新增 `app/services/video_understand.py`（`understand_video_direct`，同步函数 `asyncio.to_thread`，fps 0.1-10 SDK 硬约束，>1280s 长视频尽力而为）
- config 增量标注 dashscope 视频模型（qwen3.7-plus/qwen-vl-max/qwen3-vl-flash 加 `video`；新增 qwen3.8-max）；opencode 网关不标（第三方 video_url 支持未知）
- 前端：GenPanel 视频区块「视频直传模型处理」勾选（无 video 能力禁用 + tooltip），截图间隔/拼图/质量参数置灰；模型选择能力标记「视频」
- LLMService / generator / 流式 SSE / 截帧链路 / ASR **零改动**（直传是 prepare 阶段一次非流式理解）

---

## 2026-08-17 — 新增内置厂商 OpenCode Go（OpenAI 兼容网关）

> 用户要求在设置页「大模型厂商 / 引擎厂商」支持 OpenCode Go。阅读官方文档确认：**OpenCode Go 不是引擎服务，而是 OpenAI 兼容的 LLM 网关**（`opencode.ai/go/v1`、`/chat/completions`、Bearer Key，与 OpenCode Zen 同平台共用 `OPENCODE_API_KEY`，Go 固定订阅 5h/$12·周/$30·月/$60，Zen 按量付费）。定位为**内置 LLM 厂商 + 图片处理（image_process 场景）可用视觉模型**。

### 新增
- **`PROVIDER_DEFAULTS` 注册 `opencode` 内置厂商**（`https://opencode.ai/go/v1`）——设置页 LLM 厂商区自动出现，前端零改动（providers 由 `_get_providers` 自动补全内置）
- **`llm_models` 预置 chat-completions 系模型**（官方 docs/go 清单，GPT/Grok 走 Responses API 与 LiMo 的 chat.completions 不互通，不进预置）：
  DeepSeek V4 Pro/Flash、Qwen3.8/3.7/3.6 Max/Plus、GLM-5.1/5.2/5.3、Kimi K3/K2.7 Code/K2.6、MiniMax M3/M2.7、MiMo-V2.5
- **图片处理可用**：Qwen/GLM 高版本预置标 `["text","image"]`——图片处理/OCR 场景可选（网关实际能力以调用结果为准，不支持时在设置页取消「多模态」标记；未知模型默认 text 可手动标）
- **连接测试兜底**：`test_connection` 对不实现 `GET /v1/models` 的网关（OpenCode 等）改用最小 `chat/completions` 请求真正验证 key/base（404/405 → POST 探活）；模型刷新失败自动回退预置 list（原逻辑保留）

### 影响
- 生成/对话/图片处理三个场景均可选 OpenCode Go 模型；Key 在设置页填写后立即生效
- 订阅制有请求限额（5h/周/月三级），超限后网关返回错误，属服务方限制

---

> 用户：「图片还是有错位之类的东西」。上一轮多边形闭合是皮外伤，这轮深挖到两个**系统性**渲染缺陷：**`<g transform>` 元素整块不画 / 画错位** + **节点内文字垂直不居中**。这是「看起来错位、缺东西、不如原仓库好看」的主要来源（原仓库 diagram-design 的规范大量用 `<g transform>` 定位 + `dominant-baseline=middle` 让文字在节点内居中）。纯 PIL 内补齐，不引入外部依赖。

### 根因
- **`_svg_to_pil` 遍历时直接跳过 `<g>`**：`transform="translate(...)"` 完全不被解析，g 内子元素（连线/标签/分组节点）要么整块不画、要么画在原始坐标 → 视觉错位/缺内容。qwen-vl 实测：`<g transform="translate(120,60)">` 内的连线+标签整体不可见
- **`_draw_text` 把 SVG 文字 y 一律当基线**：`dominant-baseline="middle"`（节点内垂直居中的标准写法）被忽略，文字按基线画偏下 → 节点标签下方偏移感

### 修复
- **`_parse_transform` + `_mat_mul`**：SVG transform（translate/scale/rotate/matrix，空格或逗号分隔组合）→ 2×3 affine 矩阵；矩阵按文本顺序从左到右复合（SVG 语义）
- **`_svg_to_pil` 改递归遍历**：`<g>` 滚入子元素并累计 transform；`_map(x,y,m)` 用户坐标（含变换）→ 像素统一映射。全部绘制函数（rect/text/line/path/circle/ellipse/polygon/polyline）坐标改走 `_map`
- **旋转/斜切兜底**：rect/ellipse 在含旋转/斜切时退化为多边形近似（`draw.line` 闭合描边；漏画圆角优于画错位置），无旋转保持原 PIL 绘制（rounded_rectangle/ellipse 观感不变）；字号/笔宽按 transform 局部缩放因子自适应（`tots = scale * lscale`）
- **`_draw_text` 支持组合锚**：`text-anchor`（start/middle/end → PIL l/m/r）× `dominant-baseline`（middle/central → 中线 m、hanging/top → ascender、text-after-edge → descender，默认基线 s）组合成 PIL 双字母 anchor——`text-anchor="middle" dominant-baseline="middle"` 真正水平+垂直居中

### 验证
- 像素断言：`<g transform="translate(120,60)">` 内连线+标签出现在平移后区域 2356px / 原位 0px（修复前原位也空 = 整块丢失）；`dominant-baseline=middle` 文字 bbox 中线 410 vs 节点中线 400（基线画法偏 ~20px）
- **qwen-vl 识图复验**：标签位于预定位置（不在原点）、盒内文字居中、无缺元素无错位
- test_chart_renderer.py 追加 transform translate + dominant-baseline middle 两项回归断言；9 个后端测试文件全绿

### 影响
- 补上 SVG 子集最常用的定位手段（transform），LLM 按原仓库规范输出 `<g transform>` 时节点/标签不再消失或错位；节点内居中文字对齐
- 仍不解析的 SVG 特性（transform 内的复杂元素、A 椭圆弧、defs/marker 等）退回「忽略或近似」（漏画优于画错）；临时验证文件 `_transform_demo.png` 留存供查看

---

> 用户在类型示例图（`_type_demo_flowchart.png`）中发现流程图错位、边框缺失。定位到渲染器 bug：**`_draw_poly` 的 polygon 描边用 `draw.line(pts)` 不闭合**——SVG 语义中 polygon 最后一点要回到起点，draw.line 只画点间连线，导致菱形/多边形缺「最后一点→首点」那条边（流程图菱形只剩 3 条边，视觉上既缺框又像错位）。用 qwen-vl-max 识图复验：5 个节点边框全部完整、文字居中、连线无断头。

### 修复
- **`_draw_poly` polygon 描边闭合**：`closed = pts + [pts[0]]`，polyline 保持不闭合（SVG 语义区分）。菱形/箭头等带 stroke 的 polygon 四条边完整
- 示例流程图 SVG 自身漏画两条流程线（开始→输入数据、输入数据→菱形）——VL 识图抓出，补全（非渲染器问题）

### 验证
- 像素分析：菱形 4 边 402/393/480/442px（修复前左上边仅 146px）
- **qwen-vl-max 识图**（新增临时工具 `_vision_check.py`，dashscope 多模态看图代替人工像素）：4 张类型示例图（流程图/时间线/结构树/架构图）全部识别出正确类型、元素与连接，无缺边框无错位
- test_chart_renderer.py 追加 polygon 闭合回归（闭合边 171px）；9 个后端测试全绿

### 影响
- 带描边的多边形（决策菱形、箭头、分组框）完整闭合，图表不再出现「缺边残缺」
- 新增 `_vision_check.py` 临时识图工具（qwen-vl 看图描述），可复用验证渲染效果

---

## 2026-08-16 — 图表连线补齐：贝塞尔曲线（C/S/Q/T）采样渲染 + 手写示例图全类型实测

> 用户：「各种连线可以正常生成吗我们手写的，你最好再搞几个示例图片看看」。构造 10 种连线类型示例图实测，暴露 PIL 自绘渲染器短板：**曲线命令此前跳过参数漏画**（图表连接线用曲线时整条消失），其余（line 直线/虚线、path 折线/虚线、polyline、polygon 箭头、相对命令）均正常。

### 补齐
- **`_path_polylines` 增加 C/S/Q/T 贝塞尔采样**：C/S 三次（`_bezier3`）+ Q/T 二次（`_bezier2`）各采样 24 段为密集折线点（虚线按弧长分段自然兼容）；S/T 平滑曲线的控制点用上一控制点反射（`_reflect`，无上一控制点时用当前点）；Z 闭合回子路径起点。A（椭圆弧）保持跳过——图表基本不用，漏画优于画错
- 相对曲线命令（c/s/q/t）一并支持（参数相对命令起点偏移）

### 验证
- 三张手写示例图存 `backend/_line_demo_{1,2,3}.png`（临时文件，用户判别后删）：① 连线类型总览（line/虚线/折线/C 曲线/Q 曲线/polyline/箭头/C 虚线）② S/T 平滑 + 相对 q/t + Z 闭合三角形 + C 虚线 ③ 模拟 LLM 真实流程图（节点 + 竖直线/水平折线/虚线/C 竖线/贝塞尔曲线 + 箭头），像素分析全部画到预期区域
- 初测用强调色 #eb6c36（RGB 和 397）时深色阈值 300 误判漏画——曲线其实已画出，检测阈值放宽到 600 复测全过
- test_chart_renderer.py 追加曲线渲染回归（C/Q/S/T 真实渲染断言，2192px）；9 个后端测试文件全绿

### 影响
- 图表曲线连线不再消失；A 椭圆弧仍不画（图表未用到，漏画优于画错）

---

## 2026-08-16 — 图表渲染 resvg→PIL 切换：resvg 全系在 Windows 无法渲染 SVG 文字（实测定案）

> 用户用两条路径生成对比图判别：「后端生成的没有文字，PIL 有文字」——此前的「resvg 有系统字体 fallback 链」结论被彻底推翻。深挖 resvg-py 与 resvg CLI（cargo 编译 + 官方二进制）实测：**根因是 resvg/usvg 在 Windows 的 text 渲染 bug，不是字体配置问题**。

### 根因（实测证据链）
- **resvg-py 0.4.0**（Python 3.12 唯一可用版本）：`font_dirs`/`font_files` 传真实字体路径全部无效，连英文字符都渲染为 0px，日志 `No match for "Times New Roman"/"Arial"`——fontdb 匹配不上
- **resvg CLI v0.48.1**（cargo 编译 + `system-fonts` feature）：`--list-fonts` 正常列出 Arial 等系统字体（fontdb 加载成功），但 usvg 渲染一律 `No match for "<font-family>"`——默认 / `--font-family` / `--use-fonts-dir` / `--use-font-file` 四种方式全试均失败
- **resvg CLI v0.47.0**（官方 Windows 二进制）：同样 `No match`，文字 0 深色像素
- 佐证：v0.48.1 release **无 Windows 二进制**（Windows CI 构建失败；v0.47 及更早有）——resvg 在 Windows 的 text 问题是上游普遍 bug，非个别绑定缺陷

### 方案：PIL 自绘 SVG 子集渲染器（`chart_renderer.py`）
- **`_render_png` 从 resvg 切 PIL**：新增 `_svg_to_pil`（rect/text/line/path/circle/ellipse/polygon/polyline 子集），非子集元素忽略（漏画优于画错）；text 用系统探测中文字体文件路径渲染（PIL `anchor` 基线锚——SVG text 的 y 即基线）
- **修复 path 虚线不画**（用户实测「PIL 的没有一些连接线」）：SVG path 命令与数字粘连（`M470 90 V130`），此前 split() 解析漏命令 → pts 全空 → 不画。改正则 tokenizer（`_path_polylines`）分离命令/数字，支持 M/L/H/V/Z + 相对命令 + 隐式 L，曲线命令跳过；`stroke-dasharray` 虚线按弧长分段绘制
- **`_detect_chinese_font` 改返回字体文件路径**（PIL 需要；此前返回 resvg 注册名）+ 按 `(path,size)` 缓存字体对象
- **零新依赖**：PIL 已随图片优化打包；**resvg_py 收集已从 LiMoBackend.spec 移除**（此前需显式 glob `resvg_py/*.pyd` 进 binaries + hiddenimports）
- 校验链 / 安全过滤 / SVG 片段保存 / 失败标注逻辑**全部不变**

### 验证
- 用户实际 SVG（`7 分钟了解 10 种 RAG 策略` chart_1.svg）渲染：文字条带深色 2951/3844 + path 虚线竖线/横线 248/562（resvg 全系为 0）
- test_chart_renderer.py 新增**真实 PIL 渲染回归用例**（不 mock `_render_png`：文字 + path 虚线必须渲染）；全部 9 个后端测试文件全绿
- 中文字体探测成功（msyh.ttc）

### 影响
- 图表渲染不再依赖 resvg_py（打包体积略减，去掉一个 .pyd + 收集配置）
- 取舍：PIL 只覆盖 prompt 约束的 SVG 子集；LLM 若输出超子集元素（image 等）→ 忽略该元素（漏画优于画错）

---

## 2026-08-16 — 图表文字问题定案：字体非根因 + 保存 SVG 片段 + 约束精简（修正上一条）

> 上一条「resvg 不跨字体 fallback 字形」的根因结论**被实测推翻**。用户连续反馈"图表没文字"后实测：resvg fontdb **有系统字体 fallback 链**——`sans-serif`/不指定字体都能渲染中文（文字中心深墨 1032px），显式 YaHei 或渲染参数映射更清晰（1568px）。字体不是"没文字"的根因。

### 修正
- **移除脆弱的字符串替换 `_ensure_chinese_font`**：正则只匹配双引号 `font-family="..."`，LLM 用单引号/style 内联/CSS 类写法全部漏掉——即使字体是根因也覆盖不全。删除该函数与 `_FONT_FAMILY_RE`
- **改 resvg 渲染参数**：`svg_to_bytes(sans_serif_family=font, font_family=font)`（`_detect_chinese_font` 保留，探测失败用 resvg 默认，仍有 fallback）——不修改 SVG 内容，所有字体写法全覆盖
- **prompt 去掉 font-family 约束**（用户"不需要指定字体"）+ **图表约束精简压缩**（用户反馈"生成太久"）：样式/节点/显示规则合并为 ~5 行核心 + 最小示例（去 font-family），**强制只画 1 张最有代表性的图**（原 1-2 张 → 图表 SVG 输出 token 减半，最大提速点）；注入段从 ~1488 字符压到 ~826 字符
- **保存 SVG 源码片段**（用户提议）：`_process_svg` 渲染成功同步写 `assets/chart_{n}.svg`——①调试：文字没渲染时能查看 LLM 原始输出（产物零残留设计此前把 SVG 直接丢了，无从取证）②用户深度使用时导出矢量图。不用于展示（assets 白名单无 svg）

### 真正的"没文字"根因（尚未定位）
- 既然 resvg 渲染中文没问题，"没文字"指向 **LLM 输出的具体 SVG**（文字 fill 与背景同色 / 文字被 rect 覆盖 / 字号或位置异常 / visibility 等）。保存 SVG 片段后重新生成即可取证定位

### 验证
- 真实 resvg 端到端冒烟：```chart 围栏 → `![图表](assets/chart_1.png)` 引用 + chart_1.png（640x320，中文深墨 3761px）+ chart_1.svg（含中文源码）三通
- test_chart_renderer.py（新增 SVG 保存断言）+ test_prompt.py 全绿

---

## 2026-08-16 — 图表文字无法显示根因定位 + 渲染层中文字体兜底（未打包）

> 用户反馈：生成的图表"没有文字/文字无法正常显示"。与上一条「LLM 没写 `<text>`」是两码事——本次**文字其实画了但渲染不出来**。

### 根因（resvg 不跨字体 fallback 字形）
- resvg 的 fontdb 按 `font-family` **精确匹配**，找不到时不回退到其他字体补字形。prompt 用 `sans-serif` → Windows 上 resvg 映射到 **Arial**（无中文字形）→ 所有中文渲染成空白/豆腐块。批量字体对比实测：sans-serif 中文深墨仅 147px（稀疏/豆腐块），Microsoft YaHei 492px（正常）
- 与「LLM 没写 `<text>`」叠加：即便 LLM 认真写了文字节点，渲染层也画不出来——这就是用户反复看到无字图的完整链条

### 修复
- **渲染层中文字体兜底**（chart_renderer.py）：`_render_png` 渲染前调 `_ensure_chinese_font()`——把 `<text>` 的泛型/西文 `font-family` 替换为系统探测的中文字体。跨平台候选：Windows `Microsoft YaHei/SimHei/SimSun`、macOS `PingFang SC`、Linux `Noto Sans CJK SC/WenQuanYi`；PIL `ImageFont.truetype(path)` 探测 + 进程内缓存（每进程只探测一次）
- **尊重 LLM 明确指定**：`font-family` 已含中文字体名（YaHei/SimHei/PingFang/Noto 等）则原样保留，不二次替换；探测失败回退原样渲染不崩溃
- **校验链加强**（同批）：`<text` 打开标签后必须有**非空白文字内容**——空 `<text></text>` / 自闭合 `<text/>` 与零文字图一并拦截（此前只查标签存在，空 text 会通过校验却渲染出无字图）
- **prompt 不动**：用户要求「通用简单字体」→ prompt 保持 `font-family="sans-serif"`，具体字体由渲染层兜底决定（LLM 无需知道平台字体名）

### 验证
- 实测修复效果：文字区域深墨像素 1255→2104px（1.7x，全图统计被节点描边稀释故取文字区域对比）
- test_chart_renderer.py 新增 3 组用例（mock 字体探测，跨环境稳定）：泛型 sans-serif 替换 / 明确中文字体保留 / 探测失败原样返回 + 空 text/自闭合 text 校验失败
- 后端 9 个测试文件全绿，无回归

### 待用户验证
- 当前用户环境 7414 是旧后端（缺 resvg_py）+ 5173 被 file-viewer 项目占用。需处理残留进程后 `bash start.sh`（7413 + reload + `.venv`）重新生成图表，确认文字正常显示

---

## 2026-08-16 — 生成警告弹窗修复（未打包，0.4.1 基础上追加）

> 用户反馈：每次生成都弹「当前内容类型不支持：保留文档图片」+「图表未生成」两个警告，且日志无记录。

### 修复
- **「当前内容类型不支持」不再被跨类型残留骚扰**：`formatImages`/`formatLink`/`formatScreenshot`/`formatReferences` 等被 genPrefs 跨文档类型持久化，切到 UI 不支持该格式的类型（如 PDF 勾过「保留文档图片」→ 文本/视频）时，`droppedFormats` 检测到残留 → **每次生成都弹 toast**。修复：只对「当前类型 UI 可见」的格式弹提示（镜像 GenerateOptions 显示条件），UI 不可见的残留静默忽略（该类型本就不支持、用户无法操作）；全量被过滤格式 `console.warn` 记录便于排查（GenPanel.tsx）
- **图表约束强化**：`get_chart_format` 从「可选生成…不适合则明确说不画」改「内容适合时必须生成；判断不适合时**不得静默跳过**，须输出 `> 图表说明：内容不适合图表化`」——消除 LLM 合法跳过路径，让「跳过」显式化（prompt.py）
- **图表约束二次精简**（同日下午）：实测 flash 模型（deepseek-v4-flash）多次不画——28 行质量硬规则（防重叠 6 条/复杂度预算/布局约定/≤300 行）创作成本过高，模型宁可跳过。移除质量硬规则，压缩到 8 行核心（必须生成/chart 围栏/viewBox 一致/安全红线）+ **最小可行动例模板**（2 节点流程图，模型照抄门槛极低）。质量略降但产出率大幅提升；防重叠/4px 网格等**质量**规则回归原仓库建议层（参考仓库 `C:\WorkPlace\_pi\explore\diagram-design\`）；但「正常显示」底线约束保留——二次精简删过头，见下条
- **warning 补日志**：generate.py 生成开始记录 `source_type/formats`；两处「图表未生成」warning 加 `log.warning` 并区分「模型判断内容不适合图表化」（检测到声明）vs「LLM 未输出图表围栏」，warning 消息同步区分——前端不再收到笼统的「LLM 未输出围栏」
- **加回「正常显示」必要约束**（当日下午，用户反馈「图表里没有文字」）：二次精简删过头，模型产出纯框线无文字图。先用 `.venv` resvg 实测**能正常渲染中英文**（排除缺字体论）——根因是 LLM 没写 `<text>` + 校验链只查形状不查文字。从原仓库 diagram-design（`C:\WorkPlace\_pi\explore\diagram-design\`）提取「至少能正常显示」必要约束回注 prompt：① 背景不透明（铺满 `<rect width="100%" height="100%" fill="#ffffff"/>`，透明底深色主题下看不清）② 元素不越界、四周留 ≥16px 边距、文字不超节点边界（超出被裁剪）③ 相邻节点间距 ≥16px 不重叠 ④ 每个节点必须有文字、字号 ≥12、深字浅底、节点内居中。校验链加**「无文字说明」兜底**（有形状但零 `<text>` → 校验失败替换为失败文本，不渲染无字图）。
- **复用原仓库样式系统**（用户要求，同日）：不只提取约束，把参考仓库 editorial 视觉语言整体注入 prompt——背景 `paper #f5f5f5`（不透明铺满）、主文字/描边 `ink #2d3142`、次要文字/连线 `muted #4f5d75`、强调 `accent #eb6c36`（≤2 焦点）、节点=白底 `#ffffff` + `rx=6` 圆角 + ink 描边 1px、节点名 `sans-serif` 12-14px 600 字重居中（Geist/Instrument Serif 换系统 sans-serif——resvg 无 Google 字体）。示例模板整体升级为该样式。实测渲染 640x320 PNG：文字像素 1.44%、角落 (245,245,245) 背景不透明。注入后 ~1488 字符，仍是单条短规范；只搬视觉样式，不搬 4px 网格/正交连接器/复杂度预算——未重蹈 28 行覆辙

### 诊断结论（约束是否有问题）
- 注入链路完整（前端序列化 → 后端 format_list → hard_constraints 注入）；**约束本身没有 bug**，但「可选生成」措辞给了 LLM 合法跳过路径 + 用户用 deepseek-v4-flash（fast 模型）画复杂 SVG 遵循度低——3 次生成零图表输出（连 svg 围栏都没有）。建议图表功能用更强模型（deepseek-v4 / qwen 大模型）

### resvg_py 缺失（用户实测日志 `No module named 'resvg_py'` 暴露）
- 用户日志实锤 **LLM 其实输出了 chart 围栏**，问题断在渲染链路。双根因：
  1. **start.sh / restart-backend.sh 用裸 `python`**（PATH 里的系统 Python，缺 backend/.venv 的项目依赖）——开发模式后端进程 import 不到 resvg_py。修复：两脚本显式用 `.venv/Scripts/python.exe -m uvicorn`（start.sh + restart-backend.sh）
  2. **PyInstaller spec 收集不到 resvg_py**——Rust 编译的 `resvg_py.pyd`（2.3MB）在包内，`collect_all` 的 `collect_dynamic_libs` 识别不到（实测 binaries=0），打包产物 `_internal/` 无 resvg_py。修复：spec 显式 `glob('resvg_py/*.pyd')` 进 binaries + hiddenimports 加 `resvg_py`/`resvg_py.resvg_py`
- 渲染链路验证：`.venv` 下示例 SVG 渲染出 `chart_1.png` ✓；已跑 PyInstaller 打包确认含 resvg_py（本次**未打包发布**，用户开发模式测试中）
- **待用户验证**：重新 `bash start.sh` 重启后端后再测图表

### 验证
- 后端 11 测试文件 + 前端 98 tests + build + lint 全绿（lint 仅存量 exhaustive-deps warnings）

---

## 2026-08-16 — 图表围栏内部协议收紧（对齐协议设计说明定稿）· 本次打包 0.4.1

> 依据 `pi-docs/图表围栏内部协议设计说明.md`（设计定稿）收紧图表识别协议。核心取舍：**误伤用户内容（静默改写用户数据）不可接受 > LLM 偶尔不遵循围栏约定（warning 重新生成）可接受**——识别从「启发式猜测（双条件/svg 根）」改「协议显式匹配」。

### 协议变化
- **只认 ````chart ```` 专用围栏**：删 chart/svg/xml 三选 + 双条件检测——svg/xml 是通用语言标记，用户内容出现概率高；chart 是专有标记，概率≈0，零误伤
- **删裸内联 `<svg>` 兜底**：围栏外裸 svg 不再渲染——处理面收敛到协议内，用户自己粘贴的 SVG 示例/富文本复制不再被静默改写
- **失败态零残留**：校验/渲染失败由「保留原代码块 + ⚠️ 标注」改为「**替换为** `📊 图表生成失败（原因）` 文本」——产物（存储/展示/导出 MD/HTML）永不含围栏，内部协议不外泄
- **prompt 收紧**：`get_chart_format` 明示「必须用 ````chart ```` 围栏；不要用 svg/xml 围栏，不要在围栏外输出裸 `<svg>`」
- **prompt 硬性规范前移**（质量检查延伸，2026-08-16）：代码校验链保留为兜底，另把 3 处「LLM 产出质量」约束前移到 prompt——① viewBox 与 width/height 必须同时给出且一致 ≤640×640（收窄 P0-2 尺寸绕过面，超限直接失败）② 单图源码 ≤300 行（减少 P1-2 长度上限触达）③ 安全红线显式清单（script/on* 事件/外部 URL 引用/animation/foreignObject——后端会过滤，含此类可能渲染失败）+ 围栏内只放 svg、图后不残留代码（减少 `_IMG_SVG_GARBAGE_RE` 触达）
- **前端只折叠 chart**：`SvgCodeBlock` 仅对 ````chart ```` 围栏折叠（协议围栏防流式卡顿）；svg/xml 恢复普通代码块（用户内容空间不折叠）
- **「图表未生成」warning**：勾选图表但 LLM 未输出 ````chart ```` 围栏（旧习惯 svg 围栏/裸内联/没画）→ 完成时 warning「图表未生成：请重新生成」（双产物 + 单产物 notes 路径）
- **保留不变**：校验链（XML 良构/尺寸双校验 ≤640/64KB/有内容/渲染重试）、安全过滤、`_IMG_SVG_GARBAGE_RE` 垃圾清理（只作用于图片引用后粘连字面量，不触碰用户内容）、P0-3 只入 notes

### 验证
- `tests/test_chart_renderer.py` 重写对齐新协议（12 用例）：svg 围栏/裸内联不处理、失败零残留、混合场景只处理 chart、垃圾清理
- 后端 11 测试文件 + 前端 98 tests + build + lint 全绿

### 影响
- LLM 输出 ````svg ```` 围栏（旧习惯）→ 不再处理，用户看到 svg 代码块 + 完成 warning 提示重新生成（过渡期成本，prompt 已引导 chart）
- 用户内容含 svg/xml 代码块或裸内联 `<svg>` → 不再被图表链路触碰（原可能被静默渲染成图改写）

---

## 2026-08-16 — 全面健壮性/代码质量/用户视角优化（45 项）+ UI 体验升级（4 项）

> 用户要求「全面进行代码健壮性以及代码质量优化以及从用户的角度来再做一些」。3 个 Explore agent（后端健壮性 / 前端用户视角 / 代码质量）+ 人工自查共发现约 60 项问题，分 **P0（崩溃/安全/数据正确性 10 项）→ P1（图表链路/资源泄漏/UX 竞态 21 项）→ P2（中断恢复/死代码清理 14 项）** 三批全部修复，每批独立回归。本次打包 **0.4.0**（新功能 → minor）。

### P0 崩溃/安全/数据正确性（10 项）
- **React hooks 崩溃修复**：`MarkdownImage` 条件 `return null` 移到两个 useEffect 之后（imgSrc 空→非空时 hook 数量 2→4 曾抛「Rendered more hooks」）
- **图表尺寸校验绕过（OOM）**：`_parse_viewbox` 同时解析 `width`/`height` 并入 `MAX_CANVAS` 校验——`width="50000"` + 合法 viewBox 不再绕过（曾 ×2 zoom ≈ 40GB 内存）
- **chart 文件名冲突**：图表**只入 notes**——generate_both 只对 notes 调 render_charts；`_post_process` 加 `render_chart` 参数，polished 三处调用传 `False`，双产物不再互踩 `chart_N.png`
- **asyncio.gather 异常泄漏**：`return_exceptions=True` 逐路处理 + 任一路异常时 cancel 另一任务再 `await gather(...)` 收尾，`_cleanup_video` 不再在另一路运行时提前删视频
- **流式取消/异常路径不删下载视频**：`video_path`/`screenshot_video_path` 删除挪进 `finally`（原每次取消泄漏单次最大 500MB）
- **process 大图 OOM**：LLM 路径前 `stat().st_size` 校验 ≤20MB，超限跳过 LLM 直接走引擎回退（Engine 路径不读 base64 不误拦）
- **assets 服务剔除 svg**：白名单仅留 png/jpg/jpeg/gif/webp——svg 经 FileResponse 直接返回时浏览器导航 URL 同源执行脚本（存储型 XSS 面）；数据目录实测 0 存量
- **删除文档不取消进行中任务**：confirmDelete 前检查 `processingTasks` 有该 doc 任务 → 弹确认 + `cancelTask` + `clearDocTasks` 再删（防孤儿落盘/文档复活）
- **per-product 失败误报整体失败**：后端单产物错误 `{"type":"error"}` → 改发 `warning`（带 mode）——前端 onWarning 只标 partial + toast，不清双产物 buffer、不 failTask（polished 超时但 notes 成功 = 单 warning + notes 保留完整）
- **ingestStream 无终态兜底**：`receivedDoneOrError` 追踪，流结束未收终态 → 「连接意外断开，请重试」（原导入永久卡死）

### P1 图表链路 + 资源泄漏 + UX 竞态（21 项）
- **图表链路**：resvg 渲染/ffmpeg 抽帧改 `asyncio.to_thread`（不阻塞事件循环）；SVG 围栏 >64KB 直接失败标注不渲染；图表勾选能力门控（polished-only 时禁用 + tooltip）+ 被过滤格式一次性 toast + 后端对「勾 chart 但产物无任何 ```svg」发 warning；文案改「AI 生成 SVG，本地渲染为 PNG 嵌入」
- **资源泄漏**：LLM 流式生成器超时/异常后 finally `_it.aclose()`（httpx 连接不再泄漏）；process.py `llm.aclose()` 进 finally；双产物断连时 finally 取消 `_tasks` 孤儿任务；`mkdtemp("limo_ss_")` 永久泄漏修复（doc_dir 空跳过截图替换）+ `tempfile.mktemp`→`mkstemp`（TOCTOU）+ 下载视频父目录/`_cookie_to_file` 临时文件用后清理；双重取消 `except BaseException` 防 `semaphore.release()` 跳过（槽位永久丢失）；上传临时文件 adapter 失败时清理；LLM 重试退避补 `cancel_event` 检查；screenshot_marker 时间戳 ≤60min
- **UX 竞态**：apiFetch 包 try/catch 网络错误归一化（`ApiErrorException('网络连接失败，请检查后端是否运行', recoverable)`，全屏「内容加载失败」不再被瞬时抖动触发）；import fallback 前检查 `signal.aborted` + fallback 传同一 signal + 已收进度时提示「网络恢复重试中」；DocLibrary 改名/标签/删除 try/catch + toast（原静默假成功）；chatStore 4 处同步失败 catch 加 toast；同源导入防重入（相同 title 检查，保留不同源并发）；取消生成同时刷 polished versions（原只刷 notes）

### P2 中断恢复 + 死代码清理（14 项）
- **中断生成恢复（用户确认恢复）**：PartialBanner 加「继续生成」入口 → `limo:continue-generate` 自定义事件 → GenPanel 监听 → `handleGenerate('partial')` 以 `continue_from=partial` 续写残缺产物（与 epub 计划隔离，状态源不同）
- **去重提示改 toast**：「该内容已导入，已为你选中 · title」（原 3s 静默小字）
- **图片处理结果加载失败 toast**（原误显示「点击处理卡片」）
- **DocLibrary 底部任务进度死面板删除** + store `importProgress`/`importStages`/`setImportProgress`/`setImportStages` 字段 + `ImportStage` 接口 + 5 个死字段测试（生产恒 null 的死 UI）
- **formatSize 公共工具**：B/KB/MB/GB 智能单位，图片/文件/视频三处统一（原永远按 KB 显示）
- **主导入大小预检**：pickFile 统一入口（2GB 拒绝 + 1GB 警告）
- **取消单条导入精确匹配**：taskManager 按完整 `__import__N` key 精确取消（原归一成 `__import__` 前缀误取消全部）
- **ingest.py 图片服务去重**：`_validate_tx_id`/`_check_asset_ext`/`_check_asset_within` 三个公共 helper 消除 serve_media_file/serve_asset 重复 + 全中文化错误（无效的文档 ID/不支持的文件格式/路径穿越已拒绝/文件不存在）
- **generate.py 日志风格收敛**：9 处 `logger.%s` → `app.core.logging` kwargs/f-string，删未用 `import logging`
- **死代码清理**：`parseSSEStreamWithAbort`（两分支相同）、`Conversation` 接口、`formatTranslate` 死状态、`_export_filename`、`/process-image/{id}` 废弃路由、generate.py `Optional`/`_resolve_model`/`_track_generation`、prompt.py `_STYLE_HEADER`/`PDF_STYLE_MAP`/`VIDEO_STYLE_KEYS`/`EXCEL_STYLE_KEYS`（`STYLE_MAP`/`DOC_STYLE_MAP` 被 capabilities.py 使用保留）、capabilities.py `VIDEO_STYLE_KEYS`
- **ChatSidebar 裸错误处理**：改 `getErrorMessage(e)`（丢结构化 message）

### UI 体验升级（4 项）
- **阅读宽度动态跟随面板**：内容上限 `max(1100, 面板宽 - 160)`——折叠 GenPanel/DocLibrary 后面板变宽 → 阅读内容同步放宽（最高 ~1440px，两侧各留 ~80px 边距）利用空白空间
- **编辑/输入统一 mono 字体**：对话输入框 + 文本/链接/图片/文件 URL 粘贴框改 `font-mono`（产物编辑器源码区/代码块本就是等宽）
- **编辑器分屏可拖拽调宽**：TextareaMarkdownEditor 源码 | 预览 之间加 ResizeHandle 分隔条（默认 50%，clamp 25%–75%）
- **欢迎文档三产物化**：欢迎文档（空态示例文档）改造成 LiMo 三产物形态——**笔记**（欢迎/简介+快速开始）/ **整理版原文**（面向开发者的全面功能总览）/ **转录**（CHANGELOG 更新记录），tab 样式对齐主工具栏，内容提为模块级常量便于同步

### 文档同步
- CLAUDE.md：待办 UI/UX 域新增 4 项；前端模式更新（欢迎文档三产物 = 模式 34 扩展、mono 输入字体、分屏拖拽、动态阅读宽度）
- STATUS.md：版本 0.4.0、前端测试 103→98、工程质量表更新
- BUILD.md：产物文件名 0.3.1 → 0.4.0

### 测试
- 后端 **11 个测试文件全绿**（含 P0 新增 `tests/test_chart_renderer.py`：OOM 尺寸校验回归 + `render_chart=False` polished 不渲染 + 同 doc_dir 双产物文件名不互踩）
- 前端 `npm test`（**98 tests**）+ `npm run build` + `npm run lint`（0 error）全绿

---

## 2026-08-16 — 图表作为笔记可选组件 + AI 审核（fact_check）移除转历史

> 用户 @ `pi-docs/图表功能与fact_check移除方案.md`（2026-08-15 评审定型）要求实施。两块：① 移除 AI 审核（事实核查产品无强需求、验证链路重），② 图表作为生成笔记的可选组件（对齐「AI 输出意图 → 本地确定性工具收口 → Markdown + assets 引用」的截图模式）。

### ① AI 审核（fact_check）移除转历史

**后端**：
- 删除 `routers/fact_check.py`、`generation/fact_check.py`（旧 FactCheckEngine）、`generation/review.py`（ReviewEngine）、`agent/evidence.py`（Evidence Manager）+ main.py/spec 注册；`types.py` 删 GenerateMode.FACT_CHECK / MarkdownView.fact_check / FactCheckClaim / FactCheckResult
- `product_registry.py` 删 fact_check ProductDef；`export.py` 6 处（mode_label / 内容源回退链 / fact_check 分支）；`prompt.py` 删 7 个事实验证 prompt（旧 FactCheck 3 + AI Review 4）+ 相关温度速查；`tools.py` 删 4 个死工具（search_official/get_url_metadata/today/is_recent，仅被 review.py 用）
- 模型解析注释 `（generate / chat / process 共用）`
- **历史保留**：已生成的 `fact_check.md` 文件保留不删（转历史，无读取入口；export 内容源回退链不再含 fact_check → 旧文件 404，前端无 tab 展示）

**前端**：删 tabRegistry fact_check tab + contentSources factCheck getter + store types/contentSlice/chatSlice 字段（factChecking 一并移除）+ api `streamFactCheck`/`ReviewChunk` 类型 + PreviewArea `handleFactCheck`/case 分支 + ExportMenu/TaskPanel/GenPanel/constants 相关行 + genPrefs 防御性迁移（旧 `fact_check`/`factCheck` localStorage 清理）

**测试**：CopyContentBtn/tabRegistry/store 测试删 fact_check 断言；`test_markdown_edit.py` 的 `('summary','fact_check','transcript')` 非法 mode 用例保留（fact_check 仍是非法模式）

### ② 图表作为笔记可选组件

**后端**：
- 新建 `app/services/chart_renderer.py`：检测 ` ```svg/```xml ` 代码块（双条件：围栏语言∈{svg,xml} 且含 `<svg` 根元素）→ 安全过滤（script/on*/外部引用剥离）→ 校验链（XML 良构 → viewBox ≤640×640 → 有形状元素）→ resvg 渲染 PNG(2x) → `assets/chart_N.png` → `![图表](assets/chart_N.png)`；失败保留原代码块 + `> ⚠️ 图表生成失败（{reason}）` 标注，不阻塞整体生成；`_render_png` 惰性 import resvg_py（spec 需 collect_all）
- `prompt.py` 新增 `get_chart_format(source_type)`（基础规范 + 来源差异段：DOC→架构图/层级树、网页→流程/结构树、视频→流程步骤图/时间线）；`AVAILABLE_OPTIONS` 加 chart（video/webpage/pdf/docx/pptx/text，xlsx/audio 无）；`_ALL_FORMAT_FNS["chart"]` + `_build_hard_constraints` 加 source_type 参数注入
- **只入 notes**：`_POLISH_FORMAT_FNS` 无 chart，polish 不注入；后处理挂载 generator.py generate/generate_both + generate.py `_post_process`（在 `_normalize_generated_images` 之前）
- `LiMoBackend.spec`：hiddenimports 加 `app.services.chart_renderer` + collect_all 加 `resvg_py`
- 依赖：`uv add resvg-py`（清华镜像，resvg-py==0.4.0；API 为 `svg_to_bytes(svg, zoom=2.0)`，无 scale 参数）

**前端**：
- GenerateOptions 两区块（视频/网页、文本/文档）各加「图表」勾选（默认不勾选），排除 epub（独立解读管道）；xlsx/图片本就无笔记格式区
- genPrefs 加 `formatChart` 持久化；GenPanel state/saveGenPrefs/formatStr（chart 进 formats，availableFormats 过滤自动生效——capabilities genOptions 直引 AVAILABLE_OPTIONS）
- 欢迎文档第 3/5 条：加图表说明、删事实验证

### 文档同步
- CLAUDE.md：AI 审核管道 section → 图表管道 section；待办 AI 审核域 → 图表生成域；合计 72/74→68/70；chatSlice 只留 generating；取消机制 5 端点
- STATUS.md：端点 78→76、后端 py 95→92（agent 6/generation 10/services 3）、管道表事实验证→图表、进度删 AI 审核（72/74→63/64）、目录结构更新

### 测试
- 后端全量（prompt/adapters/engines/store/url_security/capabilities/plugin_loader/markdown_edit + e2e 冒烟 36）全绿
- 前端 `npm run build` + `npm test`（103 tests）全绿

---

## 2026-08-15 — 产物编辑能力（notes/polished 手动编辑 + 覆盖保护）

> 用户 @ `pi-docs/产物编辑方案.md` 要求实施产物编辑。此前产物（notes/polished）纯只读，用户无法修正 AI 错误、补充内容、导出前润色。零新依赖起步（textarea 版），组件抽象预留 ByteMD 增强路径。

### 后端（最小面：零改 store.py / generate.py）
- 新增 `PUT /api/markdown/{transcript_id}`（`app/routers/markdown_edit.py`）：校验 mode ∈ {notes, polished} / doc 存在 / 内容非空；**复用 `save_markdown(mv, extra={"edited": True})` 写 edited 标记**（`**(extra or {})` 零改 store 本体）；两版制自动保留生成版为 prev 可回退
- **style 保留**：编辑保存继承当前最新 entry 的 style，不因编辑丢失排版语义
- **图片 URL 反转**：GET 返回时 `rewrite_asset_urls` 把 `assets/` → `/api/assets/{tx}/`；编辑保存前 `_unrewrite_asset_urls` 反向转回，绝对 URL 不写回磁盘污染导出/二次渲染。形态对称（只处理 `![alt]()` 一种，用户手动输入的 `<img>` 不反转），`re.escape(tx_id)` 防注入
- **清除语义**：重新生成链路 `save_markdown` 不传 edited → 新 entry 无该字段 → 标记自动消失（零改 generate.py）

### 前端
- 新增 `TextareaMarkdownEditor.tsx`（`MarkdownEditor` 接口第一实现）：工具栏（B/I/S｜H1-3｜列表/引用/代码块/表格｜链接｜撤销/重做）+ 左源码右分屏实时预览（debounce 300ms，复用现有 REMARK/REHYPE + normalizeLatexDelimiters）+ 快捷键 Ctrl+B/I/S、Ctrl+Z/Y（手动历史栈 cap 100）+ 选区包裹/行级操作
- **内容区工具条**（用户确认入口位置）：summary/polished tab 的 MarkdownPreview 上方窄条，含「已手动编辑」徽章 + 铅笔编辑按钮；显示条件排除 Excel（其 summary 走 notes 内容源但数据摘要不可编辑）；生成中/任务中置灰；fact_check/transcript 等 tab 不渲染
- 保存 = PUT → 更新 currentContent/currentPolishedContent + meta（含 edited）+ versions + toast「已保存」；未保存防丢：切 tab / 切文档两态 `window.confirm`（用户确认）+ beforeunload 拦截（Tauri 关窗为边缘情况，已知限制标注）
- **覆盖保护**：`markdownEdited` store 状态（独立于 markdownMeta，两产物同时记录）——GenPanel 重新生成前检测 → confirm「将覆盖手动编辑内容」；EpubInterpretPanel 解读/继续/重跑同样确认；生成完成/解读完成自动清除标记

### 已知限制（评审确认）
- **两版制 × 连续编辑**：连续编辑 2 次会把编辑版 1 顶为 prev、生成版被挤出两版制（无法回退生成版）。接受——两版制本为「生成版 + 最近编辑」语义，不为此改 save_markdown 版本逻辑（会牵连生成链路）

### 测试
- 新增 `backend/tests/test_markdown_edit.py`（11 用例：保存成功/URL 反转/prev 保留/polished/mode 校验/不存在/空内容/清除语义/style 保留/无产物拒绝/超限拒绝）全绿
- 前端 `store.test.ts` 加 `setMarkdownEdited` 单测（103 tests 全过）；`tsc -b` + vite build 通过；oxlint 无新增 error
- 后端 10 测试文件全绿（含 e2e_smoke 36/36）

### 健壮性维护（同日，用户要求「健壮性代码维护 + 从用户角度修复漏洞」）
- **覆盖确认不再被「查看旧版」清除**：`loadVersion` 曾用 `!!md.edited` 覆盖文档级 `markdownEdited`——编辑保存 v2 后切到 v1 查看会把标记清成 false，之后重新生成不再弹确认。现 `markdownEdited` 只在「保存编辑 / 生成完成 / 切文档」时变化（查看版本不影响），确认语义与两版制回退一致
- **编辑徽章跨 tab 串扰修复**：徽章原读单槽 `markdownMeta.edited`（切 tab 不更新）→ 编辑 notes 后切到 polished tab 会错误显示「已手动编辑」。改读两产物独立的 `markdownEdited[editMode]`
- **编辑器历史栈 ref 化**（`TextareaMarkdownEditor`）：`undo`/`redo` 原在 setState updater 内调副作用（`setFuture` + `onChange`）——React 18+ 不保证 updater 只执行一次（StrictMode dev 下必然双调用），会导致 future 栈重复记录 + onChange 双发；改为 ref 历史栈 + 长度 state 驱动按钮 disabled，撤销/重做读即时状态，连按 Ctrl+Z 也正确
- **后端 PUT 加固**：`MD_EDIT_TOO_LARGE`（>5M 字符拒绝）+ `MD_EDIT_NO_PRODUCT`（transcript 存在但该 mode 无产物时拒绝——前端无产物不显示编辑入口，此为 API 层防御防直接调用创建无源产物）
- **e2e Windows PIPE 缓冲死锁修复**：`test_e2e_smoke.py` 后端 subprocess 用 `stdout/stderr=PIPE` 但从不读取 → Windows 匿名管道 ~4KB 缓冲被 uvicorn 日志填满后 `write()` 阻塞事件循环 → `GET /api/config` 间歇超时（4 次复现 3 次失败，曾误判为端口冲突 flaky）。改为写入临时日志文件（不阻塞 + 保留后端日志供诊断）

### 影响范围
- 后端：新增 `routers/markdown_edit.py` + `main.py` 注册；store.py / generate.py 零改动
- 前端：`api/generate.ts`（saveMarkdownContent）、`api/core.ts`（MarkdownView.edited）、`store/types.ts` + `contentSlice.ts`（markdownEdited）、`PreviewArea.tsx`、`GenPanel.tsx`、`EpubInterpretPanel.tsx`、新增 `TextareaMarkdownEditor.tsx`；欢迎页快速开始同步（模式 34）
- 只读预览零变化（不点编辑 = 原样）；fact_check/transcript/Excel 均不可编辑

---

## 2026-08-14 — 插件 venv 检查路径遍历异常修复

> `GET /api/plugins` 500：`_venv_valid()` 里 `base_py.exists()` 对 AppData 下 uv 托管 python（`C:\Users\...\AppData\Roaming\uv\python\...\python.exe`）做 `stat()` 时抛 `OSError: [WinError 448] 不受信任装入点`。此前 `except OSError` 只保护读 pyvenv.cfg（L452），`base_py.exists()` 在 try 外，异常一路冒泡到 router 变 500。

- **修复**：`_venv_valid()` 整个检查包进 `try/except OSError`——路径遍历失败（含 WinError 448）视为 venv 不可用返回 `False`（而非抛 500），并 `logger.warning` 记录原因（不静默）。语义与既有「失效 venv 视为未安装」一致：前端显示「一键安装依赖」，重建 venv 会用可访问的 base python 自愈
- **验证**：模拟 WinError 448（monkeypatch `Path.exists` 抛 OSError 448）→ `_venv_valid` 返回 `False` + warning 日志；后端 8 测试全绿
- 影响范围：仅 `backend/app/engines/plugin_loader.py` `_venv_valid()`；`enumerate_plugins`/路由无改动

---

## 2026-08-14 — 打包体积优化（docling 清理 + ffmpeg 精简）

> 用户 @ `pi-docs/打包体积优化计划.md` 要求执行打包体积优化。实测确认现状比计划记录更极端（ffmpeg 实际 434M 而非 194M）。执行 P1 + P2b，安装包从 ~265M/~366M 降到 **~148M/~195M**。

### P1 docling 依赖残留清理
- `backend/pyproject.toml` 删 `docling>=2.115.0`（docling 引擎 2026-08-03 已移除但依赖没删，spec 早清干净但 venv 全家残留）+ 顺带删 `pytesseract>=0.3`（Tesseract 已移除的残留，代码零引用）
- `uv sync` 移除 docling/torch/torchvision/transformers/scipy/rapidocr/tree-sitter/pywin32 等全家（venv 原 ~250M+）
- `LiMoBackend.spec` 删 hiddenimports 残留 `filelock`（docling 传递依赖时代产物，代码零引用）
- 后端 8 测试全绿（e2e_smoke 36/36）+ 打包版 exe health 200

### P2b ffmpeg 精简 build 替换
- gyan.dev full build（434M：ffmpeg 227M + ffprobe 227M）→ **gyan.dev essentials 8.1.1（194M）**
- 下载走 **ghfast.top GitHub 加速代理**（gyan.dev 官网 ~48KB/s 太慢；ghproxy.com 已失效）
- 验证：libmp3lame/pcm_s16le/mjpeg 编码器 + h264/hevc/av1/aac/mp3/opus 解码 + mp4/mkv/webm/avi/flv 容器 + ffprobe 探测；4 个调用点回归通过（video.py MP3 提取 / asr_cloud.py WAV / video_reader.py+video_helper.py 截帧）
- 完整版备份 `~/.limo/ffmpeg-backup-full/`；精简版分发源 `~/.limo/ffmpeg-portable/`

### P3 markitdown
- **保留**（用户决策，保守）。onnxruntime 39M 大头是 capi 34M 必需 DLL，tools/transformers 纯 py 仅 ~3.5M 不值得优化

### 体积结果（实测）

| 项 | 前 | 后 | 变化 |
|---|---|---|---|
| PyInstaller 产物（含 ffmpeg） | 918M | 443M | 省 475M（-52%） |
| NSIS 安装包 | ~265M | **148M** | 省 ~117M（-44%） |
| MSI 安装包 | ~366M | **195M** | 省 ~171M（-47%） |

### 文档
- `pi-docs/打包体积优化计划.md` 更新为已执行（实测数据 + 下载坑）；`docs/BUILD.md` 产物体积 + 精简 ffmpeg 源说明；`CLAUDE.md` ffmpeg 内置描述

---

## 2026-08-14 — 文档全面更新 + AGENT-ONBOARDING 上手文档

> 用户要求三件事：① 全面更新所有文档；② 写一份面向其他 AI agent 快速上手/接手开发的文档（打包章节要详细）；③ 清理和合并不必要/过时的文档。用 3 个 Explore agent 逐份对比文档与代码 + Plan agent 交叉验证，产出偏差清单后逐份对齐。**所有计数以代码实测为准。**

### 文档更新（10 份 docs + design + 根目录；docs/ 由 11 → 12 份）
- **大改**：export-pipeline.md（TAB_EXPORT → tabRegistry/contentSources 机制、GFM checkbox/时间戳 badge/标题锚点、KaTeX 双路径、补 3 导出端点、行号）；design/components.md（品牌名 MyBigWork→LiMo、ResizablePanelGroup→SlotLayout 插槽引擎、生成面板左插槽、AI 浮窗唯一形态、设置页 7 tab、快捷键）；prompts-inventory.md（Polish 白名单 +link/screenshot 移 translate、AVAILABLE_OPTIONS 补 formulas、SYSTEM_PROMPT→agent/prompts 分层、SSE 事件、AI 审核四阶段、prompt 计数）；user-scenarios.md（场景 9b 跨多篇已实现、6/7 状态修正、主动提醒部分落地）
- **中改**：architecture.md（引擎 10、路由 14、load_config 位置、格式残留修正 + 补零文档模块 FTS5 搜索/Slot 注册表/download/sse/transcript 契约/SessionManager/ocr_preprocessor/cookie_parser/视频 utils/sample_doc/config 同步/启动时序/protocols）；STATUS.md（91 py/74 端点/72-74 进度 + 目录结构）；CLAUDE.md（文档索引 + 对话协议、测试 8 文件/101 tests、关键模式 34 条、导出系统描述、待办 72/74、语义搜索措辞）
- **小改**：frontend-patterns.md（streamBuffers/backendStatus/appUrl/mbw 14 key）；对话协议.md（done 无 usage、ref 随 tool_result 携带）；qianwen-models.md（context_window/推荐模型）；BUILD.md（产物 0.3.1/hiddenimports 清单）；plugins.md（管理 API 补两行）
- **修正 2 处隐藏文档 bug**：CLAUDE.md 引用不存在的 `scripts/build.sh`（改指向 BUILD.md）；start.sh 品牌名 DeepDraft → LiMo

### 新文档
- **docs/AGENT-ONBOARDING.md**（新）：课本式上手教程——从零跑通 → 架构全景 → 六大注册表 → 数据流横切 → How-to 8 任务 → **打包发布（最详细：命令级步骤 + 顺序铁律 + 陷阱排查对照表 + 新增依赖 checklist + 发布门禁）** → 测试门禁 → 调试速查 → 上手练习

### 清理（温和清理，用户确认）
- 删除 `pi-docs/`（3 份已完成功能的规划稿，CHANGELOG 有完整记录）；删除 `frontend/README.md`（Vite 模板残留）；删除 `frontend/src/components/ui/SetupWizard.tsx`（死代码，OnboardingWizard 替代）；修正 `backend/plugins/examples/README.md`「两个模板」→「三个模板」
- SECURITY_AUDIT.md / backend/tests/TEST_REPORT.md 历史时点报告保留原样（用户确认）

### 验证
- 前端 `tsc -b` + vite build ✓、oxlint 无新增 error ✓、vitest 101/101 ✓；后端 8 测试脚本全绿（含 e2e_smoke 36/36）✓
- grep 抽查：文档新引用的 14 个文件/端点全部真实存在（slotRegistry/tabRegistry/contentSources/streamBuffers/search.py/transcripts_fts/agent/prompts/…）

---

## 2026-08-13 — 首次体验与健壮性（P1-P3 实施）

> 用户提交「首次体验与健壮性计划」。深度现状核验修正 4 处偏差：中转站后端已存在（0 新工作）、OnboardingWizard 已存在（改扩展）、启动探活已存在（补持续探活）、OCR/ASR 缺 key 已基本显式（补唯一缺口）。

### P1 健壮性 + 可观测性
- **前端持续探活**（`lib/health.ts` 新 + `uiSlice.backendStatus`）：10s 轮询 `/api/health`，连续 2 次失败 → `down`（顶部横幅「后端服务异常，正在重试…」），连续失败超 30s → `crashed`（「后端已崩溃，请重启应用」），恢复自动消失。与启动探活（只管「后端未就绪加载屏」）职责分离共存
- **`/api/health` 别名**：`/health` 是根路径（无 `/api` 前缀）且 Vite proxy 只代理 `/api`、`/static`（浏览器下走 SPA fallback）→ 后端加 `/api/health` 别名，前端 `apiUrl('/health')` 双环境通
- **请求级日志**（`main.py` middleware 扩展）：`call_next` 前后计时记录 `method/path/status/duration_ms`，慢请求（>2s）WARN、正常 INFO；**SSE 按响应头 `content-type: text/event-stream` 判定不判慢**（非路径白名单——新增流式端点不漏）；跳过高频噪音（health / static / assets 图片渲染）

### P2 首次体验
- **配置向导第三屏**（OnboardingWizard 扩展）：阅读氛围 → 系统组件 → **AI 配置**（数据源 `capabilities.missingKeys`）——LLM 厂商卡填 key + `testProviderConnection` 连通验证（复用已有 API）；DashScope 引擎卡填 key（`PUT /engine-providers/dashscope` 后端自动双向同步 `cloud.dashscope`）；已配项保存后从列表消失；可整屏跳过；老用户 missingKeys 全空自动跳过不打扰
- **`capabilities.missingKeys`**：后端 `_compute_missing_keys` **复用 `model._get_providers`**（含全局 `llm.api_key` 同步 + sk-ws/sk-ant 防错配）——llm 遍历 `llm_models` 厂商查 provider.api_key 为空，engine 复用 `cloud.dashscope` 检测
- **缺 key 主动提示**：ImportSection 图片/视频区**内联提示条**（X 关闭 + localStorage `mbw_dashscope_hint_dismissed`；非 toast——切类型不重复打扰）；chatStore `dispatchSend` 首次发送缺 LLM key toast（localStorage `mbw_llm_hint_dismissed`）
- **后端 OCR/ASR 可见化补缺**：video.py `_adapt_local`（本地视频）FatalError 原写死「未获取到转写结果」→ 汇总 warnings 带原因（对齐 URL 路径，如「ASR dashscope-asr 失败: DashScope API key 未配置」）。其余路径已显式无需改（document 扫描件占位带原因 / process 图片 AppError·TranscribeError 带 detail / URL 视频 FatalError）；image.py `_ocr` 为无调用方死代码不改

### 范围外
- P3（32-bit / 按需下载）后置；日志分级/轮转体系重构（只补请求级）；tavily 配置向导步骤

### 影响范围
- **后端**：main.py（health 别名 + 请求日志）· capabilities.py（missingKeys）· video.py（本地 ASR detail）+ tests/test_capabilities.py（新，6 用例）
- **前端**：lib/health.ts（新）· uiSlice.ts（backendStatus）· App.tsx（横幅 + 探活）· OnboardingWizard.tsx（第三屏）· api/core.ts（Capabilities 类型）· ImportSection.tsx / chatStore.ts（缺 key 提示）
- **验证**：前端 `tsc -b` + vitest 101/101；后端 6 脚本 + e2e_smoke 36/36；实测 `/api/health` 200 + 请求日志输出 + missingKeys 字段

---

## 2026-08-06 — PDF 笔记图片显示修复

> 用户反馈：PDF 笔记（DSpark，pymupdf4llm 插件导出）图片显示有问题，生成完成后需刷新才正常。

- **根因 1（图片 404）**：LLM 生成 notes 时把转录里的 `assets/img_xxx.png` 幻觉改写成 `/api/files/assets/img_0003.png`（错误绝对 URL → 前端 404）。transcript/polished 保留相对路径正确，notes 错。
- **修复**：generate.py `_post_process` 加 `_normalize_generated_images`——把 `/api/files/...` / `/api/assets/{tx}/...` 绝对 URL 规范化回相对 `assets/{filename}`（仅当文件在本文档 `{doc_dir}/assets/` 存在）；data URI / 外部 http / 已是相对路径保留。顺带修复 `_post_process` 传 `store.root` 当 doc_dir 的隐藏 bug（改传文档目录）
- **根因 2（需刷新才显示）**：前端 `MarkdownImage` 加载失败后 `broken` 永久，文件就绪不自动恢复 → 需手动刷新
- **修复**：`MarkdownImage` 加「src 变化重置 broken」+「加载失败 2s 自动重试」——生成完成后图片文件就绪自动显示，无需刷新

---

## 2026-08-06 — 插件 venv 健壮性修复

> 用户报告：pymupdf4llm 插件导入失败 `No Python at "C:\Users\...\uv\python\...\python.exe"`——插件 venv 的 base python（uv 管理的 python）路径失效。

- **`_plugin_subdir` 修复**：用声明文件 stem（而非 `adapter.name`）解析插件子目录——`pymupdf4llm_heavy.py` 里 `name=pymupdf4llm` 但子目录是 `pymupdf4llm_heavy/`，此前按 name 解析到错误路径导致 requirements/venv 找不到（`install_plugin_deps` 误报 no-requirements）
- **`_venv_valid` 新增**：校验 venv python 存在 + `pyvenv.cfg` 的 base python（home）可用——失效 venv（base python 被 uv 升级/清理）视为未安装，前端显示「一键安装依赖」
- **`install_plugin_deps` 自愈**：检测到失效 venv → 删除重建（用当前可用 base python），不再因 venv 存在而跳过
- 插件 venv base 策略：源码模式用后端 python（sys.executable）；打包版用系统 python（PyInstaller 无法用自身 exe 跑 `-m venv`）；都不可用 → 失败显式引导手动（`no-python` + hint）

---

## 2026-08-04 — PDF 链路优化 + 两个生成 bug 修复

> 用户诉求：PDF 整体转图片（供 OCR/多模态）+ OCR 引擎可选图片转文字小模型（不只多模态大模型）。

### pdf-image 引擎 + OCR 引擎名称检测
- **pdf-image Doc 引擎**（`doc_pdf_image.py` 新）：pypdfium2 渲染每页为 PNG（media_dir 写 `page_NNNN.png` / data URI），标记 is_scanned；绑定 `doc:pdf` 后可替代 pypdfium2 文字提取，对纯图片/扫描 PDF 更稳（绕开 pypdfium2 提取嵌入图的坏图问题）；document.py 对 pdf-image 跳过重复渲染+OCR（产物本身是图片转录）
- **OCR 名称检测**：`_PROVIDER_IMAGE_RULES` dashscope include 加 `ocr` → `qwen3.5-ocr` 等自动标 image，生成时「OCR 模型」下拉可选到图片转文字专用小模型；检测结果用户可手动取消/标记（`updateModel` capabilities 已支持）
- **OCR 默认绑定**：`engine_bindings.ocr` 未绑定且 dashscope 有 key → 默认绑 `qwen-vl-ocr-latest`（图片转文字专用，比 VL 大模型便宜），避免「无可用 OCR 引擎」

### 生成 bug 修复
- **PIL 坏图崩溃**：`_sanitize_image_uri` 校验 data URI（PIL 可识别 + 可选优化），损坏/截断图片跳过不中断生成流（`content_preparer`，根因是扫描/图片层 PDF 提取的坏图）
- **公式 `\tag` 渲染**：含 `\tag` 的内联公式被当 inline 渲染 → KaTeX 报 `\tag works only in display equations` 红色 katex-error；前端 `markdown-plugins.ts` + 后端 `export.py` + `render_math.js` 三处同步把含 `\tag` 内联公式提升为 display（导出 HTML 一直正常因为走 display）

---

## 2026-08-04 — 对话上下文管理（C1-C5 · 依 pi-docs/对话上下文管理计划.md）

> 用户确认「C1-C5 全量」。对话重写后上下文管理未实现：历史固定 40 条 + agent 二次截 20 条、文档引用每篇 1000 截断——无模型感知、无 token 预算。用户 token 敏感，不同模型窗口/价格不同需适配。

### C1 模型元数据
- `core/token.py`（新）：公共 tiktoken 估算（从 content_preparer 抽出复用）
- `config.py` llm_models 加结构化 `context_window`（deepseek 1M / qwen 128k / gpt 128k / claude 200k / gemini 1M）
- `model_resolver.resolve_context_window` 四层：用户自定义 > 预设（合并 DEFAULT，兼容旧 config 缺字段）> provider 默认表 > 保守 32k
- `model.py` addModel/updateModel 支持 context_window/price_in/price_out；ProviderSettings 模型元数据编辑弹窗
- price 预设不填（None），用户配了才启用费用显示——避免错误价格误导

### C2 预算引擎
- `core/chat_context.py`（新）：compute_budget（窗口×0.7 留输出）、trim_history（反向裁剪跳 isError）、build_doc_context（富余 2000 / 紧张 1000 截断）
- chat.py 按预算组装（system→doc→history 分配），meta 事件带 context_window/budget；agent.py 移除 `history[-20:]` 二次截断
- 修正 NextChat 缺陷：其 max_tokens 同时当输入预算和输出上限，本引擎预算=窗口×0.7 留输出空间

### C3 历史压缩
- `agent/compress.py`（新）：超阈值（未压缩历史 > 预算×60%）LLM 摘要早期消息；压缩模型 fallback：deepseek-chat → 当前模型 → 跳过本轮不阻塞
- Conversation 加 summary/last_compress_index/clear_context_at；session.set_clear_context（压缩基准重置）
- chat.py 压缩检查 + summary 进 system + SSE `compressed` 事件

### C4 前端上下文感知
- `utils/token.ts`（新）：estimateTokenLength（NextChat 同款字符启发式）+ formatTokens
- ChatInput：上下文占用进度条 + 输入 token + 每轮 in→out + 费用估算（配 price 才显示）
- chatStore：meta 存 budget/price、done 存 usage、compressed 提示、clearContext action
- ChatCore：清除上下文按钮 + 压缩/隐私提示条；ProviderSettings「对话上下文」区块（预算档位/历史上限/文档引用模式）
- conversations.py `POST /conversations/{id}/clear-context`

### C5 打磨
- agent.py `_create_with_retry`：create 阶段 429/5xx 退避重试（1s/3s），stream 中途失败不重试（避免重复输出）
- build_doc_context 按 docId+limit 缓存（contentHash 失效），同文档多次对话不重复处理
- 协议文档补 compressed / meta budget·price / done usage / clear-context

---

## 2026-08-04 — 对话模块全量重写（P1-P6 · 依 pi-docs/对话重写方案.md）

> 用户确认「放开手脚做完（无实际用户）」。LiMo 对话 UI 原本简陋（ChatCore 360 行 = 消息列表 + Markdown + 导出），本次按建议稿全量重写，对标 NextChat 的对话管理核心。

### 前端：独立 chatStore + 消息层组件树

- **独立 `store/chatStore.ts`**（不并入主 useStore）：流式 delta 高频更新隔离；ChatMsg 消息模型（`streaming`/`isError`/`toolCalls`/`docRefs`/`thinking`）+ 流式状态机（send/stop/regenerate/edit 分支语义/delete）
- **chatSlice 瘦身**：对话管理字段迁至 chatStore，只留 generating/factChecking + AI 浮窗 UI
- **组件树重写**：MessageItem（气泡/打字机 key 技巧/消息操作栏/工具气泡三态/引用 chips/思考折叠）、ChatInput（textarea 多行 + IME 修复 + 停止按钮 + @ 引用浮层）、MessageList（stick-to-bottom + 长会话分页窗口）、ChatSidebar（搜索/归档折叠/右键菜单）、ChatCore 整合
- **打字机**：streaming 时 ReactMarkdown `key='loading'` 增量重渲染，结束切 `'done'` 强制重挂载（对齐 NextChat）
- **消息操作**：复制 / 编辑（user 分支语义：截断+重发；assistant 本地覆盖）/ 重新生成 / 删除；流式中停止生成

### 后端：SSE 8 事件 + 会话 API 增强

- `agent.py`：tool_result 带 `refs`（从 search_docs/lookup_doc/list_docs 结果提取 docId/title）；`_stream_response` 探测 `reasoning_content` → **thinking 事件**（deepseek 思考链折叠展示）
- `chat.py`：lookup_doc 结果补 docId；chat_stream 加 `doc_refs` 参数；sse_stream 收集 tool_calls/refs/thinking 持久化
- `conversations.py`：`PATCH /conversations/{id}`（archived 归档）+ `PUT /conversations/{id}/messages`（全量 rebuild——编辑/删除/分支截断统一入口）
- `session.py` append 透传 doc_refs/tool_calls/thinking；`types.py` ChatMessage 扩展字段；store.py 会话 index 带 archived
- 新 `docs/对话协议.md`：SSE 事件流全量文档化

### 关键行为确定

- **中断持久化**：stopGenerating → abort → 后端 cancel_event → `done+cancelled`，部分回复**也会保存**（前端标「已停止」+ 保留部分内容）
- **docIds 上下文策略**：「当前选中文档 + 本条 @ 引用」（每轮动态组合去重），比建议稿的「会话内引用集合」更省 token（对齐用户 token 敏感）；已同步方案文档

### 验证

前端 100 tests（含 chatStore 18 用例）+ tsc 零错误；后端 5 测试 + e2e 36 全绿。

---

## 2026-08-03 — 视频笔记 prompt 过滤规则补「下集/续集预告」

用户审阅生成的视频笔记时发现「下集预告」被做成独立章节——纯吊胃口套话（"我知道你很急但你先别急，咱们下个视频见"）零信息量。根因：`_NOTES_TASK` 的广告/无关内容过滤规则只覆盖硬/软广告与填充词，**漏掉「下集预告/续集引流/催更」**这类视频片尾常见套话。修复：过滤规则补明确类别——纯吊胃口套话必须省略；若预告带实质信息（如下集主题）最多一句带过，不作为独立章节。

---

## 2026-08-03 — 视频下载修复：max_filesize 静默拒下载 + 按大小适配超时 + 前端提示

- **B 站 m4a 视频导入失败根因**：`max_filesize: 200MB` 让 yt-dlp 静默拒下载（390MB 视频超限，yt-dlp 只返回 info 不报错）→ `_download` 返回不存在的 `video.m4a` → ASR 报"文件不存在"
- **修复**：max_filesize 200MB → 2GB（对齐导入上限）；下载后校验文件真实存在非空（失败显式化）；临时文件查找排除 `.part/.ytdl` 残留
- **按大小适配超时 + 前端提示**（用户方案）：预取视频大小（`extract_info(download=False)`，format 与下载一致避免拿到含视频轨的虚高大小 1285MB→391MB）→ 分档 socket_timeout（<50MB:60s / 50-200:120s / 200-500:180s / ≥500:300s）→ 下载前推送「视频约 X MB，预计下载约 X 分钟」（实测 ~3MB/s 估算）

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

## 2026-08-03 — 插件 zip 安装器：设置页选文件安装插件

补全插件安装闭环（此前需手动拷贝到 `~/.limo/plugins/`）：

- **后端** `POST /api/plugins/install`：multipart zip 上传 → **zip slip 防护**（条目路径必须落在解压目录内，拒绝 `../`/绝对路径）→ strip 单一顶层目录（GitHub source zip）→ 校验 EnginePlugin 声明 `.py` → 解压到 `~/.limo/plugins/`（覆盖同名）→ 返回 `hasRequirements` 提示「重启后一键装依赖」。覆盖安装 OSError（venv 占用）→ 明确 AppError 而非 500
- **前端**：插件页 banner 加「安装插件」按钮（`.zip` 文件选择）→ 上传 → 成功 toast + 重启提示；AppError 明确反馈
- 安全：zip 大小 ≤ 50MB；插件 `.py` 为用户主动选择的第三方代码（固有风险自担），解压路径安全由后端保证

验证：正常 zip / GitHub source zip strip / **zip slip 攻击拒绝** / 覆盖安装 / 非 zip 与无声明明确提示——5 场景全过。

---

## 2026-08-03 — .xls 旧格式支持 + .doc/.ppt 明确提示

- **.xls**：ExcelEngine 加 **xlrd** 分支（openpyxl 不支持旧二进制格式），逐 sheet → MD 表格（数字/日期规范化），与 .xlsx 产物一致。新增依赖 xlrd + spec hiddenimports
- **.doc / .ppt**（Word/PPT 旧二进制格式）：python-docx/pptx 只支持 OpenXML → DocumentAdapter 明确提示「请另存为 .docx/.pptx 后导入」（失败显式化，不静默）

---

## 2026-08-03 — v0.2.0 发布

LiMo 0.2.0 正式发布（NSIS + MSI 安装包 + GitHub Release）。

自 0.1.6 以来的主要变化（详见下方各功能条目）：

- **插件系统正式化**：EnginePlugin v1 进程外（默认）/trusted 例外 + 管理页（启停/卸载/配置）+ 一键安装依赖 + 首个三方插件仓库发布（pymupdf4llm）
- **参考文件（Reference Text）**：参考资料注入笔记生成（暂存 id 传递，仅 notes）
- **打包体积修剪**：移除 Tesseract / docling / faster-whisper（~57% 体积，147.8M）
- **引擎降级链彻底移除**（失败显式化：绑定只取第一个，失败直接报错）
- **设计哲学**：口子原则（Keep core lean）+ 失败显式化
- **生成超时调整**（chunk 间隔 150s / 总超时 330s）+ 若干反馈/提示修复

---

## 2026-08-03 — 插件依赖一键安装（建 venv + pip 镜像源 + 失败显式化）

用户拖插件到 `~/.limo/plugins/` 后不再需要手动建 venv。前端插件页对「进程外 + 有 requirements.txt」的插件显示「一键安装」：

- **后端**：`install_plugin_deps()`（plugin_loader.py）——建 venv（base python：源码=当前后端 / **PyInstaller 打包=系统 python，Windows 优先 `py` launcher 防 Store 别名**）+ `pip install -i 清华镜像` + `CREATE_NO_WINDOW`；返回 ok / error（stderr 尾部）/ **no-python**（打包版无系统 Python → 降级引导，不静默失败）。enumerate 加 `hasRequirements`/`depsInstalled`
- **路由**：`POST /api/plugins/{name}/install-deps`（`asyncio.to_thread` 放线程池，模式 15）
- **前端**：插件卡片依赖状态 + 一键安装/重新安装按钮（失败后半截 venv 不再卡死——depsInstalled=true 时显示「重新安装」灰字）+ 失败原因/手动命令/引导提示

验证：真实安装（requests 镜像源装成功）+ 失败显式（不存在的包 → error + 原因）+ 打包降级三态（frozen 下命中 py launcher / 无 python → no-python）。

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

- 抽取 `TranscriptResult` / `TranscriptSegment` 到独立 [transcript.py](../backend/app/engines/transcript.py)（原定义在 asr.py 里，删除后 bcut/asr_cloud 的 import 不破）
- 删除 `FasterWhisperEngine` 注册 + [asr.py](../backend/app/engines/asr.py) 整文件
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
- 实现：[sample_doc.py](../backend/app/core/sample_doc.py) + [main.py](../backend/main.py) startup 挂载（置于 init_engines 之后）+ [LiMoBackend.spec](../backend/LiMoBackend.spec) datas

**2. 修复全新安装启动崩溃（严重）** — `DEFAULT_CONFIG` 的 `engine_bindings` 中 `doc`/`doc:epub` 为**空 list**，[engines/__init__.py](../backend/app/engines/__init__.py) 清理失效绑定循环 `emap.get([])` 抛 `TypeError: unhashable type: 'list'` → 全新安装（无 `~/.limo/config.yaml`）启动必崩；打包版能跑只因用户 config 绑定已非空。修复：空 list 视为无效绑定，自动回填该子场景默认引擎（doc→pypdfium2、doc:epub→ebooklib 等）。

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

- 前端 [GenerateOptions.tsx](../frontend/src/components/gen-panel/GenerateOptions.tsx)：`AI 分析图片` 曾依赖 `formatImages && !ocrEnabled`（OCR 勾上后 AI 分析选项消失，人为互斥）；`OCR`/`AI 分析` 都依赖 `formatImages`。改为三个平铺独立开关，任意组合
- 前端 [GenPanel.tsx](../frontend/src/components/gen-panel/GenPanel.tsx)：`image_analyze`/`image_optimize` 曾被 `formatImages` 门控（不勾保留图片则 AI 分析静默失效），改为独立传递
- 后端 [generator.py](../backend/app/generation/generator.py)：非流式端点两处 OCR 调用漏传 `doc_dir` → 新格式 `assets/img_NNNN.png` 定位到错误路径 `{store_root}/files/assets/...`，OCR 全部 `[图片缺失]`。流式端点原本正确，前端走流式所以未暴露
- 后端 [ocr_preprocessor.py](../backend/app/generation/ocr_preprocessor.py)：正则只匹配 `img_NNNN`，docling 引擎产出 `fig_NNNN` 被跳过。放宽为 `(?:img|fig)_\d{4}`（捕获组结构保持 3 元素解包不变）

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
