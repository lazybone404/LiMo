/**
 * 文档库选择模型 —— 常态多选（IDE 式），**没有「批量模式」这个状态**。
 *
 * 这里守的是本轮最该先定死的一条：**单击行 = 打开文档，不改选择集**。
 * 把单击实现成 toggle（「点即选中」）读起来完全合理、写出来也自洽，
 * 但它把文档库唯一的主流程弄没了 —— 用户从此打不开任何文档。
 * 故每一条判据都要能通过「换成那个看起来更合理的实现会不会红」来证明。
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

/**
 * ⚠️ 时间**递减**：列表默认按时间降序，这样 d0 必定落在首行 ——
 * 用例里的「第 N 行」才能直接读作 dN，不必心算排序。
 */
function makeDocs(n: number): DocEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `d${i}`, title: `文档 ${i}`, sourceType: 'pdf', engine: 'pypdfium2',
    contentHash: `h${i}`, tags: [],
    createdAt: `2026-01-01T00:00:${String(59 - i).padStart(2, '0')}`,
  }))
}

const rowOf = (c: HTMLElement, i: number) => c.querySelectorAll('[data-doc-row]')[i] as HTMLElement
const toggleOf = (c: HTMLElement, i: number) => c.querySelectorAll('[data-select-toggle]')[i] as HTMLElement
const selected = () => [...useStore.getState().selectedIds].sort()

beforeEach(() => {
  useStore.setState({
    docs: [], folders: [], currentDocId: null,
    selectedIds: new Set(), listLimit: 60, docsLoading: false, toasts: [],
  })
})

afterEach(() => cleanup())

describe('选择模型 — 单击与修饰键', () => {
  it('单击行 = 打开文档，且**不改变**选择集', () => {
    useStore.setState({ docs: makeDocs(3), selectedIds: new Set(['d1']) })
    const { container } = render(<DocLibrary />)

    fireEvent.click(rowOf(container, 0))

    expect(useStore.getState().currentDocId).toBe('d0')
    // ⚠️ 对照（已验证有区分力）：把 onClick 换成 `selectMode ? toggle : open` 的
    // 「点即选中」实现，此处选择集变成 [d0, d1]，且用户再也打不开文档。
    expect(selected()).toEqual(['d1'])
  })

  it('store 上不存在 selectMode（载体不存在，就不可能与选择集漂移）', () => {
    render(<DocLibrary />)
    // ⚠️ 对照（已验证有区分力）：保留 selectMode 但让它跟随 `size > 0`（「看起来等价」），
    // 这条断言 store 上**无该键**依然红 —— 漂移的可能性还在。
    expect('selectMode' in useStore.getState()).toBe(false)
  })

  it('批量工具条由选中数**派生**：勾一条出现，取消到 0 消失', () => {
    useStore.setState({ docs: makeDocs(3) })
    const { container } = render(<DocLibrary />)

    expect(screen.queryByTitle('取消选择')).toBeNull()
    fireEvent.click(toggleOf(container, 0))
    expect(screen.getByText(/已选 1 项/)).toBeInTheDocument()

    fireEvent.click(screen.getByTitle('取消选择'))
    expect(screen.queryByText(/已选 1 项/)).toBeNull()
  })

  it('Ctrl/Cmd 点行 = 加选，且**不打开**文档', () => {
    useStore.setState({ docs: makeDocs(3) })
    const { container } = render(<DocLibrary />)

    fireEvent.click(rowOf(container, 2), { ctrlKey: true })

    expect(selected()).toEqual(['d2'])
    // ⚠️ 对照（已验证有区分力）：只处理选择而忘了短路，ctrl+点会一路走到 onOpen ——
    // 用户每次加选都顺带把预览切走。
    expect(useStore.getState().currentDocId).toBeNull()
  })

  it('Shift 点行 = 锚点到该行的**区间**（不是只加选一个）', () => {
    useStore.setState({ docs: makeDocs(6) })
    const { container } = render(<DocLibrary />)

    fireEvent.click(rowOf(container, 0), { ctrlKey: true })   // 建立锚点 d0
    fireEvent.click(rowOf(container, 4), { shiftKey: true })  // 区间 d0..d4

    expect(selected()).toEqual(['d0', 'd1', 'd2', 'd3', 'd4'])
    // ⚠️ 对照（已验证有区分力）：只实现「加选」不实现「区间」，这里会显示 2 项。
  })

  it('Esc 清空选择', () => {
    useStore.setState({ docs: makeDocs(3), selectedIds: new Set(['d0', 'd1']) })
    const { container } = render(<DocLibrary />)

    fireEvent.keyDown(container.querySelector('[data-doc-list]') as HTMLElement, { key: 'Escape' })

    expect(selected()).toEqual([])
  })
})

