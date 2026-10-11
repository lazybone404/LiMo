/**
 * MessageList — 消息列表容器。
 *
 * 滚动管理（stick-to-bottom：自动跟随最新消息；用户上滚后暂停跟随）+ 空态欢迎。
 * 长会话分页窗口：>50 条只渲染最近 50，顶部「更早消息」按钮展开全部（防长会话整页重渲染）。
 * 渲染 MessageItem（打字机 / 工具气泡 / 操作栏全部在单条内）。
 *
 * 两条交互落在这里（都只作用于消息区，不碰左侧会话列表与底部输入区）：
 * - **Ctrl+滚轮改对话正文字号** —— 与预览区同款手法，但**各存各的倍率**
 *   （`lib/chat-font.ts`，`--chat-font-scale`；预览区是 `--read-font-size`）。
 * - **「回到最新」浮层按钮** —— 用户上滚后自动滚底已暂停（`stickToBottom`），
 *   需要一个显式出口；按钮的显隐与 `stickToBottom` **同源**（同一个 `< 40px` 判据）。
 */
import { useEffect, useRef, useState } from 'react'
import { ArrowDown, Bot } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { useStore } from '../../store'
import { MessageItem } from './MessageItem'
import { SCALES, type ChatScale } from './scale'
import { applyChatFont, getSavedChatFont, nextFontScale, saveChatFont } from '../../lib/chat-font'

const WINDOW = 50

/** 「认为在底部」的容差（px）—— `stickToBottom` 与按钮显隐共用这一个判据 */
const BOTTOM_EPSILON = 40

interface MessageListProps {
  scale: ChatScale
}

export function MessageList({ scale }: MessageListProps) {
  const s = SCALES[scale]
  const messages = useChatStore(st => st.messages)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const [expanded, setExpanded] = useState(false)
  const [atBottom, setAtBottom] = useState(true)

  // ⚠️ 用 ref 存当前倍率，**不用 state**：滚轮监听挂在依赖 `[]` 的 effect 上
  // （只挂一次、卸一次），闭包里读 state 会永远读到挂载那一刻的值 ——
  // 症状是「滚一下放大一档之后就再也不动了」，且零报错。同理不放进 deps：
  // 那样每滚一档都要摘挂一次监听。
  const chatFontScale = useRef(getSavedChatFont().fontScale)

  // 长会话分页窗口：只渲染最近 WINDOW 条（导航侧栏只列最近 10 个问题，窗口内可达）
  const total = messages.length
  const sliced = total > WINDOW && !expanded ? messages.slice(-WINDOW) : messages
  const skipped = total - sliced.length

  // 新消息/流式增量时自动滚底（用户未上滚）
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [sliced])

  // Ctrl+滚轮 → 对话正文字号。
  // ⚠️ **必须用原生 `addEventListener(..., { passive: false })`，不能用 React 的 onWheel**：
  // React 把 `wheel` 按 **passive** 注册在 root 上，在处理器里调 `preventDefault()`
  // 只会得到一条控制台警告，**拦不住** WebView2 把整个应用（连文档库、工具栏一起）
  // 缩放 —— 那正是要阻止的行为。与 `PreviewArea` 同款（那边还有同样的注释）。
  // 挂载点是**消息滚动容器**而非 window：鼠标在文档库/输入框上按 Ctrl+滚轮时
  // 不该改对话字号。
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      const cur = chatFontScale.current
      const next = nextFontScale(cur, e.deltaY < 0 ? 1 : -1)
      // 到顶 / 到底静默（对齐 `store.stepFontScale`）——否则一直滚会刷一屏 toast
      if (next === cur) return
      chatFontScale.current = next
      const p = { fontScale: next }
      applyChatFont(p)
      saveChatFont(p)
      useStore.getState().addToast(`对话字号 ${Math.round(next * 100)}%`)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_EPSILON
    stickToBottom.current = near
    // 同值 bailout：onScroll 触发极频繁，值没变就别惊动 React
    setAtBottom(prev => (prev === near ? prev : near))
  }

  const scrollToBottom = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottom.current = true
    setAtBottom(true)
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  return (
    // 外层只负责定位（按钮锚在它身上）；滚动仍在内层 —— 按钮不随内容滚走。
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} onScroll={onScroll} className={`min-h-0 flex-1 overflow-y-auto ${s.pad} ${s.gap}`}>
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
      {!atBottom && (
        <button
          type="button"
          onClick={scrollToBottom}
          title="回到最新"
          aria-label="回到最新"
          className="absolute bottom-3 right-3 z-10 flex h-7 w-7 items-center justify-center rounded-full border border-border-light bg-surface text-dimmed shadow-md transition-colors hover:text-foreground"
        >
          <ArrowDown size={14} />
        </button>
      )}
    </div>
  )
}
