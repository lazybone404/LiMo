/**
 * 筛选区测试 —— 两级折叠 / 摘要带值 / 就地清除 / 标签无上限且已选置顶 /
 * 标题关键词不触发 FTS / 「本周」是自然周 / 清除全部。
 *
 * 这些判据的共同点：**实现写成「看起来等效」的样子时功能照常、界面看着也对**。
 * 典型如「收起用 CSS 隐藏」与「收起不渲染」——用户看不出差别，但 Tab 顺序、
 * `querySelectorAll`、将来包进 form 的提交行为全都在骗人。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
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

/** 与 `docLibraryPrefs.ts` 的私有 KEY 同值（那是模块内常量，未导出） */
const PREFS_KEY = 'mbw_docLibPrefs'
const seedPrefs = (p: Record<string, unknown>) => localStorage.setItem(PREFS_KEY, JSON.stringify(p))

function doc(over: Partial<DocEntry> & { id: string }): DocEntry {
  return {
    title: `文档 ${over.id}`, sourceType: 'pdf', engine: 'e',
    contentHash: `h-${over.id}`, tags: [], createdAt: '2026-01-01T00:00:00', ...over,
  }
}

const rows = (c: HTMLElement) => c.querySelectorAll('[data-doc-row]')
/** 某个小节**内部**的 chip —— 选择器必须限定在节内：folder 与 type 都有一个 `__all__` */
const chips = (c: HTMLElement, section: string) =>
  c.querySelectorAll(`[data-filter-section="${section}"] [data-chip]`)
const toggle = (c: HTMLElement, id: string) => c.querySelector(`[data-filter-section-toggle="${id}"]`) as HTMLElement

