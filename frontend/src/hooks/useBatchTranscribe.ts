/**
 * 批量转录（2026-11-07）。
 *
 * 文档库里选中若干「待转录」的文档，确认后逐个调用已有的
 * `POST /api/transcripts/{id}/retranscribe`。不新建后端能力，也不改端点。
 *
 * ## 为什么不复用 `useStartTranscription`
 *
 * 单篇那个 hook 走 `useTaskRunner`，而 `useTaskRunner` 的成功回调带 `isSame()` 判断
 * ——「跑完之后用户还看着这篇吗」。单篇场景下这个判断是对的（用户切走了就别改预览区）。
 * 批量场景下它会把**每一篇**都判成「不是当前文档」：你正在看第 5 篇，队列在跑第 2 篇，
 * 于是回填 `docs` 的那一步被跳过，转录完了列表徽章还挂着「待转录」。
 *
 * 所以这里自己排队列，任务 key 仍用 `${docId}__retranscribe`（与单篇同一个）——
 * 这样预览区的待转录卡片和任务面板都能看到进度，用户点进去也是同一个状态。
 * 成功后调 `applyTranscriptionResult`，那是单篇与批量共用的那一份回填实现。
 *
 * ## 三条与批量导入相同的纪律
 *
 * 1. **串行**：转录是本机最重的一步，并发跑只会互相抢带宽与 CPU。
 * 2. **`open()` 零请求**：只把清单摆进确认对话框，用户按「开始转录」才发第一个请求。
 * 3. **单篇失败不影响后面的**：每一项各自标红，最后汇总。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { useStore } from '../store'
import { getErrorMessage } from '../api/client'
import { retranscribe } from '../api/transcripts'
import { registerAbort } from '../api/taskManager'
import { applyTranscriptionResult } from './useStartTranscription'
import { clearQueue, markQueueIdle, markQueueRunning, saveQueue } from '../lib/batch-queue'

export type TranscribePhase = 'confirm' | 'running' | 'done'

export type TranscribeStatus = 'queued' | 'running' | 'done' | 'failed' | 'skipped'

export interface TranscribeItem {
  id: string
  title: string
  status: TranscribeStatus
  /** 失败原因。成功与排队中为空串。 */
  message: string
}

/** 单篇的终态。`aborted` 单独一个值：取消不是失败，不该计入失败数。 */
type RunOutcome =
  | { type: 'ok' }
  | { type: 'error'; message: string }
  | { type: 'aborted' }

