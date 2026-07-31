/**
 * UI slice — 插槽布局 + activeView + 后端就绪 + 阅读宽度 + 后端能力。
 */
import type { StateCreator } from 'zustand'
import type { Store, SlotState, ActiveView, ContentWidth } from './types'
import type { Capabilities } from '../api/client'

export interface UiSlice {
  // ── UI 布局（插槽系统） ──
  /** 统一的面板状态：{ genPanel, docLib, aiPanel, toc } */
  slots: Record<string, SlotState>
  /** 通用：更新一个 slot 的任意字段 */
  setSlot: (id: string, patch: Partial<SlotState>) => void
  // 保留以下设置为兼容别名（内部委托到 slots）：
  toggleGenPanel: () => void
  toggleAI: () => void
  setGenPanelOpen: (v: boolean) => void
  setAiVisible: (v: boolean) => void
  activeView: ActiveView
  setActiveView: (v: string) => void

  // 后端就绪（Tauri 冷启动保护）
  backendReady: boolean
  setBackendReady: (v: boolean) => void

  // ── 内容阅读宽度 ──
  contentWidth: ContentWidth
  cycleContentWidth: () => void

  // ── 后端能力 ──
  capabilities: Capabilities | null
  setCapabilities: (c: Capabilities) => void
}

export const createUiSlice: StateCreator<Store, [], [], UiSlice> = (set) => ({
  activeView: 'polished',
  slots: {
    genPanel: { width: 260, collapsed: false, visible: true },
    docLib:   { width: 260, collapsed: false, visible: true },
    preview:  { width: 0,   collapsed: false, visible: true },
    aiPanel:  { width: 340, collapsed: false, visible: false },
    toc:      { width: 160, collapsed: false, visible: true },
  },
  setSlot: (id, patch) => set(s => {
    const prev = s.slots[id]
    if (!prev) return s
    const next = { ...prev, ...patch }
    // 自动折叠：宽度 < 80 时标记为折叠
    if (patch.width !== undefined && patch.width < 80) {
      next.collapsed = true
    }
    return { slots: { ...s.slots, [id]: next } }
  }),
  // 兼容别名（保持旧 API 可用，避免大面积改 call site）
  toggleGenPanel: () => set(s => ({ slots: { ...s.slots, genPanel: { ...s.slots.genPanel, visible: !s.slots.genPanel.visible } } })),
  toggleAI: () => set(s => ({ slots: { ...s.slots, aiPanel: { ...s.slots.aiPanel, visible: !s.slots.aiPanel.visible } } })),
  setGenPanelOpen: (v) => set(s => ({ slots: { ...s.slots, genPanel: { ...s.slots.genPanel, visible: v } } })),
  setAiVisible: (v) => set(s => ({ slots: { ...s.slots, aiPanel: { ...s.slots.aiPanel, visible: v } } })),
  setActiveView: (v) => { try { localStorage.setItem('mbw_lastView', v) } catch { /* quota exceeded */ } set({ activeView: v as ActiveView }) },

  backendReady: true,  // 浏览器模式默认 true，Tauri 模式在 App.tsx useEffect 中重置
  setBackendReady: (v) => set({ backendReady: v }),

  contentWidth: ((typeof localStorage !== 'undefined' && localStorage.getItem('mbw_contentWidth')) || 'normal') as ContentWidth,
  cycleContentWidth: () => {
    const order: ContentWidth[] = ['normal', 'wide', 'narrow']
    set(s => {
      const i = order.indexOf(s.contentWidth)
      const next = order[(i + 1) % order.length]
      try { localStorage.setItem('mbw_contentWidth', next) } catch { /* quota */ }
      return { contentWidth: next }
    })
  },

  capabilities: null,
  setCapabilities: (c) => set({ capabilities: c }),
})
