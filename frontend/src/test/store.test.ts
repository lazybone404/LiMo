import { describe, it, expect, beforeEach } from 'vitest'
import { useStore } from '../store'

// Reset store to clean state before each test
beforeEach(() => {
  useStore.setState({
    docs: [], currentDocId: null, searchQuery: '', activeTag: '',
    currentContent: null, currentPolishedContent: null, currentRawText: null,
    markdownMeta: null, markdownVersions: [],
    currentFactCheckContent: null, factCheckMeta: null, factCheckProgress: null,
    streamTick: 0,
    importProgress: null, importStages: [],
    imageResults: {}, processingTasks: {}, taskPanelOpen: false,
    generating: false, factChecking: false,
    aiFloating: false,
    toasts: [],
  })
})

// ── Import progress merging ──

describe('setImportProgress — import progress merging', () => {
  it('adds a new stage for a new phase', () => {
    useStore.getState().setImportProgress({ phase: 'download', status: 'done', message: 'Done' })
    const stages = useStore.getState().importStages
    expect(stages).toHaveLength(1)
    expect(stages[0].phase).toBe('download')
    expect(stages[0].status).toBe('done')
    expect(stages[0].message).toBe('Done')
  })

  it('updates existing stage when phase matches', () => {
    const { setImportProgress } = useStore.getState()
    setImportProgress({ phase: 'extract', status: 'running', message: 'Working...' })
    setImportProgress({ phase: 'extract', status: 'done', message: 'Done!' })
    const stages = useStore.getState().importStages
    expect(stages).toHaveLength(1) // still 1, not 2
    expect(stages[0].status).toBe('done')
    expect(stages[0].message).toBe('Done!')
  })

  it('accumulates multiple different phases', () => {
    const { setImportProgress } = useStore.getState()
    setImportProgress({ phase: 'download', status: 'done', message: 'Downloaded' })
    setImportProgress({ phase: 'extract', status: 'done', message: 'Extracted' })
    setImportProgress({ phase: 'transcribe', status: 'running' })
    expect(useStore.getState().importStages).toHaveLength(3)
  })

  it('clears stages when event is null', () => {
    const { setImportProgress } = useStore.getState()
    setImportProgress({ phase: 'download', status: 'done' })
    setImportProgress(null)
    expect(useStore.getState().importProgress).toBeNull()
    expect(useStore.getState().importStages).toEqual([])
  })

  it('marks error status correctly', () => {
    useStore.getState().setImportProgress({ phase: 'fetch', status: 'error', message: 'Network error' })
    const stages = useStore.getState().importStages
    expect(stages[0].status).toBe('error')
  })
})

// ── Processing task key format ──

describe('setProcessing — processing task tracking', () => {
  it('creates task with docId__kind key', () => {
    useStore.getState().setProcessing('abc123__generate', { kind: 'generate', mode: 'notes', title: 'Test' })
    const tasks = useStore.getState().processingTasks
    expect(Object.keys(tasks)).toHaveLength(1)
    expect(tasks['abc123__generate'].kind).toBe('generate')
    expect(tasks['abc123__generate'].mode).toBe('notes')
  })

  it('removes task when passed null', () => {
    const { setProcessing } = useStore.getState()
    setProcessing('abc__retranscribe', { kind: 'retranscribe', title: 'Transcribing' })
    expect(Object.keys(useStore.getState().processingTasks)).toHaveLength(1)
    setProcessing('abc__retranscribe', null)
    expect(Object.keys(useStore.getState().processingTasks)).toHaveLength(0)
  })
})

describe('clearDocTasks — batch clear by docId', () => {
  it('clears all tasks for a given doc', () => {
    const { setProcessing } = useStore.getState()
    setProcessing('doc1__generate', { kind: 'generate', title: 'Gen1' })
    setProcessing('doc1__retranscribe', { kind: 'retranscribe', title: 'ReT' })
    setProcessing('doc2__generate', { kind: 'generate', title: 'Gen2' })
    expect(Object.keys(useStore.getState().processingTasks)).toHaveLength(3)

    useStore.getState().clearDocTasks('doc1')
    const tasks = useStore.getState().processingTasks
    expect(Object.keys(tasks)).toHaveLength(1)
    expect(tasks['doc2__generate'].title).toBe('Gen2')
  })
})

// ── Image result normalization ──

describe('loadImageResults — normalize to Record<mode, result>', () => {
  it('converts array to Record keyed by mode', () => {
    const results = [
      { mode: 'extract', text: 'hello', engine: 'dashscope-ocr', id: 'r1' },
      { mode: 'table', text: '|a|b|', engine: 'dashscope-ocr', id: 'r2' },
    ]
    useStore.getState().loadImageResults('docA', results)
    const ir = useStore.getState().imageResults['docA']
    expect(Object.keys(ir)).toHaveLength(2)
    expect(ir['extract'].text).toBe('hello')
    expect(ir['table'].text).toBe('|a|b|')
  })

  it('preserves existing results for other docs', () => {
    useStore.getState().setImageResult('docX', 'extract', { text: 'x', engine: 'e', id: 'i' })
    useStore.getState().loadImageResults('docY', [{ mode: 'extract', text: 'y', engine: 'e', id: 'j' }])
    expect(useStore.getState().imageResults['docX']['extract'].text).toBe('x')
    expect(useStore.getState().imageResults['docY']['extract'].text).toBe('y')
  })
})

