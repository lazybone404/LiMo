// Tauri 环境检测：生产模式用完整 URL，开发/Vite 模式用相对路径走 proxy
const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
const BASE = isTauri ? 'http://127.0.0.1:7413/api' : '/api'

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

export interface EngineOption {
  id: number; provider: string; name: string; desc: string
  capabilities?: string[]  // 从 EngineRegistry 注入的能力标签
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

// -- capabilities --

export async function fetchCapabilities(): Promise<Capabilities> {
  const r = await apiFetch(`${BASE}/capabilities`)
  return r.json()
}

// -- transcripts --

export async function listTranscripts(params?: {
  q?: string; sourceType?: string; tag?: string
}): Promise<DocEntry[]> {
  const searchParams = new URLSearchParams()
  if (params?.q) searchParams.set('q', params.q)
  if (params?.sourceType) searchParams.set('sourceType', params.sourceType)
  if (params?.tag) searchParams.set('tag', params.tag)
  const qs = searchParams.toString()
  const r = await apiFetch(`${BASE}/transcripts${qs ? '?' + qs : ''}`)
  const data = await r.json()
  return Array.isArray(data) ? data : []
}

export async function getTranscript(id: string): Promise<TranscriptFull | null> {
  const r = await apiFetch(`${BASE}/transcripts/${id}`)
  const data = await r.json()
  if (data.error || data.code) return null
  return data as TranscriptFull
}

// -- transcript CRUD --

export async function updateTranscript(
  id: string,
  data: { title?: string; tags?: string[] },
): Promise<{ status: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function deleteTranscript(id: string): Promise<{ status: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${id}`, { method: 'DELETE' })
  return r.json()
}

// -- ingest --

export function ingestURL(
  url: string,
  onProgress: (ev: { phase: string; status: string; percent?: number; message?: string }) => void,
  onDone: (doc: any) => void,
  onError?: (err: string) => void,
  signal?: AbortSignal,
) {
  const body = new URLSearchParams({ url })
  return fetchSSE(`${BASE}/ingest`, body, onProgress, onDone, onError, signal)
}

export function ingestText(
  text: string,
  onProgress: (ev: { phase: string; status: string; percent?: number; message?: string }) => void,
  onDone: (doc: any) => void,
  onError?: (err: string) => void,
  signal?: AbortSignal,
) {
  const body = new URLSearchParams({ text })
  return fetchSSE(`${BASE}/ingest`, body, onProgress, onDone, onError, signal)
}

export function ingestFile(
  file: File,
  onProgress: (ev: { phase: string; status: string; percent?: number; message?: string }) => void,
  onDone: (doc: any) => void,
  onError?: (err: string) => void,
  signal?: AbortSignal,
) {
  const body = new FormData()
  body.append('file', file)
  return fetchFileSSE(`${BASE}/ingest`, body, onProgress, onDone, onError, signal)
}

function fetchSSE(
  url: string,
  body: URLSearchParams,
  onProgress: (ev: { phase: string; status: string; percent?: number; message?: string }) => void,
  onDone: (doc: any) => void,
  onError?: (err: string) => void,
  signal?: AbortSignal,
) {
  // 优先尝试 SSE 流式端点
  const streamUrl = url + '/stream'
  fetch(streamUrl, {
    method: 'POST',
    body,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    signal,
  })
    .then(async response => {
      // 如果是 SSE 流
      const contentType = response.headers.get('content-type') || ''
      if (contentType.includes('text/event-stream')) {
        const reader = response.body?.getReader()
        if (!reader) throw new Error('No reader')
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                if (data.type === 'progress') {
                  onProgress(data)
                } else if (data.type === 'done') {
                  onDone(data)
                } else if (data.type === 'error') {
                  onProgress({ phase: 'error', status: 'error', message: data.message || '未知错误' })
                  onError?.(data.message || '未知错误')
                }
              } catch { /* skip malformed */ }
            }
          }
        }
        return
      }
      // 回退：普通 JSON 响应
      const data = await response.json()
      if (data.error) {
        onError?.(data.error)
        return
      }
      onDone(data)
    })
    .catch(err => {
      console.error('Ingest error (will retry with non-stream):', err)
      // 回退：非流式端点
      fetch(url, {
        method: 'POST',
        body,
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      })
        .then(r => r.json())
        .then(data => {
          if (data.error) {
            onError?.(data.error)
            return
          }
          if (data.status === 'duplicate_url' || data.status === 'duplicate_content') {
            onDone({ id: data.id, title: data.title, status: data.status, message: data.message })
            return
          }
          onDone(data)
        })
        .catch(err2 => {
          console.error('Ingest fallback error:', err2)
          onError?.(`网络错误: ${err2.message}`)
        })
    })
}

function fetchFileSSE(
  url: string,
  body: FormData,
  onProgress: (ev: { phase: string; status: string; percent?: number; message?: string }) => void,
  onDone: (doc: any) => void,
  onError?: (err: string) => void,
  signal?: AbortSignal,
) {
  // 优先尝试 SSE 流式端点
  const streamUrl = url + '/stream'
  fetch(streamUrl, {
    method: 'POST',
    body,  // FormData — 浏览器自动设置 Content-Type: multipart/form-data
    signal,
  })
    .then(async response => {
      const contentType = response.headers.get('content-type') || ''
      if (contentType.includes('text/event-stream')) {
        const reader = response.body?.getReader()
        if (!reader) throw new Error('No reader')
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                if (data.type === 'progress') {
                  onProgress(data)
                } else if (data.type === 'done') {
                  onDone(data)
                } else if (data.type === 'error') {
                  onProgress({ phase: 'error', status: 'error', message: data.message || '未知错误' })
                  onError?.(data.message || '未知错误')
                }
              } catch { /* skip malformed */ }
            }
          }
        }
        return
      }
      // 回退：普通 JSON 响应
      const data = await response.json()
      if (data.error) { onError?.(data.error); return }
      onDone(data)
    })
    .catch(err => {
      console.error('Ingest file error (will retry with non-stream):', err)
      fetch(url, { method: 'POST', body })
        .then(r => r.json())
        .then(data => {
          if (data.error) { onError?.(data.error); return }
          if (data.status === 'duplicate_url' || data.status === 'duplicate_content') {
            onDone({ id: data.id, title: data.title, status: data.status, message: data.message })
            return
          }
          onDone(data)
        })
        .catch(err2 => {
          console.error('Ingest file fallback error:', err2)
          onError?.(`网络错误: ${err2.message}`)
        })
    })
}

// -- retranscribe --

export async function retranscribe(transcriptId: string, signal?: AbortSignal): Promise<{ status: string; engine: string } | { error: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${transcriptId}/retranscribe`, { method: 'POST', signal })
  return r.json()
}

// -- process (image) --

export async function processImage(transcriptId: string, mode: string, modelId?: number, signal?: AbortSignal): Promise<{
  status: string; mode: string; text: string; engine: string; result_id?: string; degraded?: string[]
} | { error: string }> {
  const r = await apiFetch(`${BASE}/process/${transcriptId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, model_id: modelId ?? undefined }),
    signal,
  })
  return r.json()
}

export interface ProcessResultItem {
  id: string; transcriptId: string; mode: string; engine: string
  content: string; createdAt: string
}

export async function fetchProcessResults(transcriptId: string): Promise<ProcessResultItem[]> {
  const r = await apiFetch(`${BASE}/process-results/${transcriptId}`)
  const data = await r.json()
  return data.results || []
}

// -- generate types --

/** 所有 source_type 共享的基础选项 */
export interface BaseGenOptions {
  mode?: string        // 逗号分隔的产物列表："notes,polished"
  style?: string
  formats?: string
  model_id?: number
  user_notes?: string
}

/** source_type=video 专属 */
export interface VideoGenOptions extends BaseGenOptions {
  video_understanding?: boolean
  video_interval?: number
  grid_cols?: number
  grid_rows?: number
  dedupe_enabled?: boolean
  unit_width?: number
  unit_height?: number
  save_quality?: number
  frame_quality_filter?: boolean
}

/** source_type=document (PDF/Word/PPT) 专属 */
export interface DocGenOptions extends BaseGenOptions {
  image_analyze?: boolean
  image_optimize?: boolean
}

/** source_type=epub 专属（继承 DocGenOptions） */
export interface EpubGenOptions extends DocGenOptions {
  use_skeleton?: boolean
  chapter_indices?: string
}

/** 所有生成选项的联合类型 */
export type GenOptions = BaseGenOptions | VideoGenOptions | DocGenOptions | EpubGenOptions

// -- generate --

export async function generateMarkdown(
  transcriptId: string,
  options: GenOptions & { polish_transcript?: boolean; formats?: string[] },
  signal?: AbortSignal,
): Promise<GenerateResult & { polished_content?: string | null; polished_id?: string }> {
  const r = await apiFetch(`${BASE}/generate/${transcriptId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(options),
    signal,
  })
  return r.json()
}

export async function getMarkdown(transcriptId: string, mode?: string, version?: number): Promise<(MarkdownView & { versions?: { version: number; id: string; createdAt: string; status?: string }[] }) | null> {
  const params = new URLSearchParams()
  if (mode) params.set('mode', mode)
  if (version) params.set('version', String(version))
  const qs = params.toString()
  const r = await apiFetch(`${BASE}/markdown/${transcriptId}${qs ? '?' + qs : ''}`)
  const data = await r.json()
  // 兼容旧格式（data.error）和新 AppError 格式（data.code + data.recoverable）
  if (data.error || data.code) return null
  return data
}

// -- stream generate --

export interface StreamGenerateCallbacks {
  onChunk: (text: string, mode?: string) => void
  onDone: (result?: { id?: string }) => void
  onError: (message: string) => void
  onCancelled: (savedPartial: boolean) => void
}

export function streamGenerate(
  transcriptId: string,
  options: GenOptions & { continue_from?: string },
  callbacks: StreamGenerateCallbacks,
  signal?: AbortSignal,
): void {
  const params = new URLSearchParams()
  const opts = options as Record<string, unknown>
  if (opts.mode) params.set('mode', opts.mode as string)
  if (opts.style) params.set('style', opts.style as string)
  if (opts.formats) params.set('formats', opts.formats as string)
  if (opts.model_id) params.set('model_id', String(opts.model_id))
  if (opts.user_notes) params.set('user_notes', opts.user_notes as string)
  // 使用 !== undefined 而非 truthiness，确保 false / 0 也能正确传输
  if (opts.video_understanding !== undefined) params.set('video_understanding', String(opts.video_understanding))
  if (opts.video_interval !== undefined) params.set('video_interval', String(opts.video_interval))
  if (opts.grid_cols !== undefined) params.set('grid_cols', String(opts.grid_cols))
  if (opts.grid_rows !== undefined) params.set('grid_rows', String(opts.grid_rows))
  if (opts.dedupe_enabled !== undefined) params.set('dedupe_enabled', String(opts.dedupe_enabled))
  if (opts.unit_width !== undefined) params.set('unit_width', String(opts.unit_width))
  if (opts.unit_height !== undefined) params.set('unit_height', String(opts.unit_height))
  if (opts.save_quality !== undefined) params.set('save_quality', String(opts.save_quality))
  if (opts.frame_quality_filter !== undefined) params.set('frame_quality_filter', String(opts.frame_quality_filter))
  if (options.continue_from) params.set('continue_from', options.continue_from)
  if (opts.image_analyze !== undefined) params.set('image_analyze', String(opts.image_analyze))
  if (opts.image_optimize !== undefined) params.set('image_optimize', String(opts.image_optimize))

  const qs = params.toString()
  // 追踪 SSE 是否正常结束（收到 done/error/cancelled 事件）
  let receivedDoneOrError = false
  const origOnDone = callbacks.onDone
  const origOnError = callbacks.onError
  callbacks.onDone = (...args) => { receivedDoneOrError = true; origOnDone(...args) }
  callbacks.onError = (msg) => { receivedDoneOrError = true; origOnError(msg) }

  fetch(`${BASE}/generate/${transcriptId}/stream${qs ? '?' + qs : ''}`, { signal })
    .then(async (response) => {
      if (!response.ok) {
        let body: any = {}
        try { body = await response.json() } catch { /* not JSON */ }
        callbacks.onError(body.detail || body.message || `请求失败 (${response.status})`)
        return
      }

      // SSE 解析（与现有的 ingest/stream 模式一致）
      const reader = response.body?.getReader()
      if (!reader) { callbacks.onError('无法读取响应流'); return }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // 归一化 \r\n → \n，部分 LLM API 返回 CRLF 换行
        buffer = buffer.replace(/\r\n/g, '\n')
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''

        for (const part of parts) {
          const lines = part.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'done') {
                // 双产物模式下单个产物的 done（如 {"mode":"polished","type":"done"}）不触发 onDone
                if (!data.mode) {
                  callbacks.onDone(data)
                }
              } else if (data.type === 'error') {
                callbacks.onError(data.message || '未知错误')
              } else if (data.type === 'cancelled') {
                callbacks.onCancelled(data.saved_partial || false)
              } else if (data.mode) {
                // 双产物模式：带 mode 标签的 chunk
                callbacks.onChunk(data.text || '', data.mode)
              } else if (data.text) {
                callbacks.onChunk(data.text)
              }
            } catch { /* broken JSON, skip */ }
          }
        }
      }
      // 循环退出但没有 done/error 事件 → 连接异常断开
      if (!receivedDoneOrError) {
        callbacks.onError('连接意外断开，请重试')
      }
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') return  // 用户主动取消
      callbacks.onError(getErrorMessage(err))
    })
}

