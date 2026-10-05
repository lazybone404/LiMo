import { useEffect, useState } from 'react'
import { useStore } from '../../store'
import { getMarkdown, getErrorMessage } from '../../api/client'
import { startEpubInterpret, getEpubTask, cancelEpubInterpret, type EpubTaskStatus } from '../../api/epub'

/**
 * EPUB 一键解读入口 — GenPanel 生成区（epub 专属，替代 GenerateOptions + 生成按钮）。
 *
 * 三态 UI：
 *   - running=true                     → 进度条（阶段1「第 X/Y 章 + 当前章名」/ 阶段2「归并中」）+ 取消
 *   - stage != done（有任务文件未完成）→ 「继续上次解读（还剩 Y 章）」按钮
 *   - stage == done 或 null            → 「一键解读」按钮 + 深度开关
 *
 * 进度不走 SSE：启动后 2s 轮询 getEpubTask 驱动；完成后刷新 notes/polished 到预览并跳 polished。
 * 取消走后端软取消（POST cancel），不挂 abortController。
 */
export function EpubInterpretPanel({ docId, modelId }: { docId: string; modelId: number | null }) {
  const addToast = useStore(s => s.addToast)
  const [deep, setDeep] = useState(false)
  const [task, setTask] = useState<EpubTaskStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  const isSameDoc = () => docId === useStore.getState().currentDocId

  // 打开文档时加载任务状态（三态判定起点）
  useEffect(() => {
    let cancelled = false
    setTask(null); setStarting(false); setCancelling(false)
    getEpubTask(docId).then(t => { if (!cancelled) setTask(t) }).catch(() => { /* 后端暂不可达，保持一键解读态 */ })
    return () => { cancelled = true }
  }, [docId])

  // running 时 2s 轮询驱动进度；结束（done/取消/中断）时停止
  useEffect(() => {
    if (!task?.running) return
    let stopped = false
    const timer = setInterval(async () => {
      let t: EpubTaskStatus
      try {
        t = await getEpubTask(docId)
      } catch { return }  // 后端暂不可达，下轮再试
      if (stopped || !isSameDoc()) { clearInterval(timer); return }
      setTask(t)
      if (!t.running) {
        clearInterval(timer)
        setCancelling(false)
        if (t.stage === 'done') {
          handleDone(t)
        }
        // 取消/中断 → 保留落盘进度，UI 回到「继续上次解读」
      }
    }, 2000)
    return () => { stopped = true; clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.running, docId])

  /** 任务完成：刷新 notes/polished 到预览 + 跳 polished tab + Toast（含失败章提示） */
  const handleDone = (t: EpubTaskStatus) => {
    Promise.all([getMarkdown(docId, 'notes'), getMarkdown(docId, 'polished')]).then(([notes, polished]) => {
      if (!isSameDoc()) return
      const st = useStore.getState()
      if (notes) {
        st.setCurrentContent(notes.content)
        st.setMarkdownMeta({ id: notes.id, style: notes.style, mode: notes.mode, version: notes.version, edited: !!notes.edited })
        const prev = st.markdownVersions
        st.setMarkdownVersions([...(notes.versions || []), ...prev.filter(v => v.mode !== 'notes')])
      }
      if (polished?.content) {
        st.setCurrentPolishedContent(polished.content)
        const prev = st.markdownVersions
        st.setMarkdownVersions([...(polished.versions || []), ...prev.filter(v => v.mode !== 'polished')])
      }
      // 解读完成 → 新产物无 edited → 标记自动清除（方案 5.3）
      st.setMarkdownEdited('notes', false)
      st.setMarkdownEdited('polished', false)
      // 产物已落盘 → 补记产物位（不补则切走再切回时 `modes` 还说「没有产物」，
      // 预览区一个请求都不发 → 刚解读完的笔记不可见。见 `DocEntry.modes`）
      if (notes) st.markArtifact(docId, 'notes')
      if (polished) st.markArtifact(docId, 'polished')
    }).catch(() => {})
    if (!isSameDoc()) return
    const failed = t.chapters_failed.length
    if (failed > 0) {
      addToast(`一键解读完成，${failed} 章解读失败已跳过（整理版原文中已标注）`, 'error')
    } else {
      addToast('一键解读完成', 'success')
    }
    useStore.getState().setActiveView('polished')
  }

  const handleStart = async () => {
    if (starting) return
    // 覆盖保护：解读/继续上次/重跑都会归并覆盖 notes/polished → 已手动编辑则两态 confirm（方案 5.2）
    const ed = useStore.getState().markdownEdited
    if (ed.notes || ed.polished) {
      if (!window.confirm('重新解读将覆盖你手动编辑的内容（生成版自动保留为上一版可回退），继续？')) return
    }
    setStarting(true)
    try {
      const res = await startEpubInterpret(docId, { model_id: modelId ?? undefined, deep })
      // AppError（recoverable，HTTP 200）会以 {code, message} 返回而非 {started}
      if (res && 'code' in res && 'message' in (res as any)) {
        addToast((res as any).message, 'error')
        return
      }
      const t = await getEpubTask(docId)
      if (isSameDoc()) setTask(t)
    } catch (e) {
      addToast(getErrorMessage(e), 'error')
    } finally {
      setStarting(false)
    }
  }

  const handleCancel = async () => {
    if (cancelling) return
    setCancelling(true)
    try {
      await cancelEpubInterpret(docId)
      addToast('正在停止解读，已解读的章节进度已保存…', 'info')
      // 当前章 LLM 调用无 chunk 返回时可能等一小段，轮询会直到 running=false
    } catch (e) {
      setCancelling(false)
      addToast(getErrorMessage(e), 'error')
    }
  }

  const running = !!task?.running
  const canResume = !running && !!task?.stage && task.stage !== 'done'
  const doneCount = task?.chapters_done?.length || 0
  const total = task?.total_chapters || 0
  const failedCount = task?.chapters_failed?.length || 0
  const percent = !total ? 0 : task?.stage === 'summary' ? 100 : Math.min(Math.round((doneCount / total) * 100), 99)

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
            <span className="text-dimmed">
              {task?.stage === 'summary' ? '阶段 2：全书总结归并中…' : `阶段 1：逐章解读 ${doneCount}/${total}`}
            </span>
            {failedCount > 0 && <span className="text-warning">{failedCount} 章失败已跳过</span>}
          </div>
          {task?.stage === 'chapters' && task.current_title && (
            <div className="text-[11px] text-disabled truncate">当前：{task.current_title}</div>
          )}
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
          className="w-full py-2.5 bg-accent text-white text-[15px] font-medium rounded-lg hover:bg-accent-hover active:scale-[0.97] transition-all disabled:opacity-40">
          {starting ? '启动中…' : canResume ? `继续上次解读（还剩 ${task?.remaining_chapters ?? 0} 章）` : '一键解读'}
        </button>
      )}
    </div>
  )
}
