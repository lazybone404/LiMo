import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useStore } from '../../store'
import { streamGenerate, getMarkdown, listEnabledModels, listProviders, ingestURL, ingestText, ingestFile, processImage, saveEngineBindings, retranscribe, getTranscript, getErrorMessage, type EnabledModel, type Provider } from '../../api/client'
import { registerAbort } from '../../api/taskManager'
import { cancelTask } from '../../api/taskManager'
import { cancelEpubInterpret } from '../../api/epub'
import { getStreamBuffer, appendStreamBuffer, setStreamBuffer, deleteStreamBuffer, scheduleFlush, flushImmediate } from '../../store/streamBuffers'
import { onConfigChanged } from '../../lib/configEvents'
import { PanelLeftClose, XCircle, Plus } from 'lucide-react'
import { loadGenPrefs, saveGenPrefs } from './genPrefs'
import { EngineSelect, type EngineOption } from './EngineSelect'
import { ImportSection } from './ImportSection'
import { GenerateOptions } from './GenerateOptions'
import { ImageModeCards } from './ImageModeCards'
import { EpubInterpretPanel } from '../preview/EpubInterpretPanel'
import { EXT_TO_SUBTYPE, getSubScenario, getTypeMeta, normalizeStyle } from './constants'
import { useTaskRunner } from '../../hooks/useTaskRunner'

// 会话级标记：参考文件意图缺失提醒只弹一次（无论确认/取消，用户已理解机制）
// 模块级变量 → 刷新页面清空（与 refFile 仅会话内不持久化一致）
let refNoNotesPrompted = false

const _EMPTY_OBJ: Record<string, any> = {}

