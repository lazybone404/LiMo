/**
 * 批量生成（2026-11-07）。
 *
 * 文档库里选中若干篇，确认后逐个调 `GET /generate/{id}/stream`。已经有过产物的篇
 * 走的是「重新生成」，没有产物的篇走「首次生成」——两者在接口上是同一个请求，
 * 后端本来就按 `v{max+1}` 新建版本，**不会覆盖已编辑的那一版**。所以这里不需要分叉，
 * 分叉只体现在对话框的文案上。
 *
 * ## 为什么自己排队列，不用 `useTaskRunner`
 *
 * `useTaskRunner` 的成功回调带 `isSame()` 判断（「跑完的这篇还是用户正在看的那一篇吗」）。
 * 单篇场景下这个判断是对的；批量场景下它会把**每一篇**都判成「不是当前文档」——
 * 你正在看第 5 篇，队列在跑第 2 篇，于是刷新产物的那一步被跳过，跑完了列表上
 * 那篇仍然显示「没有产物」。这与 `useBatchTranscribe` 是同一个理由。
 *
 * ## 不写流式缓冲
 *
 * `PreviewArea` 在 `processingTasks` 里有这个任务时直接渲染 `ProcessingPlaceholder`，
 * 正文要等任务清掉才显示。所以批量队列表要做的只有「建任务、更新进度、清任务」，
 * 不必往 `streamBuffers` 里塞半截正文——塞了也没人读，反而要给每篇分配两个缓冲键
 * 并在异常路径上一一清理。
 *
 * ## 三条与批量导入、批量转录相同的纪律
 *
 * 1. **串行**：一次只跑一篇。并发跑会同时吃满云端并发额度，而后端的降级与超时
 *    都是按「一次生成」设计的。
 * 2. **`open()` 零请求**：确认对话框只是把清单和要花的调用摆出来。
 * 3. **单篇失败不影响后面的**：每一项各自标红，最后汇总成一条 toast。
 *
 * ## 参数只有一份来源
 *
 * 每篇的请求参数由 `genOptions.ts::buildGenOptions` 拼装，与 GenPanel 单篇生成
 * 走的是同一份实现。抄一份的话，两边迟早分叉，而分叉的表现是「同一篇文档，
 * 单篇生成与批量生成产出的东西不一样」，界面上完全看不出来。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { useStore } from '../store'
import { streamGenerate, getMarkdown, listEnabledModels, getErrorMessage } from '../api/client'
import { registerAbort } from '../api/taskManager'
import { buildGenOptions, supportsCapability } from '../components/gen-panel/genOptions'
import { loadGenPrefs } from '../components/gen-panel/genPrefs'
import { getTypeMeta, type TypeMeta } from '../components/gen-panel/constants'
import { toMarkdownMeta } from '../lib/markdownMeta'
import { clearQueue, markQueueIdle, markQueueRunning, saveQueue } from '../lib/batch-queue'

export type GeneratePhase = 'confirm' | 'running' | 'done'

export type GenerateStatus = 'queued' | 'running' | 'done' | 'failed' | 'skipped'

export interface GenerateItem {
  id: string
  title: string
  sourceType: string
  /**
   * 这篇已经有产物 → 本次是「重新生成」。
   *
   * 由调用方从 `DocEntry.modes` 算出：`undefined`（未知）按「生成」显示——把不知道
   * 说成确定，用户会以为自己在重跑一篇从没生成过的文档。
   */
  willRegenerate: boolean
  status: GenerateStatus
  /** 失败原因。成功与排队中为空串。 */
  message: string
}

/** 单篇的终态。`aborted` 单独一个值：取消不是失败，不该计入失败数。 */
type RunOutcome =
  | { type: 'ok' }
  | { type: 'error'; message: string }
  | { type: 'aborted' }

/** 生成只产出这两种产物；`selectedCore` 之外的值（若有）在这里被忽略。 */
type Mode = 'notes' | 'polished'

function modesOf(core: string[]): Mode[] {
  return core.filter((m): m is Mode => m === 'notes' || m === 'polished')
}

/**
 * 这一篇真正要生成的产物名。
 *
 * `genPrefs` 是跨类型持久化的，而产物名并不跨类型通用：Excel 的唯一产物在
 * `coreProducts` 里叫 `both`（界面上显示「数据摘要」），合成文档只有 `notes`。
 * 直接拿 `selectedCore` 拼 `mode` 会把别的类型的选择送给这一篇。
 *
 * 取交集；交集为空时退回该类型的全部产物 —— 与 `GenPanel` 在类型切换后重置
 * `selectedCore` 的判据相同。图片类文档的 `coreProducts` 是空数组，返回空数组
 * 由调用方跳过这一篇（它本来就只能做图片处理，不走生成）。
 */
function coreFor(typeMeta: TypeMeta, wanted: string[]): string[] {
  const valid = typeMeta.coreProducts.map(p => p.name)
  if (!valid.length) return []
  const picked = valid.filter(n => wanted.includes(n))
  return picked.length ? picked : valid
}

