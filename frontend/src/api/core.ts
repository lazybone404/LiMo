/**
 * API 共享核心 — URL 工具 + 错误体系 + 共享类型。
 * BASE 为模块内部共享（各域文件从这里 import），不通过 barrel 对外导出。
 */

/**
 * Tauri 环境检测 —— **全仓唯一判据**（`__TAURI_INTERNALS__` 还有几处私有副本，
 * 新增代码一律 import 这个）。
 *
 * 写成**函数**而不是模块常量：调用方里既有「模块加载期算一次」（下面的 `BASE`），
 * 也有「每次调用都重新读」（测试里 `delete window.__TAURI_INTERNALS__` 切换环境）。
 * 常量会让后者永远读到第一次的结果，而那是静默的 —— 环境没切成功，代码也不报错。
 */
export function isTauriEnv(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

// 生产模式用完整 URL，开发/Vite 模式用相对路径走 proxy
const isTauri = isTauriEnv()

/** API 基础路径 — 模块内部共享（不对外导出） */
export const BASE = isTauri ? 'http://127.0.0.1:7413/api' : '/api'

/**
 * 客户端标记头 —— **每个请求都要带**。
 *
 * 后端对「有副作用的 GET」（`/chat/stream`、`/generate/{id}/stream`）做了来源校验：
 * 跨站 `<img>` / `<script>` 是 no-cors 请求、无法携带自定义头，用它把「恶意网页
 * 让本地后端开始计费」这条路堵死（CORS 只挡读响应、不挡发出）。
 * 缺失时后端回落到 Origin/Referer 判定，但那条路可被 no-referrer 抹掉 —— 所以这里
 * 是主防线，**新增裸 `fetch` 时别漏掉**。
 */
export const CLIENT_HEADERS: Record<string, string> = { 'X-LiMo-Client': '1' }

/** 拼接 API 完整 URL（Tauri 补全域名，浏览器用相对路径走 Vite proxy） */
export function apiUrl(path: string): string {
  return path.startsWith('http') ? path : `${BASE}${path}`
}

/** 将应用内路径（/api/...、/static/...）转为可访问的完整 URL。
 *  Tauri 打包端无 Vite proxy，需补全 http://127.0.0.1:7413。
 *  外部 URL 原样返回。 */
export function appUrl(path: string): string {
  if (!path || path.startsWith('http')) return path
  if (!isTauri) return path
  if (path.startsWith('/api/')) return apiUrl(path.slice(4))
  return `http://127.0.0.1:7413${path}`
}

// ── 结构化 API 错误 ──

export interface ApiError {
  code: string           // 错误码: GEN_API_TIMEOUT / PROC_NOT_FOUND / ...
  message: string        // 用户可读的中文错误消息（「发生了什么」）
  detail?: string        // 技术细节（给开发者看，用户侧只作补充行）
  hint?: string          // 「那我现在该做什么」——后端可选给，没给就不渲染
  recoverable: boolean   // 是否可重试
  status: number         // HTTP 状态码
}

export class ApiErrorException extends Error {
  readonly apiError: ApiError
  constructor(apiError: ApiError) {
    super(apiError.message)
    this.name = 'ApiErrorException'
    this.apiError = apiError
  }
}

/**
 * 错误信封里 `message` 之外的**结构化补充字段** —— 后端 `AppError.to_dict()` 的可选部分。
 *
 * 流式端点拿不到 `apiFetch`（响应是 SSE，不是一个完整的 JSON 体），所以错误信封在
 * 三个地方被手工解包：`ingest.ts` 的 SSE `error` 事件 / 非 SSE 的 JSON 主路径与回退、
 * `generate.ts` 的 `!response.ok` 与「200 但 body 是 JSON」。**四处共用这一份判据** ——
 * 各写一遍的失效形态是「某一条路径上 hint 静默消失」，而那正是本轮要修的病。
 */
export interface ApiErrorInfo {
  code?: string
  detail?: string
  hint?: string
  recoverable?: boolean
}

/**
 * 从后端错误信封里取结构化补充字段。
 *
 * ⚠️ 逐字段 `typeof` 判据，不做 `data.hint || undefined`：信封来自网络，形状不可信；
 * 尤其 `detail` 在 FastAPI 校验失败时是**数组**，原样透传会让渲染处出现
 * `[object Object]`（同 `apiFetch` 的 ② 号判据）。
 */
export function errorInfoOf(data: unknown): ApiErrorInfo {
  const d = (data ?? {}) as Record<string, unknown>
  return {
    code: typeof d.code === 'string' ? d.code : undefined,
    detail: typeof d.detail === 'string' ? d.detail : undefined,
    hint: typeof d.hint === 'string' ? d.hint : undefined,
    recoverable: typeof d.recoverable === 'boolean' ? d.recoverable : undefined,
  }
}

/**
 * 从错误信封里取**用户可读文案** —— `message` 优先，`detail` 只作降级。
 *
 * ⚠️ 顺序与 `apiFetch` 的 ① 号判据同源：`detail` 是给开发者看的原始报错
 * （`ProxyError: … WinError 10061`），把它排在 `message` 前面就是「用户读到一屏英文，
 * 而我们以为已经解释清楚了」。流式端点此前正是这个顺序。
 *
 * ⚠️ `code` 排在**最后**、再之后才是调用方给的 `fallback`：只有「后端什么都没给」时
 * 才会走到它，那时 `GEN_NO_API_KEY` 这类标识仍比一句泛泛的「导入失败」更可能让人
 * 搜到原因。位置放在 detail 之后是刻意的 —— detail 是同一件事的原始报错，更有内容。
 */
export function errorMessageOf(data: unknown, fallback: string): string {
  const d = (data ?? {}) as Record<string, unknown>
  if (typeof d.message === 'string' && d.message) return d.message
  // 旧信封 `{error: "…"}` 没有 message —— 它是个**正常的人话文案**，排在 detail 前面
  if (typeof d.error === 'string' && d.error) return d.error
  if (typeof d.detail === 'string' && d.detail) return d.detail
  if (typeof d.code === 'string' && d.code) return d.code
  return fallback
}

/** 从任意错误中提取用户可读消息（用于 Toast 显示） */
export function getErrorMessage(err: unknown): string {
  if (err instanceof ApiErrorException) {
    return err.apiError.message
  }
  if (err instanceof Error) {
    return err.message.length > 150 ? err.message.slice(0, 147) + '...' : err.message
  }
  return String(err).slice(0, 150)
}

// 统一 HTTP 状态检查 + 结构化错误解析
export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  let r: Response
  try {
    // 统一带上客户端标记头（后端对计费端点做来源校验，见 CLIENT_HEADERS）
    r = await fetch(url, { ...init, headers: { ...CLIENT_HEADERS, ...(init?.headers as Record<string, string> | undefined) } })
  } catch (err) {
    // P1-16：瞬时网络抖动/后端未启动 → fetch 抛 TypeError（Failed to fetch）。
    // 归一为可恢复错误：用户看到中文提示 + 可重试，而非全屏「内容加载失败」红屏。
    throw new ApiErrorException({
      code: 'NETWORK_ERROR',
      message: '网络连接失败，请检查后端是否运行',
      detail: err instanceof Error ? err.message : String(err),
      recoverable: true,
      status: 0,
    })
  }
  if (!r.ok) {
    let body: Partial<ApiError> & { detail?: unknown } = {}
    try { body = await r.json() } catch { /* body 非 JSON */ }
    // ⚠️ 三项判据，缺一项都会出洋相：
    //  ① **message 优先于 detail**。改造前是 `detail || message` —— 而 `detail` 是
    //    给开发者看的原始报错（`ProxyError: … WinError 10061`），把它当用户文案，
    //     就是「用户读到一屏英文，而我们以为已经解释清楚了」。
    //  ② **detail 必须是字符串**。FastAPI 的校验失败返回 `{detail: [{loc, msg, type}]}`
    //     是**数组**，直接塞进 `message: string` 会在渲染处变成 `[object Object]`。
    //  ③ 全都没有时仍保留旧的 `JSON.stringify` 兜底（未知错误形状至少能看见内容）。
    const detailStr = typeof body.detail === 'string' ? body.detail : undefined
    const apiError: ApiError = {
      code: body.code || `HTTP_${r.status}`,
      message: (typeof body.message === 'string' && body.message)
        || detailStr
        || (Object.keys(body).length > 0 ? JSON.stringify(body) : `请求失败 (${r.status})`),
      detail: detailStr,
      hint: typeof body.hint === 'string' ? body.hint : undefined,
      recoverable: body.recoverable ?? (r.status < 500),
      status: r.status,
    }
    throw new ApiErrorException(apiError)
  }
  return r
}

