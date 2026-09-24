/**
 * 文档库组件测试 —— 分批渲染上限 / 全选跨分批 / 批量删除的取消顺序。
 *
 * 这三条都是「跑起来看着没问题、只在数据量或特定顺序下才出错」的那一类：
 * - 渲染上限：300 篇全画出来当时不报错，只是卡；
 * - 全选跨分批：分批后「全选」若只选已渲染的 60 条，用户批量删 300 篇只删掉 60 篇；
 * - 取消顺序：`clearDocTasks` 先于 `cancelTask` 会让任务取消不掉，文档被删后
 *   后台任务继续为该 docId 落盘（孤儿记录 / 文档「复活」），且**不报任何错**。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'

// jsdom 没有 IntersectionObserver —— 空实现（不触发回调）正好让渲染深度停在首页
class IO {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
;(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = IO

const cancelTaskMock = vi.fn()
vi.mock('../api/taskManager', () => ({
  cancelTask: (id: string) => cancelTaskMock(id),
}))

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

const { DocLibrary } = await import('../components/doc-library/DocLibrary')
const { getMarkdown, createFolder } = await import('../api/client')

function makeDocs(n: number): DocEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `d${i}`, title: `文档 ${i}`, sourceType: 'pdf', engine: 'pypdfium2',
    contentHash: `h${i}`, tags: [], createdAt: `2026-01-01T00:00:${String(i % 60).padStart(2, '0')}`,
  }))
}

beforeEach(() => {
  cancelTaskMock.mockClear()
  batchMock.mockClear()
  batchMock.mockResolvedValue({ status: 'ok', affected: 1, failed: [] })
  vi.mocked(getMarkdown).mockReset()
  vi.mocked(createFolder).mockReset()
  // toast 是 store 里的数据（addToast 有 3s 自动清除的 setTimeout），
  // 用例之间必须显式清空，否则上一条的 toast 会污染下一条的断言。
  // 选择与分页深度现在也活在 store 里（面板折叠不丢），**同样必须逐条重置**——
  // store 是模块级单例，上一条测试勾中/滚过的状态会原样带进下一条。
  useStore.setState({
    toasts: [], selectedIds: new Set(), listLimit: 60, docsLoading: false,
  })
})

afterEach(() => cleanup())

describe('DocLibrary — 分批渲染', () => {
  it('300 篇文档只渲染 PAGE_SIZE（60）行', () => {
    useStore.setState({ docs: makeDocs(300), folders: [], currentDocId: null })
    const { container } = render(<DocLibrary />)
    // ⚠️ 对照：去掉 slice 限制后此处会变成 300，本用例即红。
    expect(container.querySelectorAll('[data-doc-row]').length).toBe(60)
  })

  it('文档不足 60 篇时全部渲染（分批不丢行）', () => {
    useStore.setState({ docs: makeDocs(7), folders: [], currentDocId: null })
    const { container } = render(<DocLibrary />)
    expect(container.querySelectorAll('[data-doc-row]').length).toBe(7)
  })
})

describe('DocLibrary — 全选跨分批', () => {
  it('全选 = 当前筛选的全部匹配项，不只是已渲染的 60 条', () => {
    useStore.setState({
      docs: makeDocs(300), folders: [], currentDocId: null,
      searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
    })
    const { container } = render(<DocLibrary />)
    expect(container.querySelectorAll('[data-doc-row]').length).toBe(60)

    // 勾中任意一行 → 批量工具条自己出现（已无「批量模式」按钮；一条都没勾时
    // 想让工具条出现，路径是列表内 Ctrl+A，不是常驻一个模式开关）
    fireEvent.click(container.querySelector('[data-select-toggle]') as HTMLElement)
    fireEvent.click(screen.getByTitle(/全选当前筛选结果/))

    // ⚠️ 判据是 300 而不是 60：分批渲染只是「少画 DOM」，数据仍是 300 条。
    // 对照：把 setSelectionAll 改成只收 rendered 的 id，此处会显示「已选 60 项」。
    expect(screen.getByText(/已选 300 项/)).toBeInTheDocument()
    // 选中数 > 已渲染条数时必须明示「含未显示项」，否则用户以为只选中了看得见的那些
    expect(screen.getByText(/含未显示项/)).toBeInTheDocument()
  })
})

describe('DocLibrary — 批量删除的取消顺序', () => {
  it('cancelTask 必须早于 clearDocTasks（反之任务取消不掉）', async () => {
    const titles: string[] = []
    const cancelSpy = vi.fn((id: string) => { titles.push(`cancel:${id}`) })
    const clearSpy = vi.fn((id: string) => { titles.push(`clear:${id}`) })
    cancelTaskMock.mockImplementation(cancelSpy)
    // clearDocTasks 是 store 方法，替换成 spy 才能比较调用顺序
    useStore.setState({ clearDocTasks: clearSpy })

    // 显式构造两篇：d0 更新（date 排序后一定在第一行），任务挂在 d0 上
    const two: DocEntry[] = [
      { id: 'd0', title: '有任务的', sourceType: 'pdf', engine: 'e', contentHash: 'h0', tags: [], createdAt: '2026-01-02T00:00:00' },
      { id: 'd1', title: '无任务的', sourceType: 'pdf', engine: 'e', contentHash: 'h1', tags: [], createdAt: '2026-01-01T00:00:00' },
    ]
    useStore.setState({
      docs: two, folders: [], currentDocId: null,
      processingTasks: { d0__generate: { kind: 'generate' } },
    })
    const { container } = render(<DocLibrary />)

    // 点第一行的删除按钮 → 确认弹窗 → 删除
    const deleteBtn = container.querySelectorAll('[data-doc-row]')[0]!.querySelector('[title="删除"]') as HTMLElement
    fireEvent.click(deleteBtn)
    // 弹窗按钮用 data-confirm-delete 定位：行内的删除按钮 title 也叫「删除」，
    // 按 accessible name 取会命中多个。
    fireEvent.click(container.querySelector('[data-confirm-delete]') as HTMLElement)

    await vi.waitFor(() => expect(batchMock).toHaveBeenCalled())

    // ⚠️ 只断言「两个都被调用过」不足以证明顺序 —— 先清后取消时两个也都调用了，
    // 只是 abort 找不到句柄。故用调用序判定。
    // 对照（已验证有区分力）：把 cancelDocTasks 里的两行交换后本用例变红。
    expect(cancelSpy.mock.invocationCallOrder[0])
      .toBeLessThan(clearSpy.mock.invocationCallOrder[0])
    expect(titles).toEqual(['cancel:d0', 'clear:d0'])
    // 只有真的有任务的文档才需要取消
    expect(cancelSpy).toHaveBeenCalledTimes(1)
  })
})

// ─────────────────────────────────────────────────────────────
// Phase 0 修复项 —— 都是「静默做错事」而非「抛错」的那一类
// ─────────────────────────────────────────────────────────────

describe('DocLibrary — 移动的部分失败', () => {
  const twoInFolder = (): DocEntry[] => [
    { id: 'd0', title: '失败的', sourceType: 'pdf', engine: 'e', contentHash: 'h0', tags: [], createdAt: '2026-01-02T00:00:00', folderId: 'f1' },
    { id: 'd1', title: '成功的', sourceType: 'pdf', engine: 'e', contentHash: 'h1', tags: [], createdAt: '2026-01-01T00:00:00', folderId: 'f1' },
  ]

  it('只回滚失败的，成功的不被一起退回', async () => {
    // 服务端：d1 移动成功、d0 失败（affected + failed = 总数，不触发兜底重拉）
    batchMock.mockResolvedValue({ status: 'partial', affected: 1, failed: [{ id: 'd0', reason: 'x' }] })
    useStore.setState({
      docs: twoInFolder(), currentDocId: null, activeFolderId: null,
      folders: [{ id: 'f2', name: '目标夹', createdAt: '2026-01-01T00:00:00', count: 0 }],
    })
    const { container } = render(<DocLibrary />)

    fireEvent.click(container.querySelectorAll('[data-select-toggle]')[0] as HTMLElement)
    fireEvent.click(screen.getByTitle(/全选当前筛选结果/))
    fireEvent.click(screen.getByTitle('移动到文件夹'))
    // ⚠️ 按 `data-move-target`（id）而非文案定位：文件夹允许重名，且分组头
    // 里同时渲染着同名的一行，`getByText('目标夹')` 会命中多个元素。
    fireEvent.click(container.querySelector('[data-move-target="f2"]') as HTMLElement)
    await vi.waitFor(() => expect(batchMock).toHaveBeenCalled())
    expect(batchMock).toHaveBeenCalledWith({ action: 'move', ids: ['d0', 'd1'], folderId: 'f2' })

    const byId = (id: string) => useStore.getState().docs.find(d => d.id === id)!
    // ⚠️ 对照（已验证有区分力）：把回滚写成整体 `setDocs(prev)`，d1 会一起退回 f1，本行变红。
    // 那不是「保守」而是**错的**——服务端已经改了，用户下次刷新会看到文档跳回原处。
    expect(byId('d0').folderId).toBe('f1')
    expect(byId('d1').folderId).toBe('f2')
  })

  it('移动成功后给出反馈（此前成功路径完全静默）', async () => {
    batchMock.mockResolvedValue({ status: 'ok', affected: 2, failed: [] })
    useStore.setState({
      docs: twoInFolder(), currentDocId: null, activeFolderId: null,
      folders: [{ id: 'f2', name: '目标夹', createdAt: '2026-01-01T00:00:00', count: 0 }],
    })
    const { container } = render(<DocLibrary />)

    fireEvent.click(container.querySelectorAll('[data-select-toggle]')[0] as HTMLElement)
    fireEvent.click(screen.getByTitle(/全选当前筛选结果/))
    fireEvent.click(screen.getByTitle('移动到文件夹'))
    fireEvent.click(container.querySelector('[data-move-target="f2"]') as HTMLElement)

    // ⚠️ 断言的是 store 而不是 DOM：`<Toast />` 挂在 App 上，单独 render DocLibrary
    // 时它根本不在树里——用 getByText 找 toast 永远是「找不到」，与实现对不对无关。
    await vi.waitFor(() => {
      const msgs = useStore.getState().toasts.map(t => t.message)
      expect(msgs.some(m => m.includes('已移动 2 个到「目标夹」'))).toBe(true)
    })
    // ⚠️ 对照（已验证有区分力）：去掉成功分支的 addToast，本用例红（改之前就是这样——
    // 成功路径完全静默，用户点完不知道到底动没动）。
  })
})

describe('DocLibrary — 文件夹操作失败', () => {
  it('新建失败时保留输入框与草稿（不把用户打的字弄丢）', async () => {
    vi.mocked(createFolder).mockRejectedValue(new Error('网络错误'))
    useStore.setState({ docs: [], folders: [], currentDocId: null })
    render(<DocLibrary />)

    fireEvent.click(screen.getByTitle('新建文件夹'))
    const input = screen.getByPlaceholderText('文件夹名') as HTMLInputElement
    fireEvent.change(input, { target: { value: '我的分类' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await vi.waitFor(() => expect(createFolder).toHaveBeenCalled())

    // ⚠️ 对照（已验证有区分力）：恢复「调用前 setCreating(false); setDraft('')」，
    // 输入框连同草稿一起消失，这里 getByPlaceholderText 直接抛错。
    const after = screen.getByPlaceholderText('文件夹名') as HTMLInputElement
    expect(after.value).toBe('我的分类')
  })

  it('新建成功后输入框才收起', async () => {
    vi.mocked(createFolder).mockResolvedValue(undefined as never)
    useStore.setState({ docs: [], folders: [], currentDocId: null })
    render(<DocLibrary />)

    fireEvent.click(screen.getByTitle('新建文件夹'))
    const input = screen.getByPlaceholderText('文件夹名')
    fireEvent.change(input, { target: { value: '已建成' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await vi.waitFor(() => expect(screen.queryByPlaceholderText('文件夹名')).toBeNull())
  })
})

describe('DocLibrary — 右键复制 Markdown', () => {
  it('复制的是**右键那一篇**的内容，不是当前预览的那一篇', async () => {
    vi.mocked(getMarkdown).mockResolvedValue({ content: 'D1 的笔记' } as never)
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const two: DocEntry[] = [
      { id: 'd0', title: '预览中的', sourceType: 'pdf', engine: 'e', contentHash: 'h0', tags: [], createdAt: '2026-01-02T00:00:00' },
      { id: 'd1', title: '被右键的', sourceType: 'pdf', engine: 'e', contentHash: 'h1', tags: [], createdAt: '2026-01-01T00:00:00' },
    ]
    // 全局预览停在 d0 —— 这正是缺陷触发条件：右键 d1 却复制出 d0 的正文
    useStore.setState({ docs: two, folders: [], currentDocId: 'd0', currentContent: 'D0 的预览内容' })
    const { container } = render(<DocLibrary />)

    // date 降序 → 行序 d0, d1
    const row1 = container.querySelectorAll('[data-doc-row]')[1] as HTMLElement
    fireEvent.contextMenu(row1)
    fireEvent.click(screen.getByText('复制 Markdown'))

    await vi.waitFor(() => expect(writeText).toHaveBeenCalled())
    // ⚠️ 对照（已验证有区分力）：改回读 `useStore.getState().currentContent`，
    // getMarkdown 根本不会被调用（waitFor 超时），写进剪贴板的会是「D0 的预览内容」。
    expect(getMarkdown).toHaveBeenCalledWith('d1', 'notes')
    expect(writeText).toHaveBeenCalledWith('D1 的笔记')
  })
})
