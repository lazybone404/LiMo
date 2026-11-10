/**
 * epub 域 — 电子书一键解读（逐章解读 + 归并总结 + 中断续跑）。
 * 路由：POST /api/epub/generate/{doc_id} · GET /api/epub/task/{doc_id}
 *
 * ⚠️ **进度不在这里轮询**（2026-11-10 起）。解读跑在后端任务队列里，进度与取消
 * 住在 `jobs`（`store/jobsSlice.ts`，全局 1.5s 轮询），本文件只负责「提交」与
 * 「这本书上次解读到哪」。
 */
import { apiFetch, BASE } from './core'
import type { JobInfo } from './jobs'

export interface EpubTaskStatus {
  /** 是否有进行中的 `epub.interpret` 任务（从队列读，不是本地推断） */
  running: boolean
  /** 进行中那条任务的 id（没有则为 null）—— 取消按钮用它 */
  job_id: string | null
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

/**
 * 提交/续跑解读任务。**提交即返回**，不等解读本身。
 *
 * `created=false` = 幂等键命中了正在跑的那条（同一文档同时只会有一条解读任务），
 * 那时 `job` 就是那条 —— 等同于原来的「该文档正在解读中」，只是不报错了。
 */
export async function startEpubInterpret(
  docId: string,
  opts: StartEpubOptions = {},
): Promise<{ job: JobInfo; created: boolean }> {
  const r = await apiFetch(`${BASE}/epub/generate/${docId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
  return r.json()
}

/** 这本书上次解读到哪（落盘的 `epub_task.json`）+ 在不在跑（队列）。 */
export async function getEpubTask(docId: string): Promise<EpubTaskStatus> {
  const r = await apiFetch(`${BASE}/epub/task/${docId}`)
  return r.json()
}
