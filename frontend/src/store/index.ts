/**
 * store 组合层 — 各 slice 在此合并为 useStore。
 * 字段/action 定义见对应 slice 文件，共享类型见 types.ts。
 */
import { create } from 'zustand'
import { applyTheme, resolveTheme, getSavedTheme } from '../lib/theme'
import { applyAccent, getSavedAccent } from '../lib/accent'
import { applyTexture, applyFont, getSavedTexture, getSavedFont } from '../lib/texture'
import { applyBase, getSavedBase } from '../lib/theme-base'
import { createThemeSlice } from './themeSlice'
import { createDocsSlice } from './docsSlice'
import { createContentSlice } from './contentSlice'
import { createTasksSlice } from './tasksSlice'
import { createChatSlice } from './chatSlice'
import { createUiSlice } from './uiSlice'
import { createToastSlice } from './toastSlice'
import type { Store } from './types'

// ── 类型 re-export（消费方零改动）──

export type { DocEntry, TaskInfo, ProgressEvent, ContentWidth, SlotState, ChatMessage, ToastItem } from './types'
export type { Capabilities } from '../api/client'
export { nextMsgId } from './toastSlice'

// ── Store 创建（slice 组合）──

export const useStore = create<Store>()((...a) => ({
  ...createThemeSlice(...a),
  ...createDocsSlice(...a),
  ...createContentSlice(...a),
  ...createTasksSlice(...a),
  ...createChatSlice(...a),
  ...createUiSlice(...a),
  ...createToastSlice(...a),
}))

// Apply initial theme on store creation
applyTheme(resolveTheme(getSavedTheme()))
applyAccent(getSavedAccent())
applyTexture(getSavedTexture())
applyFont(getSavedFont())
applyBase(getSavedBase())

// Listen for system theme changes when in "system" mode
// 模块副作用保持在此 — 监听器需要 useStore.getState()，移入 slice 会制造运行时循环依赖
const sysMq = window.matchMedia('(prefers-color-scheme: dark)')
sysMq.addEventListener('change', () => {
  const s = useStore.getState()
  if (s.theme === 'system') {
    const r = sysMq.matches ? 'dark' : 'light'
    applyTheme(r)
    useStore.setState({ resolvedTheme: r })
  }
})