// ── 共享类型 ──

/** 插件 config_schema 字段（简版配置声明，前端渲染表单） */
export interface ConfigField {
  name: string
  type: 'string' | 'int' | 'number' | 'bool' | 'select' | 'textarea'
  label?: string
  default?: unknown
  hint?: string
  options?: string[]
}

export interface EngineOption {
  id: number; provider: string; name: string; desc: string
  capabilities?: string[]  // 从 EngineRegistry 注入的能力标签
  license?: string         // SPDX 许可证标识（插件引擎，仅展示，无警告语义）
  plugin?: boolean         // 是否为用户安装的插件引擎
  displayName?: string     // 插件展示名（缺省回退 name）
  userDesc?: string        // 插件一句话能力描述
  configSchema?: ConfigField[]  // 插件配置声明
}
/** Capabilities 响应中的引擎厂商简要信息 */
export interface EngineProviderInfo {
  name: string; type: string; keyConfigured?: boolean; capabilities: string[]
}

/** 一套对比维度预设（合成对话框的预设下拉项） */
export interface DimensionPreset {
  key: string
  /** 中文名，如「论文综述」 */
  label: string
  /** 该预设的维度列表（2026-10-05 起**两个落点**：「## 读者问题」的候选来源 +
   *  文末「## 各篇速查」的小节标题；不再是旧版「## 对比总表」的表格行标签） */
  dimensions: string[]
}

