/**
 * transcripts 域 — 文档查询 / CRUD / 重新转录。
 */
import { apiFetch, BASE, type DocEntry, type TranscriptFull } from './core'

export async function listTranscripts(params?: {
  q?: string; sourceType?: string; tag?: string
}): Promise<DocEntry[]> {
  const searchParams = new URLSearchParams()
  if (params?.q) searchParams.set('q', params.q)
  if (params?.sourceType) searchParams.set('sourceType', params.sourceType)
  if (params?.tag) searchParams.set('tag', params.tag)
  const qs = searchParams.toString()
  const r = await apiFetch(`${BASE}/transcripts${qs ? '?' + qs : ''}`)
  const data = await r.json()
  return Array.isArray(data) ? data : []
}

export async function getTranscript(id: string): Promise<TranscriptFull | null> {
  const r = await apiFetch(`${BASE}/transcripts/${id}`)
  const data = await r.json()
  if (data.error || data.code) return null
  return data as TranscriptFull
}

// -- transcript CRUD --

export async function updateTranscript(
  id: string,
  data: { title?: string; tags?: string[] },
): Promise<{ status: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function deleteTranscript(id: string): Promise<{ status: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${id}`, { method: 'DELETE' })
  return r.json()
}

// -- retranscribe --

export async function retranscribe(transcriptId: string, signal?: AbortSignal): Promise<{ status: string; engine: string } | { error: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${transcriptId}/retranscribe`, { method: 'POST', signal })
  return r.json()
}
