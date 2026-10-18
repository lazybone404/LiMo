/**
 * 文档库多选 —— 常态多选（IDE 式），**没有「批量模式」这个状态**。
 *
 * ⚠️ 单击行 = **打开文档**（文档库唯一的主流程），**不改变选择集**。
 * 选中只有四条路径：复选框热区、Ctrl/Cmd + 点行、Shift + 点行、**Ctrl/Cmd + 按住拖动划选**。
 * 若把单击也实现成 toggle，用户就再也打不开文档了——这是本模块最该先定死的一条，
 * 因为「点即选中」听起来完全合理，写出来也自洽，只是把主流程弄没了。
 *
 * ⚠️ **划选的方向由起点行决定，不是无条件加选**：
 * - 起点**未选中** → 区间内全部选中（`base ∪ 区间`）
 * - 起点**已选中** → 区间内全部取消（`base \ 区间`）
 *
 * 这条规则与「Ctrl + 点击 = toggle 单行」逐字同源：按住 Ctrl 点一个已选中的行会取消它，
 * 那么从它拖过去就该整段取消 —— 用户不必先「取消选择」再划。
 *
 * ⚠️ 为什么是「整段统一设成同一状态」而不是「逐行取反」：逐行取反 = 连续 Ctrl 点击的
 * 字面效果，但在一段既有已选又有未选的行上会得到「选中/取消交错」的结果，而用户的
 * 手势意图是「刷一段」。统一设置还让结果**幂等**：拖出去再拖回来不会翻转两次。
 *
 * ⚠️ 划选与 Shift 区间的分野不变：Shift 是**替换**成这一段（文件管理器语义），
 * 划选是在既有选择上做增/删。起点行不会被单独 toggle，理由见下面 suppress。
 *
 * ⚠️ **区间的两端按「屏幕上看到的顺序」算，不是按数据顺序**（见 `domOrder`）：
 * 默认排列是「按文件夹」，`groupDocs` 会把文档按树的前序重排、未分类排最后，
 * 与筛选结果的原始顺序不同；折叠组里的行更是根本不在 DOM 里。用数据顺序当区间基准，
 * 划选出来的就会是另一段文档（而且会静默选中用户看不见的文档）。
 *
 * ⚠️ 划选结束那一下 `click` 必须被吞掉（`suppressRef`）：按下时按的仍是 Ctrl，
 * mouseup 之后浏览器照样派发一次 `click`，而带 Ctrl 的 click 会被解释成「toggle 这一行」
 * —— 起点行刚被划进选中集又被取消，用户看到的是「拖了一圈，第一行没选上」。
 * 抑制是**绑定行 id 的一次性事件**而不是纯时间窗：划选结束后立刻点另一行（或另一个
 * 复选框）必须正常工作，只有「起点行自己那一下」才是需要吞的。
 *
 * ⚠️ 划选期间**滚轮滚动也会扩展区间**：滚动本身不产生 `mousemove`，不监听 `scroll`
 * 的话，用户「拖到列表底部 → 滚轮往下翻」时指针下面的行已经换了、选区却停在原地，
 * 得抖一下鼠标才接上。滚动后按**指针最后位置**重新取行即可。
 *
 * 选择状态放在 store（见 docsSlice 的文件头）：面板折叠会卸载组件，留在本地会丢。
 * 只有 `anchorId` / `folderAnchorId` 留在组件——它们是**操作过程态**，跨折叠丢失可接受。
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

/** 需要吞掉的那一下 click：**只对起点行**、只在窗口内（见文件头） */
interface SuppressedClick { id: string; until: number }

/** 划选过程态（只在一次 mousedown→mouseup 之间有效） */
interface DragState {
  /** mousedown 那一行 —— 区间的一端 */
  startId: string
  /** 鼠标当前经过的行 —— 区间的另一端 */
  lastId: string
  /** 划选前已选中的集合（增删的基），Esc 取消时原样恢复 */
  base: Set<string>
  /** 起点行在 `base` 里 → 本次划选是**取消**；否则是加选（见文件头） */
  deselect: boolean
  /** 指针**离开过起点行**才算划选；原地按下松开仍是普通 Ctrl+点击 */
  moved: boolean
  /** 指针最后位置 —— 滚轮滚动时指针不动，靠它重算「指针下方是哪一行」 */
  x: number
  y: number
}

/**
 * 视口坐标 → 该点下方的文档行 id（跨过行间空隙/分组头时返回 null）。
 *
 * ⚠️ `fallback` 是给 **mousemove** 用的：`elementFromPoint` 依赖布局，在没有布局的
 * 宿主（jsdom）里恒为 null，而 mousemove 自带一个 `target` 可以顶上（指针下方就是
 * 事件的目标元素）。**滚动重算不传它** —— 那时指针没动、也没有事件对象，只能靠
 * `elementFromPoint`；传一个过期的 target 反而会锁在滚动前的行上。
 */
