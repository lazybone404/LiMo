/**
 * ChatCore — 对话模块容器（纯组件，状态来自 chatStore）。
 *
 * 结构：标题栏 + [ChatSidebar 会话列表 | 消息区 | 用户问题导航侧栏]
 * 内部栏可拖拽调宽（ResizeHandle）+ 直接折叠（拖 <80 自动折叠 / 窄条展开），
 * 宽度/折叠状态由容器（FloatingAI）受控——与主界面插槽面板交互一致。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useStore } from '../../store'
import { useChatStore } from '../../store/chatStore'
import { MessageSquare, ChevronRight, ChevronLeft, Plus, Eraser, X } from 'lucide-react'
import { ChatSidebar } from './ChatSidebar'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { ResizeHandle } from '../ui/ResizeHandle'
import type { ChatScale } from './scale'

interface ChatCoreProps {
  scale?: ChatScale
  titleLeft?: ReactNode
  titleRight?: ReactNode
  /** 浮窗模式的标题栏拖拽 */
  onTitleMouseDown?: (e: React.MouseEvent) => void
  // ── 会话列表（受控）──
  listCollapsed: boolean
  listWidth: number
  onToggleList: () => void
  onResizeList: (dx: number) => void
  onExpandList: () => void
  // ── 导航侧栏（受控）──
  showNav: boolean
  navCollapsed: boolean
  navWidth: number
  onToggleNav: () => void
  onResizeNav: (dx: number) => void
  onExpandNav: () => void
}

export function ChatCore({
  scale = 'md',
  titleLeft, titleRight, onTitleMouseDown,
  listCollapsed, listWidth, onToggleList, onResizeList, onExpandList,
  showNav, navCollapsed, navWidth, onToggleNav, onResizeNav, onExpandNav,
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
  const clearContext = useChatStore(st => st.clearContext)
  const compressedHint = useChatStore(st => st.compressedHint)
  const dismissCompressedHint = useChatStore(st => st.dismissCompressedHint)

  // ── 主 store 状态（跨域）──
  const currentDocId = useStore(st => st.currentDocId)
  const docs = useStore(st => st.docs)
  const addToast = useStore(st => st.addToast)

  const compact = listWidth < 80

  // 隐私提示：首次使用对话提示内容发送至模型服务（localStorage 可关）
  const [privacyDismissed, setPrivacyDismissed] = useState(() => {
    try { return !!localStorage.getItem('limo_chat_privacy_seen') } catch { return false }
  })
  const dismissPrivacy = () => {
    setPrivacyDismissed(true)
    try { localStorage.setItem('limo_chat_privacy_seen', '1') } catch { /* quota */ }
  }

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
              <button onClick={onToggleList} className="rounded p-0.5 text-muted hover:bg-surface-hover">
                {listCollapsed ? <ChevronRight size={scale === 'sm' ? 12 : 14} /> : <ChevronLeft size={scale === 'sm' ? 12 : 14} />}
              </button>
            )}
            {scale === 'sm' ? null : <><span className="h-2 w-2 rounded-full bg-success" /><span className="text-[13px] font-semibold text-dimmed">AI 对话</span></>}
          </div>
          <div className="flex items-center gap-0.5">
            <button onClick={newConv} className="rounded-md p-1 text-muted hover:bg-surface-hover" title="新建对话"><Plus size={14} /></button>
            {currentId && messages.length > 0 && (
              <button onClick={() => { clearContext(); addToast('已清除上下文，之后的对话不再携带之前历史', 'info') }}
                className="rounded-md p-1 text-muted hover:bg-surface-hover" title="清除上下文（不删消息，停止携带之前历史）"><Eraser size={14} /></button>
            )}
            {compact && <button onClick={onToggleList} className="rounded-md p-1 text-muted hover:bg-surface-hover" title="对话列表"><MessageSquare size={14} /></button>}
            {titleRight}
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden" style={{ minHeight: 0 }}>
        {/* 会话列表（可拖拽调宽 / 拖窄自动折叠） */}
        {listCollapsed ? (
          <div className="flex w-8 shrink-0 flex-col items-center gap-2 border-r border-border-light px-1 py-2">
            <button onClick={onExpandList} className="rounded p-1 text-muted hover:bg-surface-hover" title="展开列表"><ChevronRight size={14} /></button>
            <button onClick={newConv} className="rounded p-1 text-muted hover:bg-surface-hover" title="新建"><Plus size={14} /></button>
          </div>
        ) : (
          <>
            <div style={{ width: listWidth, minWidth: compact ? 0 : 80 }} className="flex shrink-0 flex-col border-r border-border-light">
              <ChatSidebar conversations={conversations} currentId={currentId} compact={compact} scale={scale} onSwitch={switchConv} onNew={newConv} />
            </div>
            <ResizeHandle onResize={onResizeList} />
          </>
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

          {/* 压缩提示（C3：更早消息已摘要） */}
          {compressedHint && (
            <div className="flex shrink-0 items-center justify-between border-b border-border-light bg-accent/5 px-3 py-1 text-[11px] text-dimmed">
              <span>{compressedHint}</span>
              <button onClick={dismissCompressedHint} className="text-disabled hover:text-dimmed" title="关闭"><X size={11} /></button>
            </div>
          )}

          {/* 隐私提示（C4：内容发送至模型服务，可关闭） */}
          {!privacyDismissed && (
            <div className="flex shrink-0 items-center justify-between border-b border-border-light bg-surface-hover/60 px-3 py-1 text-[11px] text-dimmed">
              <span>对话内容将发送至模型服务，请勿输入敏感信息</span>
              <button onClick={dismissPrivacy} className="shrink-0 text-disabled hover:text-dimmed">知道了</button>
            </div>
          )}

          <MessageList scale={scale} />
          <ChatInput scale={scale} />
        </div>

        {/* 导航侧栏（可拖拽调宽 / 拖窄自动折叠） */}
        {showNav && userQuestions.length > 0 && (
          navCollapsed ? (
            <div className="flex w-6 shrink-0 flex-col items-center border-l border-border-light py-2">
              <button onClick={onExpandNav} className="rounded p-0.5 text-muted hover:bg-surface-hover" title="展开导航"><ChevronLeft size={12} /></button>
            </div>
          ) : (
            <>
              <ResizeHandle onResize={onResizeNav} />
              <div style={{ width: navWidth }} className="shrink-0 overflow-y-auto border-l border-border-light px-2 py-2">
                <div className="mb-1 flex items-center justify-between">
                  <span className="px-1 text-[10px] uppercase text-disabled">导航</span>
                  <button onClick={onToggleNav} className="rounded p-0.5 text-muted hover:bg-surface-hover" title="折叠导航"><ChevronRight size={12} /></button>
                </div>
                {userQuestions.map(q => (
                  <button key={q.id} onClick={() => document.getElementById(`msg-${q.id}`)?.scrollIntoView({ behavior: 'smooth' })}
                    className="w-full truncate rounded px-2 py-1 text-left text-[11px] text-dimmed transition-colors hover:bg-surface">#{q.index} {q.text}</button>
                ))}
              </div>
            </>
          )
        )}
      </div>
    </>
  )
}
