/**
 * MessageItem — 单条对话消息。
 *
 * 气泡布局（用户右 / 助手左）+ 流式打字机 + 消息操作栏（复制/编辑/重新生成/删除，
 * 流式中仅停止）+ 工具气泡三态 + 引用 chips + 思考段折叠。
 *
 * 打字机：streaming 时 ReactMarkdown key 保持 'loading'（增量重渲染），
 * 流式结束切 'done' 强制重挂载（清状态残留）——对齐 NextChat 模式。
 */
import { useState } from 'react'
import { useStore } from '../../store'
import { useChatStore, type ChatMsg } from '../../store/chatStore'
import { Copy, Check, Pencil, RefreshCw, Trash2, Square, Bot, User, Wrench, ChevronDown, ChevronRight, FileText } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'
import { SCALES, type ChatScale } from './scale'

interface MessageItemProps {
  msg: ChatMsg
  scale: ChatScale
}

export function MessageItem({ msg, scale }: MessageItemProps) {
  const s = SCALES[scale]
  const isUser = msg.role === 'user'
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')
  const [showThinking, setShowThinking] = useState(false)

  const stopGenerating = useChatStore(st => st.stopGenerating)
  const regenerate = useChatStore(st => st.regenerate)
  const deleteMessage = useChatStore(st => st.deleteMessage)
  const editMessage = useChatStore(st => st.editMessage)
  const statusText = useChatStore(st => st.statusText)

  const copy = () => {
    navigator.clipboard.writeText(msg.content).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 1500) },
      () => useStore.getState().addToast('复制失败，请手动复制', 'error'),
    )
  }

  const saveEdit = () => {
    const t = editText.trim()
    if (t) editMessage(msg.id, t)
    setEditing(false)
  }

  const actions = (
    <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
      {msg.streaming ? (
        <button onClick={stopGenerating} className="p-1 rounded hover:bg-surface-hover text-error" title="停止生成"><Square size={s.icon} /></button>
      ) : (
        <>
          <button onClick={copy} className="p-1 rounded hover:bg-surface-hover text-muted" title="复制">{copied ? <Check size={s.icon} className="text-success" /> : <Copy size={s.icon} />}</button>
          <button onClick={() => { setEditing(true); setEditText(msg.content) }} className="p-1 rounded hover:bg-surface-hover text-muted" title={isUser ? '编辑并重新发送' : '编辑'}><Pencil size={s.icon} /></button>
          <button onClick={() => regenerate(msg.id)} className="p-1 rounded hover:bg-surface-hover text-muted" title="重新生成"><RefreshCw size={s.icon} /></button>
          <button onClick={() => deleteMessage(msg.id)} className="p-1 rounded hover:bg-surface-hover text-error" title="删除"><Trash2 size={s.icon} /></button>
        </>
      )}
    </div>
  )

  return (
    <div className={`group flex gap-2 ${isUser ? 'flex-row-reverse' : ''}`} id={`msg-${msg.id}`}>
      <div className={`${s.avatar} rounded-full bg-surface-hover flex items-center justify-center shrink-0`}>
        {isUser ? <User size={s.icon} className="text-dimmed" /> : <Bot size={s.icon} className="text-dimmed" />}
      </div>

      <div className="max-w-[85%] min-w-0">
        <div className={`mt-0.5 px-3.5 py-2 ${s.msg} rounded-2xl break-words ${isUser ? 'bg-accent text-on-accent rounded-br-md' : 'bg-surface text-foreground rounded-bl-md'}`}>
          {/* 思考段折叠（P5 后端 thinking 事件） */}
          {msg.thinking ? (
            <div className="mb-1">
              <button onClick={() => setShowThinking(v => !v)} className="flex items-center gap-0.5 text-[11px] text-dimmed hover:text-foreground transition-colors">
                {showThinking ? <ChevronDown size={10} /> : <ChevronRight size={10} />}思考过程
              </button>
              {showThinking && <div className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surface-hover/60 px-2 py-1 text-[12px] text-dimmed">{msg.thinking}</div>}
            </div>
          ) : null}

          {/* 工具气泡三态 */}
          {msg.toolCalls && msg.toolCalls.length > 0 && (
            <div className="mb-1 space-y-1">
              {msg.toolCalls.map(tc => (
                <div key={tc.id} className="flex items-center gap-1.5 rounded-lg bg-surface-hover/60 px-2 py-1">
                  <Wrench size={10} className={`shrink-0 ${tc.status === 'running' ? 'animate-spin text-accent' : 'text-disabled'}`} />
                  <span className="truncate text-[11px] text-dimmed">{tc.display}</span>
                  {tc.status === 'running'
                    ? <span className="shrink-0 text-[11px] text-accent">…</span>
                    : tc.summary ? <span className="shrink-0 text-[11px] text-disabled">· {tc.summary}</span> : null}
                </div>
              ))}
            </div>
          )}

          {/* 引用文档 chips */}
          {msg.docRefs && msg.docRefs.length > 0 && (
            <div className="mb-1 flex flex-wrap gap-1">
              {msg.docRefs.map(r => (
                <button key={r.docId} onClick={() => useStore.getState().selectDoc(r.docId)}
                  className="inline-flex max-w-[180px] items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent hover:bg-accent/15 transition-colors" title="跳转到该文档">
                  <FileText size={10} className="shrink-0" /><span className="truncate">{r.title}</span>
                </button>
              ))}
            </div>
          )}

          {/* 内容 */}
          {msg.isError ? (
            <div className="whitespace-pre-wrap text-[12px] text-error">{msg.content || '生成出错'}</div>
          ) : isUser ? (
            <div className="whitespace-pre-wrap">{msg.content}</div>
          ) : (
            <div className={`markdown-chat ${s.msg}`}>
              <ReactMarkdown key={msg.streaming ? 'loading' : 'done'} remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
                {normalizeLatexDelimiters(msg.content || (msg.streaming ? (statusText || '…') : ''))}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* 操作栏 + 时间 */}
        <div className={`flex items-center gap-2 ${isUser ? 'justify-end' : ''}`}>
          {actions}
          {msg.date && <span className="text-[10px] text-disabled">{msg.date}</span>}
        </div>
      </div>

      {/* 编辑弹窗 */}
      {editing && (
        <div className="fixed inset-0 z-[10001] flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setEditing(false)}>
          <div className="mx-4 w-full max-w-[480px] rounded-2xl border border-border-light bg-elevated p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="mb-3 text-[15px] font-semibold">{isUser ? '编辑并重新发送' : '编辑消息'}</h3>
            <textarea autoFocus value={editText} onChange={e => setEditText(e.target.value)} rows={4}
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) saveEdit(); if (e.key === 'Escape') setEditing(false) }}
              className="mb-4 w-full resize-none rounded-lg border border-border-default bg-surface px-3 py-2 text-[13px] outline-none" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setEditing(false)} className="rounded-lg px-4 py-2 text-[13px] text-dimmed hover:bg-surface">取消</button>
              <button onClick={saveEdit} className="rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-on-accent">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
