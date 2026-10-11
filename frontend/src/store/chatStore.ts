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
import { loadChatPrefs } from '../lib/chat-prefs'
import { toChatSources, type ChatSource } from '../lib/chat-sources'
import { useStore } from './index'
import { nextMsgId } from './toastSlice'

// ── 消息模型 ──

export interface DocRef {
  docId: string
  title: string
  segment?: string
}

/**
 * 按 `docId` 保序去重 —— 引用 chips 用 `docId` 当 React key，重复项会让 chips 渲染错乱。
 *
 * ⚠️ **两条消费路径必须同源**：流式追加（`onRef`）与重开会话恢复（`openConversation`）。
 * 此前只有前者去重，而后端落盘的 `doc_refs` 里**可能有重复**（同一篇文档被多个工具轮次
 * 引用——search_docs 命中后又 lookup_doc 打开是常态），于是那批数据只在「下次打开会话」
 * 时才炸成重复 key 警告。恢复侧的去重同时兼任**历史数据自愈**（后端修复只影响新会话）。
 */
function dedupeRefs(refs: DocRef[]): DocRef[] {
  return refs.filter((r, i, arr) => arr.findIndex(x => x.docId === r.docId) === i)
}

/**
 * `toChatSources` + 空值归一：空数组 → `undefined`。
 *
 * ⚠️ 「没来源」只允许**一种**表示。留着一个 `sources: []` 会让渲染层多一个
 * `?.length` 分支、让 `toBackendMsg` 多写一个空数组进落盘数据，而两者语义完全相同。
 */
function sourcesOrUndef(list: unknown): ChatSource[] | undefined {
  const s = toChatSources(list)
  return s.length ? s : undefined
}

export interface ToolCall {
  id: string
  tool: string
  display: string
  summary?: string
  /** 公网来源（`search_web` / `fetch_url` 才非空）—— 点了开浏览器，与 `docRefs` 不同 */
  sources?: ChatSource[]
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
  /** assistant 的追问建议（开关开启时后端补调的产物；**不在 `content` 里**） */
  followUps?: string[]
  /** 追问建议生成失败的原因（非空 = 显式失败，不是「这轮没有」） */
  followUpError?: string
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
  /** ⚠️ 返回 Promise：内部要先 `await` 后端 rebuild 落定，再发流式请求（见实现注释） */
  regenerate: (msgId: string) => Promise<void>
  deleteMessage: (msgId: string) => void
  editMessage: (msgId: string, content: string) => Promise<void>
  refreshConversations: () => Promise<void>
  /** 清除上下文：不删消息，停止携带该索引前历史（后端 clear_context_at） */
  clearContext: () => Promise<void>
  /** 关闭压缩提示条 */
  dismissCompressedHint: () => void
}

/** ChatMsg → 后端 message dict（rebuild 用，保留扩展字段；timestamp 由后端覆盖）
 *
 * ⚠️ **这是白名单**：不在这里的字段在 rebuild 那一刻被丢掉。而 rebuild 有**四处**
 * 调用点（`regenerate` / `deleteMessage` / `editMessage` 的两支）—— 漏一个字段就是
 * 「编辑任意一条消息后，追问卡整批消失」，且四个动作共用本函数，症状完全一样。
 * 后端侧与之成对的是 `core/types.py::ChatMessage.to_dict()`（管落盘）。
 */
