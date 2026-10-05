/**
 * ChatInput — 对话输入区。
 *
 * textarea 多行自动增高 + IME 处理（修复中文输入法 Enter 确认拼音误发送）
 * + Enter 发送 / Shift+Enter 换行 + 流式中显示停止按钮。
 *
 * @ 文档引用：AtSign 按钮弹出文档选择浮层，选中文档 → docRef chips（本条消息引用）。
 * 发送时 docRefs 随 sendMessage 传入，docIds = 当前选中文档 + 本条引用（chatStore 组合去重）。
 */
import { useRef, useState } from 'react'
import { Send, Square, AtSign, FileText, X } from 'lucide-react'
import { useChatStore, type DocRef } from '../../store/chatStore'
import { useStore, type DocEntry } from '../../store'
import { SCALES, type ChatScale } from './scale'
import { estimateTokenLength, formatTokens } from '../../utils/token'

interface ChatInputProps {
  scale: ChatScale
}

export function ChatInput({ scale }: ChatInputProps) {
  const s = SCALES[scale]
  const streaming = useChatStore(st => st.streaming)
  const sendMessage = useChatStore(st => st.sendMessage)
  const stopGenerating = useChatStore(st => st.stopGenerating)
  const docs = useStore(st => st.docs)

  const [value, setValue] = useState('')
  const [composing, setComposing] = useState(false)
  const [docRefs, setDocRefs] = useState<DocRef[]>([])
  const [refOpen, setRefOpen] = useState(false)
  const [refSearch, setRefSearch] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 上下文占用进度（C4）— 前端字符估算仅供参考，实际裁剪以后端 tiktoken 为准
  const messages = useChatStore(st => st.messages)
  const budget = useChatStore(st => st.budget)
  const contextTokens = messages.reduce((acc, m) => acc + estimateTokenLength(m.content), 0)
  const inputTokens = estimateTokenLength(value)
  const ctxRatio = budget && budget > 0 ? Math.min(1, contextTokens / budget) : 0

  // 每轮 token 用量 + 费用估算（C4；未配价格则不显示费用）
  const lastUsage = useChatStore(st => st.lastUsage)
  const priceIn = useChatStore(st => st.priceIn)
  const priceOut = useChatStore(st => st.priceOut)
  const cost = lastUsage && (priceIn != null || priceOut != null)
    ? ((lastUsage.prompt * (priceIn ?? 0)) + (lastUsage.completion * (priceOut ?? 0))) / 1e6
    : null

  const autoGrow = () => {
    const el = taRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' }
  }

  const submit = () => {
    const text = value.trim()
    if (!text || streaming) return
    sendMessage(text, docRefs.length ? docRefs : undefined)
    setValue('')
    setDocRefs([])
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const filteredDocs = refSearch ? docs.filter(d => d.title.toLowerCase().includes(refSearch.toLowerCase())) : docs

  const addRef = (doc: DocEntry) => {
    if (docRefs.some(r => r.docId === doc.id)) return
    setDocRefs([...docRefs, { docId: doc.id, title: doc.title }])
    setRefOpen(false)
    setRefSearch('')
    taRef.current?.focus()
  }

  return (
    <div className="relative shrink-0 border-t border-border-light p-3">
      {/* 点击浮层外关闭 */}
      {refOpen && <div className="fixed inset-0 z-[5]" onClick={() => setRefOpen(false)} />}

      {/* 本条消息引用的文档 chips */}
      {docRefs.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {docRefs.map(r => (
            <span key={r.docId} className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
              <FileText size={10} /><span className="max-w-[140px] truncate">{r.title}</span>
              <button onClick={() => setDocRefs(docRefs.filter(x => x.docId !== r.docId))} className="text-accent/60 hover:text-accent" title="移除引用"><X size={10} /></button>
            </span>
          ))}
        </div>
      )}

      {/* @ 文档选择浮层 */}
      {refOpen && (
        <div className="absolute bottom-full left-3 right-3 z-10 mb-2 overflow-hidden rounded-xl border border-border-light bg-elevated shadow-xl">
          <div className="px-2 py-1.5">
            <input autoFocus value={refSearch} onChange={e => setRefSearch(e.target.value)} placeholder="搜索文档…"
              onKeyDown={e => { if (e.key === 'Escape') setRefOpen(false) }}
              className="w-full bg-transparent text-[12px] outline-none placeholder:text-disabled" />
          </div>
          <div className="max-h-48 overflow-y-auto border-t border-border-light">
            {filteredDocs.slice(0, 8).map(doc => (
              <button key={doc.id} onClick={() => addRef(doc)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-dimmed transition-colors hover:bg-surface">
                <FileText size={11} className="shrink-0 text-muted" />
                <span className="truncate">{doc.title}</span>
              </button>
            ))}
            {filteredDocs.length === 0 && <div className="px-3 py-3 text-center text-[11px] text-disabled">暂无文档</div>}
          </div>
        </div>
      )}

      <div className="flex items-end gap-2">
        <button onClick={() => { setRefOpen(v => !v); setRefSearch('') }} title="引用文档"
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors ${docRefs.length ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-surface-hover'}`}>
          <AtSign size={s.btn} />
        </button>
        <textarea
          ref={taRef}
          value={value}
          rows={1}
          onChange={e => { setValue(e.target.value); autoGrow() }}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey && !composing && !e.nativeEvent.isComposing) {
              e.preventDefault(); submit()
            }
          }}
          placeholder="输入问题… (Enter 发送，Shift+Enter 换行)"
          disabled={streaming}
          className={`flex-1 resize-none rounded-xl border border-border-default bg-surface px-3 py-2 font-mono ${s.input} outline-none placeholder:text-disabled disabled:opacity-50`}
          style={{ maxHeight: 160 }}
        />
        {streaming ? (
          <button onClick={stopGenerating} title="停止生成"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-error/10 text-error transition-all hover:bg-error/15">
            <Square size={s.btn} />
          </button>
        ) : (
          <button onClick={submit} disabled={!value.trim()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent transition-all hover:bg-accent-hover disabled:opacity-40">
            <Send size={s.btn} />
          </button>
        )}
      </div>

      {/* 上下文占用进度 + 输入 token（C4：估算仅供参考，实际裁剪以后端 tiktoken 为准） */}
      {(budget != null || value.trim() || messages.length > 0) && (
        <div className="mt-1 flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-surface-hover">
            <div className="h-full rounded-full bg-accent/50 transition-all" style={{ width: `${ctxRatio * 100}%` }} />
          </div>
          <span className="shrink-0 text-[10px] text-disabled">
            {formatTokens(contextTokens)}{budget ? `/${formatTokens(budget)}` : ''}
            {inputTokens > 0 && ` · 本条 ${formatTokens(inputTokens)}`}
            {lastUsage && ` · 本轮 ${formatTokens(lastUsage.prompt)}→${formatTokens(lastUsage.completion)}`}
            {cost != null && ` ≈¥${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}`}
          </span>
        </div>
      )}
    </div>
  )
}
