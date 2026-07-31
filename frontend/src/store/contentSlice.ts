/**
 * 预览内容 slice — 预览内容 + AI 审核 + 流式生成 tick。
 * （streamTick 域仅 2 行，不值得独立 slice，并入此处）
 */
import type { StateCreator } from 'zustand'
import type { Store, MarkdownMeta, MarkdownVersion, FactCheckMeta, FactCheckProgress } from './types'

export interface ContentSlice {
  // ── 预览内容 ──
  currentContent: string | null
  currentPolishedContent: string | null
  currentRawText: string | null
  markdownMeta: MarkdownMeta | null
  markdownVersions: MarkdownVersion[]
  setCurrentContent: (content: string | null) => void
  setCurrentPolishedContent: (content: string | null) => void
  setCurrentRawText: (text: string | null) => void
  setMarkdownMeta: (meta: MarkdownMeta | null) => void
  setMarkdownVersions: (versions: MarkdownVersion[]) => void

  // ── AI 审核 ──
  currentFactCheckContent: string | null
  factCheckMeta: FactCheckMeta | null
  factCheckProgress: FactCheckProgress | null
  setCurrentFactCheckContent: (c: string | null) => void
  setFactCheckMeta: (m: FactCheckMeta | null) => void
  setFactCheckProgress: (p: FactCheckProgress | null) => void

  // ── 流式生成（缓冲区见 store/streamBuffers.ts）──
  streamTick: number
  bumpStreamTick: () => void
}

export const createContentSlice: StateCreator<Store, [], [], ContentSlice> = (set) => ({
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

  streamTick: 0,
  bumpStreamTick: () => set(s => ({ streamTick: s.streamTick + 1 })),
})