// -- stream fact check / AI Review --

export interface ReviewChunk {
  text?: string
  phase?: string           // static_review | verify | review
  strategy?: string        // llm | web
  summary?: { claims_count: number; facts: number; data: number; opinion: number }
  issues?: { type: string; text: string; detail: string; action?: string }[]
  claim?: number; total?: number; claim_id?: string
  verdict?: string; evidence_strength?: string; basis?: string; action?: string
}

export interface ReviewDoneResult {
  id?: string
  verified_count?: number; disputed_count?: number; unverifiable_count?: number
  trustworthiness?: number; trustworthiness_label?: string
  deductions?: string[]; has_content?: boolean
}

export interface FactCheckCallbacks {
  onChunk: (text: string, phase?: string) => void
  onProgress: (data: ReviewChunk) => void
  onDone: (result?: ReviewDoneResult) => void
  onError: (message: string) => void
}

export function streamFactCheck(
  transcriptId: string,
  options: { model_id?: number },
  callbacks: FactCheckCallbacks,
  signal?: AbortSignal,
): void {
  const params = new URLSearchParams()
  if (options.model_id) params.set('model_id', String(options.model_id))
  const qs = params.toString()
  let receivedDoneOrError = false
  const origOnDone = callbacks.onDone
  const origOnError = callbacks.onError
  callbacks.onDone = (...args) => { receivedDoneOrError = true; origOnDone(...args) }
  callbacks.onError = (msg) => { receivedDoneOrError = true; origOnError(msg) }
  fetch(`${BASE}/fact-check/${transcriptId}/stream${qs ? '?' + qs : ''}`, { signal })
    .then(async (response) => {
      if (!response.ok) {
        let body: any = {}
        try { body = await response.json() } catch { /* */ }
        callbacks.onError(body.detail || body.message || `请求失败 (${response.status})`)
        return
      }
      const reader = response.body?.getReader()
      if (!reader) { callbacks.onError('无法读取响应流'); return }
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = buffer.replace(/\r\n/g, '\n')
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        for (const part of parts) {
          const lines = part.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'done') {
                if (!data.mode) callbacks.onDone(data)
              } else if (data.type === 'error') {
                callbacks.onError(data.message || '事实验证失败')
              } else if (data.type === 'cancelled') {
                callbacks.onError('验证已取消')
              } else if (data.phase === 'review' && data.text) {
                // 新：审核报告流式输出
                callbacks.onChunk(data.text, 'review')
              } else if (data.phase === 'static_review') {
                // 新：静态审核结果（summary + issues）
                callbacks.onProgress(data)
              } else if (data.phase === 'verify') {
                // 新旧兼容：验证进度
                if (data.text) callbacks.onChunk(data.text, 'verify')
                if (data.verdict) callbacks.onProgress(data)
              } else if (data.text) {
                callbacks.onChunk(data.text, data.phase || '')
              }
            } catch { /* broken JSON */ }
          }
        }
      }
      if (!receivedDoneOrError) {
        callbacks.onError('连接意外断开，请重试')
      }
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') return
      callbacks.onError(getErrorMessage(err))
    })
}

