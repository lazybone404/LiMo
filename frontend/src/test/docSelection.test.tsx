/**
 * 文档库选择模型 —— 常态多选（IDE 式），**没有「批量模式」这个状态**。
 *
 * 这里守的是本轮最该先定死的一条：**单击行 = 打开文档，不改选择集**。
 * 把单击实现成 toggle（「点即选中」）读起来完全合理、写出来也自洽，
 * 但它把文档库唯一的主流程弄没了 —— 用户从此打不开任何文档。
 * 故每一条判据都要能通过「换成那个看起来更合理的实现会不会红」来证明。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry, FolderEntry } from '../store/types'

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
    // ⚠️ `moveFolder` 是 `api/client` 从 `api/transcripts` re-export 进来的，不给它
    // 显式替身就会落到真实现上（发真 fetch）。批量移动的用例全部依赖它被 mock。
    moveFolder: vi.fn(),
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

/** 等一帧 —— 滚动重算在 `requestAnimationFrame` 里跑（见 useDocSelection 的 onScroll） */
const nextFrame = () => new Promise<void>(resolve => {
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
  else setTimeout(resolve, 0)
})

beforeEach(() => {
  useStore.setState({
    docs: [], folders: [], currentDocId: null,
    // ⚠️ 两个选择集**都要清**：它们分开存放（见 docsSlice），只清一个会让上一条用例
    // 选中的文件夹漏到下一条里去，而症状是「某条用例莫名红了、单独跑又是绿的」。
    selectedIds: new Set(), selectedFolderIds: new Set(),
    listLimit: 60, docsLoading: false, toasts: [],
  })
})

