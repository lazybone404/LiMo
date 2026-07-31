/**
 * generate 域 — 生成 / 图片处理 / AI 审核（流式端点）。
 */
import { apiFetch, getErrorMessage, BASE, type GenerateResult, type MarkdownView } from './core'
import { parseSSEStream } from './sse'

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
  ocr_enabled?: boolean
  ocr_model_id?: number
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

export async function getMarkdown(transcriptId: string, mode?: string, version?: number): Promise<(MarkdownView & { versions?: { version: number; id: string; createdAt: string; status?: string; mode?: string }[] }) | null> {
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
  /** 部分产物失败/为空（另一产物成功）——用于 Toast 提示，不终止流程 */
  onWarning?: (message: string) => void
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
  if (opts.ocr_enabled !== undefined) params.set('ocr_enabled', String(opts.ocr_enabled))
  if (opts.ocr_model_id !== undefined) params.set('ocr_model_id', String(opts.ocr_model_id))

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

      const reader = response.body?.getReader()
      if (!reader) { callbacks.onError('无法读取响应流'); return }

      await parseSSEStream(reader, (data) => {
        if (data.type === 'done') {
          if (!data.mode) { callbacks.onDone(data) }
        } else if (data.type === 'error') {
          callbacks.onError(data.message || '未知错误')
        } else if (data.type === 'warning') {
          callbacks.onWarning?.(data.message || '部分产物生成失败')
        } else if (data.type === 'cancelled') {
          callbacks.onCancelled(data.saved_partial || false)
        } else if (data.mode) {
          callbacks.onChunk(data.text || '', data.mode)
        } else if (data.text) {
          callbacks.onChunk(data.text)
        }
      })

      if (!receivedDoneOrError) {
        callbacks.onError('连接意外断开，请重试')
      }
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') return  // 用户主动取消
      callbacks.onError(getErrorMessage(err))
    })
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
  options: { model_id?: number; verify_mode?: string },
  callbacks: FactCheckCallbacks,
  signal?: AbortSignal,
): void {
  const params = new URLSearchParams()
  if (options.model_id) params.set('model_id', String(options.model_id))
  if (options.verify_mode) params.set('verify_mode', options.verify_mode)
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

      await parseSSEStream(reader, (data) => {
        if (data.type === 'done') {
          if (!data.mode) callbacks.onDone(data)
        } else if (data.type === 'error') {
          callbacks.onError(data.message || '事实验证失败')
        } else if (data.type === 'cancelled') {
          callbacks.onError('验证已取消')
        } else if (data.phase === 'review' && data.text) {
          callbacks.onChunk(data.text, 'review')
        } else if (data.phase === 'static_review') {
          callbacks.onProgress(data)
        } else if (data.phase === 'verify') {
          if (data.text) callbacks.onChunk(data.text, 'verify')
          if (data.verdict) callbacks.onProgress(data)
        } else if (data.text) {
          callbacks.onChunk(data.text, data.phase || '')
        }
      })

      if (!receivedDoneOrError) {
        callbacks.onError('连接意外断开，请重试')
      }
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') return
      callbacks.onError(getErrorMessage(err))
    })
}
