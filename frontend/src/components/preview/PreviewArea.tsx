import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useStore } from '../../store'
import { getTranscript, getMarkdown, fetchProcessResults, streamFactCheck, apiUrl, ApiErrorException } from '../../api/client'
import { loadGenPrefs } from '../gen-panel/genPrefs'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'
import { ScanText, Table, ScanLine, Eye, ChevronRight, Loader2, Terminal, Image, Lightbulb, MessageSquare, Package, AlertTriangle } from 'lucide-react'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'
import { getTypeMeta } from '../gen-panel/constants'
import { VideoBanner } from './VideoBanner'
import { LogPanel } from '../ui/LogPanel'
import { ExportMenu } from '../ui/ExportMenu'
import { ResizeHandle } from '../ui/ResizeHandle'
import { TOC } from './TOC'
import MindmapView from './MindmapView'
import { EpubSkeletonPanel } from './EpubSkeletonPanel'
import { TranscriptView } from './TranscriptView'
import { MarkdownPreview } from './MarkdownPreview'
import { PartialBanner } from './PartialBanner'
import 'katex/dist/katex.min.css'

/** PreviewArea 主组件 — 内容预览 + Tab 切换 + 工具栏 */
export function PreviewArea() {
  const activeView = useStore(s => s.activeView)
  const setActiveView = useStore(s => s.setActiveView)
  const currentDocId = useStore(s => s.currentDocId)
  const currentContent = useStore(s => s.currentContent)
  const currentPolishedContent = useStore(s => s.currentPolishedContent)
  const currentRawText = useStore(s => s.currentRawText)
  const markdownMeta = useStore(s => s.markdownMeta)
  const markdownVersions = useStore(s => s.markdownVersions)
  const streamingContent = useStore(s => s.streamingContent)
  const isStreaming = useStore(s => s.isStreaming)
  const currentFactCheckContent = useStore(s => s.currentFactCheckContent)
  const factCheckMeta = useStore(s => s.factCheckMeta)
  const factCheckProgress = useStore(s => s.factCheckProgress)
  const factChecking = useStore(s => s.factChecking)
  const setCurrentContent = useStore(s => s.setCurrentContent)
  const setCurrentPolishedContent = useStore(s => s.setCurrentPolishedContent)
  const setCurrentRawText = useStore(s => s.setCurrentRawText)
  const setMarkdownMeta = useStore(s => s.setMarkdownMeta)
  const setMarkdownVersions = useStore(s => s.setMarkdownVersions)
  const docs = useStore(s => s.docs)
  const imageResults = useStore(s => s.imageResults)
  const loadImageResults = useStore(s => s.loadImageResults)
  const tocWidth = useStore(s => s.panelWidths.toc)
  const tocCollapsed = useStore(s => s.panelsCollapsed.toc)
  const setPanelWidth = useStore(s => s.setPanelWidth)
  const setPanelCollapsed = useStore(s => s.setPanelCollapsed)

  const currentDoc = docs.find(d => d.id === currentDocId)
  const sourceUrl = currentDoc?.sourceUrl
  const isVideo = currentDoc?.sourceType === 'video'
  const imageMarkdownComponents = useMemo(() => createMarkdownComponents(sourceUrl, isVideo), [sourceUrl, isVideo])

  // TOC 拖拽回调
  const resizeToc = useCallback((dx: number) => {
    const w = useStore.getState().panelWidths.toc
    setPanelWidth('toc', Math.max(60, w + dx))
  }, [setPanelWidth])

  // 加载指定版本的 markdown
  const [versionLoading, setVersionLoading] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [contentLoadError, setContentLoadError] = useState(false)
  const loadVersion = (version: number, mode: string) => {
    const docId = useStore.getState().currentDocId
    if (!docId || versionLoading) return
    setVersionLoading(true)
    const mdMode = mode === 'polished' ? 'polished' : ''
    getMarkdown(docId, mdMode || undefined, version > 0 ? version : undefined).then(md => {
      if (!md || docId !== useStore.getState().currentDocId) return
      if (mode === 'polished') {
        setCurrentPolishedContent(md.content)
      } else {
        setCurrentContent(md.content)
      }
      setMarkdownMeta({ id: md.id, style: md.style, mode: md.mode, version: md.version })
      setMarkdownVersions(md.versions || [])
    }).catch(err => console.error('加载版本失败:', err))
     .finally(() => setVersionLoading(false))
  }

  // 选中文档时加载内容
  useEffect(() => {
    // 切换文档时清除所有旧内容
    setCurrentContent(null)
    setCurrentPolishedContent(null)
    setCurrentRawText(null)
    setMarkdownMeta(null)
    setMarkdownVersions([])
    setContentLoadError(false)
    useStore.getState().setCurrentFactCheckContent(null)
    useStore.getState().setFactCheckMeta(null)
    if (!currentDocId) return
    const capturedId = currentDocId
    const isSame = () => capturedId === useStore.getState().currentDocId
    // recoverable 错误（"尚未生成"等）是预期状态，静默处理
    const isRecoverable = (err: unknown) => err instanceof ApiErrorException && err.apiError.recoverable
    const handleLoadError = (label: string, err: unknown) => {
      if (!isRecoverable(err)) { console.error(`${label}:`, err); if (isSame()) setContentLoadError(true) }
    }
    // 预加载 rawText（已无 base64，体积小，不阻塞）
    getTranscript(capturedId).then(tx => {
      if (tx && isSame()) setCurrentRawText(tx.rawText ?? '')
    }).catch(err => { handleLoadError('加载转录失败', err) })
    getMarkdown(capturedId).then(md => {
      if (!isSame()) return
      if (md) {
        setCurrentContent(md.content)
        setMarkdownMeta({ id: md.id, style: md.style, mode: md.mode, version: md.version })
        setMarkdownVersions(md.versions || [])
      } else {
        setCurrentContent(null)
        setMarkdownMeta(null)
        setMarkdownVersions([])
      }
    }).catch(err => { handleLoadError('加载笔记失败', err) })
    getMarkdown(capturedId, 'polished').then(md => {
      if (isSame()) setCurrentPolishedContent(md && md.content ? md.content : null)
    }).catch(err => { handleLoadError('加载整理版原文失败', err) })
    getMarkdown(capturedId, 'fact_check').then(md => {
      if (!isSame()) return
      if (md?.content) {
        useStore.getState().setCurrentFactCheckContent(md.content)
        useStore.getState().setFactCheckMeta({
          id: md.id,
          verified_count: (md as any).verified_count,
          disputed_count: (md as any).disputed_count,
          unverifiable_count: (md as any).unverifiable_count,
          overall_score: (md as any).overall_score,
          source_mode: (md as any).source_mode,
          source_version: (md as any).source_version,
          verified_at: (md as any).verified_at,
        })
      }
    }).catch(err => { handleLoadError('加载事实验证失败', err) })
    // 文档/视频/PDF — 若无笔记则自动显示转录
    Promise.all([
      getMarkdown(capturedId),
      getMarkdown(capturedId, 'polished'),
    ]).then(([notes, polished]) => {
      if (!isSame()) return
      const hasContent = notes || polished?.content
      const st = useStore.getState()
      if (!hasContent && ['document', 'pdf', 'docx', 'pptx', 'xlsx', 'epub', 'video'].includes(st.docs.find(d => d.id === capturedId)?.sourceType || '')) {
        const sourceType = st.docs.find(d => d.id === capturedId)?.sourceType
        setActiveView(sourceType === 'epub' ? 'skeleton' : 'transcript')
      }
    }).catch(err => { handleLoadError('加载文档内容失败', err) })
    // 加载已持久化的处理结果
    fetchProcessResults(capturedId).then(results => {
      if (results.length > 0 && isSame()) {
        loadImageResults(capturedId, results.map(r => ({
          mode: r.mode, text: r.content, engine: r.engine, id: r.id,
        })))
      }
    }).catch(err => console.error('加载处理结果失败:', err))
  }, [currentDocId])

  const sourceType = currentDoc?.sourceType || 'webpage'
  const typeMeta = getTypeMeta(sourceType)
  const tabs = typeMeta.tabs

  // 切换文档时，如果当前 activeView 不在新文档的 Tab 列表中，回退到第一个 Tab
  useEffect(() => {
    const validKeys = tabs.map(t => t.key)
    if (!validKeys.includes(activeView)) {
      setActiveView(validKeys[0])
    }
  }, [currentDocId, sourceType])

  // 当前显示的内容
  // 流式生成中 → 优先显示流式内容；否则显示已保存内容
  const displayContent = isStreaming && streamingContent != null
    ? streamingContent
    : activeView === 'polished' ? currentPolishedContent : currentContent
  const processingTasks = useStore(s => s.processingTasks)
  const importTask = Object.entries(processingTasks).find(([k, v]) => k.startsWith('__import__') && v.kind === 'import')
  // key 格式为 `${docId}__${kind}`，查找当前文档的任一活跃任务
  const activeTask = currentDocId
    ? (Object.entries(processingTasks).find(([k]) => k.startsWith(currentDocId + '__'))?.[1] || null)
    : (importTask?.[1] || null)

  /** 触发事实验证 */
  const handleFactCheck = (docId: string) => {
    if (!docId) return
    const isSame = () => docId === useStore.getState().currentDocId
    const st = useStore.getState()
    st.setFactChecking(true)
    st.setProcessing(docId + '__fact_check', { kind: 'fact_check', title: st.docs.find(d => d.id === docId)?.title || '' })
    st.setCurrentFactCheckContent(null)
    st.setFactCheckProgress(null)
    streamFactCheck(docId, {
      model_id: loadGenPrefs().selectedModelId || undefined,
    }, {
      onChunk(text, phase) {
        if (!isSame()) return
        if (phase === 'report') {
          const prev = useStore.getState().currentFactCheckContent || ''
          useStore.getState().setCurrentFactCheckContent(prev + text)
        } else if (phase === 'verify') {
          const prev = useStore.getState().factCheckProgress?.text || ''
          useStore.getState().setFactCheckProgress({ phase: 'verify', text: prev + text })
        }
      },
      onDone(result) {
        if (!isSame()) return
        useStore.getState().setFactChecking(false)
        useStore.getState().setProcessing(docId + '__fact_check', null)
        useStore.getState().setFactCheckProgress(null)
        if (result) {
          useStore.getState().setFactCheckMeta({
            id: result.id || '',
            verified_count: result.verified_count,
            disputed_count: result.disputed_count,
            unverifiable_count: result.unverifiable_count,
            overall_score: result.overall_score,
            source_mode: (result as any).source_mode,
            source_version: (result as any).source_version,
            verified_at: new Date().toISOString(),
          })
        }
        getMarkdown(docId, 'fact_check').then(md => {
          if (isSame() && md?.content) useStore.getState().setCurrentFactCheckContent(md.content)
        })
        useStore.getState().addToast('事实验证完成', 'success')
      },
      onError(msg) {
        if (!isSame()) return
        useStore.getState().setFactChecking(false)
        useStore.getState().setProcessing(docId + '__fact_check', null)
        useStore.getState().addToast(msg, 'error')
      },
    })
  }

  /** 判断某个 view tab 是否正在处理中 */
  const isViewProcessing = (view: string) => {
    if (!activeTask) return false
    // import / retranscribe 是文档级别，影响所有 tab
    if (activeTask.kind === 'import' || activeTask.kind === 'retranscribe') return true
    // generate 影响笔记/分析报告/数据摘要
    if (activeTask.kind === 'generate') return view === 'summary' || view === 'polished' || view === 'report' || view === 'data_preview'
    if (activeTask.kind === 'fact_check') return view === 'fact_check'
    // process-image 只影响对应 mode 的 tab
    if (activeTask.kind === 'process') return view === activeTask.mode
    return false
  }

  /** 处理进度占位组件（从 renderContent 中提出来，避免每次渲染重建组件类型） */
  const processingPlaceholder = useMemo(() => {
    const task = activeTask
    if (!task) {
      return <div className="flex flex-col items-center justify-center h-64 gap-3">
        <Loader2 className="animate-spin text-accent" size={28} />
        <p className="text-[14px] font-medium text-foreground">处理中...</p>
      </div>
    }
    const modeLabels: Record<string, string> = { extract: '提取文字', table: '表格数据', digitize: '文档数字化', understand: '理解内容' }
    const labels: Record<string, string> = {
      'generate': '正在生成...',
      'process': `处理中 — ${modeLabels[task.mode || ''] || task.mode || ''}`,
      'retranscribe': '重新转录中...',
      'import': '导入中...',
    }
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <Loader2 className="animate-spin text-accent" size={28} />
        <p className="text-[14px] font-medium text-foreground">{labels[task.kind] || '处理中...'}</p>
        <p className="text-[12px] text-disabled">请耐心等待，AI 处理中...</p>
      </div>
    )
  }, [activeTask])

  const renderContent = () => {
    if (!currentDocId || !currentDoc) {
      return (
        <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
          <Package size={50} className="text-disabled mb-6 mx-auto opacity-20" />
          <h1 className="text-[30px] font-semibold mb-1">欢迎使用 LiMo</h1>
          <p className="text-[13px] text-disabled mb-8">把任何来源变成可读、可搜索的文字</p>
          <div className="markdown-body max-w-[480px] mx-auto text-left">
            <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
              {`## 快速开始\n\n1. **导入内容** — 粘贴网页链接、B站/YouTube 视频链接，或拖入 PDF/Office 文档\n2. **自动转录** — 系统会自动提取正文、转写音频、识别画面\n3. **生成 Markdown** — 在左侧生成面板选择风格，一键生成\n\n---\n\n> 把任何来源变成可读、可搜索的文字`}
            </ReactMarkdown>
          </div>
        </div>
      )
    }
    // VideoBanner props 在 5 个 case 中完全一致，提取复用
    const videoBanner = <VideoBanner title={currentDoc.title} coverUrl={currentDoc.coverUrl}
      sourceUrl={currentDoc.sourceUrl} sourceType={currentDoc.sourceType} createdAt={currentDoc.createdAt} />

    switch (activeView) {
      // Excel 数据预览 — 直接用 Markdown 渲染表格
      case 'data_preview':
        if (isViewProcessing(activeView) && !isStreaming) return processingPlaceholder
        if (!currentRawText) {
          return (
            <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
              <p className="text-[15px] text-dimmed mb-3">数据预览不可用</p>
              <p className="text-[13px] text-disabled">请重新转录后再试</p>
            </div>
          )
        }
        return <TranscriptView rawText={currentRawText} />
      // Excel 分析报告 — polishedContent
      case 'report':
        if (isViewProcessing(activeView) && !isStreaming) return processingPlaceholder
        if (!currentPolishedContent) {
          return (
            <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
              <p className="text-[15px] text-dimmed mb-3">尚未生成</p>
              <p className="text-[13px] text-disabled mb-6">点击左侧「生成分析报告」按钮</p>
            </div>
          )
        }
        return <>
          {videoBanner}
          <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={currentPolishedContent} meta={markdownMeta} />
        </>
      case 'polished':
      case 'summary':
        if (isViewProcessing(activeView) && !isStreaming) return processingPlaceholder
        if (typeMeta.isExcel) {
          // Excel: summary = 数据摘要
          if (!currentContent) {
            return (
              <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
                <p className="text-[15px] text-dimmed mb-3">尚未生成</p>
                <p className="text-[13px] text-disabled mb-6">点击左侧「生成分析报告」按钮</p>
              </div>
            )
          }
          return <>
            {videoBanner}
            <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={currentContent} meta={markdownMeta} />
          </>
        }
        if (!displayContent) {
          if (isStreaming) return processingPlaceholder
          if (contentLoadError) {
            return (
              <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
                <p className="text-[15px] text-warning mb-3">⚠️ 内容加载失败</p>
                <p className="text-[13px] text-disabled mb-4">请检查后端是否正常运行，然后刷新页面重试</p>
                <button onClick={() => window.location.reload()} className="px-4 py-1.5 bg-accent text-white text-[12px] rounded-full hover:bg-accent-hover">刷新页面</button>
              </div>
            )
          }
          return (
            <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
              <p className="text-[15px] text-dimmed mb-3">尚未生成</p>
              <p className="text-[13px] text-disabled mb-6">点击左侧「生成」按钮为本文档生成 Markdown 笔记</p>
            </div>
          )
        }
        return <>
          <PartialBanner versions={markdownVersions} isStreaming={isStreaming} />
          {videoBanner}
          <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={displayContent} meta={markdownMeta} />
        </>

      case 'ocr':
        if (isViewProcessing(activeView)) return processingPlaceholder
        // 图片默认 OCR 文字（尚未处理时显示引导）
        return (
          <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
            <Image size={50} className="text-disabled mb-6 mx-auto opacity-20" />
            <h2 className="text-[18px] font-semibold mb-2">{currentDoc.title}</h2>
            <p className="text-[13px] text-disabled mb-3">图片已导入，选择一个处理模式开始提取</p>
            <div className="flex items-center justify-center gap-3 text-[12px] text-muted">
              <span className="flex items-center gap-1"><ScanText size={14} /> 提取文字</span>
              <span className="flex items-center gap-1"><Table size={14} /> 表格数据</span>
              <span className="flex items-center gap-1"><ScanLine size={14} /> 文档数字化</span>
              <span className="flex items-center gap-1"><Eye size={14} /> 理解内容</span>
            </div>
          </div>
        )

      case 'original':
        if (isViewProcessing(activeView)) return processingPlaceholder
        // 非图片类型不应该看到此 tab（切换文档时 activeView 可能滞后一帧）
        if (!typeMeta.isImage) return null
        return (
          <div className="flex-1 flex items-center justify-center bg-surface-hover p-6">
            <Zoom>
              <img
                src={apiUrl(`/files/${currentDocId}/original.png`)}
                alt={currentDoc?.title || '原图'}
                className="max-w-full max-h-[calc(100vh-140px)] object-contain rounded-xl shadow-lg cursor-zoom-in"
              />
            </Zoom>
          </div>
        )

      case 'extract':
      case 'table':
      case 'digitize':
      case 'understand': {
        if (!typeMeta.isImage) return null
        const modeLabels: Record<string, string> = { extract: '提取文字', table: '表格数据', digitize: '文档数字化', understand: '理解内容' }
        const result = imageResults[currentDocId || '']?.[activeView]
        // 全部用 Markdown 渲染：VL 模型可能在任意模式下输出表格/公式

        if (isViewProcessing(activeView)) return processingPlaceholder
        if (!result) {
          const modeMeta: Record<string, { icon: React.ReactNode; desc: string }> = {
            extract: { icon: <ScanText size={40} />, desc: '提取图片中的全部文字，保留段落结构' },
            table: { icon: <Table size={40} />, desc: '优先识别表格数据，输出 Markdown 表格' },
            digitize: { icon: <ScanLine size={40} />, desc: '将文档扫描件转为可编辑文本' },
            understand: { icon: <Eye size={40} />, desc: '描述并解读图片中的视觉信息' },
          }
          const meta = modeMeta[activeView] || { icon: '📄', desc: '' }
          return (
            <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
              <div className="text-5xl mb-6 opacity-20">{meta.icon}</div>
              <h2 className="text-[18px] font-semibold mb-2">{modeLabels[activeView] || activeView}</h2>
              <p className="text-[13px] text-disabled mb-3">{meta.desc}</p>
              <p className="text-[12px] text-disabled">点击左侧处理卡片开始</p>
            </div>
          )
        }

        return (
          <div className="max-w-[720px] mx-auto px-10 py-8">
            <VideoBanner
              title={currentDoc?.title}
              coverUrl={currentDoc?.coverUrl}
              sourceType={currentDoc?.sourceType}
              createdAt={currentDoc?.createdAt}
            />
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-[20px] font-semibold">{currentDoc?.title}</h2>
              <span className="text-[12px] text-success font-medium">
                {result.engine} · {modeLabels[activeView] || activeView}
              </span>
            </div>
            <div className="text-[12px] text-disabled mb-4 flex items-center gap-3">
              <span>{modeLabels[activeView]}</span>
              {currentDoc?.createdAt && (
                <><span>·</span><span>{new Date(currentDoc.createdAt).toLocaleDateString('zh-CN')}</span></>
              )}
            </div>
            <div className="markdown-body">
              <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={imageMarkdownComponents}>
                {result.text}
              </ReactMarkdown>
            </div>
          </div>
        )
      }

      case 'skeleton':
        if (isViewProcessing(activeView)) return processingPlaceholder
        if (currentRawText === null) return <p className="text-[13px] text-disabled p-8">加载中...</p>
        return (
          <EpubSkeletonPanel
            rawText={currentRawText}
            transcriptId={currentDocId || ''}
          />
        )

      case 'transcript':
        if (isViewProcessing(activeView)) return processingPlaceholder
        return (
          <div className="max-w-[720px] mx-auto px-10 py-8">
            {videoBanner}
            {currentRawText ? (
              <>
                <TranscriptView rawText={currentRawText} />
                {/* H3: 转录质量指示 */}
                {(() => {
                  const len = currentRawText.length
                  const warnStart = currentRawText.startsWith('[转录失败') || currentRawText === '[转录结果为空]'
                  const shortThreshold = 500
                  const isShort = !warnStart && len < shortThreshold && currentDoc?.sourceType === 'pdf'
                  if (warnStart) {
                    return <p className="mt-3 text-[11px] text-error bg-error/10 rounded-lg px-3 py-2 flex items-center gap-1"><AlertTriangle size={12} /> 转录失败 — 请尝试在左侧「生成选项」中切换引擎后点「重新转录」</p>
                  }
                  if (isShort) {
                    return <p className="mt-3 text-[11px] text-warning bg-warning/10 rounded-lg px-3 py-2 flex items-center gap-1"><Lightbulb size={12} /> 转录仅 {len} 字符，对 PDF/Document 来说偏短。建议尝试切换为「扫描件（逐页 OCR）」引擎后点「重新转录」</p>
                  }
                  return <p className="mt-3 text-[11px] text-disabled">{len.toLocaleString()} 字符</p>
                })()}
              </>
            ) : (
              <p className="text-[13px] text-disabled">加载中...</p>
            )}
          </div>
        )

      case 'fact_check': {
        // 验证中且无报告：优先展示进度（先于 isViewProcessing 判断）
        if (factChecking && !currentFactCheckContent && factCheckProgress?.text) {
          return (
            <div className="max-w-[720px] mx-auto px-10 py-8">
              <p className="text-[14px] font-medium text-dimmed mb-4">正在验证...</p>
              <pre className="text-[12px] text-muted whitespace-pre-wrap font-mono">{factCheckProgress.text}</pre>
            </div>
          )
        }
        if (isViewProcessing(activeView)) return processingPlaceholder
        if (!currentFactCheckContent) {
          return (
            <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
              <p className="text-[15px] text-dimmed mb-3">尚无事实核查报告</p>
              <p className="text-[13px] text-disabled mb-6">
                对生成内容中的关键事实断言进行网络交叉验证，返回可信度标注和来源引用
              </p>
              <button
                onClick={() => handleFactCheck(currentDocId!)}
                disabled={factChecking || !currentDocId}
                className="px-5 py-2.5 bg-accent text-white text-[14px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40"
              >
                {factChecking ? '验证中...' : '开始验证'}
              </button>
            </div>
          )
        }
        // 过期检测：验证源版本 < 该模式最新版本
        const sourceMode = factCheckMeta?.source_mode
        const sourceVer = factCheckMeta?.source_version ?? 0
        const isStale = sourceMode && sourceMode !== 'transcript' && sourceVer > 0
        // simplified: check if markdownVersions has any version > sourceVer
        const hasNewer = markdownVersions.some(v => v.version > sourceVer)
        return (
          <div className="max-w-[720px] mx-auto px-10 py-8">
            {/* 验证上下文条 */}
            {factCheckMeta && (
              <div className="mb-5 p-3 bg-surface rounded-lg border border-border-light">
                <div className="flex items-center gap-2 text-[12px] text-dimmed">
                  <span>验证源: {sourceMode === 'transcript' ? '原始转录' : sourceMode === 'notes' ? '笔记' : sourceMode === 'polished' ? '整理版原文' : sourceMode}</span>
                  {sourceVer > 0 && <span>v{sourceVer}</span>}
                  {factCheckMeta.verified_at && (
                    <span>· {new Date(factCheckMeta.verified_at).toLocaleString('zh-CN', { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' })}</span>
                  )}
                </div>
                {isStale && hasNewer && (
                  <p className="text-[11px] text-warning mt-1">⚠️ 内容已更新，当前验证报告可能过时</p>
                )}
              </div>
            )}
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                {factCheckMeta?.verified_count != null && (
                  <span className="text-[12px] text-dimmed">
                    通过 {factCheckMeta.verified_count} · 争议 {factCheckMeta.disputed_count ?? 0} · 无法验证 {factCheckMeta.unverifiable_count ?? 0}
                  </span>
                )}
              </div>
              <button
                onClick={() => handleFactCheck(currentDocId!)}
                disabled={factChecking}
                className="px-3 py-1.5 text-[12px] text-accent border border-accent/30 rounded-lg hover:bg-accent/5 disabled:opacity-40 transition-colors"
              >
                {factChecking ? '验证中...' : '重新验证'}
              </button>
            </div>
            <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={currentFactCheckContent}
              meta={factCheckMeta
                ? { style: 'detailed', mode: 'fact_check', version: 1 }
                : null} />
          </div>
        )
      }

      case 'mindmap':
        if (isViewProcessing(activeView)) return processingPlaceholder
        if (!currentContent) {
          return (
            <div className="max-w-[720px] mx-auto px-10 py-16 text-center">
              <p className="text-[15px] text-dimmed mb-3">暂无可视化内容</p>
              <p className="text-[13px] text-disabled">点击左侧「生成」按钮为本文档生成 Markdown 笔记后，可在此查看思维导图</p>
            </div>
          )
        }
        return <div id="mindmap" className="relative flex-1 min-h-0"><MindmapView value={currentContent} /></div>

      default:
        return null
    }
  }

  return (
    <>
    <main className="flex-1 flex flex-col overflow-hidden bg-elevated">
      {/* 工具栏 — 对齐 BiliNote MarkdownHeader */}
      <div className="flex items-center justify-between px-6 py-2.5 border-b border-border-light shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 bg-surface rounded-lg p-0.5">
            {tabs.map(t => (
              <button key={t.key}
                onClick={() => setActiveView(t.key)}
                className={`px-3 py-1.5 rounded-md text-[12px] transition-all ${activeView === t.key ? 'bg-elevated font-medium shadow-sm' : 'text-dimmed hover:text-foreground'}`}
              >{t.label}</button>
            ))}
          </div>
          {/* 版本切换器 */}
          {(activeView === 'polished' || activeView === 'summary') && Array.isArray(markdownVersions) && markdownVersions.length > 1 && (
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-disabled">版本</span>
              {versionLoading && <Loader2 size={10} className="animate-spin text-accent" />}
              {markdownVersions.map(v => (
                <button key={v.id}
                  onClick={() => loadVersion(v.version, activeView === 'polished' ? 'polished' : 'notes')}
                  disabled={versionLoading}
                  className={`px-2 py-0.5 rounded text-[11px] font-mono transition-all disabled:opacity-40 ${
                    markdownMeta?.version === v.version
                      ? 'bg-accent text-white'
                      : 'text-muted hover:bg-surface-hover'
                  }`}
                >v{v.version}</button>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <ExportMenu />
          <button
            onClick={() => setShowLogs(!showLogs)}
            className={`px-3 py-1.5 text-[12px] rounded-full transition-colors ${showLogs ? 'bg-accent/10 text-accent' : 'text-dimmed hover:bg-surface-hover'}`}
            title="系统日志"
          ><Terminal size={14} /></button>
          <button
            onClick={() => useStore.getState().toggleAI()}
            className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface-hover rounded-lg"
          ><MessageSquare size={14} /></button>
        </div>
      </div>

      {/* 内容 + TOC */}
      <div className="flex-1 flex overflow-hidden">
        {(activeView === 'polished' || activeView === 'summary') && (
          tocCollapsed ? (
            <div
              onClick={() => setPanelCollapsed('toc', false)}
              className="flex flex-col items-center justify-center shrink-0 bg-elevated/40 hover:bg-elevated/60 transition-colors cursor-pointer border-r border-border-light"
              style={{ width: 28 }}
              title="展开目录"
            >
              <ChevronRight size={14} className="text-muted" />
              <span className="text-[9px] text-disabled mt-1 select-none" style={{ writingMode: 'vertical-rl' }}>目录</span>
            </div>
          ) : (
            <>
              <TOC width={tocWidth} />
              <ResizeHandle onResize={resizeToc} />
            </>
          )
        )}
        <div className={`flex-1 flex flex-col ${activeView === 'mindmap' ? 'overflow-hidden' : 'overflow-y-auto'}`}>
          {renderContent()}
        </div>
      </div>
    </main>
    {showLogs && <LogPanel onClose={() => setShowLogs(false)} />}
    </>
  )
}
