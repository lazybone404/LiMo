/**
 * 批量生成的确认与进度对话框（2026-11-07）。
 *
 * 三个阶段共用一个壳：`confirm`（清单，零请求）→ `running`（逐篇进度）→ `done`（结果）。
 * 形态与 `TranscribeDialog` 一致，两处刻意各写一份：它们的清单内容不同（这里每篇要标
 * 「重新生成」还是「生成」），合并成一个「通用队列对话框」会让两边都塞进对方的字段。
 *
 * ⚠️ **必须 Portal 到 body**：`DocLibrary` 的根 `<aside>` 带 `backdrop-blur`，它会创建
 * 包含块，把 `fixed` 后代锁进面板大小的一小块（模式 #67）。
 *
 * ⚠️ 确认页要写清两件事，它们都直接影响用户会不会按下这个按钮：
 *
 * 1. **已有产物的会重新生成，没有的会生成** —— 这是用户对批量操作最关心的判断；
 * 2. **用的是生成面板当前的设置**（风格、格式、模型、思考档位）—— 否则用户会以为
 *    这里还能逐篇调参。
 *
 * ⚠️ `running` 期间 Esc 不关闭，出口是「在后台继续」：一篇长视频生成要好几分钟，
 * 把人锁在对话框里不合适。后台继续只关窗、不停队列，进度在任务面板与预览区的
 * 生成占位上都看得到，因为它们读的是同一份 `processingTasks`。
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  Check, CircleDashed, Loader2, MinusCircle, XCircle,
} from 'lucide-react'

import type { GenerateItem, GeneratePhase } from '../../hooks/useBatchGenerate'

function StatusMark({ item }: { item: GenerateItem }) {
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

export interface GenerateDialogProps {
  items: GenerateItem[]
  phase: GeneratePhase
  onConfirm: () => void
  onCancel: () => void
  onClose: () => void
  /** 收起对话框、队列继续跑。只在 running 阶段渲染。 */
  onBackground: () => void
}

export function GenerateDialog({
  items, phase, onConfirm, onCancel, onClose, onBackground,
}: GenerateDialogProps) {
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
  const regenCount = items.filter(it => it.willRegenerate).length
  const title = phase === 'confirm' ? '批量生成'
    : phase === 'running' ? `正在生成 ${Math.min(finished + 1, items.length)}/${items.length}`
    : '生成完成'

  return createPortal(
    <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={running ? undefined : onClose}>
      <div
        data-generate-dialog
        data-generate-phase={phase}
        className="relative bg-elevated rounded-2xl shadow-2xl border border-border-light w-[520px] max-w-[92vw] max-h-[80vh] flex flex-col mx-4 animate-[fadeIn_0.15s_ease-out]"
        onClick={e => e.stopPropagation()}>

        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-light shrink-0">
          <h3 className="text-[15px] font-semibold">{title}</h3>
          <span className="text-[11px] text-disabled">共 {items.length} 篇</span>
        </div>

        {phase === 'confirm' && (
          <p className="px-5 pt-2.5 text-[11px] text-disabled shrink-0">
            逐篇串行生成。已有产物的会新建一版（不覆盖已编辑的那一版），没有产物的直接生成。
            参数取自生成面板当前的设置。
            {regenCount > 0 && `本次有 ${regenCount} 篇是重新生成。`}
          </p>
        )}

        <div className="flex-1 overflow-y-auto px-2 py-2 min-h-[140px]">
          {items.map(item => (
            <div key={item.id} data-generate-row
              data-generate-status={item.status}
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
              {/* 这一列只在确认页出现：跑起来之后状态图标已经说明进度，
                  再摆一列「重新生成」只是噪音。 */}
              {phase === 'confirm' && (
                <span className={`text-[10px] shrink-0 ${item.willRegenerate ? 'text-dimmed' : 'text-disabled'}`}>
                  {item.willRegenerate ? '重新生成' : '生成'}
                </span>
              )}
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
                data-generate-start
                onClick={onConfirm}
                disabled={items.length === 0}
                className="px-3 py-1.5 rounded-lg text-[12px] bg-accent text-on-accent hover:bg-accent-hover transition-colors disabled:opacity-40">
                开始生成 {items.length} 篇
              </button>
            </>
          )}
          {phase === 'running' && (
            <>
              <button data-generate-background onClick={onBackground}
                className="px-3 py-1.5 rounded-lg text-[12px] text-dimmed hover:bg-surface-hover transition-colors">
                在后台继续
              </button>
              <button data-generate-cancel onClick={onCancel}
                className="px-3 py-1.5 rounded-lg text-[12px] text-error hover:bg-error/10 transition-colors">
                取消
              </button>
            </>
          )}
          {phase === 'done' && (
            <button data-generate-close onClick={onClose}
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
