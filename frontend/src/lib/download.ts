/**
 * 文件下载工具 — 纯浏览器 Blob + <a>.click() 方式。
 * 配合 Toast 提示"文件已下载，请查看下载文件夹"。
 */
import { apiUrl } from '../api/client'

export interface DownloadResult {
  ok: boolean
  error?: string
}

/**
 * 导出文件名里标题部分的**长度上限**。
 *
 * ⚠️ 必须与后端 `app/routers/export.py::_safe_filename` 的 `name[:80]` **同值**
 * （P1-12）：同一个标题，从客户端导出（MD/复制）与从后端导出（HTML/Word/ZIP）
 * 若截断长度不同，会得到两个不同的文件名，用户会以为那是两份东西。
 * 跨语言无法共享常量，故两侧各自写明出处，并由 `test/exportName.test.ts` 里的
 * **源码级守卫**逐字比对（模式 #65：同一条约束写在两种语言里时只能靠机器核对）。
 */
export const EXPORT_NAME_MAX = 80

/**
 * 标题 → 安全的导出文件名主体：去掉路径/文件系统非法字符 + 限长。
 *
 * ⚠️ 空标题兜底 `export`；但**全非法字符**的标题（如 `///`）清洗后会得到空串，
 * 这一点与原实现（两处各写一遍的 `replace + slice`）保持一致 —— 不在这里「顺手加个
 * 兜底」，那是行为变更而不是收敛。
 */
export function sanitizeExportName(title: string | undefined | null): string {
  return (title || 'export').replace(/[\\/:*?"<>|]/g, '').slice(0, EXPORT_NAME_MAX)
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
  opts: { preferSuggestedName?: boolean } = {},
): Promise<DownloadResult> {
  try {
    const fullUrl = apiUrl(url)
    const r = await fetch(fullUrl)
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }))
      return { ok: false, error: err.detail || `HTTP ${r.status}` }
    }
    const blob = await r.blob()
    // 优先从 Content-Disposition 取文件名。
    // ⚠️ `preferSuggestedName` 是给「同一个按钮有前后端两条路径」的场景用的：调用方
    // 必须能给同一个动作一个**恒定**的文件名，否则改个开关就发现文件名也跟着变了
    // （后端那条命名规则是 `{标题}-{mode}.md`，不含版本后缀与日期，与前端不同源）。
    const disp = r.headers.get('Content-Disposition') || ''
    const m = disp.match(/filename\*?=(?:UTF-8'')?(.+)/)
    const finalName = !opts.preferSuggestedName && m ? decodeURIComponent(m[1]) : suggestedName
    return downloadBlob(blob, finalName)
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}
