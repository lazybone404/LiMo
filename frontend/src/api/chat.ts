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
  onMeta: (conversationId: string) => void
  onText: (text: string) => void
  onToolStart: (tool: string, display: string) => void
  onStatus?: (message: string) => void
  onToolResult?: (tool: string, summary: string) => void
  /** assistant 回答引用文档（P5 后端 ref 事件） */
  onRef?: (docId: string, title: string) => void
  /** 思考段流式（P5 后端 thinking 事件） */
  onThinking?: (text: string) => void
  onDone: () => void
  onError: (message: string) => void
}

export function streamChat(
  question: string,
  docIds: string[],
  callbacks: StreamChatCallbacks,
  signal?: AbortSignal,
  conversationId?: string,
): void {
  const params = new URLSearchParams({ question })
  if (docIds.length > 0) params.set('doc_ids', docIds.join(','))
  if (conversationId) params.set('conversation_id', conversationId)

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
        if (data.type === 'meta') callbacks.onMeta(data.conversation_id)
        else if (data.type === 'text') callbacks.onText(data.text)
        else if (data.type === 'agent_status') callbacks.onStatus?.(data.message)
        else if (data.type === 'tool_start') callbacks.onToolStart(data.tool ?? '', data.display)
        else if (data.type === 'tool_result') callbacks.onToolResult?.(data.tool, data.summary)
        else if (data.type === 'ref') callbacks.onRef?.(data.docId, data.title)
        else if (data.type === 'thinking') callbacks.onThinking?.(data.text)
        else if (data.type === 'done') callbacks.onDone()
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

export interface ConvFull extends ConvEntry {
  messages: { id: string; role: string; content: string; timestamp: string }[]
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
