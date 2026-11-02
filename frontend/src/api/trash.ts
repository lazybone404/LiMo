/**
 * trash 域 — 回收站（删除的可撤销面）。
 *
 * ⚠️ **撤销 toast 与回收站对话框共用这四个函数**，不各写一套恢复逻辑：
 * 恢复要做的事（搬目录回原位 + 写回索引条目 + 重建全文索引 + 给向量打 pending）
 * 全在后端 `restore_transcript` + `_index_for_search` 里。前端再实现一遍 =
 * 模式 #54 的经典形态（两份「什么算恢复成功」的判据，迟早分叉）。
 *
 * ⚠️ 回收站**只管文档目录**（`transcript.md` / `markdowns/` / `assets/`）。
 * 上传的原始文件在 `{root}/files/{hash}/`，删除文档今天就**不删它**——那是既有行为，
 * 与本轮无关。UI 上必须把这条说清（见 `TrashDialog` 顶部那句话），否则用户会以为
 * 「清空回收站 = 磁盘腾出来了」。
 */
import { apiFetch, BASE } from './core'

/** 一条回收站条目。字段由后端 `Store.list_trash()` 装配。 */
export interface TrashItem {
  id: string
  title: string
  sourceType: string
  /** ISO 字符串（删除时刻） */
  deletedAt: string
  /** ISO 字符串（过期时刻 = 删除 + 保留天数） */
  expiresAt: string
  /** 还剩几天（0 = 今天到期）。后端已夹紧到 [0, retentionDays] */
  daysLeft: number
}

export interface TrashList {
  items: TrashItem[]
  /** 保留天数。**由后端下发**，前端不写死 7 —— 改常量时不该还要改一处文案 */
  retentionDays: number
}

export interface TrashRestoreResult {
  status: string
  restored: string[]
  failed: { id: string; error: string }[]
}

export async function listTrash(): Promise<TrashList> {
  const r = await apiFetch(`${BASE}/trash`)
  const data = await r.json()
  // 形状兜底：后端异常时给出可渲染的空态，而不是让调用方在 `.map` 上炸掉
  return {
    items: Array.isArray(data?.items) ? data.items : [],
    retentionDays: typeof data?.retentionDays === 'number' ? data.retentionDays : 7,
  }
}

/**
 * 从回收站恢复（单条 = `ids: [one]`，**不为单条另开端点**）。
 *
 * ⚠️ 全部失败时后端返回 400（`TRASH_ALL_FAILED`），`apiFetch` 会抛 —— 调用方
 * 走 catch 即可。部分失败不抛，看 `failed` 明细。
 */
export async function restoreTrash(ids: string[]): Promise<TrashRestoreResult> {
  const r = await apiFetch(`${BASE}/trash/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  return r.json()
}

/** 彻底删除一条（不可恢复）。 */
export async function purgeTrash(id: string): Promise<{ status: string; id: string }> {
  const r = await apiFetch(`${BASE}/trash/${encodeURIComponent(id)}`, { method: 'DELETE' })
  return r.json()
}

/** 清空回收站。 */
export async function emptyTrash(): Promise<{ status: string; count: number }> {
  const r = await apiFetch(`${BASE}/trash`, { method: 'DELETE' })
  return r.json()
}
