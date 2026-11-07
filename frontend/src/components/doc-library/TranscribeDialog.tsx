/**
 * 批量转录的确认与进度对话框（2026-11-07）。
 *
 * 三个阶段共用一个壳：`confirm`（清单，零请求）→ `running`（逐篇进度）→ `done`（结果）。
 *
 * ⚠️ **必须 Portal 到 body**：`DocLibrary` 的根 `<aside>` 带 `backdrop-blur`，它会创建
 * 包含块，把 `fixed` 后代锁进面板大小的一小块（模式 #67）。
 *
 * ⚠️ `running` 期间 Esc 不关闭，但有一条**另外的出口**：「在后台继续」。转录一篇视频
 * 可能要好几分钟，把人锁在对话框里不合适。后台继续只关窗、不停队列 —— 进度在任务面板
 * 与预览区的待转录卡片上都看得到，因为它们读的是同一个 `processingTasks` 条目。
 *
 * 与取消的区别要写在按钮上：取消会中断当前请求并把剩余项标成「已取消」。
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  Check, CircleDashed, Loader2, MinusCircle, XCircle,
} from 'lucide-react'

import { type TranscribeItem, type TranscribePhase } from '../../hooks/useBatchTranscribe'

function StatusMark({ item }: { item: TranscribeItem }) {
  switch (item.status) {
    case 'running':
      return <Loader2 size={13} className="animate-spin text-accent shrink-0" />
    case 'done':
      return <Check size={13} className="text-success shrink-0" />
    case 'failed':
      return <XCircle size={13} className="text-error shrink-0" />
    case 'skipped':
      return <MinusCircle size={13} className="text-disabled shrink-0" />
    default:
      return <CircleDashed size={13} className="text-disabled shrink-0" />
  }
}

export interface TranscribeDialogProps {
  items: TranscribeItem[]
  phase: TranscribePhase
  onConfirm: () => void
  onCancel: () => void
  onClose: () => void
  /** 收起对话框、队列继续跑。只在 running 阶段渲染。 */
  onBackground: () => void
}

export function TranscribeDialog({
  items, phase, onConfirm, onCancel, onClose, onBackground,
}: TranscribeDialogProps) {
  const running = phase === 'running'

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || running) return
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [running, onClose])

  const finished = items.filter(it => it.status !== 'queued' && it.status !== 'running').length
  const title = phase === 'confirm' ? '批量转录'
    : phase === 'running' ? `正在转录 ${Math.min(finished + 1, items.length)}/${items.length}`
    : '转录完成'

  return createPortal(
    <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={running ? undefined : onClose}>
      <div
        data-transcribe-dialog
        data-transcribe-phase={phase}
        className="relative bg-elevated rounded-2xl shadow-2xl border border-border-light w-[520px] max-w-[92vw] max-h-[80vh] flex flex-col mx-4 animate-[fadeIn_0.15s_ease-out]"
        onClick={e => e.stopPropagation()}>

        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-light shrink-0">
          <h3 className="text-[15px] font-semibold">{title}</h3>
          <span className="text-[11px] text-disabled">共 {items.length} 篇</span>
        </div>

        {phase === 'confirm' && (
          <p className="px-5 pt-2.5 text-[11px] text-disabled shrink-0">
            逐篇提取正文。视频要下载音频并进行语音识别，一篇可能几十秒到几分钟；
            期间可以关掉这个对话框让它在后台继续。
          </p>
        )}

        <div className="flex-1 overflow-y-auto px-2 py-2 min-h-[140px]">
          {items.map(item => (
            <div key={item.id} data-transcribe-row
              data-transcribe-status={item.status}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg">
              <StatusMark item={item} />
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-foreground truncate" title={item.title}>
                  {item.title}
                </div>
                {item.message && (
                  <div className={`text-[11px] truncate ${item.status === 'failed' ? 'text-error' : 'text-disabled'}`}>
                    {item.message}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border-light shrink-0">
          {phase === 'confirm' && (
            <>
              <button onClick={onClose}
                className="px-3 py-1.5 rounded-lg text-[12px] text-dimmed hover:bg-surface-hover transition-colors">
                取消
              </button>
              <button
                data-transcribe-start
                onClick={onConfirm}
                disabled={items.length === 0}
                className="px-3 py-1.5 rounded-lg text-[12px] bg-accent text-on-accent hover:bg-accent-hover transition-colors disabled:opacity-40">
                开始转录 {items.length} 篇
              </button>
            </>
          )}
          {phase === 'running' && (
            <>
              <button data-transcribe-background onClick={onBackground}
                className="px-3 py-1.5 rounded-lg text-[12px] text-dimmed hover:bg-surface-hover transition-colors">
                在后台继续
              </button>
              <button data-transcribe-cancel onClick={onCancel}
                className="px-3 py-1.5 rounded-lg text-[12px] text-error hover:bg-error/10 transition-colors">
                取消
              </button>
            </>
          )}
          {phase === 'done' && (
            <button data-transcribe-close onClick={onClose}
              className="px-3 py-1.5 rounded-lg text-[12px] bg-accent text-on-accent hover:bg-accent-hover transition-colors">
              关闭
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