/**
 * 生成成功后刷新产物。
 *
 * 两件事分开做：
 *
 * 1. **`markArtifact` 无条件调**（不管用户看的是哪一篇）。它写的是 `docs` 里
 *    这篇的 `modes`，也就是「切回这篇时要不要去拉这个产物」的判据。漏了它，
 *    「生成完 → 切走 → 切回」会因 `modes` 缺项而一个请求都不发，笔记看起来凭空消失。
 * 2. **预览区只在 `currentDocId` 就是这篇时刷新**（与单篇 `refreshSaved` 同款）。
 *    队列可能跑几分钟，期间用户完全可能切走；把别的文档的正文塞进预览区是最坏的一种
 *    「成功」。
 */
async function refreshArtifacts(id: string, modes: Mode[]): Promise<void> {
  for (const mode of modes) {
    let md: Awaited<ReturnType<typeof getMarkdown>> = null
    try {
      md = await getMarkdown(id, mode)
    } catch {
      // 拉取失败只是预览没刷新，产物已经在盘上。不把整篇生成报成失败。
      continue
    }
    if (!md) continue
    useStore.getState().markArtifact(id, mode)
    if (useStore.getState().currentDocId !== id) continue

    const s = useStore.getState()
    if (mode === 'notes') {
      s.setMarkdownEdited('notes', !!md.edited)
      s.setMarkdownMeta(toMarkdownMeta(md))
      s.setCurrentContent(md.content)
      // 版本列表要按 mode 合并：notes 与 polished 各自从 v1 起，整体替换会把
      // 另一个产物的版本项抹掉（版本切换器随即显示不全）。
      s.setMarkdownVersions([...(md.versions || []), ...s.markdownVersions.filter(v => v.mode !== 'notes')])
    } else {
      s.setMarkdownEdited('polished', !!md.edited)
      s.setCurrentPolishedContent(md.content)
      s.setMarkdownVersions([...(md.versions || []), ...s.markdownVersions.filter(v => v.mode !== 'polished')])
    }
  }
}

