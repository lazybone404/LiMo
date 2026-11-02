/**
 * Toast slice — 全局通知。
 * msgCounter 为本文件模块级状态，nextMsgId 也在此导出（index.ts re-export）。
 */
import type { StateCreator } from 'zustand'
import type { Store, ToastItem } from './types'

let msgCounter = 0

export function nextMsgId() { return `msg_${++msgCounter}_${Date.now()}` }

/** `addToast` 的可选扩展。**第 3 个参数仍是 type**，这里的 opts 是第 4 个 ——
 *  既有 `addToast(msg, 'error')` 调用一行都不用改。 */
export interface ToastOptions {
  /** 动作按钮文案（如「撤销」）。给按钮一个比默认更长的存活窗口才有意义 ——
   *  撤销的窗口由**调用方**决定（`duration`），不在这里猜。 */
  actionLabel?: string
  onAction?: () => void
  /** 存活毫秒数。不传按 type 取默认（error 6000 / 其余 3000）。 */
  duration?: number
}

export interface ToastSlice {
  toasts: ToastItem[]
  addToast: (message: string, type?: 'success' | 'error' | 'info', opts?: ToastOptions) => void
  dismissToast: (id: number) => void
}

export const createToastSlice: StateCreator<Store, [], [], ToastSlice> = (set) => ({
  toasts: [],
  addToast: (message, type = 'success', opts) => {
    const id = ++msgCounter
    const duration = opts?.duration ?? (type === 'error' ? 6000 : 3000)
    // 只有**两者都在**时才带上动作：只给文案不给回调是点不动的按钮，
    // 只给回调不给文案是空白按钮（两种都不该渲染，见 Toast.tsx 的判据）。
    const action = opts?.actionLabel && opts?.onAction
      ? { actionLabel: opts.actionLabel, onAction: opts.onAction }
      : {}
    set(s => {
      const next = [...s.toasts, { id, message, type, ...action }]
      if (next.length > 5) next.shift()
      return { toasts: next }
    })
    setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })), duration)
  },
  dismissToast: (id) => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
})
