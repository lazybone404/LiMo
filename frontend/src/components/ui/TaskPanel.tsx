import { useEffect, useRef, useState, useCallback } from 'react'
import { useStore, type DocEntry } from '../../store'
import { cancelTask } from '../../api/taskManager'
import { X, GripHorizontal, FileText, Image, RefreshCw, ListTodo, Check, AlertTriangle } from 'lucide-react'

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

  // 拖动状态
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  const dragging = useRef(false)
  const dragStart = useRef({ x: 0, y: 0 })
  const posRef = useRef(pos)
  posRef.current = pos

  const entries = Object.entries(tasks)

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
    if (open && entries.length === 0) {
      const t = setTimeout(() => setOpen(false), 1500)
      return () => clearTimeout(t)
    }
  }, [open, entries.length, setOpen])

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
          {entries.length > 0 && (
            <span className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[9px] font-bold text-on-accent bg-accent rounded-full">
              {entries.length}
            </span>
          )}
        </div>
        <button onClick={() => setOpen(false)}
          className="p-1 rounded-md hover:bg-surface-hover text-muted transition-colors">
          <X size={13} />
        </button>
      </header>

      {/* 列表 */}
      {entries.length === 0 ? (
        <div className="py-10 text-center">
          <Check size={20} className="text-disabled mx-auto mb-2 opacity-30" />
          <p className="text-[12px] text-disabled">无进行中的任务</p>
        </div>
      ) : (
        <div className="max-h-[300px] overflow-y-auto">
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
      {entries.length > 0 && (
        <footer className="border-t border-border-light px-4 py-2 bg-surface flex justify-between items-center">
          <span className="text-[10px] text-disabled">拖拽移动</span>
          <button
            onClick={() => { entries.forEach(([id]) => {
              // P2-7：逐条精确取消（导入用完整 taskKey，不归一成 __import__ 前缀）
              const baseKey = id.startsWith('__import__') ? id : id.includes('__') ? id.slice(0, id.indexOf('__')) : id
              cancelTask(baseKey)
              setProcessing(id, null)
            }); }}
            className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-medium text-error hover:bg-error/10 rounded-md transition-colors">
            <AlertTriangle size={10} />
            取消全部
          </button>
        </footer>
      )}
    </div>
  )
}

/** 触发 TaskPanel 的入口按钮 */
export function TaskPanelToggle() {
  const tasks = useStore(s => s.processingTasks)
  const count = Object.keys(tasks).length

  return (
    <button
      data-task-toggle
      onClick={() => useStore.getState().setTaskPanelOpen(true)}
      className="relative p-1.5 rounded-lg hover:bg-surface-hover text-muted hover:text-dimmed transition-colors"
      title={`任务 (${count})`}
    >
      <ListTodo size={15} />
      {count > 0 && (
        <span className="absolute -top-1 -right-1 flex items-center justify-center w-4 h-4 text-[9px] font-bold text-on-accent bg-accent rounded-full shadow-sm shadow-accent/20">
          {count > 9 ? '9+' : count}
        </span>
      )}
    </button>
  )
}
