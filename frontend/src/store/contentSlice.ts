/**
 * 预览内容 slice — 预览内容 + 流式生成 tick。
 * （streamTick 域仅 2 行，不值得独立 slice，并入此处）
 */
import type { StateCreator } from 'zustand'
import type { Store, MarkdownMeta, MarkdownVersion } from './types'

/**
 * 「哪一篇的哪个模式哪一版」→ 追问建议的键。
 * 写（`setVersionFollowUps`）与读（预览区）**必须**经这一个函数，手拼字符串
 * 迟早有一处少一个分隔符而静默落空。
 */
export const followUpsKey = (docId: string, mode: string, version: number) =>
  `${docId}::${mode}::${version}`

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
  /**
   * 追问建议的**晚到通道**（键见 `followUpsKey`）。
   *
   * 为什么不能只靠 `markdownMeta.followUps`：追问是**独立一次** LLM 调用，结果推在
   * `done` **之后**（几十秒）。而 `markdownMeta` 是**单槽** —— 它只装「当前 tab 那个
   * mode」的元数据。那一刻用户若切到了整理版/转录/思维导图 tab，或已经切走文档，
   * 那次写入要么被 mode 同步 effect 换成别的 mode 的值、要么被 `isSameDoc` 早退丢掉
   * ⇒ 卡片永久不出现。这正是「勾了追问却没有」的**偶发**来源（用户 2026-10-26 报告）。
   *
   * ⚠️ **必须带 version**：追问在后端是**按版本各存**的（`v{N}.followups.json`），
   * 不带版本会让「切到没有追问的旧版」错误地显示新版那一批。
   * ⚠️ 它**不是**第二真相源：渲染时优先用 `markdownMeta.followUps`（来自后端、随版本
   * 一起返回），这里只补「后端那份还没到、而事件已经到了」的那个窗口。
   * ⚠️ 不需要清理：键含 docId + version，重新生成会开新版本号，旧值自然不再被读到。
   */
  followUpsByVersion: Record<string, string[]>
  setVersionFollowUps: (docId: string, mode: string, version: number, questions: string[]) => void
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
  followUpsByVersion: {},
  setVersionFollowUps: (docId, mode, version, questions) => set(s => ({
    followUpsByVersion: { ...s.followUpsByVersion, [followUpsKey(docId, mode, version)]: questions },
  })),
  setCurrentContent: (content) => set({ currentContent: content }),
  setCurrentPolishedContent: (content) => set({ currentPolishedContent: content }),
  setCurrentRawText: (text) => set({ currentRawText: text }),
  setMarkdownMeta: (meta) => set({ markdownMeta: meta }),
  setMarkdownVersions: (versions) => set({ markdownVersions: versions }),
  setMarkdownEdited: (mode, edited) => set(s => ({ markdownEdited: { ...s.markdownEdited, [mode]: edited } })),

  streamTick: 0,
  bumpStreamTick: () => set(s => ({ streamTick: s.streamTick + 1 })),
})
