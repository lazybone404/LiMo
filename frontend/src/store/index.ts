import { create } from 'zustand'
import { type ThemeMode, getSavedTheme, resolveTheme, applyTheme, saveTheme } from '../lib/theme'
import { type AccentColor, getSavedAccent, applyAccent, saveAccent } from '../lib/accent'
import { type TexturePreset, type FontPreset, getSavedTexture, getSavedFont, applyTexture, applyFont, saveTexture, saveFont } from '../lib/texture'
import { type BasePreset, getSavedBase, applyBase, saveBase } from '../lib/theme-base'
import { THEME_PRESETS } from '../lib/theme-presets'
import type { Capabilities } from '../api/client'

// ── 导出类型 ──

export interface DocEntry {
  id: string; title: string; sourceType: string; sourceFileName?: string; sourceUrl?: string;
  engine: string; language?: string; duration?: number; coverUrl?: string; contentHash: string;
  tags: string[]; createdAt: string;
}

export interface TaskInfo {
  kind: string
  mode?: string
  title?: string
  status?: 'running' | 'failed'
  error?: string  // 失败原因，保留 5 秒后自动清除
}

export interface ProgressEvent {
  phase: string; status: string; percent?: number; message?: string;
}

export type { Capabilities } from '../api/client'

export type ContentWidth = 'narrow' | 'normal' | 'wide'

export interface SlotState {
  width: number
  collapsed: boolean
  visible: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  id: string
}

export interface ToastItem {
  id: number; message: string; type: 'success' | 'error' | 'info'
}

// ── Store 接口（所有字段按域组织）──

interface Store {
  // ── 文档库 ──
  docs: DocEntry[]
  currentDocId: string | null
  searchQuery: string
  activeTag: string
  setDocs: (docs: DocEntry[]) => void
  addDoc: (doc: DocEntry) => void
  selectDoc: (id: string) => void
  setSearchQuery: (q: string) => void
  setActiveTag: (tag: string) => void

  // ── 预览内容 ──
  currentContent: string | null
  currentPolishedContent: string | null
  currentRawText: string | null
  markdownMeta: { id: string; style: string; mode: string; version: number; status?: string } | null
  markdownVersions: { version: number; id: string; createdAt: string; status?: string; mode?: string }[]
  setCurrentContent: (content: string | null) => void
  setCurrentPolishedContent: (content: string | null) => void
  setCurrentRawText: (text: string | null) => void
  setMarkdownMeta: (meta: { id: string; style: string; mode: string; version: number; status?: string } | null) => void
  setMarkdownVersions: (versions: { version: number; id: string; createdAt: string; status?: string }[]) => void

  // ── AI 审核 ──
  currentFactCheckContent: string | null
  factCheckMeta: {
    id: string; verified_count?: number; disputed_count?: number; unverifiable_count?: number
    has_content?: boolean
    source_mode?: string; source_version?: number; verified_at?: string
  } | null
  factCheckProgress: {
    phase: string; text: string
    summary?: { claims_count: number; facts: number; data: number; opinion: number }
    issues?: { type: string; text: string; detail: string; action?: string }[]
  } | null
  setCurrentFactCheckContent: (c: string | null) => void
  setFactCheckMeta: (m: Store['factCheckMeta']) => void
  setFactCheckProgress: (p: Store['factCheckProgress']) => void

  // ── 流式生成（缓冲区见 store/streamBuffers.ts）──
  streamTick: number
  bumpStreamTick: () => void

  // ── 导入进度 ──
  importProgress: ProgressEvent | null
  importStages: { phase: string; status: 'done' | 'running' | 'pending' | 'error'; message: string }[]
  setImportProgress: (ev: ProgressEvent | null) => void
  setImportStages: (stages: { phase: string; status: 'done' | 'running' | 'pending' | 'error'; message: string }[]) => void

  // ── 图片处理结果: docId → mode → { text, engine, id } ──
  imageResults: Record<string, Record<string, { text: string; engine: string; id: string }>>
  setImageResult: (docId: string, mode: string, result: { text: string; engine: string; id: string }) => void
  loadImageResults: (docId: string, results: { mode: string; text: string; engine: string; id: string }[]) => void

