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
}

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
 *  ⚠️ 打开本身不花钱，它只是允许 worker 开始编码；首次全库编码的成本在卡片上告知。 */
export async function setVectorSearch(enabled: boolean): Promise<VectorActionResult> {
  return (await apiFetch(`${BASE}/search/vector`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })).json() as Promise<VectorActionResult>
}

/** **重建**向量索引 —— 清空旧口径与全部块、重新排队（要花一遍全库编码的钱）。
 *  后端要求二次确认由前端承担（这里只负责发请求）。 */
export async function rebuildVectorIndex(): Promise<VectorActionResult> {
  return (await apiFetch(`${BASE}/search/vector/rebuild`, { method: 'POST' }))
    .json() as Promise<VectorActionResult>
}