export function GenPanel() {
  const caps = useStore(s => s.capabilities)
  const currentDocId = useStore(s => s.currentDocId)
  const processingTasks = useStore(s => s.processingTasks)
  const setCurrentContent = useStore(s => s.setCurrentContent)
  const setCurrentPolishedContent = useStore(s => s.setCurrentPolishedContent)
  const setMarkdownMeta = useStore(s => s.setMarkdownMeta)
  const markdownVersions = useStore(s => s.markdownVersions)
  const setMarkdownVersions = useStore(s => s.setMarkdownVersions)
  const bumpStreamTick = useStore(s => s.bumpStreamTick)
  const clearDocTasks = useStore(s => s.clearDocTasks)
  const imageResults = useStore(s => s.imageResults)
  const setImageResult = useStore(s => s.setImageResult)
  const setActiveView = useStore(s => s.setActiveView)
  const setCurrentRawText = useStore(s => s.setCurrentRawText)
  const setProcessing = useStore(s => s.setProcessing)
  const failTask = useStore(s => s.failTask)
  const addToast = useStore(s => s.addToast)
  const docs = useStore(s => s.docs)
  const currentDoc = docs.find(d => d.id === currentDocId)
  const isVideo = currentDoc?.sourceType === 'video'
  const isEpub = currentDoc?.sourceType === 'epub'

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
  const [formatChart, setFormatChart] = useState(saved.formatChart ?? false)
  const [formatFormulas, setFormatFormulas] = useState(saved.formatFormulas ?? false)
  const [formatReferences, setFormatReferences] = useState(saved.formatReferences ?? false)
  const [formatImages, setFormatImages] = useState(saved.formatImages ?? false)
  const [imageAnalyze, setImageAnalyze] = useState(saved.imageAnalyze ?? false)
  const [imageOptimize, setImageOptimize] = useState(saved.imageOptimize ?? true)
  const [ocrEnabled, setOcrEnabled] = useState(saved.ocrEnabled ?? false)
  const [ocrModelId, setOcrModelId] = useState<number | null>(saved.ocrModelId ?? null)
  const [selectedModelId, setSelectedModelId] = useState<number | null>(saved.selectedModelId)
  const [videoUnderstanding, setVideoUnderstanding] = useState(saved.videoUnderstanding)
  const [videoDirect, setVideoDirect] = useState(saved.videoDirect)
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
  // 参考文件（仅会话内，不持久化；内容已暂存后端，前端只持 id + 元信息）
  const [refFile, setRefFile] = useState<{ id: string; name: string; size: number } | null>(null)
  const [streamDisplay, setStreamDisplay] = useState(saved.streamDisplay ?? true)
  const [imageMode, setImageMode] = useState<'extract' | 'table' | 'digitize' | 'understand'>(
    (['extract', 'table', 'digitize', 'understand'].includes(saved.imageMode) ? saved.imageMode : null) || 'extract'
  )
  const [error, setError] = useState('')

  // ── 从 processingTasks 推导当前文档的活跃任务（per-doc，支持多任务并发）──
  const docTasks = useMemo(() => {
    if (!currentDocId) return []
    return Object.entries(processingTasks)
      .filter(([k]) => k.startsWith(currentDocId + '__'))
      .map(([, v]) => v)
  }, [currentDocId, processingTasks])
  const isGenerating = docTasks.some(t => t.kind === 'generate')
  const isRetranscribing = docTasks.some(t => t.kind === 'retranscribe')
  const isImageProcessing = docTasks.some(t => t.kind === 'process')
  const isImporting = docTasks.some(t => t.kind === 'import')

  // 导入
  const addDoc = useStore(s => s.addDoc)
  const selectDoc = useStore(s => s.selectDoc)
  const [importInput, setImportInput] = useState('')
  const [importError, setImportError] = useState('')
  const [importProgress, setImportProgress] = useState('')
  const [importPercent, setImportPercent] = useState<number | null>(null)
  const activeImportKeyRef = useRef<string | null>(null)  // 当前显示的导入 key
  const MAX_CONCURRENT_IMPORTS = 3
  const MAX_CONCURRENT_GENERATES = 2
  const [inputType, setInputType] = useState<'link' | 'video' | 'text' | 'file' | 'image'>('link')
  const [fileObj, setFileObj] = useState<File | null>(null)
  const [fileSubType, setFileSubType] = useState('pdf')
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
    const names = caps?.fileTypeMap?.[effectiveFileSubType] || []
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
    for (const sk of Object.keys(caps?.scenarioLabels || {})) {
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
    saveGenPrefs({ style, selectedCore, formatToc, formatLink, formatSummary, formatScreenshot, formatChart,
      formatFormulas, formatReferences, formatImages,
      imageAnalyze, imageOptimize,
      selectedModelId, videoUnderstanding, videoDirect, videoInterval, gridCols, gridRows,
      dedupeEnabled, unitWidth, unitHeight, saveQuality, frameQualityFilter,
      imageMode, userNotes, notesExpanded, streamDisplay })
  }, [style, selectedCore, formatToc, formatLink, formatSummary, formatScreenshot, formatChart,
      formatFormulas, formatReferences, formatImages, imageAnalyze, imageOptimize,
      ocrEnabled, ocrModelId,
      selectedModelId,
      videoUnderstanding, videoDirect, videoInterval, gridCols, gridRows, streamDisplay,
      dedupeEnabled, unitWidth, unitHeight, saveQuality, frameQualityFilter, userNotes, imageMode, notesExpanded])

  const [enabledModels, setEnabledModels] = useState<EnabledModel[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const reloadModelLists = useCallback(() => {
    listEnabledModels().then(models => {
      setEnabledModels(models)
      if (saved.selectedModelId && !models.some(m => m.id === saved.selectedModelId)) {
        setSelectedModelId(null)
      }
    }).catch(e => addToast(getErrorMessage(e), 'error'))
    listProviders().then(setProviders).catch(e => addToast(getErrorMessage(e), 'error'))
  }, [saved.selectedModelId])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reloadModelLists()
    // 设置页保存配置后自动刷新（无需手动刷新页面）
    return onConfigChanged(reloadModelLists)
    // 仅在挂载时订阅一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const providerName = (pid: string) => providers.find(p => p.id === pid)?.name || pid
  const hasImageModel = selectedModelId
    ? (enabledModels.find(m => m.id === selectedModelId)?.capabilities?.includes('image') ?? false)
    : enabledModels.some(m => m.capabilities?.includes('image'))
  // 视频直传：选中模型 capabilities 含 video（未选中模型时看任一启用模型，对齐 hasImageModel）
  const hasVideoModel = selectedModelId
    ? (enabledModels.find(m => m.id === selectedModelId)?.capabilities?.includes('video') ?? false)
    : enabledModels.some(m => m.capabilities?.includes('video'))

  // ── 导入进度辅助 ──
  const updateImportProgress = (ev: { phase?: string; status?: string; percent?: number; message?: string }) => {
    setImportProgress(ev.message || ev.phase || '')
    if (ev.percent != null) setImportPercent(ev.percent)
  }

  // ── C2: AbortController 管理（按完整 task key 注册，支持逐任务取消）──
  const importSeqRef = useRef(0)  // 导入计数器，确保每次导入用独立 key
  // 产物级进度计数：per-taskKey Map（并发生成时互不干扰——单 useRef + 每次生成重置会互踩）
  const productChars = useRef<Map<string, { notes: number; polished: number }>>(new Map())
  // 并行双产物取消去重：两路各自发 cancelled 事件，只处理第一次
  const cancelledHandled = useRef(false)
  // 生成结果状态：error（onError 已报过，onDone 不重复报）/ partial（部分失败，
  // onDone 报"部分失败"而非"生成完成"）/ ok（全部成功）
  const genOutcomeRef = useRef<'ok' | 'error' | 'partial'>('ok')
  // 思考链累积：per-taskKey Map（双文档并发生成互不干扰——单 ref + 每次生成重置会
  // 清掉另一文档的思考链，且节流判断产生负数冻结更新）
  const reasoningBuf = useRef<Map<string, { notes: string; polished: string }>>(new Map())

  const createAbort = useCallback((taskKey: string) => {
    const ctrl = new AbortController()
    registerAbort(taskKey, ctrl)
    return ctrl.signal
  }, [])

  const cancelOp = () => {
    const { processingTasks } = useStore.getState()
    // 取消导入任务 — P2-7：只取消当前 active 那条（并发导入各自独立，不误伤其他）
    if (Object.keys(processingTasks).some(k => k.startsWith('__import__')) && activeImportKeyRef.current) {
      cancelTask(activeImportKeyRef.current)
    }
    // 取消当前文档的所有操作 — abort 网络请求 + 清除 state
    if (currentDocId) {
      cancelTask(currentDocId)
      clearDocTasks(currentDocId)
    }
    // epub 一键解读：任务状态在后端（不走 processingTasks/abort），软取消
    if (isEpub && currentDocId) {
      cancelEpubInterpret(currentDocId).catch(() => {})
    }
    setImportProgress(''); setImportPercent(null); setImportError(''); setError('')
    addToast('已取消', 'info')
  }

  // ── C1: 防切文档串数据 —— stale-check 辅助函数 ──
  const isSameDoc = (capturedId: string | null) => capturedId === useStore.getState().currentDocId

  // ── C3: 任务执行公共骨架（retranscribe/process 复用，见 hooks/useTaskRunner.ts）──
  const { runTask } = useTaskRunner()

  // ── Handlers ──
  const handleRetranscribe = () => {
    if (!currentDocId) return
    const capturedId = currentDocId
    void runTask({
      taskKey: capturedId + '__retranscribe',
      taskInfo: { kind: 'retranscribe' },
      prepareAbort: createAbort,
      onStart: () => { setError(''); setImportError('') },
      run: (signal) => retranscribe(capturedId, signal),
      onSuccess: async (result) => {
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
            setMarkdownVersions([...(notes.versions || []), ...(polished?.versions || [])])
          } else {
            setCurrentContent(null)
            setMarkdownMeta(null)
            setMarkdownVersions(polished?.versions || [])
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
      },
      onFail: (msg) => { setError(msg); addToast(msg, 'error') },
    })
  }

  const handleImport = () => {
    // ── 并发上限检查 ──
    const activeImports = Object.keys(useStore.getState().processingTasks)
      .filter(k => k.startsWith('__import__'))
    if (activeImports.length >= MAX_CONCURRENT_IMPORTS) {
      addToast(`同时最多 ${MAX_CONCURRENT_IMPORTS} 个导入任务，请等待完成后再试`, 'info')
      return
    }

    // P1-20：同源防重入——同一来源（文件名/URL/文本）已有导入任务在跑则忽略，
    // 防连按 Enter/连点按钮产生重复任务；不同来源的并发导入仍受 MAX_CONCURRENT_IMPORTS 约束
    const pendingTitle = fileObj?.name || importInput.trim().slice(0, 40) || '导入'
    const sameSourceRunning = Object.values(useStore.getState().processingTasks)
      .some(t => t.kind === 'import' && t.title === pendingTitle)
    if (sameSourceRunning) {
      addToast('该内容正在导入中，请稍候', 'info')
      return
    }

    if (inputType === 'file' || inputType === 'image' || inputType === 'video') {
      const url = importInput.trim()
      if (!fileObj && !url) return
      setImportError(''); setError('')
      const importTitle = fileObj?.name || importInput.slice(0, 40) || '导入'
      const importKey = `__import__${importSeqRef.current++}`
      const taskKey = importKey + '__import'
      activeImportKeyRef.current = importKey
      setProcessing(taskKey, { kind: 'import', title: importTitle })
      const onDone = async (doc: any) => {
        const isDup = doc.status === 'duplicate_url' || doc.status === 'duplicate_content'
        const existing = useStore.getState().docs.find(d => d.id === doc.id)
        if (isDup) {
          if (existing) { selectDoc(doc.id) }
          else { addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || inputType, sourceFileName: doc.sourceFileName || '', sourceUrl: doc.sourceUrl || '', coverUrl: doc.coverUrl || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() }); selectDoc(doc.id) }
          // P2-2：去重提示改 toast——3s 静默小字不可见，toast 明确告知已选中
          addToast(`该内容已导入，已为你选中 · ${doc.title}`, 'info')
          setImportProgress('')
        } else if (!existing) {
          addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || inputType, sourceFileName: doc.sourceFileName || '', sourceUrl: doc.sourceUrl || '', coverUrl: doc.coverUrl || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() })
          selectDoc(doc.id)
          if (inputType !== 'image') setActiveView('transcript')
          addToast(inputType === 'image' ? '导入完成' : '导入完成，点击左侧「生成」按钮生成笔记')
        } else {
          // M10: 已存在但非重复标记 → 直接选中
          selectDoc(doc.id)
          addToast('已存在，已帮你选中')
        }
        /* importing derived from processingTasks */; setFileObj(null)
        if (!isDup) setImportProgress('')
        setProcessing(taskKey, null)
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
      const onError = (err: string) => { setImportError(err); setImportProgress(''); failTask(taskKey, err) }
      if (fileObj) {
        const signal = createAbort(taskKey)
        setImportProgress(inputType === 'video' ? '语音转写中...' : inputType === 'image' ? '导入中...' : '文档解析中...')
        ingestFile(fileObj, ev => updateImportProgress(ev), onDone, onError, signal)
      } else {
        const signal = createAbort(taskKey)
        setImportProgress('下载中...')
        ingestURL(url, ev => updateImportProgress(ev), onDone, onError, signal)
      }
      return
    }
    if (!importInput.trim()) return
    setImportError(''); setError('')
    const text = importInput.trim()
    const importKey = `__import__${importSeqRef.current++}`
    const taskKey = importKey + '__import'
    activeImportKeyRef.current = importKey
    setProcessing(taskKey, { kind: 'import', title: text.slice(0, 40) })
    const signal = createAbort(taskKey)
    const isURL = text.startsWith('http://') || text.startsWith('https://')
    const onDone = (doc: any) => {
      const isDup = doc.status === 'duplicate_url' || doc.status === 'duplicate_content'
      const existing = useStore.getState().docs.find(d => d.id === doc.id)
      if (isDup) {
        if (existing) { selectDoc(doc.id) }
        else { addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || 'webpage', sourceFileName: doc.sourceFileName || '', sourceUrl: doc.sourceUrl || '', coverUrl: doc.coverUrl || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() }); selectDoc(doc.id) }
        setImportProgress(`已导入过，帮你选中了 · ${doc.title}`)
        setTimeout(() => setImportProgress(''), 3000)
      } else if (!existing) {
        addDoc({ id: doc.id, title: doc.title, sourceType: doc.sourceType || 'webpage', sourceFileName: doc.sourceFileName || '', sourceUrl: doc.sourceUrl || '', coverUrl: doc.coverUrl || '', engine: doc.engine || '', language: '', contentHash: '', tags: [], createdAt: new Date().toISOString() })
        selectDoc(doc.id)
        setActiveView('transcript')
        addToast('导入完成')
      } else {
        // M10
        selectDoc(doc.id)
        addToast('已存在，已帮你选中')
      }
      /* importing derived from processingTasks */; setImportInput('')
      if (!isDup) setImportProgress('')
      setProcessing(taskKey, null)
    }
    // C3: 移除误吞错误守卫
    const onError = (err: string) => { setImportError(err); setImportProgress(''); failTask(taskKey, err) }
    if (isURL) { setImportProgress('下载中...'); ingestURL(text, ev => updateImportProgress(ev), onDone, onError, signal) }
    else { setImportProgress('导入中...'); ingestText(text, ev => updateImportProgress(ev), onDone, onError, signal) }
  }

  const handleProcessImage = () => {
    if (!currentDocId) return
    const capturedId = currentDocId
    void runTask({
      taskKey: capturedId + '__process',
      taskInfo: { kind: 'process', mode: imageMode },
      prepareAbort: createAbort,
      onStart: () => { setError(''); setImportError('') },
      run: (signal) => processImage(capturedId, imageMode, selectedModelId ?? undefined, signal),
      onSuccess: (result) => {
        if ('error' in result) { setError(result.error); addToast(result.error, 'error'); return }
        setImageResult(capturedId, imageMode, { text: result.text, engine: result.engine || caps?.engineOptions?.ocr?.[0]?.name || 'ocr', id: result.result_id || '' })
        setActiveView(imageMode)
        // 降级提醒
        if (result.degraded?.length) {
          result.degraded.forEach(note => addToast(`${note}`, 'success'))
        }
        addToast('处理完成')
      },
      onFail: (msg) => { setError(msg); addToast(msg, 'error') },
    })
  }

  // P2-1：continueFrom='partial' → 续写最新残缺产物（后端 continue_from=partial 加载 partial
  // 版本作为 assistant 已有输出从断点续写，完成后合并标记 complete）
  const handleGenerate = (continueFrom: '' | 'partial' = '') => {
    // P1-20：防重入（按钮 disabled 之外的事件路径——快捷键/双击兜底）
    if (isGenerating) return
    if (!currentDocId) { setError('请先选择文档'); return }

    // ── 参考文件意图缺失提醒（会话内只弹一次；确认 → 降级为背景材料）──
    if (refFile && !userNotes.trim() && !refNoNotesPrompted) {
      refNoNotesPrompted = true  // 先标记再弹——无论确认/取消，本次会话不再重复打扰
      const ok = window.confirm(
        '已选择参考文件但未填写备注。建议在备注中说明用途（如：请参考其中的观点），否则参考文件将作为背景材料处理。是否继续？',
      )
      if (!ok) {
        setNotesExpanded(true)
        addToast('请在备注中说明参考文件来源与用途', 'info')
        return
      }
    }

    // ── 编辑中未保存 × 重新生成拦截（评审遗留①）：产物编辑中未保存就重新生成，
    // 产物被覆盖后编辑框还开着旧值，保存会把旧编辑写回新产物——比「覆盖确认」更危险
    // （覆盖确认至少是显式的，此路径旧值静默污染新产物）。轻量桥：PreviewArea 将
    // 编辑态暴露为 dirtyEditingDocId（仅文档 id，不提升编辑态到 store），确认放弃 →
    // 清标记，PreviewArea effect 自关闭编辑态。优先于下方 markdownEdited 覆盖保护。
    const dirtyEditingDocId = useStore.getState().dirtyEditingDocId
    if (dirtyEditingDocId && dirtyEditingDocId === currentDocId) {
      if (!window.confirm('当前文档正在编辑且未保存，重新生成将丢失未保存的修改。确定放弃编辑并重新生成？（可先保存后再生成）')) return
      useStore.getState().setDirtyEditingDocId(null)
    }

    // ── 覆盖保护：重新生成会覆盖已手动编辑的产物 → 两态 confirm（方案 5.2；生成完成后标记自动清除）──
    const editedState = useStore.getState().markdownEdited
    if ((selectedCore.includes('notes') && editedState.notes) ||
        (selectedCore.includes('polished') && editedState.polished)) {
      if (!window.confirm('重新生成将覆盖你手动编辑的内容（生成版自动保留为上一版可回退），继续？')) return
    }

    // ── 全局并发上限检查 ──
    const activeGenerates = Object.keys(useStore.getState().processingTasks)
      .filter(k => k.endsWith('__generate'))
    if (activeGenerates.length >= MAX_CONCURRENT_GENERATES) {
      addToast(`同时最多 ${MAX_CONCURRENT_GENERATES} 个生成任务，请等待完成后再试`, 'info')
      return
    }

    // ── 操作冲突检查（per-doc）──
    if (isRetranscribing) { addToast('当前文档正在转录中，请等待完成后再生成', 'info'); return }
    if (isImageProcessing) { addToast('当前文档正在图片处理中，请等待完成后再生成', 'info'); return }

    const rawText = useStore.getState().currentRawText
    if (!rawText || rawText.startsWith('[转录失败') || rawText === '[转录结果为空]' || rawText === '[无可用转录引擎，请在设置中配置文档引擎]') {
      setError('当前文档无有效转录内容，请先导入或重新转录后再生成')
      return
    }
    const capturedId = currentDocId
    const taskKey = capturedId + '__generate'
    const signal = createAbort(taskKey)
    setError(''); setImportError('')
    productChars.current.set(taskKey, { notes: 0, polished: 0 })  // 新生成从 0 开始计数
    reasoningBuf.current.set(taskKey, { notes: '', polished: '' })  // 思考链清零（per-taskKey）
    cancelledHandled.current = false  // 并行双产物两路 cancelled 事件去重
    genOutcomeRef.current = 'ok'  // 生成结果状态重置
    const doc = useStore.getState().docs.find(d => d.id === capturedId)
    setProcessing(taskKey, { kind: 'generate', title: doc?.title || '' })

    // 初始化流式缓冲区（按产物隔离）
    const notesKey = taskKey + '_notes'
    const polishedKey = taskKey + '_polished'
    setStreamBuffer(notesKey, '')
    setStreamBuffer(polishedKey, '')

    const formatStr = [formatToc && 'toc', formatLink && 'link', formatScreenshot && 'screenshot',
      formatSummary && 'summary', formatChart && 'chart',
      formatFormulas && 'formulas', formatReferences && 'references', formatImages && 'images',
    ].filter(Boolean).filter(f => typeMeta.availableFormats.includes(f as string)).join(',')

    // P1-5：被 availableFormats 静默过滤的格式一次性 toast 提示（不静默丢弃）。
    // 只提示「当前类型 UI 可见」的格式（镜像 GenerateOptions 显示条件）——UI 不可见的
    // 是 genPrefs 跨类型持久化残留（如 PDF 勾过「保留文档图片」→ 切到文本/视频），
    // 该类型本就不支持、用户无法在 UI 上操作，静默忽略即可，避免每次生成都弹骚扰。
    const droppedAll = [formatToc && 'toc', formatLink && 'link', formatScreenshot && 'screenshot',
      formatSummary && 'summary', formatChart && 'chart',
      formatFormulas && 'formulas', formatReferences && 'references', formatImages && 'images',
    ].filter(Boolean).map(String).filter(f => !typeMeta.availableFormats.includes(f))
    if (droppedAll.length > 0) console.warn('[GenPanel] 生成格式被当前类型过滤:', droppedAll, 'sourceType=', typeMeta.sourceType)
    const formatVisible = (f: string): boolean => {
      if (f === 'link' || f === 'screenshot') return typeMeta.isVideo
      if (f === 'formulas' || f === 'references') return typeMeta.isPDF
      if (f === 'images') return typeMeta.hasImages
      return !typeMeta.isExcel && !typeMeta.isImage && typeMeta.sourceType !== 'epub' &&
        (typeMeta.isMedia || typeMeta.sourceType === 'text' || typeMeta.isDoc)
    }
    const droppedFormats = droppedAll.filter(formatVisible)
    if (droppedFormats.length > 0) {
      const fmtNames: Record<string, string> = { toc: '目录', link: '原片跳转', screenshot: '原片截图', summary: 'AI 总结', chart: '图表', formulas: '公式集', references: '参考文献', images: '保留文档图片' }
      addToast(`当前内容类型不支持：${droppedFormats.map(f => fmtNames[f] || f).join('、')}`, 'info')
    }

    const streamMode = selectedCore.join(',')
    const isBoth = selectedCore.includes('notes') && selectedCore.includes('polished')
    const isPolishedOnly = selectedCore.includes('polished') && !selectedCore.includes('notes')
    // 跳转到能实时看到流式的 tab：polished-only → 整理版 tab；否则 → 笔记 tab
    // （此前强制跳 summary——polished-only 用户看不到整理版流式，误以为"一直在加载"）
    setActiveView(isPolishedOnly ? 'polished' : 'summary')

    // 双产物新写：清空两个 tab（chunk 直接 append 到 current）
    if (!isBoth) {
      // 单产物新写：清空对应 tab
      if (isPolishedOnly) setCurrentPolishedContent(null)
      else setCurrentContent(null)
    } else {
      setCurrentContent(null)
      setCurrentPolishedContent(null)
    }

    streamGenerate(capturedId, {
      mode: streamMode, style: normalizeStyle(style),
      formats: formatStr || undefined,
      model_id: selectedModelId || undefined,
      user_notes: userNotes || undefined,
      reference_ids: refFile ? [refFile.id] : undefined,
      continue_from: continueFrom || undefined,
      video_understanding: isVideo ? videoUnderstanding : undefined,
      video_direct: isVideo && videoUnderstanding ? (videoDirect || undefined) : undefined,
      video_interval: isVideo ? videoInterval : undefined,
      grid_cols: isVideo ? gridCols : undefined,
      grid_rows: isVideo ? gridRows : undefined,
      dedupe_enabled: isVideo ? dedupeEnabled : undefined,
      unit_width: isVideo ? unitWidth : undefined,
      unit_height: isVideo ? unitHeight : undefined,
      save_quality: isVideo ? saveQuality : undefined,
      frame_quality_filter: isVideo ? frameQualityFilter : undefined,
      image_analyze: isVideo ? videoUnderstanding : (imageAnalyze || undefined),
      image_optimize: isVideo ? (videoUnderstanding ? imageOptimize : undefined) : (imageAnalyze ? imageOptimize : undefined),
      ocr_enabled: isVideo ? undefined : (ocrEnabled || undefined),
      ocr_model_id: isVideo ? undefined : (ocrEnabled ? ocrModelId ?? undefined : undefined),
    }, {
      onChunk(text, mode) {
        // 1. 无条件写 buffer（key 按 docId 隔离——切走期间流式内容不丢，切回显示完整）
        if (mode === 'polished') appendStreamBuffer(polishedKey, text)
        else if (mode === 'notes') appendStreamBuffer(notesKey, text)
        else appendStreamBuffer(isPolishedOnly ? polishedKey : notesKey, text)
        // 2. 无条件累加进度（节流 ~300 字符更新一次）——保留 `if (cur && ...)` 守卫，
        //    防止已取消任务（task=null）被迟到的 chunk 用 setProcessing 复活
        //    ProcessingPlaceholder 显示「正在生成整理版原文...（已生成 N 字符）」
        if (mode === 'polished' || mode === 'notes') {
          const pc = productChars.current.get(taskKey) || { notes: 0, polished: 0 }
          pc[mode] = (pc[mode] || 0) + (text?.length || 0)
          productChars.current.set(taskKey, pc)
          const cur = useStore.getState().processingTasks[taskKey]
          const curChars = cur?.progress?.[mode] || 0
          if (cur && (cur.mode !== mode || pc[mode] - curChars >= 300)) {
            setProcessing(taskKey, {
              kind: 'generate', mode, title: doc?.title || '',
              progress: { notes: pc.notes || 0, polished: pc.polished || 0 },
              reasoning: reasoningBuf.current.get(taskKey),  // 完整 {notes,polished}（全量替换语义）
            })
          }
        }
        // 3. 仅当前文档才写 store（驱动预览显示）+ flush
        if (!isSameDoc(capturedId)) return
        if (mode === 'polished') {
          if (streamDisplay) setCurrentPolishedContent(getStreamBuffer(polishedKey))
        } else if (mode === 'notes') {
          if (streamDisplay) setCurrentContent(getStreamBuffer(notesKey))
        } else if (streamDisplay) {
          if (isPolishedOnly) setCurrentPolishedContent(getStreamBuffer(polishedKey))
          else setCurrentContent(getStreamBuffer(notesKey))
        }
        // 节流 flush 到 store（驱动 PreviewArea 重读 Map）
        scheduleFlush(() => bumpStreamTick())
      },
      onReasoning(text, mode) {
        // 思考链（deepseek-v4 reasoning_content）——临时展示打消等待焦虑，
        // 正式 content 一开始流式，ProcessingPlaceholder 自动被正式内容替换
        const m = mode === 'polished' ? 'polished' : (mode === 'notes' ? 'notes' : (isPolishedOnly ? 'polished' : 'notes'))
        const rb = reasoningBuf.current.get(taskKey) || { notes: '', polished: '' }
        rb[m] = (rb[m] + text).slice(-2000)
        reasoningBuf.current.set(taskKey, rb)
        const r = rb[m]
        const cur = useStore.getState().processingTasks[taskKey]
        // 节流 ~200 字符更新一次（思考链到达频繁，避免 store 高频快照）。
        // 注意：并行双产物时 cur.mode 可能是另一产物（两路交替 setProcessing），
        // 节流判断只看当前产物的 reasoning 长度差
        if (cur && cur.reasoning?.[m] && r.length - (cur.reasoning?.[m]?.length || 0) < 200) return
        const pc = productChars.current.get(taskKey) || { notes: 0, polished: 0 }
        setProcessing(taskKey, {
          kind: 'generate', mode: m, title: doc?.title || '',
          progress: { notes: pc.notes || 0, polished: pc.polished || 0 },
          reasoning: reasoningBuf.current.get(taskKey),
        })
      },
      onDone(_result) {
        // 立即 flush 最终内容
        flushImmediate(() => bumpStreamTick())
        setProcessing(taskKey, null)
        productChars.current.delete(taskKey)
        reasoningBuf.current.delete(taskKey)
        deleteStreamBuffer(notesKey)
        deleteStreamBuffer(polishedKey)
        if (!isSameDoc(capturedId)) return
        // 刷新版本列表 + 以服务器保存的最终内容为准刷新预览
        // （此前只刷 versions 不刷内容：streamDisplay=false 或流式丢失时产物 tab 永远空白）
        const refreshSaved = (mode: 'notes' | 'polished') => {
          getMarkdown(capturedId, mode).then(md => {
            if (!isSameDoc(capturedId)) return
            // 防竞态：用户已重新生成（processingTasks 重新激活）→ 放弃旧响应，
            // 否则旧产物会覆盖新流式内容、污染版本列表
            if (useStore.getState().processingTasks[taskKey]) return
            // 生成完成 → 新 entry 无 edited → 标记自动清除（方案 5.3）
            useStore.getState().setMarkdownEdited(mode, !!md?.edited)
            if (mode === 'notes') {
              setMarkdownMeta(md ? { id: md.id, style: md.style, mode: md.mode, version: md.version, edited: !!md.edited } : null)
              if (md) {
                setCurrentContent(md.content)
                const prev = useStore.getState().markdownVersions
                setMarkdownVersions([...(md.versions || []), ...prev.filter(v => v.mode !== 'notes')])
              } else { setMarkdownVersions([]) }
            } else if (md) {
              setCurrentPolishedContent(md.content)
              const prev = useStore.getState().markdownVersions
              setMarkdownVersions([...(md.versions || []), ...prev.filter(v => v.mode !== 'polished')])
            }
          })
        }
        refreshSaved('notes')
        refreshSaved('polished')
        // 按结果状态报 Toast——部分失败（polished 超时但 notes 成功）时报
        // "部分失败"而非"生成完成"（此前 warning + done 两个事件 → 双 Toast 矛盾）
        if (genOutcomeRef.current === 'partial') {
          addToast('部分产物生成失败，已完成的部分已保存', 'error')
        } else if (genOutcomeRef.current === 'error') {
          // error 已 Toast，不重复报"生成完成"
        } else {
          addToast('生成完成')
        }
        genOutcomeRef.current = 'ok'
      },
      onError(msg) {
        genOutcomeRef.current = 'error'
        failTask(taskKey, msg)
        productChars.current.delete(taskKey)
        reasoningBuf.current.delete(taskKey)
        deleteStreamBuffer(notesKey)
        deleteStreamBuffer(polishedKey)
        if (!isSameDoc(capturedId)) return
        setError(msg); addToast(msg, 'error')
      },
      onWarning(msg, kind) {
        // 图表未生成（kind=chart_missing，可选组件缺失）：产物本身成功，仅 info 提示
        // 不标 partial——避免 onDone 误报「部分产物生成失败」（协议收紧的语义区分）
        if (kind === 'chart_missing') {
          if (!isSameDoc(capturedId)) return
          addToast(msg, 'info')
          return
        }
        if (genOutcomeRef.current === 'ok') genOutcomeRef.current = 'partial'
        if (!isSameDoc(capturedId)) return
        addToast(msg, 'error')
      },
      onCancelled(savedPartial) {
        // 并行双产物：两路各自发 cancelled 事件 → 只处理第一次（第二次 buffer 已清理）
        if (cancelledHandled.current) return
        cancelledHandled.current = true
        setProcessing(taskKey, null)
        productChars.current.delete(taskKey)
        reasoningBuf.current.delete(taskKey)
        if (!isSameDoc(capturedId)) {
          // 切走时取消：不保留（切回时 effect 从服务器加载），只清理 buffer
          deleteStreamBuffer(notesKey)
          deleteStreamBuffer(polishedKey)
          return
        }
        // 将缓冲区内容写入 store（保留 partial）——双产物也保留
        // （此前 both 模式丢弃 buffer → 取消后产物 tab 空白，尽管后端保存了 partial）
        if (!isBoth) {
          const singleKey = isPolishedOnly ? polishedKey : notesKey
          const buf = getStreamBuffer(singleKey)
          if (isPolishedOnly) setCurrentPolishedContent(buf)
          else setCurrentContent(buf)
        } else {
          const nb = getStreamBuffer(notesKey)
          const pb = getStreamBuffer(polishedKey)
          if (nb) setCurrentContent(nb)
          if (pb) setCurrentPolishedContent(pb)
        }
        deleteStreamBuffer(notesKey)
        deleteStreamBuffer(polishedKey)
        if (savedPartial) {
          addToast('生成已取消，已生成的内容已保留', 'success')
          // P1-21：取消时 notes + polished 都可能已保存 partial——两路都刷新版本
          // （此前只刷 notes，polished 的 partial 不可见；getMarkdown 404 返回 null 不 throw）
          Promise.all([getMarkdown(capturedId, 'notes'), getMarkdown(capturedId, 'polished')]).then(([nmd, pmd]) => {
            if (!isSameDoc(capturedId)) return
            if (useStore.getState().processingTasks[taskKey]) return  // 已重新生成，放弃旧响应
            const fresh = [...(nmd?.versions || []), ...(pmd?.versions || [])]
            if (fresh.length > 0) {
              const prev = useStore.getState().markdownVersions
              setMarkdownVersions([...fresh, ...prev.filter(v => !fresh.some(f => f.mode === v.mode))])
            }
          }).catch(() => {})  // 网络异常忽略，取消态不阻塞 UI
        }
      },
    }, signal)
  }

  // P2-1：监听「继续生成」事件——PreviewArea 的 PartialBanner「继续生成」按钮
  // dispatch limo:continue-generate → 触发 handleGenerate('partial') 续写残缺产物。
  // ref 保存最新 handleGenerate，effect 只绑定一次（避免 deps 里函数每次重建重绑）。
  const handleGenerateRef = useRef(handleGenerate)
  handleGenerateRef.current = handleGenerate
  useEffect(() => {
    const onContinue = () => {
      const st = useStore.getState()
      if (!st.currentDocId || st.processingTasks[st.currentDocId + '__generate']) return
      handleGenerateRef.current('partial')
    }
    window.addEventListener('limo:continue-generate', onContinue)
    return () => window.removeEventListener('limo:continue-generate', onContinue)
  }, [])

  // ── Render ──
  const isBusy = isImporting || isGenerating || isRetranscribing || isImageProcessing
  // 操作互斥（per-doc）：转录/图片处理中 → 禁生成
  const lockOtherOps = isRetranscribing || isImageProcessing
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
          <button onClick={() => useStore.getState().setSlot('genPanel', { collapsed: true })}
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
            importing={isImporting} importError={importError} importProgress={importProgress} importPercent={importPercent}
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
                    const cat = getSubScenario(effectiveFileSubType)
                    return (
                      <EngineSelect category={cat} label="转录方案" options={filteredDocEngines}
                        binding={engineBindings[cat] ?? null} onChange={handleEngineChange} variant="section" />
                    )
                  })()}
                  <button onClick={handleRetranscribe} disabled={isRetranscribing || isGenerating}
                    className="w-full py-2 text-[11px] font-medium text-warning bg-warning/10 rounded-lg hover:bg-warning/20 disabled:opacity-40 transition-colors">
                    {isRetranscribing ? '转录中...' : '⟳ 重新转录'}
                  </button>
                </div>
              )}

              {/* 生成选项 — 仅非图片类型（图片用 ImageModeCards；epub 用一键解读 EpubInterpretPanel） */}
              {!typeMeta.isImage && !isEpub && (
                <GenerateOptions
                  selectedModelId={selectedModelId} setSelectedModelId={setSelectedModelId}
                  enabledModels={enabledModels}
                  providers={providers} providerName={providerName}
                  formatToc={formatToc} setFormatToc={setFormatToc}
                  formatLink={formatLink} setFormatLink={setFormatLink}
                  formatSummary={formatSummary} setFormatSummary={setFormatSummary}
                  formatScreenshot={formatScreenshot} setFormatScreenshot={setFormatScreenshot}
                  formatChart={formatChart} setFormatChart={setFormatChart}
                  formatFormulas={formatFormulas} setFormatFormulas={setFormatFormulas}
                  formatReferences={formatReferences} setFormatReferences={setFormatReferences}
                  formatImages={formatImages} setFormatImages={setFormatImages}
                  imageAnalyze={imageAnalyze} setImageAnalyze={setImageAnalyze}
                  imageOptimize={imageOptimize} setImageOptimize={setImageOptimize}
                  ocrEnabled={ocrEnabled} setOcrEnabled={setOcrEnabled}
                  ocrModelId={ocrModelId} setOcrModelId={setOcrModelId}
                  hasImageModel={hasImageModel}
                  hasVideoModel={hasVideoModel}
                  style={style} setStyle={setStyle} styleKeys={styleKeysWithNone}
                  selectedCore={selectedCore} setSelectedCore={setSelectedCore}
                  videoUnderstanding={videoUnderstanding} setVideoUnderstanding={setVideoUnderstanding}
                  videoDirect={videoDirect} setVideoDirect={setVideoDirect}
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
                  refFile={refFile} setRefFile={setRefFile}
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

              {/* 流式显示开关 — epub 一键解读不走流式 generate，隐藏 */}
              {!typeMeta.isImage && !isEpub && (
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={streamDisplay} onChange={e => setStreamDisplay(e.target.checked)} className="w-4 h-4 accent-accent" />
                  <span className="text-[12px] text-dimmed">实时流式显示（低性能电脑可关闭）</span>
                </label>
              )}

              {/* 操作按钮 — epub 走一键解读（三态：进度条 / 继续 / 一键解读） */}
              {isEpub ? (
                <EpubInterpretPanel docId={currentDocId!} modelId={selectedModelId} />
              ) : typeMeta.isImage ? (
                <button onClick={handleProcessImage} disabled={isImageProcessing || !currentDocId}
                  className="w-full py-2.5 bg-success text-white text-[15px] font-medium rounded-full hover:bg-success/80 active:scale-[0.97] transition-all disabled:opacity-40">
                  {isImageProcessing ? '处理中...' : `处理 — ${({ extract: '提取文字', table: '表格数据', digitize: '文档数字化', understand: '理解内容' })[imageMode]}`}
                </button>
              ) : (
                <div className="flex gap-2">
                  {(() => {
                    const hasComplete = (Array.isArray(markdownVersions) && markdownVersions.some(v => v.status !== 'partial'))
                    if (hasComplete) {
                      return <button onClick={() => handleGenerate()} disabled={isGenerating || lockOtherOps || !currentDocId || selectedCore.length === 0}
                        className="flex-1 py-2.5 bg-accent text-white text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
                        {isGenerating ? '生成中...' : '重新生成'}
                      </button>
                    }
                    return <button onClick={() => handleGenerate()} disabled={isGenerating || lockOtherOps || !currentDocId || selectedCore.length === 0}
                      className="flex-1 py-2.5 bg-accent text-white text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
                      {isGenerating ? '生成中...' : typeMeta.generateLabel}
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

