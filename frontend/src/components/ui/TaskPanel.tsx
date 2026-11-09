import { useEffect, useRef, useState, useCallback } from 'react'
import { useStore, type DocEntry } from '../../store'
import { cancelTask } from '../../api/taskManager'
import {
  X, GripHorizontal, FileText, Image, RefreshCw, ListTodo, Check, AlertTriangle,
  Download, RotateCcw, Package, Layers,
} from 'lucide-react'
import { useElapsed } from '../../hooks/useElapsed'
import { isUpdateBusy } from '../../store/updateSlice'
import { isJobActive } from '../../store/jobsSlice'
import type { JobInfo } from '../../api/jobs'
import { formatSize } from '../../lib/format'

/**
 * 已用时。抽成组件而不是在 `entries.map` 里直接调 hook —— map 回调里调 hook
 * 违反 hook 规则（条目数会变）。`startedAt` 缺失时不渲染。
 */
function Elapsed({ startedAt }: { startedAt?: number }) {
  const text = useElapsed(startedAt)
  return text ? <span> · 已用 {text}</span> : null
}

const KIND_META: Record<string, { label: string; Icon: typeof FileText; color: string; bg: string }> = {
  import:     { label: '导入',    Icon: RefreshCw, color: 'text-accent',   bg: 'bg-accent/10' },
  retranscribe:{ label: '重转录', Icon: RefreshCw, color: 'text-warning', bg: 'bg-warning/10' },
  generate:   { label: '生成',    Icon: FileText,   color: 'text-indigo-500', bg: 'bg-indigo-50' },
  'process': { label: '图片处理', Icon: Image, color: 'text-success', bg: 'bg-success/10' },
}

