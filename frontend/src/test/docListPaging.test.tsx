/**
 * 分批加载的两条回归 —— 都属于「静默失效」：跑起来不报任何错，只是列表再也不动。
 *
 * 1. **哨兵元素必须被重新观察**。简略 / 详细是两条不同的 JSX 分支，切换密度时
 *    `DocList` 整个重挂载，哨兵与滚动容器都换成了新 DOM。若 observer 的 effect
 *    只依赖 `[hasMore, limit]`，它不会重跑 —— 旧 observer 继续盯着一个已从文档
 *    移除的元素，交叉事件永不触发：列表永远停在第一屏的 60 行，**零报错**。
 *    修法是把「元素是谁」变成 effect 的依赖（callback ref + state）。
 *
 * 2. **哨兵必须落在截断点**（第一个还有行没画出来的组）之后。挂在列表最末尾时，
 *    用户滚到底触发加载，新行是插在**前面某个组内部**的（视口上方），配合浏览器的
 *    滚动锚定眼前画面一个字都不变 —— 用户得反复滚 N 次才到底，体感是「翻不到底」。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'

/** 记录每次 `observe` 的元素 —— 断言「观察的是不是当前文档里的那个」 */
let observed: Element[] = []

class IO {
  constructor(_cb: (entries: Array<{ isIntersecting: boolean }>) => void) { /* 不自动触发 */ }
  observe(el: Element) { observed.push(el) }
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
;(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = IO

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
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
const { listFolders } = await import('../api/client')

function makeDocs(n: number, folderId = ''): DocEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `${folderId || 'u'}-${i}`,
    title: `${folderId || '未分类'} 文档 ${i}`,
    sourceType: 'pdf',
    engine: 'pypdfium2',
    contentHash: `h${folderId}${i}`,
    tags: [],
    createdAt: `2026-01-01T00:00:${String(i % 60).padStart(2, '0')}`,
    folderId,
  }))
}

beforeEach(() => {
  observed = []
  localStorage.clear()
  vi.mocked(listFolders).mockResolvedValue([])
  useStore.setState({
    docs: [], folders: [], currentDocId: null, toasts: [], selectedIds: new Set(),
    listLimit: 60, docsLoading: false,
    searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
  })
})

afterEach(() => cleanup())

describe('分批哨兵', () => {
  it('切换密度（DocList 重挂载）后，observer 重新观察新元素', () => {
    useStore.setState({ docs: makeDocs(250) })
    const { container } = render(<DocLibrary />)

    // 初始就位：观察的必须是当前文档里的元素
    expect(observed.length).toBeGreaterThan(0)
    expect(observed[observed.length - 1].isConnected).toBe(true)

    const before = observed.length
    fireEvent.click(container.querySelector('[data-density="compact"]')!)

    // 修复前：effect 依赖没变 → 不重新 observe → observed 不增长，
    // 且最后一个元素是被卸载的旧哨兵（`isConnected === false`）
    expect(observed.length).toBeGreaterThan(before)
    expect(observed[observed.length - 1].isConnected).toBe(true)
  })

  it('分组模式下哨兵跟在被预算截断的组之后，而不是列表末尾', () => {
    const folders = [
      { id: 'f1', name: 'A', createdAt: '2026-01-01', count: 100 },
      { id: 'f2', name: 'B', createdAt: '2026-01-01', count: 100 },
    ]
    vi.mocked(listFolders).mockResolvedValue(folders)
    useStore.setState({ folders, docs: [...makeDocs(100, 'f1'), ...makeDocs(100, 'f2')] })
    const { container } = render(<DocLibrary />)

    const groups = [...container.querySelectorAll('[data-doc-group]')]
    const sentinel = container.querySelector('[data-doc-sentinel]')
    expect(sentinel).not.toBeNull()

    // A 组（第一组）有 100 篇、预算只有 60 → 截断点就在 A 组内部。
    // 修复前哨兵是列表的最后一个子节点，`closest('[data-doc-group]')` 为 null。
    expect(sentinel!.closest('[data-doc-group]')).toBe(groups[0])
  })
})
