/**
 * 批量导入的确认与进度对话框（2026-11-07）。
 *
 * 三个阶段共用一个壳：`confirm`（确认清单，零请求）→ `running`（逐项进度）→ `done`（结果）。
 *
 * ⚠️ **必须 Portal 到 body**：`GenPanel` 的根 `<aside>` 带 `backdrop-blur`，它会创建包含块，
 * 把 `fixed` 后代锁进面板大小的一小块（模式 #67）。
 *
 * ⚠️ `running` 期间 Esc 不关闭对话框。此时关掉只会让用户失去进度，而请求还在跑；
 * 要中断请点「取消」，那条路径会把剩余项标成「已取消」并保留结果。
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  Check, CircleDashed, Copy, FileText, FileType, Film, Image as ImageIcon,
  Loader2, MinusCircle, XCircle,
} from 'lucide-react'

import { type BatchItem, type BatchPhase } from './useBatchImport'

const KIND_LABEL: Record<string, string> = { video: '视频', image: '图片', file: '文档', text: '文本' }
const SUB_LABEL: Record<string, string> = {
  pdf: 'PDF', word: 'Word', ppt: 'PPT', excel: 'Excel', epub: 'EPUB',
}

function kindLabel(item: BatchItem): string {
  if (item.kind === 'file') return SUB_LABEL[item.subType] || '文档'
  return KIND_LABEL[item.kind || ''] || '未知'
}

function KindIcon({ item }: { item: BatchItem }) {
  const size = 13
  if (item.kind === 'video') return <Film size={size} />
  if (item.kind === 'image') return <ImageIcon size={size} />
  if (item.kind === 'text') return <FileType size={size} />
  if (item.kind === 'file') return <FileText size={size} />
  return <FileText size={size} />
}

function StatusMark({ item }: { item: BatchItem }) {
  switch (item.status) {
    case 'running':
      return <Loader2 size={13} className="animate-spin text-accent shrink-0" />
    case 'done':
      return <Check size={13} className="text-success shrink-0" />
    case 'duplicate':
      return <Copy size={13} className="text-dimmed shrink-0" />
    case 'failed':
      return <XCircle size={13} className="text-error shrink-0" />
    case 'skipped':
      return <MinusCircle size={13} className="text-disabled shrink-0" />
    default:
      return <CircleDashed size={13} className="text-disabled shrink-0" />
  }
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

export interface BatchImportDialogProps {
  items: BatchItem[]
  phase: BatchPhase
  /** 超出文件数上限、未进入清单的数量。 */
  truncated: number
  onConfirm: () => void
  onCancel: () => void
  onClose: () => void
}

export function BatchImportDialog({
  items, phase, truncated, onConfirm, onCancel, onClose,
}: BatchImportDialogProps) {
  const importable = items.filter(it => it.status === 'queued').length
  const running = phase === 'running'

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // 跑的时候不响应 Esc —— 见文件头。
      if (e.key !== 'Escape' || running) return
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [running, onClose])

  const finished = items.filter(it => it.status === 'done' || it.status === 'duplicate'
    || it.status === 'failed' || it.status === 'skipped').length

  const title = phase === 'confirm' ? '批量导入'
    : phase === 'running' ? `正在导入 ${Math.min(finished + 1, items.length)}/${items.length}`
    : '导入完成'

  return createPortal(
    <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={running ? undefined : onClose}>
      <div
        data-batch-import-dialog
        data-batch-phase={phase}
        className="relative bg-elevated rounded-2xl shadow-2xl border border-border-light w-[520px] max-w-[92vw] max-h-[80vh] flex flex-col mx-4 animate-[fadeIn_0.15s_ease-out]"
        onClick={e => e.stopPropagation()}>

        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-light shrink-0">
          <h3 className="text-[15px] font-semibold">{title}</h3>
          <span className="text-[11px] text-disabled">共 {items.length} 个文件</span>
        </div>

        {truncated > 0 && (
          <p data-batch-truncated className="px-5 pt-2.5 text-[11px] text-warning shrink-0">
            一次最多导入 {items.length} 个文件，另有 {truncated} 个未加入本次导入。
          </p>
        )}

        {phase === 'confirm' && (
          <p className="px-5 pt-2.5 text-[11px] text-disabled shrink-0">
            每个文件按扩展名自动选择导入方式，逐个处理。
          </p>
        )}

        <div className="flex-1 overflow-y-auto px-2 py-2 min-h-[140px]">
          {items.map(item => (
            <div key={item.key} data-batch-row
              data-batch-status={item.status}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg">
              <StatusMark item={item} />
              <span className="text-disabled shrink-0"><KindIcon item={item} /></span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-foreground truncate" title={item.name}>{item.name}</div>
                <div className="text-[11px] text-disabled truncate">
                  {kindLabel(item)} · {formatSize(item.size)}
                  {item.message && <span className={item.status === 'failed' ? ' text-error' : ''}>
                    {' · '}{item.message}
                  </span>}
                </div>
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
                data-batch-start
                onClick={onConfirm}
                disabled={importable === 0}
                className="px-3 py-1.5 rounded-lg text-[12px] bg-accent text-on-accent hover:bg-accent-hover transition-colors disabled:opacity-40">
                开始导入 {importable} 篇
              </button>
            </>
          )}
          {phase === 'running' && (
            <button data-batch-cancel onClick={onCancel}
              className="px-3 py-1.5 rounded-lg text-[12px] text-error hover:bg-error/10 transition-colors">
              取消
            </button>
          )}
          {phase === 'done' && (
            <button data-batch-close onClick={onClose}
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
