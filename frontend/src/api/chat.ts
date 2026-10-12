/**
 * chat 域 — 对话（流式/非流式）+ 对话持久化 CRUD。
 */
import { apiFetch, getErrorMessage, BASE, type ChatResult } from './core'
import { parseSSEStream } from './sse'
import { toChatSources, type ChatSource } from '../lib/chat-sources'

// -- chat --

export async function chat(
  question: string,
  docIds: string[],
  history: { role: string; content: string }[] = [],
  mode: string = 'single',
): Promise<ChatResult> {
  const r = await apiFetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, doc_ids: docIds, history, mode }),
  })
  return r.json()
}

export interface StreamChatCallbacks {
  onMeta: (conversationId: string, meta?: { context_window?: number; budget?: number; price_in?: number | null; price_out?: number | null }) => void
  onText: (text: string) => void
  onToolStart: (tool: string, display: string) => void
  onStatus?: (message: string) => void
  /**
   * `sources` = 公网来源（`search_web` 的结果列表 / `fetch_url` 抓的那一页）。
   *
   * ⚠️ 它是**展示用**的第三条通路，与 `onRef`（库内文档）**不是一回事**：
   * `onRef` 是 `refs: [{docId, title}]`（点了跳预览区），这是
   * `sources: [{title, url}]`（点了开浏览器）。别再合并成一个回调。
   */
  onToolResult?: (tool: string, summary: string, sources?: ChatSource[]) => void
  /** assistant 回答引用文档（P5 后端 ref 事件） */
  onRef?: (docId: string, title: string) => void
  /** 思考段流式（P5 后端 thinking 事件） */
  onThinking?: (text: string) => void
  /** 历史压缩通知（C3）：更早 N 条已摘要 */
  onCompressed?: (count: number) => void
  /** 每轮 token 用量（C4，done 事件携带；拿不到则不带） */
  onUsage?: (usage: { prompt_tokens?: number; completion_tokens?: number }) => void
  onDone: (usage?: { prompt_tokens?: number; completion_tokens?: number }) => void
  onError: (message: string) => void
  /**
   * 追问建议（`follow_up=1` 时后端在 `done` **之后**补推的事件）。
   *
   * ⚠️ **它晚于 `onDone`** —— 上面的 `receivedDoneOrError` 守卫在 `done` 到达时就已置真，
   * 故这次「迟到的推送」**不会**被误报成「连接意外断开」。若将来有人把那个守卫改成
   * 「以流关闭为准」，这里会当场变成「每次追问都额外弹一条连接错误」。
   *
   * `error` 非空 = 后端开了开关但没生成出来（**失败要显式**，不能静默当「这轮没有」）。
   */
  onFollowUp?: (questions: string[], error?: string | null) => void
}

/** `streamChat` 的尾部可选参数 —— 收成对象：位置参数已经到 6 个，再加就是「第几个是哪个」的记忆题 */
export interface StreamChatOptions {
  signal?: AbortSignal
  conversationId?: string
  docRefs?: { docId: string; title: string }[]
  /** 用户开着「追问建议」→ 带 `follow_up=1`（默认不带 = 零回归，见 `lib/chat-prefs.ts`） */
  followUp?: boolean
  /**
   * 用户关掉「联网」→ 带 `web_search=0`（默认**不带** = 联网，见 `lib/chat-prefs.ts`）。
   *
   * ⚠️ 与 `followUp` **方向相反**：那边是「开着才带参数」，这边是「关着才带参数」——
   * 因为两者的默认值相反（追问默认关、联网默认开），而纪律是同一条：
   * **参数只在偏离默认时才发出去**（少一个参数是「与改造前逐字相同」里可被断言的那种）。
   */
  webSearch?: boolean
}