function docIdAt(x: number, y: number, fallback?: EventTarget | null): string | null {
  const fromPoint = typeof document.elementFromPoint === 'function'
    ? document.elementFromPoint(x, y)
    : null
  const el = fromPoint instanceof Element
    ? fromPoint
    : (fallback instanceof Element ? fallback : null)
  return el?.closest('[data-doc-id]')?.getAttribute('data-doc-id') ?? null
}

/**
 * 当前 DOM 中**实际渲染出来的**文档行顺序 —— Shift 区间与划选区间唯一的顺序基准。
 *
 * ⚠️ **不能用数据顺序**（`orderedIds`）：默认排列是「按文件夹」，`groupDocs` 会按树的
 * 前序重排、未分类固定最后，与筛选结果的原始顺序不同。用它算区间，用户从视觉第 2 行
 * 拖到第 4 行，选中的是数据里第 2~4 条 —— 完全是另一段文档，而界面上看不出任何异常。
 *
 * ⚠️ 折叠组里的行**不在 DOM 里**，于是「划选选中看不见的文档」也一并消失（旧行为里
 * 它们的 id 仍在数据顺序中，会被静默划进来 —— 批量删除时删掉用户没看见的东西）。
 *
 * ⚠️ `Ctrl+A` **刻意不走这里**：全选 = 当前筛选结果的全部匹配项（跨分批），
 * 未渲染的行也必须选上，见 `onListKeyDown`。
 *
 * 找不到列表容器时退回数据顺序：那是 `DocList` 尚未挂载（空态 / 首帧）的退化情况，
 * 此时也不会有行可点，回退值只是个安全兜底。
 */
function domOrder(fallback: string[]): string[] {
  if (typeof document.querySelector !== 'function') return fallback
  const list = document.querySelector('[data-doc-list]')
  if (!list) return fallback
  return [...list.querySelectorAll('[data-doc-id]')]
    .map(el => el.getAttribute('data-doc-id'))
    .filter((id): id is string => !!id)
}

/**
 * @param orderedIds       文档的当前显示顺序（Shift 区间与划选都靠它算）
 * @param onOpen           单击行 = 打开文档
 * @param orderedFolderIds 文件夹的当前显示顺序（前序）。**只有分组模式下非空**；
 *                         Shift 选文件夹区间用。平铺时没有分组头，自然也没有它。
 */
