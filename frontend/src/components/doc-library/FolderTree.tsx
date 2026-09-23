/**
 * 文件夹树（**单层**，扁平列表）— 「全部 / 未分类 / 各文件夹」+ 增删改 + 拖放目标。
 *
 * ⚠️ 拖拽实现要点（原生 HTML5 DnD，零新依赖）：
 * - 每行 `onDragOver` **必须** `e.preventDefault()`，否则浏览器判定「不可放置」，drop 永不触发。
 * - `onDragOver` 阶段**读不到 payload**（浏览器安全限制，getData 返回空串），
 *   所以「正在悬停哪一行」只能靠本地 state，不能靠「payload 是否匹配」。
 * - 高亮必须由父级传入的 `dragging` 驱动：没有它就无法区分「拖文档经过」与「鼠标划过」。
 */
import { useState } from 'react'
import { Folder as FolderIcon, FolderPlus, FolderOpen, Pencil, Trash2, Check, X } from 'lucide-react'
import type { FolderEntry } from '../../store/types'

export interface FolderTreeProps {
  folders: FolderEntry[]
  /** null = 全部；'' = 未分类；其他 = 文件夹 id */
  activeFolderId: string | null
  totalCount: number
  uncategorizedCount: number
  /** 是否正在拖拽文档（决定要不要把行渲染成放置目标） */
  dragging: boolean
  onSelect: (id: string | null) => void
  onCreate: (name: string) => void
  onRename: (id: string, name: string) => void
  onDelete: (folder: FolderEntry) => void
  onDrop: (folderId: string, e: React.DragEvent) => void
}

export function FolderTree({
  folders, activeFolderId, totalCount, uncategorizedCount,
  dragging, onSelect, onCreate, onRename, onDelete, onDrop,
}: FolderTreeProps) {
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<FolderEntry | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)

  const commitCreate = () => {
    const name = draft.trim()
    setCreating(false)
    setDraft('')
    if (name) onCreate(name)
  }

  const commitRename = (id: string) => {
    const name = draft.trim()
    setRenamingId(null)
    setDraft('')
    if (name) onRename(id, name)
  }

  /** 行的拖放行为 —— 只有真实行（非「全部」）才接受放置 */
  const dropProps = (key: string) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!dragging) return
      e.preventDefault()          // ⚠️ 缺这行 drop 永远不会触发
      e.dataTransfer.dropEffect = 'move'
      if (hoverId !== key) setHoverId(key)
    },
    onDragLeave: (e: React.DragEvent) => {
      // contains 判定避免进入子元素时误触发 leave（否则高亮闪烁）
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setHoverId(null)
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault()
      setHoverId(null)
      onDrop(key, e)
    },
  })

  const rowClass = (active: boolean, key: string) =>
    `group/f flex items-center gap-1.5 px-2 py-1 rounded-md cursor-pointer text-[12px] transition-colors ${
      hoverId === key
        ? 'bg-accent/15 text-accent ring-1 ring-accent/40'
        : active
          ? 'bg-accent/10 text-accent font-medium'
          : 'text-dimmed hover:bg-surface-hover'
    }`

  return (
    <div className="px-3 pb-1.5">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] font-medium text-disabled">文件夹</span>
        <button
          onClick={() => { setCreating(true); setDraft('') }}
          className="w-5 h-5 rounded hover:bg-surface-hover flex items-center justify-center"
          title="新建文件夹"
        >
          <FolderPlus size={13} className="text-muted" />
        </button>
      </div>

      {creating && (
        <input
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') commitCreate()
            if (e.key === 'Escape') { setCreating(false); setDraft('') }
          }}
          onBlur={commitCreate}
          autoFocus
          placeholder="文件夹名"
          className="w-full mb-1 px-2 py-1 text-[12px] bg-elevated border border-accent rounded-md outline-none"
        />
      )}

      <div className="space-y-0.5 max-h-[150px] overflow-y-auto">
        {/* 「全部」不接受放置——它不是容器，放上去没有语义 */}
        <div className={rowClass(activeFolderId === null, '__all__')}
          onClick={() => onSelect(null)}>
          <FolderOpen size={13} className="shrink-0" />
          <span className="flex-1 truncate">全部</span>
          <span className="text-[10px] text-disabled">{totalCount}</span>
        </div>

        {folders.map(f => (
          <div key={f.id}>
            {renamingId === f.id ? (
              <input
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') commitRename(f.id)
                  if (e.key === 'Escape') { setRenamingId(null); setDraft('') }
                }}
                onBlur={() => commitRename(f.id)}
                autoFocus
                className="w-full px-2 py-1 text-[12px] bg-elevated border border-accent rounded-md outline-none"
              />
            ) : (
              <div
                className={rowClass(activeFolderId === f.id, f.id)}
                onClick={() => onSelect(f.id)}
                {...dropProps(f.id)}
              >
                <FolderIcon size={13} className="shrink-0" />
                <span className="flex-1 truncate" title={f.name}>{f.name}</span>
                <span className="text-[10px] text-disabled group-hover/f:hidden">{f.count}</span>
                <span className="hidden group-hover/f:flex items-center gap-0.5">
                  <button
                    onClick={e => { e.stopPropagation(); setRenamingId(f.id); setDraft(f.name) }}
                    className="w-4 h-4 rounded hover:bg-surface flex items-center justify-center" title="重命名"
                  >
                    <Pencil size={10} />
                  </button>
                  <button
                    onClick={e => { e.stopPropagation(); setConfirmDelete(f) }}
                    className="w-4 h-4 rounded hover:bg-error/10 flex items-center justify-center" title="删除文件夹"
                  >
                    <Trash2 size={10} className="hover:text-error" />
                  </button>
                </span>
              </div>
            )}
          </div>
        ))}

        <div className={rowClass(activeFolderId === '', '__uncat__')}
          onClick={() => onSelect('')} {...dropProps('')}>
          <FolderIcon size={13} className="shrink-0 opacity-50" />
          <span className="flex-1 truncate">未分类</span>
          <span className="text-[10px] text-disabled">{uncategorizedCount}</span>
        </div>
      </div>

      {/* 删除确认 — 行内二次确认，明确说明「文档不会被删」 */}
      {confirmDelete && (
        <div className="mt-1.5 px-2 py-1.5 rounded-md bg-error/5 border border-error/20">
          <p className="text-[11px] text-dimmed">
            删除「{confirmDelete.name}」？其中 {confirmDelete.count} 个文档将移到未分类。
          </p>
          <div className="flex items-center gap-1 mt-1">
            <button
              onClick={() => { onDelete(confirmDelete); setConfirmDelete(null) }}
              className="flex items-center gap-0.5 px-1.5 py-0.5 text-[11px] rounded bg-error/10 text-error hover:bg-error/20"
            >
              <Check size={11} />删除
            </button>
            <button
              onClick={() => setConfirmDelete(null)}
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
