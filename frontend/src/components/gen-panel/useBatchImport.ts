/**
 * 批量导入的执行队列（2026-11-07）。
 *
 * 一次拖入多个文件时，`ImportSection` 把文件列表交给 `start()`。`start()` **一个请求都不发**，
 * 只把文件识别成条目、摆进确认对话框。用户点「开始导入」后 `confirm()` 才按顺序逐个调用
 * 既有的 `ingestFile` / `ingestText`。
 *
 * 三条设计约束：
 *
 * 1. **串行**。前一个文件的终态到了才发下一个。并行的进度条会在同一条 TaskPanel 上互相覆盖，
 *    且并发上限（`MAX_CONCURRENT_IMPORTS = 3`）是为「用户手动连点」设计的，批量场景不该撞它。
 * 2. **不复用 `useTaskRunner`**。它的 stale 判据是 `taskKey.split('__')[0]` 即文档 id，
 *    而批量导入在完成之前没有文档 id。
 * 3. **单个文件的失败不影响后面的文件**。每一项各有一个 `taskKey`，失败只把这一项标红。
 */
import { useCallback, useRef, useState } from 'react'

import { useStore } from '../../store'
import { ingestFile, ingestText, type ProbeOnlyOptions } from '../../api/ingest'
import { processImage } from '../../api/generate'
import { registerAbort } from '../../api/taskManager'
import { optimisticDoc } from '../../lib/docEntry'
import {
  WARN_FILE_BYTES,
  planBatchFiles,
  type BatchCandidate,
  type BatchKind,
} from './batchImport'

export type BatchItemStatus = 'queued' | 'running' | 'done' | 'duplicate' | 'failed' | 'skipped'

export interface BatchItem {
  key: string
  name: string
  size: number
  kind: BatchKind | null
  subType: string
  status: BatchItemStatus
  /** 进度阶段（导入中）或失败原因（失败 / 跳过）。 */
  message: string
}

export type BatchPhase = 'confirm' | 'running' | 'done'

export interface BatchImportOptions {
  probeOpts: ProbeOnlyOptions
  /** 图片导入后是否顺带跑一次文字提取。与单文件导入的「导入后自动 OCR」同义。 */
  preOcr: boolean
}

export interface BatchImportResult {
  done: number
  duplicate: number
  failed: number
  skipped: number
}

/** 文档类条目的兜底 sourceType —— 后端给了 `sourceType` 时以它为准（见 `optimisticDoc`）。 */
function fallbackType(kind: BatchKind | null): string {
  return kind || 'file'
}

/** 单次导入的终态。`aborted` 是第三种，不能并进 `failed`：取消不是失败。 */
type RunOutcome =
  | { type: 'done'; doc: any }
  | { type: 'error'; message: string }
  | { type: 'aborted' }

