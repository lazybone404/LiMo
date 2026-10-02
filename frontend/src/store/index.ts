/**
 * store 组合层 — 各 slice 在此合并为 useStore。
 * 字段/action 定义见对应 slice 文件，共享类型见 types.ts。
 */
import { create } from 'zustand'
import { applyTheme, resolveTheme, getSavedTheme } from '../lib/theme'
import { applyAccent, getSavedAccent } from '../lib/accent'
import { applyTexture, applyFont, applyIntensity, getSavedTexture, getSavedFont, getSavedIntensity } from '../lib/texture'
import { applyBase, getSavedBase } from '../lib/theme-base'
import { applyReading, getSavedReading } from '../lib/reading-prefs'
import { createThemeSlice } from './themeSlice'
import { createDocsSlice } from './docsSlice'
import { createContentSlice } from './contentSlice'
import { createTasksSlice } from './tasksSlice'
import { createChatSlice } from './chatSlice'
import { createUiSlice } from './uiSlice'
import { createToastSlice } from './toastSlice'
import { deleteStreamBuffersForDoc } from './streamBuffers'
import type { Store } from './types'

// ── 类型 re-export（消费方零改动）──

export type { DocEntry, TaskInfo, ProgressEvent, ContentWidth, SlotState, ChatMessage, ToastItem } from './types'
export type { Capabilities } from '../api/client'
export { nextMsgId } from './toastSlice'

// ── Store 创建（slice 组合）──

export const useStore = create<Store>()((...a) => {
  const [set, get] = a
  return {
    ...createThemeSlice(...a),
    ...createDocsSlice(...a),
    ...createContentSlice(...a),
    ...createTasksSlice(...a),
    ...createChatSlice(...a),
    ...createUiSlice(...a),
    ...createToastSlice(...a),

    // 跨 slice 动作（类型见 types.ts 的 AppSlice）
    purgeDocArtifacts: (ids) => {
      if (!ids.length) return
      const s = get()

      const imageResults = { ...s.imageResults }
      for (const id of ids) delete imageResults[id]
      // 流式缓冲是模块级 Map（不在 store 里），必须显式通知它清理
      for (const id of ids) deleteStreamBuffersForDoc(id)

      // ⚠️ 预览内容只在「被删的正是当前打开的那篇」时才清：删的是别的文档时，
      // 预览区显示的本来就不是它，一并清掉等于**无端清空用户的阅读位置**。
      const droppedCurrent = !!s.currentDocId && ids.includes(s.currentDocId)

      set({
        imageResults,
        ...(droppedCurrent ? {
          currentContent: null, currentPolishedContent: null, currentRawText: null,
          markdownMeta: null, markdownVersions: [],
          markdownEdited: { notes: false, polished: false },
        } : {}),
      })
    },
  }
})

// Apply initial theme on store creation
applyTheme(resolveTheme(getSavedTheme()))
applyAccent(getSavedAccent())
applyTexture(getSavedTexture())
applyIntensity(getSavedIntensity())
applyFont(getSavedFont())
applyBase(getSavedBase())
applyReading(getSavedReading())

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
