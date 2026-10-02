/**
 * synthesis 域 — 多篇文档合成（POST + SSE）。
 *
 * ⚠️ 这是全项目**唯一**用 POST + body 的 SSE 端点（其余都是 GET + query）：
 * 选中集是数组，塞进 query 既容易漏 set（CLAUDE.md #6 的历史坑），又会撞 URL 长度上限。
 * 代价是必须自己拼 `Content-Type: application/json` —— 照抄 `streamChat` 的 GET 写法
 * 会得到一个 422。
 */
import { BASE, getErrorMessage } from './core'
import { parseSSEStream } from './sse'

/** 原料模式：原文转录 / 已生成的笔记。**互斥不混合**（见设计 §19.2）。 */
export type SourceMode = 'raw' | 'notes'

/**
 * 每篇的预计处理方式。
 *
 * ⚠️ 取值与后端 `_classify` 同源，**且前四个直接复用 `_STATUS_LABEL` 的 key**
 * （产物里的降级标注与预览里的预估说的是同一件事）。`unavailable` 是预览独有
 * 的第五态：当前原料模式下这一篇没有可用原料（如 `notes` 模式下无笔记）。
 * `over_window` 是**计划态**——执行后落成 `truncated_over_window`。
 */
export type SynthesisPlan =
  | 'full' | 'summarized' | 'chunked' | 'over_window' | 'unavailable'

export interface SynthesisPreviewDoc {
  id: string
  title: string
  /** 该篇在当前原料模式下的 token 量 */
  tokens: number
  plan: SynthesisPlan
  /** 该篇**是否存在**笔记（与当前模式无关，供切换模式时预判） */
  hasNotes: boolean
  notesTokens: number
}

export interface SynthesisPreview {
  context_window: number
  budget: number
  total_tokens: number
  /** stuff = 全部原样装下（零 LLM 调用）；map_reduce = 需压缩；reject = 窗口不够 */
  mode: 'stuff' | 'map_reduce' | 'reject'
  reason: string
  /**
   * 「让全部 N 篇**原样装下**所需的窗口」。
   *
   * ⚠️ 语义不是「能做合成的最小窗口」——总量超了照样能做，只是改走压缩。
   * 文案不要写成后者，否则用户以为窗口不够就不能合成。
   */
  stuff_window: number
  estimated_calls: number
  docs: SynthesisPreviewDoc[]
  /**
   * 当前原料模式下被排除的篇的 **id**（`notes` 模式下无笔记的那些）。
   *
   * ⚠️ 是 id 不是标题：消费方要拿它从选中集里做差集。标题经过后端清洗、且允许
   * 重名，拿它做身份判定必然错（同 §4.2.2）。
   */
  excluded: string[]
}

/**
 * 合成**前**的预估：每篇 token 量 + 预计处理方式 + 需要多大窗口才能全部装下。
 *
 * **零 LLM 调用、零副作用**——让用户在花 token 之前就知道该不该换模型，
 * 以及选「笔记」模式会不会有篇用不上。
 *
 * ⚠️ **必须检查 `body.code`**：后端把「篇数不足 / 源已删」这类**预期业务状态**
 * 转成 **HTTP 200 + `{code, message, ...}`**（项目模式 #36）——只看 `response.ok`
 * 会把失败当成功，然后拿着一个没有 `docs` 的对象去渲染。
 */
