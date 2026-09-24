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
import { useState } from 'react'
import { ChevronDown, ChevronRight, Folder, Pencil, Trash2, Check, X } from 'lucide-react'
import type { DocGroup } from './useDocQuery'

export interface GroupHeaderProps {
  group: DocGroup
  expanded: boolean
  /** 组内文档数（全量，见文件头） */
  count: number
  /** 该文件夹的增删改进行中 —— 禁用入口，防重复发起 */
  busy: boolean
  /** 紧凑行高（26px）—— 简略模式的 IDE 文件树里与文件行同高，见文件头 */
  compact?: boolean
  onToggle: (key: string) => void
  /** 以下三项只有文件夹组会传（`group.editable` 时） */
  onRename?: (id: string, name: string) => Promise<boolean>
  onDelete?: (id: string) => void
  onDropDocs?: (folderId: string, e: React.DragEvent) => void
}

export function GroupHeader({
  group, expanded, count, busy, compact, onToggle, onRename, onDelete, onDropDocs,
}: GroupHeaderProps) {
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState('')
  const [confirming, setConfirming] = useState(false)
  /** 提交中 —— 防 Enter 提交与紧随的 blur 各发一次请求 */
  const [commitBusy, setCommitBusy] = useState(false)
  const [hover, setHover] = useState(false)

  const Chevron = expanded ? ChevronDown : ChevronRight
  const canEdit = group.editable && !!onRename && !!onDelete

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
      className={`rounded-lg ${hover ? 'bg-accent/10 ring-1 ring-accent/40' : ''}`}
      {...dropProps}
    >
      <div
        data-group-compact={compact ? 'true' : undefined}
        className={`group/g flex items-center gap-1 pl-1 pr-2 ${compact ? 'h-6.5' : ''}`}
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
              <span className="text-[12px] font-medium text-dimmed truncate" title={group.label}>
                {group.label}
              </span>
              <span className="text-[10px] text-disabled shrink-0">{count}</span>
            </button>
            {/* ⚠️ `opacity-0` 只隐藏视觉、**不阻止点击**：没有 pointer-events-none 时
                这些按钮在看不见的状态下依然可点（误触删除文件夹）。另配 focus-within
                给 Tab 用户一条能看见它们的路径——pointer-events 管鼠标不管键盘。 */}
            {canEdit && (
              <span className="opacity-0 pointer-events-none group-hover/g:opacity-100 group-hover/g:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto transition-opacity flex items-center gap-0.5 shrink-0">
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

      {/* 删除确认 —— 行内二次确认，明确说明「文档不会被删」 */}
      {confirming && (
        <div className="mx-1 mb-1 px-2 py-1.5 rounded-md bg-error/5 border border-error/20">
          <p className="text-[11px] text-dimmed">
            删除「{group.label}」？其中 {count} 个文档将移到未分类。
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
