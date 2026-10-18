/**
 * 列表分组头 —— 文件夹 / 类型 / 时间三个维度共用。
 *
 * 前身是 FolderTree.tsx（文件夹树 + 列表上下分区、各自滚动）；2026-09-25 重设计后
 * 文件夹不再是独立树，而是「排列方式」的一个分组维度，与类型/时间共用这一个头组件。
 *
 * ⚠️ 增删改（重命名 / 删除 / 接受拖放）**只对文件夹组开放**（`group.editable`）：
 * 类型和时间的组名是派生值，「把 PDF 组改叫视频」没有语义。用一个显式标志区分，
 * 而不是让每个调用点各自 `groupBy === 'folder'`——那种判据散开几处后必然漏一处。
 *
 * ⚠️ 徽标数字**不是** `group.docs.length`，而是调用方传入的 `count`（该文件夹的**全量**文档数）：
 * 筛选到「PDF」时组里只有 2 行，但删除这个文件夹会搬走全部 12 篇。徽标若是筛选后的数，
 * 删除确认就成了骗人的——用户按 2 篇的心理预期点下去，实际动了 12 篇。
 *
 * ⚠️ `compact` 的行高 **26px 必须与 `DocRow` 紧凑行同值**（IDE 文件树形态下父节点与
 * 子节点同高才成树）。两处是**成对的常量**，改一处必须改另一处——不共用一个导出的
 * 常量是因为它们落在两个不同组件的 Tailwind 类里，抽出来反而要绕过 Tailwind 的静态扫描。
 */
import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Folder, FolderPlus, Pencil, Trash2, Check, X } from 'lucide-react'
import type { DocGroup } from './useDocQuery'

/** 缩进最多推到这一层（再深就只剩图标了，完整路径见 `title`） */
const MAX_INDENT_DEPTH = 6

/**
 * 父级要求某个分组头进入某个就地状态（新建子夹 / 重命名 / 删除确认）。
 *
 * ⚠️ **为什么要有这个信号**：文件夹右键菜单住在 `DocLibrary`（编排层），而三种就地
 * 交互的状态都住在本组件内部（输入框、确认框都是本地 state）。用 `document.querySelector`
 * 找按钮再 `.click()` 也能跑通，但它依赖 DOM 结构、依赖该分组头**已经渲染出来**
 * （父级收着时它根本不在 DOM 里），是本类问题里最脆弱的一种写法。
 */
export interface HeaderAction { kind: 'newChild' | 'rename' | 'delete'; id: string }

export interface GroupHeaderProps {
  group: DocGroup
  expanded: boolean
  /** 组内文档数（全量，见文件头）。文件夹组传的是**含子树**的总数 */
  count: number
  /** 该文件夹的增删改进行中 —— 禁用入口，防重复发起 */
  busy: boolean
  /** 紧凑行高（26px）—— 简略模式的 IDE 文件树里与文件行同高，见文件头 */
  compact?: boolean
  /** 树形层级（0 = 根级）；非文件夹维度恒 0 */
  depth?: number
  /** 完整路径（`父 › 子`）—— 放进 `title`，缩进夹紧后仍能读全 */
  path?: string
  /**
   * **整棵子树**的文件夹行数（不含自身；决定删除确认里要不要提「及其 N 个子文件夹」）。
   * ⚠️ 不是直接子级数：删除是整棵子树一起删，与 toast 的 `deletedFolders - 1` 同源。
   */
  descendantCount?: number
  onToggle: (key: string) => void
  /** 以下四项只有文件夹组会传（`group.editable` 时） */
  onRename?: (id: string, name: string) => Promise<boolean>
  onDelete?: (id: string) => void
  onDropDocs?: (folderId: string, e: React.DragEvent) => void
  /** 就地新建子文件夹：`(parentId, name) => 是否成功` */
  onNewChild?: (parentId: string, name: string) => Promise<boolean>
  /** 右键分组头（文件夹菜单：新建子夹 / 重命名 / 移动到… / 删除） */
  onContextMenu?: (e: React.MouseEvent, id: string) => void
  /**
   * 「请进入某个就地状态」的信号（见 `HeaderAction`）。
   * 本组件消费后立刻回调 `onActionConsumed` 把它清掉 —— 否则用户取消后再右键同一个夹，
   * 值没变 → effect 不跑 → 看起来点了没反应。
   */
  actionFor?: HeaderAction | null
  onActionConsumed?: () => void
}