/**
 * 材料体裁选项（合成对话框「高级」区的多选框；后端 `synthesis.CONTENT_TYPES` 下发）。
 *
 * ⚠️ `label` 与 `DimensionPreset.label` **刻意不同**——这里描述的是**材料本身**
 * （「论文」），那边描述的是**产物形态**（「论文综述」）。两组键相同、文案不同。
 */
export interface ContentTypeOption {
  key: string
  label: string
}

/** 读者意图选项（合成对话框的主控件；后端 `synthesis.READER_INTENTS` 下发） */
export interface ReaderIntentOption {
  key: string
  label: string
  /** 该意图的完整描述（对话框里作小字说明） */
  purpose: string
  /**
   * 建议的材料体裁 key。**`null` = 不预设**——「理解主题」「比较材料」说的是
   * 读者的动作，不指示材料是什么体裁；前端据此留空高级区，而不是替用户猜一个
   * （静默预填「论文」对一叠视频转写稿毫无意义，而用户看不出为什么）。
   */
  defaultContentType: string | null
  /**
   * 该意图派生的**产物形态** key（`article` / `claims` / `comparison` /
   * `controversy` / `decision`）。
   *
   * ⚠️ **只读展示用**：形态由意图在**生成侧**重新解析（meta 里的意图 key 是唯一真相源），
   * 前端拿到它只为了在意图组下方显示一行提示，**不据此传任何请求参数** —— 多传一份
   * 就是第二真相源（模式 #54）。
   */
  shape: string
  /** 形态的中文名（「比较简报」），与 `shapeDescription` 一起作提示 */
  shapeLabel: string
  /** 形态的一句话说明（「按比较轴逐个对照…」） */
  shapeDescription: string
}

/**
 * 生成本地向量后，模型本体在内存里留多久（`search.vector_release`）。
 *
 * - `never`：一直留着 —— 每次搜索都是热的，代价是那几十 MB 常驻。
 * - `idle`（默认）：闲置一段时间后释放。
 * - `immediate`：建完索引立刻释放 —— 内存回到字面检索的水平，代价是下一次搜索
 *   要等模型重新载入。
 *
 * ⚠️ **只对本地引擎有实际差别**：云端引擎不占本机内存，三档下行为完全相同。
 * ⚠️ 这个联合类型与后端 `RELEASE_POLICIES` 是**编译期 / 运行期**两层，天然无法合并；
 * 而**展示列表**（label / hint / 顺序）一律走 capabilities 下发（见 `ReleasePolicyOption`）。
 */
export type ReleasePolicy = 'never' | 'idle' | 'immediate'

/**
 * 释放策略的一档（后端 `capabilities.py::_RELEASE_POLICY_META` 下发）。
 *
 * ⚠️ **前端不硬编码这三档的文案**：后端 `VectorWorker.maybe_release` 按 key 分派，
 * 加一档要改后端 —— 前端再存一份文案，加档时设置页就永远看不到它（用户选不到
 * 就等于没加），而同一条纪律下 `styleLabels` / `dimensionPresets` 早已是下发的。
 */
export interface ReleasePolicyOption {
  key: ReleasePolicy
  label: string
  hint: string
}

/**
 * 索引触发方式（2026-10-27）。
 *
 * - `auto`（默认）：待索引文档**攒够一批**才建一轮 —— 本地模型只为一批加载一次，
 *   不会为一两篇反复「载入 → 编码 → 释放」。
 * - `manual`：永不自动建，只有用户点「立即建立索引」才跑。
 *
 * ⚠️ 与 `ReleasePolicy` 同为「后端拥有的枚举 + 前端只存联合类型、文案走 capabilities」。
 * 攒批阈值（`threshold`）是 `auto` 档的**量**，不是第三档 —— 把它做成档位会让
 * 「不改阈值」与「关了攒批」在界面上长得一样。
 */
