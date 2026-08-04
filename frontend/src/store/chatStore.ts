/**
 * chatStore — 独立对话状态机（消息 / 会话 / 流式）。
 *
 * 不并入主 useStore 的理由：
 *  - 流式 delta 高频更新需隔离（主 store 订阅者不受波及）
 *  - 消息模型（ChatMsg）比主 store 的简单 ChatMessage 更丰富
 *  - 对齐 NextChat 的独立 chat store 模式
 *
 * 跨 store：组件层用细粒度 selector 读主 store 的 currentDocId/docs/addToast；
 * 本 store action 内需要时用 useStore.getState() 读取。
 *
 * 持久化策略（与后端保持一致）：
 *  - 发送消息时后端自动 append_user_message，流式完成时 append_assistant_message
 *  - 中断（stopGenerating → abort）后后端通过 cancel_event 也会保存部分回复
 *  - 删除/编辑/分支截断本地更新，P5 走 PUT /conversations/{id}/messages 全量 rebuild 同步后端
 */
import { create } from 'zustand'
import { streamChat, listConversations, getConversation } from '../api/chat'
import { useStore } from './index'
import { nextMsgId } from './toastSlice'

// ── 消息模型 ──

export interface DocRef {
  docId: string
  title: string
  segment?: string
}

export interface ToolCall {
  id: string
  tool: string
  display: string
  summary?: string
  status: 'running' | 'done' | 'error'
}

export interface ChatMsg {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  /** 展示时间 */
  date: string
  /** 流式打字机状态 */
  streaming?: boolean
  isError?: boolean
  /** assistant 工具调用气泡 */
  toolCalls?: ToolCall[]
  /** user @ 的文档 / assistant 引用的文档 */
  docRefs?: DocRef[]
  /** 思考段（P5 后端 thinking 事件） */
  thinking?: string
}

/** 会话列表条目（轻量，来自 GET /api/conversations） */
export interface ChatListItem {
  id: string
  title: string
  model_id?: number | null
  created_at?: string
  updated_at?: string
  archived?: boolean
}

export interface ChatStoreState {
  // ── 会话列表 ──
  conversations: ChatListItem[]
  // ── 当前会话 ──
  currentId: string | null
  topic: string
  messages: ChatMsg[]
  streaming: boolean
  generating: boolean
  /** agent_status / 工具状态文本 */
  statusText: string

  // ── actions ──
  loadConversations: () => Promise<void>
  openConversation: (id: string | null) => Promise<void>
  newChat: () => void
  sendMessage: (text: string, docRefs?: DocRef[]) => void
  stopGenerating: () => void
  regenerate: (msgId: string) => void
  deleteMessage: (msgId: string) => void
  editMessage: (msgId: string, content: string) => void
  refreshConversations: () => Promise<void>
}

/** 当前活跃流式请求的 AbortController（同一时刻只允许一条流式） */
let activeController: AbortController | null = null

