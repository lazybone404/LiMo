/**
 * 文档库搜索测试 —— 相关度排序键 / 与「排列」正交 / Ctrl+K 聚焦 / 截断提示的边界。
 *
 * 核心背景：FTS 命中原本被固定追加成「原文匹配（N）」独立分组，用户从没选过排序 ——
 * 「最相关的在最上面」是那个分组的**副作用**。并入主列表后若不补一个排序键，
 * 同样的搜索会按「时间」排，最相关的那篇可能沉到第三行：**功能没坏，只是变难用了**。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { useStore } from '../store'
import type { SearchHit } from '../api/search'
import type { DocEntry } from '../store/types'

class IO {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
;(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = IO

const searchMock = vi.fn()
vi.mock('../api/search', () => ({
  searchDocs: (q: string, limit?: number, signal?: AbortSignal) => searchMock(q, limit, signal),
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    batchTranscripts: vi.fn(),
    listFolders: vi.fn().mockResolvedValue([]),
    listTranscripts: vi.fn().mockResolvedValue([]),
    getMarkdown: vi.fn(),
    createFolder: vi.fn(),
    renameFolder: vi.fn(),
    deleteFolder: vi.fn(),
    updateTranscript: vi.fn(),
  }
})

const { DocLibrary } = await import('../components/doc-library/DocLibrary')
const { handleAppKeyDown } = await import('../App')

const PREFS_KEY = 'mbw_docLibPrefs'
const readPrefs = () => JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')
const seedPrefs = (p: Record<string, unknown>) => localStorage.setItem(PREFS_KEY, JSON.stringify(p))

function doc(over: Partial<DocEntry> & { id: string }): DocEntry {
  return {
    title: `文档 ${over.id}`, sourceType: 'pdf', engine: 'e',
    contentHash: `h-${over.id}`, tags: [], createdAt: '2026-01-01T00:00:00', ...over,
  }
}

const hit = (id: string, snippet = ''): SearchHit => ({ id, title: `t-${id}`, snippet })
const rows = (c: HTMLElement, scope: string = '') =>
  c.querySelectorAll(`${scope} [data-doc-row]`)
const searchBox = (c: HTMLElement) => c.querySelector('[data-search-input]') as HTMLElement

beforeEach(() => {
  localStorage.clear()
  searchMock.mockReset()
  searchMock.mockResolvedValue([])
  useStore.setState({
    docs: [], folders: [], currentDocId: null, toasts: [],
    selectedIds: new Set(), listLimit: 60, docsLoading: false,
    searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
    titleFilter: '', dateFrom: null, dateTo: null,
    docLibSearchFocusNonce: 0,
  })
})

afterEach(() => cleanup())

describe('相关度排序键', () => {
  // 「B」的时间**更旧**，按时间排会落在「A」后面 —— 两条排序给出的顺序不同，
  // 判据才有区分力（同序的话断言谁在前面都是对的）。
  const two = () => [
    doc({ id: 'a', title: 'A', createdAt: '2026-03-01T00:00:00' }),
    doc({ id: 'b', title: 'B', createdAt: '2026-01-01T00:00:00' }),
  ]

  it('搜索词从空变非空 → 自动切「相关度」，首行是 FTS 的第一条', async () => {
    seedPrefs({ groupBy: 'none' })
    useStore.setState({ docs: two() })
    // 后端返回时已按相关度排好（ORDER BY rank）：b 最相关 → 排在数组第 0 位
    searchMock.mockResolvedValue([hit('b'), hit('a')])
    const { container } = render(<DocLibrary />)

    fireEvent.change(searchBox(container), { target: { value: '关键词' } })

    await waitFor(() => expect(readPrefs().sort).toBe('relevance'), { timeout: 2000 })
    // 等 FTS 回来（防抖 250ms + 请求）—— 自动切排序是同步的，命中的行不是
    await waitFor(() => expect(rows(container).length).toBe(2), { timeout: 2000 })
    // ⚠️ 对照（有区分力）：
    //  ① 搜索后仍按「时间」排（没加自动切换）→ 首行是 A（时间更新），红；
    //  ② rank 取成 `hits.length - i`（反序，看着也能排出个「相关度」）→ 首行是 A，红。
    expect(rows(container)[0].textContent).toContain('B')
  })

  it('「相关度」键只在**本次搜索真有命中**时出现（避免一个点不动的死键）', async () => {
    seedPrefs({ groupBy: 'none' })
    useStore.setState({ docs: two() })
    const { container } = render(<DocLibrary />)

    expect(container.querySelector('[data-sort-key="relevance"]')).toBeNull()

    searchMock.mockResolvedValue([hit('a')])
    fireEvent.change(searchBox(container), { target: { value: '关键词' } })
    await waitFor(() => expect(container.querySelector('[data-sort-key="relevance"]')).toBeTruthy(), { timeout: 2000 })
  })

  it('清空搜索词 → 相关度键消失且排序回落到时间（不留「四个键都没高亮」的空档）', async () => {
    seedPrefs({ groupBy: 'none' })
    useStore.setState({ docs: two() })
    searchMock.mockResolvedValue([hit('a')])
    const { container } = render(<DocLibrary />)

    fireEvent.change(searchBox(container), { target: { value: '关键词' } })
    await waitFor(() => expect(readPrefs().sort).toBe('relevance'), { timeout: 2000 })

    fireEvent.click(container.querySelector('[data-search-clear]') as HTMLElement)
    await waitFor(() => expect(readPrefs().sort).toBe('date'), { timeout: 2000 })
    expect(container.querySelector('[data-sort-key="relevance"]')).toBeNull()
  })
})

describe('相关度与「排列」正交', () => {
  it('按文件夹排列 + 相关度：分组头在，且**组内**首行是该组内 rank 最小的', async () => {
    seedPrefs({ groupBy: 'folder' })
    useStore.setState({
      docs: [
        doc({ id: 'a', title: 'A', folderId: 'f1', createdAt: '2026-01-01T00:00:00' }),
        doc({ id: 'b', title: 'B', folderId: 'f1', createdAt: '2026-03-01T00:00:00' }),
      ],
      folders: [{ id: 'f1', name: '工作', createdAt: '2026-01-01T00:00:00', count: 0 }],
    })
    // 相关度：a 第 0；而按时间排的话 b（更新）会在前面 —— 两种排序在 f1 组内给出相反的顺序
    searchMock.mockResolvedValue([hit('a'), hit('b')])
    const { container } = render(<DocLibrary />)

    fireEvent.change(searchBox(container), { target: { value: '关键词' } })
    await waitFor(() => expect(readPrefs().sort).toBe('relevance'), { timeout: 2000 })
    await waitFor(() => expect(rows(container, '[data-doc-group="f:f1"]').length).toBe(2), { timeout: 2000 })

    expect(container.querySelector('[data-doc-group="f:f1"]')).toBeTruthy()
    // ⚠️ 对照（有区分力）：排序被分组覆盖（组内恒按时间，即 `sortDocs` 只作用于平铺）→
    // 组内首行是 B，红。
    expect(rows(container, '[data-doc-group="f:f1"]')[0].textContent).toContain('A')
  })
})

/**
 * ⚠️ 这两条测的是 **store action 本身**（App 的 Ctrl+K 与顶栏搜索按钮都只调它），
 * 不是 SlotLayout 的挂载行为。「折叠态下 SearchField 其实还没挂载、展开后才 mount，
 * 而 effect 在 mount 后跑，所以 nonce 依然生效」这条链路要渲染整个 App 才覆盖得到。
 */
