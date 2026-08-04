/**
 * 对话 UI 状态 slice — AI 浮窗 + 生成/审核状态。
 *
 * 对话管理（会话列表 / 消息 / 流式状态机）已迁至独立 chatStore（store/chatStore.ts）。
 * 此 slice 只保留主 store 仍需要的跨域 UI 状态。
 */
import type { StateCreator } from 'zustand'
import type { Store } from './types'

export interface ChatSlice {
  // ── 生成状态 ──
  generating: boolean
  setGenerating: (v: boolean) => void
  factChecking: boolean
  setFactChecking: (v: boolean) => void

  // ── AI 浮动对话 ──
  aiFloating: boolean
  aiFloatingPos: { x: number; y: number }
  aiFloatingSize: { width: number; height: number }
  setAiFloating: (v: boolean) => void
  setAiFloatingPos: (pos: { x: number; y: number }) => void
  setAiFloatingSize: (size: { width: number; height: number }) => void
}

export const createChatSlice: StateCreator<Store, [], [], ChatSlice> = (set) => ({
  generating: false,
  setGenerating: (v) => set({ generating: v }),
  factChecking: false,
  setFactChecking: (v) => set({ factChecking: v }),

  aiFloating: false,
  aiFloatingPos: { x: window.innerWidth - 500, y: window.innerHeight - 560 },
  aiFloatingSize: { width: 480, height: 540 },
  setAiFloating: (v) => set({ aiFloating: v }),
  setAiFloatingPos: (pos) => set({ aiFloatingPos: pos }),
  setAiFloatingSize: (size) => set({ aiFloatingSize: size }),
})