export function streamChat(
  question: string,
  docIds: string[],
  callbacks: StreamChatCallbacks,
  options: StreamChatOptions = {},
): void {
  const { signal, conversationId, docRefs, followUp, webSearch } = options
  const params = new URLSearchParams({ question })
  if (docIds.length > 0) params.set('doc_ids', docIds.join(','))
  if (conversationId) params.set('conversation_id', conversationId)
  if (docRefs && docRefs.length > 0) params.set('doc_refs', JSON.stringify(docRefs))
  // ⚠️ 关着的时候**根本不设这个参数**（不是 `follow_up=0`）—— 与「改造前逐字相同」
  // 是同一件事的两种说法，而少一个参数是其中**可被断言**的那种。
  if (followUp) params.set('follow_up', '1')
  // ⚠️ 同理但方向相反：**开着**（＝默认）时根本不设参数，只有 `false` 才带 `=0`。
  // `undefined` 视为默认（开）—— 与后端 `Query(1)` 同源，别写成 `if (!webSearch)`。
  if (webSearch === false) params.set('web_search', '0')

  // 追踪 SSE 是否正常结束（收到 done/error）— 对齐 generate.ts。
  // 断连时 onDone/onError 都不会触发，若不追踪则聊天 UI 永久卡加载
  let receivedDoneOrError = false
  const origOnDone = callbacks.onDone
  const origOnError = callbacks.onError
  callbacks.onDone = () => { receivedDoneOrError = true; origOnDone() }
  callbacks.onError = (msg) => { receivedDoneOrError = true; origOnError(msg) }

  fetch(`${BASE}/chat/stream?${params}`, { signal })
    .then(async (response) => {
      if (!response.ok) {
        let body: any = {}
        try { body = await response.json() } catch { /* not JSON */ }
        callbacks.onError(body.detail || body.message || `请求失败 (${response.status})`)
        return
      }
      const reader = response.body?.getReader()
      if (!reader) { callbacks.onError('无法读取响应流'); return }

      await parseSSEStream(reader, (data) => {
        // ⚠️ **`done` 之后必须继续处理事件**：追问事件就排在 `done` 后面（后端先推 done
        // 让输入框即刻可用、再补推 followup）。这里现在没有 early return，天然满足——
        // 加 `followup` 分支时**别顺手**在 `done` 那句上写 `return`。
        if (data.type === 'meta') callbacks.onMeta(data.conversation_id, { context_window: data.context_window, budget: data.budget, price_in: data.price_in, price_out: data.price_out })
        else if (data.type === 'text') callbacks.onText(data.text)
        else if (data.type === 'agent_status') callbacks.onStatus?.(data.message)
        else if (data.type === 'tool_start') callbacks.onToolStart(data.tool ?? '', data.display)
        else if (data.type === 'tool_result') {
          // `toChatSources` 过滤 + 去重（纵深防御的第二道，主防线在后端
          // `llm/agent.py::_safe_http_url`，见 `lib/chat-sources.ts`）
          callbacks.onToolResult?.(data.tool, data.summary, toChatSources(data.sources))
          // ⚠️ 引用**随 `tool_result` 一起发**（后端 `agent.py::_extract_refs` → `refs: [{docId, title}]`），
          // 这里必须逐个转发给 `onRef`。此前只取了 `summary`、把 `refs` 整个丢掉，
          // 于是**实时流里 assistant 的引用 chips 从不出现**，只有重开会话才从落盘的
          // `doc_refs` 复原 —— 而下面那条 `ref` 事件后端**根本不发**（死分支）。
          for (const r of data.refs ?? []) callbacks.onRef?.(r.docId, r.title)
        }
        else if (data.type === 'thinking') callbacks.onThinking?.(data.text)
        else if (data.type === 'compressed') callbacks.onCompressed?.(data.count)
        else if (data.type === 'done') { callbacks.onUsage?.(data.usage); callbacks.onDone(data.usage) }
        else if (data.type === 'error') callbacks.onError(data.message)
        // 追问建议（晚于 `done`，见 `onFollowUp` 的注释）
        else if (data.type === 'followup') callbacks.onFollowUp?.(data.questions ?? [], data.error ?? null)
      })

      if (!receivedDoneOrError) {
        callbacks.onError('连接意外断开，请重试')
      }
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') {
        // 用户主动停止（AbortController.abort）：复位 UI。
        // 后端通过 cancel_event → done+cancelled 保存部分回复，前端保留已收到内容。
        if (!receivedDoneOrError) callbacks.onDone()
        return
      }
      callbacks.onError(getErrorMessage(err))
    })
}

