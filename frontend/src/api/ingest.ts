/**
 * ingest 域 — URL/文本/文件导入（SSE 流式 + 非流式回退）。
 *
 * 注：原 fetchSSE / fetchFileSSE 两份近逐行重复的实现已合并为私有 ingestStream
 * （仅 body 类型与 header 不同），手写 SSE 解析统一走 sse.ts 的 parseSSEStream。
 * 已知微小差异：console.error 文案从两份统一为一条。
 */
import { BASE } from './core'
import { parseSSEStream } from './sse'

type ProgressCb = (ev: { phase: string; status: string; percent?: number; message?: string }) => void
type DoneCb = (doc: any) => void
type ErrorCb = (err: string) => void

export function ingestURL(
  url: string,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
) {
  const body = new URLSearchParams({ url })
  return ingestStream(`${BASE}/ingest`, body, onProgress, onDone, onError, signal)
}

export function ingestText(
  text: string,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
) {
  const body = new URLSearchParams({ text })
  return ingestStream(`${BASE}/ingest`, body, onProgress, onDone, onError, signal)
}

export function ingestFile(
  file: File,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
) {
  const body = new FormData()
  body.append('file', file)
  return ingestStream(`${BASE}/ingest`, body, onProgress, onDone, onError, signal)
}

/**
 * 统一流式导入（私有）— 合并原 fetchSSE(URLSearchParams 版) 与 fetchFileSSE(FormData 版)。
 * 行为逐点保留：SSE 成功不回退；reader 缺失/网络错误（非 Abort）→ 回退 JSON；
 * 回退里 duplicate_url/duplicate_content 归一化 onDone；回退失败 → `网络错误: ...`。
 */
async function ingestStream(
  url: string,
  body: URLSearchParams | FormData,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
) {
  // URLSearchParams 需显式 Content-Type；FormData 让浏览器自动设置 multipart
  const headers = body instanceof URLSearchParams
    ? { 'Content-Type': 'application/x-www-form-urlencoded' }
    : undefined
  try {
    // 优先尝试 SSE 流式端点
    const response = await fetch(url + '/stream', { method: 'POST', body, headers, signal })
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('text/event-stream')) {
      const reader = response.body?.getReader()
      if (!reader) throw new Error('No reader')
      await parseSSEStream(reader, (data) => {
        if (data.type === 'progress') {
          onProgress(data)
        } else if (data.type === 'done') {
          onDone(data)
        } else if (data.type === 'error') {
          onProgress({ phase: 'error', status: 'error', message: data.message || '未知错误' })
          onError?.(data.message || '未知错误')
        }
      })
      return
    }
    // 回退：普通 JSON 响应
    const data = await response.json()
    if (data.error) {
      onError?.(data.error)
      return
    }
    onDone(data)
  } catch (err) {
    if ((err as Error).name === 'AbortError') return  // 用户主动取消，不回退
    console.error('Ingest error (will retry with non-stream):', err)
    // 回退：非流式端点
    await fallbackJson(url, body, onDone, onError)
  }
}

/** 非流式回退（SSE 失败时的兜底请求） */
async function fallbackJson(
  url: string,
  body: URLSearchParams | FormData,
  onDone: DoneCb,
  onError?: ErrorCb,
) {
  try {
    const headers = body instanceof URLSearchParams
      ? { 'Content-Type': 'application/x-www-form-urlencoded' }
      : undefined
    const r = await fetch(url, { method: 'POST', body, headers })
    const data = await r.json()
    if (data.error) {
      onError?.(data.error)
      return
    }
    if (data.status === 'duplicate_url' || data.status === 'duplicate_content') {
      onDone({ id: data.id, title: data.title, status: data.status, message: data.message })
      return
    }
    onDone(data)
  } catch (err2) {
    console.error('Ingest fallback error:', err2)
    onError?.(`网络错误: ${(err2 as Error).message}`)
  }
}
