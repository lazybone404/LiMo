import { create } from 'zustand'
import { type ThemeMode, getSavedTheme, resolveTheme, applyTheme, saveTheme } from '../lib/theme'
import { type AccentColor, getSavedAccent, applyAccent, saveAccent } from '../lib/accent'
import { type TexturePreset, type FontPreset, getSavedTexture, getSavedFont, applyTexture, applyFont, saveTexture, saveFont } from '../lib/texture'
import { type BasePreset, getSavedBase, applyBase, saveBase } from '../lib/theme-base'
import { THEME_PRESETS } from '../lib/theme-presets'
import type { Capabilities } from '../api/client'

export interface DocEntry {
  id: string; title: string; sourceType: string; sourceFileName?: string; sourceUrl?: string;
  engine: string; language?: string; duration?: number; coverUrl?: string; contentHash: string;
  tags: string[]; createdAt: string;
}

export interface ProgressEvent {
  phase: string; status: string; percent?: number; message?: string;
}

export type { Capabilities } from '../api/client'

export type ContentWidth = 'narrow' | 'normal' | 'wide'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  id: string
}

interface Store {
  // 文档库
  docs: DocEntry[]
  currentDocId: string | null
  searchQuery: string
  activeTag: string
  setDocs: (docs: DocEntry[]) => void
  addDoc: (doc: DocEntry) => void
  selectDoc: (id: string) => void
  setSearchQuery: (q: string) => void
  setActiveTag: (tag: string) => void

  // 预览区
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

  // 事实验证 / AI 审核
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

  // 流式生成
  streamingContent: string | null       // 当前正在流式生成的内容
  isStreaming: boolean                  // 是否正在流式生成
  setStreamingContent: (c: string | null) => void
  setIsStreaming: (v: boolean) => void

  // 导入进度
  importProgress: ProgressEvent | null
  importStages: { phase: string; status: 'done' | 'running' | 'pending' | 'error'; message: string }[]
  setImportProgress: (ev: ProgressEvent | null) => void
  setImportStages: (stages: { phase: string; status: 'done' | 'running' | 'pending' | 'error'; message: string }[]) => void

  // 图片处理结果: docId → mode → { text, engine, id }
  imageResults: Record<string, Record<string, { text: string; engine: string; id: string }>>
  setImageResult: (docId: string, mode: string, result: { text: string; engine: string; id: string }) => void
  loadImageResults: (docId: string, results: { mode: string; text: string; engine: string; id: string }[]) => void

  // 处理任务追踪（供 PreviewArea 渲染进度 + TaskPanel 展示）
  processingTasks: Record<string, { kind: string; mode?: string; title: string }>
  /** key = `${docId}__${kind}`, null task = 清除该文档所有任务 */
  setProcessing: (taskKey: string, task: { kind: string; mode?: string; title?: string } | null) => void
  /** 清除指定文档的所有处理任务 */
  clearDocTasks: (docId: string) => void

  // 任务面板
  taskPanelOpen: boolean
  setTaskPanelOpen: (v: boolean) => void

  // 对话管理
  conversations: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }[]
  currentConversationId: string | null
  setConversations: (list: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }[]) => void
  addConversation: (conv: { id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string }) => void
  removeConversation: (id: string) => void
  renameConversation: (id: string, title: string) => void
  setCurrentConversationId: (id: string | null) => void

  // 生成中
  generating: boolean
  setGenerating: (v: boolean) => void
  // 事实验证中
  factChecking: boolean
  setFactChecking: (v: boolean) => void

  // AI 对话
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

  // UI 布局
  genPanelOpen: boolean
  aiVisible: boolean
  activeView: 'polished' | 'summary' | 'transcript' | 'skeleton' | 'report' | 'data_preview' | 'original' | 'ocr' | 'extract' | 'table' | 'digitize' | 'understand' | 'mindmap' | 'fact_check'
  panelWidths: { genPanel: number; docLib: number; aiPanel: number; toc: number }
  panelsCollapsed: { genPanel: boolean; docLib: boolean; aiPanel: boolean; toc: boolean }
  toggleGenPanel: () => void
  toggleAI: () => void
  setGenPanelOpen: (v: boolean) => void
  setAiVisible: (v: boolean) => void
  setActiveView: (v: string) => void
  setPanelWidth: (panel: string, w: number) => void
  setPanelCollapsed: (panel: string, v: boolean) => void

  // 内容阅读宽度
  contentWidth: ContentWidth
  cycleContentWidth: () => void

  // 能力
  capabilities: Capabilities | null
  setCapabilities: (c: Capabilities) => void

  // 主题
  theme: ThemeMode
  resolvedTheme: 'light' | 'dark'
  setTheme: (t: ThemeMode) => void

  // 主色调
  accent: AccentColor
  setAccent: (a: AccentColor) => void

  // 预览区纹理
  texture: TexturePreset
  setTexture: (t: TexturePreset) => void

  // 阅读字体
  font: FontPreset
  setFont: (f: FontPreset) => void

  // 界面基调
  base: BasePreset
  setBase: (b: BasePreset) => void

  // 一键主题包
  applyPreset: (name: string) => void

  // Toast
  toasts: { id: number; message: string; type: 'success' | 'error' | 'info' }[]
  addToast: (message: string, type?: 'success' | 'error' | 'info') => void
  dismissToast: (id: number) => void
}