describe('setImageResult — single result update', () => {
  it('stores result by docId + mode', () => {
    useStore.getState().setImageResult('d1', 'extract', { text: 'Result', engine: 'e1', id: 'r1' })
    expect(useStore.getState().imageResults['d1']['extract'].text).toBe('Result')
  })

  it('adds mode to existing doc without overwriting other modes', () => {
    const { setImageResult } = useStore.getState()
    setImageResult('d1', 'extract', { text: 'E', engine: 'e', id: '1' })
    setImageResult('d1', 'table', { text: 'T', engine: 'e', id: '2' })
    expect(Object.keys(useStore.getState().imageResults['d1'])).toHaveLength(2)
  })
})

// ── Toast ──

describe('addToast — toast notification', () => {
  it('adds toast and assigns sequential ID', () => {
    useStore.getState().addToast('hello')
    expect(useStore.getState().toasts).toHaveLength(1)
    expect(useStore.getState().toasts[0].message).toBe('hello')
    expect(useStore.getState().toasts[0].type).toBe('success')
  })

  it('creates toast with error type', () => {
    useStore.getState().addToast('fail', 'error')
    expect(useStore.getState().toasts[0].type).toBe('error')
  })

  it('limits to 5 toasts (drops oldest)', () => {
    const { addToast } = useStore.getState()
    for (let i = 1; i <= 7; i++) addToast(`msg${i}`)
    const toasts = useStore.getState().toasts
    expect(toasts).toHaveLength(5)
    expect(toasts[0].message).toBe('msg3') // msg1 and msg2 dropped
  })

  it('supports manual dismiss', () => {
    const { addToast } = useStore.getState()
    addToast('first')
    const id = useStore.getState().toasts[0].id
    useStore.getState().dismissToast(id)
    expect(useStore.getState().toasts).toHaveLength(0)
  })
})

// ── Docs ──

describe('addDoc + selectDoc', () => {
  it('addDoc prepends to docs array', () => {
    const doc1 = { id: '1', title: 'A', sourceType: 'webpage', engine: '', contentHash: 'a', tags: [], createdAt: '2024-01-01' }
    const doc2 = { id: '2', title: 'B', sourceType: 'webpage', engine: '', contentHash: 'b', tags: [], createdAt: '2024-01-02' }
    useStore.getState().addDoc(doc1)
    useStore.getState().addDoc(doc2)
    const docs = useStore.getState().docs
    expect(docs).toHaveLength(2)
    expect(docs[0].id).toBe('2') // prepended
  })

  it('selectDoc sets currentDocId', () => {
    useStore.getState().selectDoc('abc')
    expect(useStore.getState().currentDocId).toBe('abc')
  })
})

// ── 多任务并发 ──

describe('cancelOneTask — 逐任务取消', () => {
  it('removes exact task key only', () => {
    const { setProcessing } = useStore.getState()
    setProcessing('docA__generate', { kind: 'generate', title: 'GenA' })
    setProcessing('docA__retranscribe', { kind: 'retranscribe', title: 'ReA' })
    setProcessing('docB__generate', { kind: 'generate', title: 'GenB' })
    expect(Object.keys(useStore.getState().processingTasks)).toHaveLength(3)

    useStore.getState().cancelOneTask('docA__generate')
    const tasks = useStore.getState().processingTasks
    expect(Object.keys(tasks)).toHaveLength(2)
    expect(tasks['docA__retranscribe']).toBeDefined()
    expect(tasks['docB__generate']).toBeDefined()
  })
})

describe('processingTasks — 多任务状态推导', () => {
  it('同一文档可同时有 generate + retranscribe + process 任务', () => {
    const { setProcessing } = useStore.getState()
    setProcessing('docA__generate', { kind: 'generate', title: 'Gen' })
    setProcessing('docA__retranscribe', { kind: 'retranscribe', title: 'ReT' })
    setProcessing('docA__process', { kind: 'process', mode: 'extract', title: 'Proc' })

    const tasks = useStore.getState().processingTasks
    expect(Object.keys(tasks)).toHaveLength(3)
  })

  it('不同文档的任务完全独立', () => {
    const { setProcessing } = useStore.getState()
    setProcessing('docA__generate', { kind: 'generate', title: 'GenA' })
    setProcessing('docB__generate', { kind: 'generate', title: 'GenB' })

    // docA 任务存在
    const tasksA = Object.entries(useStore.getState().processingTasks)
      .filter(([k]) => k.startsWith('docA__'))
    expect(tasksA).toHaveLength(1)

    // docB 任务独立
    const tasksB = Object.entries(useStore.getState().processingTasks)
      .filter(([k]) => k.startsWith('docB__'))
    expect(tasksB).toHaveLength(1)
  })
})

describe('bumpStreamTick — 流式 tick', () => {
  it('increments streamTick on each call', () => {
    useStore.setState({ streamTick: 0 })
    useStore.getState().bumpStreamTick()
    expect(useStore.getState().streamTick).toBe(1)
    useStore.getState().bumpStreamTick()
    expect(useStore.getState().streamTick).toBe(2)
  })
})
