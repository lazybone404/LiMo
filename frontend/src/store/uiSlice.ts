/**
 * UI slice — 插槽布局 + AI 浮窗 + activeView + 后端就绪 + 阅读宽度 + 后端能力。
 *
 * 2026-08-04：AI 对话移除插槽面板，统一为浮窗（唯一形态）。
 * 浮窗显隐 = aiVisible；位置/尺寸持久化到 localStorage（mbw_aiPos / mbw_aiSize）。
 */
import type { StateCreator } from 'zustand'
import type { Store, SlotState, ActiveView, ContentWidth } from './types'
import type { Capabilities } from '../api/client'

// ── AI 浮窗默认/记忆 ──

const DEFAULT_AI_POS = () => ({ x: window.innerWidth - 500, y: window.innerHeight - 560 })
const DEFAULT_AI_SIZE = { width: 480, height: 540 }

function loadJson<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    if (v) return JSON.parse(v) as T
  } catch { /* 损坏 JSON 忽略，用默认 */ }
  return fallback
}

export interface UiSlice {
  // ── UI 布局（插槽系统，不含 AI——AI 走浮窗） ──
  /** 统一的面板状态：{ genPanel, docLib, toc } */
  slots: Record<string, SlotState>
  /** 通用：更新一个 slot 的任意字段 */
  setSlot: (id: string, patch: Partial<SlotState>) => void
  // 保留以下设置为兼容别名（内部委托到 slots）：
  toggleGenPanel: () => void
  setGenPanelOpen: (v: boolean) => void

  // ── AI 浮窗（唯一形态） ──
  aiVisible: boolean
  aiPos: { x: number; y: number }
  aiSize: { width: number; height: number }
  toggleAI: () => void
  setAiVisible: (v: boolean) => void
  setAiPos: (pos: { x: number; y: number }) => void
  setAiSize: (size: { width: number; height: number }) => void

  activeView: ActiveView
  setActiveView: (v: string) => void

  // 后端就绪（Tauri 冷启动保护）
  backendReady: boolean
  setBackendReady: (v: boolean) => void

  // 后端运行状态（P1.1 持续探活）：ready=正常 / down=断开重试中 / crashed=长时间无响应（重启应用）
  backendStatus: 'ready' | 'down' | 'crashed'
  setBackendStatus: (v: 'ready' | 'down' | 'crashed') => void

  // ── 内容阅读宽度 ──
  contentWidth: ContentWidth
  cycleContentWidth: () => void

  // ── 后端能力 ──
  capabilities: Capabilities | null
  setCapabilities: (c: Capabilities) => void

  // ── 产物编辑中未保存标记 ──
  // PreviewArea 编辑态（editing + editDirty）的轻量桥：编辑内容归属文档 id。
  // 供 GenPanel「重新生成」入口检查「编辑中未保存 × 重新生成」拦截（评审遗留①）——
  // 重新生成会覆盖产物，但编辑框还开着旧值，保存会把旧编辑写回新产物。
  // 只暴露布尔/文档 id 标记，不提升编辑态（editValue 等）本身到 store。
  dirtyEditingDocId: string | null
  setDirtyEditingDocId: (docId: string | null) => void
}

export const createUiSlice: StateCreator<Store, [], [], UiSlice> = (set) => ({
  activeView: 'polished',
  slots: {
    genPanel: { width: 260, collapsed: false, visible: true },
    docLib:   { width: 260, collapsed: false, visible: true },
    preview:  { width: 0,   collapsed: false, visible: true },
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
  setGenPanelOpen: (v) => set(s => ({ slots: { ...s.slots, genPanel: { ...s.slots.genPanel, visible: v } } })),

  // ── AI 浮窗 ──
  aiVisible: false,
  aiPos: loadJson('mbw_aiPos', DEFAULT_AI_POS()),
  aiSize: loadJson('mbw_aiSize', DEFAULT_AI_SIZE),
  toggleAI: () => set(s => ({ aiVisible: !s.aiVisible })),
  setAiVisible: (v) => set({ aiVisible: v }),
  setAiPos: (pos) => { try { localStorage.setItem('mbw_aiPos', JSON.stringify(pos)) } catch { /* quota */ } set({ aiPos: pos }) },
  setAiSize: (size) => { try { localStorage.setItem('mbw_aiSize', JSON.stringify(size)) } catch { /* quota */ } set({ aiSize: size }) },

  setActiveView: (v) => { try { localStorage.setItem('mbw_lastView', v) } catch { /* quota exceeded */ } set({ activeView: v as ActiveView }) },

  // ⚠️ Tauri 冷启动时后端要 ~2s 才就绪，而 effect 在 render **之后**才跑 ——
  // 初值给 true 的话，首渲染就会挂载完整 UI（GenPanel 一挂载就拉 /api/models、
  // /api/providers），必然失败，然后才被 setBackendReady(false) 换回加载屏：
  // 既刷一屏 ERR_CONNECTION_REFUSED，界面还会闪一下。
  // 故此处**同步**判定：Tauri 环境初值即 false，浏览器环境没有后端启动过程，直接 true。
  backendReady: !(typeof window !== 'undefined' && (window as any).__TAURI_INTERNALS__),
  setBackendReady: (v) => set({ backendReady: v }),

  backendStatus: 'ready',  // 持续探活（lib/health.ts）更新：down→crashed 分级
  setBackendStatus: (v) => set({ backendStatus: v }),

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

  dirtyEditingDocId: null,
  setDirtyEditingDocId: (docId) => set({ dirtyEditingDocId: docId }),
})