export const useChatStore = create<ChatStoreState>()((set, get) => {
  /** 更新当前会话最后一条 assistant 消息（产生新数组引用触发订阅） */
  const updateLastBot = (fn: (msg: ChatMsg) => ChatMsg) => {
    const s = get()
    const msgs = s.messages
    const last = msgs[msgs.length - 1]
    if (!last || last.role !== 'assistant') return
    const next = [...msgs]
    next[next.length - 1] = fn(last)
    set({ messages: next })
  }

  /** 结束流式（done / error / abort 后统一收敛） */
  const endStreaming = () => {
    activeController = null
    set({ streaming: false, generating: false, statusText: '' })
    get().refreshConversations()
  }

  /** 真正的发送动作——sendMessage / regenerate / editMessage 复用 */
  const dispatchSend = (text: string, docRefs: DocRef[] | undefined, trimToIndex: number | null) => {
    const s = get()
    if (s.streaming) return
    const now = new Date().toLocaleString()
    const userMsg: ChatMsg = { id: nextMsgId(), role: 'user', content: text, date: now, docRefs: docRefs?.length ? docRefs : undefined }
    const botMsg: ChatMsg = { id: nextMsgId(), role: 'assistant', content: '', date: now, streaming: true }
    const base = trimToIndex === null ? s.messages : s.messages.slice(0, trimToIndex)
    set({ messages: [...base, userMsg, botMsg], streaming: true, generating: true, statusText: '' })

    // docIds = 当前选中文档 + 本条引用（去重）——消息级引用，后端 _build_doc_context 已按 doc_ids 注入
    const currentDocId = useStore.getState().currentDocId
    const docIds = Array.from(new Set([
      ...(currentDocId ? [currentDocId] : []),
      ...(docRefs || []).map(r => r.docId),
    ]))

    const controller = new AbortController()
    activeController = controller
    const convId = s.currentId || undefined

    streamChat(text, docIds, {
      onMeta(convId) {
        const cur = get()
        if (!cur.currentId) {
          const item: ChatListItem = {
            id: convId,
            title: text.slice(0, 60) + (text.length > 60 ? '...' : ''),
            created_at: now,
            updated_at: now,
          }
          set({ currentId: convId, topic: item.title, conversations: [item, ...cur.conversations] })
        }
      },
      onStatus(message) { set({ statusText: message }) },
      onToolStart(tool, display) {
        updateLastBot(msg => ({
          ...msg,
          toolCalls: [...(msg.toolCalls || []), { id: nextMsgId(), tool, display, status: 'running' as const }],
        }))
      },
      onToolResult(tool, summary) {
        updateLastBot(msg => ({
          ...msg,
          toolCalls: (msg.toolCalls || []).map(tc =>
            tc.tool === tool && tc.status === 'running' ? { ...tc, summary, status: 'done' as const } : tc,
          ),
        }))
      },
      onRef(docId, title) {
        updateLastBot(msg => ({
          ...msg,
          docRefs: [...(msg.docRefs || []), { docId, title }].filter(
            (r, i, arr) => arr.findIndex(x => x.docId === r.docId) === i,
          ),
        }))
      },
      onThinking(text) {
        updateLastBot(msg => ({ ...msg, thinking: (msg.thinking || '') + text }))
      },
      onText(chunk) {
        updateLastBot(msg => ({ ...msg, content: msg.content + chunk }))
      },
      onDone() {
        // 标记最后一条结束流式，其余状态收敛
        const cur = get()
        const msgs = cur.messages
        const last = msgs[msgs.length - 1]
        if (last && last.role === 'assistant' && last.streaming) {
          const next = [...msgs]
          next[next.length - 1] = { ...last, streaming: false }
          set({ messages: next })
        }
        endStreaming()
      },
      onError(msg) {
        updateLastBot(bot => ({ ...bot, content: bot.content || msg, isError: true, streaming: false }))
        endStreaming()
      },
    }, controller.signal, convId)
  }

  return {
    conversations: [],
    currentId: null,
    topic: '新对话',
    messages: [],
    streaming: false,
    generating: false,
    statusText: '',

    loadConversations: async () => {
      try {
        set({ conversations: await listConversations() })
      } catch { /* 组件层 toast */ }
    },

    openConversation: async (id) => {
      const s = get()
      if (s.streaming) return
      if (!id) {
        set({ currentId: null, topic: '新对话', messages: [], streaming: false, generating: false, statusText: '' })
        return
      }
      try {
        const c = await getConversation(id)
        if (!c) return
        const messages: ChatMsg[] = (c.messages || []).map(m => ({
          id: m.id,
          role: m.role === 'user' || m.role === 'assistant' ? m.role : 'system',
          content: m.content,
          date: m.timestamp || new Date().toLocaleString(),
        }))
        set({ currentId: id, topic: c.title, messages, streaming: false, generating: false, statusText: '' })
      } catch { /* 组件层 toast */ }
    },

    newChat: () => {
      if (get().streaming) return
      set({ currentId: null, topic: '新对话', messages: [], streaming: false, generating: false, statusText: '' })
    },

    sendMessage: (text, docRefs) => {
      const trimmed = text.trim()
      if (!trimmed) return
      dispatchSend(trimmed, docRefs, null)
    },

    stopGenerating: () => {
      // abort → fetch AbortError → streamChat 内部调 onDone（复位 streaming，保留部分内容）
      activeController?.abort()
    },

    regenerate: (msgId) => {
      const s = get()
      if (s.streaming) return
      const msgs = s.messages
      const idx = msgs.findIndex(m => m.id === msgId)
      if (idx < 0) return
      // user 起点：该消息本身（user）或向前最近 user（assistant 配对）
      let ui = idx
      while (ui >= 0 && msgs[ui].role !== 'user') ui--
      if (ui < 0) return
      const userMsg = msgs[ui]
      // 截断到 ui（含 ui 及之后的回复全部删除）
      dispatchSend(userMsg.content, userMsg.docRefs, ui)
    },

    deleteMessage: (msgId) => {
      const s = get()
      set({ messages: s.messages.filter(m => m.id !== msgId) })
      // TODO(P5): 后端 PUT /conversations/{id}/messages 全量 rebuild 同步
    },

    editMessage: (msgId, content) => {
      const s = get()
      if (s.streaming) return
      const msgs = s.messages
      const idx = msgs.findIndex(m => m.id === msgId)
      if (idx < 0) return
      const msg = msgs[idx]
      const trimmed = content.trim()
      if (!trimmed) return
      if (msg.role === 'user') {
        // 分支语义：截断该条及之后 → 立即以新内容重发
        dispatchSend(trimmed, msg.docRefs, idx)
      } else {
        // assistant：本地覆盖文本（不重发）
        const next = [...msgs]
        next[idx] = { ...msg, content: trimmed, isError: false }
        set({ messages: next })
        // TODO(P5): 后端 rebuild 同步
      }
    },

    refreshConversations: async () => {
      try {
        set({ conversations: await listConversations() })
      } catch { /* 忽略，下次自动刷新 */ }
    },
  }
})
