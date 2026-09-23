import { useState, useRef, useEffect } from 'react'
import { Download, Copy, FileText, Globe, Archive, Share2, Box, Loader2, FileType, FileDown } from 'lucide-react'
import { useStore } from '../../store'
import { getTypeMeta } from '../gen-panel/constants'
import { TabRegistry } from '../../lib/tabRegistry'
import { getContentForView } from '../../lib/contentSources'
import { downloadText, downloadFromUrl } from '../../lib/download'
import { exportPdfFromUrl } from '../../lib/print'

/** Tauri 环境检测 —— 决定 PDF 导出是「直接落文件」还是「退回打印对话框」 */
const IN_TAURI = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

/**
 * 导出菜单 — MD/HTML/ZIP/复制
 * 对标 BiliNote MarkdownHeader 导出按钮 + ExportUtils
 *
 * ## 设计原则
 *
 * 1. **预览一致**：用户在预览栏看到什么，导出就应该得到什么。
 *    每个 tab 的内容源 → 导出行为的映射见 lib/contentSources.ts（TabDef.contentSource 驱动）。
 * 2. **两条路径分工明确**：
 *    - ExportMenu（本组件）：快捷单文件导出，上下文跟随当前预览 tab
 *    - ExportPanel（侧边面板）：批量多文档导出，用户自选 tab/格式/主题
 * 3. **MD/复制走客户端**：直接从 Zustand store 读取内容，不经过后端。
 *    这意味着即使后端不支持某个 tab 的 mode，MD 下载仍然可用。
 * 4. **HTML 走后端**：需要服务端 KaTeX 预渲染 + Pygments 代码高亮 + 图片 base64 内联。
 *    mindmap tab 的 HTML 走专用端点 /export/{id}/mindmap。
 * 5. **无产物处理**：当前 tab 没有对应产物时（如导入后还没生成笔记），
 *    对应按钮 disabled，不会发送注定失败的请求。
 */

// ── Tab → 导出行为 ──
// 内容来源统一走 lib/contentSources.ts 的 CONTENT_GETTERS（TabDef.contentSource 驱动），
// 新增 tab 只需在 tabRegistry 注册一行，导出/复制自动跟随。

/**
 * 导出菜单 — MD/HTML/复制
 * 对标 BiliNote MarkdownHeader 导出按钮 + ExportUtils
 */
