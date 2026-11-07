/**
 * useStartTranscription — 「把这篇的正文转录出来」（2026-11-07）。
 *
 * ## 为什么要有这个 hook
 *
 * 这件事有**两个入口**：
 *
 *   1. 生成面板的「⟳ 重新转录」按钮（GenPanel）
 *   2. 预览区的「待转录」卡片（只收纳的文档点进去就能看到）
 *
 * 两条路要做的成功副作用**完全相同**：重拉转录正文 → 回填预览区 → 把 `docs` 里这条的
 * `transcribeState` 改回 `'done'` → 重载已有产物。其中第三条是新增的、最容易漏的：
 * 漏了它，后端已经写回真正文、预览区也显示了真正文，**列表徽章还挂着「待转录」**，
 * 直到用户切去文档库重新拉一次列表。两处信息公开矛盾，而没有任何地方报错。
 *
 * 所以实现只留一份，两个入口各传自己的「产物重载」回调进去。
 *
 * ## 不新建转录端点
 *
 * 走的就是 `POST /api/transcripts/{id}/retranscribe`（`api/transcripts.ts::retranscribe`）。
 * 「待转录」与「重新转录」在后端是**同一个动作**：按 `source_url` / `source_file_path`
 * 重建输入、跑 adapter、覆盖 rawText。差别只在用户看到的那句话。
 *
 * ⚠️ 失败时后端**不写盘**、`transcribeState` 保持 `pending`，文档原样留在库里等重试
 * —— 这正是计划里「刻意不加 `failed` 状态」的那条判据。
 */
import { useCallback } from 'react'
import { useStore } from '../store'
import { registerAbort } from '../api/taskManager'
import { getTranscript, retranscribe } from '../api/client'
import { useTaskRunner } from './useTaskRunner'

/**
 * 转录成功之后要做的两件事：回填正文、把列表里该条改成 `'done'`。
 *
 * 抽成模块级函数是因为它有**三个消费方**：单篇的 `start`（本文件）、待转录卡片、
 * 以及批量转录队列（`useBatchTranscribe`）。批量那条自己排队列（它要三态结果与
 * `failTask` 的重试条目），但「转录完了回填什么」与单篇共用这一份 —— 同一件事写在
 * 三处，失效形态就是「其中一条路忘了回填」。
 *
 * ⚠️ 回填正文仍要判断 `currentDocId`：转录可能跑几分钟，期间用户完全可能切走。
 * 那一次判断属于「该不该改预览区」，与「该不该改列表」是两件事，不能合并。
 * 调用方因此把它挂在 `runTask` 的 `onSuccessAlways` 上（不受导航约束），
 * 内部这行判断才管预览区。
 */
export async function applyTranscriptionResult(docId: string): Promise<void> {
  const tx = await getTranscript(docId)
  if (!tx) return
  const s = useStore.getState()
  if (s.currentDocId === docId) s.setCurrentRawText(tx.rawText)
  s.setDocs(s.docs.map(d => (d.id === docId ? { ...d, transcribeState: 'done' as const } : d)))
}

export interface StartTranscriptionOptions {
  /**
   * 转录成功后重载产物（笔记 / 整理版 / 切换视图）。**GenPanel 传它原有那一段**，
   * 预览区不传。抛错会被当作任务失败处理。
   */
  onProducts?: (docId: string) => void | Promise<void>
  /** 开始前清错（GenPanel 的红框 / 导入错误框） */
  onStart?: () => void
  /** 失败回调 —— 由调用方决定显示在哪（GenPanel 是红框，预览区是卡片内文字） */
  onError?: (msg: string) => void
  /** 成功提示文案（面板里说「重新转录完成」更准确，卡片里说「转录完成」） */
  successToast?: string
}

export function useStartTranscription() {
  const { runTask } = useTaskRunner()
  // 与 `GenPanel.createAbort` 同一形态：`registerAbort` 收的是 controller，不是 signal。
  // ⚠️ 不把这个函数提到模块级 —— 它只是「造一个已登记的 controller」，两处各一份不构成
  // 真相源分叉（没有判据、没有默认值）；真正需要同源的「转录完做什么」在下面。
  const prepareAbort = useCallback((taskKey: string) => {
    const ctrl = new AbortController()
    registerAbort(taskKey, ctrl)
    return ctrl.signal
  }, [])

  const start = useCallback(
    async (docId: string, opts: StartTranscriptionOptions = {}) => {
      await runTask({
        // key 粒度 `${docId}__${kind}`：与生成/图片处理互不覆盖，
        // 且 `useTaskRunner` 靠 key 首段做 stale 检查（UUID 不含 `__`）
        taskKey: `${docId}__retranscribe`,
        taskInfo: { kind: 'retranscribe' },
        prepareAbort,
        onStart: opts.onStart,
        run: (signal) => retranscribe(docId, signal),
        onSuccessAlways: async (result) => {
          if ('error' in result) {
            // 抛出去交给 `runTask` 的 catch → `onFail`，别在这里再写一遍错误文案
            throw new Error(result.error)
          }
          // 列表回填与「用户还在看这篇吗」无关：转录可能跑几分钟，切走是常态，
          // 而 `docs` 里那条的状态在切走之后依然是真的。
          await applyTranscriptionResult(docId)
        },
        onSuccess: async () => {
          await opts.onProducts?.(docId)
          useStore.getState().addToast(opts.successToast || '转录完成')
        },
        onFail: (msg) => {
          opts.onError?.(msg)
          if (!opts.onError) useStore.getState().addToast(msg, 'error')
        },
      })
    },
    [runTask, prepareAbort],
  )

  return { start }
}
