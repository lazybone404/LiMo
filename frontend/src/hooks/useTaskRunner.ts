/**
 * useTaskRunner — 任务执行公共骨架（capturedId → taskKey → abort → setProcessing →
 * await run → stale 检查 → 错误处理 → finally 清理）。
 *
 * 消除 handleRetranscribe / handleProcessImage 逐字重复的 ~20 行样板。
 * 约束：
 * - taskKey = `${docId}__${kind}`（同时用于 stale 检查与 setProcessing）
 * - AbortError 静默返回（用户取消不报错）
 * - finally 用 setProcessing(taskKey, null) 前缀树形删除（语义见 tasksSlice）
 * - onSuccess 内部多段 await 之后的 stale 检查由调用方保留（hook 只在 run 完成后查一次）
 * - 与导航无关的副作用走 onSuccessAlways（见该字段说明）
 */
import { useCallback } from 'react'
import { useStore } from '../store'
import { getErrorMessage } from '../api/client'
import type { TaskInfo } from '../store/types'

export interface RunTaskOptions<T> {
  /** 完整任务 key（`${docId}__${kind}`）— 同时用于 stale 检查与 setProcessing */
  taskKey: string
  /** 写入 processingTasks 的任务信息（kind 必填） */
  taskInfo: TaskInfo
  /** 注册 AbortController 并返回 signal（GenPanel 传 createAbort） */
  prepareAbort: (taskKey: string) => AbortSignal
  /** 实际执行。抛 AbortError 表示用户取消（静默返回） */
  run: (signal: AbortSignal) => Promise<T>
  /** 成功副作用 — hook 保证调用时仍同文档 */
  onSuccess?: (result: T) => void | Promise<void>
  /**
   * 成功副作用 — **不受**「仍是当前文档」约束，在 `onSuccess` 之前调用。
   *
   * 用于回填与导航无关的全局状态：`docs` 列表里的状态字段、进度等。那些状态在用户
   * 切走之后**仍然是真的**，跳过它就会留下「后端已更新、列表还显示旧值」的矛盾，
   * 而且没有任何地方报错（用户要切去文档库重新拉一次列表才会看到变化）。
   *
   * ⚠️ 改预览区的内容**不要**放这里 —— 那属于「用户还看着这篇吗」，是 `onSuccess`。
   * 两者分开的理由：同一件事写在一处，就没法让一半受约束一半不受。
   */
  onSuccessAlways?: (result: T) => void | Promise<void>
  /** 任务开始前清错（对应原 setError(''); setImportError('')） */
  onStart?: () => void
  /** 失败副作用（对应原 setError(msg); addToast(msg, 'error')） */
  onFail?: (msg: string) => void
}

export function useTaskRunner() {
  const setProcessing = useStore(s => s.setProcessing)
  const runTask = useCallback(async <T,>(opts: RunTaskOptions<T>) => {
    const { taskKey, taskInfo, prepareAbort, run } = opts
    // retranscribe/process 的 key 首位即 docId（UUID，不含 __）
    const capturedDocId = taskKey.split('__')[0]
    const isSame = () => capturedDocId === useStore.getState().currentDocId
    const signal = prepareAbort(taskKey)
    opts.onStart?.()
    setProcessing(taskKey, taskInfo)
    try {
      const result = await run(signal)
      await opts.onSuccessAlways?.(result)
      if (!isSame()) return
      await opts.onSuccess?.(result)
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return  // 用户取消，静默
      if (!isSame()) return
      opts.onFail?.(getErrorMessage(e))
    } finally {
      setProcessing(taskKey, null)  // 前缀树形删除清理
    }
  }, [setProcessing])
  return { runTask }
}
