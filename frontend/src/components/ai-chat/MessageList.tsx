/**
 * MessageList — 消息列表容器。
 *
 * 滚动管理（stick-to-bottom：自动跟随最新消息；用户上滚后暂停跟随）+ 空态欢迎。
 * 长会话分页窗口：>50 条只渲染最近 50，顶部「更早消息」按钮展开全部（防长会话整页重渲染）。
 * 渲染 MessageItem（打字机 / 工具气泡 / 操作栏全部在单条内）。
 */
import { useEffect, useRef, useState } from 'react'
import { Bot } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { MessageItem } from './MessageItem'
import { SCALES, type ChatScale } from './scale'

const WINDOW = 50

interface MessageListProps {
  scale: ChatScale
}

export function MessageList({ scale }: MessageListProps) {
  const s = SCALES[scale]
  const messages = useChatStore(st => st.messages)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const [expanded, setExpanded] = useState(false)

  // 长会话分页窗口：只渲染最近 WINDOW 条（导航侧栏只列最近 10 个问题，窗口内可达）
  const total = messages.length
  const sliced = total > WINDOW && !expanded ? messages.slice(-WINDOW) : messages
  const skipped = total - sliced.length

  // 新消息/流式增量时自动滚底（用户未上滚）
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [sliced])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  return (
    <div ref={scrollRef} onScroll={onScroll} className={`flex-1 overflow-y-auto ${s.pad} ${s.gap}`}>
      {skipped > 0 && (
        <button onClick={() => setExpanded(true)}
          className="mx-auto block rounded-full px-3 py-1 text-[11px] text-dimmed transition-colors hover:bg-surface">
          更早的 {skipped} 条消息 · 点击显示全部
        </button>
      )}
      {total === 0 && (
        <div className="flex gap-2">
          <div className={`${s.avatar} flex shrink-0 items-center justify-center rounded-full bg-surface-hover`}><Bot size={s.icon} className="text-dimmed" /></div>
          <div className={`max-w-[95%] px-3.5 py-2 ${s.msg} rounded-2xl rounded-bl-md bg-surface`}>你好！我是你的知识库助手。选中文档后可以向我提问，也可以 @ 引用其他文档。</div>
        </div>
      )}
      {sliced.map(msg => (
        <MessageItem key={msg.id} msg={msg} scale={scale} />
      ))}
    </div>
  )
}
