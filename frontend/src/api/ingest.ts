/**
 * ingest 域 — URL/文本/文件导入（SSE 流式 + 非流式回退）。
 *
 * 注：原 fetchSSE / fetchFileSSE 两份近逐行重复的实现已合并为私有 ingestStream
 * （仅 body 类型与 header 不同），手写 SSE 解析统一走 sse.ts 的 parseSSEStream。
 * 已知微小差异：console.error 文案从两份统一为一条。
 */
import { BASE, CLIENT_HEADERS, errorInfoOf, errorMessageOf, type ApiErrorInfo } from './core'
import { parseSSEStream } from './sse'

type ProgressCb = (ev: { phase: string; status: string; percent?: number; message?: string }) => void
type DoneCb = (doc: any) => void

/**
 * 「导入与转录分开」的两个开关（2026-11-07）。
 *
 * ⚠️ **传的是「只收纳吗」，不是「要自动转录吗」** —— 与后端 `media_probe_only` /
 * `doc_probe_only` 同名同义。调用方传 `!autoTranscribeMedia` / `!autoTranscribeDoc`
 * （`lib/ingest-prefs.ts`）。
 *
 * ⚠️ **两档的默认值不同，所以「发什么」也不同**：后端的默认是
 * `media_probe_only=True` / `doc_probe_only=False`（= 视频只收纳、文档一键），
 * 故每一档**只在偏离自己那一档的默认值时才发**：
 *
 * | 参数 | 后端默认 | 什么时候要发 |
 * |---|---|---|
 * | `media_probe_only` | `True` | 只有 `false`（用户打开「导入视频后自动转录」）|
 * | `doc_probe_only` | `False` | 只有 `true`（用户关掉「导入文档后自动提取正文」）|
 *
 * ⚠️ **两个方向在这里恰好相反**，写成同一条「真值才发」会让媒体那一档永远发不出
 * `false` —— 用户打开开关、界面显示已打开，导入时后端仍按只收纳跑，零报错。
 *
 * ⚠️ **哪个 adapter 归哪一档由后端决定**（见 `routers/ingest.py::_resolve_probe_only`）：
 * 前端拿到一个 URL 时不知道它是视频还是文章，只有后端选出 adapter 才谈得上分档。
 * 这里传的是**用户的偏好**，不是分类结果。
 */
export interface ProbeOnlyOptions {
  mediaProbeOnly?: boolean
  docProbeOnly?: boolean
}

/**
 * 导入失败的结构化补充 —— 后端 `AppError` 的可选字段，**缺哪个就少哪个**。
 *
 * ⚠️ `message` 仍是第一参数，`info` 是**只增不改**的第二参数：既有调用方
 * `(err) => …` 逐个都不用改（多出来的实参被忽略），而新调用方拿得到
 * 「下一步该做什么」与「该不该给重试按钮」。
 *
 * ⚠️ 类型与解析函数住在 `core.ts`：`generate.ts` 的流式端点要解**同一个**信封
 * （它也用裸 `fetch`，走不到 `apiFetch`）。这里保留别名只为不打断既有 import。
 */
export type IngestErrorInfo = ApiErrorInfo
type ErrorCb = (err: string, info?: IngestErrorInfo) => void

export function ingestURL(
  url: string,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
  opts?: ProbeOnlyOptions,
) {
  const body = new URLSearchParams({ url })
  return ingestStream(`${BASE}/ingest`, body, onProgress, onDone, onError, signal, opts)
}

export function ingestText(
  text: string,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
  opts?: ProbeOnlyOptions,
) {
  const body = new URLSearchParams({ text })
  return ingestStream(`${BASE}/ingest`, body, onProgress, onDone, onError, signal, opts)
}

export function ingestFile(
  file: File,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
  opts?: ProbeOnlyOptions,
) {
  const body = new FormData()
  body.append('file', file)
  return ingestStream(`${BASE}/ingest`, body, onProgress, onDone, onError, signal, opts)
}

/**
 * 统一流式导入（私有）— 合并原 fetchSSE(URLSearchParams 版) 与 fetchFileSSE(FormData 版)。
 * 行为逐点保留：SSE 成功不回退；reader 缺失/网络错误（非 Abort）→ 回退 JSON；
 * 回退里 duplicate_url/duplicate_content 归一化 onDone；回退失败 → `网络错误: ...`。
 */