// -- conversations --

export interface ConvEntry {
  id: string; title: string; model_id?: number | null
  created_at: string; updated_at: string
}

export interface ConvMessage {
  id: string; role: string; content: string; timestamp: string
  doc_refs?: { docId: string; title: string }[]
  /** ⚠️ 与后端 `routers/chat.py` 的落盘白名单、前端 `chatStore.ts::toBackendMsg` **三处同源** */
  tool_calls?: { tool: string; display: string; summary?: string; sources?: ChatSource[] }[]
  thinking?: string
  /** assistant 的追问建议（**独立第二次调用**的产物，不在 `content` 里） */
  follow_ups?: string[]
}

export interface ConvFull extends ConvEntry {
  messages: ConvMessage[]
  doc_ids: string[]
}

export async function listConversations(): Promise<ConvEntry[]> {
  return (await apiFetch(`${BASE}/conversations`)).json()
}

export async function getConversation(id: string): Promise<ConvFull | null> {
  const r = await apiFetch(`${BASE}/conversations/${id}`)
  const data = await r.json()
  if (data.error) return null
  return data
}

export async function deleteConversation(id: string) {
  return (await apiFetch(`${BASE}/conversations/${id}`, { method: 'DELETE' })).json()
}

export async function renameConversation(id: string, title: string) {
  return (await apiFetch(`${BASE}/conversations/${id}/rename`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })).json()
}

export async function archiveConversation(id: string, archived: boolean) {
  return (await apiFetch(`${BASE}/conversations/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ archived }),
  })).json()
}

export async function rebuildConversationMessages(
  id: string,
  messages: { id: string; role: string; content: string; doc_refs?: unknown; tool_calls?: unknown; thinking?: string; follow_ups?: unknown }[],
) {
  return (await apiFetch(`${BASE}/conversations/${id}/messages`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  })).json()
}

/** 清除上下文边界（C4）— 不删消息，停止携带该索引前历史；索引前消息也不再参与压缩 */
export async function clearConversationContext(id: string, index: number) {
  return (await apiFetch(`${BASE}/conversations/${id}/clear-context`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index }),
  })).json()
}

/**
 * 「换一批」追问建议 —— 重新生成同一条回答的追问候选。
 *
 * ⚠️ **入参只有原文，没有会话 / 消息标识**（后端端点也是无状态的）。原因是**消息 id
 * 两侧不同源**：这里 `ChatMsg.id` 由 `nextMsgId()` 生成，后端 `SessionManager` 生成的是
 * `msg_…` —— 一次会话进行中两者必然不同，拿前端的 id 去后端定位只会 404 或定位到别的
 * 消息。⚠️ 于是**落库由调用方负责**（`chatStore.refreshFollowUps` 拿到新一批后调
 * `rebuildConversationMessages`）—— 端点本身不碰会话数据。
 *
 * ⚠️ 返回形状与 SSE 的 `followup` 事件**逐字相同**（后端 `followup_payload` 只此一份）：
 * `questions` 为空即失败，`error` 非空是给人看的原因。⚠️ 生成不出问题**不是**请求失败
 * （HTTP 200 + `error`），调用方不要靠 try/catch 判失败。
 */
export async function refreshFollowUps(
  question: string, answer: string, modelId?: number | null,
): Promise<{ type: string; questions: string[]; error: string | null }> {
  return (await apiFetch(`${BASE}/chat/followups`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, answer, model_id: modelId ?? null }),
  })).json()
}
