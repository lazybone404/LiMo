/**
 * 全文搜索域 — FTS5 检索（GET /api/search）+ 索引健康与重建 + **语义检索开关**。
 *
 * 索引文本 = **原文 + 最新版笔记**（后端 `generation/doc_text.py::search_index_text`
 * 是唯一判据）。这条曾长期写成「只搜原文」，而 2026-10-26 重做后产物也在索引里 ——
 * 注释与实现对不上时，读代码的人会按注释去改 `search_index_text`。
 *
 * ⚠️ 检索有**两条独立的库**：字面的 `search.db`（免费、毫秒级重建）与向量的
 * `vec.db`（**编码要花钱**、全库重建以分钟计）。所以状态、开关、重建三件事都各有一套，
 * 不要合并成「重建索引」一个按钮。
 */
import { apiFetch, BASE } from './core'
import type { ReleasePolicy, TriggerPolicy } from './core'

export interface SearchHit {
  id: string
  title: string
  /** 含后端注入的 `<mark>` 高亮标记。**必须经 SearchSnippet 渲染**——
   *  片段是用户导入的原文，直接 dangerouslySetInnerHTML 等于把导入内容当 HTML 执行。
   *  ⚠️ **语义路独有的命中这里是空串**（没有可截取的高亮位置，硬造摘要等于
   *  凭空发明「为什么匹配」）；消费方只对非空值渲染第二行。 */
  snippet: string
}

/**
 * 语义（向量）路**本次**的结果。
 *
 * ⚠️ `error` 与 `hits` 缺一不可：
 * - `semantic === null`（整个对象） = 用户没开语义检索；
 * - `{error: null, hits: 0}` = 开了、跑通了，但索引里还没有可比向量（后台正在建）；
 * - `{error: '…', hits: 0}` = 本次只用了字面结果，原因在文案里。
 *
 * 少了 `hits`，前两种在界面上逐字相同；少了 `error`，「向量没帮上忙」与
 * 「向量压根没跑」也逐字相同 —— 而这两件事用户该做的事完全不同。
 */
export interface SemanticStatus {
  /** 非 null = 本次只用了字面结果 */
  error: string | null
  /** 语义路召回篇数（融合前）。注意它不是「本页命中数」 */
  hits: number
}

export interface SearchResponse {
  hits: SearchHit[]
  /** `null` = 语义检索未启用 */
  semantic: SemanticStatus | null
}

/** 向量索引的四元组身份 —— 模型 / 维度 / 切块规则 / schema 版本。任一不符即整体不可用。 */
export interface VectorIdentity {
  modelId: string
  dimension: number
  chunkSize: number
  schemaVersion: number
  builtAt: string
}

/** 一个可选的向量化引擎（`GET /api/search/status` 的 `vector.models`）。 */
export interface VectorModelOption {
  /** 引擎名 —— 切引擎时把它原样发回 `POST /api/search/vector` 的 `model` 字段 */
  name: string
  /** 展示名（含「免费 / 按量计费」这类成本线索） */
  label: string
  modelId: string
  dimension: number
  /** 本地引擎：不联网、不计费、零外发。**成本文案按它分支，不按名字前缀猜** */
  local: boolean
  /** 引擎自检通过与否（来自引擎自己的 `precheck()`） */
  ready: boolean
  /** `ready` 为 false 时的原因 —— 通常是一句「装什么 / 缺哪个文件」 */
  reason: string | null
}

