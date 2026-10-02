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

export interface StreamSynthesisCallbacks {
  /** 阶段提示（读取源文档 / 分析对比维度 / 逐篇结构化抽取 i/N / 组装正文） */
  onStage?: (message: string) => void
  /** 完成：`id` 是新建的 composite 文档，`degraded` 是被截断/摘要失败的篇目标题 */
  onDone: (id: string, mode: string, degraded: string[]) => void
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
    // ⚠️ 字段名与后端 `SynthesisRequest` 逐字对齐（doc_ids / title / user_notes / model_id）
    body: JSON.stringify({
      doc_ids: sourceDocIds,
      title: opts.title ?? '',
      user_notes: opts.userNotes ?? '',
      model_id: opts.modelId ?? null,
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
          callbacks.onDone(data.id, data.mode ?? '', data.degraded ?? [])
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