beforeEach(() => {
  localStorage.clear()
  searchMock.mockReset()
  searchMock.mockResolvedValue([])
  useStore.setState({
    docs: [doc({ id: 'a', tags: ['重点'] })], folders: [], currentDocId: null, toasts: [],
    selectedIds: new Set(), listLimit: 60, docsLoading: false,
    searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
    titleFilter: '', dateFrom: null, dateTo: null,
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('两级折叠', () => {
  it('区头折叠：**所有**小节不进 DOM（不是 CSS 隐藏）', () => {
    const { container } = render(<DocLibrary />)
    const n = container.querySelectorAll('[data-filter-section]').length
    expect(n).toBeGreaterThan(0)

    fireEvent.click(container.querySelector('[data-filter-area-toggle]') as HTMLElement)
    // ⚠️ 对照（有区分力）：把整区实现成 `hidden` / `max-h-0 overflow-hidden`（省空间、
    // DOM 还在），下面这条仍是 n，本断言红。
    expect(container.querySelectorAll('[data-filter-section]').length).toBe(0)
    // 头部留着 —— 它是重新展开的入口
    expect(container.querySelector('[data-filter-area-toggle]')).toBeTruthy()
  })

  it('小节折叠是**独立**的：收起一节不动另一节', () => {
    const { container } = render(<DocLibrary />)
    // 默认只展开 folder 节；先展开 type
    fireEvent.click(toggle(container, 'type'))
    expect(chips(container, 'type').length).toBeGreaterThan(0)
    expect(chips(container, 'folder').length).toBeGreaterThan(0)

    fireEvent.click(toggle(container, 'type'))
    // ⚠️ 对照（有区分力）：只做一级折叠（小节头不可点 / 点了没反应），本条仍是 12 个 chip，红。
    expect(chips(container, 'type').length).toBe(0)
    // 独立性：folder 节一个 chip 都没少
    expect(chips(container, 'folder').length).toBeGreaterThan(0)
  })

  it('展开状态是持久化偏好（跨重挂载保留）', () => {
    const { container } = render(<DocLibrary />)
    fireEvent.click(toggle(container, 'date'))
    expect(JSON.parse(localStorage.getItem(PREFS_KEY) || '{}').expandedSections).toContain('date')

    cleanup()
    const c2 = render(<DocLibrary />).container
    expect(chips(c2, 'date').length).toBeGreaterThan(0)
  })
})

describe('摘要与小节级清除', () => {
  it('收起的小节标题行显示**具体值**（不是「已筛选」）', () => {
    useStore.setState({ activeType: 'pdf' })
    const { container } = render(<DocLibrary />)
    // 默认 type 节是收起的 → 内容不进 DOM，但摘要必须在
    expect(chips(container, 'type').length).toBe(0)
    // ⚠️ 对照（有区分力）：摘要写成 `activeType ? '已筛选' : ''`（有文案无信息），
    // 或干脆不渲染摘要 → 本断言红。
    expect(container.querySelector('[data-filter-summary="type"]')?.textContent).toContain('PDF')
  })

  it('摘要旁的 × 就地清除该条件（不必先展开）', () => {
    useStore.setState({ activeType: 'pdf' })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelector('[data-filter-clear="type"]') as HTMLElement)
    expect(useStore.getState().activeType).toBe('')
  })

  it('无激活条件时不渲染摘要（不留空位）', () => {
    const { container } = render(<DocLibrary />)
    expect(container.querySelector('[data-filter-summary="type"]')).toBeNull()
  })
})

describe('标签小节', () => {
  /**
   * ⚠️ 标签名**零填充**：facet 按「频次降序 + 同频字典序」排，13 个标签各出现 1 次 →
   * 纯字典序。不填充时 `标签12` 排在 `标签1` 与 `标签2` 之间（**第 5 位**），
   * `slice(0, 12)` 砍不到它，对照实验会假绿。
   */
  const manyTags = () => Array.from({ length: 13 }, (_, i) =>
    doc({ id: `d${i}`, tags: [`标签${String(i + 1).padStart(2, '0')}`] }))

  it('**没有** 12 个上限：第 13 个标签可点且能激活', () => {
    seedPrefs({ expandedSections: ['tag'] })
    useStore.setState({ docs: manyTags() })
    const { container } = render(<DocLibrary />)

    const last = container.querySelector('[data-chip="标签13"]') as HTMLElement
    // ⚠️ 对照（有区分力）：恢复 `tagFacets.slice(0, 12)`（或「slice(0,12) + 已选补回」这种
    // 部分修正——已选的能显示，但第 13 个**未选**标签依然够不到）→ 本断言红。
    expect(last).toBeTruthy()
    fireEvent.click(last)
    expect(useStore.getState().activeTags).toEqual(['标签13'])
  })

  it('已选标签**置顶**（低频标签选中后不会被排到末尾看不见的地方）', () => {
    seedPrefs({ expandedSections: ['tag'] })
    // 「热门」出现 3 次（频次降序排第一）；「冷门」只 1 次（排最后）
    useStore.setState({
      docs: [
        doc({ id: 'h1', tags: ['热门'] }), doc({ id: 'h2', tags: ['热门'] }),
        doc({ id: 'h3', tags: ['热门'] }), doc({ id: 'c1', tags: ['冷门'] }),
      ],
      activeTags: ['冷门'],
    })
    const { container } = render(<DocLibrary />)
    const all = [...chips(container, 'tag')].map(e => e.getAttribute('data-chip'))
    // ⚠️ 对照（有区分力）：直接用 `collectTagFacets` 的顺序（不置顶）→ 首位是「热门」，红。
    // （这条在标签多到要滚动时才真正影响可用性，但它是个不能用肉眼看出来的顺序错。）
    expect(all[0]).toBe('冷门')
  })
})

describe('标题关键词与搜索框的分工', () => {
  it('只填标题关键词 → **不**打 /api/search（纯内存筛选）', async () => {
    seedPrefs({ expandedSections: ['title'] })
    useStore.setState({
      docs: [doc({ id: 'a', title: '论文A' }), doc({ id: 'b', title: '另一篇' })],
    })
    const { container } = render(<DocLibrary />)

    fireEvent.change(container.querySelector('[data-title-filter]') as HTMLElement, { target: { value: '论文' } })

    // 等过搜索的 debounce 窗口（250ms）——若这条路走了 FTS，早就发出请求了
    await new Promise(r => setTimeout(r, 350))
    // ⚠️ 对照（有区分力）：把这一节复用 `searchQuery`（同一个 setter）实现 —— 看着没问题、
    // 结果也一样，但每次敲标题都会打一次 FTS，本断言红。
    expect(searchMock).not.toHaveBeenCalled()
    // 结果确实收窄了（不是「没报错但也没筛」）
    expect(rows(container).length).toBe(1)
  })
})

describe('日期范围', () => {
  it('「本周」= 本**自然周**（周一起），不是「最近 7 天」', () => {
    // 2026-09-25 是周五 → 本周一是 09-21；而「最近 7 天」= 09-19 起
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-25T12:00:00'))
    seedPrefs({ expandedSections: ['date'] })
    useStore.setState({
      docs: [
        doc({ id: 'today', createdAt: '2026-09-25T08:00:00' }),
        doc({ id: 'sunday', createdAt: '2026-09-20T08:00:00' }),  // 上周日：在「最近 7 天」内、不在本周
      ],
    })
    const { container } = render(<DocLibrary />)
    expect(rows(container).length).toBe(2)

    fireEvent.click(container.querySelector('[data-chip="week"]') as HTMLElement)
    // ⚠️ 对照（有区分力）：「本周」实现成 `now - 6d ~ now`（看着更「宽松友好」的实现）→
    // 上周日那篇也被带出来，本断言红。
    expect(rows(container).length).toBe(1)
    expect(rows(container)[0].textContent).toContain('文档 today')
  })

  it('再点一次已激活的快捷项 = 取消（不落到「全部」）', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-25T12:00:00'))
    seedPrefs({ expandedSections: ['date'] })
    useStore.setState({ docs: [doc({ id: 'today', createdAt: '2026-09-25T08:00:00' })] })
    const { container } = render(<DocLibrary />)

    const chip = container.querySelector('[data-chip="today"]') as HTMLElement
    fireEvent.click(chip)
    expect(useStore.getState().dateFrom).not.toBeNull()
    fireEvent.click(chip)
    expect(useStore.getState().dateFrom).toBeNull()
  })
})

describe('清除全部', () => {
  it('一次清掉五个维度，但**保留**搜索词（搜索框不在筛选区里）', () => {
    seedPrefs({ expandedSections: ['folder'] })
    useStore.setState({
      docs: [doc({ id: 'a', folderId: 'f1' })],
      folders: [{ id: 'f1', name: '工作', createdAt: '2026-01-01T00:00:00', count: 0 }],
      activeFolderId: 'f1', activeType: 'pdf', activeTags: ['重点'],
      titleFilter: 'x', dateFrom: '2026-01-01', dateTo: null,
      searchQuery: '保留我', selectedIds: new Set(['a']),
    })
    const { container } = render(<DocLibrary />)
    expect(container.querySelector('[data-filter-count]')?.textContent).toContain('5')

    fireEvent.click(container.querySelector('[data-filter-clear-all]') as HTMLElement)
    const s = useStore.getState()
    expect([s.activeFolderId, s.activeType, s.titleFilter, s.dateFrom, s.dateTo]).toEqual([null, '', '', null, null])
    expect(s.activeTags).toEqual([])
    // ⚠️ 对照（有区分力）：`clearAllFilters` 里直接 `setState({...})` 一把梭（绕过各 setter）——
    // 五个筛选确实都清空了、界面看起来完全正确，但**同时绕过了各 setter 内联的「清空选择」**，
    // 复活了本轮要消灭的时序窗口。故这里同时断言选择集也被清掉（那半是本轮新增的不变量）。
    expect(s.selectedIds.size).toBe(0)
    expect(s.searchQuery).toBe('保留我')
  })
})

describe('筛选变更清空选择（同一次 set 内原子发生）', () => {
  it('改任一筛选条件 → selectedIds 立即清空（不留时序窗口）', async () => {
    seedPrefs({ expandedSections: ['title'] })
    useStore.setState({
      docs: [doc({ id: 'a', title: '论文A' })], selectedIds: new Set(['a']),
    })
    const { container } = render(<DocLibrary />)
    expect(useStore.getState().selectedIds.size).toBe(1)

    fireEvent.change(container.querySelector('[data-title-filter]') as HTMLElement, { target: { value: '论' } })
    // 同步断言：没有 await —— 「下一帧才清」的实现（组件 effect）在这里会红
    expect(useStore.getState().selectedIds.size).toBe(0)
  })
})
