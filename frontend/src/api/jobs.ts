/**
 * 后台任务（`/api/jobs`）—— 长操作的状态读取与取消。
 *
 * ⚠️ 任务本身跑在后端（`services/job_queue.py`），这里**只读不推**：没有 SSE，
 * 用轮询。理由是这些任务的粒度是「几十秒到几分钟一次进度更新」，为此开一条长连接
 * 要维护重连与生命周期，收益不成比例。
 */
import { apiFetch, BASE } from './core'

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface JobInfo {
  id: string
  kind: string
  /** 中文名，后端注册表下发（`JobQueue.kinds`）—— 前端不抄第二份 */
  label: string
  status: JobStatus
  payload: Record<string, unknown>
  /** 0–1；尚未上报时为 null（不是 0 —— 「没开始」与「0%」是两件事） */
  progress: number | null
  message: string | null
  result: unknown
  error: string | null
  attempts: number
  cancel_requested: boolean
  dismissed: boolean
  /** epoch ms（后端秒 × 1000） */
  created_at: number
  started_at: number | null
  finished_at: number | null
}

/** 进行中 + 未忽略的失败。省略 status 时后端也是这个默认值。 */
export async function listJobs(): Promise<JobInfo[]> {
  const r = await apiFetch(`${BASE}/jobs`)
  const data = (await r.json()) as { jobs: JobInfo[] }
  return data.jobs
}

export async function cancelJob(id: string): Promise<JobInfo> {
  const r = await apiFetch(`${BASE}/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
  const data = (await r.json()) as { job: JobInfo }
  return data.job
}

export async function dismissJob(id: string): Promise<JobInfo> {
  const r = await apiFetch(`${BASE}/jobs/${encodeURIComponent(id)}/dismiss`, { method: 'POST' })
  const data = (await r.json()) as { job: JobInfo }
  return data.job
}
