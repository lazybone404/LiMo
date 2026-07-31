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
  onToolStart: (display: string) => void
  onStatus?: (message: string) => void
  onToolResult?: (tool: string, summary: string) => void
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
        else if (data.type === 'tool_start') callbacks.onToolStart(data.display)
        else if (data.type === 'tool_result') callbacks.onToolResult?.(data.tool, data.summary)
        else if (data.type === 'done') callbacks.onDone()
        else if (data.type === 'error') callbacks.onError(data.message)
      })
    })
    .catch(err => {
      if ((err as Error).name === 'AbortError') return
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