export interface ToastItem { id: number; message: string; type: 'success' | 'error' | 'info' }

let msgCounter = 0

export const useStore = create<Store>((set) => ({
  docs: [], currentDocId: null, searchQuery: '', activeTag: '',
  setDocs: (docs) => set({ docs }),
  addDoc: (doc) => set(s => ({ docs: [doc, ...s.docs] })),
  selectDoc: (id) => { try { localStorage.setItem('mbw_lastDocId', id) } catch { /* quota exceeded */ } set({ currentDocId: id }) },
  setActiveView: (v) => { try { localStorage.setItem('mbw_lastView', v) } catch { /* quota exceeded */ } set({ activeView: v as Store['activeView'] }) },
  setSearchQuery: (q) => set({ searchQuery: q }),
  setActiveTag: (tag) => set({ activeTag: tag }),

  currentContent: null, currentPolishedContent: null, currentRawText: null, markdownMeta: null, markdownVersions: [],
  setCurrentContent: (content) => set({ currentContent: content }),
  setCurrentPolishedContent: (content) => set({ currentPolishedContent: content }),
  setCurrentRawText: (text) => set({ currentRawText: text }),
  setMarkdownMeta: (meta) => set({ markdownMeta: meta }),
  setMarkdownVersions: (versions) => set({ markdownVersions: versions }),

  currentFactCheckContent: null, factCheckMeta: null, factCheckProgress: null,
  setCurrentFactCheckContent: (c) => set({ currentFactCheckContent: c }),
  setFactCheckMeta: (m) => set({ factCheckMeta: m }),
  setFactCheckProgress: (p) => set({ factCheckProgress: p }),

  streamingContent: null, isStreaming: false,
  setStreamingContent: (c) => set({ streamingContent: c }),
  setIsStreaming: (v) => set({ isStreaming: v }),

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

  processingTasks: {},
  /** taskKey = `${docId}__${task.kind}` — 同一文档的不同操作不会互相覆盖 */
  setProcessing: (taskKey, task) => set(s => {
    const tasks = { ...s.processingTasks }
    if (task) tasks[taskKey] = { ...task, title: task.title || '' }
    else {
      // null → 清除该文档该类型的所有任务（匹配前缀）
      Object.keys(tasks).forEach(k => {
        if (k.startsWith(taskKey + '__') || k === taskKey) delete tasks[k]
      })
    }
    return { processingTasks: tasks }
  }),
  /** 清除指定文档的所有处理任务 */
  clearDocTasks: (docId) => set(s => {
    const tasks = { ...s.processingTasks }
    const prefix = docId + '__'
    Object.keys(tasks).forEach(k => {
      if (k === docId || k.startsWith(prefix)) delete tasks[k]
    })
    return { processingTasks: tasks }
  }),

  taskPanelOpen: false,
  setTaskPanelOpen: (v) => set({ taskPanelOpen: v }),

  conversations: [], currentConversationId: null,
  setConversations: (list) => set({ conversations: list }),
  addConversation: (conv) => set(s => ({ conversations: [conv, ...s.conversations] })),
  removeConversation: (id) => set(s => ({ conversations: s.conversations.filter(c => c.id !== id), currentConversationId: s.currentConversationId === id ? null : s.currentConversationId })),
  renameConversation: (id, title) => set(s => ({ conversations: s.conversations.map(c => c.id === id ? { ...c, title } : c) })),
  setCurrentConversationId: (id) => { if (id) try { localStorage.setItem('mbw_lastConvId', id) } catch {/*quota*/} else try { localStorage.removeItem('mbw_lastConvId') } catch {/*quota*/}; set({ currentConversationId: id }) },

  generating: false,
  setGenerating: (v) => set({ generating: v }),
  factChecking: false,
  setFactChecking: (v) => set({ factChecking: v }),

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

  genPanelOpen: true, aiVisible: false, activeView: 'polished',
  panelWidths: { genPanel: 260, docLib: 260, aiPanel: 340, toc: 160 },
  panelsCollapsed: { genPanel: false, docLib: false, aiPanel: false, toc: false },
  toggleGenPanel: () => set(s => ({ genPanelOpen: !s.genPanelOpen })),
  toggleAI: () => set(s => ({ aiVisible: !s.aiVisible })),
  setGenPanelOpen: (v) => set({ genPanelOpen: v }),
  setAiVisible: (v) => set({ aiVisible: v }),
  setPanelWidth: (panel, w) => set(s => ({
    panelWidths: { ...s.panelWidths, [panel]: w },
    panelsCollapsed: { ...s.panelsCollapsed, [panel]: w < 80 },
  })),
  setPanelCollapsed: (panel, v) => set(s => ({
    panelsCollapsed: { ...s.panelsCollapsed, [panel]: v },
  })),

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

  // 主题
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

  toasts: [],
  addToast: (message, type = 'success') => {
    const id = ++msgCounter
    // M6: 错误提示 6 秒，成功 3 秒，最多保留 5 条
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
