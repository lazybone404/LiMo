import { AlertTriangle, AlertCircle } from 'lucide-react'

export function PartialBanner({ versions, isStreaming }: { versions: { version: number; status?: string }[] | null; isStreaming: boolean }) {
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
      <div>
        <p className="font-medium text-foreground">
          {isStreaming ? '正在生成中...' : '此版本生成被中断，内容不完整'}
        </p>
        <p className="text-[11px] text-dimmed mt-0.5">
          {isStreaming
            ? '实时显示生成内容，完成后自动保存'
            : '可在左侧生成面板点击「继续」基于残缺内容续写'}
        </p>
      </div>
    </div>
  )
}
