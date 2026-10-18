import { useState, useRef, useEffect } from 'react'
import { Download, Copy, FileText, Globe, Archive, Share2, Box, Loader2, FileType, FileDown } from 'lucide-react'
import { useStore } from '../../store'
import { getTypeMeta } from '../gen-panel/constants'
import { TabRegistry } from '../../lib/tabRegistry'
import { getContentForView } from '../../lib/contentSources'
import { downloadText, downloadFromUrl, sanitizeExportName } from '../../lib/download'
import { linearizeEvidence, stripMarkers } from '../../lib/markdown-markers'
import { serializeCardVars } from '../../lib/card-prefs'
import { getSavedIntensity, textureOpacity } from '../../lib/texture'
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
  const markdownMeta = useStore(s => s.markdownMeta)
  const imageResults = useStore(s => s.imageResults)
  const docs = useStore(s => s.docs)
  const skeleton = useStore(s => s.skeleton)
  const cardStyle = useStore(s => s.cardStyle)
  const cardBg = useStore(s => s.cardBg)
  const cardLift = useStore(s => s.cardLift)
  const cardBorder = useStore(s => s.cardBorder)
  const markerDecor = useStore(s => s.markerDecor)
  // 主题跟随（2026-10-16）—— 与骨架/卡片同一条「预览所见即导出所得」，只是这条管**颜色**。
  const resolvedTheme = useStore(s => s.resolvedTheme)
  const accent = useStore(s => s.accent)
  const base = useStore(s => s.base)
  const font = useStore(s => s.font)
  const texture = useStore(s => s.texture)
  const exportPrefs = useStore(s => s.exportPrefs)
  const menuRef = useRef<HTMLDivElement>(null)

  const currentDoc = docs.find(d => d.id === currentDocId)
  const typeMeta = getTypeMeta(currentDoc?.sourceType || 'webpage')
  const addToast = useStore(s => s.addToast)

  const tabDef = TabRegistry.get(activeView) || null

  /**
   * 当前 tab 该导出**哪一版**产物（0 = 最新，后端默认）。
   *
   * 「所见即所得」在版本维度上的落点：用户在预览区切到 v1 再导出，拿到的必须是 v1。
   * ⚠️ **必须校验 mode**：`markdownMeta` 只在**加载文档时设一次**（notes 优先），
   * 切到 polished tab 并不会更新它——不看 mode 就把 notes 的版本号塞给 polished 导出，
   * 拿到的会是「polished 的第 N 版」而不是用户正在看的那一版。
   * 不匹配时落回 0（最新），这与 `VersionSwitcher` 的高亮判据同源（它也以「最新」兜底）。
   */
  const exportVersion = markdownMeta && markdownMeta.mode === tabDef?.htmlMode
    ? markdownMeta.version
    : 0

  /** 后端三条导出路径（HTML / Word / PDF）共用的查询参数 —— 版本语义只在一处拼。 */
  const backendParams = (format: string, mode: string): URLSearchParams => {
    const p = new URLSearchParams({ format, mode })
    // 0 是后端的「最新」默认值，发出去等价但多一个参数
    if (exportVersion > 0) p.set('version', String(exportVersion))
    // 骨架轴 / 卡片视觉方案 —— 「用户在预览栏看到什么，导出就应该得到什么」。
    // ⚠️ `paper`（默认）时**一个参数都不发**：后端默认值同为 `paper`，不发即走今天
    // 那条逐字未改的分支，URL 也与改造前逐字一致（零回归的最简形态）。
    // 三条路径（HTML / Word / PDF）共用本函数，故只在这里拼一次。
    if (skeleton === 'card') {
      p.set('skeleton', 'card')
      p.set('card_style', cardStyle)
      // 逐维度微调 —— 与骨架/方案同一条「所见即所得」：预览里覆盖了某个变量，
      // 导出就该覆盖同一个。⚠️ **没调过时一个参数都不发**（`serializeCardVars`
      // 返回空串），故未微调用户的导出 URL 与本轮之前逐字一致。
      // 传的是**解析好的 CSS 值**（`--card-lift:40%`）而不是档位 key —— 后端只做
      // 字符白名单校验、不认语义，见 `lib/card-prefs.ts::serializeCardVars` 的注。
      const cardVars = serializeCardVars({
        skeleton, style: cardStyle, bg: cardBg, lift: cardLift, border: cardBorder,
      })
      if (cardVars) p.set('card_vars', cardVars)
    }
    // 标记装饰开关：同样是「关掉才发」（后端默认 `marker_decor=1`）。语义是
    // 「预览里怎么显示，导出就怎么显示」——用户关掉装饰后导出的 HTML 也不带。
    // ⚠️ md / 纯文本**不看这个开关**：那里标记行是**整体剥掉**的（后端与这边的
    // `stripMarkers` 都剥），没有「装饰」可谈。
    if (!markerDecor) p.set('marker_decor', '0')
    // 主题跟随（2026-10-16）—— 六项**无条件发**（不像上面几个开关那样「关掉才发」）：
    // 「跟随应用主题」是这一轮的**产品决策**（用户已确认），不是可选项。后端逐项做
    // 字符白名单校验、非法项静默丢弃，故这里不需要同步一份 key 列表。
    // ⚠️ 明暗发 `resolvedTheme` 而**不是** `theme`：后者可能是 `'system'`，而导出页
    // 没有 matchMedia 之外的判断依据 —— `data-theme="system"` 是一条谁都不认识的死属性，
    // 症状是「系统设成暗色、导出却是白的」。
    p.set('theme_mode', resolvedTheme)
    p.set('accent', accent)
    p.set('base', base)
    p.set('font', font)
    p.set('texture', texture)
    // 强度传的是**反转映射后**的值（`textureOpacity`，与写进 DOM 的那份同源）——
    // 后端原样搬运、不再换算。`plain` 时它是一个恒无效果的属性（导出页的
    // `body::before` 选择器带 `:not([data-texture="plain"])`），故不发。
    if (texture !== 'plain') p.set('texture_opacity', textureOpacity(getSavedIntensity()))
    return p
  }

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
  /** `version > 0` 时文件名带 `_v{N}` —— 「按版本导出」的全部意义就是拿到**能区分开**
   *  的不同版本；不加的话两版落进同一个文件名，靠浏览器加 `(1)` 反而更难认。 */
  const makeFileName = (ext: string, version = 0): string => {
    const title = currentDoc?.title || 'export'
    // 清洗 + 限长收口在 `sanitizeExportName`（与 ExportPanel 及后端 `_safe_filename` 同源）
    const safe = sanitizeExportName(title)
    const date = new Date().toISOString().slice(0, 10)
    const suffix = tabDef?.exportSuffix || ''
    const vSuffix = version > 0 ? `_v${version}` : ''
    return `${safe}${suffix}${vSuffix}-${date}.${ext}`
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

  /**
   * 「下载 MD」的两条路径 —— 由设置里的 `exportPrefs.embedImages` 决定（默认开）。
   *
   * - **开（默认）：走后端**，`GET /export/{id}?format=md&image_md=inline` —— 图片以
   *   base64 内嵌进 `.md`，单文件自包含，离线 / 转发都正常显示。代价是**依赖本地后端**
   *   （这正是那个开关的语义，也是它默认开着的原因：正常使用中后端一直随应用在跑）。
   * - **关：纯前端**（改造前的老路径），直接把 store 里的正文落盘，图片只留链接。
   *
   * ⚠️ **两条路径必须给出同一个文件名**：后端会带回自己的 `Content-Disposition`
   * （`{标题}-{mode}.md`，不含版本后缀与日期），与前端 `makeFileName` 不同源 —— 故这里
   * 用 `preferSuggestedName` 强制覆盖。否则用户只是换了个开关，却会发现「连文件名都变了」。
   *
   * 另见 `lib/export-prefs.ts` 的文件头：该开关**只管这一个按钮**，复制与详细导出不受影响。
   */
  const handleDownloadMD = async () => {
    if (!exportContent || !currentDocId) return
    setOpen(false)

    if (exportPrefs.embedImages && tabDef?.htmlMode) {
      // 思维导图 tab 的正文就是笔记，而它的 htmlMode 是 `__mindmap__` 这个哨兵（不是 mode）
      const mode = tabDef.htmlMode === '__mindmap__' ? 'notes' : tabDef.htmlMode
      const params = new URLSearchParams({ format: 'md', mode, image_md: 'inline' })
      // 用户切到 v1 再导出，拿到的必须是 v1（与另三条后端路径同一个 `exportVersion`）
      if (exportVersion > 0) params.set('version', String(exportVersion))
      const result = await downloadFromUrl(
        `/export/${currentDocId}?${params.toString()}`,
        makeFileName('md', exportVersion),
        { preferSuggestedName: true },
      )
      if (result.ok) addToast('Markdown 已下载（图片已内嵌），请查看下载文件夹')
      else addToast(`下载失败: ${result.error}`, 'error')
      return
    }

    // ⚠️ **先线性化证据块、再剥掉其余标记行**（用户拍板：导出的文件应当是干净正文）：
    // `.md` 里留一行 `<!-- limo-block:v1 … -->` 只会让下游工具和读者困惑。剥的是
    // **标记行**，块内文字一个字不少。
    // ⚠️ **顺序不能反**：`linearizeEvidence` 靠围栏识别证据块，先剥就永远生成不出
    // 文末的「## 证据附录」（而两条链都「跑成功了」，零报错）。
    // 后端三条路径（html/word/pdf）走**转换**而非剥离，md 分支同样是
    // `linearize_evidence` + `strip_blocks`（`export.py`），故上一条走后端的路径与
    // 这里在「标记怎么办」上一致。
    const result = downloadText(
      stripMarkers(linearizeEvidence(exportContent)), makeFileName('md'), 'text/markdown',
    )
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

    const params = backendParams('html', tabDef.htmlMode)
    const result = await downloadFromUrl(`/export/${currentDocId}?${params.toString()}`, makeFileName('html', exportVersion))
    if (result.ok) {
      addToast('HTML 已导出，请查看下载文件夹')
    } else {
      addToast(`导出失败: ${result.error}`, 'error')
    }
  }

  const handleExportDocx = async () => {
    if (!currentDocId || !tabDef?.htmlMode || docxBlocked) return
    setOpen(false)
    const params = backendParams('docx', tabDef.htmlMode)
    const result = await downloadFromUrl(`/export/${currentDocId}?${params.toString()}`, makeFileName('docx', exportVersion))
    if (result.ok) addToast('Word 已导出，请查看下载文件夹')
    else addToast(`导出失败: ${result.error}`, 'error')
  }

  const handleExportPdf = async () => {
    if (!currentDocId || !tabDef?.htmlMode || docxBlocked || exportingPdf) return
    setOpen(false)
    setExportingPdf(true)
    try {
      const params = backendParams('html', tabDef.htmlMode)
      const result = await exportPdfFromUrl(
        `/export/${currentDocId}?${params.toString()}`,
        makeFileName('pdf', exportVersion),
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
      // 与 md 下载同源：复制的是「用户看到的内容」，标记行在预览里本就不显示。
      // ⚠️ 证据块同样线性化 —— 剪贴板里没有折叠，照搬围栏会让依据整段消失。
      await navigator.clipboard.writeText(stripMarkers(linearizeEvidence(exportContent)))
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
