/**
 * epub 域 — 电子书一键解读（逐章解读 + 归并总结 + 中断续跑）。
 * 路由：POST /api/epub/generate|task|cancel/{doc_id}
 *
 * 进度不走 SSE：前端 2s 轮询 getEpubTask 驱动进度条。
 */
import { apiFetch, BASE } from './core'

export interface EpubTaskStatus {
  running: boolean
  /** chapters（阶段1 逐章）/ summary（阶段2 归并）/ done / null（无任务） */
  stage: 'chapters' | 'summary' | 'done' | null
  chapters_done: number[]
  chapters_failed: number[]
  total_chapters: number
  current_title: string
  remaining_chapters: number
  deep: boolean
  model_id: number | null
}

export interface StartEpubOptions {
  model_id?: number
  /** true = 输入全文，false = 骨架（省 ~70% token），默认骨架 */
  deep?: boolean
}

export async function startEpubInterpret(
  docId: string,
  opts: StartEpubOptions = {},
): Promise<{ started: boolean; total_chapters: number }> {
  const r = await apiFetch(`${BASE}/epub/generate/${docId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
  return r.json()
}

export async function getEpubTask(docId: string): Promise<EpubTaskStatus> {
  const r = await apiFetch(`${BASE}/epub/task/${docId}`)
  return r.json()
}

export async function cancelEpubInterpret(docId: string): Promise<{ cancelled: boolean }> {
  const r = await apiFetch(`${BASE}/epub/cancel/${docId}`, { method: 'POST' })
  return r.json()
}
