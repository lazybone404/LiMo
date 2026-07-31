import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../../store'
import { getTranscript, getMarkdown, fetchProcessResults, streamFactCheck, getConfig, apiUrl, ApiErrorException } from '../../api/client'
import { loadGenPrefs } from '../gen-panel/genPrefs'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'
import { getStreamBuffer } from '../../store/streamBuffers'
import { TabRegistry, type TabDef } from '../../lib/tabRegistry'
import { getContentForView } from '../../lib/contentSources'
import { ScanText, Table, ScanLine, Eye, ChevronRight, Loader2, Terminal, Image, MessageSquare, Package, AlertTriangle, FileText, Columns, AlignCenter, Maximize2 } from 'lucide-react'
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
import { EmptyState } from './EmptyState'
import { ProcessingPlaceholder } from './ProcessingPlaceholder'
import { VersionSwitcher } from './VersionSwitcher'
import { TranscriptQualityNote } from './TranscriptQualityNote'
import { ImageResultView } from './ImageResultView'
import { CopyContentBtn } from './CopyContentBtn'
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
  // 订阅 streamTick 以在 flush 时重读 getStreamBuffer（Module Map 非响应式）
  useStore(s => s.streamTick)
  const isStreaming = useStore(s => {
    if (!s.currentDocId) return false
    return !!s.processingTasks[s.currentDocId + '__generate']
  })
  const currentFactCheckContent = useStore(s => s.currentFactCheckContent)
  const factCheckProgress = useStore(s => s.factCheckProgress)
  const contentWidth = useStore(s => s.contentWidth)
  const cycleContentWidth = useStore(s => s.cycleContentWidth)

  const WIDTH_RATIO: Record<string, { label: string; icon: typeof Columns; ratio: number }> = {
    narrow: { label: '紧凑', icon: AlignCenter, ratio: 0.80 },
    normal: { label: '标准', icon: Columns, ratio: 0.90 },
    wide:   { label: '宽屏', icon: Maximize2, ratio: 0.97 },
  }

  // ResizeObserver 追踪预览面板实际宽度
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = useState(800)
  useEffect(() => {
    const el = containerRef.current?.parentElement
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const contentMaxW = `${Math.min(containerWidth * WIDTH_RATIO[contentWidth].ratio, 1100)}px`
  const fontSizeScale: Record<string, number> = { narrow: 0.92, normal: 1.0, wide: 1.06 }
  const factChecking = useStore(s => s.factChecking)
  const setCurrentContent = useStore(s => s.setCurrentContent)
  const setCurrentPolishedContent = useStore(s => s.setCurrentPolishedContent)

  const setCurrentRawText = useStore(s => s.setCurrentRawText)
  const setMarkdownMeta = useStore(s => s.setMarkdownMeta)
  const setMarkdownVersions = useStore(s => s.setMarkdownVersions)
  const docs = useStore(s => s.docs)
  const imageResults = useStore(s => s.imageResults)
  const loadImageResults = useStore(s => s.loadImageResults)
  const tocWidth = useStore(s => s.slots.toc.width)
  const tocCollapsed = useStore(s => s.slots.toc.collapsed)
  const setSlot = useStore(s => s.setSlot)

  const currentDoc = docs.find(d => d.id === currentDocId)
  const sourceUrl = currentDoc?.sourceUrl
  const isVideo = currentDoc?.sourceType === 'video'
  const imageMarkdownComponents = useMemo(() => createMarkdownComponents(sourceUrl, isVideo), [sourceUrl, isVideo])

  // TOC 拖拽回调
  const resizeToc = useCallback((dx: number) => {
    const w = useStore.getState().slots.toc.width
    setSlot('toc', { width: Math.max(60, w + dx) })
  }, [setSlot])

  // 加载指定版本的 markdown
  const [versionLoading, setVersionLoading] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [contentLoadError, setContentLoadError] = useState(false)
  const loadVersion = (version: number, mode: string) => {
    const docId = useStore.getState().currentDocId
    if (!docId || versionLoading) return
    setVersionLoading(true)
    const mdMode = mode === 'polished' ? 'polished' : 'notes'
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
    useStore.getState().setFactCheckProgress(null)  // F5：漏清进度 → A 的审核 spinner 残留在 B
    useStore.getState().setFactChecking(false)      // F5：漏清全局标志（A 的流式仍在跑，其 onDone 会再置 false，无害）
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
    getMarkdown(capturedId, 'notes').then(md => {
      if (!isSame()) return
      // F4：生成中切回（生成快结束时）→ 跳过——此 GET 可能返回保存前的旧版本 V0，
      // 网络慢时会在 onDone 的 refreshSaved(V1) 之后 resolve → V0 覆盖 V1。
      // 跳过安全：流式 buffer 会遮蔽 store 值
      if (useStore.getState().processingTasks[capturedId + '__generate']) return
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
      if (!isSame()) return
      // F4：同上——生成中跳过，防旧版本覆盖 onDone 刷新结果
      if (useStore.getState().processingTasks[capturedId + '__generate']) return
      setCurrentPolishedContent(md && md.content ? md.content : null)
    }).catch(err => { handleLoadError('加载整理版原文失败', err) })
    getMarkdown(capturedId, 'fact_check').then(md => {
      if (!isSame()) return
      // N2：fact_check 流式中切回 → 跳过——否则旧版本 V0 成为 onChunk 的
      // 恢复起点，V0 + 新 chunk 混合错乱
      if (useStore.getState().processingTasks[capturedId + '__fact_check']) return
      if (md?.content) {
        useStore.getState().setCurrentFactCheckContent(md.content)
        useStore.getState().setFactCheckMeta({
          id: md.id,
          verified_count: (md as any).verified_count,
          disputed_count: (md as any).disputed_count,
          unverifiable_count: (md as any).unverifiable_count,
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
      const hasContent = !!(notes || polished?.content)
      const st = useStore.getState().docs.find(d => d.id === capturedId)?.sourceType || ''
      // 已有生成内容时，按文档类型选最佳默认 Tab（由 TabDef.defaultFor 驱动）
      if (hasContent) {
        const defKey = TabRegistry.defaultFor(st)
        if (defKey) setActiveView(defKey)
      } else if (['document', 'pdf', 'docx', 'pptx', 'xlsx', 'epub', 'video'].includes(st)) {
        setActiveView(st === 'epub' ? 'skeleton' : 'transcript')
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

  // 从 TabRegistry 获取当前 sourceType 下的可见 tab 列表
  const tabs = useMemo(() => {
    return TabRegistry.forSourceType(sourceType, {
      currentContent,
      imageResults,
    })
  }, [sourceType, currentContent, imageResults])

  // 当前 activeView 对应的 TabDef
  const activeTabDef: TabDef | undefined = TabRegistry.get(activeView)

  // 切换文档时，如果当前 activeView 不在新文档的 Tab 列表中，回退到第一个 Tab
  useEffect(() => {
    const validKeys = tabs.map(t => t.key)
    if (!validKeys.includes(activeView)) {
      setActiveView(validKeys[0])
    }
  }, [currentDocId, sourceType])

  // 当前显示的内容 — 由 TabDef.contentSource 决定从哪个字段读（lib/contentSources.ts）
  // 流式生成中 → notes/polished 优先读缓冲区（Map 由 streamTick 驱动重渲染）；否则显示已保存内容
  // 注：非 notes/polished tab 不再读 notes 缓冲（原硬编码 bug 语义，fact_check/transcript 等
  // 在生成期间应显示各自内容源——有意的修正，勿"恢复"）
  const displayContent = activeTabDef
    ? getContentForView(activeView, {
        currentDocId, activeView,
        currentContent, currentPolishedContent, currentRawText, currentFactCheckContent,
        imageResults,
        readStreamBuffer: (mode) => {
          if (!isStreaming || !currentDocId) return null
          const b = getStreamBuffer(currentDocId + '__generate_' + mode)
          return b || null    // '' → null，触发 getter 的 ?? 回退到已保存内容
        },
      })
    : currentContent
  const processingTasks = useStore(s => s.processingTasks)
  // key 格式为 `${docId}__${kind}`，收集当前文档所有活跃任务
  const activeTasks = currentDocId
    ? Object.entries(processingTasks)
        .filter(([k]) => k.startsWith(currentDocId + '__'))
        .map(([, v]) => v)
    : (Object.entries(processingTasks)
        .filter(([k, v]) => k.startsWith('__import__') && v.kind === 'import')
        .map(([, v]) => v))

  /** 触发事实验证 */
  const handleFactCheck = (docId: string) => {
    if (!docId) return
    const isSame = () => docId === useStore.getState().currentDocId
    const st = useStore.getState()
    st.setFactChecking(true)
    st.setProcessing(docId + '__fact_check', { kind: 'fact_check', title: st.docs.find(d => d.id === docId)?.title || '' })
    st.setCurrentFactCheckContent('')
    st.setFactCheckProgress(null)

    const appendMarkdown = (s: string) => {
      if (!isSame()) return
      const prev = useStore.getState().currentFactCheckContent || ''
      useStore.getState().setCurrentFactCheckContent(prev + s)
    }

    getConfig().then(cfg => {
      const verifyMode = cfg?.agent_tools?.verify_mode || 'llm'
      streamFactCheck(docId, {
        model_id: loadGenPrefs().selectedModelId || undefined,
        verify_mode: verifyMode,
      }, {
      onChunk(text, phase) {
        if (!isSame()) return
        const prev = useStore.getState().currentFactCheckContent || ''
        if (phase === 'verify') {
          // verify 阶段文本 → blockquote
          useStore.getState().setCurrentFactCheckContent(prev + `> ${text}\n`)
        } else {
          // review 流式报告及其他 → 直接拼接
          useStore.getState().setCurrentFactCheckContent(prev + text)
        }
      },
      onProgress(data) {
        if (!isSame()) return
        const st = useStore.getState()
        const prev = st.factCheckProgress
        // 静态审核完成 → 拼接结构化摘要
        if (data.phase === 'static_review' && data.summary) {
          const s = data.summary
          const issues = data.issues || []
          let md = `\n> 静态审核完成：${s.claims_count} 条断言（${s.facts + s.data} 事实/数据，${s.opinion} 观点）\n`
          if (issues.length > 0) {
            const labels: Record<string, string> = {
              fact_error: '事实错误', data_inaccurate: '数据近似', citation_missing: '缺少引用',
              source_weak: '来源偏弱', reasoning_gap: '推理跳跃', logic_conflict: '逻辑冲突',
              overclaim: '过度确定化', vague_definition: '定义不清', misleading_phrasing: '易误导表述',
              freshness_concern: '时效性存疑',
            }
            md += '> 发现问题：' + issues.map((i: any) =>
              `${labels[i.type] || i.type}${i.action ? '（' + i.action + '）' : ''}`
            ).join('、') + '\n'
          }
          md += '\n---\n\n'
          appendMarkdown(md)
        }
        // 同步更新进度供加载态使用
        st.setFactCheckProgress({
          phase: data.phase || '',
          text: data.text || '',
          summary: data.summary || prev?.summary,
          issues: data.issues || prev?.issues,
        })
      },
      onDone(result) {
        useStore.getState().setFactChecking(false)
        useStore.getState().setProcessing(docId + '__fact_check', null)
        useStore.getState().setFactCheckProgress(null)
        if (!isSame()) return
        if (result) {
          useStore.getState().setFactCheckMeta({
            id: result.id || '',
            verified_count: result.verified_count,
            disputed_count: result.disputed_count,
            unverifiable_count: result.unverifiable_count,
            source_mode: (result as any).source_mode,
            source_version: (result as any).source_version,
            verified_at: new Date().toISOString(),
          })
        }
        if (result?.has_content === false) {
          useStore.getState().addToast('未发现可审核的事实性内容', 'info')
        } else {
          useStore.getState().addToast('AI 审核完成', 'success')
        }
      },
      onError(msg) {
        useStore.getState().setFactChecking(false)
        useStore.getState().setProcessing(docId + '__fact_check', null)
        if (!isSame()) return
        useStore.getState().addToast(msg, 'error')
      },
    })
  })  // getConfig().then(...) close
  }

  /** 判断某个 view tab 是否正在处理中（从 TabDef.processingKinds 推导） */
  const isViewProcessing = (view: string) => {
    const def = TabRegistry.get(view)
    if (!def?.processingKinds) return false
    return activeTasks.some(task => {
      // process kind 需要额外匹配 mode（图片处理 tab）
      if (task.kind === 'process') return def.processingKinds!.includes('process') && view === task.mode
      return def.processingKinds!.includes(task.kind)
    })
  }

  const renderContent = () => {
    if (!currentDocId || !currentDoc) {
      return (
        <div className="mx-auto px-10 py-16 text-center">
          <Package size={50} className="text-disabled mb-6 mx-auto opacity-20" />
          <h1 className="text-[30px] font-semibold mb-1">欢迎使用 LiMo</h1>
          <p className="text-[13px] text-disabled mb-8">在左侧面板导入内容，开始你的知识管理</p>

          <div className="markdown-body max-w-[480px] mx-auto text-left mt-10" data-texture-reset="true">
            <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
              {`## 快速开始\n\n1. **导入内容** — 粘贴网页链接、B站/YouTube 视频链接，或拖入 PDF/Office 文档\n2. **自动转录** — 系统会自动提取正文、转写音频、识别画面\n3. **生成 Markdown** — 在左侧生成面板选择风格，一键生成\n\n---\n\n> 把任何来源变成可读、可搜索的文字`}
            </ReactMarkdown>
          </div>
        </div>
      )
    }
    switch (activeView) {
      // Excel 数据预览 — 直接用 Markdown 渲染表格
      case 'data_preview':
        if (isViewProcessing(activeView) && !isStreaming) return <ProcessingPlaceholder task={activeTasks[0]} />
        if (!currentRawText) {
          return <EmptyState title="数据预览不可用" description="请重新转录后再试" />
        }
        return <TranscriptView rawText={currentRawText} sourceType={currentDoc?.sourceType} />
      case 'polished':
      case 'summary':
        if (isViewProcessing(activeView) && !isStreaming) return <ProcessingPlaceholder task={activeTasks[0]} />
        if (typeMeta.isExcel) {
          // Excel: 仅数据摘要（分析报告已移除）——用 displayContent（含流式 buffer 回退，
          // F6：生成中切走再切回能显示流式内容，而非直读 currentContent 的空态"尚未生成"）
          if (!displayContent) {
            return <EmptyState title="尚未生成" description="点击左侧「生成数据摘要」按钮" />
          }
          return <>
            <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={displayContent} meta={markdownMeta} />
          </>
        }
        if (!displayContent) {
          if (isStreaming) return <ProcessingPlaceholder task={activeTasks[0]} />
          if (contentLoadError) {
            return <EmptyState icon={<AlertTriangle size={16} className="text-warning" />} title="内容加载失败" description="请检查后端是否正常运行，然后刷新页面重试" action={<button onClick={() => window.location.reload()} className="px-4 py-1.5 bg-accent text-white text-[12px] rounded-full hover:bg-accent-hover">刷新页面</button>} />
          }
          return <EmptyState title="尚未生成" description="点击左侧「生成」按钮为本文档生成 Markdown 笔记" />
        }
        return <>
          <PartialBanner versions={markdownVersions} isStreaming={isStreaming} />
          <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={displayContent} meta={markdownMeta} />
        </>

      case 'ocr':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={activeTasks[0]} />
        // 图片默认 OCR 文字（尚未处理时显示引导）
        return (
          <EmptyState
            icon={<Image size={50} className="text-disabled mx-auto opacity-20" />}
            title={<h2 className="text-[18px] font-semibold">{currentDoc.title}</h2>}
            description="图片已导入，选择一个处理模式开始提取"
            action={<div className="flex items-center justify-center gap-3 text-[12px] text-muted">
              <span className="flex items-center gap-1"><ScanText size={14} /> 提取文字</span>
              <span className="flex items-center gap-1"><Table size={14} /> 表格数据</span>
              <span className="flex items-center gap-1"><ScanLine size={14} /> 文档数字化</span>
              <span className="flex items-center gap-1"><Eye size={14} /> 理解内容</span>
            </div>}
          />
        )

      case 'original':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={activeTasks[0]} />
        // 非图片类型不应该看到此 tab（切换文档时 activeView 可能滞后一帧）
        if (!typeMeta.isImage) return null
        return (
          <div className="flex-1 flex items-center justify-center bg-surface-hover p-6">
            <Zoom>
              <img
                src={apiUrl(`/assets/${currentDocId}/original.png`)}
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

        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={activeTasks[0]} />
        if (!result) {
          const modeMeta: Record<string, { icon: React.ReactNode; desc: string }> = {
            extract: { icon: <ScanText size={40} />, desc: '提取图片中的全部文字，保留段落结构' },
            table: { icon: <Table size={40} />, desc: '优先识别表格数据，输出 Markdown 表格' },
            digitize: { icon: <ScanLine size={40} />, desc: '将文档扫描件转为可编辑文本' },
            understand: { icon: <Eye size={40} />, desc: '描述并解读图片中的视觉信息' },
          }
          const meta = modeMeta[activeView] || { icon: <FileText size={40} />, desc: '' }
          return (
            <EmptyState
              icon={<div className="text-5xl opacity-20">{meta.icon}</div>}
              title={<h2 className="text-[18px] font-semibold">{modeLabels[activeView] || activeView}</h2>}
              description={meta.desc}
              hint="点击左侧处理卡片开始"
            />
          )
        }

        return (
          <ImageResultView
            result={result}
            mode={activeView}
            title={currentDoc?.title}
            createdAt={currentDoc?.createdAt}
            markdownComponents={imageMarkdownComponents}
          />
        )
      }

      case 'skeleton':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={activeTasks[0]} />
        if (currentRawText === null) return <p className="text-[13px] text-disabled p-8">加载中...</p>
        return (
          <EpubSkeletonPanel
            rawText={currentRawText}
            transcriptId={currentDocId || ''}
          />
        )

      case 'transcript':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={activeTasks[0]} />
        // 文档类转录（PDF/Word等）→ 统一用 MarkdownPreview 渲染
        if (currentDoc?.sourceType && !['video', 'image', 'webpage', 'text'].includes(currentDoc.sourceType) && currentRawText) {
          let isJson = false
          try { JSON.parse(currentRawText); isJson = true } catch {}
          if (!isJson) {
            return <MarkdownPreview content={currentRawText} meta={markdownMeta} sourceUrl={sourceUrl} isVideo={false} />
          }
        }
        return (
          <div className="mx-auto px-10 py-8">
            {currentRawText ? (
              <>
                <TranscriptView rawText={currentRawText} sourceType={currentDoc?.sourceType} />
                {/* 转录质量指示 */}
                <TranscriptQualityNote rawText={currentRawText} sourceType={currentDoc?.sourceType} />
              </>
            ) : (
              <p className="text-[13px] text-disabled">加载中...</p>
            )}
          </div>
        )

      case 'fact_check': {
        if (currentFactCheckContent) {
          return (
            <div className="mx-auto px-10 py-8">
              <MarkdownPreview sourceUrl={sourceUrl} isVideo={isVideo} content={currentFactCheckContent}
                meta={{ style: 'detailed', mode: 'fact_check', version: 1 }} />
            </div>
          )
        }
        if (factChecking) {
          return (
            <div className="mx-auto px-10 py-16 text-center">
              <Loader2 size={20} className="animate-spin mx-auto mb-3 text-accent" />
              <p className="text-[13px] text-dimmed">{factCheckProgress?.text || '正在准备审核...'}</p>
            </div>
          )
        }
        return (
          <EmptyState
            title="AI 辅助审核"
            description="自动提取事实断言，检查引用完整性和逻辑一致性"
            hint="审核基于 AI 模型知识，不能替代人工事实核查。结果仅供辅助参考。"
            action={<button
              onClick={() => handleFactCheck(currentDocId!)}
              disabled={factChecking || !currentDocId}
              className="px-5 py-2.5 bg-accent text-white text-[14px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40"
            >
              开始审核
            </button>}
          />
        )
      }

      case 'mindmap':
        if (isViewProcessing(activeView)) return <ProcessingPlaceholder task={activeTasks[0]} />
        if (!currentContent) {
          return <EmptyState title="暂无可视化内容" description="点击左侧「生成」按钮为本文档生成 Markdown 笔记后，可在此查看思维导图" />
        }
        return <div id="mindmap" className="relative flex-1 min-h-0"><MindmapView value={currentContent} /></div>

      default:
        return null
    }
  }

  return (
    <>
    <main className="flex-1 flex flex-col overflow-hidden bg-surface rounded-2xl">
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
          {/* 版本切换器 — 由 TabDef.showVersionSwitcher 驱动 */}
          {activeTabDef?.showVersionSwitcher && (
            <VersionSwitcher
              mode={activeTabDef.showVersionSwitcher}
              versions={markdownVersions || []}
              activeMeta={markdownMeta}
              loading={versionLoading || isStreaming}
              onSelect={(v) => loadVersion(v, activeTabDef.showVersionSwitcher!)}
            />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <CopyContentBtn ctx={{
            currentDocId, activeView,
            currentContent, currentPolishedContent, currentRawText, currentFactCheckContent,
            imageResults,
          }} />
          <ExportMenu />
          {/* 阅读宽度切换 */}
          <button
            onClick={cycleContentWidth}
            className="w-7 h-7 flex items-center justify-center text-dimmed hover:bg-surface-hover rounded-lg transition-colors"
            title={`阅读宽度：${WIDTH_RATIO[contentWidth].label}（Ctrl+W）`}
          >
            {contentWidth === 'narrow' ? <AlignCenter size={15} /> : contentWidth === 'wide' ? <Maximize2 size={15} /> : <Columns size={15} />}
          </button>
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
      <div className="preview-area flex-1 flex overflow-hidden">
        {activeTabDef?.showTOC && (
          tocCollapsed ? (
            <div
              onClick={() => setSlot('toc', { collapsed: false })}
              className="flex flex-col items-center justify-center shrink-0 bg-elevated hover:bg-surface-hover transition-colors cursor-pointer border-r border-border-light"
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
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* VideoBanner — 窄条，由 TabDef.showBanner 驱动 */}
          {activeTabDef?.showBanner && currentDoc?.sourceType === 'video' && (
            <VideoBanner title={currentDoc.title}
              sourceUrl={currentDoc.sourceUrl} sourceType={currentDoc.sourceType} />
          )}
          <div
            ref={containerRef}
            className={`preview-content flex-1 flex flex-col ${activeView === 'mindmap' ? 'overflow-hidden' : 'overflow-y-scroll'} ${activeView === 'mindmap' ? '' : 'mx-auto w-full'} ${activeView === 'mindmap' ? '' : contentWidth === 'wide' ? 'px-1 md:px-2 lg:px-4 xl:px-6' : contentWidth === 'normal' ? 'px-3 md:px-6 lg:px-10 xl:px-16' : 'px-4 md:px-12 lg:px-20 xl:px-32'}`}
            style={activeView === 'mindmap' ? {} : { maxWidth: contentMaxW, fontSize: `${fontSizeScale[contentWidth]}em`, transition: 'max-width 350ms ease-out, padding 350ms ease-out, font-size 350ms ease-out' }}
          >
            {renderContent()}
          </div>
        </div>
      </div>
    </main>
    {showLogs && <LogPanel onClose={() => setShowLogs(false)} />}
    </>
  )
}
