/**
 * store 共享类型 — 公开类型 + 具名类型 + Store 交集。
 *
 * 注意：types.ts 与各 slice 文件之间是 type-only 循环引用（slice 用 StateCreator<Store>，
 * types 用 ThemeSlice 等），TS 编译期解析、bundler 擦除，零运行时依赖，安全。
 */

import type { ThemeSlice } from './themeSlice'
import type { DocsSlice } from './docsSlice'
import type { ContentSlice } from './contentSlice'
import type { TasksSlice } from './tasksSlice'
import type { ChatSlice } from './chatSlice'
import type { UiSlice } from './uiSlice'
import type { ToastSlice } from './toastSlice'

// ── 公开类型 ──

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
  /** 生成任务：各产物已生成字符数（进度显示） */
  progress?: { notes?: number; polished?: number }
}

export interface ProgressEvent {
  phase: string; status: string; percent?: number; message?: string;
}

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

// ── 具名类型（原 Store 接口内联类型抽离，解除 Store['xxx'] 自引用）──

export type ActiveView =
  | 'polished' | 'summary' | 'transcript' | 'skeleton' | 'report'
  | 'data_preview' | 'original' | 'ocr'
  | 'extract' | 'table' | 'digitize' | 'understand'
  | 'mindmap' | 'fact_check'

export interface MarkdownMeta {
  id: string; style: string; mode: string; version: number; status?: string
}

export interface MarkdownVersion {
  version: number; id: string; createdAt: string; status?: string; mode?: string
}

export interface FactCheckMeta {
  id: string; verified_count?: number; disputed_count?: number; unverifiable_count?: number
  has_content?: boolean
  source_mode?: string; source_version?: number; verified_at?: string
}

export interface FactCheckProgress {
  phase: string; text: string
  summary?: { claims_count: number; facts: number; data: number; opinion: number }
  issues?: { type: string; text: string; detail: string; action?: string }[]
}

export interface ImportStage {
  phase: string; status: 'done' | 'running' | 'pending' | 'error'; message: string
}

export interface ImageResult {
  text: string; engine: string; id: string
}

export interface Conversation {
  id: string; title: string; model_id?: number | null; created_at?: string; updated_at?: string
}

// ── Store 交集（各 slice 合并）──

export type Store = ThemeSlice & DocsSlice & ContentSlice & TasksSlice & ChatSlice & UiSlice & ToastSlice
