import { AlertTriangle, AlertCircle, RefreshCw } from 'lucide-react'

export function PartialBanner({ versions, isStreaming, onContinue }: {
  versions: { version: number; status?: string }[] | null
  isStreaming: boolean
  /** P2-1：中断恢复入口——点击触发续写 partial 产物（后端 continue_from=partial 从断点继续写） */
  onContinue?: () => void
}) {
  if (!Array.isArray(versions)) return null
  const hasPartial = versions.some(v => v.status === 'partial')
  if (!hasPartial) return null

  return (
    <div className={`mx-4 mb-4 p-3 rounded-xl flex items-start gap-2.5 text-[13px] leading-relaxed ${isStreaming ? 'bg-warning/10 border border-warning/30' : 'bg-warning/5 border border-warning/20'}`}>
      {isStreaming ? (
        <AlertCircle size={16} className="text-warning shrink-0 mt-0.5" />
      ) : (
        <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" />
      )}
      <div className="flex-1">
        <p className="font-medium text-foreground">
          {isStreaming ? '正在生成中...' : '此版本生成被中断，内容不完整'}
        </p>
        <p className="text-[11px] text-dimmed mt-0.5">
          {isStreaming
            ? '实时显示生成内容，完成后自动保存'
            : '可点击「继续生成」从断点续写，或到左侧重新生成'}
        </p>
      </div>
      {!isStreaming && onContinue && (
        <button
          onClick={onContinue}
          className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-warning/15 hover:bg-warning/25 text-warning text-[12px] font-medium rounded-lg transition-colors"
          title="从上次中断处继续生成">
          <RefreshCw size={12} />
          继续生成
        </button>
      )}
    </div>
  )
}