export function useBatchGenerate() {
  const setProcessing = useStore(s => s.setProcessing)
  const failTask = useStore(s => s.failTask)

  const [items, setItems] = useState<GenerateItem[] | null>(null)
  const [phase, setPhase] = useState<GeneratePhase>('confirm')
  /** 「在后台继续」：置真时对话框不渲染，队列继续跑，进度在任务面板与占位里看。 */
  const [background, setBackground] = useState(false)

  const cancelledRef = useRef(false)
  const ctrlRef = useRef<AbortController | null>(null)

  const patch = useCallback((id: string, next: Partial<GenerateItem>) => {
    setItems(prev => prev && prev.map(it => (it.id === id ? { ...it, ...next } : it)))
  }, [])

  /**
   * 队列快照落盘：刷新之后文档库读回这份记录，提示「还有 N 篇没生成完」。
   *
   * ⚠️ `items === null` 时**一个字节都不写**。挂载那一刻 items 就是 null，顺手写一份
   * 空快照会把上次留下的记录当场抹掉，恢复入口永远看不到。
   */
  useEffect(() => {
    if (phase === 'done') {
      clearQueue('generate')
      markQueueIdle('generate')
      return
    }
    if (items && items.length) saveQueue('generate', items)
  }, [items, phase])

  /** 打开确认对话框。零请求。 */
  const open = useCallback((docs: Omit<GenerateItem, 'status' | 'message'>[]) => {
    cancelledRef.current = false
    setBackground(false)
    setItems(docs.map(d => ({ ...d, status: 'queued', message: '' })))
    setPhase('confirm')
  }, [])

  const reset = useCallback(() => {
    // 关掉对话框 = 用户不要这一轮了。快照一并清掉，否则下次打开文档库会提示恢复。
    clearQueue('generate')
    setItems(null)
    setPhase('confirm')
    setBackground(false)
  }, [])

  /**
   * 跑一篇，返回终态。
   *
   * `streamGenerate` 返回 `void`，三条路都不保证走到：`onDone` / `onError` 各一条，
   * 而用户取消时它的 `.catch` 只判断 `AbortError` 就 `return`，**一个回调都不发**。
   * 只等前两条的话，取消之后这个 Promise 永不落地，队列卡在当前篇。
   * 故这里挂 `abort` 监听作为第三条出口。
   */
  const runOne = useCallback(
    (
      id: string,
      title: string,
      options: ReturnType<typeof buildGenOptions>,
      isPolishedOnly: boolean,
      signal: AbortSignal,
    ): Promise<RunOutcome> =>
      new Promise((resolve) => {
        let settled = false
        const settle = (outcome: RunOutcome) => {
          if (settled) return
          settled = true
          resolve(outcome)
        }

        const taskKey = `${id}__generate`

        signal.addEventListener('abort', () => settle({ type: 'aborted' }))

        // 进度累加与单篇同款：每篇各记一份，节流 300 字符更新一次。
        // 任务已被清掉（取消后迟到的 chunk）就不复活它。
        const chars: Record<Mode, number> = { notes: 0, polished: 0 }
        const bumpProgress = (mode: Mode, text: string) => {
          chars[mode] += text.length
          const cur = useStore.getState().processingTasks[taskKey]
          if (!cur) return
          const curChars = cur.progress?.[mode] || 0
          if (cur.mode !== mode || chars[mode] - curChars >= 300) {
            useStore.getState().setProcessing(taskKey, {
              kind: 'generate', mode, title,
              progress: { notes: chars.notes, polished: chars.polished },
            })
          }
        }

        streamGenerate(id, options, {
          onChunk(text, mode) {
            if (mode === 'notes' || mode === 'polished') bumpProgress(mode, text)
            else bumpProgress(isPolishedOnly ? 'polished' : 'notes', text)
          },
          // 思考链与阶段提示（参考文件压缩）不进批量队列的进度显示：
          // 任务面板那一行只放得下一句状态，写多了反而盖住「已生成 N 字符」。
          onStage() {},
          onReasoning() {},
          // 部分产物失败（只成功了一个）不终止这一篇，产物仍算成功。
          onWarning() {},
          // 追问建议落在盘上的旁路文件里，`getMarkdown` 会随 meta 带回来
          // （`toMarkdownMeta` 已含 `followUps`），这里没有要额外做的事。
          onFollowUp() {},
          onDone() { settle({ type: 'ok' }) },
          onError(message) { settle({ type: 'error', message }) },
          onCancelled() { settle({ type: 'aborted' }) },
        }, signal)
      }),
    [],
  )

  /** 开始逐个生成。 */
  const confirm = useCallback(async () => {
    const list = items
    if (!list || !list.length) return

    // 一个产物都没勾时，后端拿到的是空 `mode`，报出来的话很难对上「我没勾产物」。
    // 这是纯前端判据，直接说清楚。
    const prefs = loadGenPrefs()
    if (!prefs.selectedCore.length) {
      useStore.getState().addToast('没有勾选任何产物，请先在生成面板选择要生成什么', 'error')
      return
    }

    // 能力位查一次就够：它问的是「当前选中的模型支持什么」，与哪一篇无关。
    // 查不到时按「不支持」处理 —— 少传两个参数，后端走默认值，生成照常进行。
    const models = await listEnabledModels().catch(() => [])
    const reasoningSupported = supportsCapability(models, prefs.selectedModelId, 'reasoning')
    const hasDocumentModel = supportsCapability(models, prefs.selectedModelId, 'document')
    const caps = useStore.getState().capabilities?.genOptions ?? {}

    cancelledRef.current = false
    // 本页此刻在跑：面板折叠后重新展开时，恢复入口不能再提供这一批（会跑第二遍）。
    markQueueRunning('generate')
    setPhase('running')

    let done = 0
    let failed = 0

    for (const item of list) {
      if (cancelledRef.current) {
        patch(item.id, { status: 'skipped', message: '已取消' })
        continue
      }

      // 参数按这一篇的类型现算：`typeMeta` 决定哪些格式项可用、要不要走 PDF 直传，
      // 也决定这一篇的产物名（Excel 叫 `both`，合成文档只有 `notes`）。
      const typeMeta = getTypeMeta(item.sourceType, caps)
      const core = coreFor(typeMeta, prefs.selectedCore)
      if (!core.length) {
        // 图片类文档没有可生成的产物。调用方本该在打开对话框前就排除它们，
        // 这里再拦一次是为了让「排除了但漏了」表现为一行说明而不是一次错误的请求。
        patch(item.id, { status: 'skipped', message: '这类文档没有可生成的产物' })
        continue
      }
      const itemPrefs = { ...prefs, selectedCore: core }
      const options = buildGenOptions(itemPrefs, {
        typeMeta,
        reasoningSupported,
        hasDocumentModel,
      })
      const isPolishedOnly = core.length === 1 && core[0] === 'polished'

      const taskKey = `${item.id}__generate`
      const ctrl = new AbortController()
      ctrlRef.current = ctrl
      registerAbort(taskKey, ctrl)
      setProcessing(taskKey, { kind: 'generate', title: item.title })
      patch(item.id, { status: 'running', message: '' })

      let outcome: RunOutcome
      try {
        outcome = await runOne(item.id, item.title, options, isPolishedOnly, ctrl.signal)
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
      await refreshArtifacts(item.id, modesOf(core))
      patch(item.id, { status: 'done', message: '' })
      done++
    }

    setPhase('done')
    setBackground(false)
    // ⚠️ 这里必须显式清，不能只靠上面那个 effect：面板折叠时组件已卸载，`setPhase`
    // 不会带来一次渲染，effect 也就不跑 ——「收起对话框在后台跑完 → 重新展开」会看到
    // 一条指向已跑完队列的恢复横幅。
    clearQueue('generate')
    markQueueIdle('generate')
    const parts = [`完成 ${done} 篇`]
    if (failed > 0) parts.push(`失败 ${failed} 篇`)
    const skipped = list.length - done - failed
    if (skipped > 0) parts.push(`跳过 ${skipped} 篇`)
    useStore.getState().addToast(
      `批量生成${parts.join('，')}`,
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