export async function previewSynthesis(
  docIds: string[],
  modelId?: number | null,
  sourceMode: SourceMode = 'raw',
): Promise<SynthesisPreview> {
  const response = await fetch(`${BASE}/synthesis/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      doc_ids: docIds,
      model_id: modelId ?? null,
      source_mode: sourceMode,
    }),
  })
  let body: Record<string, unknown> = {}
  try { body = await response.json() } catch { /* 非 JSON 响应体 */ }
  // 成功结构里**没有** `code` 键——它只由 AppError.to_dict() 产出
  if (!response.ok || typeof body.code === 'string') {
    throw new Error(String(body.message || body.detail || `预估失败 (${response.status})`))
  }
  return body as unknown as SynthesisPreview
}

export interface StreamSynthesisCallbacks {
  /** 阶段提示（读取源文档 / 分析对比维度 / 逐篇结构化抽取 i/N / 组装正文） */
  onStage?: (message: string) => void
  /**
   * 完成：`id` 是新建的 composite 文档。
   * `degraded` = 被截断/摘要失败的篇目标题；
   * `excluded` = `notes` 模式下因无笔记被排除的篇 **id**（设计 §19.4：它不是错误，
   * 但没看预览就点合成的用户必须知道，否则会以为它参与了对比）。
   * ⚠️ 两个数组**单位不同**（标题 vs id）—— 有意的：`degraded` 只用于提示文案，
   * `excluded` 要参与差集运算（决定 `sourceDocIds` 写哪几篇）。
   */
  onDone: (id: string, mode: string, degraded: string[], excluded: string[]) => void
  /**
   * 失败。⚠️ `code` 是后端给的结构化错误码，**不是** HTTP 状态
   * （业务失败也走 200 + SSE，见后端 synthesis.py）——
   * `cancelled` 单列出来是因为用户主动取消不该弹错误 toast。
   */
  onError: (code: string, message: string, cancelled: boolean) => void
}

export interface SynthesisOptions {
  title?: string
  /** 对比指引（可选，追加到 prompt 的 user_request） */
  userNotes?: string
  modelId?: number | null
  /** 对比维度（每行一个，已清洗）；空数组 = 用默认预设 */
  dimensions?: string[]
  /** 原料模式：原文 / 已生成的笔记（互斥） */
  sourceMode?: SourceMode
}

export function streamSynthesis(
  sourceDocIds: string[],
  opts: SynthesisOptions,
  callbacks: StreamSynthesisCallbacks,
  signal?: AbortSignal,
): void {
  // 追踪是否收到 done/error：连接异常断开时两者都不会触发，
  // 不追踪则 UI 永久停在「合成中」（同 generate/chat 的既有做法）
  let receivedDoneOrError = false
  const origDone = callbacks.onDone
  const origError = callbacks.onError
  callbacks.onDone = (...a) => { receivedDoneOrError = true; origDone(...a) }
  callbacks.onError = (...a) => { receivedDoneOrError = true; origError(...a) }

  fetch(`${BASE}/synthesis/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // ⚠️ 字段名与后端 `SynthesisRequest` 逐字对齐
    // （doc_ids / title / user_notes / model_id / dimensions / source_mode）
    body: JSON.stringify({
      doc_ids: sourceDocIds,
      title: opts.title ?? '',
      user_notes: opts.userNotes ?? '',
      model_id: opts.modelId ?? null,
      dimensions: opts.dimensions ?? [],
      source_mode: opts.sourceMode ?? 'raw',
    }),
    signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        let body: Record<string, unknown> = {}
        try { body = await response.json() } catch { /* 非 JSON 响应体 */ }
        callbacks.onError(
          String(body.code ?? 'SYNTH_HTTP_ERROR'),
          String(body.detail || body.message || `请求失败 (${response.status})`),
          false,
        )
        return
      }
      const reader = response.body?.getReader()
      if (!reader) {
        callbacks.onError('SYNTH_NO_BODY', '无法读取响应流', false)
        return
      }

      await parseSSEStream(reader, (data) => {
        if (data.type === 'stage') callbacks.onStage?.(data.message)
        else if (data.type === 'done') {
          callbacks.onDone(data.id, data.mode ?? '', data.degraded ?? [], data.excluded ?? [])
        } else if (data.type === 'error') {
          callbacks.onError(String(data.code ?? 'SYNTH_FAILED'),
            String(data.message ?? '合成失败'), data.code === 'SYNTH_CANCELLED')
        }
      })

      if (!receivedDoneOrError) {
        callbacks.onError('SYNTH_DISCONNECTED', '连接意外断开，请重试', false)
      }
    })
    .catch((err) => {
      if ((err as Error).name === 'AbortError') {
        if (!receivedDoneOrError) callbacks.onError('SYNTH_CANCELLED', '已取消', true)
        return
      }
      callbacks.onError('SYNTH_FAILED', getErrorMessage(err), false)
    })
}
