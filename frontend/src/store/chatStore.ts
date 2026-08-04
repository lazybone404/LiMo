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
import { streamChat, listConversations, getConversation, rebuildConversationMessages, clearConversationContext } from '../api/chat'
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

  // ── 上下文管理（C2-C4）──
  /** 模型上下文窗口（meta 事件，后端算好） */
  contextWindow: number | null
  /** 输入预算 = 窗口 × ratio（meta 事件，后端算好） */
  budget: number | null
  /** 本轮 token 用量（done 事件；provider 拿不到则 null） */
  lastUsage: { prompt: number; completion: number } | null
  /** 会话累计用量 */
  totalUsage: { prompt: number; completion: number }
  /** 历史压缩提示（compressed 事件） */
  compressedHint: string | null
  /** clearContext 边界（该索引前消息不再携带上下文） */
  clearContextIndex: number | null
  /** 模型价格（meta 事件；未配置则 null，费用显示隐藏） */
  priceIn: number | null
  priceOut: number | null

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
  /** 清除上下文：不删消息，停止携带该索引前历史（后端 clear_context_at） */
  clearContext: () => Promise<void>
  /** 关闭压缩提示条 */
  dismissCompressedHint: () => void
}

/** ChatMsg → 后端 message dict（rebuild 用，保留扩展字段；timestamp 由后端覆盖） */
const toBackendMsg = (m: ChatMsg) => ({
  id: m.id,
  role: m.role,
  content: m.content,
  ...(m.docRefs && m.docRefs.length ? { doc_refs: m.docRefs } : {}),
  ...(m.toolCalls && m.toolCalls.length ? { tool_calls: m.toolCalls.map(tc => ({ tool: tc.tool, display: tc.display, summary: tc.summary })) } : {}),
  ...(m.thinking ? { thinking: m.thinking } : {}),
})

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
      onMeta(convId, meta) {
        const cur = get()
        if (meta?.budget || meta?.context_window) {
          set({ contextWindow: meta.context_window ?? null, budget: meta.budget ?? null })
        }
        if (meta?.price_in != null || meta?.price_out != null) {
          set({ priceIn: meta.price_in ?? null, priceOut: meta.price_out ?? null })
        }
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
      onCompressed(count) {
        set({ compressedHint: `更早的 ${count} 条消息已摘要（节省上下文）` })
      },
      onDone(usage) {
        // 每轮 token 用量（provider 拿不到则不带，费用显示随之隐藏）
        const p = usage?.prompt_tokens ?? 0
        const c = usage?.completion_tokens ?? 0
        if (p || c) set(s => ({
          lastUsage: { prompt: p, completion: c },
          totalUsage: { prompt: s.totalUsage.prompt + p, completion: s.totalUsage.completion + c },
        }))
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
    }, controller.signal, convId, docRefs)
  }

  return {
    conversations: [],
    currentId: null,
    topic: '新对话',
    messages: [],
    streaming: false,
    generating: false,
    statusText: '',
    contextWindow: null,
    budget: null,
    lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 },
    compressedHint: null,
    clearContextIndex: null,
    priceIn: null,
    priceOut: null,

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
          docRefs: m.doc_refs?.length ? m.doc_refs : undefined,
          toolCalls: m.tool_calls?.length
            ? m.tool_calls.map(tc => ({ id: nextMsgId(), tool: tc.tool, display: tc.display, summary: tc.summary, status: 'done' as const }))
            : undefined,
          thinking: m.thinking || undefined,
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
      // 后端先截断到 ui（rebuild），再本地重发（流式 append 新消息）
      if (s.currentId) {
        rebuildConversationMessages(s.currentId, msgs.slice(0, ui).map(toBackendMsg)).catch(() => { /* 组件层忽略 */ })
      }
      dispatchSend(userMsg.content, userMsg.docRefs, ui)
    },

    deleteMessage: (msgId) => {
      const s = get()
      const next = s.messages.filter(m => m.id !== msgId)
      set({ messages: next })
      if (s.currentId) {
        rebuildConversationMessages(s.currentId, next.map(toBackendMsg)).catch(() => { /* 组件层忽略 */ })
      }
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
        // 分支语义：后端先截断到 idx（rebuild），再本地以新内容重发（流式 append）
        if (s.currentId) {
          rebuildConversationMessages(s.currentId, msgs.slice(0, idx).map(toBackendMsg)).catch(() => { /* 组件层忽略 */ })
        }
        dispatchSend(trimmed, msg.docRefs, idx)
      } else {
        // assistant：本地覆盖文本（不重发），同步 rebuild
        const next = [...msgs]
        next[idx] = { ...msg, content: trimmed, isError: false }
        set({ messages: next })
        if (s.currentId) {
          rebuildConversationMessages(s.currentId, next.map(toBackendMsg)).catch(() => { /* 组件层忽略 */ })
        }
      }
    },

    refreshConversations: async () => {
      try {
        set({ conversations: await listConversations() })
      } catch { /* 忽略，下次自动刷新 */ }
    },

    clearContext: async () => {
      const s = get()
      if (!s.currentId || s.messages.length === 0) return
      const index = s.messages.length  // 边界：该索引前消息不再携带（不清除消息）
      try {
        await clearConversationContext(s.currentId, index)
        set({ clearContextIndex: index })
      } catch { /* 组件层 toast */ }
    },

    dismissCompressedHint: () => set({ compressedHint: null }),
  }
})
