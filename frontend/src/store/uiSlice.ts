/**
 * UI slice — 插槽布局 + AI 浮窗 + activeView + 后端就绪 + 阅读宽度 + 后端能力。
 *
 * 2026-08-04：AI 对话移除插槽面板，统一为浮窗（唯一形态）。
 * 浮窗显隐 = aiVisible；位置/尺寸持久化到 localStorage（mbw_aiPos / mbw_aiSize）。
 */
import type { StateCreator } from 'zustand'
import type { Store, SlotState, ActiveView, ContentWidth } from './types'
import type { Capabilities } from '../api/client'
import { PANEL_DEFAULT_WIDTH } from '../lib/slotMetrics'
import { type LayoutPrefs, getSavedLayout, saveLayout } from '../lib/layout-prefs'
import { isPanelOpen, openPatch, nextActive } from '../lib/panelToggle'

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
  // 保留以下设置为兼容别名（自身零逻辑，只转调下面的统一入口）：
  toggleGenPanel: () => void
  setGenPanelOpen: (v: boolean) => void

  // ── 侧栏布局偏好（互斥切换 / 允许并排） ──
  layout: LayoutPrefs
  setLayout: (patch: Partial<LayoutPrefs>) => void
  /**
   * 互斥模式下当前展开的左侧面板 id（`null` = 全收起，只剩活动栏）。并排模式忽略它。
   *
   * ⚠️ 用**一个字段当选择器**，而不是「打开甲时遍历同侧把乙收起」——后者可被任何
   * 直接改单个 slot 的调用点绕过（Ctrl+K 搜索、右键菜单、面板自己的关闭按钮），
   * 是本仓库反复写守卫防的「多入口漂移」。选择器让「两个同时开」在结构上不可能发生，
   * 且 `uiSlice` 无需知道「左侧有哪些面板」→ 不必 import `slotRegistry` → 环不存在。
   */
  activeLeftPanel: string | null
  /**
   * 左侧面板的统一入口 —— 活动栏 / Ctrl+G / Ctrl+K / 右键菜单**共用这三个**。
   * 两处各写一遍必然漂移（一处忘了互斥、一处忘了宽度复位），同 `openDocLibSearch` 的既有教训。
   */
  openPanel: (id: string) => void
  closePanel: (id: string) => void
  togglePanel: (id: string) => void

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

  // ── 沉浸阅读（去噪容器：藏两侧面板 + 顶栏 + 预览工具条/TOC） ──
  // ⚠️ 进/出**不对称**：进入要过 processingTasks 守卫（与 Ctrl+G 同一判据），因为沉浸
  // 会让 SlotLayout 真卸载 GenPanel（hideSlots 是卸载，不是 CSS 隐藏）——正在跑的任务
  // 会变僵尸；退出绝不能设限，否则用户被困在沉浸里出不来。
  // 版式（--read-*）**不在这里管**：它常驻生效，与沉浸刻意解耦（见计划 §16.0）。
  // 不持久化 —— 沉浸是会话态，重启回到常规视图。
  immersive: boolean
  toggleImmersive: () => void
  setImmersive: (v: boolean) => void

  // ── 后端能力 ──
  capabilities: Capabilities | null
  setCapabilities: (c: Capabilities) => void

  // ── 文档库搜索聚焦信号（Ctrl+K / 顶栏搜索按钮） ──
  // ⚠️ 用**自增的 nonce** 而不是 boolean：触发器（App 快捷键、顶栏按钮）与接收方
  // （SearchField）之间没有父子关系，只能靠 store 传话。boolean 表达不了「已经是 true 时
  // 再按一次」—— 而用户第二次按 Ctrl+K 时依然预期「再聚焦一次」（比如刚点开筛选浮层、
  // 焦点被抢走之后）。nonce 自增则每次都是新值，effect 必然重跑。
  docLibSearchFocusNonce: number
  /** 只发聚焦信号（不展开面板）——一般用下面的 `openDocLibSearch` */
  focusDocLibSearch: () => void
  /**
   * 「打开文档库并聚焦搜索」—— Ctrl+K 与顶栏搜索按钮的**唯一**入口。
   * 两处各写一遍必然会漂移（一处展开了没聚焦、另一处聚焦了但面板还是折叠的）。
   */
  openDocLibSearch: () => void

  // ── 产物编辑中未保存标记 ──
  // PreviewArea 编辑态（editing + editDirty）的轻量桥：编辑内容归属文档 id。
  // 供 GenPanel「重新生成」入口检查「编辑中未保存 × 重新生成」拦截（评审遗留①）——
  // 重新生成会覆盖产物，但编辑框还开着旧值，保存会把旧编辑写回新产物。
  // 只暴露布尔/文档 id 标记，不提升编辑态（editValue 等）本身到 store。
  dirtyEditingDocId: string | null
  setDirtyEditingDocId: (docId: string | null) => void
}

