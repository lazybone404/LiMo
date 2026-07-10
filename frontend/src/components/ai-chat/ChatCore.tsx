import { useRef, useEffect, useState, useMemo, type ReactNode } from 'react'
import { useStore, nextMsgId } from '../../store'
import { streamChat, listConversations, getConversation, deleteConversation, renameConversation } from '../../api/client'
import { REMARK_PLUGINS, REHYPE_PLUGINS } from '../../lib/markdown-plugins'
import ReactMarkdown from 'react-markdown'
import { Search, Plus, Trash2, Pencil, Download, Send, Bot, User, MessageSquare, ChevronRight, ChevronLeft } from 'lucide-react'

interface ChatCoreProps {
  compact: boolean
  listWidth: number
  navWidth: number
  showNav: boolean
  listCollapsed: boolean
  onListCollapsedChange: (v: boolean) => void
  scale?: 'sm' | 'md' | 'lg'
  titleLeft?: ReactNode
  titleRight?: ReactNode
  onConversationsChange?: (list: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }[]) => void
  onCurrentConvChange?: (id: string | null) => void
  /** Floating 模式的标题栏拖拽，AIPanel 不传 */
  onTitleMouseDown?: (e: React.MouseEvent) => void
}

const SCALES = {
  sm: { msg: 'text-[13px]', input: 'text-[12px]', btn: 11, icon: 12, avatar: 'w-6 h-6', gap: 'space-y-2', pad: 'px-3 py-2', listText: 'text-[11px]', listItem: 'px-2 py-1.5' },
  md: { msg: 'text-[14px]', input: 'text-[13px]', btn: 13, icon: 14, avatar: 'w-7 h-7', gap: 'space-y-2.5', pad: 'px-3 py-2.5', listText: 'text-[12px]', listItem: 'px-2 py-2' },
  lg: { msg: 'text-[15px]', input: 'text-[14px]', btn: 14, icon: 15, avatar: 'w-8 h-8', gap: 'space-y-3', pad: 'px-4 py-3', listText: 'text-[13px]', listItem: 'px-2.5 py-2' },
}

