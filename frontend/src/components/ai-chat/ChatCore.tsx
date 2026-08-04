/**
 * ChatCore — 对话模块容器（插槽面板 / 浮窗共用，纯组件）。
 *
 * 状态全部来自独立 chatStore（消息/会话/流式）+ 主 store 的 currentDocId/docs/addToast。
 * 结构：标题栏 + [会话列表 | 文档badge+消息列表+输入区 | 用户问题导航侧栏]。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useStore } from '../../store'
import { useChatStore } from '../../store/chatStore'
import { deleteConversation, renameConversation } from '../../api/client'
import { Search, Plus, Trash2, Pencil, Download, MessageSquare, ChevronRight, ChevronLeft } from 'lucide-react'
import { ContextMenu } from '../ui/ContextMenu'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { SCALES, type ChatScale } from './scale'

interface ChatCoreProps {
  compact: boolean
  listWidth: number
  navWidth: number
  showNav: boolean
  listCollapsed: boolean
  onListCollapsedChange: (v: boolean) => void
  scale?: ChatScale
  titleLeft?: ReactNode
  titleRight?: ReactNode
  /** Floating 模式的标题栏拖拽，AIPanel 不传 */
  onTitleMouseDown?: (e: React.MouseEvent) => void
}

interface ConvItem { id: string; title: string; archived?: boolean }