// -- chat --

export async function chat(
  question: string,
  docIds: string[],
  history: { role: string; content: string }[] = [],
  mode: string = 'single',
): Promise<ChatResult> {
  const r = await apiFetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, doc_ids: docIds, history, mode }),
  })
  return r.json()
}

export interface StreamChatCallbacks {
  onMeta: (conversationId: string) => void
  onText: (text: string) => void
  onToolStart: (display: string) => void
  onStatus?: (message: string) => void
  onToolResult?: (tool: string, summary: string) => void
  onDone: () => void
  onError: (message: string) => void
}

export function streamChat(
  question: string,
  docIds: string[],
  callbacks: StreamChatCallbacks,
  signal?: AbortSignal,
  conversationId?: string,
): void {
  const params = new URLSearchParams({ question })
  if (docIds.length > 0) params.set('doc_ids', docIds.join(','))
  if (conversationId) params.set('conversation_id', conversationId)

  fetch(`${BASE}/chat/stream?${params}`, { signal })
    .then(async (response) => {
      if (!response.ok) {
        let body: any = {}
        try { body = await response.json() } catch { /* not JSON */ }
        callbacks.onError(body.detail || body.message || `请求失败 (${response.status})`)
        return
      }
      const reader = response.body?.getReader()
      if (!reader) { callbacks.onError('无法读取响应流'); return }

      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = buffer.replace(/\r\n/g, '\n')
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        for (const part of parts) {
          const lines = part.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'meta') callbacks.onMeta(data.conversation_id)
              else if (data.type === 'text') callbacks.onText(data.text)
              else if (data.type === 'agent_status') callbacks.onStatus?.(data.message)
              else if (data.type === 'tool_start') callbacks.onToolStart(data.display)
              else if (data.type === 'tool_result') callbacks.onToolResult?.(data.tool, data.summary)
              else if (data.type === 'done') callbacks.onDone()
              else if (data.type === 'error') callbacks.onError(data.message)
            } catch { /* broken JSON */ }
          }
        }
      }
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') return
      callbacks.onError(getErrorMessage(err))
    })
}

