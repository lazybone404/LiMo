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
  /** 各产物模型的思考链（reasoning_content，截断 ~2000 字符）——与 progress 同构，
   *  并行双产物各存各的，ProcessingPlaceholder 按当前 tab 读取对应产物 */
  reasoning?: { notes?: string; polished?: string }
  /** 生成前置阶段提示（如「参考文件超出模型上下文，正在压缩…」）——单值不区分产物。
   *  setProcessing 是全量替换语义：正式 chunk 到达后不再携带本字段 → 提示自动消失 */
  stage?: string
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
  | 'polished' | 'summary' | 'transcript' | 'report'
  | 'data_preview' | 'original' | 'ocr'
  | 'extract' | 'table' | 'digitize' | 'understand'
  | 'mindmap'

export interface MarkdownMeta {
  id: string; style: string; mode: string; version: number; status?: string
  /** 已手动编辑标记（重新生成前确认用） */
  edited?: boolean
}

export interface MarkdownVersion {
  version: number; id: string; createdAt: string; status?: string; mode?: string
}

export interface ImageResult {
  text: string; engine: string; id: string
}

// ── Store 交集（各 slice 合并）──

export type Store = ThemeSlice & DocsSlice & ContentSlice & TasksSlice & ChatSlice & UiSlice & ToastSlice