export type TriggerPolicy = 'auto' | 'manual'

/** 触发方式的一档（后端 `capabilities.py::_TRIGGER_META` 下发）。前端不存文案，理由同
 *  `ReleasePolicyOption`：加一档要改后端，前端再存一份则设置页永远看不到它。 */
export interface TriggerPolicyOption {
  key: TriggerPolicy
  label: string
  hint: string
}

export interface Capabilities {
  adapters: string[]
  engines: Record<string, Record<string, { supported_modes: string[] }>>
  engineProviders: Record<string, EngineProviderInfo>
  engineOptions: Record<string, EngineOption[]>
  engineBindings: Record<string, number | null>
  styles: string[]
  docStyles: string[]
  excelStyles: string[]
  genOptions: Record<string, string[]>
  products: { name: string; label: string; category: string; description: string }[]
  /** LLM 预设表（config.py llm_models）— context_window 结构化窗口，供对话预算展示 */
  llmModels: Record<string, {
    id: string; name: string; desc: string; capabilities?: string[]; context_window?: number
    /** 下线日期（如 "2026-10-02"）。非空 = 官方已下架，UI 须提示（不许当正常模型推荐） */
    retired?: string
    /** 下线说明（已含「建议改用 X」）。与 retired 成对出现，由后端 `_SYNC_OPTIONAL_FLAGS` 保证 */
    retired_note?: string
  }[]>
  version: string
  /** 文件类型→可用引擎名称白名单（后端动态推导） */
  fileTypeMap: Record<string, string[]>
  /** 子场景 key→中文标签 */
  scenarioLabels: Record<string, string>
  /** 引擎能力标签→中文 */
  engineLabels: Record<string, string>
  /** 风格 key→中文标签 */
  styleLabels: Record<string, string>
  /**
   * 多篇合成的对比维度预设（后端 `synthesis.DIMENSION_PRESETS` 下发）。
   *
   * ⚠️ **前端不硬编码预设名**——新增一套预设只改后端一处即可（同「引擎前端
   * 后端驱动」原则）。可选是因为它有版本差：老后端不返回该字段时不该让
   * 整个 capabilities 解析失败（消费处一律 `?? []`）。
   */
  dimensionPresets?: DimensionPreset[]
  /**
   * 材料体裁选项（「高级」区多选框）。与 `dimensionPresets` 同款的可选字段——
   * 老后端不返回时消费处一律 `?? []`，不让整个 capabilities 解析失败。
   */
  contentTypes?: ContentTypeOption[]
  /** 读者意图选项（对话框主控件）。可选理由同上。 */
  readerIntents?: ReaderIntentOption[]
  /** 本地 embedding 模型的内存释放策略三档（设置页「模型内存」）。可选理由同上。 */
  releasePolicies?: ReleasePolicyOption[]
  /** 索引触发方式两档（设置页「什么时候建索引」）。可选理由同上。 */
  triggerPolicies?: TriggerPolicyOption[]
  /** 缺 key 项（P2.1 主动提示）：llm 未配 key 的厂商 + 未配 key 的引擎（dashscope） */
  missingKeys: {
    llm: { id: string; name: string }[]
    engine: string[]
  }
}

/**
 * 列表返回的文档条目 —— **全仓唯一真相源**。
 *
 * 此前 `store/types.ts` 另有一份同名定义，两份已经分叉（store 侧多 `sourceUrl` /
 * `coverUrl` / `modes`），而 TS 对「两个结构兼容的同名 interface」不报错，
 * 表现是「类型上没有这个字段，运行时确实在」。2026-11-07 合并到此处：
 * `store/types.ts` 只做 re-export，**加字段只改这一处**。
 *
 * 字段来源：`sourceUrl` / `coverUrl` 在 `doc_meta.DOC_FIELDS`（index 直通投影带出）；
 * `modes` 由 `QueryService.list_resources` 从产物索引派生后附加（不是 index 字段）。
 */
