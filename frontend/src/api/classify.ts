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
  /**
   * **仅当 `none` 是「新建超限被拒」的结果时在场** —— 模型原本想建的那个夹名。
   *
   * ⚠️ 与「本来就无合适归处」的 `none` 是两回事：那个不带这个字段。带它的时候，
   * 对话框会多出一个「新建：XX」选项供用户一键采纳（等于手动把上限放开一格）。
   * 没有它，用户只能看到「不归入」，模型原本的分类意图就整个丢了。
   */
  candidate?: string
  /**
   * `true` = 这是**口袋（兜底）文件夹**（后端 `MISC_FOLDER_NAME`）。
   *
   * 它不占新建额度，由后端机械闸门约束（钻进太多就整夹撤下）。此处只作标记，
   * UI 暂不特殊渲染 —— 它在界面上就是一个普通的「建议新建」。
   */
  misc?: boolean
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
  /** 「最多新建」滑条的上界 */
  maxNewFoldersLimit: number
  /** 夹紧后的实际取材深度 */
  notesChars: number
  notesCharsMax: number
  maxTagsPerDoc: number
  /** 用户手填基础标签的条数上限 */
  maxSeedTags: number
}

/**
 * 文件夹策略 —— **用户在点「开始分析」之前**选定，决定模型面对怎样的分类空间。
 *
 * ⚠️ **这三项硬编码在前端是刻意的**，与「引擎/风格/体裁必须 capabilities 下发」不同：
 * 后者是「后端注册了什么，前端就得列什么」（漏了就是后端加了引擎而前端静默不显示）；
 * 这里是**界面选项**——每项配一段人写的说明与排布，新策略上线本就该手工设计 UI，
 * 不能像引擎列表那样自动铺开。后端 `FOLDER_MODES` 是同一份清单的**校验侧**，
 * 两边不一致时后端会显式报 `CLASSIFY_BAD_OPTION`（显式失败，不是静默降级）。
 */
export type FolderMode =
  /** 优先复用现有夹，必要时新建（受 maxNewFolders 约束） */
  | 'reuse_or_new'
  /** 只用现有夹，一篇都不新建 */
  | 'reuse_only'
  /** 本次完全不动文件夹，只打标签 */
  | 'tags_only'

/**
 * 标签策略 —— 决定模型的标签从哪来。
 *
 * `vocab`：把全库现有标签喂给它并要求优先复用（收敛词表）。
 * `seed`：用户手填几个基础标签作为词表（**库里还没标签时**的起步方案）。
 * `free`：不给词表，完全由模型自由发挥。
 */
export type TagMode = 'vocab' | 'seed' | 'free'

/** 「开始分析」时用户选定的那一套策略。 */
export interface ClassifyOptions {
  /** 0 = 仅标题（后端 `clamp_notes_chars` 会夹紧） */
  notesChars: number
  folderMode: FolderMode
  maxNewFolders: number
  tagMode: TagMode
  /** 仅 `tagMode === 'seed'` 时有意义 */
  seedTags: string[]
}

export interface ClassifyPreview {
  status: string
  model: string
  limits: ClassifyLimits
  /** 策略回显：前端据此知道「这一次模型到底看到了什么」 */
  folderMode: FolderMode
  tagMode: TagMode
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
 * ⚠️ **它是计费调用的唯一触发点**——调用方必须等到用户在配置阶段按下
 * 「开始分析」才调它，不能在打开对话框时就调（2026-10-11 实机反馈的原始形态）。
 *
 * `signal` 用于面板折叠时中止（折叠 = 卸载组件，不中止就是一次无人接收的计费调用）。
 *
 * ⚠️ **必须检查 `body.code`**：后端把「没选文档 / 没配 key / 超时 / JSON 不合法」
 * 这类业务失败转成 **HTTP 200 + `{code, message}`**（项目模式 #36）——只看
 * `response.ok` 会把失败当成功，然后拿一个没有 `suggestions` 的对象去渲染。
 */
export async function classifyPreview(
  docIds: string[],
  opts: ClassifyOptions,
  signal?: AbortSignal,
): Promise<ClassifyPreview> {
  const response = await fetch(`${BASE}/classify/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ docIds, ...opts }),
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
