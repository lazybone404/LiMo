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

// -- 文件夹（可嵌套的纯逻辑分类）--
//
// ⚠️ 四个出口（list / create / rename / move）返回的条目**形状相同**：
// `{id, name, createdAt, parentId, count, totalCount}`。后端为此在 create / rename
// 时补了计数键——否则「新建后直接并入本地表」的写法会让计数渲染成空白而不是 0。

export async function listFolders(): Promise<FolderEntry[]> {
  const r = await apiFetch(`${BASE}/folders`)
  const data = await r.json()
  return Array.isArray(data) ? data : []
}

/** 新建**单层**文件夹（`parentId` 省略 = 根级）。
 *  ⚠️ 多层路径不走这里——逐层调用是 N 次独立事务，任一层失败时前几层已经落盘。
 *  多层一律走 `ensureFolderPath`。 */
export async function createFolder(name: string, parentId = ''): Promise<FolderEntry> {
  const r = await apiFetch(`${BASE}/folders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parentId }),
  })
  return r.json()
}

/**
 * 确保一条路径存在（`技术/前端` → 缺哪层建哪层，已有则复用）。
 *
 * **一次锁、一次写盘、全有或全无**，是本项目里「建多层夹」唯一允许的入口。
 * 返回 `created` = 本轮真正新建的条目（已在时为空数组），前端可直接并入本地表。
 *
 * ⚠️ **前端刻意不实现路径解析**：`/` 切分、整串优先命中现有夹名、同名歧义判定
 * 全部住后端 `folder_tree.parse_path` 一处。在这里再写一份 = 第二个真相源，
 * 而两份实现的分歧只会在「名字里本来就有 `/`」这类历史数据上暴露。
 */
export async function ensureFolderPath(
  path: string, parentId = '',
): Promise<{ id: string; segments: string[]; created: FolderEntry[] }> {
  const r = await apiFetch(`${BASE}/folders/ensure-path`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, parentId }),
  })
  return r.json()
}

/**
 * PATCH 文件夹的唯一底层。
 *
 * ⚠️ 只发**实际要改的键**：后端把「两项都不给」判成 400，而发 `{name: undefined}`
 * 会被 JSON.stringify 丢掉、发 `{name: ''}` 会被判成空名——两种「没想改它」的写法
 * 都会变成错误。故由下面两个函数各发一个键。
 */
async function patchFolder(
  id: string, patch: { name?: string; parentId?: string },
): Promise<FolderEntry> {
  const r = await apiFetch(`${BASE}/folders/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return r.json()
}

/** 原地重命名。 */
export async function renameFolder(id: string, name: string): Promise<FolderEntry> {
  return patchFolder(id, { name })
}

/** 移动到另一个父级（`parentId = ''` = 移到根级）。移到自己或自己的后代 → 后端 400。 */
export async function moveFolder(id: string, parentId: string): Promise<FolderEntry> {
  return patchFolder(id, { parentId })
}

/**
 * 删除文件夹**及其整棵子树**。各层文档回落未分类（**绝不删文档**）。
 *
 * ⚠️ `deletedFolderIds` 是前端本地回落的**唯一依据**：用本地 `descendantIds` 再算
 * 一遍「哪些文档要回落」就是第二个真相来源，后端改语义时前端会静默停在旧规则上。
 */
export async function deleteFolder(id: string): Promise<{
  status: string
  /** 迁回未分类的文档数（含所有后代层的） */
  moved: number
  /** 连带删掉的文件夹行数（含自身） */
  deletedFolders: number
  /** 被删掉的文件夹 id（含自身与全部后代） */
  deletedFolderIds: string[]
}> {
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
