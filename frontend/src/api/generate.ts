/**
 * generate 域 — 生成 / 图片处理（流式端点）。
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
  /** 参考文件暂存 id（POST /api/references 取得；后端仅 notes 注入） */
  reference_ids?: string[]
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

/** 所有生成选项的联合类型 */
export type GenOptions = BaseGenOptions | VideoGenOptions | DocGenOptions

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

/** 保存 notes/polished 手动编辑 — 覆盖当前版本（两版制自动保留生成版为 prev），返回对齐 GET /api/markdown */
export async function saveMarkdownContent(
  docId: string,
  mode: 'notes' | 'polished',
  content: string,
): Promise<MarkdownView & { edited?: boolean; versions?: { version: number; id: string; createdAt: string; status?: string; mode?: string }[] }> {
  const r = await apiFetch(`${BASE}/markdown/${docId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, content }),
  })
  return r.json()
}

// -- stream generate --

export interface StreamGenerateCallbacks {
  onChunk: (text: string, mode?: string) => void
  onDone: (result?: { id?: string }) => void
  onError: (message: string) => void
  onCancelled: (savedPartial: boolean) => void
  /** 部分产物失败/为空（另一产物成功）——用于 Toast 提示，不终止流程。
   * kind='chart_missing' 为图表未生成（可选组件缺失，产物本身成功），
   * 前端不标 partial——避免误报「部分产物生成失败」。 */
  onWarning?: (message: string, kind?: string) => void
  /** 思考链（deepseek-v4 reasoning_content）——ProcessingPlaceholder 展示"模型思考中" */
  onReasoning?: (text: string, mode?: string) => void
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
  const refIds = opts.reference_ids as string[] | undefined
  if (refIds?.length) params.set('reference_ids', refIds.join(','))
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
          callbacks.onWarning?.(data.message || '部分产物生成失败', data.kind)
        } else if (data.type === 'cancelled') {
          receivedDoneOrError = true  // cancelled 是终态 — 否则流结束后误报「连接意外断开」
          callbacks.onCancelled(data.saved_partial || false)
        } else if (data.type === 'reasoning') {
          // 思考链（带 mode 也不走 onChunk——必须在 data.mode 判断之前）
          callbacks.onReasoning?.(data.text || '', data.mode)
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