describe('选择模型 — Ctrl+A 的领地', () => {
  it('焦点在列表上 → 全选当前结果集（跨分批）', () => {
    useStore.setState({ docs: makeDocs(300) })
    const { container } = render(<DocLibrary />)
    expect(container.querySelectorAll('[data-doc-row]').length).toBe(60)

    fireEvent.keyDown(container.querySelector('[data-doc-list]') as HTMLElement, { key: 'a', ctrlKey: true })

    // 300 而不是 60：分批只是少画 DOM
    expect(useStore.getState().selectedIds.size).toBe(300)
  })

  it('焦点在列表内的输入框里 → 让给浏览器（不劫持「全选文本」）', () => {
    useStore.setState({ docs: makeDocs(3) })
    const { container } = render(<DocLibrary />)

    // 进入标题内联编辑 —— 这个 input 在列表容器**内部**，keydown 会冒泡到列表处理器
    fireEvent.click(rowOf(container, 0).querySelector('[title="编辑标题"]') as HTMLElement)
    const input = container.querySelector('input[value="文档 0"]') as HTMLElement
    fireEvent.keyDown(input, { key: 'a', ctrlKey: true })
    expect(selected()).toEqual([])

    // ⚠️ 紧接着把焦点移出输入框、再在列表上按一次并断言选中：先证明**这套处理器确实
    // 被触达了**，上一条「没选中」才是「被守卫拦住」而不是「处理器根本没跑」。
    // ⚠️ 必须真移焦点：守卫判的是 `document.activeElement`，只派发事件的模拟点击
    // 不会改焦点，不移的话第二次照样被拦。
    // ⚠️ 对照（已验证有区分力）：去掉 `isTextField` 守卫，第一条断言即红
    // （`expected [ 'd0', 'd1', 'd2' ] to deeply equal []`）——在标题编辑框里
    // 按 Ctrl+A 会把整个列表全选上，而用户只是想选中自己刚打的那几个字。
    const list = container.querySelector('[data-doc-list]') as HTMLElement
    ;(input as HTMLInputElement).blur()
    list.focus()
    fireEvent.keyDown(list, { key: 'a', ctrlKey: true })
    expect(useStore.getState().selectedIds.size).toBe(3)
  })
})

describe('选择模型 — 状态归属', () => {
  it('选择存在 store 里：组件卸载重挂载后仍在（等价于面板收起再展开）', () => {
    useStore.setState({ docs: makeDocs(3), selectedIds: new Set(['d0', 'd1']) })
    const { unmount } = render(<DocLibrary />)
    expect(screen.getByText(/已选 2 项/)).toBeInTheDocument()

    unmount()
    render(<DocLibrary />)

    // ⚠️ 对照（已验证有区分力）：把 selectedIds 放回组件本地 useState —— 左侧面板
    // 折叠走的就是「卸载组件」（SlotLayout 是 `collapsed ? <PanelStrip/> : <Comp/>`），
    // 第二次 render 归零，本断言红。
    expect(screen.getByText(/已选 2 项/)).toBeInTheDocument()
  })

  it('切换筛选时选择被清空 —— 且与筛选变更在同一次 set 内（无时序窗口）', () => {
    useStore.setState({ docs: makeDocs(3), selectedIds: new Set(['d0', 'd1']) })

    useStore.getState().setActiveType('pdf')

    // ⚠️ 对照（已验证有区分力）：改回「组件 useEffect 里清空」——此处读不到清空结果
    // （effect 要等渲染提交后才跑），而那一帧里用户能对着已不可见的文档点批量删除。
    expect(selected()).toEqual([])
    expect(useStore.getState().activeType).toBe('pdf')
  })
})
