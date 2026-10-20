/**
 * API 共享核心 — URL 工具 + 错误体系 + 共享类型。
 * BASE 为模块内部共享（各域文件从这里 import），不通过 barrel 对外导出。
 */

// Tauri 环境检测：生产模式用完整 URL，开发/Vite 模式用相对路径走 proxy
const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

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
  message: string        // 用户可读的中文错误消息
  detail?: string        // 技术细节
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
    let body: Partial<ApiError> = {}
    try { body = await r.json() } catch { /* body 非 JSON */ }
    const apiError: ApiError = {
      code: body.code || `HTTP_${r.status}`,
      message: body.detail || body.message || (Object.keys(body).length > 0
        ? JSON.stringify(body)
        : `请求失败 (${r.status})`),
      detail: body.detail,
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
  /** 缺 key 项（P2.1 主动提示）：llm 未配 key 的厂商 + 未配 key 的引擎（dashscope） */
  missingKeys: {
    llm: { id: string; name: string }[]
    engine: string[]
  }
}

/**
 * 列表返回的文档条目。
 *
 * ⚠️ **store 侧另有一份同名定义**（`store/types.ts` 的 `DocEntry`），两者描述同一份
 * 后端 JSON、由 `listTranscripts()` 直接喂给 `setDocs()`（结构兼容所以没有编译错误）。
 * **改动必须两处同步**——否则会出现「类型上没这个字段，但运行时字段确实在」（或反过来
 * 「类型说有、实际后端没返回」），而 TS 一声不吭。已存在字段集漂移（store 侧多
 * `sourceUrl`），此处补 `source*` 时一并对齐。
 */
export interface DocEntry {
  id: string; title: string; sourceType: string; sourceFileName?: string; engine: string
  language?: string; duration?: number; contentHash: string
  tags: string[]; createdAt: string
  /** 所属文件夹 id；"" = 未分类（后端一律返回字符串，不返回 undefined） */
  folderId?: string
  /** 合成文档的来源 id / 标题 / 各源内容 hash 前 8 位（非合成恒为 []） */
  sourceDocIds?: string[]; sourceTitles?: string[]; sourceHashes?: string[]
  /** 出处头丢失或损坏 → 保留文档但不猜来源（设计 §4.2.3） */
  metadataCorrupt?: boolean
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
