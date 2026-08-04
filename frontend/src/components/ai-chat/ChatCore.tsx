/**
 * ChatCore — 对话模块容器（插槽面板 / 浮窗共用，纯组件）。
 *
 * 状态全部来自独立 chatStore（消息/会话/流式）+ 主 store 的 currentDocId/docs。
 * 结构：标题栏 + [ChatSidebar 会话列表 | 文档badge+MessageList+ChatInput | 用户问题导航侧栏]。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useStore } from '../../store'
import { useChatStore } from '../../store/chatStore'
import { MessageSquare, ChevronRight, ChevronLeft, Plus } from 'lucide-react'
import { ChatSidebar } from './ChatSidebar'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import type { ChatScale } from './scale'

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

export function ChatCore({
  compact, listWidth, navWidth, showNav,
  listCollapsed, onListCollapsedChange,
  scale = 'md',
  titleLeft, titleRight,
  onTitleMouseDown,
}: ChatCoreProps) {
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

  const [navCollapsed, setNavCollapsed] = useState(false)

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

  // 用户问题导航（只列最近 10 个，保证落在 MessageList 分页窗口内）
  const userQuestions = useMemo(
    () => messages
      .filter(m => m.role === 'user')
      .slice(-10)
      .map((m, i) => ({ id: m.id, text: m.content.slice(0, 30) + (m.content.length > 30 ? '...' : ''), index: i + 1 })),
    [messages],
  )

  const currentTitle = (currentId ? (conversations.find(c => c.id === currentId)?.title ?? topic) : null) || '新对话'

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
            <ChatSidebar conversations={conversations} currentId={currentId} compact={compact} scale={scale} onSwitch={switchConv} onNew={newConv} />
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
    </>
  )
}
