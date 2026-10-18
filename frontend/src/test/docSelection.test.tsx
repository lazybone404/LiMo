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

describe('选择模型 — Ctrl 拖动划选', () => {
  /**
   * ⚠️ jsdom 没有 `document.elementFromPoint`（未实现布局），实现里对它做了 function
   * 判据并回退到 `ev.target` —— 把 mousemove 派发到**行元素**上，正好走回退分支。
   * 真实浏览器里走的是 elementFromPoint，两条分支指向同一件事：「指针下面是哪一行」。
   */
  const dragOver = (from: number, to: number, c: HTMLElement) => {
    fireEvent.mouseDown(rowOf(c, from), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(c, to), { buttons: 1 })
    fireEvent.mouseUp(rowOf(c, to))
  }

  it('Ctrl 从 d1 拖到 d4 = 区间**加选**，原有的选中不被取消', () => {
    useStore.setState({ docs: makeDocs(6), selectedIds: new Set(['d0']) })
    const { container } = render(<DocLibrary />)

    dragOver(1, 4, container)

    // ⚠️ 对照（已验证有区分力）：把加选写成 `new Set(range)`（替换语义），d0 会消失；
    // 而「从第 3 行拖到第 9 行，原本勾着的不该被取消」正是用户要的手感。
    expect(selected()).toEqual(['d0', 'd1', 'd2', 'd3', 'd4'])
  })

  it('划选结束那一下 click 被吞掉 —— 起点行不被二次 toggle', () => {
    useStore.setState({ docs: makeDocs(6) })
    const { container } = render(<DocLibrary />)

    fireEvent.mouseDown(rowOf(container, 1), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(container, 3), { buttons: 1 })
    fireEvent.mouseUp(rowOf(container, 3))
    // 浏览器在 mouseup 之后照样派发一次带 Ctrl 的 click
    fireEvent.click(rowOf(container, 1), { ctrlKey: true })

    // ⚠️ 对照（已验证有区分力）：去掉 suppressUntil 判定，d1 会被 toggle 掉，
    // 结果变成 ['d2','d3'] —— 用户看到的是「拖了一圈，第一行没选上」。
    expect(selected()).toEqual(['d1', 'd2', 'd3'])
    // 划选不是「打开文档」
    expect(useStore.getState().currentDocId).toBeNull()
  })

  it('划选中按 Esc = 回到按下前的选择集（不是清空）', () => {
    useStore.setState({ docs: makeDocs(6), selectedIds: new Set(['d0']) })
    const { container } = render(<DocLibrary />)

    fireEvent.mouseDown(rowOf(container, 2), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(container, 4), { buttons: 1 })
    expect(selected()).toEqual(['d0', 'd2', 'd3', 'd4'])

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(selected()).toEqual(['d0'])

    // 松手时监听已撤掉，不会把刚取消的区间又加回来
    fireEvent.mouseUp(rowOf(container, 4))
    expect(selected()).toEqual(['d0'])
  })

  it('从标题编辑框里 Ctrl+按下不起划选（那是选中文字）', () => {
    useStore.setState({ docs: makeDocs(3) })
    const { container } = render(<DocLibrary />)

    fireEvent.click(rowOf(container, 0).querySelector('[title="编辑标题"]') as HTMLElement)
    const input = container.querySelector('input[value="文档 0"]') as HTMLElement
    fireEvent.mouseDown(input, { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(container, 2), { buttons: 1 })

    expect(selected()).toEqual([])
  })

  it('点复选框热区 = toggle，不启动划选（否则单击热区会被抑制掉）', () => {
    useStore.setState({ docs: makeDocs(3) })
    const { container } = render(<DocLibrary />)

    fireEvent.mouseDown(toggleOf(container, 0), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(container, 2), { buttons: 1 })
    fireEvent.mouseUp(rowOf(container, 2))
    fireEvent.click(toggleOf(container, 0))

    // 只有热区那一次点击生效；拖动没有把 d1/d2 也划进来
    expect(selected()).toEqual(['d0'])
  })

  it('无修饰键的拖动仍走 DnD —— 且 Ctrl+拖动不产生 DnD', () => {
    useStore.setState({ docs: makeDocs(3), selectedIds: new Set(['d1']) })
    const { container } = render(<DocLibrary />)

    const plain = { setData: vi.fn(), effectAllowed: '' }
    fireEvent.dragStart(rowOf(container, 1), { dataTransfer: plain })
    // 拖已选中的项 = 整批拖（既有语义不变）
    expect(plain.setData).toHaveBeenCalledWith('application/x-limo-doc', 'd1')

    // ⚠️ jsdom **没有 DragEvent**，`fireEvent.dragStart(..., { ctrlKey: true })` 会退化成
    // 裸 Event，修饰键被静默丢弃（断言会假绿/假红）。这里手动派发 MouseEvent。
    const withCtrl = { setData: vi.fn(), effectAllowed: '' }
    const ev = new MouseEvent('dragstart', { bubbles: true, cancelable: true, ctrlKey: true })
    Object.defineProperty(ev, 'dataTransfer', { value: withCtrl })
    rowOf(container, 0).dispatchEvent(ev)

    // ⚠️ 对照（已验证有区分力）：去掉 handleDragStart 的 ctrlKey 守卫，这里会再调一次
    // setData —— 划选手势途中弹出拖拽幽灵图，两套语义同时生效。
    expect(withCtrl.setData).not.toHaveBeenCalled()
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
