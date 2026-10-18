/**
 * 文档库多选 —— 常态多选（IDE 式），**没有「批量模式」这个状态**。
 *
 * ⚠️ 单击行 = **打开文档**（文档库唯一的主流程），**不改变选择集**。
 * 选中只有四条路径：复选框热区、Ctrl/Cmd + 点行、Shift + 点行、**Ctrl/Cmd + 按住拖动划选**。
 * 若把单击也实现成 toggle，用户就再也打不开文档了——这是本模块最该先定死的一条，
 * 因为「点即选中」听起来完全合理，写出来也自洽，只是把主流程弄没了。
 *
 * ⚠️ 划选是**加选**不是替换（`base ∪ 区间`）：按住 Ctrl 从第 3 行拖到第 9 行，横跨的
 * 7 行全部选中，而原本勾着的行**不被取消**——这正是与 Shift 区间的分野（Shift 是
 * 「替换成这一段」，文件管理器语义）。起点行也不会被 toggle 掉，理由见下面 suppress。
 *
 * ⚠️ 划选结束那一下 `click` 必须被吞掉（`suppressClickUntil`）：按下时按的仍是 Ctrl，
 * mouseup 之后浏览器照样派发一次 `click`，而带 Ctrl 的 click 会被解释成「toggle 这一行」
 * —— 起点行刚被划进选中集又被取消，用户看到的是「拖了一圈，第一行没选上」。
 *
 * 选择状态放在 store（见 docsSlice 的文件头）：面板折叠会卸载组件，留在本地会丢。
 * 只有 `anchorId` 留在组件——它是**操作过程态**，跨折叠丢失可接受（退化为单点加选）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'

/** 焦点在这些元素里时，Ctrl+A / Esc 属于它们自己，不该被列表劫持 */
function isTextField(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
}

/** 划选结束后抑制 click 的窗口。300ms 与「双击间隔」同量级，再长会吞掉用户的下一次点击 */
const SUPPRESS_CLICK_MS = 300

/** 划选过程态（只在一次 mousedown→mouseup 之间有效） */
interface DragState {
  /** mousedown 那一行 —— 区间的一端 */
  startId: string
  /** 鼠标当前经过的行 —— 区间的另一端 */
  lastId: string
  /** 划选前已选中的集合（加选的基），Esc 取消时原样恢复 */
  base: Set<string>
  /** 指针**离开过起点行**才算划选；原地按下松开仍是普通 Ctrl+点击 */
  moved: boolean
}