export function TaskPanel() {
  const tasks = useStore(s => s.processingTasks)
  const docs = useStore(s => s.docs)
  const open = useStore(s => s.taskPanelOpen)
  const setOpen = useStore(s => s.setTaskPanelOpen)
  const setProcessing = useStore(s => s.setProcessing)
  // 应用更新是**另一种「进行中的长任务」**：不挂在某篇文档上，但同样要几分钟、
  // 同样会在用户离开设置页之后继续跑。所以它在这个面板里占一行。
  const update = useStore(s => s.update)
  // 后端任务（插件依赖安装等）。状态住在后端 `jobs.db`，这里只是镜像。
  const jobs = useStore(s => s.jobs)
  const cancelJobById = useStore(s => s.cancelJobById)

  // 拖动状态
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  const dragging = useRef(false)
  const dragStart = useRef({ x: 0, y: 0 })
  const posRef = useRef(pos)
  posRef.current = pos

  const entries = Object.entries(tasks)
  const showUpdate = isUpdateBusy(update)
  // 只显示「进行中 + 失败」：后端列表已经这么过滤，但 `trackJob` 可能塞进来一条
  // 提交瞬间就结束的任务，那行不该赖在面板上等一次永远不会发生的轮询。
  const jobList = Object.values(jobs).filter(j => isJobActive(j) || j.status === 'failed')
  // 「有东西在跑」的总行数 —— 自动关闭、头部角标、空态三处都看它。
  // ⚠️ 只看 `entries.length` 时，更新下载中面板会在 1.5 秒后自己关掉，
  // 而下载还在继续 —— 正好把「看不见但没停」这个毛病又做了一遍。
  // 三个来源（文档任务 / 应用更新 / 后端任务）必须**都算进来**，否则同一条
  // 三个来源里有任何一个漏了，都会重现「自动关闭 + 任务还在跑」。
  const rowCount = entries.length + (showUpdate ? 1 : 0) + jobList.length
  // 安装会让应用退出，正在跑的生成 / 转录 / 后端任务会断在半路（没有断点续传）。
  const anyTaskRunning = entries.some(([, t]) => t.status === 'running')
    || jobList.some(isJobActive)
  const hasCancellable = entries.length > 0 || jobList.some(isJobActive)

  // 拖动
  const onDragStart = useCallback((e: React.MouseEvent) => {
    dragging.current = true
    dragStart.current = { x: e.clientX - posRef.current.x, y: e.clientY - posRef.current.y }
    e.preventDefault()
  }, [])
  useEffect(() => {
    if (!open) return
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      setPos({ x: e.clientX - dragStart.current.x, y: e.clientY - dragStart.current.y })
    }
    const onUp = () => { dragging.current = false }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [open])

  // 无任务时自动关闭
  useEffect(() => {
    if (open && rowCount === 0) {
      const t = setTimeout(() => setOpen(false), 1500)
      return () => clearTimeout(t)
    }
  }, [open, rowCount, setOpen])

  // 默认显示位置：右上角
  useEffect(() => {
    if (open) setPos({ x: window.innerWidth - 420, y: 60 })
  }, [open])

  if (!open) return null

  const getTitle = (docId: string, task: { title?: string }) =>
    task.title || docs.find((d: DocEntry) => d.id === docId)?.title || docId.slice(0, 16) + '…'

  return (
    <div
      ref={panelRef}
      style={{ left: pos.x, top: pos.y }}
      className="fixed z-[9999] w-[380px] max-w-[92vw] rounded-2xl border border-border-light bg-elevated/95 backdrop-blur-xl shadow-2xl shadow-black/5 overflow-hidden select-none"
    >
      {/* 头部 — 拖动手柄 */}
      <header
        onMouseDown={onDragStart}
        className="flex items-center justify-between px-4 py-2.5 border-b border-border-light bg-surface cursor-grab active:cursor-grabbing"
      >
        <div className="flex items-center gap-2">
          <GripHorizontal size={12} className="text-muted" />
          <div className="flex items-center justify-center w-6 h-6 rounded-[8px] bg-accent/10">
            <ListTodo size={13} className="text-accent" />
          </div>
          <span className="text-[12px] font-semibold text-foreground">任务</span>
          {rowCount > 0 && (
            <span className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[9px] font-bold text-on-accent bg-accent rounded-full">
              {rowCount}
            </span>
          )}
        </div>
        <button onClick={() => setOpen(false)}
          className="p-1 rounded-md hover:bg-surface-hover text-muted transition-colors">
          <X size={13} />
        </button>
      </header>

      {/* 列表 */}
      {rowCount === 0 ? (
        <div className="py-10 text-center">
          <Check size={20} className="text-disabled mx-auto mb-2 opacity-30" />
          <p className="text-[12px] text-disabled">无进行中的任务</p>
        </div>
      ) : (
        <div className="max-h-[300px] overflow-y-auto">
          {showUpdate && <UpdateTaskRow anyTaskRunning={anyTaskRunning} />}
          {jobList.map(job => <JobTaskRow key={job.id} job={job} />)}
          {entries.map(([docId, task]) => {
            const meta = KIND_META[task.kind] || { label: task.kind, Icon: FileText, color: 'text-dimmed', bg: 'bg-surface-hover' }
            const title = getTitle(docId, task)
            const isFailed = task.status === 'failed'
            return (
              <div key={docId}
                className={`group flex items-center gap-3 px-4 py-2.5 border-b border-black/[0.04] transition-colors last:border-b-0 ${isFailed ? 'bg-error/[0.04]' : 'hover:bg-surface-hover'}`}>
                <div className={`shrink-0 flex items-center justify-center w-8 h-8 rounded-xl ${isFailed ? 'bg-error/10' : meta.bg}`}>
                  {isFailed ? <AlertTriangle size={15} className="text-error" /> : <meta.Icon size={15} className={meta.color} />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] font-medium text-foreground truncate leading-tight">{title}</p>
                  <p className="text-[11px] mt-0.5">
                    {isFailed ? (
                      <span className="text-error">{task.error || '未知错误'}</span>
                    ) : (
                      <span className="flex items-center gap-1 text-disabled">
                        <span className="inline-block w-1 h-1 rounded-full bg-success animate-pulse" />
                        {meta.label}{task.mode ? ` · ${task.mode}` : ''}
                        <Elapsed startedAt={task.startedAt} />
                      </span>
                    )}
                  </p>
                </div>
                {isFailed ? (
                  <button
                    onClick={() => setProcessing(docId, null)}
                    className="shrink-0 opacity-50 group-hover:opacity-100 p-1 rounded-md hover:bg-surface-hover text-disabled hover:text-foreground transition-all"
                    title="关闭">
                    <X size={14} />
                  </button>
                ) : (
                  <button
                    onClick={() => {
                      // P2-7：导入任务按完整 taskKey 精确取消（不再归一成 __import__ 前缀），并发导入只取消本条
                      const baseKey = docId.startsWith('__import__') ? docId : docId.includes('__') ? docId.slice(0, docId.indexOf('__')) : docId
                      cancelTask(baseKey)
                      setProcessing(docId, null)
                    }}
                    className="shrink-0 opacity-50 group-hover:opacity-100 p-1 rounded-md hover:bg-error/10 text-disabled hover:text-error transition-all"
                    title="取消任务">
                    <X size={14} />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 底部 */}
      {hasCancellable && (
        <footer className="border-t border-border-light px-4 py-2 bg-surface flex justify-between items-center">
          <span className="text-[10px] text-disabled">拖拽移动</span>
          <button
            onClick={() => { entries.forEach(([id]) => {
              // P2-7：逐条精确取消（导入用完整 taskKey，不归一成 __import__ 前缀）
              const baseKey = id.startsWith('__import__') ? id : id.includes('__') ? id.slice(0, id.indexOf('__')) : id
              cancelTask(baseKey)
              setProcessing(id, null)
            }); jobList.forEach(job => {
              if (isJobActive(job)) void cancelJobById(job.id)
            }) }}
            className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-medium text-error hover:bg-error/10 rounded-md transition-colors">
            <AlertTriangle size={10} />
            取消全部
          </button>
        </footer>
      )}
    </div>
  )
}

/** 后端任务的图标。**中文名不在这里** —— `job.label` 由后端注册表下发。
 *  这张表只决定「用哪个图标、什么颜色」，不认识的 kind 走默认样式，文字仍然正确。 */
const JOB_META: Record<string, { Icon: typeof Package; color: string; bg: string }> = {
  'plugin.install': { Icon: Package, color: 'text-accent', bg: 'bg-accent/10' },
  synthesis: { Icon: Layers, color: 'text-indigo-500', bg: 'bg-indigo-50' },
}

/**
 * 后端任务（`jobs.db`）在任务面板里的那一行。
 *
 * 与文档任务的区别只在**动作**：失败的任务只能「忽略」（重试入口在它自己的页面，
 * 比如设置 → 插件那张卡片），因为重试要带该业务自己的参数与幂等键 —— 任务面板
 * 不认识「重新提交一个 plugin.install」意味着什么。
 */
function JobTaskRow({ job }: { job: JobInfo }) {
  const cancelJobById = useStore(s => s.cancelJobById)
  const dismissJobById = useStore(s => s.dismissJobById)

  const meta = JOB_META[job.kind] || { Icon: ListTodo, color: 'text-dimmed', bg: 'bg-surface-hover' }
  const active = isJobActive(job)
  const failed = job.status === 'failed'
  const queued = job.status === 'queued'
  const pct = active && job.progress !== null ? Math.round(job.progress * 100) : null
  // 一行说不清「是哪一个」的任务要补个目标：插件安装是插件名，合成是篇数
  // （合成那条 payload 里没有 name，只有 doc_ids）。
  const target = typeof job.payload?.name === 'string'
    ? job.payload.name
    : Array.isArray(job.payload?.doc_ids)
      ? `${job.payload.doc_ids.length} 篇`
      : ''

  return (
    <div
      data-job-task
      className={`group flex items-center gap-3 px-4 py-2.5 border-b border-black/[0.04] transition-colors last:border-b-0 ${failed ? 'bg-error/[0.04]' : 'hover:bg-surface-hover'}`}
    >
      <div className={`shrink-0 flex items-center justify-center w-8 h-8 rounded-xl ${failed ? 'bg-error/10' : meta.bg}`}>
        {failed
          ? <AlertTriangle size={15} className="text-error" />
          : <meta.Icon size={15} className={meta.color} />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[12px] font-medium text-foreground truncate leading-tight">
          {job.label}{target ? ` · ${target}` : ''}
        </p>
        <p className="text-[11px] mt-0.5 min-w-0">
          {failed ? (
            <span className="text-error">{job.error || '未知错误'}</span>
          ) : active ? (
            <span className="flex items-center gap-1 text-disabled">
              <span className="inline-block w-1 h-1 rounded-full bg-success animate-pulse" />
              {job.cancel_requested ? '正在取消…' : (job.message || (queued ? '排队中' : '进行中'))}
              {pct !== null ? ` ${pct}%` : ''}
              {!queued && <Elapsed startedAt={job.started_at ?? undefined} />}
            </span>
          ) : (
            <span className="text-disabled">{job.status === 'cancelled' ? '已取消' : '已完成'}</span>
          )}
        </p>
      </div>
      {active ? (
        <button
          onClick={() => { void cancelJobById(job.id) }}
          disabled={job.cancel_requested}
          title={job.cancel_requested ? '正在取消，当前步骤结束后停止' : '取消任务'}
          className="shrink-0 opacity-50 group-hover:opacity-100 disabled:opacity-30 disabled:cursor-not-allowed p-1 rounded-md hover:bg-error/10 text-disabled hover:text-error transition-all"
        >
          <X size={14} />
        </button>
      ) : (
        <button
          onClick={() => { void dismissJobById(job.id) }}
          title="关闭"
          className="shrink-0 opacity-50 group-hover:opacity-100 p-1 rounded-md hover:bg-surface-hover text-disabled hover:text-foreground transition-all"
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}

/**
 * 应用更新在任务面板里的那一行。
 *
 * 与文档任务的三处差别都是刻意的：
 * ① **没有取消** —— 下载走的是插件的整体 `download()`，没有中断点（见 `updater.rs`）。
 *    摆一个点了没反应的叉比不摆更糟。
 * ② 失败后的出路是**重新下载**，不是「关掉」了事；「关掉」只清 UI 状态。
 * ③ `ready` 时的动作是**重启安装**，它会关掉应用。所以有任务在跑时禁用并写明原因 ——
 *    这里不做二次确认：面板里它就一个按钮，误触概率低，而真会出事的场景（有任务在跑）
 *    已经由禁用挡住了。卡片那边有二次确认，是因为那一排按钮挨得更近。
 */
function UpdateTaskRow({ anyTaskRunning }: { anyTaskRunning: boolean }) {
  const update = useStore(s => s.update)
  const applyUpdate = useStore(s => s.applyUpdate)
  const startDownload = useStore(s => s.startDownload)
  const dismissUpdate = useStore(s => s.dismissUpdate)

  const downloading = update.phase === 'downloading'
  const ready = update.phase === 'ready'
  const installing = update.phase === 'installing'
  const failed = update.phase === 'failed'
  const pct = downloading && update.total
    ? Math.min(100, Math.round((update.downloaded / update.total) * 100))
    : null

  return (
    <div
      data-update-task
      className={`group flex items-center gap-3 px-4 py-2.5 border-b border-black/[0.04] transition-colors last:border-b-0 ${failed ? 'bg-error/[0.04]' : 'hover:bg-surface-hover'}`}
    >
      <div className={`shrink-0 flex items-center justify-center w-8 h-8 rounded-xl ${failed ? 'bg-error/10' : 'bg-accent/10'}`}>
        {failed
          ? <AlertTriangle size={15} className="text-error" />
          : ready
            ? <Check size={15} className="text-accent" />
            : <Download size={15} className="text-accent" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[12px] font-medium text-foreground truncate leading-tight">
          {update.version ? `应用更新 v${update.version}` : '应用更新'}
        </p>
        <p className="text-[11px] mt-0.5 min-w-0">
          {failed ? (
            <span className="text-error">{update.error || '未知错误'}</span>
          ) : ready ? (
            <span className="text-disabled">已下载完成 · 等待重启安装</span>
          ) : installing ? (
            <span className="text-disabled">正在安装，应用即将重启</span>
          ) : (
            <span className="flex items-center gap-1 text-disabled">
              <span className="inline-block w-1 h-1 rounded-full bg-success animate-pulse" />
              下载中{pct !== null ? ` ${pct}%` : ''}
              {update.total ? ` · ${formatSize(update.downloaded)} / ${formatSize(update.total)}` : ''}
              <Elapsed startedAt={downloading ? update.startedAt ?? undefined : undefined} />
            </span>
          )}
        </p>
      </div>
      {ready ? (
        <button
          onClick={() => { void applyUpdate() }}
          disabled={anyTaskRunning}
          title={anyTaskRunning ? '有任务正在运行，完成后再安装' : '重启并安装'}
          data-update-apply
          className="shrink-0 opacity-50 group-hover:opacity-100 disabled:opacity-30 disabled:cursor-not-allowed p-1 rounded-md hover:bg-accent/10 text-disabled hover:text-accent transition-all"
        >
          <RotateCcw size={14} />
        </button>
      ) : failed ? (
        <>
          <button
            onClick={() => { void startDownload() }}
            title="重新下载"
            data-update-retry
            className="shrink-0 opacity-50 group-hover:opacity-100 p-1 rounded-md hover:bg-surface-hover text-disabled hover:text-foreground transition-all"
          >
            <RotateCcw size={14} />
          </button>
          <button
            onClick={dismissUpdate}
            title="关闭"
            className="shrink-0 opacity-50 group-hover:opacity-100 p-1 rounded-md hover:bg-surface-hover text-disabled hover:text-foreground transition-all"
          >
            <X size={14} />
          </button>
        </>
      ) : null}
    </div>
  )
}

/** 触发 TaskPanel 的入口按钮。
 *  ⚠️ 角标计数必须与面板里的行数同源，否则「面板里有一行、入口显示 0」或者反过来 ——
 *  两个数字各自算一份就是这种漂移的来历。三处来源（`processingTasks` / 更新 /
 *  后端任务）都要算，且判定函数与面板里逐字相同（`isUpdateBusy` / `isJobActive`）。 */
export function TaskPanelToggle() {
  const tasks = useStore(s => s.processingTasks)
  const update = useStore(s => s.update)
  const jobs = useStore(s => s.jobs)
  const jobCount = Object.values(jobs).filter(j => isJobActive(j) || j.status === 'failed').length
  const count = Object.keys(tasks).length + (isUpdateBusy(update) ? 1 : 0) + jobCount

  return (
    <button
      data-task-toggle
      onClick={() => useStore.getState().setTaskPanelOpen(true)}
      className="relative p-1.5 rounded-lg hover:bg-surface-hover text-muted hover:text-dimmed transition-colors"
      title={`任务 (${count})`}
    >
      <ListTodo size={14} />
      {count > 0 && (
        <span className="absolute -top-1 -right-1 flex items-center justify-center w-4 h-4 text-[9px] font-bold text-on-accent bg-accent rounded-full shadow-sm shadow-accent/20">
          {count > 9 ? '9+' : count}
        </span>
      )}
    </button>
  )
}
