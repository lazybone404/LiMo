/**
 * transcripts 域 — 文档查询 / CRUD / 重新转录。
 */
import { apiFetch, BASE, type DocEntry, type FolderEntry, type TranscriptFull } from './core'

export async function listTranscripts(params?: {
  q?: string; sourceType?: string; tag?: string; folderId?: string
}): Promise<DocEntry[]> {
  const searchParams = new URLSearchParams()
  if (params?.q) searchParams.set('q', params.q)
  if (params?.sourceType) searchParams.set('sourceType', params.sourceType)
  if (params?.tag) searchParams.set('tag', params.tag)
  // ⚠️ 必须用 `!== undefined`（模式 #6）：空串是**有效筛选值**（只看未分类），
  // 与「不传 = 不过滤」是两种语义。写成 `if (params?.folderId)` 会让
  // 「未分类」视图永远退化成看全部，而且后端返回 200、前端毫无察觉。
  if (params?.folderId !== undefined) searchParams.set('folderId', params.folderId)
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
  data: { title?: string; tags?: string[]; folderId?: string },
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

// -- 文件夹（单层逻辑分类）--

export async function listFolders(): Promise<FolderEntry[]> {
  const r = await apiFetch(`${BASE}/folders`)
  const data = await r.json()
  return Array.isArray(data) ? data : []
}

export async function createFolder(name: string): Promise<FolderEntry> {
  const r = await apiFetch(`${BASE}/folders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return r.json()
}

export async function renameFolder(id: string, name: string): Promise<FolderEntry> {
  const r = await apiFetch(`${BASE}/folders/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  return r.json()
}

/** 删除文件夹。其下文档回落未分类（**绝不删文档**），返回迁走的文档数。 */
export async function deleteFolder(id: string): Promise<{ status: string; moved: number }> {
  const r = await apiFetch(`${BASE}/folders/${id}`, { method: 'DELETE' })
  return r.json()
}

// -- 批量操作 --

export interface BatchResult {
  status: string
  affected: number
  failed: { id: string; error: string }[]
}

/**
 * 批量删除/打标签/移动。
 *
 * ⚠️ 失败不是异常而是**返回值的一部分**（failed 明细）——调用方必须检查
 * `failed.length` 并提示，否则「部分失败」会被当成完全成功。
 */
export async function batchTranscripts(payload: {
  action: 'delete' | 'tag' | 'move'
  ids: string[]
  tags?: string[]
  tagMode?: 'add' | 'replace'
  folderId?: string
}): Promise<BatchResult> {
  const r = await apiFetch(`${BASE}/transcripts/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return r.json()
}

// -- retranscribe --

export async function retranscribe(transcriptId: string, signal?: AbortSignal): Promise<{ status: string; engine: string } | { error: string }> {
  const r = await apiFetch(`${BASE}/transcripts/${transcriptId}/retranscribe`, { method: 'POST', signal })
  return r.json()
}
