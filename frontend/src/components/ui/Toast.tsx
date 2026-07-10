import { useStore } from '../../store'
import { CheckCircle2, AlertCircle, X } from 'lucide-react'

export function Toast() {
  const toasts = useStore(s => s.toasts)
  const dismiss = useStore(s => s.dismissToast)

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-6 right-6 z-[400] flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id}
          className={`pointer-events-auto flex items-center gap-2 px-4 py-3 rounded-xl shadow-lg border animate-[slide-up_0.2s_ease-out] ${
            t.type === 'success'
              ? 'bg-success/10 border-success'
              : 'bg-error/10 border-error'
          }`}
        >
          {t.type === 'success'
            ? <CheckCircle2 size={16} className="text-success shrink-0" />
            : <AlertCircle size={16} className="text-error shrink-0" />
          }
          <span className="text-[13px] font-medium text-foreground">{t.message}</span>
          <button onClick={() => dismiss(t.id)} className="ml-2 shrink-0">
            <X size={13} className="text-disabled hover:text-dimmed" />
          </button>
        </div>
      ))}
    </div>
  )
}
