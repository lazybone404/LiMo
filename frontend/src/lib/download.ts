/**
 * 文件下载工具 — 纯浏览器 Blob + <a>.click() 方式。
 * 配合 Toast 提示"文件已下载，请查看下载文件夹"。
 */
import { apiUrl } from '../api/client'

export interface DownloadResult {
  ok: boolean
  error?: string
}

/** 下载文本（Markdown/SVG 等） */
export function downloadText(
  content: string,
  suggestedName: string,
  mimeType: string = 'text/plain',
): DownloadResult {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` })
  return downloadBlob(blob, suggestedName)
}

/** 下载 Blob（ZIP 等） */
export function downloadBlob(blob: Blob, suggestedName: string): DownloadResult {
  try {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = suggestedName
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    // 延迟释放，确保浏览器已开始下载
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/** 从后端 URL 下载（HTML/ZIP/MD 等导出端点） */
export async function downloadFromUrl(
  url: string,
  suggestedName: string,
): Promise<DownloadResult> {
  try {
    const fullUrl = apiUrl(url)
    const r = await fetch(fullUrl)
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }))
      return { ok: false, error: err.detail || `HTTP ${r.status}` }
    }
    const blob = await r.blob()
    // 优先从 Content-Disposition 取文件名
    const disp = r.headers.get('Content-Disposition') || ''
    const m = disp.match(/filename\*?=(?:UTF-8'')?(.+)/)
    const finalName = m ? decodeURIComponent(m[1]) : suggestedName
    return downloadBlob(blob, finalName)
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}