export function ChatCore({
  compact, listWidth, navWidth, showNav,
  listCollapsed, onListCollapsedChange,
  scale = 'md',
  titleLeft, titleRight,
  onTitleMouseDown,
}: ChatCoreProps) {
  const s = SCALES[scale]

  // ── chatStore 状态 ──
  const conversations = useChatStore(st => st.conversations)
  const currentId = useChatStore(st => st.currentId)
  const topic = useChatStore(st => st.topic)
  const messages = useChatStore(st => st.messages)
  const streaming = useChatStore(st => st.streaming)
  const loadConversations = useChatStore(st => st.loadConversations)
  const openConversation = useChatStore(st => st.openConversation)
  const newChat = useChatStore(st => st.newChat)

  // ── 主 store 状态（跨域）──
  const currentDocId = useStore(st => st.currentDocId)
  const docs = useStore(st => st.docs)
  const addToast = useStore(st => st.addToast)

  const [search, setSearch] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; id: string; title: string } | null>(null)

  // 初始加载 + 恢复上次会话
  useEffect(() => {
    (async () => {
      await loadConversations()
      const list = useChatStore.getState().conversations
      const lastId = (() => { try { return localStorage.getItem('mbw_lastConvId') } catch { return null } })()
      const target = lastId && list.some(c => c.id === lastId) ? lastId : null
      if (target) openConversation(target)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 切换会话
  const switchConv = (id: string) => {
    if (id === currentId || streaming) return
    openConversation(id)
    try { localStorage.setItem('mbw_lastConvId', id) } catch { /* quota */ }
  }

  // 新建会话
  const newConv = () => {
    if (streaming) return
    newChat()
    try { localStorage.removeItem('mbw_lastConvId') } catch { /* quota */ }
  }

  // 右键菜单关闭
  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [contextMenu])

  // 会话列表过滤
  const filtered = useMemo(() => {
    const list = conversations as ConvItem[]
    return search ? list.filter(c => c.title.toLowerCase().includes(search.toLowerCase())) : list
  }, [conversations, search])

  // 用户问题导航
  const userQuestions = messages
    .filter(m => m.role === 'user')
    .map((m, i) => ({ id: m.id, text: m.content.slice(0, 30) + (m.content.length > 30 ? '...' : ''), index: i + 1 }))

  const currentTitle = (currentId ? (conversations.find(c => c.id === currentId)?.title ?? topic) : null) || '新对话'

  const doRename = (id: string, title: string) => {
    renameConversation(id, title)
    useChatStore.setState({ conversations: useChatStore.getState().conversations.map(c => c.id === id ? { ...c, title } : c) })
    setRenamingId(null)
  }

  const doDelete = (id: string) => {
    deleteConversation(id)
    useChatStore.setState({ conversations: useChatStore.getState().conversations.filter(c => c.id !== id) })
    if (currentId === id) { newChat(); try { localStorage.removeItem('mbw_lastConvId') } catch { /* quota */ } }
    setDeletingId(null)
  }

  return (
    <>
      {/* 标题栏 */}
      <div className={`shrink-0 border-b border-border-light ${onTitleMouseDown ? 'cursor-move select-none' : ''}`} onMouseDown={onTitleMouseDown}>
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2">
            {titleLeft}
            {!compact && (
              <button onClick={() => onListCollapsedChange(!listCollapsed)} className="rounded p-0.5 text-muted hover:bg-surface-hover">
                {listCollapsed ? <ChevronRight size={scale === 'sm' ? 12 : 14} /> : <ChevronLeft size={scale === 'sm' ? 12 : 14} />}
              </button>
            )}
            {scale === 'sm' ? null : <><span className="h-2 w-2 rounded-full bg-success" /><span className="text-[13px] font-semibold text-dimmed">AI 对话</span></>}
          </div>
          <div className="flex items-center gap-0.5">
            <button onClick={newConv} className="rounded-md p-1 text-muted hover:bg-surface-hover" title="新建对话"><Plus size={14} /></button>
            {compact && <button onClick={() => onListCollapsedChange(!listCollapsed)} className="rounded-md p-1 text-muted hover:bg-surface-hover" title="对话列表"><MessageSquare size={14} /></button>}
            {titleRight}
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden" style={{ minHeight: 0 }}>
        {/* 会话列表 */}
        {listCollapsed ? (
          <div className="flex w-8 shrink-0 flex-col items-center gap-2 border-r border-border-light px-1 py-2">
            <button onClick={() => onListCollapsedChange(false)} className="rounded p-1 text-muted hover:bg-surface-hover" title="展开列表"><ChevronRight size={14} /></button>
            <button onClick={newConv} className="rounded p-1 text-muted hover:bg-surface-hover" title="新建"><Plus size={14} /></button>
          </div>
        ) : (
          <div style={{ width: listWidth, minWidth: compact ? 0 : 80 }} className="flex shrink-0 flex-col border-r border-border-light">
            <div className={`px-${compact ? 1.5 : 2} py-${compact ? 1.5 : 2}`}>
              <div className="flex items-center gap-1.5 rounded-lg bg-surface px-2 py-1">
                <Search size={12} className="text-disabled" />
                <input value={search} onChange={e => setSearch(e.target.value)} placeholder={compact ? '' : '搜索对话...'} className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-disabled" />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto px-1">
              {filtered.map(c => (
                <button key={c.id}
                  onClick={() => switchConv(c.id)}
                  onContextMenu={e => { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY, id: c.id, title: c.title }) }}
                  className={`w-full truncate text-left ${s.listItem} rounded-lg ${s.listText} transition-colors ${c.id === currentId ? 'bg-accent/10 font-medium text-accent' : 'text-dimmed hover:bg-surface'}`}>
                  {compact ? <MessageSquare size={s.icon} className="mx-auto" /> : c.title}
                </button>
              ))}
              {filtered.length === 0 && <div className="py-4 text-center text-[11px] text-disabled">暂无对话</div>}
            </div>
          </div>
        )}

        {/* 消息区 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center gap-1 border-b border-border-light px-3 py-1.5 text-[11px]">
            {currentDocId ? (
              <span className="rounded-full bg-accent/8 px-2 py-0.5 text-accent">{docs.find(d => d.id === currentDocId)?.title?.slice(0, 15) || '文档'}</span>
            ) : (
              <span className="text-disabled">未选中文档</span>
            )}
            <span className="ml-auto truncate text-disabled">{currentTitle.slice(0, 24)}</span>
          </div>

          <MessageList scale={scale} />
          <ChatInput scale={scale} />
        </div>

        {/* 导航侧栏 */}
        {showNav && userQuestions.length > 0 && (
          navCollapsed ? (
            <div className="flex w-6 shrink-0 flex-col items-center border-l border-border-light py-2">
              <button onClick={() => setNavCollapsed(false)} className="rounded p-0.5 text-muted hover:bg-surface-hover" title="展开导航"><ChevronLeft size={12} /></button>
            </div>
          ) : (
            <div style={{ width: navWidth }} className="shrink-0 overflow-y-auto border-l border-border-light px-2 py-2">
              <div className="mb-1 flex items-center justify-between">
                <span className="px-1 text-[10px] uppercase text-disabled">导航</span>
                <button onClick={() => setNavCollapsed(true)} className="rounded p-0.5 text-muted hover:bg-surface-hover" title="折叠导航"><ChevronRight size={12} /></button>
              </div>
              {userQuestions.map(q => (
                <button key={q.id} onClick={() => document.getElementById(`msg-${q.id}`)?.scrollIntoView({ behavior: 'smooth' })}
                  className="w-full truncate rounded px-2 py-1 text-left text-[11px] text-dimmed transition-colors hover:bg-surface">#{q.index} {q.text}</button>
              ))}
            </div>
          )
        )}
      </div>

      {/* 右键菜单 */}
      {contextMenu && !renamingId && deletingId !== contextMenu.id && (
        <ContextMenu x={contextMenu.x} y={contextMenu.y} onClose={() => setContextMenu(null)} items={[
          { label: '重命名', icon: <Pencil size={12} />, onClick: () => { setRenamingId(contextMenu.id); setRenameValue(contextMenu.title); setContextMenu(null) } },
          { label: '导出 Markdown', icon: <Download size={12} />, onClick: async () => {
            setContextMenu(null)
            try {
              const { downloadFromUrl } = await import('../../lib/download')
              const result = await downloadFromUrl(`/export/conversation/${contextMenu.id}?format=md`, `${contextMenu.title || '对话'}.md`)
              if (result.ok) addToast('对话已导出，请查看下载文件夹')
              else addToast(`导出失败: ${result.error}`, 'error')
            } catch (e) {
              addToast('导出失败: ' + (e instanceof Error ? e.message : String(e)), 'error')
            }
          }},
          { label: '删除', icon: <Trash2 size={12} />, danger: true, onClick: () => { setDeletingId(contextMenu.id); setContextMenu(null) } },
        ]} />
      )}

      {/* 重命名弹窗 */}
      {renamingId && (
        <div className="fixed inset-0 z-[10001] flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setRenamingId(null)}>
          <div className="mx-4 w-full max-w-[320px] rounded-2xl border border-border-light bg-elevated p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-4 text-[17px] font-semibold">重命名对话</h3>
            <input autoFocus value={renameValue} onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && renameValue.trim()) doRename(renamingId, renameValue.trim())
                else if (e.key === 'Escape') setRenamingId(null)
              }}
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
    </>
  )
}