export function ExportMenu() {
  const [open, setOpen] = useState(false)
  const [exportingZip, setExportingZip] = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)
  const currentContent = useStore(s => s.currentContent)
  const currentPolishedContent = useStore(s => s.currentPolishedContent)
  const currentRawText = useStore(s => s.currentRawText)
  const currentDocId = useStore(s => s.currentDocId)
  const activeView = useStore(s => s.activeView)
  const imageResults = useStore(s => s.imageResults)
  const docs = useStore(s => s.docs)
  const menuRef = useRef<HTMLDivElement>(null)

  const currentDoc = docs.find(d => d.id === currentDocId)
  const typeMeta = getTypeMeta(currentDoc?.sourceType || 'webpage')
  const addToast = useStore(s => s.addToast)

  const tabDef = TabRegistry.get(activeView) || null

  // ── 按 tab 确定导出内容 ──
  // 返回 null 表示当前 tab 没有可导出内容（如还没生成笔记、图片未处理等）
  const getExportContent = (): string | null => {
    if (!currentDocId || !tabDef) return null
    return getContentForView(activeView, {
      currentDocId, activeView,
      currentContent, currentPolishedContent, currentRawText,
      imageResults,
    })
  }

  const exportContent = getExportContent()

  // ── 按钮可用性 ──
  // 主按钮（MD/HTML/复制）：依赖当前 tab 的导出内容
  const canExportMD = !!exportContent
  // HTML 导出：需要有 htmlMode 配置 + 有可导出内容
  const canExportHTML = !!(tabDef?.htmlMode && exportContent)
  // Word 导出：复用同一 mode，但思维导图无 Word 形态（交互脑图是 HTML 专有）
  const docxBlocked = tabDef?.htmlMode === '__mindmap__'
  const canExportDocx = !!(tabDef?.htmlMode && !docxBlocked && exportContent)
  // PDF：喂给 WebView2 PrintToPdf 的就是 HTML 导出的那份自包含 HTML（已含 `_PRINT_CSS`），
  // 故可用条件与 Word 相同；思维导图走专用端点，打印效果未验证，一并禁用
  const canExportPdf = canExportDocx
  // ZIP 全部产物：只要有任意产物就可用（不限于当前 tab）
  const hasExportableContent = !!(
    currentContent || currentPolishedContent || currentRawText ||
    (typeMeta.isImage && Object.keys(imageResults[currentDocId || ''] || {}).length > 0)
  )

  // ── 生成文件名 ──
  const makeFileName = (ext: string): string => {
    const title = currentDoc?.title || 'export'
    const safe = title.replace(/[\\/:*?"<>|]/g, '').slice(0, 60)
    const date = new Date().toISOString().slice(0, 10)
    const suffix = tabDef?.exportSuffix || ''
    return `${safe}${suffix}-${date}.${ext}`
  }

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [open])

  // ── 操作处理 ──

  const handleDownloadMD = () => {
    if (!exportContent) return
    setOpen(false)
    const result = downloadText(exportContent, makeFileName('md'), 'text/markdown')
    if (result.ok) {
      addToast('Markdown 已下载，请查看下载文件夹')
    } else {
      addToast(`下载失败: ${result.error}`, 'error')
    }
  }

  const handleExportHTML = async () => {
    if (!currentDocId || !tabDef?.htmlMode) return
    setOpen(false)

    // mindmap tab：走专用端点，生成交互式脑图 HTML
    if (tabDef.htmlMode === '__mindmap__') {
      const result = await downloadFromUrl(
        `/export/${currentDocId}/mindmap`,
        makeFileName('html').replace('.html', '_mindmap.html'),
      )
      if (result.ok) addToast('思维导图 HTML 已导出，请查看下载文件夹')
      else addToast(`导出失败: ${result.error}`, 'error')
      return
    }

    const params = new URLSearchParams({ format: 'html', mode: tabDef.htmlMode })
    const result = await downloadFromUrl(`/export/${currentDocId}?${params.toString()}`, makeFileName('html'))
    if (result.ok) {
      addToast('HTML 已导出，请查看下载文件夹')
    } else {
      addToast(`导出失败: ${result.error}`, 'error')
    }
  }

  const handleExportDocx = async () => {
    if (!currentDocId || !tabDef?.htmlMode || docxBlocked) return
    setOpen(false)
    const params = new URLSearchParams({ format: 'docx', mode: tabDef.htmlMode })
    const result = await downloadFromUrl(`/export/${currentDocId}?${params.toString()}`, makeFileName('docx'))
    if (result.ok) addToast('Word 已导出，请查看下载文件夹')
    else addToast(`导出失败: ${result.error}`, 'error')
  }

  const handleExportPdf = async () => {
    if (!currentDocId || !tabDef?.htmlMode || docxBlocked || exportingPdf) return
    setOpen(false)
    setExportingPdf(true)
    try {
      const params = new URLSearchParams({ format: 'html', mode: tabDef.htmlMode })
      const result = await exportPdfFromUrl(
        `/export/${currentDocId}?${params.toString()}`,
        makeFileName('pdf'),
      )
      if (result.ok) {
        // Tauri：直接落盘，报出完整路径方便用户去找；浏览器：只是调起了对话框
        addToast(
          result.path
            ? `PDF 已保存到 ${result.path}`
            : '已调起打印，请在对话框中选择「另存为 PDF」',
        )
      } else {
        addToast(`导出 PDF 失败: ${result.error}`, 'error')
      }
    } finally {
      setExportingPdf(false)
    }
  }

  const handleCopy = async () => {
    if (!exportContent) return
    try {
      await navigator.clipboard.writeText(exportContent)
      addToast('已复制到剪贴板')
    } catch {
      addToast('复制失败，请手动复制', 'error')
    }
    setOpen(false)
  }

  const handleExportSVG = () => {
    const svg = document.querySelector('#mindmap svg') as SVGElement
    if (!svg) return
    setOpen(false)
    const clone = svg.cloneNode(true) as SVGElement
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    // 收集页面中 markmap 相关的样式表规则，内联到 SVG 中
    // cloneNode 丢失外部 CSS，文本/颜色等样式会消失
    const styleEl = document.createElement('style')
    styleEl.textContent = Array.from(document.styleSheets)
      .filter(s => { try { return s.cssRules } catch { return false } })
      .flatMap(s => Array.from(s.cssRules))
      .filter(r => r instanceof CSSStyleRule)
      .map(r => r.cssText)
      .join('\n')
    clone.insertBefore(styleEl, clone.firstChild)
    const result = downloadText(clone.outerHTML, makeFileName('svg'), 'image/svg+xml')
    if (result.ok) {
      addToast('SVG 已下载，请查看下载文件夹')
    } else {
      addToast(`下载失败: ${result.error}`, 'error')
    }
  }

  const handleMindmapHTML = async () => {
    if (!currentDocId) return
    setOpen(false)
    const result = await downloadFromUrl(`/export/${currentDocId}/mindmap`,
      makeFileName('html').replace('.html', '_mindmap.html'))
    if (result.ok) {
      addToast('思维导图 HTML 已导出，请查看下载文件夹')
    } else {
      addToast(`导出失败: ${result.error}`, 'error')
    }
  }

  const isMindmap = activeView === 'mindmap'

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen(!open)}
        disabled={!canExportMD}
        className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface-hover rounded-full disabled:opacity-30 disabled:cursor-not-allowed"
        title={!canExportMD && tabDef ? `尚未生成${tabDef.exportLabel}` : '导出'}
      >
        <Download size={14} />
      </button>

      {open && (
        <div className="absolute right-0 top-9 w-40 bg-elevated rounded-xl shadow-lg border border-border-light py-1 z-20 animate-[fadeIn_0.15s_ease-out]">
          {/* MD 下载 — 所有有内容的 tab 都可用 */}
          <button
            onClick={handleDownloadMD}
            disabled={!canExportMD}
            className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors disabled:opacity-30"
          >
            <FileText size={14} className="text-muted" />
            下载 MD
          </button>
          {/* HTML 导出 — mindmap 走专用端点，无 htmlMode 的 tab 隐藏 */}
          <button
            onClick={handleExportHTML}
            disabled={!canExportHTML}
            className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors disabled:opacity-30"
          >
            <Globe size={14} className="text-muted" />
            导出 HTML
          </button>
          {/* Word 导出 — 与 HTML 同门控，思维导图 tab 除外 */}
          <button
            onClick={handleExportDocx}
            disabled={!canExportDocx}
            title={docxBlocked ? '思维导图没有 Word 形态，请用 HTML 导出' : '导出为 Word 文档（.docx）'}
            className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors disabled:opacity-30"
          >
            <FileType size={14} className="text-muted" />
            导出 Word
          </button>
          {/* 导出 PDF — 内容源是 HTML 导出的那份自包含 HTML（自带打印样式） */}
          <button
            onClick={handleExportPdf}
            disabled={!canExportPdf || exportingPdf}
            title={
              docxBlocked
                ? '思维导图请改用「导出交互版 (HTML)」'
                : IN_TAURI
                  ? '导出为 PDF，保存到系统下载目录'
                  : '浏览器环境将调起打印，请在其中选择「另存为 PDF」'
            }
            className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors disabled:opacity-30"
          >
            {exportingPdf ? <Loader2 size={14} className="text-accent animate-spin" /> : <FileDown size={14} className="text-muted" />}
            {exportingPdf ? '导出中...' : '导出 PDF'}
          </button>
          {/* ZIP 全部产物 — 只要有任意产物就可用 */}
          <button
            onClick={async () => {
              if (!currentDocId || exportingZip) return
              setExportingZip(true); setOpen(false)
              try {
                const result = await downloadFromUrl(`/export/${currentDocId}/all`, 'export.zip')
                if (result.ok) addToast('ZIP 已导出，请查看下载文件夹')
                else addToast(`导出失败: ${result.error}`, 'error')
              } catch { addToast('ZIP 导出失败', 'error') }
              finally { setExportingZip(false) }
            }}
            disabled={!hasExportableContent || exportingZip}
            className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors disabled:opacity-30"
          >
            {exportingZip ? <Loader2 size={14} className="text-accent animate-spin" /> : <Archive size={14} className="text-muted" />}
            {exportingZip ? '打包中...' : '导出全部产物 (ZIP)'}
          </button>
          {/* 思维导图专属：SVG + 交互版 HTML */}
          {isMindmap && (
            <>
              <button onClick={handleExportSVG}
                className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors">
                <Box size={14} className="text-muted" />
                下载 SVG
              </button>
              <button onClick={handleMindmapHTML}
                className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors">
                <Share2 size={14} className="text-muted" />
                导出交互版 (HTML)
              </button>
            </>
          )}
          <div className="border-t border-border-light my-1" />
          <button
            onClick={handleCopy}
            disabled={!canExportMD}
            className="w-full text-left px-3 py-2 text-[13px] hover:bg-surface-hover flex items-center gap-2 transition-colors disabled:opacity-30"
          >
            <Copy size={14} className="text-muted" />
            复制 Markdown
          </button>
        </div>
      )}
    </div>
  )
}
