/**
 * MessageList — 消息列表容器。
 *
 * 滚动管理（stick-to-bottom：自动跟随最新消息；用户上滚后暂停跟随）+ 空态欢迎。
 * 渲染 MessageItem（打字机 / 工具气泡 / 操作栏全部在单条内）。
 */
import { useEffect, useRef } from 'react'
import { Bot } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { MessageItem } from './MessageItem'
import { SCALES, type ChatScale } from './scale'

interface MessageListProps {
  scale: ChatScale
}

export function MessageList({ scale }: MessageListProps) {
  const s = SCALES[scale]
  const messages = useChatStore(st => st.messages)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  // 新消息/流式增量时自动滚底（用户未上滚）
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [messages])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  return (
    <div ref={scrollRef} onScroll={onScroll} className={`flex-1 overflow-y-auto ${s.pad} ${s.gap}`}>
      {messages.length === 0 && (
        <div className="flex gap-2">
          <div className={`${s.avatar} flex shrink-0 items-center justify-center rounded-full bg-surface-hover`}><Bot size={s.icon} className="text-dimmed" /></div>
          <div className={`max-w-[95%] px-3.5 py-2 ${s.msg} rounded-2xl rounded-bl-md bg-surface`}>你好！我是你的知识库助手。选中文档后可以向我提问，也可以 @ 引用其他文档。</div>
        </div>
      )}
      {messages.map(msg => (
        <MessageItem key={msg.id} msg={msg} scale={scale} />
      ))}
    </div>
  )
}
