/**
 * export 域 — 导出 ZIP 下载。
 *
 * ⚠️ 这里原有一个 `streamExportProgress`（POST `/export/custom/stream` 收进度 SSE），
 * **全前端零调用**，已删除（P1-12）。后端那两个端点（SSE 进度 + 临时 ZIP 下载）
 * 仍然存在且行为正确，只是 UI 从未接线 —— 见 docs/export-pipeline.md。
 * 将来若要接进度反馈，要做的是**把 UI 接起来**，而不是把这个封装函数抄回来：
 * 抄回来仍是零调用，死代码只是换了个地方躺。
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
