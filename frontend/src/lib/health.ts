/**
 * 后端持续探活（P1.1）— 启动探活之外的运行期监控。
 *
 * 启动探活（App.tsx）负责「后端未就绪加载屏」；本模块负责「运行中断开横幅」：
 * - 10s 轮询 /health（经 apiUrl：Tauri 下补全域名，浏览器走 Vite proxy）
 * - 连续 2 次失败 → status=down（「后端服务异常，正在重试…」）
 * - 连续失败超 30s → status=crashed（「后端已崩溃，请重启应用」）
 * - 恢复 → ready，横幅自动消失
 *
 * 状态存 uiSlice.backendStatus，App.tsx 用 useStore 订阅渲染横幅。
 */

import { apiUrl } from '../api/client'
import { useStore } from '../store'

const POLL_INTERVAL = 10_000       // 探活间隔 10s
const DOWN_THRESHOLD = 2           // 连续失败次数 → down
const CRASH_THRESHOLD_MS = 30_000  // 连续失败时长 → crashed

let started = false

/** 启动持续探活（幂等，App 挂载时调用一次）。返回 stop 函数（页面卸载时清理）。 */
export function startHealthPoll(): () => void {
  if (started) return () => {}
  started = true

  let consecutiveFails = 0
  let firstFailAt = 0

  const tick = async () => {
    const set = useStore.getState().setBackendStatus
    try {
      const r = await fetch(apiUrl('/health'), { cache: 'no-store' })
      if (!r.ok) throw new Error(`health ${r.status}`)
      // 恢复：重置计数与状态
      consecutiveFails = 0
      firstFailAt = 0
      if (useStore.getState().backendStatus !== 'ready') set('ready')
    } catch {
      consecutiveFails += 1
      if (firstFailAt === 0) firstFailAt = Date.now()
      const elapsed = Date.now() - firstFailAt
      const cur = useStore.getState().backendStatus
      if (elapsed >= CRASH_THRESHOLD_MS) {
        if (cur !== 'crashed') set('crashed')
      } else if (consecutiveFails >= DOWN_THRESHOLD) {
        if (cur !== 'down') set('down')
      }
    }
  }

  void tick()
  const timer = setInterval(tick, POLL_INTERVAL)
  return () => clearInterval(timer)
}
