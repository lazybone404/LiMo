/**
 * export 域 — 自定义导出流式进度 + ZIP 下载。
 */
import { apiFetch, BASE } from './core'
import { parseSSEStream } from './sse'

export interface ExportProgressCallbacks {
  onProgress?: (event: { phase: string; doc: number; total: number; message: string }) => void
  onDone?: (downloadUrl: string, totalFiles: number) => void
  onError?: (msg: string) => void
}

export function streamExportProgress(
  body: any,
  callbacks: ExportProgressCallbacks,
  signal?: AbortSignal,
): void {
  fetch(`${BASE}/export/custom/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        let err: any = {}
        try { err = await response.json() } catch { /* not JSON */ }
        callbacks.onError?.(err.detail || err.message || `请求失败 (${response.status})`)
        return
      }
      const reader = response.body?.getReader()
      if (!reader) { callbacks.onError?.('无法读取响应流'); return }

      await parseSSEStream(reader, (event) => {
        switch (event.type) {
          case 'progress': (callbacks.onProgress as any)?.(event); break
          case 'done': callbacks.onDone?.(event.download_url, event.total_files); break
          case 'error': callbacks.onError?.(event.message); break
          case 'cancelled': callbacks.onError?.('导出已取消'); break
        }
      })
    })
    .catch((err) => {
      if ((err as Error).name !== 'AbortError') {
        callbacks.onError?.((err as Error).message)
      }
    })
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