export function useDocSelection(orderedIds: string[], onOpen: (id: string, mode: 'preview' | 'pinned') => void) {
  const selectedIds = useStore(s => s.selectedIds)
  const setSelectedIds = useStore(s => s.setSelectedIds)
  const clearSelection = useStore(s => s.clearSelection)

  /** Shift 区间的锚点（起点）。连续 Shift 点击时**保持不变**，这样区间能来回伸缩。 */
  const [anchorId, setAnchorId] = useState<string | null>(null)

  // 当前列表顺序：Shift 区间靠它算。⚠️ 用 effect 而非在 render 里赋值——render 期间
  // 写 ref 在并发渲染下不安全，而这里晚一帧没有实际影响（顺序变了必然先提交再跑 effect，
  // 用户的点击总在那之后）。
  const orderRef = useRef(orderedIds)
  useEffect(() => { orderRef.current = orderedIds }, [orderedIds])

  /** 划选过程态；null = 没在划选 */
  const dragRef = useRef<DragState | null>(null)
  /** `Date.now()` 之前到达的 click 一律吞掉（见文件头的 suppress 段） */
  const suppressUntilRef = useRef(0)
  /** 本次划选挂上的 document 监听 + 对 body 的副作用，统一在这里撤 */
  const cleanupRef = useRef<(() => void) | null>(null)

  const toggle = useCallback((id: string) => {
    // 划选刚结束时那一下 click 不能当真点击（详情见文件头）
    if (Date.now() < suppressUntilRef.current) return
    setSelectedIds(toggleIn(useStore.getState().selectedIds, id))
    setAnchorId(id)
  }, [setSelectedIds])

  const selectRange = useCallback((id: string) => {
    const order = orderRef.current
    const to = order.indexOf(id)
    const from = anchorId === null ? -1 : order.indexOf(anchorId)
    if (from < 0 || to < 0) {
      // 锚点已滚出当前结果集（换了筛选、或锚点文档被删）——退化为单点加选而不是乱选一段
      setSelectedIds(toggleIn(useStore.getState().selectedIds, id))
      setAnchorId(id)
      return
    }
    const [a, b] = from <= to ? [from, to] : [to, from]
    // ⚠️ 区间是**替换**语义（与文件管理器一致）：只按 Shift 时预期是「选中这一段」，
    // 不是「在原有勾选上再并一段」。锚点不更新，于是连续 Shift 点击可以来回伸缩。
    setSelectedIds(new Set(order.slice(a, b + 1)))
  }, [anchorId, setSelectedIds])

  const setAll = useCallback((ids: string[], on: boolean) => {
    setSelectedIds(on ? new Set(ids) : new Set())
  }, [setSelectedIds])

  const clear = useCallback(() => {
    clearSelection()
    setAnchorId(null)
  }, [clearSelection])

  // 组件卸载时撤掉可能还挂着的划选监听（面板折叠 = 卸载，不撤会留下悬空的 document 监听）
  useEffect(() => () => cleanupRef.current?.(), [])

  /**
   * 行根 mousedown —— 按住 Ctrl/Cmd **拖动**划选的入口。
   *
   * ⚠️ 只拦「左键 + Ctrl/Cmd」，其余原样放行：普通按下必须继续冒泡到拖拽（DnD）、
   * Shift 区间、以及行点击「打开文档」。划选是**附加**的手势，不是新的点击语义。
   */
  const handleRowMouseDown = useCallback((e: React.MouseEvent, id: string) => {
    if (e.button !== 0 || !(e.ctrlKey || e.metaKey)) return
    const t = e.target
    // 复选框热区自己处理点击：按下即划选的话，单击热区会「选中又被抑制」等于点不动
    if (t instanceof Element && t.closest('[data-select-toggle]')) return
    // 编辑框里 Ctrl+按下是「选中一段文字」，不是划选文档
    if (t instanceof Element && t.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""]')) return

    e.preventDefault()            // 阻止浏览器把整段文字选蓝（否则拖动时满屏蓝底）
    cleanupRef.current?.()        // 上一次没收到 mouseup（切窗口等）时先撤干净

    const drag: DragState = {
      startId: id, lastId: id, moved: false,
      base: new Set(useStore.getState().selectedIds),
    }
    dragRef.current = drag
    const prevUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'

    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      // ⚠️ 用 elementFromPoint 而不是 `ev.target`：指针跨到行之间的空隙/分组头时，
      // target 不再是行，而我们要的是「指针正下方是哪一行」。
      const fromPoint = typeof document.elementFromPoint === 'function'
        ? document.elementFromPoint(ev.clientX, ev.clientY)
        : null
      const under = fromPoint ?? (ev.target instanceof Element ? ev.target : null)
      const rowId = under?.closest('[data-doc-id]')?.getAttribute('data-doc-id')
      // 行没变就不重算 —— mousemove 每秒几十次，每次都重建 Set 会让整列表跟着重渲染
      if (!rowId || rowId === d.lastId) return
      d.lastId = rowId
      d.moved = true
      const order = orderRef.current
      const a = order.indexOf(d.startId)
      const b = order.indexOf(rowId)
      // 起点滑出了当前结果集（滚动到分批外 / 筛选在拖动中变了）——停在已画出的部分
      if (a < 0 || b < 0) return
      const [lo, hi] = a <= b ? [a, b] : [b, a]
      // 加选：base 是按下时的快照，区间整体并进去（不 toggle，见文件头）
      const next = new Set(d.base)
      for (let i = lo; i <= hi; i++) next.add(order[i])
      setSelectedIds(next)
    }

    const onUp = () => {
      const d = dragRef.current
      if (d?.moved) {
        suppressUntilRef.current = Date.now() + SUPPRESS_CLICK_MS
        // 划选后锚点跟到终点，紧接着的 Shift+点击从松手那一行开始
        setAnchorId(d.lastId)
      }
      cleanupRef.current?.()
    }

    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return
      // Esc = 取消整次划选（与「Esc 清空选择」不同：这里只回到按下前的样子）
      const d = dragRef.current
      if (d) {
        setSelectedIds(d.base)
        // ⚠️ 用户此刻**还按着鼠标**，松手时浏览器仍会派发一次 click（多半还带着 Ctrl）。
        // 不抑制的话，那一下 click 会走 `toggle(startId)` —— 刚被 Esc 恢复的行又被勾上，
        // 「Esc 取消了划选」在他松手的一刻自我推翻。
        suppressUntilRef.current = Date.now() + SUPPRESS_CLICK_MS
      }
      cleanupRef.current?.()
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('keydown', onKey, true)
    // ⚠️ 划选途中切走窗口（Alt+Tab / 点任务栏）收不到 mouseup —— 不监听 blur 的话
    // document 上的 mousemove 与 `body.userSelect='none'` 会一直留着：回到页面后
    // 鼠标随便一动就继续划选，且整页文字选不中。blur 时按「松手」处理并撤干净。
    window.addEventListener('blur', onUp)
    cleanupRef.current = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('blur', onUp)
      document.body.style.userSelect = prevUserSelect
      dragRef.current = null
      cleanupRef.current = null
    }
  }, [setSelectedIds])

  /** 行点击统一入口 —— 三种修饰键三套语义，默认那套是**打开**（预览标签） */
  const handleRowClick = useCallback((e: React.MouseEvent, id: string) => {
    if (e.ctrlKey || e.metaKey) { toggle(id); return }
    if (e.shiftKey) { selectRange(id); return }
    // 划选结束后那一下 click（普通单击）同样不该触发「打开文档」
    if (Date.now() < suppressUntilRef.current) return
    onOpen(id, 'preview')
  }, [toggle, selectRange, onOpen])

  /**
   * 行双击 = **钉成常驻标签**（VS Code 的 Keep Open）。
   *
   * ⚠️ **不需要防抖**：浏览器双击先派发两次 `click`、再派发 `dblclick`。第二次 click
   * 到达时该文档已在组内 → 只走「切过去」，是 no-op。所以 `click → click → dblclick`
   * 的结果正是「打开 → 无变化 → 钉住」。即使 React 没在两次 click 之间 flush，
   * `pendingOpenMode` 也会被这次的 `'pinned'` 覆盖，最终提交仍是常驻。
   *
   * 带修饰键的双击忽略：那时用户要的是加选 / 区间（单击那套语义），钉标签是误触。
   */
  const handleRowDoubleClick = useCallback((e: React.MouseEvent, id: string) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey) return
    onOpen(id, 'pinned')
  }, [onOpen])

  /** 挂到列表容器（需 `tabIndex={0}` 才能收到按键） */
  const onListKeyDown = useCallback((e: React.KeyboardEvent) => {
    // ⚠️ 焦点在输入框里时**必须让给浏览器**：搜索框里 Ctrl+A 是「全选已输入的文本」，
    // 劫持它会让用户连自己刚打的关键词都选不中。
    if (isTextField(document.activeElement)) return
    if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
      e.preventDefault()
      setAll(orderRef.current, true)
      return
    }
    if (e.key === 'Escape') clear()
  }, [setAll, clear])

  return {
    selectedIds,
    /** 批量条的显隐条件 —— **派生**而非独立状态，故不可能与选择集漂移 */
    hasSelection: selectedIds.size > 0,
    toggle,
    selectRange,
    setAll,
    clear,
    handleRowClick,
    handleRowMouseDown,
    handleRowDoubleClick,
    onListKeyDown,
  }
}

/** 返回新的 Set（不可变更新）——就地 add/delete 不会触发订阅者更新 */
function toggleIn(prev: Set<string>, id: string): Set<string> {
  const next = new Set(prev)
  if (next.has(id)) next.delete(id); else next.add(id)
  return next
}
