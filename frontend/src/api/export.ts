/**
 * export 域 — 导出 ZIP 下载。
 *
 * ⚠️ 这里原有一个 `streamExportProgress`（POST `/export/custom/stream` 收进度 SSE），
 * **全前端零调用**，已删除（P1-12）。此后**进度反馈已接线（2026-10-29）**——
 * 落点在 `components/ui/ExportPanel.tsx::handleCustomExport`，它自己 `fetch` +
 * `api/sse.ts::parseSSEStream`，**没有把封装函数抄回来**：那份封装当年零调用的根因
 * 是没人需要它，不是因为函数写得不好；现在需要的地方只有一个，再抽一层就是纯转发。
 */
import { apiFetch, BASE } from './core'

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
