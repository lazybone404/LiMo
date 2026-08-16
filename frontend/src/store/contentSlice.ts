/**
 * 预览内容 slice — 预览内容 + 流式生成 tick。
 * （streamTick 域仅 2 行，不值得独立 slice，并入此处）
 */
import type { StateCreator } from 'zustand'
import type { Store, MarkdownMeta, MarkdownVersion } from './types'

export interface ContentSlice {
  // ── 预览内容 ──
  currentContent: string | null
  currentPolishedContent: string | null
  currentRawText: string | null
  markdownMeta: MarkdownMeta | null
  markdownVersions: MarkdownVersion[]
  /** 两产物的「已手动编辑」标记（独立于 markdownMeta——后者只存当前 tab 的；
   *  重新生成确认需同时知道 notes/polished 两状态） */
  markdownEdited: { notes: boolean; polished: boolean }
  setCurrentContent: (content: string | null) => void
  setCurrentPolishedContent: (content: string | null) => void
  setCurrentRawText: (text: string | null) => void
  setMarkdownMeta: (meta: MarkdownMeta | null) => void
  setMarkdownVersions: (versions: MarkdownVersion[]) => void
  setMarkdownEdited: (mode: 'notes' | 'polished', edited: boolean) => void

  // ── 流式生成（缓冲区见 store/streamBuffers.ts）──
  streamTick: number
  bumpStreamTick: () => void
}

export const createContentSlice: StateCreator<Store, [], [], ContentSlice> = (set) => ({
  currentContent: null, currentPolishedContent: null, currentRawText: null, markdownMeta: null, markdownVersions: [],
  markdownEdited: { notes: false, polished: false },
  setCurrentContent: (content) => set({ currentContent: content }),
  setCurrentPolishedContent: (content) => set({ currentPolishedContent: content }),
  setCurrentRawText: (text) => set({ currentRawText: text }),
  setMarkdownMeta: (meta) => set({ markdownMeta: meta }),
  setMarkdownVersions: (versions) => set({ markdownVersions: versions }),
  setMarkdownEdited: (mode, edited) => set(s => ({ markdownEdited: { ...s.markdownEdited, [mode]: edited } })),

  streamTick: 0,
  bumpStreamTick: () => set(s => ({ streamTick: s.streamTick + 1 })),
})