  // ── 处理任务追踪 ──
  processingTasks: Record<string, TaskInfo>
  /** taskKey = `${docId}__${task.kind}` 或裸 docId（导入） */
  setProcessing: (taskKey: string, task: TaskInfo | null) => void
  /** 将任务标记为失败并保留 error 信息 */
  failTask: (taskKey: string, error: string) => void
  clearDocTasks: (docId: string) => void
  cancelOneTask: (taskKey: string) => void

  // ── 任务面板 ──
  taskPanelOpen: boolean
  setTaskPanelOpen: (v: boolean) => void

  // ── 对话管理 ──
  conversations: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }[]
  currentConversationId: string | null
  setConversations: (list: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }[]) => void
  addConversation: (conv: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }) => void
  removeConversation: (id: string) => void
  renameConversation: (id: string, title: string) => void
  setCurrentConversationId: (id: string | null) => void

  // ── 生成状态 ──
  generating: boolean
  setGenerating: (v: boolean) => void
  factChecking: boolean
  setFactChecking: (v: boolean) => void

  // ── AI 浮动对话 ──
  chatMessages: ChatMessage[]
  chatLoading: boolean
  aiFloating: boolean
  aiFloatingPos: { x: number; y: number }
  aiFloatingSize: { width: number; height: number }
  addChatMessage: (msg: ChatMessage) => void
  setChatMessages: (msgs: ChatMessage[]) => void
  setChatLoading: (v: boolean) => void
  setAiFloating: (v: boolean) => void
  setAiFloatingPos: (pos: { x: number; y: number }) => void
  setAiFloatingSize: (size: { width: number; height: number }) => void

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
  activeView: 'polished' | 'summary' | 'transcript' | 'skeleton' | 'report' | 'data_preview' | 'original' | 'ocr' | 'extract' | 'table' | 'digitize' | 'understand' | 'mindmap' | 'fact_check'
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

  // ── 主题 ──
  theme: ThemeMode
  resolvedTheme: 'light' | 'dark'
  setTheme: (t: ThemeMode) => void

  // ── 主色调 ──
  accent: AccentColor
  setAccent: (a: AccentColor) => void

  // ── 预览区纹理 ──
  texture: TexturePreset
  setTexture: (t: TexturePreset) => void

  // ── 阅读字体 ──
  font: FontPreset
  setFont: (f: FontPreset) => void

  // ── 界面基调 ──
  base: BasePreset
  setBase: (b: BasePreset) => void

  // ── 一键主题包 ──
  applyPreset: (name: string) => void

  // ── Toast ──
  toasts: ToastItem[]
  addToast: (message: string, type?: 'success' | 'error' | 'info') => void
  dismissToast: (id: number) => void
}

// ── 模块级状态 ──

let msgCounter = 0

// ── Store 创建（按域分节，便于导航）──

