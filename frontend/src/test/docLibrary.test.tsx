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
    createFolder: vi.fn(),
    renameFolder: vi.fn(),
    deleteFolder: vi.fn(),
    updateTranscript: vi.fn(),
  }
})

const { DocLibrary } = await import('../components/doc-library/DocLibrary')

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

    // 进入批量模式（此时一条都没勾，工具条也必须出现——否则「全选」无从点起）
    fireEvent.click(screen.getByTitle('批量操作'))
    fireEvent.click(screen.getByTitle(/全选当前筛选结果/))

    // ⚠️ 判据是 300 而不是 60：分批渲染只是「少画 DOM」，数据仍是 300 条。
    // 对照：把 toggleSelectAll 改成只收 rendered 的 id，此处会显示「已选 60 项」。
    expect(screen.getByText(/已选 300 项/)).toBeInTheDocument()
    // 选中数 > 已渲染条数时必须明示「含未显示项」，否则用户以为只选中了看得见的那些
    expect(screen.getByText(/含未显示项/)).toBeInTheDocument()
  })

  it('选择模式下点行 = 切换勾选，不进详情', () => {
    useStore.setState({ docs: makeDocs(3), folders: [], currentDocId: null })
    const { container } = render(<DocLibrary />)
    fireEvent.click(screen.getByTitle('批量操作'))
    const row = container.querySelectorAll('[data-doc-row]')[0] as HTMLElement
    fireEvent.click(row)
    expect(screen.getByText(/已选 1 项/)).toBeInTheDocument()
    expect(useStore.getState().currentDocId).toBeNull()
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