/** 向量侧的只读状态（`GET /api/search/status` 的 `vector` 段）。 */
export interface VectorStatus {
  /** **运行时**是否真的启用了（含「配置开着但引擎/索引不可用」这一档），
   *  不是配置项的值 —— 两者都读会得到「设置页说开着、状态说不开」。 */
  enabled: boolean
  /** 未启用的原因（`enabled` 为真时是 null） */
  reason: string | null
  available: boolean
  lastError: string | null
  identity: VectorIdentity | null
  /** `pending` = 已排队待编码；`failed` = 连续失败达上限（worker 不再自动重试） */
  docs: { total: number; ok: number; pending: number; failed: number }
  chunks: number
  sizeBytes: number
  /** 当前**选中**的引擎名。⚠️ 读配置而不是读 runtime —— 还没启用时下拉也必须
   *  显示用户选中的那项，否则「开着但下拉是空的」。 */
  model: string
  /** 可选引擎清单（设置页下拉的数据源，顺序 = 后端注册顺序）。 */
  models: VectorModelOption[]
  /** 本地模型的内存释放策略（**配置值**，不是运行时状态 —— 开关关着时也要显示
   *  用户选中的那一档）。三档的 label / hint 由 capabilities 下发
   *  （`Capabilities.releasePolicies`，设置页渲染那一组按钮的唯一数据源）。 */
  release: ReleasePolicy
  /** 索引触发方式（**配置值**，理由同 `release`）。两档文案由
   *  `Capabilities.triggerPolicies` 下发。 */
  trigger: TriggerPolicy
  /** 攒批阈值（**配置值**，`auto` 档下「攒够几篇才建一轮」）。
   *  ⚠️ 后端对非正数**静默夹紧到 1**（错的是量不是性质）—— 这里拿到的永远是 ≥ 1。 */
  threshold: number
}

/** 释放策略 / 触发方式的类型住在 `core`（与它们一起下发的选项类型和
 *  `Capabilities` 同处），这里转出以免消费方多记一个 import 路径。 */
export type { ReleasePolicy, TriggerPolicy }

/** 检索索引的只读状态（`GET /api/search/status`）。 */
export interface SearchStatus {
  /** 字面索引当前是否可用于检索 */
  available: boolean
  /** 索引里的文档篇数 */
  docCount: number
  /** `search.db` 文件体积 */
  sizeBytes: number
  /** 不可用的原因（`available` 为真时是 null） */
  lastError: string | null
  /** 「这一次读状态」本身失败的原因（读到了就是 null）。
   *  ⚠️ 与 `lastError` **不是一回事**：`lastError` 说「索引坏了」，这个说
   *  「状态没读出来」——后者不影响可用性，所以界面上**刻意不展示**（用户对它
   *  没有可采取的行动），只给排查时看。 */
  statusError?: string | null
  /** 上次成功重建的时间（本地时间 ISO 串，本次进程内） */
  rebuiltAt: string | null
  /** 向量侧状态。老后端没有这一段时为 `undefined`，UI 按「未启用」处理。 */
  vector?: VectorStatus
}

/** 开关 / 重建两个动作的共同返回（都带上动作之后的完整状态，省一次往返）。 */
export interface VectorActionResult {
  enabled: boolean
  reason: string | null
  status: VectorStatus
}

/** 搜索正文+笔记。q 为空返回空结果（不发请求）。
 *
 *  ⚠️ 索引不可用时后端返回 **503 + 错误对象**，而 `apiFetch` 对非 2xx **一定抛**
 *  `ApiErrorException`（所以这里没有 `if (!r.ok)`）—— 判据在调用方：
 *  `doc-library/useDocQuery.ts::isIndexUnavailable` 读 `apiError.status` / `code`。
 *  曾经那里用 `message.includes('503')`，而 message 来自后端文案、**从来不含 503**
 *  ⇒ 分支恒假、顶部横幅永远浮不出来（注释与实现对不上的经典形态）。
 *
 *  ⚠️ 响应形状是 `{hits, semantic}` 而不是裸数组（语义融合上线后改的）。
 *  拿到不认识的形状时**显式抛错**而不是 `?? []`：静默返回空数组会让「前后端版本
 *  对不上」表现为「搜索一条结果都没有」，而用户会去查自己是不是记错了关键词。 */
export async function searchDocs(q: string, limit = 10, signal?: AbortSignal): Promise<SearchResponse> {
  if (!q.trim()) return { hits: [], semantic: null }
  const r = await apiFetch(`${BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`, { signal })
  const data = await r.json()
  if (!data || !Array.isArray(data.hits)) throw new Error('搜索响应格式不符（缺 hits）')
  return { hits: data.hits, semantic: data.semantic ?? null }
}