async function ingestStream(
  url: string,
  body: URLSearchParams | FormData,
  onProgress: ProgressCb,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
  opts?: ProbeOnlyOptions,
) {
  // 「只收纳」两个开关 —— **各按自己的后端默认值判方向**（对照表见 `ProbeOnlyOptions`）。
  // ⚠️ 写在 `fetch` **之前**：SSE 与下面的 JSON 回退**共用同一个 body**，一处写齐两条路。
  // `URLSearchParams.set` 与 `FormData.set` 同名可用，故不需要分支。
  // ⚠️ 媒体档默认是 `true`，所以**要发的是 `false`**；写成「真值才发」等于让这个开关
  // 永远关不掉（后端读不到参数 → 用默认值 `True` → 仍然只收纳）。
  if (opts?.mediaProbeOnly === false) body.set('media_probe_only', 'false')
  if (opts?.docProbeOnly === true) body.set('doc_probe_only', 'true')
  // URLSearchParams 需显式 Content-Type；FormData 让浏览器自动设置 multipart
  const headers = body instanceof URLSearchParams
    ? { 'Content-Type': 'application/x-www-form-urlencoded' }
    : undefined
  // P1-17：终态/进度标志提升到函数级——catch 里做网络恢复提示需要知道是否已收进度
  let receivedDoneOrError = false
  let receivedProgress = false
  try {
    // 优先尝试 SSE 流式端点
    const response = await fetch(url + '/stream', { method: 'POST', body, headers: { ...CLIENT_HEADERS, ...headers }, signal })
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('text/event-stream')) {
      const reader = response.body?.getReader()
      if (!reader) throw new Error('No reader')
      // 终态兜底（对齐 generate.ts）：SSE 连接被干净关闭但无 done/error →
      // 导入会永久卡死、TaskPanel 挂死。EOF 后未收终态 → 报「连接意外断开」。
      await parseSSEStream(reader, (data) => {
        if (data.type === 'progress') {
          receivedProgress = true
          onProgress(data)
        } else if (data.type === 'done') {
          receivedDoneOrError = true
          onDone(data)
        } else if (data.type === 'error') {
          receivedDoneOrError = true
          const msg = data.message || '未知错误'
          onProgress({ phase: 'error', status: 'error', message: msg })
          onError?.(msg, errorInfoOf(data))
        }
      })
      if (!receivedDoneOrError) {
        onProgress({ phase: 'error', status: 'error', message: '连接意外断开，请重试' })
        onError?.('连接意外断开，请重试')
      }
      return
    }
    // 回退：普通 JSON 响应
    const data = await response.json()
    // 兼容两种错误信封：旧 `{error}` 与新 AppError `{code, message, recoverable}`
    // （对齐 generate.ts:78 / transcripts.ts:26）。只判 `data.error` 时，后端返回的
    // **新信封**业务错误一个都不命中 → 被当作成功送给 `onDone` → 文档没落库，
    // 界面上却没有任何报错。
    // ⚠️ 判据是 `error || code`，**不能拿 `message` 当判据**：「已导入过《…》」
    // 「内容与已有…完全相同」这类**去重成功**响应本来就带 `message`（见后端
    // `routers/ingest.py` 的 duplicate_url / duplicate_content 分支），拿它判会把
    // 正常去重当成失败。`message` 只在**已经确定是错误之后**用来取人话文案。
    if (data.error || data.code) {
      // ⚠️ 判据仍是 `error || code`（**不能拿 `message` 当判据**：去重成功响应本来就带
      // `message`，见下面 `duplicate_url` 分支），但**取文案**走 `errorMessageOf`
      // —— `message` 优先于 `detail`，两个信封（旧 `{error}` / 新 `{code,message}`）都覆盖。
      onError?.(errorMessageOf(data, '导入失败'), errorInfoOf(data))
      return
    }
    onDone(data)
  } catch (err) {
    // P1-17：用户取消（AbortError 或 signal 已标记）→ 不回退、不提示；网络抖动才走重试
    if ((err as Error).name === 'AbortError' || signal?.aborted) return
    console.error('Ingest error (will retry with non-stream):', err)
    // 已收部分进度 → 流中途断网，提示用户正在重试而非静默重传
    if (receivedProgress) {
      onProgress({ phase: 'retrying', status: '网络恢复重试中', message: '网络恢复重试中' })
    }
    // 回退：非流式端点（传同一 signal，用户取消可中断回退请求）
    await fallbackJson(url, body, onDone, onError, signal)
  }
}

/** 非流式回退（SSE 失败时的兜底请求） */
async function fallbackJson(
  url: string,
  body: URLSearchParams | FormData,
  onDone: DoneCb,
  onError?: ErrorCb,
  signal?: AbortSignal,
) {
  try {
    const headers = body instanceof URLSearchParams
      ? { 'Content-Type': 'application/x-www-form-urlencoded' }
      : undefined
    const r = await fetch(url, { method: 'POST', body, headers: { ...CLIENT_HEADERS, ...headers }, signal })
    const data = await r.json()
    // 同上（主路径那份注释解释了判据为什么不能含 `message`）——两处同源，改一处
    // 另一处仍漏：`fallbackJson` 是**流式失败才走**的兜底路径，漏了它等于「SSE
    // 正常时能报错、降级后不能报错」，而用户看到的都是同一个导入按钮。
    if (data.error || data.code) {
      onError?.(errorMessageOf(data, '导入失败'), errorInfoOf(data))
      return
    }
    if (data.status === 'duplicate_url' || data.status === 'duplicate_content') {
      onDone({ id: data.id, title: data.title, status: data.status, message: data.message })
      return
    }
    onDone(data)
  } catch (err2) {
    console.error('Ingest fallback error:', err2)
    onError?.(`网络错误: ${(err2 as Error).message}`)
  }
}