// -- conversations --

export interface ConvEntry {
  id: string; title: string; model_id?: number | null
  created_at: string; updated_at: string
}

export interface ConvFull extends ConvEntry {
  messages: { id: string; role: string; content: string; timestamp: string }[]
  doc_ids: string[]
}

export async function listConversations(): Promise<ConvEntry[]> {
  return (await apiFetch(`${BASE}/conversations`)).json()
}

export async function getConversation(id: string): Promise<ConvFull | null> {
  const r = await apiFetch(`${BASE}/conversations/${id}`)
  const data = await r.json()
  if (data.error) return null
  return data
}

export async function deleteConversation(id: string) {
  return (await apiFetch(`${BASE}/conversations/${id}`, { method: 'DELETE' })).json()
}

export async function renameConversation(id: string, title: string) {
  return (await apiFetch(`${BASE}/conversations/${id}/rename`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })).json()
}

// -- config --

export async function getConfig() {
  const r = await apiFetch(`${BASE}/config`)
  return r.json()
}

export async function updateConfig(data: Record<string, any>) {
  const r = await apiFetch(`${BASE}/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function detectModels(provider: string, apiKey: string): Promise<{
  models?: { id: string; name: string; desc: string }[]
  error?: string
}> {
  const r = await apiFetch(`${BASE}/llm/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, api_key: apiKey }),
  })
  return r.json()
}