export function GroupHeader({
  group, expanded, count, busy, compact, depth = 0, path, descendantCount = 0,
  onToggle, onRename, onDelete, onDropDocs, onNewChild, onContextMenu,
  actionFor, onActionConsumed,
}: GroupHeaderProps) {
  const [renaming, setRenaming] = useState(false)
  const [creatingChild, setCreatingChild] = useState(false)
  const [draft, setDraft] = useState('')
  const [confirming, setConfirming] = useState(false)
  /** 提交中 —— 防 Enter 提交与紧随的 blur 各发一次请求 */
  const [commitBusy, setCommitBusy] = useState(false)
  const [hover, setHover] = useState(false)

  const Chevron = expanded ? ChevronDown : ChevronRight
  const canEdit = group.editable && !!onRename && !!onDelete
  const label = path || group.label
  /** 缩进夹紧：深嵌套下把标题挤成一条缝比「看不出层级」更糟，完整路径在 title 里 */
  const indent = Math.min(depth, MAX_INDENT_DEPTH) * (compact ? 16 : 12)

  useEffect(() => {
    if (!actionFor || actionFor.id !== group.ref) return
    if (actionFor.kind === 'newChild') { setCreatingChild(true); setDraft('') }
    // 重命名的草稿是**当前名字**（与行内铅笔按钮同一初始态）
    else if (actionFor.kind === 'rename') { setRenaming(true); setDraft(group.label) }
    else if (actionFor.kind === 'delete') setConfirming(true)
    onActionConsumed?.()
    // ⚠️ 依赖**只有信号的形状**。`group.label` / `onActionConsumed` 被刻意排除：
    // 父级每次重渲染都会给一个新的 `onActionConsumed` 闭包 → 若列进依赖，本 effect
    // 会在「用户正在输入框里打新名字」时被无端重跑一次，`setDraft(group.label)`
    // 把刚打的字覆盖成原名（输入框每敲一个字都可能被重置）。
    // 闭包里的这两个值来自**触发本 effect 的那次渲染** —— 父级发出 actionFor 的同一批
    // props，正是当次应有的取值，不「过期」。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionFor, group.ref])

  // ⚠️ 成功才清输入框：失败时保留草稿，用户重试一次即可，而不是把刚打的文件夹名弄丢
  const commitRename = async () => {
    if (commitBusy || !onRename) return
    const name = draft.trim()
    if (!name) { setRenaming(false); setDraft(''); return }
    setCommitBusy(true)
    try {
      if (await onRename(group.ref, name)) { setRenaming(false); setDraft('') }
    } finally { setCommitBusy(false) }
  }

  // 与 commitRename 同构（共用 draft / commitBusy）：失败保留草稿让用户重试
  const commitCreateChild = async () => {
    if (commitBusy || !onNewChild) return
    const name = draft.trim()
    if (!name) { setCreatingChild(false); setDraft(''); return }
    setCommitBusy(true)
    try {
      if (await onNewChild(group.ref, name)) { setCreatingChild(false); setDraft('') }
    } finally { setCommitBusy(false) }
  }

  const dropProps = canEdit && onDropDocs ? {
    onDragOver: (e: React.DragEvent) => {
      // ⚠️ 判据是 payload **类型**而不是父级的 dragging state：dragOver 阶段读不到数据
      // （getData 返回空串），但 types 可读；而 dragging 要等 setState 生效才为 true，
      // 快速拖入时读到旧值就漏了 preventDefault，drop 永不触发。
      // 这个判据同时把「从资源管理器拖文件进来」挡在外面。
      if (!e.dataTransfer.types.includes('application/x-limo-doc')) return
      e.preventDefault()          // ⚠️ 缺这行 drop 永远不会触发
      e.dataTransfer.dropEffect = 'move'
      setHover(true)
    },
    onDragLeave: (e: React.DragEvent) => {
      // contains 判定避免进入子元素时误触发 leave（否则高亮闪烁）
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setHover(false)
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault()
      setHover(false)
      onDropDocs(group.ref, e)
    },
  } : {}

  return (
    <div
      data-group-header={group.key}
      data-group-depth={depth}
      className={`rounded-lg ${hover ? 'bg-accent/10 ring-1 ring-accent/40' : ''}`}
      {...(canEdit && onContextMenu
        ? { onContextMenu: (e: React.MouseEvent) => onContextMenu(e, group.ref) }
        : {})}
      {...dropProps}
    >
      <div
        data-group-compact={compact ? 'true' : undefined}
        className={`group/g flex items-center gap-1 pr-2 ${compact ? 'h-6.5' : ''}`}
        style={{ paddingLeft: 4 + indent }}
      >
        {renaming ? (
          <input
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') void commitRename()
              if (e.key === 'Escape') { setRenaming(false); setDraft('') }
            }}
            onBlur={commitRename}
            autoFocus
            className="flex-1 my-0.5 px-2 py-0.5 text-[12px] bg-elevated border border-accent rounded-md outline-none"
          />
        ) : creatingChild ? (
          <input
            data-new-child-input={group.key}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') void commitCreateChild()
              if (e.key === 'Escape') { setCreatingChild(false); setDraft('') }
            }}
            onBlur={commitCreateChild}
            autoFocus
            placeholder="子文件夹名"
            className="flex-1 my-0.5 px-2 py-0.5 text-[12px] bg-elevated border border-accent rounded-md outline-none"
          />
        ) : (
          <>
            <button
              data-group-toggle={group.key}
              onClick={() => onToggle(group.key)}
              aria-expanded={expanded}
              className={`flex items-center gap-1 flex-1 min-w-0 text-left rounded hover:bg-surface-hover ${compact ? 'h-full' : 'py-1'}`}
            >
              <Chevron size={12} className="text-disabled shrink-0" />
              {group.editable && <Folder size={13} className="text-disabled shrink-0" />}
              {/* ⚠️ `title` 放**完整路径**而不是 `group.label`：缩进在 6 层夹紧后，
                  深层的同名夹只能靠它区分（`技术 › 前端 › React` vs `前端 › React`） */}
              <span className="text-[12px] font-medium text-dimmed truncate" title={label}>
                {group.label}
              </span>
              <span className="text-[10px] text-disabled shrink-0">{count}</span>
            </button>
            {/* ⚠️ `opacity-0` 只隐藏视觉、**不阻止点击**：没有 pointer-events-none 时
                这些按钮在看不见的状态下依然可点（误触删除文件夹）。另配 focus-within
                给 Tab 用户一条能看见它们的路径——pointer-events 管鼠标不管键盘。 */}
            {canEdit && (
              <span className="opacity-0 pointer-events-none group-hover/g:opacity-100 group-hover/g:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto transition-opacity flex items-center gap-0.5 shrink-0">
                {onNewChild && (
                  <button
                    data-group-new-child={group.key}
                    onClick={() => { setCreatingChild(true); setDraft('') }}
                    disabled={busy}
                    className="w-4 h-4 rounded hover:bg-surface flex items-center justify-center disabled:opacity-40 disabled:hover:bg-transparent"
                    title="新建子文件夹"
                  >
                    <FolderPlus size={10} className="text-disabled" />
                  </button>
                )}
                <button
                  onClick={() => { setRenaming(true); setDraft(group.label) }}
                  disabled={busy}
                  className="w-4 h-4 rounded hover:bg-surface flex items-center justify-center disabled:opacity-40 disabled:hover:bg-transparent"
                  title="重命名文件夹"
                >
                  <Pencil size={10} className="text-disabled" />
                </button>
                <button
                  onClick={() => setConfirming(true)}
                  disabled={busy}
                  className="w-4 h-4 rounded hover:bg-error/10 flex items-center justify-center disabled:opacity-40 disabled:hover:bg-transparent"
                  title="删除文件夹"
                >
                  <Trash2 size={10} className="text-disabled hover:text-error" />
                </button>
              </span>
            )}
          </>
        )}
      </div>

      {/* 删除确认 —— 行内二次确认，明确说明「文档不会被删」。
          ⚠️ 删除是**整棵子树**（各层文档全部回落未分类），故文案必须报出子文件夹个数：
          只说「其中 N 个文档」会让用户以为只删这一个夹。`count` 是含子树的文档数。 */}
      {confirming && (
        <div className="mx-1 mb-1 px-2 py-1.5 rounded-md bg-error/5 border border-error/20">
          <p className="text-[11px] text-dimmed">
            删除「{label}」{descendantCount > 0 ? `及其 ${descendantCount} 个子文件夹` : ''}？
            其中 {count} 个文档将移到未分类（文档不会被删除）。
          </p>
          <div className="flex items-center gap-1 mt-1">
            <button
              data-confirm-delete-folder
              onClick={() => { onDelete?.(group.ref); setConfirming(false) }}
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[11px] rounded bg-error/10 text-error hover:bg-error/20"
            >
              <Check size={11} />删除
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[11px] rounded hover:bg-surface-hover text-muted"
            >
              <X size={11} />取消
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
