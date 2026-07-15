import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useStore } from '../../store'
import { streamGenerate, getMarkdown, listEnabledModels, listProviders, ingestURL, ingestText, ingestFile, processImage, saveEngineBindings, retranscribe, getTranscript, getErrorMessage, type EnabledModel, type Provider } from '../../api/client'
import { registerAbort } from '../../api/taskManager'
import { cancelTask } from '../../api/taskManager'
import { PanelLeftClose, XCircle, Plus } from 'lucide-react'
import { loadGenPrefs, saveGenPrefs } from './genPrefs'
import { EngineSelect, type EngineOption } from './EngineSelect'
import { ImportSection } from './ImportSection'
import { GenerateOptions } from './GenerateOptions'
import { ImageModeCards } from './ImageModeCards'
import { DOC_ENGINE_NAMES, EXT_TO_SUBTYPE, SUB_SCENARIO, getTypeMeta, normalizeStyle } from './constants'

const _EMPTY_OBJ: Record<string, any> = {}

export function GenPanel() {
  const caps = useStore(s => s.capabilities)
  const currentDocId = useStore(s => s.currentDocId)
  const processingTasks = useStore(s => s.processingTasks)
  const generating = useStore(s => s.generating)
  const setGenerating = useStore(s => s.setGenerating)
  const setCurrentContent = useStore(s => s.setCurrentContent)
  const setCurrentPolishedContent = useStore(s => s.setCurrentPolishedContent)
  const setMarkdownMeta = useStore(s => s.setMarkdownMeta)
  const markdownVersions = useStore(s => s.markdownVersions)
  const setMarkdownVersions = useStore(s => s.setMarkdownVersions)
  const currentContent = useStore(s => s.currentContent)
  const currentPolishedContent = useStore(s => s.currentPolishedContent)
  const setIsStreaming = useStore(s => s.setIsStreaming)
  const setStreamingContent = useStore(s => s.setStreamingContent)
  const imageResults = useStore(s => s.imageResults)
  const setImageResult = useStore(s => s.setImageResult)
  const setActiveView = useStore(s => s.setActiveView)
  const setCurrentRawText = useStore(s => s.setCurrentRawText)
  const setProcessing = useStore(s => s.setProcessing)
  const addToast = useStore(s => s.addToast)
  const docs = useStore(s => s.docs)
  const currentDoc = docs.find(d => d.id === currentDocId)
  const isVideo = currentDoc?.sourceType === 'video'

  // ── 从 localStorage 恢复 ──
  const saved = loadGenPrefs()
  const [style, setStyle] = useState(saved.style)
  const [selectedCore, setSelectedCore] = useState<string[]>(
    saved.selectedCore?.length ? saved.selectedCore : ['notes', 'polished']
  )
  const [formatToc, setFormatToc] = useState(saved.formatToc)
  const [formatLink, setFormatLink] = useState(saved.formatLink)
  const [formatSummary, setFormatSummary] = useState(saved.formatSummary)
  const [formatScreenshot, setFormatScreenshot] = useState(saved.formatScreenshot)
  const [formatTranslate, setFormatTranslate] = useState(saved.formatTranslate ?? false)
  const [formatFormulas, setFormatFormulas] = useState(saved.formatFormulas ?? false)
  const [formatReferences, setFormatReferences] = useState(saved.formatReferences ?? false)
  const [formatImages, setFormatImages] = useState(saved.formatImages ?? false)
  const [imageAnalyze, setImageAnalyze] = useState(saved.imageAnalyze ?? false)
  const [imageOptimize, setImageOptimize] = useState(saved.imageOptimize ?? true)
  const [selectedModelId, setSelectedModelId] = useState<number | null>(saved.selectedModelId)
  const [videoUnderstanding, setVideoUnderstanding] = useState(saved.videoUnderstanding)
  const [videoInterval, setVideoInterval] = useState(saved.videoInterval)
  const [gridCols, setGridCols] = useState(saved.gridCols)
  const [gridRows, setGridRows] = useState(saved.gridRows)
  const [dedupeEnabled, setDedupeEnabled] = useState(saved.dedupeEnabled)
  const [unitWidth, setUnitWidth] = useState(saved.unitWidth)
  const [unitHeight, setUnitHeight] = useState(saved.unitHeight)
  const [saveQuality, setSaveQuality] = useState(saved.saveQuality)
  const [frameQualityFilter, setFrameQualityFilter] = useState(saved.frameQualityFilter)
  const [userNotes, setUserNotes] = useState(saved.userNotes)
  const [notesExpanded, setNotesExpanded] = useState(saved.notesExpanded ?? false)
  const [streamDisplay, setStreamDisplay] = useState(saved.streamDisplay ?? true)
  const [imageMode, setImageMode] = useState<'extract' | 'table' | 'digitize' | 'understand'>(
    (['extract', 'table', 'digitize', 'understand'].includes(saved.imageMode) ? saved.imageMode : null) || 'extract'
  )
  const [error, setError] = useState('')
  const [imageProcessing, setImageProcessing] = useState(false)
  const [retranscribing, setRetranscribing] = useState(false)

  // 导入
  const addDoc = useStore(s => s.addDoc)
  const selectDoc = useStore(s => s.selectDoc)
  const [importInput, setImportInput] = useState('')
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')
  const [importProgress, setImportProgress] = useState('')
  const [importPercent, setImportPercent] = useState<number | null>(null)
  const [inputType, setInputType] = useState<'link' | 'video' | 'text' | 'file' | 'image'>('link')
  const [fileObj, setFileObj] = useState<File | null>(null)
  const [fileSubType, setFileSubType] = useState<'pdf' | 'word' | 'ppt' | 'excel' | 'epub'>('pdf')
  const [preOcr, setPreOcr] = useState(false)  // 导入图片时同时提取文字

  // ── 引擎配置 ──
  const setCapabilities = useStore(s => s.setCapabilities)
  const engineOptions: Record<string, EngineOption[]> = caps?.engineOptions || {}
  const engineBindings: Record<string, number | null> = caps?.engineBindings || {}
  const handleEngineChange = async (category: string, modelId: number | null) => {
    const updated = { ...engineBindings, [category]: modelId }
    if (caps) setCapabilities({ ...caps, engineBindings: updated })
    await saveEngineBindings(updated)
  }

  // ── 文档引擎过滤 ──
  const effectiveFileSubType = (() => {
    if (!currentDoc) return fileSubType
    const name = currentDoc?.sourceFileName || ''
    const ext = '.' + (name.split('.').pop() || '').toLowerCase()
    return EXT_TO_SUBTYPE[ext] || 'pdf'
  })()
  const filteredDocEngines = (() => {
    const all = engineOptions.doc || []
    const names = DOC_ENGINE_NAMES[effectiveFileSubType] || []
    return names.length ? all.filter(e => names.includes(e.name)) : all
  })()

  // ── 计算属性 ──
  const effectiveType = currentDoc?.sourceType || inputType
  const typeMeta = getTypeMeta(effectiveType, caps?.genOptions)

  const styleKeys = (typeMeta.isExcel
    ? caps?.excelStyles || ['detailed', 'minimal', 'business']
    : typeMeta.isDoc
    ? caps?.docStyles || ['imrad', 'detailed', 'business', 'tutorial']
    : caps?.styles || ['minimal', 'detailed', 'academic', 'life']
  )
  const styleKeysWithNone = ['none', ...styleKeys]

  // ── OCR 引擎模式 ──
  const ocrEngines = caps?.engines?.ocr || _EMPTY_OBJ
  const ocrSupportedModes = useMemo(
    () => new Set<string>(Object.values(ocrEngines).flatMap((e: { supported_modes: string[] }) => e.supported_modes)),
    [ocrEngines]
  )
  const ocrModesReady = Object.keys(ocrEngines).length > 0

  // ── Effects ──

  useEffect(() => {
    if (!ocrModesReady) return
    if (!ocrSupportedModes.has(imageMode)) {
      const first = (['extract', 'table', 'digitize', 'understand'] as const).find(m => ocrSupportedModes.has(m))
      if (first) setImageMode(first)
    }
  }, [ocrModesReady, ocrSupportedModes, imageMode])

  // 切换文档类型时，确保 selectedCore 匹配当前类型可用的产物
  useEffect(() => {
    const validNames = new Set(typeMeta.coreProducts.map(p => p.name))
    if (validNames.size === 0) return  // 图片类型无产物
    // 当前选中产物不属于此类型的可选列表 → 重置为全部可选
    if (!selectedCore.some(n => validNames.has(n))) {
      setSelectedCore(typeMeta.coreProducts.map(p => p.name))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeMeta.coreProducts])

  useEffect(() => {
    if (!fileObj) return
    const ext = '.' + (fileObj.name.split('.').pop() || '').toLowerCase()
    const matched = EXT_TO_SUBTYPE[ext]
    if (matched === 'pdf' || matched === 'word' || matched === 'ppt' || matched === 'excel' || matched === 'epub') {
      setFileSubType(matched)
    }
  }, [fileObj])

  // 引擎默认绑定：页面加载时自动填充空绑定，用户无需手动选择
  useEffect(() => {
    if (!caps?.engineBindings || !caps?.engineOptions) return
    const bindings = { ...caps.engineBindings }
    let changed = false
    const docOptions = caps.engineOptions['doc'] || []
    // 所有需要检查的 key：已有的 + doc 子场景（即使后端还没返回）
    const allKeys = new Set(Object.keys(bindings))
    for (const sk of ['doc:text+image','doc:text-only','doc:scan-ocr','doc:word','doc:ppt','doc:excel','doc:epub']) {
      allKeys.add(sk)
    }
    for (const cat of allKeys) {
      if (bindings[cat] != null) continue
      if (cat === 'asr' || cat === 'ocr' || cat === 'doc') {
        const opts = caps.engineOptions[cat]
        if (opts?.length) { bindings[cat] = opts[0].id; changed = true }
      } else if (cat.startsWith('doc:') && docOptions.length) {
        bindings[cat] = docOptions[0].id; changed = true
      }
    }
    if (changed) {
      setCapabilities({ ...caps, engineBindings: bindings })
      saveEngineBindings(bindings).catch(() => {})
    }
  }, [caps?.engineBindings, caps?.engineOptions])

  // 持久化
  useEffect(() => {
    saveGenPrefs({ style, selectedCore, formatToc, formatLink, formatSummary, formatScreenshot,
      formatTranslate, formatFormulas, formatReferences, formatImages,
      imageAnalyze, imageOptimize,
      selectedModelId, videoUnderstanding, videoInterval, gridCols, gridRows,
      dedupeEnabled, unitWidth, unitHeight, saveQuality, frameQualityFilter,
      imageMode, userNotes, notesExpanded, streamDisplay })
  }, [style, selectedCore, formatToc, formatLink, formatSummary, formatScreenshot,
      formatTranslate, formatFormulas, formatReferences, formatImages, imageAnalyze, imageOptimize, selectedModelId,
      videoUnderstanding, videoInterval, gridCols, gridRows, streamDisplay,
      dedupeEnabled, unitWidth, unitHeight, saveQuality, frameQualityFilter, userNotes, imageMode, notesExpanded])

  const [enabledModels, setEnabledModels] = useState<EnabledModel[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  useEffect(() => {
    listEnabledModels().then(models => {
      setEnabledModels(models)
      if (saved.selectedModelId && !models.some(m => m.id === saved.selectedModelId)) {
        setSelectedModelId(null)
      }
    }).catch(e => addToast(getErrorMessage(e), 'error'))
    listProviders().then(setProviders).catch(e => addToast(getErrorMessage(e), 'error'))
    // 仅在挂载时运行一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const providerName = (pid: string) => providers.find(p => p.id === pid)?.name || pid
  const hasImageModel = selectedModelId
    ? (enabledModels.find(m => m.id === selectedModelId)?.capabilities?.includes('image') ?? false)
    : enabledModels.some(m => m.capabilities?.includes('image'))

  // ── 导入进度辅助 ──
  const updateImportProgress = (ev: { phase?: string; status?: string; percent?: number; message?: string }) => {
    setImportProgress(ev.message || ev.phase || '')
    if (ev.percent != null) setImportPercent(ev.percent)
  }

  // ── C2: AbortController 管理（用 taskManager 注册，TaskPanel 可取消）──
  const importSeqRef = useRef(0)  // 导入计数器，确保每次导入用独立 key
  // 流式内容累积缓冲区 — streamDisplay=false 时 UI 不更新但数据不丢
  const accNotesRef = useRef('')
  const accPolishedRef = useRef('')
  const accSingleRef = useRef('')

  const createAbort = useCallback((docId: string) => {
    const ctrl = new AbortController()
    registerAbort(docId, ctrl)
    return ctrl.signal
  }, [])

  // TaskPanel 外部取消 → 同步重置本地 busy 状态
  useEffect(() => {
    if (currentDocId && !Object.keys(processingTasks).some(k => k.startsWith(currentDocId + '__'))) {
      setGenerating(false); setRetranscribing(false); setImageProcessing(false)
      setIsStreaming(false)
    }
    if (!Object.keys(processingTasks).some(k => k.startsWith('__import__'))) {
      setImporting(false); setImportProgress(''); setImportPercent(null)
    }
    // setGenerating/setIsStreaming 等是稳定的 useState setter，无需列入 deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processingTasks, currentDocId])

  const cancelOp = () => {
    const { clearDocTasks, processingTasks } = useStore.getState()
    // 取消导入任务 — 控制器注册 key 固定为 '__import__'
    if (Object.keys(processingTasks).some(k => k.startsWith('__import__'))) {
      cancelTask('__import__')
    }
    // 取消当前文档的操作 — abort 网络请求
    if (currentDocId) cancelTask(currentDocId)
    // 清除当前文档的所有任务 state
    if (currentDocId) clearDocTasks(currentDocId)
    setImporting(false); setRetranscribing(false); setGenerating(false); setImageProcessing(false)
    setImportProgress(''); setImportPercent(null); setImportError(''); setError('')
    addToast('已取消', 'info')
  }

  // ── C1: 防切文档串数据 —— stale-check 辅助函数 ──
  const isSameDoc = (capturedId: string | null) => capturedId === useStore.getState().currentDocId

  // ── Handlers ──
  const handleRetranscribe = async () => {
    if (!currentDocId) return
    const capturedId = currentDocId
    const signal = createAbort(capturedId)
    setRetranscribing(true); setError(''); setImportError('')
    setProcessing(capturedId + '__retranscribe', { kind: 'retranscribe' })
    try {
      const result = await retranscribe(capturedId, signal)
      if (!isSameDoc(capturedId)) return
      if ('error' in result) { setError(result.error); addToast(result.error, 'error'); return }
      const tx = await getTranscript(capturedId)
      if (!isSameDoc(capturedId)) return
      if (tx) {
        setCurrentRawText(tx.rawText)
        // 重新加载已有的笔记产物（重新转录不影响已生成的 markdown）
        const [notes, polished] = await Promise.all([
          getMarkdown(capturedId),
          getMarkdown(capturedId, 'polished'),
        ])
        if (!isSameDoc(capturedId)) return
        if (notes) {
          setCurrentContent(notes.content)
          setMarkdownMeta({ id: notes.id, style: notes.style, mode: notes.mode, version: notes.version })
          setMarkdownVersions(notes.versions || [])
        } else {
          setCurrentContent(null)
          setMarkdownMeta(null)
          setMarkdownVersions([])
        }
        if (polished && polished.content) {
          setCurrentPolishedContent(polished.content)
        } else {
          setCurrentPolishedContent(null)
        }
        // 有产物就显示笔记，没有就显示转录
        setActiveView(notes ? 'summary' : 'transcript')
      }
      addToast('重新转录完成')
    } catch (e: unknown) {
      if ((e instanceof Error) && e.name === 'AbortError') return
      if (!isSameDoc(capturedId)) return
      const msg = getErrorMessage(e)
      setError(msg); addToast(msg, 'error')
    } finally {
      if (isSameDoc(capturedId)) { setRetranscribing(false); setProcessing(capturedId + '__retranscribe', null) }
    }
  }

  const handleImport = () => {
    if (inputType === 'file' || inputType === 'image' || inputType === 'video') {
      const url = importInput.trim()
      if (!fileObj && !url) return
      setImportError(''); setError(''); setImporting(true)
      const importTitle = fileObj?.name || importInput.slice(0, 40) || '导入'
      const importKey = `__import__${importSeqRef.current++}`
      setProcessing(importKey + '__import', { kind: 'import', title: importTitle })
      const onDone = async (doc: any) => {
        const isDup = doc.status === 'duplicate_url' || doc.status === 'duplicate_content'
        const existing = useStore.getState().docs.find(d => d.id === doc.id)
        if (isDup) {
          if (existing) { selectDoc(doc.id) }
          else { addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || inputType, sourceFileName: doc.sourceFileName || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() }); selectDoc(doc.id) }
          setImportProgress(`已导入过，帮你选中了 · ${doc.title}`)
          setTimeout(() => setImportProgress(''), 3000)
        } else if (!existing) {
          addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || inputType, sourceFileName: doc.sourceFileName || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() })
          selectDoc(doc.id)
          if (inputType !== 'image') setActiveView('transcript')
          addToast('导入完成')
        } else {
          // M10: 已存在但非重复标记 → 直接选中
          selectDoc(doc.id)
          addToast('已存在，已帮你选中')
        }
        setImporting(false); setFileObj(null)
        if (!isDup) setImportProgress('')
        setProcessing(importKey + '__import', null)
        if (!isDup && preOcr && inputType === 'image' && doc.id) {
          setProcessing(doc.id + '__process', { kind: 'process', mode: 'extract' })
          try {
            const result = await processImage(doc.id, 'extract')
            if (!('error' in result)) {
              setImageResult(doc.id, 'extract', { text: result.text, engine: result.engine || '', id: result.result_id || '' })
              setActiveView('extract')
            }
          } catch { addToast('导入完成，但文字提取失败，可稍后在左侧手动处理', 'error') }
            finally { setProcessing(doc.id + '__process', null) }
        }
      }
      // C3: 移除误吞错误守卫，始终重置状态
      const onError = (err: string) => { setImportError(err); setImporting(false); setImportProgress(''); setProcessing(importKey + '__import', null) }
      if (fileObj) {
        const signal = createAbort('__import__')
        setImportProgress(inputType === 'video' ? '语音转写中...' : inputType === 'image' ? '导入中...' : '文档解析中...')
        ingestFile(fileObj, ev => updateImportProgress(ev), onDone, onError, signal)
      } else {
        const signal = createAbort('__import__')
        setImportProgress('下载中...')
        ingestURL(url, ev => updateImportProgress(ev), onDone, onError, signal)
      }
      return
    }
    if (!importInput.trim()) return
    setImportError(''); setError(''); setImporting(true)
    const signal = createAbort('__import__')
    const importKey = `__import__${importSeqRef.current++}`
    const text = importInput.trim()
    const isURL = text.startsWith('http://') || text.startsWith('https://')
    const onDone = (doc: any) => {
      const isDup = doc.status === 'duplicate_url' || doc.status === 'duplicate_content'
      const existing = useStore.getState().docs.find(d => d.id === doc.id)
      if (isDup) {
        if (existing) { selectDoc(doc.id) }
        else { addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || 'webpage', sourceFileName: doc.sourceFileName || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() }); selectDoc(doc.id) }
        setImportProgress(`已导入过，帮你选中了 · ${doc.title}`)
        setTimeout(() => setImportProgress(''), 3000)
      } else if (!existing) {
        addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || 'webpage', sourceFileName: doc.sourceFileName || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() })
        selectDoc(doc.id)
        setActiveView('transcript')
        addToast('导入完成')
      } else {
        // M10
        selectDoc(doc.id)
        addToast('已存在，已帮你选中')
      }
      setImporting(false); setImportInput('')
      if (!isDup) setImportProgress('')
      setProcessing(importKey + '__import', null)
    }
    // C3: 移除误吞错误守卫
    const onError = (err: string) => { setImportError(err); setImporting(false); setImportProgress(''); setProcessing(importKey + '__import', null) }
    if (isURL) { setImportProgress('下载中...'); ingestURL(text, ev => updateImportProgress(ev), onDone, onError, signal) }
    else { setImportProgress('导入中...'); ingestText(text, ev => updateImportProgress(ev), onDone, onError, signal) }
  }

  const handleProcessImage = async () => {
    if (!currentDocId) return
    const capturedId = currentDocId
    const signal = createAbort(capturedId)
    setImageProcessing(true); setError(''); setImportError('')
    setProcessing(capturedId + '__process', { kind: 'process', mode: imageMode })
    try {
      const result = await processImage(capturedId, imageMode, selectedModelId ?? undefined, signal)
      if (!isSameDoc(capturedId)) return
      if ('error' in result) { setError(result.error); addToast(result.error, 'error'); return }
      setImageResult(capturedId, imageMode, { text: result.text, engine: result.engine || 'dashscope-ocr', id: result.result_id || '' })
      setActiveView(imageMode)
      // 降级提醒
      if (result.degraded?.length) {
        result.degraded.forEach(note => addToast(`⚠️ ${note}`, 'success'))
      }
      addToast('处理完成')
    } catch (e: unknown) {
      if ((e instanceof Error) && e.name === 'AbortError') return
      if (!isSameDoc(capturedId)) return
      const msg = getErrorMessage(e)
      setError(msg); addToast(msg, 'error')
    } finally {
      if (isSameDoc(capturedId)) { setImageProcessing(false); setProcessing(capturedId + '__process', null) }
    }
  }

  const handleGenerate = (continueFrom?: string) => {
    if (!currentDocId) { setError('请先选择文档'); return }
    const rawText = useStore.getState().currentRawText
    if (!rawText || rawText.startsWith('[转录失败') || rawText === '[转录结果为空]' || rawText === '[无可用转录引擎，请在设置中配置文档引擎]') {
      setError('当前文档无有效转录内容，请先导入或重新转录后再生成')
      return
    }
    const capturedId = currentDocId
    const signal = createAbort(capturedId)
    setError(''); setImportError(''); setGenerating(true)
    const doc = useStore.getState().docs.find(d => d.id === capturedId)
    setProcessing(capturedId + '__generate', { kind: 'generate', title: doc?.title || '' })
    accNotesRef.current = ''; accPolishedRef.current = ''; accSingleRef.current = ''
    setIsStreaming(true)
    setActiveView('summary')

    const formatStr = [formatToc && 'toc', formatLink && 'link', formatScreenshot && 'screenshot',
      formatSummary && 'summary', formatTranslate && 'translate',
      formatFormulas && 'formulas', formatReferences && 'references', formatImages && 'images',
    ].filter(Boolean).join(',')

    // mode = 勾选的产物列表，逗号分隔传给后端
    const streamMode = selectedCore.join(',')

    const isBoth = selectedCore.includes('notes') && selectedCore.includes('polished')
    const isPolishedOnly = selectedCore.includes('polished') && !selectedCore.includes('notes')

    // 新写/重写：设置流式缓冲区；续写：从残缺内容开始
    if (continueFrom) {
      // 续写：单产物从残缺内容起跑；双产物不清空（partial 已在 current 中）
      if (!isBoth) {
        setStreamingContent((isPolishedOnly ? currentPolishedContent : currentContent) || '')
      }
    } else {
      if (selectedCore.includes('notes') && selectedCore.includes('polished')) {
        // 双产物新写：清空两个 tab（chunk 直接 append 到 current）
        setCurrentContent(null)
        setCurrentPolishedContent(null)
        useStore.getState().setCurrentFactCheckContent(null)
      } else {
        // 单产物新写：用 streamingContent 缓冲，不清旧内容（等完成再替换）
        setStreamingContent('')
      }
    }

    streamGenerate(capturedId, {
      mode: streamMode, style: normalizeStyle(style),
      formats: formatStr || undefined,
      model_id: selectedModelId || undefined,
      user_notes: userNotes || undefined,
      video_understanding: isVideo ? videoUnderstanding : undefined,
      video_interval: isVideo ? videoInterval : undefined,
      grid_cols: isVideo ? gridCols : undefined,
      grid_rows: isVideo ? gridRows : undefined,
      dedupe_enabled: isVideo ? dedupeEnabled : undefined,
      unit_width: isVideo ? unitWidth : undefined,
      unit_height: isVideo ? unitHeight : undefined,
      save_quality: isVideo ? saveQuality : undefined,
      frame_quality_filter: isVideo ? frameQualityFilter : undefined,
      // 视频：AI 画面理解开启 = 直接发给 LLM；文档：formatImages 勾选后才可选发送
      image_analyze: isVideo ? videoUnderstanding : (formatImages ? imageAnalyze : undefined),
      // 图片预处理：视频从 VL 参数取，文档从嵌套选项取
      image_optimize: isVideo ? (videoUnderstanding ? imageOptimize : undefined) : ((formatImages && imageAnalyze) ? imageOptimize : undefined),
      continue_from: continueFrom || undefined,
    }, {
      onChunk(text, mode) {
        if (!isSameDoc(capturedId)) return
        // 始终累积到内部缓冲区（streamDisplay=false 时 UI 不更新但数据不丢）
        if (mode === 'polished') {
          accPolishedRef.current += text
          if (streamDisplay) setCurrentPolishedContent(accPolishedRef.current)
        } else if (mode === 'notes') {
          accNotesRef.current += text
          if (streamDisplay) setCurrentContent(accNotesRef.current)
        } else {
          accSingleRef.current += text
          if (streamDisplay) setStreamingContent(accSingleRef.current)
        }
      },
      onDone(_result) {
        if (!isSameDoc(capturedId)) return
        setGenerating(false); setProcessing(capturedId + '__generate', null)
        setIsStreaming(false)
        // 内容整理 — 从内部缓冲区读取，streamDisplay=false 时也不会丢
        if (!isBoth) {
          const hasCore = selectedCore.includes('notes') || selectedCore.includes('polished')
          if (hasCore) {
            const finalContent = accSingleRef.current || useStore.getState().streamingContent || ''
            if (isPolishedOnly) {
              setCurrentPolishedContent(finalContent)
            } else {
              setCurrentContent(finalContent)
            }
          }
          setStreamingContent(null)
        } else {
          // 双产物模式：确保最终内容从缓冲区写入 store
          if (!streamDisplay) {
            if (accPolishedRef.current) setCurrentPolishedContent(accPolishedRef.current)
            if (accNotesRef.current) setCurrentContent(accNotesRef.current)
          }
          setStreamingContent(null)
        }
        // 刷新版本列表
        getMarkdown(capturedId, 'notes').then(md => {
          if (isSameDoc(capturedId)) {
            setMarkdownMeta(md ? { id: md.id, style: md.style, mode: md.mode, version: md.version } : null)
            setMarkdownVersions(md?.versions || [])
          }
        })
        getMarkdown(capturedId, 'polished').then(md => {
          if (isSameDoc(capturedId) && md) {
            const prev = useStore.getState().markdownVersions
            const existing = prev.filter(v => v.id !== md.id)
            setMarkdownVersions([...existing, ...(md.versions || [])])
          }
        })
        addToast(continueFrom ? '续写完成' : '生成完成')
      },
      onError(msg) {
        if (!isSameDoc(capturedId)) return
        setError(msg); addToast(msg, 'error')
        setGenerating(false); setProcessing(capturedId + '__generate', null)
        setIsStreaming(false)
      },
      onCancelled(savedPartial) {
        if (!isSameDoc(capturedId)) return
        setGenerating(false); setProcessing(capturedId + '__generate', null)
        setIsStreaming(false)
        // 单产物模式：把已生成的 buffer 存到最终内容中
        if (!isBoth) {
          const finalContent = useStore.getState().streamingContent
          const hasCore = selectedCore.includes('notes') || selectedCore.includes('polished')
          if (hasCore) {
            if (isPolishedOnly) {
              setCurrentPolishedContent(finalContent)
            } else {
              setCurrentContent(finalContent)
            }
          }
          setStreamingContent(null)
        }
        if (savedPartial) {
          addToast('生成已取消，残缺内容已保留，可稍后继续生成', 'success')
          getMarkdown(capturedId, 'notes').then(md => {
            if (isSameDoc(capturedId)) setMarkdownVersions(md?.versions || [])
          })
          getMarkdown(capturedId, 'polished').then(md => {
            if (isSameDoc(capturedId) && md) {
              const prev2 = useStore.getState().markdownVersions
              const existing2 = prev2.filter(v => v.id !== md.id)
              setMarkdownVersions([...existing2, ...(md.versions || [])])
            }
          })
        }
      },
    }, signal)
  }

  // ── Render ──
  const isBusy = importing || generating || retranscribing || imageProcessing
  // 操作互斥：转录/图片处理中 → 禁生成；反向（生成 → 禁转录）见下方按钮 disabled 中的 generating 条件
  const lockOtherOps = retranscribing || imageProcessing
  return (
    <aside className="w-full border-r border-border-light flex flex-col shrink-0 bg-elevated/60 backdrop-blur-[20px] overflow-y-auto overflow-x-hidden h-full">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-light shrink-0">
        <span className="text-[12px] font-medium text-dimmed truncate">{currentDoc ? '生成选项' : '导入'}</span>
        <div className="flex items-center gap-1">
          {isBusy && (
            <button onClick={cancelOp}
              className="flex items-center gap-1 px-2 py-1 text-[11px] text-error hover:bg-error/10 rounded-full transition-colors">
              <XCircle size={12} />取消
            </button>
          )}
          <button onClick={() => useStore.getState().setPanelCollapsed('genPanel', true)}
            className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center"
            title="收起面板"
          ><PanelLeftClose size={14} className="text-muted" /></button>
        </div>
      </div>

      <div className="px-3 py-5 space-y-4">
        {/* M2: capabilities 加载中 → 引擎区骨架 */}
        {!caps && (
          <div className="space-y-2 animate-pulse">
            <div className="h-9 bg-surface-hover rounded-lg" />
            <div className="h-9 bg-surface-hover rounded-lg w-3/4" />
          </div>
        )}
        {/* ── 导入区（无选中文档时完整展示，选中文档后折叠） ── */}
        {currentDoc ? (
          <div className="border-b border-border-light pb-3">
            <button
              onClick={() => selectDoc('')}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-[10px] bg-surface text-[12px] text-muted hover:text-dimmed hover:bg-surface-hover transition-colors"
            >
              <Plus size={14} />
              <span>导入新内容</span>
            </button>
          </div>
        ) : (
          <ImportSection
            inputType={inputType} setInputType={setInputType}
            fileObj={fileObj} setFileObj={setFileObj}
            fileSubType={fileSubType} setFileSubType={setFileSubType}
            importInput={importInput} setImportInput={setImportInput}
            importing={importing} importError={importError} importProgress={importProgress} importPercent={importPercent}
            engineOptions={engineOptions} engineBindings={engineBindings}
            handleEngineChange={handleEngineChange} handleImport={handleImport}
            isImageType={inputType === 'image'}
            preOcr={preOcr} setPreOcr={setPreOcr}
          />
        )}

        {/* ── 选中文档 → 生成区 ── */}
        {currentDoc && (
          <>
            {/* 当前文档 */}
            <div className="pt-3 border-t border-border-light space-y-3">
              <div className="px-3 py-2 rounded-[10px] bg-surface text-[12px]">
                <span className="text-disabled">当前文档: </span>
                <span className="font-medium text-foreground truncate">{currentDoc.title}</span>
              </div>

              {/* 重新转录 — 视频/文档有引擎选择，网页/文本仅重提取 */}
              {(currentDoc.sourceType === 'video' || typeMeta.isDoc || currentDoc.sourceType === 'webpage' || currentDoc.sourceType === 'text') && (
                <div className="space-y-2">
                  {currentDoc.sourceType === 'video' && engineOptions.asr?.length > 0 && (
                    <EngineSelect category="asr" label="语音转写" options={engineOptions.asr}
                      binding={engineBindings.asr ?? null} onChange={handleEngineChange} variant="section" />
                  )}
                  {typeMeta.isDoc && filteredDocEngines.length > 0 && (() => {
                    const cat = SUB_SCENARIO[effectiveFileSubType] || 'doc:word'
                    return (
                      <EngineSelect category={cat} label="转录方案" options={filteredDocEngines}
                        binding={engineBindings[cat] ?? null} onChange={handleEngineChange} variant="section" />
                    )
                  })()}
                  <button onClick={handleRetranscribe} disabled={retranscribing || generating}
                    className="w-full py-2 text-[11px] font-medium text-warning bg-warning/10 rounded-lg hover:bg-warning/20 disabled:opacity-40 transition-colors">
                    {retranscribing ? '转录中...' : '⟳ 重新转录'}
                  </button>
                </div>
              )}

              {/* 生成选项 — 仅非图片类型（图片用下面的 ImageModeCards） */}
              {!typeMeta.isImage && (
                <GenerateOptions
                  selectedModelId={selectedModelId} setSelectedModelId={setSelectedModelId}
                  enabledModels={enabledModels}
                  providers={providers} providerName={providerName}
                  formatToc={formatToc} setFormatToc={setFormatToc}
                  formatLink={formatLink} setFormatLink={setFormatLink}
                  formatSummary={formatSummary} setFormatSummary={setFormatSummary}
                  formatScreenshot={formatScreenshot} setFormatScreenshot={setFormatScreenshot}
                  formatTranslate={formatTranslate} setFormatTranslate={setFormatTranslate}
                  formatFormulas={formatFormulas} setFormatFormulas={setFormatFormulas}
                  formatReferences={formatReferences} setFormatReferences={setFormatReferences}
                  formatImages={formatImages} setFormatImages={setFormatImages}
                  imageAnalyze={imageAnalyze} setImageAnalyze={setImageAnalyze}
                  imageOptimize={imageOptimize} setImageOptimize={setImageOptimize}
                  hasImageModel={hasImageModel}
                  style={style} setStyle={setStyle} styleKeys={styleKeysWithNone}
                  selectedCore={selectedCore} setSelectedCore={setSelectedCore}
                  videoUnderstanding={videoUnderstanding} setVideoUnderstanding={setVideoUnderstanding}
                  videoInterval={videoInterval} setVideoInterval={setVideoInterval}
                  gridCols={gridCols} setGridCols={setGridCols}
                  gridRows={gridRows} setGridRows={setGridRows}
                  dedupeEnabled={dedupeEnabled} setDedupeEnabled={setDedupeEnabled}
                  unitWidth={unitWidth} setUnitWidth={setUnitWidth}
                  unitHeight={unitHeight} setUnitHeight={setUnitHeight}
                  saveQuality={saveQuality} setSaveQuality={setSaveQuality}
                  frameQualityFilter={frameQualityFilter} setFrameQualityFilter={setFrameQualityFilter}
                  userNotes={userNotes} setUserNotes={setUserNotes}
                  notesExpanded={notesExpanded} setNotesExpanded={setNotesExpanded}
                  typeMeta={typeMeta}
                />
              )}

              {/* 图片处理 — 模型选择 + 模式卡片 */}
              {typeMeta.isImage && (
                <>
                  <div>
                    <span className="text-[12px] font-medium text-dimmed mb-2 block">模型</span>
                    <select value={selectedModelId || ''} onChange={e => setSelectedModelId(e.target.value ? Number(e.target.value) : null)}
                      className="w-full px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] outline-none">
                      <option value="">默认</option>
                      {enabledModels.filter(m => m.capabilities?.includes('image')).map(m => (
                        <option key={m.id} value={m.id}>{m.model_name} · {providerName(m.provider_id)}</option>
                      ))}
                    </select>
                  </div>
                  <ImageModeCards
                    imageMode={imageMode} setImageMode={setImageMode}
                    ocrSupportedModes={ocrSupportedModes} ocrModesReady={ocrModesReady}
                    imageResults={imageResults} currentDocId={currentDocId}
                    setActiveView={setActiveView}
                  />
                </>
              )}

              {/* 错误提示 */}
              {error && <div className="px-3 py-2 rounded-[10px] bg-error/10 text-[12px] text-error border border-error/30">{error}</div>}

              {/* 流式显示开关 */}
              {!typeMeta.isImage && (
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={streamDisplay} onChange={e => setStreamDisplay(e.target.checked)} className="w-4 h-4 accent-accent" />
                  <span className="text-[12px] text-dimmed">实时流式显示（低性能电脑可关闭）</span>
                </label>
              )}

              {/* 操作按钮 */}
              {typeMeta.isImage ? (
                <button onClick={handleProcessImage} disabled={imageProcessing || !currentDocId}
                  className="w-full py-2.5 bg-success text-white text-[15px] font-medium rounded-full hover:bg-success/80 active:scale-[0.97] transition-all disabled:opacity-40">
                  {imageProcessing ? '处理中...' : `处理 — ${({ extract: '提取文字', table: '表格数据', digitize: '文档数字化', understand: '理解内容' })[imageMode]}`}
                </button>
              ) : (
                <div className="flex gap-2">
                  {(() => {
                    const hasPartial = (Array.isArray(markdownVersions) && markdownVersions.some(v => v.status === 'partial'))
                    const hasComplete = (Array.isArray(markdownVersions) && markdownVersions.some(v => v.status !== 'partial'))
                    if (hasPartial) {
                      return <>
                        <button onClick={() => handleGenerate('partial')} disabled={generating || lockOtherOps || !currentDocId || selectedCore.length === 0}
                          className="flex-1 py-2.5 bg-accent text-white text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
                          {generating ? '生成中...' : '继续生成'}
                        </button>
                        <button onClick={() => handleGenerate()} disabled={generating || lockOtherOps || !currentDocId || selectedCore.length === 0}
                          className="px-4 py-2.5 text-dimmed text-[13px] font-medium rounded-lg hover:bg-surface-hover active:scale-[0.97] transition-all disabled:opacity-40 border border-border-default">
                          重新生成
                        </button>
                      </>
                    }
                    if (hasComplete) {
                      return <button onClick={() => handleGenerate()} disabled={generating || lockOtherOps || !currentDocId || selectedCore.length === 0}
                        className="flex-1 py-2.5 bg-accent text-white text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
                        {generating ? '生成中...' : '重新生成'}
                      </button>
                    }
                    return <button onClick={() => handleGenerate()} disabled={generating || lockOtherOps || !currentDocId || selectedCore.length === 0}
                      className="flex-1 py-2.5 bg-accent text-white text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
                      {generating ? '生成中...' : typeMeta.generateLabel}
                    </button>
                  })()}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </aside>
  )
}

