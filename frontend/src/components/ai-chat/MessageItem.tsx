/**
 * MessageItem — 单条对话消息。
 *
 * 气泡布局（用户右 / 助手左）+ 流式打字机 + 消息操作栏（复制/编辑/重新生成/删除，
 * 流式中仅停止）+ 工具气泡三态（含公网来源展开）+ 引用 chips + 思考段折叠。
 *
 * 打字机：streaming 时 ReactMarkdown key 保持 'loading'（增量重渲染），
 * 流式结束切 'done' 强制重挂载（清状态残留）——对齐 NextChat 模式。
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../../store'
import { useChatStore, type ChatMsg, type ToolCall } from '../../store/chatStore'
import { Copy, Check, Pencil, RefreshCw, Trash2, Square, Bot, User, Wrench, ChevronDown, ChevronRight, FileText } from 'lucide-react'
import { stripMarkers } from '../../lib/markdown-markers'
import { sourceHost } from '../../lib/chat-sources'
import { ChatMarkerContent, ChatFollowUpList } from './ChatMarkerContent'
import { SCALES, type ChatScale } from './scale'

interface MessageItemProps {
  msg: ChatMsg
  scale: ChatScale
}

/**
 * 工具气泡 —— 一行状态（图标 + display + 摘要），有公网来源时多一个展开箭头。
 *
 * ⚠️ **「搜了什么、来源在哪」是本轮新增的可见性**：此前 `tool_result` 的 `refs` 只覆盖
 * 库内文档（`search_docs` / `lookup_doc` / `list_docs`），`search_web` 的结果 URL
 * **一条通道都没有** —— 用户只看得到「正在搜索网络: xxx」，看不到搜到了什么。
 *
 * ⚠️ `sources` 的 URL 已在入库边界（`api/chat.ts` 的 `toChatSources`、`chatStore`
 * 的 `sourcesOrUndef`）过滤过；这里直接渲染，**不是「忘了判」** —— 判据住在
 * 「不可信数据进入应用」的那两个点上，重复判会让它看起来像有两个真相源。
 */
function ToolBubble({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false)
  const sources = tc.sources ?? []
  const hasSources = sources.length > 0

  return (
    <div className="rounded-lg bg-surface-hover/60 px-2 py-1">
      <div className="flex items-center gap-1.5">
        <Wrench size={10} className={`shrink-0 ${tc.status === 'running' ? 'animate-spin text-accent' : 'text-disabled'}`} />
        <span className="min-w-0 truncate text-[11px] text-dimmed">{tc.display}</span>
        {tc.status === 'running'
          ? <span className="shrink-0 text-[11px] text-accent">…</span>
          : tc.summary ? <span className="shrink-0 text-[11px] text-disabled">· {tc.summary}</span> : null}
        {hasSources && (
          <button onClick={() => setOpen(v => !v)} data-tool-sources-toggle={open ? 'open' : 'closed'}
            className="ml-auto flex shrink-0 items-center gap-0.5 rounded px-1 text-[10px] text-muted transition-colors hover:bg-surface-hover hover:text-foreground"
            title={open ? '收起来源' : `展开 ${sources.length} 条来源`}>
            {open ? <ChevronDown size={10} /> : <ChevronRight size={10} />}来源 {sources.length}
          </button>
        )}
      </div>
      {open && hasSources && (
        <ul className="mt-1 space-y-0.5 border-t border-border-light pt-1">
          {sources.map(src => (
            <li key={src.url} className="flex items-baseline gap-1.5">
              <a href={src.url} target="_blank" rel="noopener noreferrer" title={src.url}
                className="min-w-0 truncate text-[11px] text-accent hover:underline">
                {src.title || sourceHost(src.url)}
              </a>
              {/* 有标题时补一个域名，便于快速判断站点；没标题时标题位已经是域名了，不重复 */}
              {src.title && <span className="shrink-0 text-[10px] text-disabled">{sourceHost(src.url)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
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
  const refreshFollowUps = useChatStore(st => st.refreshFollowUps)
  const statusText = useChatStore(st => st.statusText)

  const copy = () => {
    // ⚠️ **复制的是剥掉标记行的正文**（`limo-block` 是渲染协议，不是给用户看的内容）。
    // 无标记时 `stripMarkers` 原样返回入参 —— 零回归。与产物侧 ExportMenu 同纪律。
    navigator.clipboard.writeText(stripMarkers(msg.content)).then(
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

          {/* 工具气泡三态 + 公网来源展开 */}
          {msg.toolCalls && msg.toolCalls.length > 0 && (
            <div className="mb-1 space-y-1">
              {msg.toolCalls.map(tc => <ToolBubble key={tc.id} tc={tc} />)}
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
            // ⚠️ 外层 `markdown-chat` 容器**保持不动**：无标记时 `ChatMarkerContent` 走快路径，
            // 产出的树与改造前逐字相同（零回归）。容器留在外面也让分段路径下的多段渲染
            // 共用同一套 `.markdown-chat` 样式作用域。
            <div className={`markdown-chat ${s.msg}`}>
              <ChatMarkerContent content={msg.content} streaming={!!msg.streaming} statusText={statusText} />
            </div>
          )}

          {/* 追问建议 —— **气泡内部末尾**（正文之后、操作栏之前），视觉上是回答的收尾
              而不是挂在气泡外的独立 UI。两个来源共用 `ChatFollowUpList`（另一个来源是
              模型自主写在正文里的 `follow-up` 块，由 `ChatMarkerContent` 渲染）。
              ⚠️ 两者由后端保证互斥（有可用块就不补调），前端**不再判一次**。 */}
          {msg.followUps?.length ? (
            <ChatFollowUpList
              items={msg.followUps}
              streaming={!!msg.streaming}
              // ⚠️ 「换一批」只在**这一支**出现：`msg.followUps` 就是后端补调来的那份，
              //    它知道自己的上游问题与回答（`refreshFollowUps` 会向前找最近一条 user）。
              //    另一个来源（模型自主写在正文里的 `follow-up` 块）住在 `content` 里，
              //    由 `ChatMarkerContent` 渲染，**没有可重跑的上游**。
              onRefresh={() => refreshFollowUps(msg.id)}
            />
          ) : null}
          {/* ⚠️ 失败要有落点：开了开关却什么都没有，必须看得出是失败而非「这轮没有」 */}
          {msg.followUpError ? <div className="mt-1 text-[11px] text-dimmed">{msg.followUpError}</div> : null}
        </div>

        {/* 操作栏 + 时间 */}
        <div className={`flex items-center gap-2 ${isUser ? 'justify-end' : ''}`}>
          {actions}
          {msg.date && <span className="text-[10px] text-disabled">{msg.date}</span>}
        </div>
      </div>

      {/* 编辑弹窗 —— **Portal 到 body**：祖先 `FloatingAI` 的根 `<aside>` 带
          `backdrop-blur-[20px]`，会创建包含块把 `fixed` 后代锁进浮窗内（同 DocLibrary） */}
      {editing && createPortal(
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
        </div>,
        document.body,
      )}
    </div>
  )
}
