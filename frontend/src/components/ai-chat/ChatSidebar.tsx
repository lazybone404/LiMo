/**
 * ChatSidebar — 会话列表。
 *
 * 搜索过滤 + 归档折叠（archived 字段，P5 后端）+ 右键菜单（重命名/归档/导出/删除）。
 * 置顶语义由后端 updated_at 降序天然实现，不做物理拖拽。
 */
import { useEffect, useMemo, useState } from 'react'
import { useChatStore, type ChatListItem } from '../../store/chatStore'
import { useStore } from '../../store'
import { deleteConversation, renameConversation, archiveConversation } from '../../api/client'
import { Search, Trash2, Pencil, Download, Archive, ArchiveRestore, MessageSquare, ChevronDown, ChevronRight } from 'lucide-react'
import { ContextMenu } from '../ui/ContextMenu'
import { SCALES, type ChatScale } from './scale'

interface ChatSidebarProps {
  conversations: ChatListItem[]
  currentId: string | null
  compact: boolean
  scale: ChatScale
  onSwitch: (id: string) => void
  onNew: () => void
}

interface MenuEntry {
  x: number; y: number; id: string; title: string
}

export function ChatSidebar({ conversations, currentId, compact, scale, onSwitch, onNew }: ChatSidebarProps) {
  const s = SCALES[scale]
  const addToast = useStore(st => st.addToast)

  const [search, setSearch] = useState('')
  const [archivedOpen, setArchivedOpen] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useState<MenuEntry | null>(null)

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    const list = q ? conversations.filter(c => c.title.toLowerCase().includes(q)) : conversations
    return { active: list.filter(c => !c.archived), archived: list.filter(c => c.archived) }
  }, [conversations, search])

  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [contextMenu])

  const patchConv = (id: string, patch: Partial<ChatListItem>) => {
    useChatStore.setState({ conversations: useChatStore.getState().conversations.map(c => c.id === id ? { ...c, ...patch } : c) })
  }

  const doRename = (id: string, title: string) => {
    renameConversation(id, title)
    patchConv(id, { title })
    setRenamingId(null)
  }

  const doArchive = (id: string, archived: boolean) => {
    archiveConversation(id, archived)
    patchConv(id, { archived })
    addToast(archived ? '已归档' : '已取消归档')
  }

  const doDelete = (id: string) => {
    deleteConversation(id)
    useChatStore.setState({ conversations: useChatStore.getState().conversations.filter(c => c.id !== id) })
    if (currentId === id) onNew()
    setDeletingId(null)
  }

  const doExport = (id: string, title: string) => {
    setContextMenu(null)
    void (async () => {
      try {
        const { downloadFromUrl } = await import('../../lib/download')
        const result = await downloadFromUrl(`/export/conversation/${id}?format=md`, `${title || '对话'}.md`)
        if (result.ok) addToast('对话已导出，请查看下载文件夹')
        else addToast(`导出失败: ${result.error}`, 'error')
      } catch (e) {
        addToast('导出失败: ' + (e instanceof Error ? e.message : String(e)), 'error')
      }
    })()
  }

  const menuItems = (entry: MenuEntry) => {
    const item = conversations.find(c => c.id === entry.id)
    return [
      { label: '重命名', icon: <Pencil size={12} />, onClick: () => { setRenamingId(entry.id); setRenameValue(entry.title); setContextMenu(null) } },
      item?.archived
        ? { label: '取消归档', icon: <ArchiveRestore size={12} />, onClick: () => { doArchive(entry.id, false); setContextMenu(null) } }
        : { label: '归档', icon: <Archive size={12} />, onClick: () => { doArchive(entry.id, true); setContextMenu(null) } },
      { label: '导出 Markdown', icon: <Download size={12} />, onClick: () => doExport(entry.id, entry.title) },
      { label: '删除', icon: <Trash2 size={12} />, danger: true, onClick: () => { setDeletingId(entry.id); setContextMenu(null) } },
    ]
  }

  const renderItem = (c: ChatListItem) => (
    <button key={c.id}
      onClick={() => onSwitch(c.id)}
      onContextMenu={e => { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY, id: c.id, title: c.title }) }}
      className={`w-full truncate text-left ${s.listItem} rounded-lg ${s.listText} transition-colors ${c.id === currentId ? 'bg-accent/10 font-medium text-accent' : 'text-dimmed hover:bg-surface'}`}>
      {compact ? <MessageSquare size={s.icon} className="mx-auto" /> : c.title}
    </button>
  )

  return (
    <div className="flex h-full flex-col">
      <div className={`px-${compact ? 1.5 : 2} py-${compact ? 1.5 : 2}`}>
        <div className="flex items-center gap-1.5 rounded-lg bg-surface px-2 py-1">
          <Search size={12} className="text-disabled" />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder={compact ? '' : '搜索对话...'} className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-disabled" />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-1">
        {filtered.active.map(renderItem)}

        {filtered.archived.length > 0 && (
          <>
            <button onClick={() => setArchivedOpen(v => !v)}
              className="flex w-full items-center gap-1 px-2 py-1 text-[10px] uppercase text-disabled transition-colors hover:text-dimmed">
              {archivedOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              归档 ({filtered.archived.length})
            </button>
            {archivedOpen && filtered.archived.map(renderItem)}
          </>
        )}

        {filtered.active.length === 0 && filtered.archived.length === 0 && (
          <div className="py-4 text-center text-[11px] text-disabled">暂无对话</div>
        )}
      </div>

      {/* 右键菜单 */}
      {contextMenu && !renamingId && deletingId !== contextMenu.id && (
        <ContextMenu x={contextMenu.x} y={contextMenu.y} onClose={() => setContextMenu(null)} items={menuItems(contextMenu)} />
      )}

      {/* 重命名弹窗 */}
      {renamingId && (
        <div className="fixed inset-0 z-[10001] flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setRenamingId(null)}>
          <div className="mx-4 w-full max-w-[320px] rounded-2xl border border-border-light bg-elevated p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-4 text-[17px] font-semibold">重命名对话</h3>
            <input autoFocus value={renameValue} onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && renameValue.trim()) doRename(renamingId, renameValue.trim()); else if (e.key === 'Escape') setRenamingId(null) }}
              className="mb-4 w-full rounded-lg border border-border-default bg-surface px-3 py-2 text-[13px] outline-none" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setRenamingId(null)} className="rounded-lg px-4 py-2 text-[13px] text-dimmed hover:bg-surface">取消</button>
              <button onClick={() => doRename(renamingId, renameValue.trim())} className="rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-white">确定</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {deletingId && (
        <div className="fixed inset-0 z-[10001] flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setDeletingId(null)}>
          <div className="mx-4 w-full max-w-[320px] rounded-2xl border border-border-light bg-elevated p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-2 text-[17px] font-semibold">删除对话</h3>
            <p className="mb-6 text-[13px] text-dimmed">此操作不可撤销。</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeletingId(null)} className="rounded-lg px-4 py-2 text-[13px] text-dimmed hover:bg-surface">取消</button>
              <button onClick={() => doDelete(deletingId)} className="rounded-lg bg-error px-4 py-2 text-[13px] font-medium text-white">删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
