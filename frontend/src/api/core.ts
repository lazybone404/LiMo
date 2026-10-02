/**
 * API 共享核心 — URL 工具 + 错误体系 + 共享类型。
 * BASE 为模块内部共享（各域文件从这里 import），不通过 barrel 对外导出。
 */

// Tauri 环境检测：生产模式用完整 URL，开发/Vite 模式用相对路径走 proxy
const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

/** API 基础路径 — 模块内部共享（不对外导出） */
export const BASE = isTauri ? 'http://127.0.0.1:7413/api' : '/api'

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
    r = await fetch(url, init)
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
  llmModels: Record<string, { id: string; name: string; desc: string; capabilities?: string[]; context_window?: number }[]>
  version: string
  /** 文件类型→可用引擎名称白名单（后端动态推导） */
  fileTypeMap: Record<string, string[]>
  /** 子场景 key→中文标签 */
  scenarioLabels: Record<string, string>
  /** 引擎能力标签→中文 */
  engineLabels: Record<string, string>
  /** 风格 key→中文标签 */
  styleLabels: Record<string, string>
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

/** 单层文件夹（纯逻辑分类，不映射磁盘目录） */
export interface FolderEntry {
  id: string
  name: string
  createdAt: string
  /** 该文件夹下文档数（后端现算） */
  count: number
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
  /** 已手动编辑标记（GET /api/markdown 透传 index entry；生成版无此字段） */
  edited?: boolean
}

export interface ChatResult {
  answer: string
}