describe('Ctrl+K 的键盘绑定', () => {
  /** 造一个够用的假事件 —— handler 只碰 key / metaKey / ctrlKey / preventDefault */
  const press = (k: string, mod = true) => {
    const e = { key: k, ctrlKey: mod, metaKey: false, preventDefault: vi.fn() }
    handleAppKeyDown(e as unknown as Parameters<typeof handleAppKeyDown>[0])
    return e
  }

  it('Ctrl+K 接到了「打开文档库搜索」上', () => {
    const e = press('k')
    expect(e.preventDefault).toHaveBeenCalled()
    // ⚠️ 这条补的是「action 测过了、但没人验证按键绑没绑上去」那一环：
    // `case` 写成别的手感一样（按了没反应，用户只当快捷键不存在）。
    expect(useStore.getState().docLibSearchFocusNonce).toBe(1)
  })

  it('无修饰键的 k 不触发（否则每次打字按到 k 都跳走）', () => {
    const e = press('k', false)
    // ⚠️ 对照（有区分力）：去掉 `if (!(e.metaKey || e.ctrlKey)) return` 守卫 →
    // 敲正文时每打一个 k 都会展开面板并抢走焦点，本断言红。
    expect(e.preventDefault).not.toHaveBeenCalled()
    expect(useStore.getState().docLibSearchFocusNonce).toBe(0)
  })
})

describe('Ctrl+K 与顶栏搜索入口', () => {
  it('openDocLibSearch：展开文档库**且**把焦点交给搜索框', async () => {
    useStore.setState({ slots: { ...useStore.getState().slots, docLib: { width: 260, collapsed: true, visible: true } } })
    const { container } = render(<DocLibrary />)

    useStore.getState().openDocLibSearch()
    await waitFor(() => expect(document.activeElement).toBe(searchBox(container)))
    // ⚠️ 对照（有区分力）：只发聚焦信号、不展开面板（或反之只展开不聚焦）→ 其中一条红。
    // 两个动作必须成对，否则用户按了快捷键却什么也看不到 / 光标没进去。
    expect(useStore.getState().slots.docLib.collapsed).toBe(false)
  })

  it('折叠态的窄面板（拖窄后被自动折叠）展开时宽度一并复位 —— 不能展开成一条缝', async () => {
    useStore.setState({ slots: { ...useStore.getState().slots, docLib: { width: 40, collapsed: true, visible: true } } })
    const { container } = render(<DocLibrary />)

    useStore.getState().openDocLibSearch()
    await waitFor(() => expect(document.activeElement).toBe(searchBox(container)))
    // ⚠️ 对照（有区分力）：只传 `collapsed: false` 而不管 width —— 面板确实「展开」了
    // （collapsed 变 false、组件也在渲染），但它只有 **40px 宽**：用户按 Ctrl+K 是为了
    // 搜索，拿到的却是一条缝。这条断言必须看 width，只看 `collapsed === false` 会漏掉它。
    const slot = useStore.getState().slots.docLib
    expect(slot.collapsed).toBe(false)
    expect(slot.width).toBeGreaterThanOrEqual(80)
  })
})
