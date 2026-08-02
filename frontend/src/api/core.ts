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
  const r = await fetch(url, init)
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
  llmModels: Record<string, { id: string; name: string; desc: string }[]>
  version: string
  /** 文件类型→可用引擎名称白名单（后端动态推导） */
  fileTypeMap: Record<string, string[]>
  /** 子场景 key→中文标签 */
  scenarioLabels: Record<string, string>
  /** 引擎能力标签→中文 */
  engineLabels: Record<string, string>
  /** 风格 key→中文标签 */
  styleLabels: Record<string, string>
}

export interface DocEntry {
  id: string; title: string; sourceType: string; sourceFileName?: string; engine: string
  language?: string; duration?: number; contentHash: string
  tags: string[]; createdAt: string
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
}

export interface ChatResult {
  answer: string
}
