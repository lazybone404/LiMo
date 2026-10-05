/**
 * 单击 / 双击打开语义 —— 从文档库行到标签意图的接线。
 *
 * 两条语义：**单击 = 预览标签**（可被下一次单击替换）、**双击 = 常驻标签**。
 * 难点不在语义本身，而在「双击**不该**从哪里冒上来」——行内有 6 个 `<button>` +
 * 3 个 `<input>`，双击复选框、双击标题输入框都是极自然的动作。
 *
 * 判据写在这两处而不是逐个给子元素补 `stopPropagation`：**枚举必漏**，而
 * `onClick` 已补过 `stopPropagation` 的地方反而更危险（`dblclick` 是独立事件，
 * 完全不受 `click` 的 handler 影响，却会给人一种「已经处理过」的错觉）。
 *
 * ⚠️ 两种密度**都要测**：紧凑分支的行根 div 曾漏掉 `onDoubleClick` 接线
 * （只有详细分支接上了），只测默认密度会让「简略模式双击没反应」一路绿着上线。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'

class IO {
  observe() {}
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
    batchTranscripts: vi.fn(),
    createFolder: vi.fn(),
    renameFolder: vi.fn(),
    deleteFolder: vi.fn(),
    updateTranscript: vi.fn(),
    getMarkdown: vi.fn(),
  }
})

const { DocLibrary } = await import('../components/doc-library/DocLibrary')

const S = () => useStore.getState()

function makeDocs(n: number): DocEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `d${i}`, title: `文档 ${i}`, sourceType: 'pdf', engine: 'pypdfium2',
    contentHash: `h${i}`, tags: [],
    createdAt: `2026-01-01T00:00:${String(59 - i).padStart(2, '0')}`,
  }))
}

const rowAt = (c: HTMLElement, i = 0) => c.querySelectorAll('[data-doc-row]')[i] as HTMLElement
const toggleAt = (c: HTMLElement, i = 0) => c.querySelectorAll('[data-select-toggle]')[i] as HTMLElement

function seedAndRender(density: 'detailed' | 'compact', n = 2) {
  localStorage.setItem('mbw_docLibPrefs', JSON.stringify({ density }))
  useStore.setState({ docs: makeDocs(n) })
  return render(<DocLibrary />)
}

beforeEach(() => {
  localStorage.clear()
  useStore.setState({
    docs: [], folders: [], currentDocId: null, docTabs: [],
    pendingOpenMode: null, pendingClose: null,
    selectedIds: new Set(), listLimit: 60, docsLoading: false, toasts: [],
  })
})

afterEach(() => cleanup())

/** 两种密度跑同一组判据 —— 紧凑分支单独漏接线的那次就是靠这个抓到的 */
describe.each(['detailed', 'compact'] as const)('打开语义（%s 密度）', (density) => {
  it('单击行 → 预览标签意图 + currentDocId 切过去', () => {
    const { container } = seedAndRender(density)
    fireEvent.click(rowAt(container))
    expect(S().currentDocId).toBe('d0')
    expect(S().pendingOpenMode).toBe('preview')
  })

  it('双击行 → 常驻标签意图', () => {
    const { container } = seedAndRender(density)
    fireEvent.doubleClick(rowAt(container))
    expect(S().currentDocId).toBe('d0')
    expect(S().pendingOpenMode).toBe('pinned')
  })

  it('⚠️ 双击复选框 → 不产生任何标签意图', () => {
    // 对照：守卫 `closest('button,…')` 漏掉 → 复选框双击冒泡成「钉标签」→ 红
    const { container } = seedAndRender(density)
    fireEvent.doubleClick(toggleAt(container))
    expect(S().pendingOpenMode).toBe(null)
    expect(S().docTabs).toEqual([])
  })

  it('⚠️ 双击标题输入框 → 不产生任何标签意图（最典型的漏网）', () => {
    // 「双击选中一个词」是输入框里最自然的动作。若改成给每个子元素补
    // stopPropagation 的写法，这条会红。
    const { container } = seedAndRender(density)
    fireEvent.click(container.querySelector('[title="编辑标题"]') as HTMLElement)

    const input = container.querySelector('[data-doc-row] input') as HTMLInputElement
    expect(input).toBeTruthy()          // 编辑态确实进去了，否则这条测试是空转
    fireEvent.doubleClick(input)

    expect(S().pendingOpenMode).toBe(null)
    expect(S().docTabs).toEqual([])
  })

  it('带修饰键的双击 → 不钉标签（让位给系统行为）', () => {
    const { container } = seedAndRender(density)
    fireEvent.doubleClick(rowAt(container), { ctrlKey: true })
    expect(S().pendingOpenMode).toBe(null)
    fireEvent.doubleClick(rowAt(container), { shiftKey: true })
    expect(S().pendingOpenMode).toBe(null)
  })

  it('真实事件序列 click→click→dblclick：最终常驻，且只有一个标签', () => {
    // 浏览器双击会先派发两次 click。第二次 click 时文档已在组内 → no-op，
    // 所以「双击钉住」不需要任何防抖。
    const { container } = seedAndRender(density)
    const row = rowAt(container)

    fireEvent.click(row); S().commitTabChange('d0')     // 第一次 click（含 loadDocContent 提交）
    fireEvent.click(row); S().commitTabChange('d0')     // 第二次 click
    fireEvent.doubleClick(row); S().commitTabChange('d0')

    expect(S().docTabs).toEqual([{ docId: 'd0', pinned: true }])
  })
})

describe('打开的标签与列表状态的关系', () => {
  it('单击不改选择集（打开与多选是两个正交维度）', () => {
    useStore.setState({ selectedIds: new Set(['d1']) })
    const { container } = seedAndRender('detailed')
    fireEvent.click(rowAt(container, 0))
    expect([...S().selectedIds]).toEqual(['d1'])
  })

  it('点已打开的文档 → 切过去，不新增标签', () => {
    const { container } = seedAndRender('detailed', 3)
    fireEvent.click(rowAt(container, 0)); S().commitTabChange('d0')
    fireEvent.click(rowAt(container, 1)); S().commitTabChange('d1')
    fireEvent.click(rowAt(container, 0)); S().commitTabChange('d0')
    // d0 是预览槽，被 d1 顶掉后又点回来 —— 仍是预览槽，且组内只有 1 个
    expect(S().docTabs).toEqual([{ docId: 'd0', pinned: false }])
  })

  it('右键菜单「打开」→ 常驻标签（显式动作 = 常驻，不是会被顶掉的预览槽）', () => {
    const { container } = seedAndRender('detailed')
    fireEvent.contextMenu(rowAt(container, 0), { clientX: 5, clientY: 5 })
    // ContextMenu 走 Portal 到 body
    fireEvent.click(screen.getByText('打开'))
    expect(S().currentDocId).toBe('d0')
    expect(S().pendingOpenMode).toBe('pinned')
  })
})