afterEach(() => {
  cleanup()
  // 滚动划选的用例会给 `document.elementFromPoint` 打桩（jsdom 没有布局）——
  // 不撤的话，后续用例会意外走「从坐标取行」那条分支。jsdom 本来就没有这个属性。
  delete (document as unknown as Record<string, unknown>).elementFromPoint
  // 「排列方式」是 localStorage 偏好（`docLibraryPrefs` 每次 render 现读，没有模块缓存）——
  // 写过 prefs 的用例不清理，会让后续所有用例的排列方式跟着变，症状是「单独跑绿、整跑红」。
  localStorage.removeItem('mbw_docLibPrefs')
})

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

  it('起点行**已选中** → 整段**取消**（Ctrl+点击语义的延伸，不是无条件加选）', () => {
    useStore.setState({ docs: makeDocs(6), selectedIds: new Set(['d0', 'd1', 'd2', 'd3']) })
    const { container } = render(<DocLibrary />)

    dragOver(1, 2, container)   // 起点 d1 在按下前就已选中

    // ⚠️ 对照（已验证有区分力）：去掉 `deselect: base.has(id)`（永远按「加选」算），
    // 这里仍是 ['d0','d1','d2','d3'] —— 用户没法用拖动把「手滑多选的一段」刷掉，
    // 只能一个个点掉。「从已选中的行走 = 整段取消」是 Ctrl+点击那个 toggle 的直接延伸。
    expect(selected()).toEqual(['d0', 'd3'])
  })

  it('拖出去再拖回起点 = 按 base 重算，结果不累积也不翻转', () => {
    useStore.setState({ docs: makeDocs(6), selectedIds: new Set(['d0']) })
    const { container } = render(<DocLibrary />)

    fireEvent.mouseDown(rowOf(container, 1), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(container, 4), { buttons: 1 })
    expect(selected()).toEqual(['d0', 'd1', 'd2', 'd3', 'd4'])

    fireEvent.mouseMove(rowOf(container, 1), { buttons: 1 })

    // ⚠️ 对照（已验证有区分力）：把 `next` 改成在上一次结果上累积（或逐行取反），
    // d2..d4 会留在选中集里（逐行取反则得到完全空集）—— 用户看到的是「往回拖，选区
    // 反而越拖越大」。
    expect(selected()).toEqual(['d0', 'd1'])
    fireEvent.mouseUp(rowOf(container, 1))
  })

  it('划选途中滚动列表 → 区间按指针位置续到滚动后那一行', async () => {
    useStore.setState({ docs: makeDocs(12) })
    const { container } = render(<DocLibrary />)
    fireEvent.mouseDown(rowOf(container, 1), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(container, 2), { buttons: 1 })
    expect(selected()).toEqual(['d1', 'd2'])

    // ⚠️ jsdom 没有布局，`document.elementFromPoint` 不存在。滚动重算**没有事件 target
    // 可以回退**（指针根本没收事件），只能靠它 —— 故这里显式打桩成「指针下方是第 7 行」，
    // 模拟「列表滚动之后指针下面换了一行」。afterEach 里撤掉。
    // ⚠️ 桩必须**在 mousemove 之后**才装：mousemove 走的是 `ev.target` 回退分支，
    // 提前装上的话上面那次移动就会先扩到第 7 行，滚动那一步测不到任何东西（假绿）。
    Object.defineProperty(document, 'elementFromPoint', {
      configurable: true,
      value: () => rowOf(container, 6),
    })

    // 指针没动（**没有 mousemove**），只是列表被滚动了
    await act(async () => {
      fireEvent.scroll(container.querySelector('[data-doc-list]') as HTMLElement)
      await nextFrame()
    })

    // ⚠️ 对照（已验证有区分力）：去掉 document 上的 scroll 监听（capture），这里仍是
    // ['d1','d2'] —— 用户「拖到底 → 滚轮继续往下」时选区停在原地，必须抖一下鼠标才接上。
    expect(selected()).toEqual(['d1', 'd2', 'd3', 'd4', 'd5', 'd6'])
    fireEvent.mouseUp(rowOf(container, 6))
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

describe('选择模型 — 文件夹多选（与文档选择集分开的第二个集合）', () => {
  /**
   * ⚠️ 默认排列是「按文件夹」，所以只要 store 里有 folders，分组头与它的复选框就会渲染。
   * `count` 是 `FolderEntry` 的必填字段（徽标数字），别省。
   */
  const makeFolders = (): FolderEntry[] => [
    { id: 'fd1', name: '技术', createdAt: '2026-01-01T00:00:00', count: 0 },
    { id: 'fd2', name: '生活', createdAt: '2026-01-01T00:00:01', count: 0 },
    { id: 'fd3', name: '读书', createdAt: '2026-01-01T00:00:02', count: 0 },
  ]
  const folderToggle = (c: HTMLElement, id: string) =>
    c.querySelector(`[data-folder-select-toggle="${id}"]`) as HTMLElement | null
  const folderSel = () => [...useStore.getState().selectedFolderIds].sort()
  /** 分组头当前的展开态（复选框与折叠是两个不同的点击目标，见 GroupHeader） */
  const expandedOf = (c: HTMLElement, id: string) => (c
    .querySelector(`[data-folder-select-toggle="${id}"]`)
    ?.closest('[data-group-header]')
    ?.querySelector('[data-group-toggle]') as HTMLElement | null)?.getAttribute('aria-expanded')

  it('点分组头复选框 = 选中该文件夹：**不动文档选择集**，也不折叠它', () => {
    useStore.setState({ docs: makeDocs(2), folders: makeFolders(), selectedIds: new Set(['d0']) })
    const { container } = render(<DocLibrary />)

    fireEvent.click(folderToggle(container, 'fd1')!)

    expect(folderSel()).toEqual(['fd1'])
    // ⚠️ 对照（已验证有区分力）：两个集合合并成一个 `selectedIds`，这里会多出 fd1；
    // 而它的下游是「把 fd_xxx 当文档 id 发去 /api/transcripts/batch」。
    expect(selected()).toEqual(['d0'])
    // ⚠️ 对照（已验证有区分力）：把复选框并进 `onToggle`（折叠），点选文件夹会顺手把它收起来。
    expect(expandedOf(container, 'fd1')).toBe('true')
  })

  it('Shift 点文件夹 = 锚点到该文件夹的**区间**（与文档行同语义）', () => {
    useStore.setState({ docs: makeDocs(2), folders: makeFolders() })
    const { container } = render(<DocLibrary />)

    fireEvent.click(folderToggle(container, 'fd1')!)                 // 建立锚点
    fireEvent.click(folderToggle(container, 'fd3')!, { shiftKey: true })

    expect(folderSel()).toEqual(['fd1', 'fd2', 'fd3'])
    // ⚠️ 对照（已验证有区分力）：把文件夹顺序错用成文档顺序 —— 锚点不在其中，会退化成
    // 「只 toggle fd3」（显示 2 项）。
  })

  it('只选中文件夹时：批量条照常出现，但**文档动作全部禁用**、文件夹动作可用', () => {
    useStore.setState({ docs: makeDocs(2), folders: makeFolders() })
    const { container } = render(<DocLibrary />)

    expect(container.querySelector('[data-batch-delete-folders]')).toBeNull()
    fireEvent.click(folderToggle(container, 'fd1')!)

    expect(screen.getByText(/1 个文件夹/)).toBeInTheDocument()
    // 文档动作的 title 在「没选文档」时统一是这句 —— 逐个断言禁用
    const docBtns = screen.getAllByTitle('没有选中任何文档')
    expect(docBtns.length).toBeGreaterThan(0)
    for (const b of docBtns) expect(b).toBeDisabled()
    // 文件夹自己的动作不能跟着被禁用
    expect(container.querySelector('[data-batch-delete-folders]')).not.toBeDisabled()
    expect(container.querySelector('[data-batch-move-folders]')).not.toBeDisabled()
    // ⚠️ 对照（已验证有区分力）：`hasSelection` 只算文档集 → 整条批量条都不出现，
    // 文件夹从此没有任何批量入口（getByText 直接抛错）。
  })

  it('切筛选时**两个集合一起清空**（不能让批量条挂着对已不可见的文件夹生效的删除）', () => {
    useStore.setState({
      docs: makeDocs(3), folders: makeFolders(),
      selectedIds: new Set(['d0']), selectedFolderIds: new Set(['fd1']),
    })

    useStore.getState().setActiveType('pdf')

    // ⚠️ 对照（已验证有区分力）：`noSelection()` 只清文档集，folderSel 仍是 ['fd1']，
    // 而该分组头已经被筛掉了 —— 用户看得见的是「批量条挂着 1 个文件夹的删除按钮」。
    expect(selected()).toEqual([])
    expect(folderSel()).toEqual([])
  })

  it('Esc 同样清空两个集合', () => {
    useStore.setState({
      docs: makeDocs(3), folders: makeFolders(),
      selectedIds: new Set(['d0']), selectedFolderIds: new Set(['fd1']),
    })
    const { container } = render(<DocLibrary />)

    fireEvent.keyDown(container.querySelector('[data-doc-list]') as HTMLElement, { key: 'Escape' })

    expect(selected()).toEqual([])
    expect(folderSel()).toEqual([])
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

/**
 * 区间的两端必须按**屏幕上看到的顺序**取，不是数据顺序。
 *
 * 默认排列是「按文件夹」（简略模式下更是写死），`groupDocs` 会按文件夹树的前序重排、
 * 未分类固定排最后 —— 于是 DOM 顺序与 `visibleIds` 必然不同。折叠起来的组更极端：
 * 那些行的 id 还在数据里，元素却根本不在 DOM 里。
 *
 * 用数据顺序当基准的失效形态是**静默的**：拖选出来的是一段别的文档、
 * 还会把看不见的文档一起选进批量删除的名单里。
 */
describe('选择模型 — 区间按**渲染顺序**（分组重排 / 折叠组不在 DOM）', () => {
  const makeFolders = (): FolderEntry[] => [
    { id: 'fd1', name: '技术', createdAt: '2026-01-01T00:00:00', count: 0 },
  ]
  /**
   * 数据顺序（时间降序）= d0 d1 d2 d3 d4。
   * d1 / d4 归「技术」，其余未分类 ⇒ 按文件夹排列时 DOM 是
   * `[技术] d1 d4` + `[未分类] d0 d2 d3`。
   * 「视觉第 2 行」是 d4、「视觉第 4 行」是 d2 —— 与数据顺序的第 2~4 条（d2/d3/d4）完全不同。
   */
  const groupedDocs = (): DocEntry[] => {
    const docs = makeDocs(5)
    docs[1].folderId = 'fd1'
    docs[4].folderId = 'fd1'
    return docs
  }
  const domIds = (c: HTMLElement) =>
    [...c.querySelectorAll('[data-doc-list] [data-doc-id]')].map(el => el.getAttribute('data-doc-id'))
  const dragOver = (from: number, to: number, c: HTMLElement) => {
    fireEvent.mouseDown(rowOf(c, from), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(c, to), { buttons: 1 })
    fireEvent.mouseUp(rowOf(c, to))
  }

  it('前提：按文件夹排列**确实**把 DOM 顺序打乱了（否则下面的用例测不到东西）', () => {
    useStore.setState({ docs: groupedDocs(), folders: makeFolders() })
    const { container } = render(<DocLibrary />)

    // ⚠️ 这条是「前提断言」，不是被测行为：它证明本 describe 的 fixture 真的触发了重排。
    // 少了它，`groupDocs` 哪天不再重排（或默认排列改成平铺）时，下面三条会静默变成
    // 「数据顺序恰好等于 DOM 顺序」的假绿。
    expect(domIds(container)).toEqual(['d1', 'd4', 'd0', 'd2', 'd3'])
  })

  it('Ctrl 划选：从视觉第 2 行拖到第 4 行 = d4 d0 d2（不是数据顺序的 d2 d3 d4）', () => {
    useStore.setState({ docs: groupedDocs(), folders: makeFolders() })
    const { container } = render(<DocLibrary />)

    dragOver(1, 3, container)

    // 用户手指划过的是屏幕上那三行：d4（第 2 行）、d0（第 3 行）、d2（第 4 行）
    expect(selected()).toEqual(['d0', 'd2', 'd4'])
    // ⚠️ 对照（已验证有区分力）：区间两端改回 `orderRef.current`（数据顺序）——
    // 结果是 ['d2','d3','d4']：刷的是上面那一段，被选中的却是下面一段。
  })

  it('Shift 区间同样按渲染顺序（同一根因的第二处消费点）', () => {
    useStore.setState({ docs: groupedDocs(), folders: makeFolders() })
    const { container } = render(<DocLibrary />)

    fireEvent.click(rowOf(container, 0), { ctrlKey: true })   // 视觉首行 = d1（建立锚点）
    fireEvent.click(rowOf(container, 3), { shiftKey: true })  // 视觉第 4 行 = d2

    // DOM 区间 d1 → d2 = [d1, d4, d0, d2]
    expect(selected()).toEqual(['d0', 'd1', 'd2', 'd4'])
    // ⚠️ 对照（已验证有区分力）：`selectRange` 改回数据顺序 → ['d1','d2']——
    // 用户横跨了一个分组头，得到的却只是两端的两个夹心。
  })

  it('折叠组里的文档不会被人划进来（它们根本不在屏幕上）', () => {
    useStore.setState({ docs: groupedDocs(), folders: makeFolders() })
    const { container } = render(<DocLibrary />)

    // 收起「技术」——它的两行与组头一起从 DOM 里消失
    fireEvent.click(container.querySelector('[data-group-toggle="f:fd1"]') as HTMLElement)
    expect(domIds(container)).toEqual(['d0', 'd2', 'd3'])

    dragOver(0, 2, container)

    expect(selected()).toEqual(['d0', 'd2', 'd3'])
    // ⚠️ 对照（已验证有区分力）：按数据顺序算，d1 会被静默划进来 —— 它此刻在收起的
    // 组里，用户看不见，批量删除却会把它一起删掉；d0/d2/d3 反而只选中一部分。
  })
})

describe('选择模型 — 分组切换与批量操作的边界', () => {
  const makeFolders = (): FolderEntry[] => [
    { id: 'fd1', name: '技术', createdAt: '2026-01-01T00:00:00', count: 0 },
    { id: 'fd2', name: '生活', createdAt: '2026-01-01T00:00:01', count: 0 },
  ]
  const folderSel = () => [...useStore.getState().selectedFolderIds].sort()
  const dragOver = (from: number, to: number, c: HTMLElement) => {
    fireEvent.mouseDown(rowOf(c, from), { button: 0, ctrlKey: true })
    fireEvent.mouseMove(rowOf(c, to), { buttons: 1 })
    fireEvent.mouseUp(rowOf(c, to))
  }
  /** 让 batch 的 async 链（PATCH → refreshFolders → toast）跑完 */
  const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

  it('切成「按类型」排列时清空文件夹选择（分组头复选框已不再渲染）', () => {
    // `groupBy` 是 localStorage 偏好，render 时现读；必须在渲染**之前**写
    localStorage.setItem('mbw_docLibPrefs', JSON.stringify({ groupBy: 'type' }))
    useStore.setState({
      docs: makeDocs(2), folders: makeFolders(),
      selectedFolderIds: new Set(['fd1']),
    })
    render(<DocLibrary />)

    // ⚠️ 对照（已验证有区分力）：清空判据写成 `grouped === null`——按类型/按时间排列时
    // `grouped` 同样非 null，于是 fd1 留在选中集里，批量条一直挂着「删除 1 个文件夹」，
    // 而它的分组头在界面上根本不存在（类型组的 `editable` 是 false）。
    expect(folderSel()).toEqual([])
  })

  it('划选结束后的那一下 click 只被吞在**起点行**：紧接着点另一行照常打开', () => {
    useStore.setState({ docs: makeDocs(6) })
    const { container } = render(<DocLibrary />)

    dragOver(1, 3, container)

    // 浏览器在 mouseup 后必派发一次 click —— 起点行那一下必须被吞，否则 d1 被 toggle 掉
    fireEvent.click(rowOf(container, 1), { ctrlKey: true })
    expect(selected()).toEqual(['d1', 'd2', 'd3'])

    // 紧接着点**另一行**：这不是划选的尾巴，照常打开文档
    fireEvent.click(rowOf(container, 4))
    expect(useStore.getState().currentDocId).toBe('d4')
    // ⚠️ 对照（已验证有区分力）：抑制条件改回纯时间窗（只看 `Date.now() < until`、
    // 不看 id），最后这条拿到 null —— 划完选想点开一篇看看，那 300ms 里点什么都没反应。
  })

  it('批量删除文件夹部分失败 → 失败的那个**留在选中集**里（可直接重试）', async () => {
    const { deleteFolder } = await import('../api/client')
    vi.mocked(deleteFolder).mockImplementation(async (id: string) => {
      if (id === 'fd2') throw new Error('网络错误')
      return { status: 'ok', deletedFolders: 1, deletedFolderIds: [id], moved: 0 }
    })
    useStore.setState({
      docs: makeDocs(2), folders: makeFolders(),
      selectedFolderIds: new Set(['fd1', 'fd2']),
    })
    const { container } = render(<DocLibrary />)

    fireEvent.click(container.querySelector('[data-batch-delete-folders]') as HTMLElement)
    fireEvent.click(document.querySelector('[data-confirm-delete-folders]') as HTMLElement)
    await settle()

    // ⚠️ 对照（已验证有区分力）：结尾无条件 `clearSelection()`（不把失败项放回去），
    // 这里拿到 [] —— 那个没删掉的文件夹重新变回「在树上找不到」，用户得重新点一遍。
    expect(folderSel()).toEqual(['fd2'])
    expect(useStore.getState().toasts.some(t => t.type === 'error' && t.message.includes('1 个失败'))).toBe(true)
  })

  it('批量移动到「已经在那」的父级 = 不发请求，只说一句无需移动', async () => {
    const { moveFolder } = await import('../api/client')
    vi.mocked(moveFolder).mockClear()
    // fd1 / fd2 都没有 parentId ⇒ 它们已经在根级
    useStore.setState({
      docs: makeDocs(2), folders: makeFolders(),
      selectedFolderIds: new Set(['fd1', 'fd2']),
    })
    const { container } = render(<DocLibrary />)

    fireEvent.click(container.querySelector('[data-batch-move-folders]') as HTMLElement)
    fireEvent.click(document.querySelector('[data-move-target=""]') as HTMLElement)  // 首行 = 根级
    await settle()

    // ⚠️ 对照（已验证有区分力）：不做 no-op 预筛，会发 2 次 PATCH + toast「已移动 2 个文件夹」，
    // 而树上一个像素都没变 —— 用户会以为移动没生效，再点一次。
    expect(moveFolder).not.toHaveBeenCalled()
    expect(useStore.getState().toasts.some(t => t.message.includes('无需移动'))).toBe(true)
    // 什么都没做，选择集不该被清掉（用户多半是想换个目标）
    expect(folderSel()).toEqual(['fd1', 'fd2'])
  })

  it('文件夹删除确认框认 Esc（模态遮罩的键盘出口）', () => {
    useStore.setState({
      docs: makeDocs(2), folders: makeFolders(),
      selectedFolderIds: new Set(['fd1']),
    })
    const { container } = render(<DocLibrary />)

    fireEvent.click(container.querySelector('[data-batch-delete-folders]') as HTMLElement)
    expect(document.querySelector('[data-confirm-delete-folders]')).not.toBeNull()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(document.querySelector('[data-confirm-delete-folders]')).toBeNull()
    // ⚠️ Esc 只关确认框，不清选择：焦点不在列表上，那是列表自己的 Esc 语义。
    // ⚠️ 对照（已验证有区分力）：不加这个 effect，确认框只能靠点遮罩关掉 ——
    // 键盘用户进了这个框就没有退路。
    expect(folderSel()).toEqual(['fd1'])
  })

  it('文档删除确认框同样认 Esc（两个模态都是同一套约定）', () => {
    useStore.setState({ docs: makeDocs(2), selectedIds: new Set(['d0']) })
    render(<DocLibrary />)

    fireEvent.click(screen.getByTitle('删除选中文档'))
    expect(document.querySelector('[data-confirm-delete]')).not.toBeNull()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(document.querySelector('[data-confirm-delete]')).toBeNull()
    // ⚠️ 对照（已验证有区分力）：删掉 `deleteTargets` 那个 effect，确认框留着 ——
    // 键盘用户进来后只能 Tab 到「取消」才退得出去。
    expect(selected()).toEqual(['d0'])
  })
})
