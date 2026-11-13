/**
 * 「首屏可交互」打点 —— 启动链最末一段，只有前端量得到。
 *
 * 为什么需要它：后端的 perf.log 只覆盖到 `GET /health` 200（`startup.import` /
 * `startup.engines` / `startup.search_index`）。那之后还有 React 挂载、布局计算、
 * 数据首屏渲染，这一段在 0.5.25 的日志里**一个数都没有**，而这个数字是「用户按了
 * 图标之后到底等了多久」的最后一块。
 *
 * ⚠️ **判据不是 `DOMContentLoaded`，也不是「React 挂载完成」**。两种都不等于能点：
 * 加载屏（`正在启动后端服务…`）在挂载之后仍然盖着整个界面，按钮存在但点了没反应；
 * 首次启动的向导也是全屏遮罩。故这里的判据是**命中测试**：活动栏第一个按钮的
 * 中心点，`document.elementFromPoint` 命中的必须是它自己或它的后代 —— 布局已完成、
 * 且没有任何东西盖在它上面。这个探针坏掉的方式与它要防的东西恰好同形：多一层遮罩
 * 就判不出来，而那正是「看起来画好了、其实点不动」。
 *
 * ⚠️ **量的是 `performance.now()`**：页面导航起点到这一刻。它包含资源加载与 React
 * 挂载，也就是用户感知的那一段。与后端 `perf.log` 里 `GET /health` 那一行的 `ts`
 * 相减即得「/health 200 → 首屏可点」——两个时间戳都是本机墙钟，不需要对时。
 * 请求体里的 `ms` 同时带上页面内的相对时间，两者互为校验。
 *
 * ⚠️ **拿不到就不报**：等待超过 `MAX_FRAMES`（约 3 秒）仍未可点（例如卡在失败屏、
 * 或用户停在首次启动向导上），放弃且**不发任何数据** —— 把「没画完」记成一条
 * 很长的耗时，会让这条统计从此不可信。
 *
 * ⚠️ **失败静默**：这是诊断设施，与后端 `core/perf.py` 同一条纪律 —— 打点写不进去
 * 绝不影响任何功能，也不弹 toast。
 */
import { apiFetch, apiUrl } from '../api/core'

/** 后端白名单里的名字（`main.py::_CLIENT_PERF_NAMES`）。改这里必须同时改那边。 */
export const BOOT_REPORT_NAME = 'startup.frontend_interactive'

/** 轮询上限：60fps 下约 3 秒。再久就不是「慢启动」而是「没画出来」。 */
export const MAX_FRAMES = 180

let reported = false

/**
 * 主界面此刻是否可点（判据见模块注释）。
 *
 * ⚠️ jsdom 没有 `elementFromPoint`（测试环境），那里直接判否 —— 不伪造一个「可点」。
 */
export function isMainUiClickable(): boolean {
  if (typeof document === 'undefined' || typeof document.elementFromPoint !== 'function') {
    return false
  }
  const btn = document.querySelector<HTMLElement>('[data-activity-item]')
  if (!btn) return false
  const r = btn.getBoundingClientRect()
  if (r.width <= 0 || r.height <= 0) return false
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
  return hit === btn || btn.contains(hit)
}

/**
 * 单次：可点就上报，返回是否上报了。**整个页面只上报一次**（`reported` 守卫）——
 * 组件重挂载、effect 重跑都不该让统计里多出一条。
 */
export async function reportBootInteractive(ms: number): Promise<boolean> {
  if (reported) return false
  if (!isMainUiClickable()) return false
  reported = true
  try {
    await apiFetch(apiUrl('/perf/client'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: BOOT_REPORT_NAME, ms: Math.round(ms) }),
    })
    return true
  } catch {
    // 打点失败不影响任何功能；也不再重试（重试会把「网络抖动」变成一条偏大的数）
    return false
  }
}

/**
 * 逐帧等到可交互再上报（`App.tsx` 在「后端就绪 + 无失败屏 + 无向导」时调用）。
 *
 * 用 rAF 而不是 `setTimeout`：判据是**画面**上的状态，而 rAF 的回调恰好排在
 * 下一次布局 / 绘制之后 —— 用定时器可能反复命中一个还没被绘制出来的 DOM 状态。
 */
export function reportFirstInteractive(maxFrames: number = MAX_FRAMES): void {
  if (typeof requestAnimationFrame !== 'function') return
  let frames = 0
  const tick = () => {
    if (reported) return
    if (isMainUiClickable()) {
      void reportBootInteractive(performance.now())
      return
    }
    if (++frames >= maxFrames) return
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

/** 仅测试用：复位「已上报」守卫。 */
export function _resetBootTimingForTest(): void {
  reported = false
}