/**
 * 文档库面板的默认宽度 —— 取自 [slotMetrics.ts](../lib/slotMetrics.ts) 的共享常量。
 *
 * ⚠️ 此前这里写的是「不能直接 import `slotRegistry` 的 `docLib.defaultWidth`（会成环），
 * 故同值重写」——那句判断**已过期**（slotRegistry 只 import `type FC` + lucide，是纯数据
 * 叶子；会成环的是「将来把组件 register 进去」的形态）。但**也不该**改成 import 注册表：
 * store 是注册表的下游（SlotLayout 读 store 拿宽度），反向读 SlotDef 会把两者焊死。
 * 正解是双方都引零依赖的常量模块 —— 与 `lib/reading-prefs.ts` 同一手法。
 */
const DOC_LIB_DEFAULT_WIDTH = PANEL_DEFAULT_WIDTH

/**
 * 互斥模式下默认展开的左侧面板 —— 用户拍板「文档库」（打开应用多半是看文档，
 * 「生成」是唯一导入入口，但它随时在活动栏上，点一下就有）。
 * 也用作「激活项失效时退回哪个」的兜底（见 `setLayout` 的归一化）。
 */
const DEFAULT_LEFT_PANEL = 'docLib'

/**
 * 初值**同源**：`layout` 与 `activeLeftPanel` 必须来自同一次读取。
 * 分两次 `getSavedLayout()` 不仅多解析一次 JSON，更糟的是两次之间理论上可以不同
 * —— 模式读出「互斥」而激活项按「并排」算，且这种不一致只在改过偏好的机器上出现。
 */
const INITIAL_LAYOUT = getSavedLayout()

/** 有任务在跑时拒绝进入沉浸 —— 与 App.tsx 里 Ctrl+G 的守卫同一语义（防 GenPanel 卸载致任务僵尸） */
const IMMERSIVE_BUSY_MSG = '操作进行中，请完成或取消后再进入沉浸阅读'