export function useBatchImport({ probeOpts, preOcr }: BatchImportOptions) {
  const setProcessing = useStore(s => s.setProcessing)
  const failTask = useStore(s => s.failTask)

  const [items, setItems] = useState<BatchItem[] | null>(null)
  const [phase, setPhase] = useState<BatchPhase>('confirm')
  /** 因超过 `BATCH_MAX_FILES` 而未进入清单的文件数。 */
  const [truncated, setTruncated] = useState(0)

  const candidatesRef = useRef<BatchCandidate[]>([])
  const seqRef = useRef(0)
  const ctrlRef = useRef<AbortController | null>(null)
  const cancelledRef = useRef(false)

  const patch = useCallback((key: string, next: Partial<BatchItem>) => {
    setItems(prev => prev && prev.map(it => (it.key === key ? { ...it, ...next } : it)))
  }, [])

  /** 打开确认对话框。零请求。 */
  const start = useCallback((files: File[]) => {
    const { candidates, truncated } = planBatchFiles(files)
    setTruncated(truncated)
    candidatesRef.current = candidates
    cancelledRef.current = false
    setItems(candidates.map((c, i) => ({
      key: `b${i}`,
      name: c.file.name,
      size: c.file.size,
      kind: c.kind,
      subType: c.subType,
      status: c.reject ? 'skipped' : 'queued',
      message: c.reject,
    })))
    setPhase('confirm')
  }, [])

  /** 关闭对话框并清空。 */
  const reset = useCallback(() => {
    setItems(null)
    setPhase('confirm')
    setTruncated(0)
    candidatesRef.current = []
  }, [])

  /**
   * 跑一个文件，返回它的终态。
   *
   * 包装成 Promise 的原因：`ingestFile` / `ingestText` 是回调式接口，且**用户取消时两者
   * 都不回调**（`ingestStream` 在 AbortError 分支直接 return）。只等 `onDone` / `onError`
   * 的话，取消会让这个 Promise 永远不落地、串行队列卡在第一项。
   */
  const runOne = useCallback((
    candidate: BatchCandidate,
    signal: AbortSignal,
    onStage: (text: string) => void,
  ): Promise<RunOutcome> => new Promise((resolve) => {
    let settled = false
    const settle = (outcome: RunOutcome) => {
      if (settled) return
      settled = true
      resolve(outcome)
    }
    signal.addEventListener('abort', () => settle({ type: 'aborted' }))

    const onProgress = (ev: { message?: string; phase?: string }) => {
      const text = ev.message || ev.phase || ''
      if (text) onStage(text)
    }
    const onDone = (doc: any) => settle({ type: 'done', doc })
    const onError = (err: string) => {
      // 取消途中 `fallbackJson` 的 catch 会把 AbortError 报成「网络错误」。
      // 那条消息不是用户的失败，按取消处理。
      if (signal.aborted) settle({ type: 'aborted' })
      else settle({ type: 'error', message: err })
    }

    if (candidate.kind === 'text') {
      candidate.file.text()
        .then(text => ingestText(text, onProgress, onDone, onError, signal, probeOpts))
        .catch((e: unknown) => settle({ type: 'error', message: `读取文件失败：${(e as Error).message}` }))
      return
    }
    ingestFile(candidate.file, onProgress, onDone, onError, signal, probeOpts)
  }), [probeOpts])

  /** 开始逐个导入。 */
  const confirm = useCallback(async () => {
    const candidates = candidatesRef.current
    if (!candidates.length) return
    cancelledRef.current = false
    setPhase('running')

    let result: BatchImportResult = { done: 0, duplicate: 0, failed: 0, skipped: 0 }
    /** 第一篇真正新入库、且不是待转录的文档，用于完成后切换视图。 */
    let firstNewId = ''
    let firstNewPending = false

    for (let i = 0; i < candidates.length; i++) {
      const candidate = candidates[i]
      const key = `b${i}`
      if (cancelledRef.current) {
        patch(key, { status: 'skipped', message: '已取消' })
        result.skipped++
        continue
      }
      // 识别阶段已经判死的条目（不支持的类型 / 超过 2GB）不再尝试。
      if (candidate.reject) { result.skipped++; continue }

      if (candidate.file.size > WARN_FILE_BYTES) {
        patch(key, { message: '文件较大，上传与解析可能较慢' })
      }

      const importKey = `__import__${seqRef.current++}`
      const taskKey = importKey + '__import'
      const ctrl = new AbortController()
      ctrlRef.current = ctrl
      registerAbort(taskKey, ctrl)
      setProcessing(taskKey, { kind: 'import', title: candidate.file.name })
      patch(key, { status: 'running', message: '' })

      let outcome: RunOutcome
      try {
        outcome = await runOne(candidate, ctrl.signal, text => {
          patch(key, { message: text })
        })
      } catch (e) {
        outcome = { type: 'error', message: (e as Error).message || '导入失败' }
      } finally {
        ctrlRef.current = null
      }

      if (outcome.type === 'aborted') {
        setProcessing(taskKey, null)
        patch(key, { status: 'skipped', message: '已取消' })
        result.skipped++
        continue
      }
      if (outcome.type === 'error') {
        // ⚠️ 这里**不能**先 `setProcessing(taskKey, null)` —— `failTask` 只改已有条目的状态
        // （`if (tasks[taskKey])`），先清就什么都不剩，任务面板上那条带重试按钮的记录
        // 永远不会出现。失败条目由 `failTask` 自己的 5 秒定时清除。
        failTask(taskKey, outcome.message)
        patch(key, { status: 'failed', message: outcome.message })
        result.failed++
        continue
      }

      const doc = outcome.doc
      const isDup = doc.status === 'duplicate_url' || doc.status === 'duplicate_content'
      const existing = useStore.getState().docs.find(d => d.id === doc.id)
      setProcessing(taskKey, null)

      if (isDup) {
        // 库里没有这一篇时补一条乐观条目 —— 与单文件导入的去重分支同一处理。
        if (!existing && doc.id) useStore.getState().addDoc(optimisticDoc(doc, fallbackType(candidate.kind)))
        patch(key, { status: 'duplicate', message: '已存在' })
        result.duplicate++
        continue
      }

      if (!existing) {
        useStore.getState().addDoc(optimisticDoc(doc, fallbackType(candidate.kind)))
        if (!firstNewId) {
          firstNewId = doc.id
          firstNewPending = doc.transcribeState === 'pending'
        }
      }
      patch(key, { status: 'done', message: '' })
      result.done++

      // 图片的「导入后自动提取文字」与单文件路径一致；这里只刷产物、不切视图，
      // 批量结束后由下面的收尾统一决定停在哪一页。
      if (preOcr && candidate.kind === 'image' && doc.id) {
        const processKey = doc.id + '__process'
        setProcessing(processKey, { kind: 'process', mode: 'extract' })
        try {
          const r = await processImage(doc.id, 'extract')
          if (!('error' in r)) {
            useStore.getState().setImageResult(doc.id, 'extract', {
              text: r.text, engine: r.engine || '', id: r.result_id || '',
            })
          }
        } catch {
          // 提取失败不影响导入本身，这一篇的「已导入」结论不变。
        } finally {
          setProcessing(processKey, null)
        }
      }
    }

    setPhase('done')
    if (firstNewId) {
      const s = useStore.getState()
      s.selectDoc(firstNewId)
      if (!firstNewPending) s.setActiveView('transcript')
    }
    useStore.getState().addToast(summaryText(result), result.failed > 0 ? 'error' : 'success')
  }, [patch, preOcr, runOne, setProcessing, failTask])

  /** 取消：置标志、中断当前请求、剩余项标为已跳过。对话框保留，用户能看到结果。 */
  const cancel = useCallback(() => {
    cancelledRef.current = true
    ctrlRef.current?.abort()
    if (phase === 'confirm') {
      reset()
      return
    }
    setItems(prev => prev && prev.map(it => (
      it.status === 'queued' ? { ...it, status: 'skipped', message: '已取消' } : it
    )))
  }, [phase, reset])

  return { items, phase, truncated, start, confirm, cancel, reset }
}

function summaryText(r: BatchImportResult): string {
  const parts = [`成功 ${r.done} 篇`]
  if (r.duplicate > 0) parts.push(`已存在 ${r.duplicate} 篇`)
  if (r.failed > 0) parts.push(`失败 ${r.failed} 篇`)
  if (r.skipped > 0) parts.push(`跳过 ${r.skipped} 篇`)
  return `批量导入完成：${parts.join('，')}`
}