export function useBatchTranscribe() {
  const setProcessing = useStore(s => s.setProcessing)
  const failTask = useStore(s => s.failTask)

  const [items, setItems] = useState<TranscribeItem[] | null>(null)
  const [phase, setPhase] = useState<TranscribePhase>('confirm')
  /**
   * 「在后台继续」：用户不想盯着对话框，但队列不停。
   * 置真时 `TranscribeDialog` 不渲染，任务面板与预览区的进度照常。
   */
  const [background, setBackground] = useState(false)

  const cancelledRef = useRef(false)
  const ctrlRef = useRef<AbortController | null>(null)

  const patch = useCallback((id: string, next: Partial<TranscribeItem>) => {
    setItems(prev => prev && prev.map(it => (it.id === id ? { ...it, ...next } : it)))
  }, [])

  /**
   * 队列快照落盘：刷新之后文档库读回这份记录，提示「还有 N 篇没转录完」。
   *
   * ⚠️ `items === null` 时**一个字节都不写**。挂载那一刻 items 就是 null，顺手写一份
   * 空快照会把上次留下的记录当场抹掉，恢复入口永远看不到。
   */
  useEffect(() => {
    if (phase === 'done') {
      clearQueue('transcribe')
      markQueueIdle('transcribe')
      return
    }
    if (items && items.length) saveQueue('transcribe', items)
  }, [items, phase])

  /** 打开确认对话框。零请求。 */
  const open = useCallback((docs: { id: string; title: string }[]) => {
    cancelledRef.current = false
    setBackground(false)
    setItems(docs.map(d => ({ id: d.id, title: d.title, status: 'queued', message: '' })))
    setPhase('confirm')
  }, [])

  const reset = useCallback(() => {
    // 关掉对话框 = 用户不要这一轮了。快照一并清掉，否则下次打开文档库会提示恢复。
    clearQueue('transcribe')
    setItems(null)
    setPhase('confirm')
    setBackground(false)
  }, [])

  /**
   * 跑一篇，返回终态。
   *
   * `retranscribe` 在用户取消时抛的是 `AbortError`（`apiFetch` 透传），不判它会
   * 被 `getErrorMessage` 报成一句用户看不懂的话，同时把「取消」计入失败数。
   */
  const runOne = useCallback(
    (id: string, signal: AbortSignal): Promise<RunOutcome> =>
      new Promise((resolve) => {
        let settled = false
        const settle = (outcome: RunOutcome) => {
          if (settled) return
          settled = true
          resolve(outcome)
        }
        signal.addEventListener('abort', () => settle({ type: 'aborted' }))
        retranscribe(id, signal)
          .then(r => {
            if ('error' in r) settle({ type: 'error', message: r.error })
            else settle({ type: 'ok' })
          })
          .catch((e: unknown) => {
            if (signal.aborted) settle({ type: 'aborted' })
            else settle({ type: 'error', message: getErrorMessage(e) })
          })
      }),
    [],
  )

  /** 开始逐个转录。 */
  const confirm = useCallback(async () => {
    const list = items
    if (!list || !list.length) return
    cancelledRef.current = false
    // 本页此刻在跑：面板折叠后重新展开时，恢复入口不能再提供这一批（会跑第二遍）。
    markQueueRunning('transcribe')
    setPhase('running')

    let done = 0
    let failed = 0

    for (const item of list) {
      if (cancelledRef.current) {
        patch(item.id, { status: 'skipped', message: '已取消' })
        continue
      }

      const taskKey = `${item.id}__retranscribe`
      const ctrl = new AbortController()
      ctrlRef.current = ctrl
      registerAbort(taskKey, ctrl)
      setProcessing(taskKey, { kind: 'retranscribe', title: item.title })
      patch(item.id, { status: 'running', message: '' })

      let outcome: RunOutcome
      try {
        outcome = await runOne(item.id, ctrl.signal)
      } catch (e) {
        outcome = { type: 'error', message: getErrorMessage(e) }
      } finally {
        ctrlRef.current = null
      }

      // ⚠️ 成功与取消才清任务；失败那条**不能清** —— `failTask` 只改已有条目的状态
      // （`if (tasks[taskKey])`），先清就什么都不剩，任务面板上那条带重试按钮的记录
      // 永远不会出现（`failTask` 刻意留 5 秒正是给用户点重试的）。失败条目由它自己定时清除。
      if (outcome.type === 'aborted') {
        setProcessing(taskKey, null)
        patch(item.id, { status: 'skipped', message: '已取消' })
        continue
      }
      if (outcome.type === 'error') {
        failTask(taskKey, outcome.message)
        patch(item.id, { status: 'failed', message: outcome.message })
        failed++
        continue
      }

      setProcessing(taskKey, null)
      await applyTranscriptionResult(item.id)
      patch(item.id, { status: 'done', message: '' })
      done++
    }

    setPhase('done')
    setBackground(false)
    // ⚠️ 这里必须显式清，不能只靠上面那个 effect：面板折叠时组件已卸载，`setPhase`
    // 不会带来一次渲染，effect 也就不跑 ——「收起对话框在后台跑完 → 重新展开」会看到
    // 一条指向已跑完队列的恢复横幅。
    clearQueue('transcribe')
    markQueueIdle('transcribe')
    const parts = [`完成 ${done} 篇`]
    if (failed > 0) parts.push(`失败 ${failed} 篇`)
    const skipped = list.length - done - failed
    if (skipped > 0) parts.push(`跳过 ${skipped} 篇`)
    useStore.getState().addToast(
      `批量转录${parts.join('，')}`,
      failed > 0 ? 'error' : 'success',
    )
  }, [items, patch, runOne, setProcessing, failTask])

  /** 取消：中断当前请求，剩余项标为已跳过。对话框留着让用户看结果。 */
  const cancel = useCallback(() => {
    if (phase === 'confirm') {
      reset()
      return
    }
    cancelledRef.current = true
    ctrlRef.current?.abort()
    setItems(prev => prev && prev.map(it => (
      it.status === 'queued' ? { ...it, status: 'skipped', message: '已取消' } : it
    )))
  }, [phase, reset])

  /** 收起对话框但让队列继续跑。只在 running 阶段有意义。 */
  const sendToBackground = useCallback(() => setBackground(true), [])

  return { items, phase, background, open, confirm, cancel, reset, sendToBackground }
}