/** 读取检索索引的健康状态（字面 + 向量两段）。 */
export async function getSearchStatus(): Promise<SearchStatus> {
  return (await apiFetch(`${BASE}/search/status`)).json() as Promise<SearchStatus>
}

/** 全量重建**字面**索引（后端带 VACUUM 回收空间）。返回重建后的状态。
 *  免费、毫秒级 —— 与 `rebuildVectorIndex` 是两件事。 */
export async function reindexSearch(): Promise<SearchStatus> {
  return (await apiFetch(`${BASE}/search/reindex`, { method: 'POST' })).json() as Promise<SearchStatus>
}

/** 开 / 关语义检索。后端会**就地**启停后台索引 worker 与查询融合层。
 *  ⚠️ 打开本身不花钱，它只是允许 worker 开始编码；首次全库编码的成本在卡片上告知。
 *
 *  `model` 传了就是**同时切引擎**（不传 = 保持当前配置）。⚠️ 切换会让口径不符
 *  （云端 1024 维 ↔ 本地 512 维），后端据此停用并给出「需要重建」的 reason ——
 *  这是预期语义，不是错误。
 *
 *  `release` 传了就是**同时改释放策略**（不传 = 保持当前配置）。后端非法值 400 ——
 *  与 `model` 同一条纪律（错的是性质，静默退化会让用户以为改成功了）。
 *
 *  `trigger` / `threshold` 同理（不传 = 保持当前配置）。⚠️ 两者非法值**语义不同**：
 *  非法 `trigger` → 400 `VECTOR_BAD_TRIGGER`（错的是性质；静默退回 `auto` 会让
 *  「选了手动档」变成一句谎话，而 worker 继续自动花钱）；`threshold` 非正数则
 *  **静默夹紧到 1**（错的是量，0 只是让闸门恒真 —— 用户要的就是「多建点」）。 */
export async function setVectorSearch(
  enabled: boolean, model?: string, release?: ReleasePolicy,
  trigger?: TriggerPolicy, threshold?: number,
): Promise<VectorActionResult> {
  const body: {
    enabled: boolean; model?: string; release?: ReleasePolicy
    trigger?: TriggerPolicy; threshold?: number
  } = { enabled }
  if (model) body.model = model
  if (release) body.release = release
  if (trigger) body.trigger = trigger
  // ⚠️ 用 `!== undefined` 而不是真值判断：`0` 是合法入参（后端会夹紧成 1），
  // 而 `if (threshold)` 会把它静默丢掉 —— 用户以为改了、请求里根本没这个字段。
  if (threshold !== undefined) body.threshold = threshold
  return (await apiFetch(`${BASE}/search/vector`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })).json() as Promise<VectorActionResult>
}

/** **立即建立索引** —— 叫后台 worker 跳过攒批闸门跑一轮**增量**。
 *
 *  - 与 `rebuildVectorIndex` 的区别：那个清空口径与全部块、重花一遍全库的钱；
 *    这个只补还没建的部分（已建过的块 hash 全中，一次编码调用都不发）。
 *  - 与「等自动触发」的区别：`auto` 档要攒够阈值才动手；`manual` 档则**只有**
 *    这条路。
 *
 *  ⚠️ 返回的 `requested` 只表示「话递到了 worker」，**不代表编码已完成** ——
 *  之后要么轮询 `getSearchStatus()` 看 `docs.pending` 下降，要么就等着。
 *  worker 没在跑（语义检索未启用 / 索引库不可用）时后端 400 `VECTOR_DISABLED`。 */
export async function syncVectorIndex(): Promise<VectorActionResult & { requested: boolean }> {
  return (await apiFetch(`${BASE}/search/vector/sync`, { method: 'POST' }))
    .json() as Promise<VectorActionResult & { requested: boolean }>
}

/** **重建**向量索引 —— 清空旧口径与全部块、重新排队（要花一遍全库编码的钱）。
 *  后端要求二次确认由前端承担（这里只负责发请求）。 */
export async function rebuildVectorIndex(): Promise<VectorActionResult> {
  return (await apiFetch(`${BASE}/search/vector/rebuild`, { method: 'POST' }))
    .json() as Promise<VectorActionResult>
}
