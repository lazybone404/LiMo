/**
 * classify 域 — 自动分类（一次 LLM 建议 + 一次审定写入）。
 *
 * 两个端点是一对，**分工即产品决策**：`preview` 只产出建议、**零写入**；
 * 用户在对话框里改完，`commit` 才写库。模型永远不直接改库里的状态。
 *
 * ⚠️ **字段名跟 camelCase**（与本域的 `BatchTranscriptsRequest` 的 `tagMode`/`folderId`
 * 对齐）。`synthesis.*` 那份是合成域的历史写法（snake_case），不要照抄过来。
 */
import { BASE } from './core'

/** 取材来源三态。`shortened` = 被上下文预算缩减（**不是**用户选的那个数）。 */
export type ClassifySource = 'notes' | 'shortened' | 'title_only'

export type FolderAction = 'reuse' | 'new' | 'none'

export interface ClassifyFolderSuggestion {
  action: FolderAction
  /** 仅 `action === 'reuse'` 时在场 */
  id?: string
  /** `reuse`（现有夹名）/ `new`（新建名）时在场；`none` 时缺省 */
  name?: string
}

export interface ClassifySuggestion {
  id: string
  title: string
  source: ClassifySource
  /** 实际喂给模型的字符数（`shortened` 时是缩减后的值） */
  chars: number
  folder: ClassifyFolderSuggestion
  tags: string[]
  /** 该篇的降级/改写说明（**必须显示**——它是「模型看到了什么」的唯一线索） */
  warnings: string[]
}

/** 上限由后端**回显**而非前端硬编码（同「引擎前端后端驱动」原则）。 */
export interface ClassifyLimits {
  maxNewFolders: number
  /** 夹紧后的实际取材深度 */
  notesChars: number
  notesCharsMax: number
  maxTagsPerDoc: number
}

export interface ClassifyPreview {
  status: string
  model: string
  limits: ClassifyLimits
  /** 喂给模型的分类空间（回显，供 UI 显示「已归入现有文件夹」） */
  folders: { id: string; name: string; count: number }[]
  /** 喂给模型的标签词表（回显） */
  tagVocab: string[]
  /** **不变式**：长度与顺序恒等于请求里的 docIds */
  suggestions: ClassifySuggestion[]
  warnings: string[]
}

export interface ClassifyAssignment {
  id: string
  /** `''` = 未分类（移出文件夹）；非空必须是**已存在**的文件夹 id */
  folderId: string
  tags: string[]
}

export interface ClassifyCommitResult {
  status: string
  affected: number
  failed: { id: string; error: string }[]
}

/**
 * 生成分类建议。**零写入**、恰好一次 LLM 调用（计费）。
 *
 * `signal` 用于面板折叠时中止（折叠 = 卸载组件，不中止就是一次无人接收的计费调用）。
 *
 * ⚠️ **必须检查 `body.code`**：后端把「没选文档 / 没配 key / 超时 / JSON 不合法」
 * 这类业务失败转成 **HTTP 200 + `{code, message}`**（项目模式 #36）——只看
 * `response.ok` 会把失败当成功，然后拿一个没有 `suggestions` 的对象去渲染。
 */
export async function classifyPreview(
  docIds: string[],
  notesChars: number,
  signal?: AbortSignal,
): Promise<ClassifyPreview> {
  const response = await fetch(`${BASE}/classify/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ docIds, notesChars }),
    signal,
  })
  let body: Record<string, unknown> = {}
  try { body = await response.json() } catch { /* 非 JSON 响应体 */ }
  // 成功结构里**没有** `code` 键——它只由 AppError.to_dict() 产出
  if (!response.ok || typeof body.code === 'string') {
    throw new Error(String(body.message || body.detail || `分类失败 (${response.status})`))
  }
  return body as unknown as ClassifyPreview
}

/**
 * 写入审定后的方案（一次加锁、一次写盘）。
 *
 * ⚠️ **调用方必须检查 `failed.length`**：部分失败是**返回值的一部分**而不是异常
 * ——把「3 篇里有 1 篇没写进去」当完全成功，是这里最坏的失效形态。
 */
export async function commitClassifications(
  assignments: ClassifyAssignment[],
): Promise<ClassifyCommitResult> {
  const response = await fetch(`${BASE}/classify/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments }),
  })
  let body: Record<string, unknown> = {}
  try { body = await response.json() } catch { /* 非 JSON 响应体 */ }
  if (!response.ok || typeof body.code === 'string') {
    throw new Error(String(body.message || body.detail || `写入分类失败 (${response.status})`))
  }
  return body as unknown as ClassifyCommitResult
}
