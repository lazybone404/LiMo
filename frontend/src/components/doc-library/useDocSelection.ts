/**
 * 文档库多选 —— 常态多选（IDE 式），**没有「批量模式」这个状态**。
 *
 * ⚠️ 单击行 = **打开文档**（文档库唯一的主流程），**不改变选择集**。
 * 选中只有三条路径：复选框、Ctrl/Cmd + 点行、Shift + 点行。
 * 若把单击也实现成 toggle，用户就再也打不开文档了——这是本模块最该先定死的一条，
 * 因为「点即选中」听起来完全合理，写出来也自洽，只是把主流程弄没了。
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

  const toggle = useCallback((id: string) => {
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

  /** 行点击统一入口 —— 三种修饰键三套语义，默认那套是**打开**（预览标签） */
  const handleRowClick = useCallback((e: React.MouseEvent, id: string) => {
    if (e.ctrlKey || e.metaKey) { toggle(id); return }
    if (e.shiftKey) { selectRange(id); return }
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