export const useStore = create<Store>((set) => ({
  // ═══════════════════════════════════════════
  // 文档库
  // ═══════════════════════════════════════════
  docs: [], currentDocId: null, searchQuery: '', activeTag: '',
  setDocs: (docs) => set({ docs }),
  addDoc: (doc) => set(s => ({ docs: [doc, ...s.docs] })),
  selectDoc: (id) => { try { localStorage.setItem('mbw_lastDocId', id) } catch { /* quota exceeded */ } set({ currentDocId: id }) },
  setSearchQuery: (q) => set({ searchQuery: q }),
  setActiveTag: (tag) => set({ activeTag: tag }),

  // ═══════════════════════════════════════════
  // 预览内容
  // ═══════════════════════════════════════════
  currentContent: null, currentPolishedContent: null, currentRawText: null, markdownMeta: null, markdownVersions: [],
  setCurrentContent: (content) => set({ currentContent: content }),
  setCurrentPolishedContent: (content) => set({ currentPolishedContent: content }),
  setCurrentRawText: (text) => set({ currentRawText: text }),
  setMarkdownMeta: (meta) => set({ markdownMeta: meta }),
  setMarkdownVersions: (versions) => set({ markdownVersions: versions }),

  // ═══════════════════════════════════════════
  // AI 审核
  // ═══════════════════════════════════════════
  currentFactCheckContent: null, factCheckMeta: null, factCheckProgress: null,
  setCurrentFactCheckContent: (c) => set({ currentFactCheckContent: c }),
  setFactCheckMeta: (m) => set({ factCheckMeta: m }),
  setFactCheckProgress: (p) => set({ factCheckProgress: p }),

  // ═══════════════════════════════════════════
  // 流式生成（缓冲区见 store/streamBuffers.ts）
  // ═══════════════════════════════════════════
  streamTick: 0,
  bumpStreamTick: () => set(s => ({ streamTick: s.streamTick + 1 })),

  // ═══════════════════════════════════════════
  // 导入进度
  // ═══════════════════════════════════════════
  importProgress: null,
  importStages: [],
  setImportProgress: (ev) => set(s => {
    if (!ev) return { importProgress: null, importStages: [] }
    const stages = [...s.importStages]
    const existingIdx = stages.findIndex(st => st.phase === ev.phase)
    const stage = {
      phase: ev.phase,
      status: (ev.status === 'error' ? 'error' : ev.status === 'done' ? 'done' : ev.status === 'running' ? 'running' : 'pending') as 'done' | 'running' | 'pending' | 'error',
      message: ev.message || ev.phase,
    }
    if (existingIdx >= 0) stages[existingIdx] = stage
    else stages.push(stage)
    return { importProgress: ev, importStages: stages }
  }),
  setImportStages: (stages) => set({ importStages: stages }),

  // ═══════════════════════════════════════════
  // 图片处理结果
  // ═══════════════════════════════════════════
  imageResults: {},
  setImageResult: (docId, mode, result) => set(s => ({
    imageResults: {
      ...s.imageResults,
      [docId]: { ...(s.imageResults[docId] || {}), [mode]: result },
    },
  })),
  loadImageResults: (docId, results) => set(s => {
    const byMode: Record<string, { text: string; engine: string; id: string }> = {}
    for (const r of results) {
      byMode[r.mode] = { text: r.text, engine: r.engine, id: r.id }
    }
    return { imageResults: { ...s.imageResults, [docId]: byMode } }
  }),

  // ═══════════════════════════════════════════
  // 处理任务追踪 + 任务面板
  // ═══════════════════════════════════════════
  processingTasks: {},
  setProcessing: (taskKey, task) => set(s => {
    const tasks = { ...s.processingTasks }
    if (task) tasks[taskKey] = { status: 'running', ...task, title: task.title || '' }
    else {
      Object.keys(tasks).forEach(k => {
        if (k.startsWith(taskKey + '__') || k === taskKey) delete tasks[k]
      })
    }
    return { processingTasks: tasks }
  }),
  /** 将任务标记为失败，5 秒后自动清除 */
  failTask: (taskKey, error) => set(s => {
    const tasks = { ...s.processingTasks }
    if (tasks[taskKey]) {
      tasks[taskKey] = { ...tasks[taskKey], status: 'failed', error }
    }
    // 5 秒后自动清除
    setTimeout(() => {
      set(s2 => {
        const t = { ...s2.processingTasks }
        if (t[taskKey]?.status === 'failed') delete t[taskKey]
        return { processingTasks: t }
      })
    }, 5000)
    return { processingTasks: tasks }
  }),
  clearDocTasks: (docId) => set(s => {
    const tasks = { ...s.processingTasks }
    const prefix = docId + '__'
    Object.keys(tasks).forEach(k => {
      if (k === docId || k.startsWith(prefix)) delete tasks[k]
    })
    return { processingTasks: tasks }
  }),
  /** 取消单个任务（精确 key 匹配，用于 TaskPanel 逐任务取消） */
  cancelOneTask: (taskKey) => set(s => {
    const tasks = { ...s.processingTasks }
    delete tasks[taskKey]
    return { processingTasks: tasks }
  }),

  taskPanelOpen: false,
  setTaskPanelOpen: (v) => set({ taskPanelOpen: v }),

  // ═══════════════════════════════════════════
  // 对话管理
  // ═══════════════════════════════════════════
  conversations: [], currentConversationId: null,
  setConversations: (list) => set({ conversations: list }),
  addConversation: (conv) => set(s => ({ conversations: [conv, ...s.conversations] })),
  removeConversation: (id) => set(s => ({ conversations: s.conversations.filter(c => c.id !== id), currentConversationId: s.currentConversationId === id ? null : s.currentConversationId })),
  renameConversation: (id, title) => set(s => ({ conversations: s.conversations.map(c => c.id === id ? { ...c, title } : c) })),
  setCurrentConversationId: (id) => { if (id) try { localStorage.setItem('mbw_lastConvId', id) } catch {/*quota*/} else try { localStorage.removeItem('mbw_lastConvId') } catch {/*quota*/}; set({ currentConversationId: id }) },

  // ═══════════════════════════════════════════
  // 生成状态
  // ═══════════════════════════════════════════
  generating: false,
  setGenerating: (v) => set({ generating: v }),
  factChecking: false,
  setFactChecking: (v) => set({ factChecking: v }),

  // ═══════════════════════════════════════════
  // AI 浮动对话
  // ═══════════════════════════════════════════
  chatMessages: [], chatLoading: false,
  aiFloating: false,
  aiFloatingPos: { x: window.innerWidth - 500, y: window.innerHeight - 560 },
  aiFloatingSize: { width: 480, height: 540 },
  addChatMessage: (msg) => set(s => ({ chatMessages: [...s.chatMessages, msg] })),
  setChatMessages: (msgs) => set({ chatMessages: msgs }),
  setChatLoading: (v) => set({ chatLoading: v }),
  setAiFloating: (v) => set({ aiFloating: v }),
  setAiFloatingPos: (pos) => set({ aiFloatingPos: pos }),
  setAiFloatingSize: (size) => set({ aiFloatingSize: size }),

  // ═══════════════════════════════════════════
  // UI 布局（插槽系统 — Phase 2）
  // ═══════════════════════════════════════════
  activeView: 'polished',
  slots: {
    genPanel: { width: 260, collapsed: false, visible: true },
    docLib:   { width: 260, collapsed: false, visible: true },
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
  setActiveView: (v) => { try { localStorage.setItem('mbw_lastView', v) } catch { /* quota exceeded */ } set({ activeView: v as Store['activeView'] }) },

  // ═══════════════════════════════════════════
  // 内容阅读宽度
  // ═══════════════════════════════════════════
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

  // ═══════════════════════════════════════════
  // 后端能力
  // ═══════════════════════════════════════════
  capabilities: null,
  setCapabilities: (c) => set({ capabilities: c }),

  // ═══════════════════════════════════════════
  // 主题六轴
  // ═══════════════════════════════════════════
  theme: getSavedTheme(),
  resolvedTheme: resolveTheme(getSavedTheme()),
  setTheme: (mode) => {
    saveTheme(mode)
    const resolved = resolveTheme(mode)
    applyTheme(resolved)
    set({ theme: mode, resolvedTheme: resolved })
  },

  accent: getSavedAccent(),
  setAccent: (accent) => {
    saveAccent(accent)
    applyAccent(accent)
    set({ accent })
  },

  texture: getSavedTexture(),
  setTexture: (texture) => {
    saveTexture(texture)
    applyTexture(texture)
    set({ texture })
  },

  font: getSavedFont(),
  setFont: (font) => {
    saveFont(font)
    applyFont(font)
    set({ font })
  },

  base: getSavedBase(),
  setBase: (base) => {
    saveBase(base)
    applyBase(base)
    set({ base })
  },

  applyPreset: (name) => {
    const p = THEME_PRESETS.find(pr => pr.name === name)
    if (!p) return
    applyBase(p.base); saveBase(p.base)
    applyAccent(p.accent); saveAccent(p.accent)
    applyTexture(p.texture); saveTexture(p.texture)
    applyFont(p.font); saveFont(p.font)
    set({ base: p.base, accent: p.accent, texture: p.texture, font: p.font })
  },

  // ═══════════════════════════════════════════
  // Toast
  // ═══════════════════════════════════════════
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
}))

// Apply initial theme on store creation
applyTheme(resolveTheme(getSavedTheme()))
applyAccent(getSavedAccent())
applyTexture(getSavedTexture())
applyFont(getSavedFont())
applyBase(getSavedBase())

// Listen for system theme changes when in "system" mode
const sysMq = window.matchMedia('(prefers-color-scheme: dark)')
sysMq.addEventListener('change', () => {
  const s = useStore.getState()
  if (s.theme === 'system') {
    const r = sysMq.matches ? 'dark' : 'light'
    applyTheme(r)
    useStore.setState({ resolvedTheme: r })
  }
})

export function nextMsgId() { return `msg_${++msgCounter}_${Date.now()}` }
