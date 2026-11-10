import { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import { getErrorMessage } from '../../api/client'
import { startEpubInterpret, getEpubTask, type EpubTaskStatus } from '../../api/epub'
import { isJobActive } from '../../store/jobsSlice'

/**
 * EPUB 一键解读入口 — GenPanel 生成区（epub 专属，替代 GenerateOptions + 生成按钮）。
 *
 * 三态 UI，与以前相同：
 *   - running                           → 进度条（文案来自 `job.message`）+ 取消
 *   - stage 未完成且没有活跃任务         → 「继续上次解读（还剩 Y 章）」
 *   - stage == done 或 null             → 「一键解读」按钮 + 深度开关
 *
 * ⚠️ **进度不再由本组件轮询**（原先 2s 一次 `getEpubTask`）。解读跑在后端任务队列里，
 * 于是本组件卸载（折叠侧栏、切到别的文档）不影响它；任务结束后由 `jobsSlice` 的
 * `settleVanished` 把产物接回预览（模式 #73：「卸载以后这份状态从哪读回来」）。
 * 本组件只订阅 `jobs` 里那条记录，并在它消失时重读一次落盘状态。
 */
export function EpubInterpretPanel({ docId, modelId }: { docId: string; modelId: number | null }) {
  const addToast = useStore(s => s.addToast)
  const cancelJobById = useStore(s => s.cancelJobById)
  const trackJob = useStore(s => s.trackJob)
  // 订阅而不是拥有：本组件卸载之后，这条记录仍然在 store 里（由 jobsSlice 轮询维护）
  const job = useStore(s => Object.values(s.jobs).find(
    j => j.kind === 'epub.interpret'
      && (j.payload.doc_id as string | undefined) === docId
      && isJobActive(j),
  ))
  const [deep, setDeep] = useState(false)
  const [task, setTask] = useState<EpubTaskStatus | null>(null)
  const [starting, setStarting] = useState(false)

  const running = !!job || !!task?.running

  // 打开文档时加载落盘状态（「继续上次解读」的起点）
  useEffect(() => {
    let cancelled = false
    setTask(null); setStarting(false)
    getEpubTask(docId)
      .then(t => { if (!cancelled) setTask(t) })
      .catch(() => { /* 后端暂不可达，保持一键解读态 */ })
    return () => { cancelled = true }
  }, [docId])

  // 任务结束（那条 job 从列表里消失）时重读一次：按钮要变成「继续上次」或「一键解读」。
  // 不做定时轮询 —— 结束只会由 `jobs` 的变化通知，轮询是 jobsSlice 的事。
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) {
      getEpubTask(docId).then(setTask).catch(() => {})
    }
    wasRunning.current = running
  }, [running, docId])

  const handleStart = async () => {
    if (starting) return
    // 覆盖保护：解读/继续上次/重跑都会归并覆盖 notes/polished → 已手动编辑则两态 confirm
    const ed = useStore.getState().markdownEdited
    if (ed.notes || ed.polished) {
      if (!window.confirm('重新解读将覆盖你手动编辑的内容（生成版自动保留为上一版可回退），继续？')) return
    }
    setStarting(true)
    try {
      const res = await startEpubInterpret(docId, { model_id: modelId ?? undefined, deep })
      // 幂等命中（同一文档已在解读）时拿回的就是那条任务 —— 不报错，直接接上
      trackJob(res.job)
      if (!res.created) addToast('该文档正在解读中，已接到那条任务上', 'info')
    } catch (e) {
      addToast(getErrorMessage(e), 'error')
    } finally {
      setStarting(false)
    }
  }

  const handleCancel = () => {
    if (!job) return
    // 取消的软语义不变：置旗标、当前章结束后停、已落盘章节保留
    void cancelJobById(job.id)
    addToast('正在停止解读，已解读的章节进度已保存…', 'info')
  }

  const canResume = !running && !!task?.stage && task.stage !== 'done'
  const percent = job?.progress != null ? Math.min(Math.round(job.progress * 100), 99) : 0
  const cancelling = !!job?.cancel_requested

  return (
    <div className="space-y-2">
      {/* 深度开关 — 默认骨架（省 ~70% token），开 = 输入全文 */}
      <label className="flex items-center justify-between cursor-pointer">
        <span className="text-[12px] text-dimmed">深度解读（输入全文，更全面但更费 token）</span>
        <input type="checkbox" checked={deep} onChange={e => setDeep(e.target.checked)} disabled={running}
          className="w-4 h-4 accent-accent" />
      </label>

      {running ? (
        <div className="space-y-1.5 bg-surface rounded-[10px] p-3 border border-border-light">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-dimmed truncate">{job?.message || '解读进行中…'}</span>
            {job?.progress != null && <span className="text-disabled shrink-0">{percent}%</span>}
          </div>
          <div className="h-1.5 bg-surface-hover rounded-full overflow-hidden">
            <div className="h-full bg-accent rounded-full transition-all duration-500" style={{ width: `${percent}%` }} />
          </div>
          <button onClick={handleCancel} disabled={cancelling}
            className="w-full py-1.5 text-[11px] font-medium text-error bg-error/10 rounded-lg hover:bg-error/20 disabled:opacity-40 transition-colors">
            {cancelling ? '正在停止…' : '取消'}
          </button>
        </div>
      ) : (
        <button onClick={handleStart} disabled={starting}
          className="w-full py-2.5 bg-accent text-on-accent text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
          {starting ? '启动中…' : canResume ? `继续上次解读（还剩 ${task?.remaining_chapters ?? 0} 章）` : '一键解读'}
        </button>
      )}
    </div>
  )
}
