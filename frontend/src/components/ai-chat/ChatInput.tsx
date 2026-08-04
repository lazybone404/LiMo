/**
 * ChatInput — 对话输入区。
 *
 * textarea 多行自动增高 + IME 处理（修复中文输入法 Enter 确认拼音误发送）
 * + Enter 发送 / Shift+Enter 换行 + 流式中显示停止按钮。
 *
 * P4 扩展：@ 文档引用选择浮层（docRefs 传入 sendMessage）。
 */
import { useRef, useState } from 'react'
import { Send, Square } from 'lucide-react'
import { useChatStore, type DocRef } from '../../store/chatStore'
import { SCALES, type ChatScale } from './scale'

interface ChatInputProps {
  scale: ChatScale
  /** 本条消息 @ 的文档引用（P4 输入区插入） */
  docRefs?: DocRef[]
  setDocRefs?: (refs: DocRef[]) => void
}

export function ChatInput({ scale, docRefs, setDocRefs }: ChatInputProps) {
  const s = SCALES[scale]
  const streaming = useChatStore(st => st.streaming)
  const sendMessage = useChatStore(st => st.sendMessage)
  const stopGenerating = useChatStore(st => st.stopGenerating)

  const [value, setValue] = useState('')
  const [composing, setComposing] = useState(false)
  const taRef = useRef<HTMLTextAreaElement>(null)

  const autoGrow = () => {
    const el = taRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' }
  }

  const submit = () => {
    const text = value.trim()
    if (!text || streaming) return
    sendMessage(text, docRefs?.length ? docRefs : undefined)
    setValue('')
    setDocRefs?.([])
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  return (
    <div className="shrink-0 border-t border-border-light p-3">
      <div className="flex items-end gap-2">
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
          className={`flex-1 resize-none rounded-xl border border-border-default bg-surface px-3 py-2 ${s.input} outline-none placeholder:text-disabled disabled:opacity-50`}
          style={{ maxHeight: 160 }}
        />
        {streaming ? (
          <button onClick={stopGenerating} title="停止生成"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-error/10 text-error transition-all hover:bg-error/15">
            <Square size={s.btn} />
          </button>
        ) : (
          <button onClick={submit} disabled={!value.trim()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition-all hover:bg-accent-hover disabled:opacity-40">
            <Send size={s.btn} />
          </button>
        )}
      </div>
    </div>
  )
}