// -- model management (BiliNote style) --

export interface Provider {
  id: string; name: string; api_key: string; base_url: string
  type: string; enabled: number
}

export interface EnabledModel {
  id: number; provider_id: string; model_name: string
  capabilities?: string[]  // ["text", "image"]
  context_window?: number
}

export interface SceneModels {
  generate: number | null; chat: number | null; image_process: number | null
}

export async function listProviders(): Promise<Provider[]> {
  const r = await apiFetch(`${BASE}/providers`)
  return r.json()
}

export async function updateProvider(id: string, data: Partial<Provider>) {
  const r = await apiFetch(`${BASE}/providers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function addProvider(data: { name: string; api_key?: string; base_url?: string; type?: string }) {
  const r = await apiFetch(`${BASE}/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function deleteProvider(id: string) {
  const r = await apiFetch(`${BASE}/providers/${id}`, { method: 'DELETE' })
  return r.json()
}

export async function testProviderConnection(id: string) {
  const r = await apiFetch(`${BASE}/providers/${id}/test`, { method: 'POST' })
  return r.json()
}

export async function listEnabledModels(): Promise<EnabledModel[]> {
  const r = await apiFetch(`${BASE}/models`)
  return r.json()
}

export async function refreshModels(providerId: string): Promise<{
  models?: { id: string; owned_by: string; desc?: string }[]; error?: string
}> {
  const r = await apiFetch(`${BASE}/models/refresh/${providerId}`)
  return r.json()
}

export async function addModel(providerId: string, modelName: string, contextWindow?: number) {
  const r = await apiFetch(`${BASE}/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider_id: providerId, model_name: modelName,
      ...(contextWindow ? { context_window: contextWindow } : {}) }),
  })
  return r.json()
}

export async function deleteModel(modelId: number) {
  const r = await apiFetch(`${BASE}/models/${modelId}`, { method: 'DELETE' })
  return r.json()
}

export async function updateModelCapabilities(modelId: number, capabilities: string[]) {
  const r = await apiFetch(`${BASE}/models/${modelId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ capabilities }),
  })
  return r.json()
}

