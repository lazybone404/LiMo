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
import type { UpdateSlice } from './updateSlice'
import type { JobsSlice } from './jobsSlice'

// ── 公开类型 ──

/**
 * 文档 / 文件夹条目 —— **定义在 `api/core.ts`**（它们是后端响应的形状），此处只做
 * re-export，不再保留第二份。
 *
 * ⚠️ 此前这里有一份独立定义，两份已经分叉过（本文件多 `sourceUrl` / `coverUrl` /
 * `modes`，`api/core.ts` 缺），而 `tsc` 不会因为「两个结构兼容的同名 interface」
 * 报错 —— 静默漂移只能在运行时看见。2026-11-07 合并，**加字段只改 `api/core.ts`**。
 */
export type { DocEntry, FolderEntry } from '../api/core'

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
  /**
   * 任务开始时间（epoch ms），UI 据此显示「已用时」。
   *
   * ⚠️ **不是 ETA** —— 生成 SSE 只有 `stage` 文本、模型自己也不知道会写多长，
   * 编一个剩余时间就是误导。能诚实给的只有「已经转了几分钟」。
   * ⚠️ 由 `setProcessing` 维护（唯一写入点），调用方不必传。
   */
  startedAt?: number
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
  /** 可选动作按钮的文案（如「撤销」）。与 `onAction` 成对出现 —— 只有文案没有
   *  回调是个点不动的按钮，只有回调没有文案是个空白按钮，两种都不渲染。 */
  actionLabel?: string
  /** 点动作按钮时执行。执行后 toast 自行消失（调用方不必再 `dismissToast`）。 */
  onAction?: () => void
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
  /** 纯正文字数（不含自证头）。⚠️ 读时由后端从文件头派生，不是 index 的持久字段 */
  chars?: number
  /** 生成用的模型名（版本列表的「来源」列） */
  source?: string
  /** 追问建议（预览区笔记末尾的可点 chip）。`undefined` = 这一版没有（不是空数组） */
  followUps?: string[]
  /** 这一版**磁盘字节**的 sha256（后端读盘时算）。编辑保存时原样带回，用于发现
   *  「你编辑期间别的程序改过这个文件」。`undefined` = 后端没给，保存不做校验 */
  diskHash?: string
}

export interface MarkdownVersion {
  version: number; id: string; createdAt: string; status?: string; mode?: string
  /** 纯正文字数（不含自证头）——展示用，`undefined` = 后端没给（不是 0 字） */
  chars?: number
  /** 该版由哪个模型生成 */
  source?: string
  /** 生成该版所用的提示词版本；`undefined` = 历史产物读不出自证头（不是第 0 版） */
  promptVersion?: string
  /** 该版被人工编辑过（编辑就地覆盖，不产生新版本） */
  edited?: boolean
}

export interface ImageResult {
  text: string; engine: string; id: string
}

// ── 跨 slice 编排（实现在 store/index.ts 组合层）──

/**
 * 单个 slice 内无法表达的 action —— 组合层是唯一被允许跨 slice 读写的位置。
 * （slice 的 action 零跨域引用是本仓库的约定，新增跨域动作放这里，不要在 slice 里
 *   `get()` 别的 slice 的字段。）
 */
export interface AppSlice {
  /**
   * 删除文档后清理它的产物残留：预览内容 / 图片处理结果 / 流式缓冲。
   *
   * ⚠️ 不清理的后果不是报错，而是**下一个文档看到上一个文档的残留**：删掉 A、再打开 B，
   * B 的内容还没加载完的那一瞬，预览区先显示 A 的正文。流式缓冲尤其隐蔽——它根本不在
   * store 里（`store/streamBuffers.ts` 是模块级 Map），漏清就永久驻留到刷新页面为止。
   */
  purgeDocArtifacts: (ids: string[]) => void

  /**
   * 正文字号缩放的下一档 / 重置。**放在组合层而非 themeSlice**：它要在改值之后发一条
   * toast，而 slice 的 action 不允许跨域读写别的 slice（那是本仓库的约定）。
   *
   * ⚠️ 键盘（`App.handleAppKeyDown`）与预览区滚轮两条入口**必须共用这一对**，
   * 各写一份必然漂移（一个带 toast、一个不带；或档位取整规则不同）。
   */
  stepFontScale: (dir: 1 | -1) => void
  resetFontScale: () => void
}

// ── Store 交集（各 slice 合并）──

export type Store = ThemeSlice & DocsSlice & ContentSlice & TasksSlice & ChatSlice & UiSlice & ToastSlice & UpdateSlice & JobsSlice & AppSlice
