/**
 * 文档库**布局与排列**测试 —— 分组折叠 / 排列独立于筛选 / 密度 / 分隔条 /
 * 简略模式 / active 与 selected 分离 / FTS 截断提示。
 *
 * 这些判据的共同点：**实现写成「看起来等效」的样子时功能照常、界面看着也对**，
 * 只有把它们钉在 DOM 上才分得出来——典型如「折叠用 CSS 隐藏」与「折叠不渲染」，
 * 用户看不出差别，但 Tab 顺序、`querySelectorAll`、分批预算全都在骗人。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { useStore } from '../store'
import { ApiErrorException } from '../api/core'
import type { DocEntry } from '../store/types'

class IO {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
;(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = IO

const batchMock = vi.fn()
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    batchTranscripts: (payload: unknown) => batchMock(payload),
    listFolders: vi.fn().mockResolvedValue([]),
    listTranscripts: vi.fn().mockResolvedValue([]),
    getMarkdown: vi.fn(),
    createFolder: vi.fn(),
    renameFolder: vi.fn(),
    deleteFolder: vi.fn(),
    updateTranscript: vi.fn(),
  }
})

const searchMock = vi.fn()
/** 索引健康状态（可变：默认「一切正常」，接线用例里临时改成不可用）。 */
const statusMock = vi.fn()
vi.mock('../api/search', () => ({
  // 桩返回 **hits 数组**，包装层补 `semantic: null`（= 未启用语义检索）——
  // 真实 `searchDocs` 返回 `{hits, semantic}`，理由见 `docLibrarySearch` 同名注释。
  searchDocs: async (q: string, limit?: number, signal?: AbortSignal) => ({
    hits: await searchMock(q, limit, signal),
    semantic: null,
  }),
  getSearchStatus: () => statusMock(),
  reindexSearch: async () => { throw new Error('本用例不应触发索引重建') },
}))

/** `getSearchStatus` 的默认答复：索引正常 ⇒ 横幅一个字都不渲染（零回归）。 */
const STATUS_OK = {
  available: true, docCount: 0, sizeBytes: 0, lastError: null, rebuiltAt: null,
}

const { DocLibrary } = await import('../components/doc-library/DocLibrary')
const { __resetSearchHealthForTest: resetSearchHealth } = await import('../lib/search-health')

/** 与 `docLibraryPrefs.ts` 的私有 KEY 同值（那是模块内常量，未导出） */
const PREFS_KEY = 'mbw_docLibPrefs'
const readPrefs = () => JSON.parse(localStorage.getItem(PREFS_KEY) || '{}')
const seedPrefs = (p: Record<string, unknown>) => localStorage.setItem(PREFS_KEY, JSON.stringify(p))

function doc(over: Partial<DocEntry> & { id: string }): DocEntry {
  return {
    title: `文档 ${over.id}`, sourceType: 'pdf', engine: 'e',
    contentHash: `h-${over.id}`, tags: [], createdAt: '2026-01-01T00:00:00', ...over,
  }
}

const FOLDERS = [{ id: 'f1', name: '工作', createdAt: '2026-01-01T00:00:00', count: 0 }]

beforeEach(() => {
  localStorage.clear()
  searchMock.mockReset()
  searchMock.mockResolvedValue([])
  statusMock.mockReset()
  statusMock.mockResolvedValue(STATUS_OK)
  // 状态住在 `lib/search-health` 的模块级变量里（30s TTL），不清就会让
  // 「本次挂载到底探测了没有」取决于用例执行顺序。
  resetSearchHealth()
  batchMock.mockReset()
  batchMock.mockResolvedValue({ status: 'ok', affected: 1, failed: [] })
  useStore.setState({
    docs: [], folders: [], currentDocId: null, toasts: [],
    selectedIds: new Set(), listLimit: 60, docsLoading: false,
    searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
    titleFilter: '', dateFrom: null, dateTo: null,
  })
})

afterEach(() => cleanup())

const rows = (c: HTMLElement) => c.querySelectorAll('[data-doc-row]')