export function ChatCore({
  compact, listWidth, navWidth, showNav,
  listCollapsed, onListCollapsedChange,
  scale = 'md',
  titleLeft, titleRight,
  onConversationsChange, onCurrentConvChange,
  onTitleMouseDown,
}: ChatCoreProps) {
  const chatMessages = useStore(s => s.chatMessages)
  const chatLoading = useStore(s => s.chatLoading)
  const addChatMessage = useStore(s => s.addChatMessage)
  const setChatLoading = useStore(s => s.setChatLoading)
  const setChatMessages = useStore(s => s.setChatMessages)
  const currentDocId = useStore(s => s.currentDocId)
  const docs = useStore(s => s.docs)
  const addToast = useStore(s => s.addToast)

  const [input, setInput] = useState('')
  const [search, setSearch] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [convList, setConvList] = useState<{ id: string; title: string }[]>([])
  const [currentConvId, setCurrentConvId] = useState<string | null>(null)
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; id: string; title: string } | null>(null)
  const [toolStatus, setToolStatus] = useState('')


  const s = SCALES[scale]

  // 加载对话列表 + 恢复上次选中
  useEffect(() => {
    listConversations().then(list => {
      setConvList(list)
      onConversationsChange?.(list)
      const lastId = (() => { try { return localStorage.getItem('mbw_lastConvId') } catch { return null } })()
      const target = (lastId && list.some(c => c.id === lastId)) ? lastId : list[0]?.id || null
      if (target) {
        setCurrentConvId(target)
        onCurrentConvChange?.(target)
        getConversation(target).then(c => {
          if (c) setChatMessages(c.messages.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content, id: m.id })))
        }).catch(err => { console.error('加载对话消息失败:', err); addToast('加载对话消息失败', 'error') })
      }
    }).catch(err => { console.error('加载对话列表失败:', err); addToast('加载对话列表失败', 'error') })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const chatScrollRef = useRef<HTMLDivElement>(null)

  // 滚动 — 用 scrollTop 替代 scrollIntoView，避免带动整个页面滚动
  useEffect(() => {
    const el = chatScrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chatMessages])

  // 切换对话
  const switchConv = (id: string) => {
    if (id === currentConvId || chatLoading) return
    setCurrentConvId(id)
    onCurrentConvChange?.(id)
    getConversation(id).then(c => {
      if (c) setChatMessages(c.messages.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content, id: m.id })))
    }).catch(err => { console.error('切换对话失败:', err); addToast('切换对话失败', 'error') })
  }

  // 新建对话
  const newConv = () => {
    setCurrentConvId(null)
    onCurrentConvChange?.(null)
    setChatMessages([])
  }

  // 发送消息
  const handleSend = () => {
    const text = input.trim()
    if (!text || chatLoading) return
    const userMsgId = nextMsgId(); const aiMsgId = nextMsgId()
    addChatMessage({ role: 'user', content: text, id: userMsgId })
    addChatMessage({ role: 'assistant', content: '', id: aiMsgId })
    setInput(''); setChatLoading(true)
    const docIds = currentDocId ? [currentDocId] : []
    const capturedConvId = currentConvId

    streamChat(text, docIds, {
      onMeta(convId) {
        if (!capturedConvId) {
          setCurrentConvId(convId)
          onCurrentConvChange?.(convId)
          const newConv = { id: convId, title: text.slice(0, 60) + (text.length > 60 ? '...' : '') }
          setConvList(prev => [newConv, ...prev])
          onConversationsChange?.([newConv, ...convList])
        }
      },
      onText(chunk) {
        const msgs = useStore.getState().chatMessages
        const updated = [...msgs]
        const last = updated[updated.length - 1]
        if (last?.id === aiMsgId) {
          updated[updated.length - 1] = { ...last, content: last.content + chunk }
          useStore.setState({ chatMessages: updated })
        }
      },
      onToolStart(tool) {
        const label = tool === 'search_web' ? '搜索网络' : tool === 'fetch_url' ? '抓取网页' : '查询文档'
        setToolStatus(`正在${label}...`)
      },
      onDone() {
        setToolStatus(''); setChatLoading(false)
        listConversations().then(l => { setConvList(l); onConversationsChange?.(l) }).catch(err => { console.error('刷新对话列表失败:', err); addToast('刷新对话列表失败', 'error') })
      },
      onError(msg) {
        const msgs = useStore.getState().chatMessages
        const updated = [...msgs]
        const last = updated[updated.length - 1]
        if (last?.id === aiMsgId) {
          updated[updated.length - 1] = { ...last, content: last.content || msg }
          useStore.setState({ chatMessages: updated })
        }
        setChatLoading(false)
      },
    }, undefined, currentConvId || undefined)
  }

  // 过滤
  const filtered = useMemo(
    () => search ? convList.filter(c => c.title.toLowerCase().includes(search.toLowerCase())) : convList,
    [convList, search]
  )

  // 导航：用户提问列表
  const userQuestions = chatMessages
    .filter(m => m.role === 'user')
    .map((m, i) => ({ id: m.id, text: m.content.slice(0, 30) + (m.content.length > 30 ? '...' : ''), index: i + 1 }))

  // 右键菜单关闭
  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [contextMenu])

  // 当前对话标题（新对话为 null 时用占位）
  const currentTitle = (currentConvId ? convList.find(c => c.id === currentConvId)?.title : null) || '新对话'

  return (
    <>
      {/* 标题栏 */}
      <div className={`shrink-0 border-b border-border-light ${onTitleMouseDown ? 'cursor-move select-none' : ''}`}
        onMouseDown={onTitleMouseDown}>
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2">
            {titleLeft}
            {!compact && (
              <button onClick={() => onListCollapsedChange(!listCollapsed)} className="p-0.5 rounded hover:bg-surface-hover text-muted">
                {listCollapsed ? <ChevronRight size={scale === 'sm' ? 12 : 14} /> : <ChevronLeft size={scale === 'sm' ? 12 : 14} />}
              </button>
            )}
            {scale === 'sm' ? null : <><span className="w-2 h-2 rounded-full bg-success" /><span className="text-[13px] font-semibold text-dimmed">AI 对话</span></>}
          </div>
          <div className="flex items-center gap-0.5">
            <button onClick={newConv} className="p-1 rounded-md hover:bg-surface-hover text-muted" title="新建对话"><Plus size={14} /></button>
            {compact && <button onClick={() => onListCollapsedChange(!listCollapsed)} className="p-1 rounded-md hover:bg-surface-hover text-muted" title="对话列表"><MessageSquare size={14} /></button>}
            {titleRight}
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden" style={{ minHeight: 0 }}>
        {/* 对话列表 */}
        {listCollapsed ? (
          <div className="shrink-0 border-r border-border-light flex flex-col items-center py-2 px-1 gap-2" style={{ width: 32 }}>
            <button onClick={() => onListCollapsedChange(false)} className="p-1 rounded hover:bg-surface-hover text-muted" title="展开列表"><ChevronRight size={14} /></button>
            <button onClick={newConv} className="p-1 rounded hover:bg-surface-hover text-muted" title="新建"><Plus size={14} /></button>
          </div>
        ) : (
          <div style={{ width: listWidth, minWidth: compact ? 0 : 80 }} className="shrink-0 border-r border-border-light flex flex-col">
            <div className={`px-${compact ? 1.5 : 2} py-${compact ? 1.5 : 2}`}>
              <div className="flex items-center gap-1.5 px-2 py-1 bg-surface rounded-lg">
                <Search size={12} className="text-disabled" />
                <input value={search} onChange={e => setSearch(e.target.value)} placeholder={compact ? '' : '搜索对话...'} className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-disabled" />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto px-1">
              {filtered.map(c => (
                <button key={c.id}
                  onClick={() => switchConv(c.id)}
                  onContextMenu={e => { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY, id: c.id, title: c.title }) }}
                  className={`w-full text-left ${s.listItem} rounded-lg ${s.listText} transition-colors truncate ${c.id === currentConvId ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface'}`}>
                  {compact ? <MessageSquare size={s.icon} className="mx-auto" /> : c.title}
                </button>
              ))}
              {filtered.length === 0 && <div className="text-center text-[11px] text-disabled py-4">暂无对话</div>}
            </div>
          </div>
        )}

        {/* 消息区 */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-light text-[11px] shrink-0">
            {currentDocId ? (
              <span className="px-2 py-0.5 bg-accent/8 text-accent rounded-full">{docs.find(d => d.id === currentDocId)?.title?.slice(0, 15) || '文档'}</span>
            ) : (
              <span className="text-disabled">未选中文档</span>
            )}
            <span className="text-disabled ml-auto truncate">{currentTitle.slice(0, 24)}</span>
          </div>

          <div ref={chatScrollRef} className={`flex-1 overflow-y-auto ${s.pad} ${s.gap}`}>
            {chatMessages.length === 0 && (
              <div className="flex gap-2"><div className={`${s.avatar} rounded-full bg-surface-hover flex items-center justify-center shrink-0`}><Bot size={s.icon} className="text-dimmed" /></div><div className={`max-w-[95%] px-3.5 py-2 bg-surface ${s.msg} rounded-2xl rounded-bl-md`}>你好！我是你的知识库助手。选中文档后可以向我提问。</div></div>
            )}
            {chatMessages.map(msg => (
              <div key={msg.id} id={`msg-${msg.id}`} className={`flex gap-2 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                <div className={`${s.avatar} rounded-full bg-surface-hover flex items-center justify-center text-[11px] shrink-0`}>{msg.role === 'user' ? <User size={s.icon} className="text-dimmed" /> : <Bot size={s.icon} className="text-dimmed" />}</div>
                <div className={`max-w-[85%] px-3.5 py-2 ${s.msg} rounded-2xl ${msg.role === 'user' ? 'bg-accent text-white rounded-br-md' : 'bg-surface text-foreground rounded-bl-md'}`}>
                  {msg.role === 'assistant' ? (
                    <div className={`markdown-chat ${s.msg}`}>
                      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
                        {msg.content || (chatLoading && msg.id === chatMessages[chatMessages.length - 1]?.id ? '...' : '')}
                      </ReactMarkdown>
                    </div>
                  ) : <div className="whitespace-pre-wrap">{msg.content}</div>}
                </div>
              </div>
            ))}
            {chatLoading && !chatMessages.some(m => m.role === 'assistant' && m.content === '') && (
              <div className="flex gap-2"><div className={`${s.avatar} rounded-full bg-surface-hover flex items-center justify-center shrink-0`}><Bot size={s.icon} className="text-dimmed" /></div><div className="px-4 py-2.5 bg-surface rounded-2xl rounded-bl-md">{toolStatus ? <span className={`${s.msg} text-dimmed`}>{toolStatus}</span> : <span className="inline-flex gap-1"><span className="w-1.5 h-1.5 rounded-full bg-disabled animate-bounce" style={{ animationDelay: '0ms' }} /><span className="w-1.5 h-1.5 rounded-full bg-disabled animate-bounce" style={{ animationDelay: '150ms' }} /><span className="w-1.5 h-1.5 rounded-full bg-disabled animate-bounce" style={{ animationDelay: '300ms' }} /></span>}</div></div>
            )}
          </div>

          <div className="p-3 border-t border-border-light">
            <div className="flex items-center gap-2">
              <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }} placeholder="输入问题... (Enter 发送)" disabled={chatLoading} className={`flex-1 px-3 py-2 bg-surface border border-border-default rounded-full ${s.input} outline-none disabled:opacity-50`} />
              <button onClick={handleSend} disabled={!input.trim() || chatLoading} className="w-9 h-9 rounded-full bg-accent flex items-center justify-center hover:bg-accent-hover disabled:opacity-40 transition-all shrink-0"><Send size={s.btn} className="text-white" /></button>
            </div>
          </div>
        </div>

        {/* 导航侧栏 */}
        {showNav && userQuestions.length > 0 && (
          navCollapsed ? (
            <div className="shrink-0 border-l border-border-light flex flex-col items-center py-2" style={{ width: 24 }}>
              <button onClick={() => setNavCollapsed(false)} className="p-0.5 rounded hover:bg-surface-hover text-muted" title="展开导航"><ChevronLeft size={12} /></button>
            </div>
          ) : (
            <div style={{ width: navWidth }} className="shrink-0 border-l border-border-light overflow-y-auto px-2 py-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase text-disabled px-1">导航</span>
                <button onClick={() => setNavCollapsed(true)} className="p-0.5 rounded hover:bg-surface-hover text-muted" title="折叠导航"><ChevronRight size={12} /></button>
              </div>
              {userQuestions.map(q => (
                <button key={q.id} onClick={() => document.getElementById(`msg-${q.id}`)?.scrollIntoView({ behavior: 'smooth' })}
                  className="w-full text-left px-2 py-1 rounded text-[11px] text-dimmed hover:bg-surface truncate transition-colors">#{q.index} {q.text}</button>
              ))}
            </div>
          )
        )}
      </div>

      {/* 右键菜单 */}
      {contextMenu && (
        <div className="fixed z-[10001] bg-elevated border border-border-light rounded-xl shadow-xl py-1 min-w-[160px]" style={{ left: contextMenu.x, top: contextMenu.y }}>
          {renamingId === contextMenu.id ? (
            <div className="px-3 py-1.5 flex items-center gap-2">
              <input autoFocus value={renameValue} onChange={e => setRenameValue(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && renameValue.trim()) {
                    renameConversation(contextMenu.id, renameValue.trim())
                    setConvList(prev => prev.map(c => c.id === contextMenu.id ? { ...c, title: renameValue.trim() } : c))
                    onConversationsChange?.(convList.map(c => c.id === contextMenu.id ? { ...c, title: renameValue.trim() } : c))
                    setRenamingId(null); setContextMenu(null)
                  } else if (e.key === 'Escape') { setRenamingId(null); setContextMenu(null) }
                }}
                onBlur={() => { setRenamingId(null); setContextMenu(null) }}
                className="flex-1 px-2 py-0.5 bg-surface border border-border-default rounded text-[12px] outline-none" />
            </div>
          ) : (
            <button onClick={() => { setRenamingId(contextMenu.id); setRenameValue(contextMenu.title); setContextMenu(null) }}
              className="w-full text-left px-3 py-1.5 text-[12px] hover:bg-surface flex items-center gap-2"><Pencil size={12} />重命名</button>
          )}
          <button onClick={async () => {
            setContextMenu(null)
            try {
              const { downloadFromUrl } = await import('../../lib/download')
              const result = await downloadFromUrl(`/export/conversation/${contextMenu.id}?format=md`, `${contextMenu.title || '对话'}.md`)
              if (result.ok) addToast('对话已导出，请查看下载文件夹')
              else addToast(`导出失败: ${result.error}`, 'error')
            } catch (e) {
              addToast('导出失败: ' + (e instanceof Error ? e.message : String(e)), 'error')
            }
          }}
            className="w-full text-left px-3 py-1.5 text-[12px] hover:bg-surface flex items-center gap-2"><Download size={12} />导出 Markdown</button>
          {deletingId === contextMenu.id ? (
            <div className="px-3 py-1.5 flex items-center gap-2">
              <span className="text-[11px] text-error">确认删除？</span>
              <button onClick={() => {
                deleteConversation(contextMenu.id)
                setConvList(prev => prev.filter(c => c.id !== contextMenu.id))
                onConversationsChange?.(convList.filter(c => c.id !== contextMenu.id))
                if (currentConvId === contextMenu.id) { setCurrentConvId(null); setChatMessages([]); onCurrentConvChange?.(null) }
                setDeletingId(null); setContextMenu(null)
              }} className="px-2 py-0.5 bg-error text-white text-[11px] rounded">删除</button>
              <button onClick={() => { setDeletingId(null); setContextMenu(null) }}
                className="px-2 py-0.5 bg-surface border border-border-default text-[11px] rounded">取消</button>
            </div>
          ) : (
            <button onClick={() => { setDeletingId(contextMenu.id); setContextMenu(null) }}
              className="w-full text-left px-3 py-1.5 text-[12px] hover:bg-error/10 text-red-500 flex items-center gap-2"><Trash2 size={12} />删除</button>
          )}
        </div>
      )}
    </>
  )
}
