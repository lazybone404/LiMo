/**
 * 对话 UI 状态 slice — 生成/审核状态。
 *
 * 对话管理（会话列表 / 消息 / 流式状态机）在独立 chatStore（store/chatStore.ts）；
 * AI 浮窗状态（aiVisible/aiPos/aiSize）已迁至 uiSlice（2026-08-04，浮窗为唯一形态）。
 */
import type { StateCreator } from 'zustand'
import type { Store } from './types'

export interface ChatSlice {
  // ── 生成状态 ──
  generating: boolean
  setGenerating: (v: boolean) => void
  factChecking: boolean
  setFactChecking: (v: boolean) => void
}

export const createChatSlice: StateCreator<Store, [], [], ChatSlice> = (set) => ({
  generating: false,
  setGenerating: (v) => set({ generating: v }),
  factChecking: false,
  setFactChecking: (v) => set({ factChecking: v }),
})