describe('分组头折叠', () => {
  const twoInFolder = () => [doc({ id: 'a', folderId: 'f1' }), doc({ id: 'b', folderId: 'f1' })]

  it('折叠后组内行**不进 DOM**（不是 CSS 隐藏）', () => {
    useStore.setState({ docs: twoInFolder(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)
    expect(rows(container).length).toBe(2)

    fireEvent.click(container.querySelector('[data-group-toggle="f:f1"]') as HTMLElement)
    // ⚠️ 对照（有区分力）：把折叠实现成给行加 `hidden` / `h-0 overflow-hidden`，
    // DOM 里仍是 2 行，本断言变红——而用户看到的效果一模一样。
    expect(rows(container).length).toBe(0)
    // 分组头本身留着（它是重新展开的入口）
    expect(container.querySelector('[data-group-toggle="f:f1"]')).toBeTruthy()
  })

  it('折叠状态跨重挂载保留（存在 prefs，不是组件本地 state）', () => {
    useStore.setState({ docs: twoInFolder(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelector('[data-group-toggle="f:f1"]') as HTMLElement)
    expect(readPrefs().collapsedGroups).toEqual(['f:f1'])

    cleanup()
    const c2 = render(<DocLibrary />).container
    expect(rows(c2).length).toBe(0)
  })

  it('再点一次恢复展开', () => {
    useStore.setState({ docs: twoInFolder(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)
    const toggle = () => fireEvent.click(container.querySelector('[data-group-toggle="f:f1"]') as HTMLElement)
    toggle(); toggle()
    expect(rows(container).length).toBe(2)
  })
})

describe('排列独立于筛选', () => {
  const mixed = () => [
    doc({ id: 'p1', sourceType: 'pdf' }),
    doc({ id: 'v1', sourceType: 'video' }),
  ]

  it('排列=按类型 + 筛选=PDF → 分组头在，且只列出 PDF', () => {
    seedPrefs({ groupBy: 'type' })
    useStore.setState({ docs: mixed(), folders: [], activeType: 'pdf' })
    const { container } = render(<DocLibrary />)

    // ⚠️ 对照（有区分力）：实现里写 `if (hasFilter) return flat`（「筛选时退化成扁平」），
    // 分组头消失、本断言红。这正是本轮刻意**不**做的联动。
    expect(container.querySelector('[data-doc-group="t:pdf"]')).toBeTruthy()
    expect(container.querySelector('[data-doc-group="t:video"]')).toBeNull()
    expect(rows(container).length).toBe(1)
    expect(rows(container)[0].textContent).toContain('文档 p1')
  })

  it('排列旋钮（平铺/按文件夹/按类型/按时间）都在，切到平铺后分组头消失', () => {
    useStore.setState({ docs: mixed(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)
    expect(container.querySelectorAll('[data-group-by]').length).toBe(4)
    expect(container.querySelectorAll('[data-doc-group]').length).toBeGreaterThan(0)

    fireEvent.click(container.querySelector('[data-group-by="none"]') as HTMLElement)
    expect(container.querySelectorAll('[data-doc-group]').length).toBe(0)
    expect(rows(container).length).toBe(2)
  })

  it('排列方式是持久化偏好：双向（写入 localStorage + 重挂载仍生效）', () => {
    useStore.setState({ docs: mixed(), folders: [] })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelector('[data-group-by="date"]') as HTMLElement)
    expect(readPrefs().groupBy).toBe('date')

    cleanup()
    const c2 = render(<DocLibrary />).container
    // 重挂载后仍按时间排列 → 出现时间分组头（今天）
    expect(c2.querySelector('[data-doc-group^="d:"]')).toBeTruthy()
  })
})

describe('密度（详细 / 简略）', () => {
  it('简略模式：筛选区小节**不进 DOM**，只留一行入口', () => {
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    expect(container.querySelectorAll('[data-filter-section]').length).toBeGreaterThan(0)

    fireEvent.click(container.querySelector('[data-density="compact"]') as HTMLElement)
    // ⚠️ 对照（有区分力）：把筛选区改成 `hidden`（省空间但 DOM 还在），
    // 数量仍 > 0，本断言红。
    expect(container.querySelectorAll('[data-filter-section]').length).toBe(0)
    expect(container.querySelector('[data-filter-popover-toggle]')).toBeTruthy()
    // 搜索框在两种模式下都在（简略模式它与筛选图标同一行）
    expect(container.querySelector('[data-search-input]')).toBeTruthy()
  })

  it('简略模式筛选浮层：点浮层外关闭走 **document 监听**（透明遮罩已删）', () => {
    // ⚠️ 判据是「点浮层**外**真的关得掉」。原实现用 `fixed inset-0` 透明遮罩，而本组件
    //    挂在文档库 `<aside>` 里、那个 aside 带 `backdrop-blur-[20px]` —— `backdrop-filter`
    //    会**创建包含块**，把 `fixed` 后代锁进面板自身的大小，于是遮罩只盖住侧栏那一条，
    //    点预览区关不掉浮层（模式 #67）。若改回遮罩，本条红。
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelector('[data-density="compact"]') as HTMLElement)
    fireEvent.click(container.querySelector('[data-filter-popover-toggle]') as HTMLElement)
    expect(container.querySelector('[data-filter-popover]')).toBeTruthy()

    // 原生 mousedown 派到 body（组件监听的是 document，不是 React 合成事件）
    fireEvent.mouseDown(document.body)
    expect(container.querySelector('[data-filter-popover]')).toBeNull()

    // 浮层**内部**的 mousedown 不关（按钮与浮层同在一个 rootRef 内，`contains` 划的内外界）
    fireEvent.click(container.querySelector('[data-filter-popover-toggle]') as HTMLElement)
    fireEvent.mouseDown(container.querySelector('[data-filter-popover]') as HTMLElement)
    expect(container.querySelector('[data-filter-popover]')).toBeTruthy()
  })

  const inFolders = () => [doc({ id: 'a', folderId: 'f1' }), doc({ id: 'b', folderId: 'f1' })]

  it('简略模式**固定按文件夹**分组（IDE 树），不跟随「排列方式」偏好', () => {
    // 偏好故意给「平铺」——简略模式要无视它
    seedPrefs({ density: 'compact', groupBy: 'none' })
    useStore.setState({ docs: inFolders(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)

    // ⚠️ 对照（有区分力）：保留旧实现 `density === 'compact' ? 'none' : groupBy`
    //（简略一律平铺）→ 分组头数为 0，本断言红。这正是 2026-09-25 推翻的设计。
    expect(container.querySelector('[data-group-header="f:f1"]')).toBeTruthy()
    expect(rows(container).length).toBe(2)
  })

  it('简略模式的子行缩进一层并带缩进参考线（分组头 = 父节点）', () => {
    seedPrefs({ density: 'compact', groupBy: 'folder' })
    useStore.setState({ docs: inFolders(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)

    // 两个组（工作 2 篇 + 未分类 0 篇），但**只有有行的组**才画缩进容器——
    // 空组画一条线却没有孩子，是纯噪音。
    // ⚠️ 对照（有区分力）：把缩进写在每行自己的 className 上（或 treeIndent 恒 false），
    // 缩进容器数为 0，本断言红——而「行看上去缩进了」这个视觉结果是一样的。
    expect(container.querySelectorAll('[data-tree-indent]').length).toBe(1)
    expect(container.querySelector('[data-tree-indent]')!.querySelectorAll('[data-doc-row]').length).toBe(2)
  })

  it('简略模式下分组头与文件行**同高**（不成树就不叫树）', () => {
    seedPrefs({ density: 'compact' })
    useStore.setState({ docs: inFolders(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)

    // ⚠️ 对照（有区分力）：不给 GroupHeader 传 `compact`（父节点比子节点矮 6px）→
    // 高度类一个是空串、一个是 h-6.5，本断言红——而单看截图「差不多高」看不出来。
    const h = (el: Element) => (el.className.match(/\bh-\d+(\.\d+)?/) || [''])[0]
    const headerH = h(container.querySelector('[data-group-compact]') as Element)
    const rowH = h(container.querySelector('[data-doc-row]') as Element)
    expect(rowH).not.toBe('')
    expect(headerH).toBe(rowH)
  })

  it('详细模式不缩进、分组头不加紧凑行高（两种密度各长各的）', () => {
    useStore.setState({ docs: inFolders(), folders: FOLDERS })
    const { container } = render(<DocLibrary />)
    expect(container.querySelector('[data-tree-indent]')).toBeNull()
    expect(container.querySelector('[data-group-compact]')).toBeNull()
  })

  it('冷启动骨架：简略模式是**单行条**，且空分组头不画', () => {
    seedPrefs({ density: 'compact' })
    useStore.setState({ docs: [], folders: [], docsLoading: true })
    const { container } = render(<DocLibrary />)

    // ⚠️ 对照（有区分力）：旧判据是 `!groups?.length`，而分组模式下 `groups` **永远非空**
    //（按文件夹分组至少有一个 0 篇的「未分类」），于是冷启动被跳过骨架、直接画出一个
    //「未分类 0」的头——正是 #19 要杀的那种假空态。改成「有内容可显示」才命中。
    expect(container.querySelector('[data-list-skeleton]')).toBeTruthy()
    expect(container.querySelectorAll('[data-doc-group]').length).toBe(0)
    expect(container.querySelector('.w-10.h-10')).toBeNull()   // 卡片骨架留给详细模式
  })

  it('确实没有文档时：只有空态文案，不画一个 0 篇的「未分类」头', () => {
    useStore.setState({ docs: [], folders: [], docsLoading: false })
    const { container } = render(<DocLibrary />)
    expect(container.textContent).toContain('还没有文档')
    // ⚠️ 对照（有区分力）：照旧无条件渲染 groups → 「未分类 0」这一行出现，本断言红。
    expect(container.querySelectorAll('[data-doc-group]').length).toBe(0)
  })

  it('详细模式的行含**摘要素材**（封面/类型图标块），简略模式没有', () => {
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    // 详细模式：整行高度靠卡片内容撑开，必有一个 40px 的缩略图块
    expect(container.querySelector('.w-10.h-10')).toBeTruthy()

    fireEvent.click(container.querySelector('[data-density="compact"]') as HTMLElement)
    expect(container.querySelector('.w-10.h-10')).toBeNull()
  })

  it('密度是持久化偏好：双向', () => {
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    render(<DocLibrary />)
    fireEvent.click(screen.getByText('简略'))
    expect(readPrefs().density).toBe('compact')

    cleanup()
    const c2 = render(<DocLibrary />).container
    expect(c2.querySelector('[data-density="compact"]')?.className).toContain('text-accent')
    expect(c2.querySelectorAll('[data-filter-section]').length).toBe(0)
  })
})

describe('筛选区 / 列表区之间的分隔条', () => {
  it('拖动**立即**落盘，且重挂载后高度仍是该值（读写双向）', () => {
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    const splitter = container.querySelector('[data-filter-splitter]') as HTMLElement
    expect(splitter).toBeTruthy()

    fireEvent.mouseDown(splitter, { clientY: 100 })
    fireEvent.mouseMove(document, { clientY: 160 })   // 监听挂在 document 上（鼠标必然移出细线）
    fireEvent.mouseUp(document)

    // 前半：写完要**立刻**在 localStorage 里（只 setState 不落盘的实现会红）
    expect(readPrefs().filterAreaHeight).toBe(236)     // 176 + delta 60

    cleanup()
    // 后半：重挂载后高度仍是 236（只落盘不读的实现会红）
    const c2 = render(<DocLibrary />).container
    const body = c2.querySelector('[data-filter-body]') as HTMLElement
    expect(body.style.height).toBe('236px')
  })

  it('拖动被钳制在允许区间内（不能拖成负数）', () => {
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    fireEvent.mouseDown(container.querySelector('[data-filter-splitter]') as HTMLElement, { clientY: 100 })
    fireEvent.mouseMove(document, { clientY: -500 })
    fireEvent.mouseUp(document)
    expect(readPrefs().filterAreaHeight).toBe(0)
  })

  it('筛选区收起时不渲染分隔条（没有配比可调）', () => {
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelector('[data-filter-area-toggle]') as HTMLElement)
    expect(container.querySelector('[data-filter-splitter]')).toBeNull()
    expect(container.querySelector('[data-filter-body]')).toBeNull()
  })

  it('双击分隔条复位到默认高度', () => {
    seedPrefs({ filterAreaHeight: 400 })
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    fireEvent.doubleClick(container.querySelector('[data-filter-splitter]') as HTMLElement)
    expect(readPrefs().filterAreaHeight).toBe(176)
  })
})

describe('active（预览目标）与 selected（批量目标）是两个维度', () => {
  const two = () => [doc({ id: 'a', createdAt: '2026-01-02T00:00:00' }), doc({ id: 'b', createdAt: '2026-01-01T00:00:00' })]

  it('预览 a、勾选 b → 两个标记落在**不同**元素上', () => {
    useStore.setState({ docs: two(), folders: [], currentDocId: 'a' })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelectorAll('[data-select-toggle]')[1] as HTMLElement)

    const activeEls = container.querySelectorAll('[data-active]')
    const selectedEls = container.querySelectorAll('[data-selected]')
    expect(activeEls.length).toBe(1)
    expect(selectedEls.length).toBe(1)
    // ⚠️ 对照（有区分力）：把 data-active 写成 `active || selected`（即恢复曾用过的
    // 「两者共用一套样式」），两处会指向同一行，本断言红。
    expect(activeEls[0]).not.toBe(selectedEls[0])
    expect(activeEls[0].textContent).toContain('文档 a')
    expect(selectedEls[0].textContent).toContain('文档 b')
  })

  it('两者可以叠加在同一行（正在看它、也勾了它）', () => {
    useStore.setState({ docs: two(), folders: [], currentDocId: 'a' })
    const { container } = render(<DocLibrary />)
    fireEvent.click(container.querySelectorAll('[data-select-toggle]')[0] as HTMLElement)

    const row = container.querySelector('[data-active]') as HTMLElement
    expect(row.getAttribute('data-selected')).toBe('true')
  })
})

describe('FTS 截断提示', () => {
  it('取回**恰好**上限条数 → 提示出现（判据是 `>=` 而不是 `>`）', async () => {
    searchMock.mockResolvedValue(
      Array.from({ length: 100 }, (_, i) => ({ id: `h${i}`, title: `t${i}`, snippet: 's' })))
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)

    fireEvent.change(container.querySelector('[data-search-input]') as HTMLElement, { target: { value: '关键词' } })
    // ⚠️ 对照（有区分力）：判据写成 `hits.length > SEARCH_LIMIT` 时，恰好 100 条不触发提示，
    // 本用例红——而后端按 limit 返回，**上限就是** limit 条，`>` 一次都不会成立。
    await waitFor(
      () => expect(container.querySelector('[data-search-truncated]')).toBeTruthy(),
      { timeout: 2000 })
  })

  it('没到上限时不提示（不是「一搜就提示」）', async () => {
    searchMock.mockResolvedValue([{ id: 'h0', title: 't', snippet: 's' }])
    useStore.setState({ docs: [doc({ id: 'a' })], folders: [] })
    const { container } = render(<DocLibrary />)
    fireEvent.change(container.querySelector('[data-search-input]') as HTMLElement, { target: { value: '关键词' } })

    await waitFor(() => expect(searchMock).toHaveBeenCalled(), { timeout: 2000 })
    expect(container.querySelector('[data-search-truncated]')).toBeNull()
  })
})

describe('计数同源', () => {
  it('分组头徽标读的是**全量**文件夹计数，不是筛选后的组内条数', () => {
    // f1 里有 3 篇，但筛选只留 PDF 的 1 篇
    useStore.setState({
      docs: [
        doc({ id: 'p', folderId: 'f1', sourceType: 'pdf' }),
        doc({ id: 'v1', folderId: 'f1', sourceType: 'video' }),
        doc({ id: 'v2', folderId: 'f1', sourceType: 'video' }),
      ],
      folders: FOLDERS, activeType: 'pdf',
    })
    const { container } = render(<DocLibrary />)
    // ⚠️ 徽标若是筛选后的数（`group.docs.length` = 1），删除文件夹的确认文案就会说
    // 「其中 1 个文档将移到未分类」，而实际搬走 3 篇——用户按 1 篇的心理预期点下去。
    const header = container.querySelector('[data-group-header="f:f1"]') as HTMLElement
    expect(header.textContent).toContain('3')
  })
})

describe('索引健康横幅的**接线**', () => {
  /**
   * ⚠️ 这条守的是「组件写好了、也测过了，但根本没挂上去」——那种情况下
   * `searchHealth.test.tsx` 全绿、用户却永远看不到横幅。判据只能落在
   * 「渲染 `DocLibrary` 之后它里面有没有这段文字」上。
   */
  it('索引不可用 → 横幅出现在文档库顶部，并给出原因', async () => {
    statusMock.mockResolvedValue({
      available: false, docCount: 0, sizeBytes: 0,
      lastError: '索引库打不开：malformed', rebuiltAt: null,
    })
    render(<DocLibrary />)
    await waitFor(() => expect(screen.getByText('全文检索不可用')).toBeInTheDocument())
    expect(screen.getByText(/malformed/)).toBeInTheDocument()
  })

  it('索引正常 → 顶部没有任何横幅（零回归）', async () => {
    render(<DocLibrary />)
    // 等一次挂载探测落地，再断言「探测确实发生了、但什么都没渲染」
    await waitFor(() => expect(statusMock).toHaveBeenCalled())
    expect(screen.queryByText('全文检索不可用')).toBeNull()
  })

  /**
   * 搜索撞上「索引不可用」时**必须强制刷新**健康状态 —— 否则横幅只在启动那一次
   * 探过，用户搜索失败、横幅却仍显示「正常」，两处说法打架。
   *
   * ⚠️ 这条守的是一个**曾经恒假**的判据：旧实现是
   * `String(e).includes('503')`，而 `apiFetch` 抛的 `ApiErrorException.message`
   * 取的是后端 `message` 字段（「索引不可用 —— 可在设置里重建」），**从来不含 503**
   * ⇒ 分支永不进入、横幅永不浮出，而当时的单测只断言「横幅能渲染」、测不到这个。
   */
  it('搜索撞上 503（ApiErrorException）→ 强制刷新后横幅浮出', async () => {
    // 首次（挂载探测）说正常，强制刷新时才拿到「坏了」——两次答复必须不同，
    // 否则「刷新过没有」无从分辨（横幅本来就会因为首次结果出现）。
    statusMock.mockResolvedValueOnce(STATUS_OK)
    statusMock.mockResolvedValue({
      available: false, docCount: 0, sizeBytes: 0,
      lastError: '索引库已损坏：database disk image is malformed', rebuiltAt: null,
    })
    // message 里**刻意不含 503**：判据只能读 apiError.status / code。
    searchMock.mockRejectedValue(new ApiErrorException({
      code: 'QUERY_INDEX_UNAVAILABLE',
      message: '全文索引不可用 —— 可在 设置 → 通用 → 检索索引 里重建',
      recoverable: true, status: 503,
    }))
    const { container } = render(<DocLibrary />)

    await waitFor(() => expect(statusMock).toHaveBeenCalled(), { timeout: 2000 })
    expect(screen.queryByText('全文检索不可用')).toBeNull()

    fireEvent.change(container.querySelector('[data-search-input]') as HTMLElement, {
      target: { value: '关键词' },
    })

    await waitFor(() => expect(screen.getByText('全文检索不可用')).toBeInTheDocument(), { timeout: 2000 })
  })
})
