import { useStore } from '../../store'
import type { ThemeMode } from '../../lib/theme'
import { Sun, Moon, Monitor } from 'lucide-react'

const modes: { mode: ThemeMode; label: string; icon: typeof Sun }[] = [
  { mode: 'light', label: '浅色', icon: Sun },
  { mode: 'dark', label: '深色', icon: Moon },
  { mode: 'system', label: '跟随系统', icon: Monitor },
]

export function ThemeToggle() {
  const theme = useStore(s => s.theme)
  const setTheme = useStore(s => s.setTheme)

  return (
    <div className="flex items-center gap-1 bg-surface rounded-lg p-0.5">
      {modes.map(({ mode, label, icon: Icon }) => (
        <button
          key={mode}
          onClick={() => setTheme(mode)}
          className={`px-3 py-1.5 rounded-md text-[12px] transition-all flex items-center gap-1.5 ${
            theme === mode
              ? 'bg-elevated font-medium shadow-sm text-foreground'
              : 'text-muted hover:text-dimmed'
          }`}
          title={label}
        >
          <Icon size={13} />
          <span>{label}</span>
        </button>
      ))}
    </div>
  )
}