export async function getSceneModels(): Promise<SceneModels> {
  const r = await apiFetch(`${BASE}/scene-models`)
  return r.json()
}

export async function setSceneModels(data: SceneModels) {
  const r = await apiFetch(`${BASE}/scene-models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

// -- engine management --

/** 引擎厂商管理 API 的完整模型 */
export interface EngineProviderConfig {
  id: string; name: string; type: string; api_key: string
  base_url: string; capabilities: string[]
}
export interface EngineModel {
  id: number; provider_id: string; model_name: string; category: string
  capabilities?: string[]; desc?: string
}

export async function listEngineProviders(): Promise<EngineProviderConfig[]> {
  return (await apiFetch(`${BASE}/engine-providers`)).json()
}
export async function addEngineProvider(data: { name: string; api_key?: string; base_url?: string; type?: string }) {
  return (await apiFetch(`${BASE}/engine-providers`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })).json()
}
export async function deleteEngineProvider(id: string) {
  return (await apiFetch(`${BASE}/engine-providers/${id}`, { method: 'DELETE' })).json()
}
export async function listEngineModels(): Promise<EngineModel[]> {
  return (await apiFetch(`${BASE}/engine-models`)).json()
}
export async function deleteEngineModel(id: number) {
  return (await apiFetch(`${BASE}/engine-models/${id}`, { method: 'DELETE' })).json()
}

export async function getEngineBindings(): Promise<Record<string, number | null>> {
  return (await apiFetch(`${BASE}/engine-bindings`)).json()
}
export async function saveEngineBindings(data: Record<string, number | null>) {
  return (await apiFetch(`${BASE}/engine-bindings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bindings: data }) })).json()
}

/** 引擎自动降级开关 */
export async function getEngineAutoFallback(): Promise<{ enabled: boolean }> {
  return (await apiFetch(`${BASE}/engine-auto-fallback`)).json()
}
export async function setEngineAutoFallback(enabled: boolean): Promise<{ status: string; enabled: boolean }> {
  return (await apiFetch(`${BASE}/engine-auto-fallback`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) })).json()
}

/** 导出单个文档的全部产物为 zip → 触发浏览器/Tauri 下载 */
export async function exportAllProducts(txId: string) {
  const { downloadFromUrl } = await import('../lib/download')
  return downloadFromUrl(`/export/${txId}/all`, 'export.zip')
}

/** 批量导出多个文档的产物为 zip → 触发浏览器/Tauri 下载 */
export async function exportBatch(ids: string[]) {
  const resp = await apiFetch(`${BASE}/export/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  const blob = await resp.blob()
  const { downloadBlob } = await import('../lib/download')
  return downloadBlob(blob, 'export.zip')
}
