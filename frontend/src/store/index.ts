/**
 * store 组合层 — 各 slice 在此合并为 useStore。
 * 字段/action 定义见对应 slice 文件，共享类型见 types.ts。
 */
import { create } from 'zustand'
import { applyTheme, resolveTheme, getSavedTheme } from '../lib/theme'
import { applyAccent, getSavedAccent } from '../lib/accent'
import { applyTexture, applyFont, applyIntensity, getSavedTexture, getSavedFont, getSavedIntensity } from '../lib/texture'
import { applyBase, getSavedBase } from '../lib/theme-base'
import { applyReading, getSavedReading, nextFontScale, READING_DEFAULTS } from '../lib/reading-prefs'
import { applyCard, getSavedCard } from '../lib/card-prefs'
import { applyMarkers, getSavedMarkers } from '../lib/marker-prefs'
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
      const dead = new Set(ids)

      const imageResults = { ...s.imageResults }
      for (const id of ids) delete imageResults[id]
      // 流式缓冲是模块级 Map（不在 store 里），必须显式通知它清理
      for (const id of ids) deleteStreamBuffersForDoc(id)

      // ⚠️ 预览内容只在「被删的正是当前打开的那篇」时才清：删的是别的文档时，
      // 预览区显示的本来就不是它，一并清掉等于**无端清空用户的阅读位置**。
      const droppedCurrent = !!s.currentDocId && dead.has(s.currentDocId)

      // ── 标签组：滤掉被删项；若删的正是激活标签，按**原下标**在剩余标签里取邻居 ──
      // ⚠️ 「删除后的归属」**只在这里做**——`DocLibrary.runDelete` 里曾有一句
      // `selectDoc(null)` 抢先把 currentDocId 置空，于是这里 `droppedCurrent` 恒为 false，
      // 删掉激活文档后标签组清了却不会激活邻居。删那行就是为了让本函数独占这件事。
      const tabs = s.docTabs.filter(t => !dead.has(t.docId))
      let nextCurrent = s.currentDocId
      if (droppedCurrent && s.currentDocId) {
        // `tabs` 是**移除后**的数组，所以原本在 `idx + 1` 的右邻现在正好落在 `tabs[idx]`
        // （右邻也被删时它就是更右边的一个；全删光则 undefined → 退到左邻 → null）。
        const idx = s.docTabs.findIndex(t => t.docId === s.currentDocId)
        nextCurrent = tabs[idx]?.docId ?? tabs[idx - 1]?.docId ?? null
      }

      set({
        imageResults,
        docTabs: tabs,
        // 删除后任何在途导航意图都已失效（目标可能正是被删的那篇）
        pendingOpenMode: null,
        pendingClose: null,
        ...(droppedCurrent ? {
          currentContent: null, currentPolishedContent: null, currentRawText: null,
          markdownMeta: null, markdownVersions: [],
          markdownEdited: { notes: false, polished: false },
        } : {}),
      })

      if (droppedCurrent) {
        // ⚠️ 用 `selectDoc`（而非直接 set）——它同时维护 `mbw_lastDocId`，
        // 直接 set 会让下次启动拿一个已删的 id 去 restore。
        // 放在上面那次 set 之后：`selectDoc` 触发的 `loadDocContent` → `commitTabChange`
        // 读到的必须是已经滤干净的 `docTabs`。
        s.selectDoc(nextCurrent)
      }
    },

    // ── 正文字号缩放 ──
    // 两条入口（键盘 / 预览区滚轮）**共用这两个动作**，档位换算与 toast 都只此一份。
    stepFontScale: (dir) => {
      const s = get()
      const cur = s.reading.fontScale
      const next = nextFontScale(cur, dir)
      // 到顶 / 到底：静默（值没变就不弹，否则滚轮一直滚会刷一屏 toast）
      if (next === cur) return
      s.setReading({ fontScale: next })
      s.addToast(`字号 ${Math.round(next * 100)}%`)
    },
    resetFontScale: () => {
      const s = get()
      if (s.reading.fontScale === READING_DEFAULTS.fontScale) return
      s.setReading({ fontScale: READING_DEFAULTS.fontScale })
      s.addToast('字号已恢复为 100%')
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
applyCard(getSavedCard())
applyMarkers(getSavedMarkers())

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
