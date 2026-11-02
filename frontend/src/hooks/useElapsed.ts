/**
 * 「已用时」—— 长任务唯一能诚实给的时间信息。
 *
 * ⚠️ **刻意不做 ETA**：生成侧 SSE 只有 `stage` 文本、没有百分比，模型自己也不知道
 * 会写多长。编一个「预计还需 2 分钟」比不显示更误导（差得远时用户会以为卡住了）。
 *
 * ⚠️ 每秒 tick 的 state **住在本 hook 里**，只有真的在渲染它的那两个组件
 * （`TaskPanel` / `ProcessingPlaceholder`）会重渲染；`startedAt` 为空时
 * **不挂 interval**（组件常在、任务不常在）。
 */
import { useEffect, useState } from 'react'

/** 秒数 → 人话。纯函数，单独导出以便表驱动测试 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  if (total < 60) return `${total} 秒`
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m < 60) return `${m} 分 ${String(s).padStart(2, '0')} 秒`
  const h = Math.floor(m / 60)
  return `${h} 小时 ${String(m % 60).padStart(2, '0')} 分`
}

/**
 * @param startedAt epoch ms；`undefined` 时返回 `null`（调用方据此不渲染）
 */
export function useElapsed(startedAt?: number): string | null {
  const [, tick] = useState(0)

  useEffect(() => {
    if (!startedAt) return
    const id = window.setInterval(() => tick(n => n + 1), 1000)
    return () => window.clearInterval(id)
  }, [startedAt])

  if (!startedAt) return null
  return formatElapsed(Date.now() - startedAt)
}