const toBackendMsg = (m: ChatMsg) => ({
  id: m.id,
  role: m.role,
  content: m.content,
  ...(m.docRefs && m.docRefs.length ? { doc_refs: m.docRefs } : {}),
  ...(m.toolCalls && m.toolCalls.length ? { tool_calls: m.toolCalls.map(tc => ({ tool: tc.tool, display: tc.display, summary: tc.summary, sources: tc.sources })) } : {}),
  ...(m.thinking ? { thinking: m.thinking } : {}),
  ...(m.followUps && m.followUps.length ? { follow_ups: m.followUps } : {}),
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

  /** 按 **id** 更新一条消息（找不到则什么都不做 —— 被删掉的轮次不该被复活）。
   *
   * ⚠️ **追问必须用它、不能用 `updateLastBot`**：追问事件在 `done` **之后**才到
   * （后端先推 done 让输入框即刻可用，再补调一次 LLM），中间用户完全可能已经发了
   * 下一条消息 —— 此时「最后一条」已经是新问题了，按末尾定位会把上一轮的追问挂到
   * 新一轮的**用户消息**上（`updateLastBot` 只认 assistant，于是干脆丢掉卡片）。
   * 这是本功能唯一新引入的竞态，实机要专门走一遍（见 CHANGELOG）。
   */
  const patchBot = (id: string, fn: (msg: ChatMsg) => ChatMsg) => {
    const msgs = get().messages
    const i = msgs.findIndex(m => m.id === id)
    if (i < 0) return
    const next = [...msgs]
    next[i] = fn(next[i])
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

    // P2.2：缺 LLM key 前置提示（首次发送提示一次，localStorage 记忆关闭）
    const missKeys = useStore.getState().capabilities?.missingKeys
    if (missKeys && missKeys.llm.length > 0 && !localStorage.getItem('mbw_llm_hint_dismissed')) {
      try { localStorage.setItem('mbw_llm_hint_dismissed', '1') } catch {}
      useStore.getState().addToast('尚未配置 LLM API Key，对话功能可能不可用。可在引导向导或设置页中配置', 'info')
    }
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
    // ⚠️ **在发送这一刻读**（不是订阅）：偏好变了只影响之后的请求，进行中的那一轮
    // 用的仍是发起时的取值 —— 下面 `onFollowUp` 的守卫也读同一个变量。
    // 两项一起读，一次 `loadChatPrefs()`（两次调用是同一个结果，只是白读一次）。
    const prefs = loadChatPrefs()
    const wantFollowUp = prefs.followUp

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
      onToolResult(tool, summary, sources) {
        // ⚠️ 按「同名 + 还在跑」定位（与后端 `routers/chat.py` 回填时同一套判据）：
        // 一轮里同名工具被调用两次时也能各归各位。`sources` 只在这里才拿到
        // （`onToolStart` 时工具还没跑完）。
        const src = sourcesOrUndef(sources)
        updateLastBot(msg => ({
          ...msg,
          toolCalls: (msg.toolCalls || []).map(tc =>
            tc.tool === tool && tc.status === 'running'
              ? { ...tc, summary, sources: src, status: 'done' as const }
              : tc,
          ),
        }))
      },
      onRef(docId, title) {
        updateLastBot(msg => ({
          ...msg,
          docRefs: dedupeRefs([...(msg.docRefs || []), { docId, title }]),
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
      // 追问建议 —— 晚于 `onDone`（见 `api/chat.ts::StreamChatCallbacks.onFollowUp`）
      onFollowUp(questions, error) {
        // ⚠️ 判的是「**本次请求**有没有要」，与上面那个 `wantFollowUp` 是同一个变量 ——
        // 不是重新推导后端的准入条件（那会是第二份判据）。开关关着时后端根本不推这个
        // 事件，真收到了说明有别的东西在推，忽略它比写进消息好。
        if (!wantFollowUp) return
        // ⚠️ **按 `botMsg.id` 定位，不能按末尾**（原因见 `patchBot` 的注释）：
        // 事件到达时用户可能已经发了新消息。
        patchBot(botMsg.id, m => ({
          ...m,
          followUps: questions.length ? questions : undefined,
          followUpError: error || undefined,
        }))
      },
    }, { signal: controller.signal, conversationId: convId, docRefs, followUp: wantFollowUp, webSearch: prefs.webSearch })
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
          // ⚠️ 恢复侧也要去重：落盘数据里可能有重复 docId（见 `dedupeRefs` 注释）——
          // 这是历史数据唯一的自愈点。
          docRefs: m.doc_refs?.length ? dedupeRefs(m.doc_refs) : undefined,
          // ⚠️ 恢复侧是**唯一**把落盘的 `sources` 捡回来的地方（与 `toBackendMsg` 成对：
          // 一个管「重建时丢」、一个管「恢复时捡」）。⚠️ 落盘数据来自**旧版本**时没有
          // 这个字段 —— `toChatSources(undefined)` 返回 `[]`，不是崩溃。
          toolCalls: m.tool_calls?.length
            ? m.tool_calls.map(tc => ({
                id: nextMsgId(), tool: tc.tool, display: tc.display, summary: tc.summary,
                sources: sourcesOrUndef(tc.sources),
                status: 'done' as const,
              }))
            : undefined,
          thinking: m.thinking || undefined,
          // 追问建议（落盘字段 `follow_ups` → 前端 `followUps`）。⚠️ 与 `toBackendMsg`
          // **成对**：一个负责「重建时丢」，一个负责「恢复时捡回来」，缺任一都是静默丢卡。
          followUps: m.follow_ups?.length ? m.follow_ups : undefined,
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

    regenerate: async (msgId) => {
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
      //
      // ⚠️ `await` 是**协议要求**，不是风格问题（P1-14）：后端 PUT 是整表覆盖，
      //    而 `/chat/stream` 在返回 StreamingResponse **之前**就 append 了用户提问。
      //    不 await 时两条请求谁先到由网络决定 —— PUT 后到就把刚发出的提问整条抹掉，
      //    症状是「问完立刻消失」。原来的 `.catch()` 只处理失败，**不保证顺序**，
      //    所以它不能替代这里的 await（判据看的是两个请求的先后，不是有没有报错）。
      if (s.currentId) {
        try {
          await rebuildConversationMessages(s.currentId, msgs.slice(0, ui).map(toBackendMsg))
        } catch {
          // 同步失败不阻塞重发：本地已更新，只是可能未持久化
          useStore.getState().addToast('同步失败，操作可能未保存', 'error')
        }
      }
      dispatchSend(userMsg.content, userMsg.docRefs, ui)
    },

    deleteMessage: (msgId) => {
      const s = get()
      // ⚠️ 流式中删历史消息 → 下面这次 rebuild 会把「含半成品回复的快照」整表回写，
      //    而正在流的那条回复随后还会继续 append → 后端快照与流式内容互相打架
      //    （P1-14）。`regenerate` / `editMessage` 早就有这条守卫，三个写动作应当一致。
      //    ⚠️ 本动作**不需要** await：它后面没有第二个写请求，不存在到达顺序问题。
      if (s.streaming) return
      const next = s.messages.filter(m => m.id !== msgId)
      set({ messages: next })
      if (s.currentId) {
        rebuildConversationMessages(s.currentId, next.map(toBackendMsg)).catch(() => {
          // 后端同步失败不再静默吞错——本地已更新但可能未持久化
          useStore.getState().addToast('同步失败，操作可能未保存', 'error')
        })
      }
    },

    editMessage: async (msgId, content) => {
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
        // ⚠️ 与 `regenerate` 同因（P1-14）：rebuild 之后还有一次写请求，必须等它落定。
        if (s.currentId) {
          try {
            await rebuildConversationMessages(s.currentId, msgs.slice(0, idx).map(toBackendMsg))
          } catch {
            useStore.getState().addToast('同步失败，操作可能未保存', 'error')
          }
        }
        dispatchSend(trimmed, msg.docRefs, idx)
      } else {
        // assistant：本地覆盖文本（不重发），同步 rebuild
        // ⚠️ 这一支**不需要** await：改完就结束了，没有后续写请求与它抢顺序。
        const next = [...msgs]
        next[idx] = { ...msg, content: trimmed, isError: false }
        set({ messages: next })
        if (s.currentId) {
          rebuildConversationMessages(s.currentId, next.map(toBackendMsg)).catch(() => {
            // 后端同步失败不再静默吞错——本地已更新但可能未持久化
            useStore.getState().addToast('同步失败，操作可能未保存', 'error')
          })
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
      // ⚠️ **刻意不吞异常**（此前是 `catch { /* 组件层 toast */ }`）：吞掉之后
      // 「对话已不在（后端 404）」与「已清除」在调用方**完全同形**，而组件层是靠
      // resolve/reject 决定弹成功还是失败的 —— 于是 `ChatCore` 无条件弹「已清除上下文」
      // （假成功，模式 #53）。store 只负责状态：失败时连 `clearContextIndex` 都不动，
      // 免得本地以为清了、后端没有。
      await clearConversationContext(s.currentId, index)
      set({ clearContextIndex: index })
    },

    dismissCompressedHint: () => set({ compressedHint: null }),
  }
})