export const createUiSlice: StateCreator<Store, [], [], UiSlice> = (set, get) => ({
  activeView: 'polished',
  slots: {
    genPanel: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true },
    docLib:   { width: DOC_LIB_DEFAULT_WIDTH, collapsed: false, visible: true },
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
  // ── 侧栏布局偏好 ──
  layout: INITIAL_LAYOUT,
  setLayout: (patch) => set(s => {
    const next = { ...s.layout, ...patch }
    saveLayout(next)
    // 切回互斥时归一化激活项：只验「激活项**自己**」是否还有效（可见且未收起），
    // 不枚举「左侧有哪些面板」—— 枚举需要 `slotRegistry`，而它持有组件 → 成环。
    // 失效就退回默认面板，免得切过去是一片空。
    let active = s.activeLeftPanel
    if (next.leftPanelsExclusive) {
      const cur = active ? s.slots[active] : undefined
      if (!cur || !cur.visible || cur.collapsed) active = DEFAULT_LEFT_PANEL
    }
    return { layout: next, activeLeftPanel: active }
  }),
  activeLeftPanel: INITIAL_LAYOUT.leftPanelsExclusive ? DEFAULT_LEFT_PANEL : null,

  // ── 左侧面板的统一入口（活动栏 / Ctrl+G / Ctrl+K / 右键菜单共用） ──
  openPanel: (id) => set(s => {
    const slot = s.slots[id]
    if (!slot) return s
    return {
      // 并排模式不动 activeLeftPanel（那个字段只对互斥有意义）
      ...(s.layout.leftPanelsExclusive ? { activeLeftPanel: id } : {}),
      slots: { ...s.slots, [id]: { ...slot, ...openPatch(slot) } },
    }
  }),
  closePanel: (id) => set(s => {
    const slot = s.slots[id]
    if (!slot) return s
    if (s.layout.leftPanelsExclusive) {
      // 互斥下「关掉它」= 谁都不展开。**不去动别的面板** —— 若顺手把某个面板置成
      // collapsed，切回并排时它会莫名其妙是收起的。
      return s.activeLeftPanel === id ? { activeLeftPanel: null } : s
    }
    return { slots: { ...s.slots, [id]: { ...slot, collapsed: true } } }
  }),
  togglePanel: (id) => set(s => {
    const slot = s.slots[id]
    if (!slot) return s
    const exclusive = s.layout.leftPanelsExclusive
    const open = isPanelOpen(slot, exclusive, s.activeLeftPanel, id)
    if (exclusive) {
      // 互斥下「再按一次是关闭」而不是切到另一个面板（见 `nextActive` 的注释）
      return nextActive(exclusive, s.activeLeftPanel, id, open) === id
        ? { activeLeftPanel: id, slots: { ...s.slots, [id]: { ...slot, ...openPatch(slot) } } }
        : { activeLeftPanel: null }
    }
    return { slots: { ...s.slots, [id]: { ...slot, ...(open ? { collapsed: true } : openPatch(slot)) } } }
  }),

  // 兼容别名（保持旧 API 可用，避免大面积改 call site）—— 自身零逻辑，只转调统一入口
  toggleGenPanel: () => get().togglePanel('genPanel'),
  setGenPanelOpen: (v) => (v ? get().openPanel('genPanel') : get().closePanel('genPanel')),

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

  immersive: false,
  toggleImmersive: () => {
    const s = get()
    // 退出方向不检查 —— 进入才需要守卫（见 interface 上的注释）
    if (s.immersive) { set({ immersive: false }); return }
    if (Object.keys(s.processingTasks).length > 0) {
      s.addToast(IMMERSIVE_BUSY_MSG, 'info')
      return
    }
    set({ immersive: true })
  },
  setImmersive: (v) => {
    const s = get()
    if (v && Object.keys(s.processingTasks).length > 0) {
      s.addToast(IMMERSIVE_BUSY_MSG, 'info')
      return
    }
    set({ immersive: v })
  },

  capabilities: null,
  setCapabilities: (c) => set({ capabilities: c }),

  docLibSearchFocusNonce: 0,
  focusDocLibSearch: () => set(s => ({ docLibSearchFocusNonce: s.docLibSearchFocusNonce + 1 })),
  openDocLibSearch: () => {
    const s = get()
    // ⚠️ 这里只负责「打开 + 聚焦」两件事。宽度复位（用户把面板拖窄到 40px 时，
    // 只设 `collapsed: false` 会展开出**一条缝**而不是搜索框）已收进 `openPatch`
    // —— 全入口共用一份实现，在这里再写一遍就是第四份副本。
    // 互斥下「先收起别的面板」由 `openPanel` 的选择器接管，这里无需枚举左侧面板。
    s.openPanel('docLib')
    s.focusDocLibSearch()
  },

  dirtyEditingDocId: null,
  setDirtyEditingDocId: (docId) => set({ dirtyEditingDocId: docId }),
})
