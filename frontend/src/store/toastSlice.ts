/**
 * Toast slice — 全局通知。
 * msgCounter 为本文件模块级状态，nextMsgId 也在此导出（index.ts re-export）。
 */
import type { StateCreator } from 'zustand'
import type { Store, ToastItem } from './types'

let msgCounter = 0

export function nextMsgId() { return `msg_${++msgCounter}_${Date.now()}` }

export interface ToastSlice {
  toasts: ToastItem[]
  addToast: (message: string, type?: 'success' | 'error' | 'info') => void
  dismissToast: (id: number) => void
}

export const createToastSlice: StateCreator<Store, [], [], ToastSlice> = (set) => ({
  toasts: [],
  addToast: (message, type = 'success') => {
    const id = ++msgCounter
    const duration = type === 'error' ? 6000 : 3000
    set(s => {
      const next = [...s.toasts, { id, message, type }]
      if (next.length > 5) next.shift()
      return { toasts: next }
    })
    setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })), duration)
  },
  dismissToast: (id) => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
})
