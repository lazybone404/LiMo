/**
 * chat 域 — 对话（流式/非流式）+ 对话持久化 CRUD。
 */
import { apiFetch, getErrorMessage, BASE, type ChatResult } from './core'
import { parseSSEStream } from './sse'

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
  onToolResult?: (tool: string, summary: string) => void
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
}

export function streamChat(
  question: string,
  docIds: string[],
  callbacks: StreamChatCallbacks,
  signal?: AbortSignal,
  conversationId?: string,
  docRefs?: { docId: string; title: string }[],
): void {
  const params = new URLSearchParams({ question })
  if (docIds.length > 0) params.set('doc_ids', docIds.join(','))
  if (conversationId) params.set('conversation_id', conversationId)
  if (docRefs && docRefs.length > 0) params.set('doc_refs', JSON.stringify(docRefs))

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
        if (data.type === 'meta') callbacks.onMeta(data.conversation_id, { context_window: data.context_window, budget: data.budget, price_in: data.price_in, price_out: data.price_out })
        else if (data.type === 'text') callbacks.onText(data.text)
        else if (data.type === 'agent_status') callbacks.onStatus?.(data.message)
        else if (data.type === 'tool_start') callbacks.onToolStart(data.tool ?? '', data.display)
        else if (data.type === 'tool_result') {
          callbacks.onToolResult?.(data.tool, data.summary)
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
  tool_calls?: { tool: string; display: string; summary?: string }[]
  thinking?: string
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
  messages: { id: string; role: string; content: string; doc_refs?: unknown; tool_calls?: unknown; thinking?: string }[],
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
