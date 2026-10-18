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
  /** 所属文件夹 id；"" = 未分类 */
  folderId?: string;
  /**
   * 合成文档的来源（`sourceType === 'composite'` 时非空）。
   * ⚠️ 只有这三项 + metadataCorrupt 能判定「这份综述引用了哪几篇」——标题是**展示字段**，
   * 重名与改名都会让它失效（设计 §4.2.2）。
   */
  sourceDocIds?: string[];
  sourceTitles?: string[];
  /** 各源 rawText 的 sha256 前 8 位。⚠️ 比 id 更可靠：id 由内容派生，重建索引会重推（§4.2.2） */
  sourceHashes?: string[];
  /** 出处头丢失/损坏 → 保留文档但**不猜**来源（§4.2.3） */
  metadataCorrupt?: boolean;
  /**
   * 这篇**已有哪些生成产物**（`"notes"` / `"polished"`），由后端列表端点带出
   * （`QueryService.list_resources` 取自 `index.json` 的产物索引，零额外读盘）。
   *
   * ⚠️ **`undefined` 与 `[]` 语义不同，消费方必须分流**：
   *   - `undefined` = **未知**（旧后端 / 测试 mock / 乐观插入的新文档）→ 照旧发请求
   *   - `[]`        = **确定没有产物** → 一个产物请求都不发
   * 这把「尚未生成」还原成一个正常状态，而不是靠一个必然 404 的请求 + `recoverable`
   * 把它静默掉（那会让浏览器网络层照打红字，见 [PreviewArea.loadDocContent]）。
   *
   * ⚠️ 由生成/解读链路在产物落盘后**追加**（见 store 的 `markArtifact`）——不追加的话
   * 「生成完 → 切走 → 切回」会因 `[]` 而不请求，笔记看起来凭空消失（模式 #52）。
   */
  modes?: string[];
}

/** 文件夹（可嵌套的纯逻辑分类，不映射磁盘目录） */
export interface FolderEntry {
  id: string; name: string; createdAt: string
  /** 父文件夹 id；`''` = 根级。后端返回的是**规范化后**的值（悬空/环已按根级处理）。
   *  ⚠️ 可选是**兼容旧后端 / 测试 mock**用的，消费方一律写 `f.parentId || ''`。 */
  parentId?: string
  /** **直接**挂在该夹下的文档数（不含子树） */
  count: number
  /** 含全部后代的文档数。⚠️ 验收对账必须用 `count`（求和 `totalCount` 会重复计数）。
   *  可选理由同 `parentId`；缺失时由前端 `countByFolder` 本地派生。 */
  totalCount?: number
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
  /** 纯正文字数（不含自证头）。⚠️ 读时由后端从文件头派生，不是 index 的持久字段 */
  chars?: number
  /** 生成用的模型名（版本列表的「来源」列） */
  source?: string
  /** 追问建议（预览区笔记末尾的可点 chip）。`undefined` = 这一版没有（不是空数组） */
  followUps?: string[]
}

export interface MarkdownVersion {
  version: number; id: string; createdAt: string; status?: string; mode?: string
  /** 纯正文字数（不含自证头）——展示用，`undefined` = 后端没给（不是 0 字） */
  chars?: number
  /** 该版由哪个模型生成 */
  source?: string
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

export type Store = ThemeSlice & DocsSlice & ContentSlice & TasksSlice & ChatSlice & UiSlice & ToastSlice & AppSlice
