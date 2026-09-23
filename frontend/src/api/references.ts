/**
 * references 域 — 参考文件暂存（POST /api/references，后端内存 + TTL 20 分钟）。
 *
 * 背景：流式 generate 是 GET（query string），参考文件 ≤1MB 无法直接放 URL。
 * 前端选文件 → 读内容 → 暂存 → 拿短 id → 生成时只传 id（reference_ids）。
 */
import { apiFetch, BASE } from './core'

/** 暂存参考文件，返回短 id（20 分钟 TTL，重启清空） */
export async function saveReference(name: string, content: string): Promise<{ id: string }> {
  const r = await apiFetch(`${BASE}/references`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, content }),
  })
  return r.json()
}