export interface DocEntry {
  id: string; title: string; sourceType: string; sourceFileName?: string
  /** 来源 URL（网页 / 视频 / arXiv）；非 URL 导入恒为 "" */
  sourceUrl?: string
  engine: string
  language?: string; duration?: number
  /** 封面图 URL（视频平台），`project_resource` 从 `metadata.cover_url` 取 */
  coverUrl?: string
  contentHash: string
  tags: string[]; createdAt: string
  /** 所属文件夹 id；"" = 未分类（后端一律返回字符串，不返回 undefined） */
  folderId?: string
  /** 合成文档的来源 id / 标题 / 各源内容 hash 前 8 位（非合成恒为 []） */
  sourceDocIds?: string[]; sourceTitles?: string[]; sourceHashes?: string[]
  /** 出处头丢失或损坏 → 保留文档但不猜来源（设计 §4.2.3） */
  metadataCorrupt?: boolean
  /**
   * 正文状态（「导入与转录分开」，2026-11-07）。
   *
   *   - `'pending'` = 只收纳了，`rawText` 是占位文本，正文还没提取；
   *   - `'done'` / `undefined` = 正文是真的（**缺键即 done** —— 历史条目零迁移）。
   *
   * ⚠️ **与后端 store 内部的 `ready` 无关**（那个 `False` 的文档在列表里根本不存在）。
   * 这是用户可见的持久态，由导入与转录两条路径显式写。
   *
   * ⚠️ 三处消费方都必须处理它：列表徽章（`DocRow`）、预览区卡片（`PreviewArea` 的
   * transcript 分支）、生成前置拦截（`GenPanel`）—— 占位文本非空且不含失败前缀，
   * 不拦的话会拿它生成一篇笔记，而产物看上去完全正常。
   */
  transcribeState?: 'pending' | 'done'
  /**
   * 这篇**已有哪些生成产物**（`"notes"` / `"polished"`），由 `QueryService.list_resources`
   * 从产物索引带出（零额外读盘）。
   *
   * ⚠️ **`undefined` 与 `[]` 语义不同，消费方必须分流**：
   *   - `undefined` = **未知**（旧后端 / 测试 mock / 乐观插入的新文档）→ 照旧发请求
   *   - `[]`        = **确定没有产物** → 一个产物请求都不发
   * 这把「尚未生成」还原成正常状态，而不是靠一个必然 404 的请求把它静默掉。
   *
   * ⚠️ 由生成/解读链路在产物落盘后**追加**（store 的 `markArtifact`）——不追加的话
   * 「生成完 → 切走 → 切回」会因 `[]` 而不请求，笔记看起来凭空消失（模式 #52）。
   */
  modes?: string[]
}

/**
 * 文件夹（可嵌套的纯逻辑分类，不映射磁盘目录）
 *
 * ⚠️ 与 `store/types.ts` 的 `FolderEntry` 是**两份**（项目既有的重复），加字段必须
 * 两处一起加——只加一处的表现是「运行时字段存在而类型不认」，`tsc` 静默放行。
 */
export interface FolderEntry {
  id: string
  name: string
  createdAt: string
  /** 父文件夹 id；`''` = 根级（后端已按根级归化过悬空 / 成环的脏值）。
   *  可选是兼容旧后端 / 测试 mock，消费方一律写 `f.parentId || ''`。 */
  parentId?: string
  /** **直接**挂在该夹下的文档数（不含子树） */
  count: number
  /** 含全部后代的文档数（缺失时前端 `countByFolder` 本地派生） */
  totalCount?: number
}

export interface TranscriptFull extends DocEntry {
  rawText: string
}

export interface GenerateResult {
  id: string; transcriptId: string; content: string
  polished_content?: string | null; polished_id?: string
  style: string; mode: string; version: number
}

export interface MarkdownView {
  id: string; transcriptId: string; content: string
  style: string; mode: string; version: number; createdAt: string
  /** 已手动编辑标记——**从产物文件头的 `edit` 键派生**（不是 index 字段），
   *  故删掉 index.json 重建后仍在（2026-10-10 版本归档）。生成版无此字段。 */
  edited?: boolean
  /** 纯正文字数（不含自证头）。同样读时从文件头派生，`undefined` = 后端没给 */
  chars?: number
  /** 生成用的模型名（版本列表的「来源」列） */
  source?: string
  /** 追问建议（勾了「追问卡片」时由后端独立补调一次 LLM 产出）。
   *  ⚠️ **住在第三个存储位置**：前三项从产物文件头的 `<!-- limo-meta -->` 现算，这一个
   *  从旁路的 `{doc_dir}/{mode}/v{N}.followups.json` 读（不进正文/不进 index/不进自证头）。
   *  ⚠️ `undefined` = 这一版没有追问（没勾 / 生成失败 / 历史版本）——**与空数组合义不同**。 */
  followUps?: string[]
}

export interface ChatResult {
  answer: string
}
