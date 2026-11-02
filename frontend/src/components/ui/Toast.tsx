import { useStore } from '../../store'
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react'
import type { ToastItem } from '../../store/types'

/**
 * 三种类型的视觉。⚠️ **`info` 必须有自己的一支**：此前它落进最终的 `else`
 * 显示成红色报错 —— 一条「检索没生效」的中性提示长得和「导入失败」一模一样，
 * 而调用方写 `addToast(msg, 'info')` 时看到的是它想要的语义。
 */
const TONE: Record<ToastItem['type'], { box: string; Icon: typeof CheckCircle2; icon: string }> = {
  success: { box: 'bg-success/10 border-success', Icon: CheckCircle2, icon: 'text-success' },
  error: { box: 'bg-error/10 border-error', Icon: AlertCircle, icon: 'text-error' },
  info: { box: 'bg-accent/10 border-accent', Icon: Info, icon: 'text-accent' },
}

export function Toast() {
  const toasts = useStore(s => s.toasts)
  const dismiss = useStore(s => s.dismissToast)

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-6 right-6 z-[400] flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => {
        const tone = TONE[t.type] ?? TONE.info
        const { Icon } = tone
        // 动作按钮**两者都在**才渲染（与 `toastSlice.addToast` 的判据同源）
        const hasAction = Boolean(t.actionLabel && t.onAction)
        return (
          <div key={t.id}
            className={`pointer-events-auto flex items-center gap-2 px-4 py-3 rounded-xl shadow-lg border animate-[slide-up_0.2s_ease-out] ${tone.box}`}
          >
            <Icon size={16} className={`${tone.icon} shrink-0`} />
            <span className="text-[13px] font-medium text-foreground">{t.message}</span>
            {hasAction && (
              <button
                data-toast-action
                onClick={() => { t.onAction?.(); dismiss(t.id) }}
                className="ml-1 shrink-0 px-2 py-0.5 rounded-md text-[12px] font-medium text-accent hover:bg-accent/10 transition-colors"
              >
                {t.actionLabel}
              </button>
            )}
            <button onClick={() => dismiss(t.id)} className="ml-2 shrink-0">
              <X size={13} className="text-disabled hover:text-dimmed" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