export function useDocSelection(
  orderedIds: string[],
  onOpen: (id: string, mode: 'preview' | 'pinned') => void,
  orderedFolderIds: string[] = [],
) {
  const selectedIds = useStore(s => s.selectedIds)
  const setSelectedIds = useStore(s => s.setSelectedIds)
  const selectedFolderIds = useStore(s => s.selectedFolderIds)
  const setSelectedFolderIds = useStore(s => s.setSelectedFolderIds)
  const clearSelection = useStore(s => s.clearSelection)

  /** Shift 区间的锚点（起点）。连续 Shift 点击时**保持不变**，这样区间能来回伸缩。 */
  const [anchorId, setAnchorId] = useState<string | null>(null)
  /** 文件夹 Shift 区间的锚点 —— 与文档那个刻意分开：两套顺序互不相关 */
  const [folderAnchorId, setFolderAnchorId] = useState<string | null>(null)

  // 当前列表顺序：Shift 区间靠它算。⚠️ 用 effect 而非在 render 里赋值——render 期间
  // 写 ref 在并发渲染下不安全，而这里晚一帧没有实际影响（顺序变了必然先提交再跑 effect，
  // 用户的点击总在那之后）。
  const orderRef = useRef(orderedIds)
  useEffect(() => { orderRef.current = orderedIds }, [orderedIds])
  /** 文件夹顺序（前序）—— 同上，只给 Shift 区间用 */
  const folderOrderRef = useRef(orderedFolderIds)
  useEffect(() => { folderOrderRef.current = orderedFolderIds }, [orderedFolderIds])

  /** 划选过程态；null = 没在划选 */
  const dragRef = useRef<DragState | null>(null)
  /** 划选刚结束时那一行要吞掉的一次 click（见文件头的 suppress 段） */
  const suppressRef = useRef<SuppressedClick | null>(null)
  /** 本次划选挂上的 document 监听 + 对 body 的副作用，统一在这里撤 */
  const cleanupRef = useRef<(() => void) | null>(null)

  /** 这一行的这次点击是不是「划选结束那一下」——只有起点行、只在 300ms 内 */
  const isSuppressed = useCallback((id: string): boolean => {
    const s = suppressRef.current
    if (!s) return false
    if (Date.now() >= s.until) { suppressRef.current = null; return false }
    return s.id === id
  }, [])

  const toggle = useCallback((id: string) => {
    // 划选刚结束时起点行那一下 click 不能当真点击（详情见文件头）
    if (isSuppressed(id)) return
    setSelectedIds(toggleIn(useStore.getState().selectedIds, id))
    setAnchorId(id)
  }, [isSuppressed, setSelectedIds])

  const selectRange = useCallback((id: string) => {
    // 区间两端都必须按**DOM 渲染顺序**取（见 `domOrder`）
    const order = domOrder(orderRef.current)
    const to = order.indexOf(id)
    const from = anchorId === null ? -1 : order.indexOf(anchorId)
    if (from < 0 || to < 0) {
      // 锚点不在已渲染的行里（换了筛选、锚点被删、或它还在未加载的分批中）——
      // 退化为单点加选而不是乱选一段
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

  /**
   * 文件夹选择 —— 入口是分组头左侧的复选框热区（见 `GroupHeader`）。
   *
   * ⚠️ 与文档选择**刻意不共用一套 id 集合**（理由见 docsSlice 的 `selectedFolderIds`）。
   * ⚠️ 也**不参与 Ctrl 划选**：划选表达的是「一段连续的文档行」，而分组头与文档行在
   * 同一条流里交错出现，混进同一条区间会让「第 3 行到第 9 行」取决于中间夹了几个分组头。
   */
  const toggleFolder = useCallback((id: string) => {
    setSelectedFolderIds(toggleIn(useStore.getState().selectedFolderIds, id))
    setFolderAnchorId(id)
  }, [setSelectedFolderIds])

  /** Shift + 点文件夹 = 替换成这一段（与文档的 Shift 同语义；锚点不更新以便来回伸缩） */
  const selectFolderRange = useCallback((id: string) => {
    const order = folderOrderRef.current
    const to = order.indexOf(id)
    const from = folderAnchorId === null ? -1 : order.indexOf(folderAnchorId)
    if (from < 0 || to < 0) {
      setSelectedFolderIds(toggleIn(useStore.getState().selectedFolderIds, id))
      setFolderAnchorId(id)
      return
    }
    const [a, b] = from <= to ? [from, to] : [to, from]
    setSelectedFolderIds(new Set(order.slice(a, b + 1)))
  }, [folderAnchorId, setSelectedFolderIds])

  const clear = useCallback(() => {
    // store 侧同时清文档与文件夹两个集合（见 `noSelection`）
    clearSelection()
    setAnchorId(null)
    setFolderAnchorId(null)
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

    const base = new Set(useStore.getState().selectedIds)
    const drag: DragState = {
      startId: id, lastId: id, moved: false,
      // ⚠️ **起点是否已选中决定方向**：从已选中的行走 = 整段取消，从未选中的行走 = 整段选中。
      // 这正是「Ctrl + 点击」的语义延伸（见文件头）。快照在 toggle 之前取 —— 起点的 click
      // 会被 suppress，所以此刻 `selectedIds` 仍是按下前的状态。
      base,
      deselect: base.has(id),
      x: e.clientX, y: e.clientY,
    }
    dragRef.current = drag
    const prevUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'
    /** 已排的滚动重算帧（0 = 没有待处理帧，见 `onScroll`） */
    let scrollFrame = 0

    /**
     * 把区间另一端推到 `rowId`。mousemove 与滚动后的重算**共用这一份**：
     * 两份各写一遍的话，「滚动后要不要重新算」迟早变成两套判据。
     */
    const applyTo = (d: DragState, rowId: string | null) => {
      // 行没变就不重算 —— mousemove 每秒几十次，每次都重建 Set 会让整列表跟着重渲染
      if (!rowId || rowId === d.lastId) return
      d.lastId = rowId
      d.moved = true
      // ⚠️ 区间按 **DOM 渲染顺序**取，不是数据顺序（见 `domOrder`）：分组会重排、
      // 折叠组不在 DOM 里，用数据顺序会选中另一段（还可能含看不见的文档）。
      const order = domOrder(orderRef.current)
      const a = order.indexOf(d.startId)
      const b = order.indexOf(rowId)
      // 起点滑出了当前结果集（滚动到分批外 / 筛选在拖动中变了）——停在已画出的部分
      if (a < 0 || b < 0) return
      const [lo, hi] = a <= b ? [a, b] : [b, a]
      // 无条件从 base 重算（不是在上一次结果上累积）：拖出去再拖回来结果一致，
      // 也让「区间缩小时被取消的行」自动回到 base 里的状态。
      const next = new Set(d.base)
      for (let i = lo; i <= hi; i++) {
        if (d.deselect) next.delete(order[i])
        else next.add(order[i])
      }
      setSelectedIds(next)
    }

    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      d.x = ev.clientX
      d.y = ev.clientY
      // ⚠️ 用 elementFromPoint 而不是 `ev.target`：指针跨到行之间的空隙/分组头时，
      // target 不再是行，而我们要的是「指针正下方是哪一行」。
      applyTo(d, docIdAt(d.x, d.y, ev.target))
    }

    /**
     * 划选期间滚动（滚轮 / 滚动条 / 键盘）后重新取指针下方的行。
     *
     * ⚠️ 滚动**不产生 mousemove**：不监听的话，用户「拖到底 → 滚轮继续往下」时指针下面的
     * 行已经换了、选区却停在原处，必须抖一下鼠标才接上（体感是「划选跟不上」）。
     * ⚠️ 用 `capture`：`scroll` 不冒泡，而真正滚动的是列表容器（不是 document）。
     * ⚠️ 等一帧再取：`scroll` 事件可能先于浏览器完成布局，此刻 `elementFromPoint` 仍
     * 返回滚动前的那个元素。
     */
    const onScroll = () => {
      // ⚠️ 每个 scroll 事件都排一个 rAF 的话，一次滚轮手势会堆起几十个回调，而它们
      // 最终算的是同一件事（指针没动、只是列表位置变了）。一帧内只留一个。
      if (scrollFrame) return
      scrollFrame = requestAnimationFrame(() => {
        scrollFrame = 0
        const cur = dragRef.current
        if (!cur) return
        applyTo(cur, docIdAt(cur.x, cur.y))
      })
    }

    const onUp = () => {
      const d = dragRef.current
      if (d?.moved) {
        // ⚠️ 只吞**起点行**那一下（见文件头）：划选结束后立刻去点另一行是完全正常的操作
        suppressRef.current = { id: d.startId, until: Date.now() + SUPPRESS_CLICK_MS }
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
        // 「Esc 取消了划选」在他松手的一刻自我推翻。同样只绑起点行。
        suppressRef.current = { id: d.startId, until: Date.now() + SUPPRESS_CLICK_MS }
      }
      cleanupRef.current?.()
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('keydown', onKey, true)
    // capture：`scroll` 不冒泡，真正滚动的是列表容器（见 onScroll 注释）
    document.addEventListener('scroll', onScroll, true)
    // ⚠️ 划选途中切走窗口（Alt+Tab / 点任务栏）收不到 mouseup —— 不监听 blur 的话
    // document 上的 mousemove 与 `body.userSelect='none'` 会一直留着：回到页面后
    // 鼠标随便一动就继续划选，且整页文字选不中。blur 时按「松手」处理并撤干净。
    window.addEventListener('blur', onUp)
    cleanupRef.current = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('blur', onUp)
      // 还没跑的重算帧一并撤掉：它捕获的 `dragRef` 会变 null，但撤掉更干净
      if (scrollFrame) { cancelAnimationFrame(scrollFrame); scrollFrame = 0 }
      document.body.style.userSelect = prevUserSelect
      dragRef.current = null
      cleanupRef.current = null
    }
  }, [setSelectedIds])

  /** 行点击统一入口 —— 三种修饰键三套语义，默认那套是**打开**（预览标签） */
  const handleRowClick = useCallback((e: React.MouseEvent, id: string) => {
    if (e.ctrlKey || e.metaKey) { toggle(id); return }
    if (e.shiftKey) { selectRange(id); return }
    // 划选结束后起点行那一下 click（普通单击）同样不该触发「打开文档」；
    // ⚠️ 只认起点行 —— 纯时间窗会把用户紧接着点开另一篇文档也一起吞掉
    if (isSuppressed(id)) return
    onOpen(id, 'preview')
  }, [toggle, selectRange, onOpen, isSuppressed])

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

  /** 文件夹复选框点击 —— Shift 走区间，其余 toggle（与文档行那套修饰键语义对齐） */
  const handleFolderSelect = useCallback((e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (e.shiftKey) selectFolderRange(id)
    else toggleFolder(id)
  }, [toggleFolder, selectFolderRange])

  return {
    selectedIds,
    selectedFolderIds,
    /** 批量条的显隐条件 —— **派生**而非独立状态，故不可能与选择集漂移。
     *  ⚠️ 两个集合都算「有选择」：只选了文件夹时批量条也要出现（它有文件夹动作）。 */
    hasSelection: selectedIds.size > 0 || selectedFolderIds.size > 0,
    toggle,
    selectRange,
    setAll,
    clear,
    handleRowClick,
    handleRowMouseDown,
    handleRowDoubleClick,
    handleFolderSelect,
    onListKeyDown,
  }
}

/** 返回新的 Set（不可变更新）——就地 add/delete 不会触发订阅者更新 */
function toggleIn(prev: Set<string>, id: string): Set<string> {
  const next = new Set(prev)
  if (next.has(id)) next.delete(id); else next.add(id)
  return next
}
