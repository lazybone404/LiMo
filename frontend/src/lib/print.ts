/**
 * 打印 / PDF 导出工具。
 *
 * 出口都吃**同一份自包含 HTML**（后端 `/export/{id}?format=html&mode=…`，自带
 * `_PRINT_CSS`）。为什么不直接 `window.print()` 打印应用 DOM：
 *   1. 应用 DOM 是屏幕布局（三栏 + 工具条 + 主题 CSS 变量），要打印得另写一套
 *      「隐藏 UI、只留正文」的打印样式，与后端 HTML 导出的那套是两份，必然漂移；
 *   2. 后端那份 HTML 已含 KaTeX/Pygments/图片内联，内容与用户预览、HTML 导出三者同源。
 *
 * 两条落地路径：
 *   - **Tauri**：`exportPdfFromUrl()` → Rust `export_pdf` → WebView2 `PrintToPdf`
 *     **直接落文件**，无对话框（存到系统「下载」目录）。
 *   - **浏览器**：没有 WebView2 COM，退回 `printFromUrl()` 的打印对话框，
 *     用户在其中选「另存为 PDF」。
 */
import { apiUrl } from '../api/client'

/** iframe 必须占正常尺寸（而非 width:0/height:0）——零尺寸会让内部视口为 0，布局全乱 */
const OFFSCREEN = 'position:fixed;left:-9999px;top:0;width:1024px;height:1400px;border:0;'

export interface PrintResult {
  ok: boolean
  error?: string
}

export interface PdfExportResult {
  ok: boolean
  /** 落盘完整路径（仅 Tauri 路径返回；浏览器路径由用户在对话框里自选位置） */
  path?: string
  error?: string
}

/** Tauri 环境检测 —— 与 api/core.ts 同款判据（`__TAURI_INTERNALS__` 由 Tauri 注入） */
function inTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/**
 * 给导出 URL 追加 `inline=1`。
 *
 * ⚠️ 这一步不能省：`/export/{id}` 默认带 `Content-Disposition: attachment`（点「HTML」
 * 就该下载文件）。而 Tauri 路径是让 WebView2 **直接导航**到这个 URL 再 PrintToPdf，
 * attachment 会让它去「下载」而非渲染 —— 窗口全白，打印出一张空白 PDF 却提示成功。
 * 在出口处统一加，免得每个调用方都要记得。浏览器路径（fetch + srcdoc）不受影响。
 */
function withInline(path: string): string {
  const [base, query = ''] = path.split('?')
  const params = new URLSearchParams(query)
  params.set('inline', '1')
  return `${base}?${params.toString()}`
}

/**
 * 导出 PDF。
 *
 * - **Tauri 内**：走 Rust `export_pdf` —— WebView2 `PrintToPdf` 把后端那份自包含
 *   HTML 渲染成 PDF 直接写盘，不经过打印对话框。内容越不过网络：新窗口加载的是
 *   `127.0.0.1:7413` 上本地后端产出的 HTML。
 * - **浏览器内**：无 WebView2 COM 可用，退回打印对话框（内容同源，只是多一步手动确认）。
 *
 * @param path 相对路径（不含 `/api` 前缀），如 `/export/{id}?format=html&mode=notes`
 * @param fileName 建议文件名（含 `.pdf` 后缀亦可，Rust 侧会剥掉再加）
 */
export async function exportPdfFromUrl(
  path: string,
  fileName: string,
): Promise<PdfExportResult> {
  const renderPath = withInline(path)

  if (!inTauri()) {
    const r = await printFromUrl(renderPath)
    return r.ok ? { ok: true } : { ok: false, error: r.error }
  }

  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const savedPath = await invoke<string>('export_pdf', {
      url: apiUrl(renderPath),
      fileName,
    })
    return { ok: true, path: savedPath }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * 拉取后端自包含 HTML 并打印（用户可在打印对话框里选「另存为 PDF」）。
 *
 * @param path 相对路径（不含 `/api` 前缀，如 `/export/{id}?format=html&mode=notes`）
 */
export async function printFromUrl(path: string): Promise<PrintResult> {
  try {
    const r = await fetch(apiUrl(path))
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }))
      return { ok: false, error: err.detail || `HTTP ${r.status}` }
    }
    const html = await r.text()
    await printHtml(html)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/** 把自包含 HTML 载入屏幕外 iframe，等资源就绪后调起打印对话框。 */
export function printHtml(html: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const iframe = document.createElement('iframe')
    iframe.style.cssText = OFFSCREEN
    iframe.setAttribute('aria-hidden', 'true')

    const cleanup = () => {
      if (iframe.parentNode) document.body.removeChild(iframe)
    }

    iframe.onload = async () => {
      try {
        const win = iframe.contentWindow
        const doc = iframe.contentDocument
        if (!win || !doc) throw new Error('iframe 未就绪')

        // 等图片解码（base64 内联通常已就绪，但大图不保证）
        await Promise.all(
          Array.from(doc.images).map(img =>
            img.complete
              ? Promise.resolve()
              : new Promise<void>(res => {
                  img.onload = img.onerror = () => res()
                }),
          ),
        )
        // 让 KaTeX / 字体就绪：无 Node.js 时公式由页内 JS 在 load 后渲染
        await new Promise(res => setTimeout(res, 300))

        win.focus()
        win.print()
        resolve()
        // 打印对话框是模态的，afterprint 才代表用户已处理完；
        // 但部分环境不派发该事件，故留 60s 兜底回收，避免 iframe 常驻
        win.addEventListener('afterprint', cleanup, { once: true })
        setTimeout(cleanup, 60000)
      } catch (e) {
        cleanup()
        reject(e)
      }
    }

    iframe.srcdoc = html
    document.body.appendChild(iframe)
  })
}
