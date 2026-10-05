import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useStore } from '../store'

// Reset store to clean state before each test
beforeEach(() => {
  useStore.setState({
    docs: [], folders: [], currentDocId: null, searchQuery: '',
    activeType: '', activeTags: [], activeFolderId: null,
    currentContent: null, currentPolishedContent: null, currentRawText: null,
    markdownMeta: null, markdownVersions: [],
    markdownEdited: { notes: false, polished: false },
    streamTick: 0,
    imageResults: {}, processingTasks: {}, taskPanelOpen: false,
    generating: false,
    aiVisible: false,
    toasts: [],
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

// ── markArtifact：产物落盘后补记产物位 ──
//
// 存在的理由是新鲜度：`docs` 是生成**开始之前**拉的快照，不补记的话「生成完 → 切到
// 别的文档 → 切回来」会因 `modes` 里没有它而一个请求都不发，笔记看起来凭空消失。
describe('markArtifact — 产物位补记', () => {
  const mk = (id: string, modes?: string[]) => ({
    id, title: id, sourceType: 'webpage', engine: '', contentHash: id, tags: [], createdAt: '2024-01-01',
    ...(modes === undefined ? {} : { modes }),
  })

  it('已有 mode 的文档追加新产物，顺序与后端 sorted() 同源', () => {
    useStore.setState({ docs: [mk('d1', ['polished'])] })
    useStore.getState().markArtifact('d1', 'notes')
    expect(useStore.getState().docs[0].modes).toEqual(['notes', 'polished'])
  })

  it('重复补记同一产物：值不变、且**引用不变**（set 按引用比较，白重建会拖累整棵树）', () => {
    useStore.setState({ docs: [mk('d1', ['notes'])] })
    const before = useStore.getState().docs[0]
    useStore.getState().markArtifact('d1', 'notes')
    expect(useStore.getState().docs[0]).toBe(before)
  })

  // ⚠️ 这条是整个修复里最容易写错的地方：把「不知道有没有」误变成「确定只有这一个」，
  // 于是另一种产物从此**永远不请求**（切回来少一半内容，零报错）。
  it('modes 为 undefined（未知）→ **原样不动**，绝不补成 [mode]', () => {
    useStore.setState({ docs: [mk('d1')] })
    const before = useStore.getState().docs[0]
    useStore.getState().markArtifact('d1', 'notes')
    expect(useStore.getState().docs[0]).toBe(before)
    expect(useStore.getState().docs[0].modes).toBeUndefined()
  })

  it('docId 不存在 → 不动任何文档', () => {
    useStore.setState({ docs: [mk('d1', ['notes'])] })
    const before = useStore.getState().docs
    useStore.getState().markArtifact('nope', 'polished')
    expect(useStore.getState().docs).toBe(before)
  })

  it('只改目标文档，兄弟文档引用不变', () => {
    useStore.setState({ docs: [mk('d1', ['notes']), mk('d2', ['notes'])] })
    const sibling = useStore.getState().docs[1]
    useStore.getState().markArtifact('d1', 'polished')
    expect(useStore.getState().docs[1]).toBe(sibling)
    expect(useStore.getState().docs[1].modes).toEqual(['notes'])
  })

  // 端到端语义：补记之后，门控判据必须真的放行（两条断言成对——只断言 modes 变了
  // 会在「补记了但判据不认」的实现下绿）
  it('补记后门控放行该产物（与 planArtifactRequests 成对）', async () => {
    const { planArtifactRequests } = await import('../lib/artifactModes')
    useStore.setState({ docs: [mk('d1', [])] })
    expect(planArtifactRequests(useStore.getState().docs[0].modes)).toEqual({ notes: false, polished: false })
    useStore.getState().markArtifact('d1', 'notes')
    expect(planArtifactRequests(useStore.getState().docs[0].modes)).toEqual({ notes: true, polished: false })
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

// ── 产物已编辑标记（覆盖保护）──

describe('setMarkdownEdited — 产物已手动编辑标记', () => {
  it('默认两产物均未编辑', () => {
    expect(useStore.getState().markdownEdited).toEqual({ notes: false, polished: false })
  })

  it('只更新指定产物，不影响另一产物', () => {
    useStore.getState().setMarkdownEdited('notes', true)
    expect(useStore.getState().markdownEdited).toEqual({ notes: true, polished: false })
    useStore.getState().setMarkdownEdited('polished', true)
    expect(useStore.getState().markdownEdited).toEqual({ notes: true, polished: true })
    // 重新生成后清除
    useStore.getState().setMarkdownEdited('notes', false)
    expect(useStore.getState().markdownEdited).toEqual({ notes: false, polished: true })
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

// ── backendReady 初值（Tauri 冷启动保护） ──
//
// ⚠️ 这条不变量很隐蔽：初值给 true 时**功能照常**（后端起来后一切正常），
// 只表现为「首渲染闪一下完整 UI」+ 控制台一屏 ERR_CONNECTION_REFUSED ——
// 因为 effect 在 render **之后**才跑，setBackendReady(false) 拦不住第一次渲染，
// 而 GenPanel 一挂载就拉 /api/models、/api/providers，那一刻后端必然还没监听；
// 那行报错还是 WebView 自动打印的，JS 抑制不掉。所以只能把「初值按环境同步判定」
// 这件事本身钉住，否则改回 `backendReady: true`（看起来更直观）没有任何测试会红。
//
// ⚠️ 初值在模块求值时定稿，故必须 resetModules + 动态 import 才能改环境重取。
// 本块放文件末尾：重置后拿到的是**另一个 store 实例**，不能与上面的静态实例混用。
describe('backendReady 初值 — Tauri 冷启动保护', () => {
  afterEach(() => { delete (window as any).__TAURI_INTERNALS__ })

  it('Tauri 环境初值为 false（首渲染就必须是加载屏）', async () => {
    vi.resetModules()
    ;(window as any).__TAURI_INTERNALS__ = {}
    const fresh = await import('../store')
    expect(fresh.useStore.getState().backendReady).toBe(false)
  })

  it('浏览器环境初值为 true（没有后端启动过程，不必等）', async () => {
    vi.resetModules()
    delete (window as any).__TAURI_INTERNALS__
    const fresh = await import('../store')
    expect(fresh.useStore.getState().backendReady).toBe(true)
  })
})

// ── 主题轴冷启动恢复：--texture-opacity ──
//
// ⚠️ 与上面 backendReady 同类：副作用发生在模块求值时的启动链里，故同样需要
// resetModules + 动态 import 才能重跑一次「冷启动」。
//
// 这条不变量**看起来完全正常**，所以此前没人发现：纹理本身恢复了（data-texture 由
// applyTexture 设），强度只是个「合理值」—— CSS 兜底 var(--texture-opacity, 0.5)
// 恰好等于默认强度 50，零报错、零视觉异常。只有用户明确设过非 50 的强度并重启后
// 才看得出来。根因是六轴里唯独 intensity 的应用逻辑留在设置页组件内（只在滑块
// onChange 时跑），启动链从未调用它。这里钉的就是「启动链必须自己应用 intensity」。
describe('主题轴冷启动恢复 — --texture-opacity', () => {
  const KEY = 'mbw_texture_intensity'

  afterEach(() => { localStorage.removeItem(KEY) })

  it('写入已保存的强度（反转映射 80 → 0.2）', async () => {
    const spy = vi.spyOn(document.documentElement.style, 'setProperty')
    vi.resetModules()
    localStorage.setItem(KEY, '80')
    await import('../store')
    // applyIntensity 做 (100-value)/100 的反转：80 → 0.2
    expect(spy).toHaveBeenCalledWith('--texture-opacity', '0.2')
    spy.mockRestore()
  })

  it('未保存时写入默认强度（50 → 0.5）', async () => {
    const spy = vi.spyOn(document.documentElement.style, 'setProperty')
    vi.resetModules()
    localStorage.removeItem(KEY)
    await import('../store')
    expect(spy).toHaveBeenCalledWith('--texture-opacity', '0.5')
    spy.mockRestore()
  })
})

// ── 阅读版式冷启动：与 intensity 完全同款的风险 ──
//
// 同一条铁律：**值进了 store ≠ DOM 被写**。`reading` 字段在 themeSlice 的初值里
// 就调了 getSavedReading()，所以「store 里有值」这条断言永远是绿的；真正把
// --read-line-height / data-read-para 写到 documentElement 上的是 store/index.ts
// 启动链里那一行 applyReading。删掉那一行 → 版式全部回落到 :root 的 1.5 与「无属性」
// → 用户保存的偏好静默失效，且界面上看不出任何异常（1.5 是个完全合理的行高）。
describe('主题轴冷启动恢复 — 阅读版式', () => {
  const KEY = 'mbw_reading'

  afterEach(() => { localStorage.removeItem(KEY) })

  it('启动链把已保存的版式写进 DOM', async () => {
    const setProp = vi.spyOn(document.documentElement.style, 'setProperty')
    const setAttr = vi.spyOn(document.documentElement, 'setAttribute')
    vi.resetModules()
    localStorage.setItem(KEY, JSON.stringify({ lineHeight: 1.8, measure: 40, paraStyle: 'book' }))
    await import('../store')
    expect(setProp).toHaveBeenCalledWith('--read-line-height', '1.8')
    expect(setAttr).toHaveBeenCalledWith('data-read-para', 'book')
    setProp.mockRestore()
    setAttr.mockRestore()
  })

  it('未保存时写入默认（1.5 / web = 改造前的现状）', async () => {
    const setProp = vi.spyOn(document.documentElement.style, 'setProperty')
    vi.resetModules()
    localStorage.removeItem(KEY)
    await import('../store')
    expect(setProp).toHaveBeenCalledWith('--read-line-height', '1.5')
    setProp.mockRestore()
  })
})

// ── 沉浸阅读的进入守卫 ──
//
// ⚠️ 为什么守卫是**硬需求**而不是贴心提示：`SlotLayout` 的 `hideSlots` 是**真卸载**
// （命中即 `return null`），不是 CSS 隐藏 —— 正在跑的生成/转录任务会因 GenPanel
// 卸载而变成僵尸。项目对同一件事早已做过选择：Ctrl+G 在 processingTasks 非空时
// 拦截 + toast（「操作进行中，请完成或取消后再切换面板」）。沉浸绕开它 = 绕过
// 一个被明确选择过的保护。
//
// 退出方向**刻意不设限**：否则有任务在跑时用户被永远困在沉浸里 —— 那是比僵尸任务
// 更糟的失败形态（用户看得见却出不去）。
describe('沉浸阅读 — toggleImmersive 的守卫', () => {
  beforeEach(() => {
    useStore.setState({ immersive: false, toasts: [] })
  })

  it('无任务时正常进入', () => {
    useStore.getState().toggleImmersive()
    expect(useStore.getState().immersive).toBe(true)
  })

  it('有任务在跑时拒绝进入，并给出 toast', () => {
    // 对照：只断言 store flag 的测试**看不见这个缺陷的另一半** —— 真正危险的是
    // SlotLayout 卸载，而卸载发生在渲染层。所以 flag + toast 两条一起断。
    useStore.getState().setProcessing('d1__generate', { kind: 'generate', title: 'T' })
    useStore.getState().toggleImmersive()
    expect(useStore.getState().immersive).toBe(false)
    expect(useStore.getState().toasts.some(t => t.message.includes('进入沉浸阅读'))).toBe(true)
  })

  it('退出方向不受守卫限制（有任务时也能退出）', () => {
    useStore.getState().setProcessing('d1__generate', { kind: 'generate', title: 'T' })
    useStore.setState({ immersive: true })
    useStore.getState().toggleImmersive()
    expect(useStore.getState().immersive).toBe(false)
  })

  it('setImmersive(true) 同守卫，setImmersive(false) 不设限', () => {
    useStore.getState().setProcessing('d1__generate', { kind: 'generate', title: 'T' })
    useStore.getState().setImmersive(true)
    expect(useStore.getState().immersive).toBe(false)

    useStore.setState({ immersive: true })
    useStore.getState().setImmersive(false)
    expect(useStore.getState().immersive).toBe(false)
  })
})
