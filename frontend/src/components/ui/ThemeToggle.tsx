import { useStore } from '../../store'
import type { ThemeMode } from '../../lib/theme'
import { Sun, Moon, Monitor } from 'lucide-react'

const modes: { mode: ThemeMode; label: string; icon: typeof Sun }[] = [
  { mode: 'light', label: '浅色', icon: Sun },
  { mode: 'dark', label: '深色', icon: Moon },
  { mode: 'system', label: '跟随系统', icon: Monitor },
]

/**
 * 明暗切换 — 三栏分段控件，**只显图标不显文字**。
 *
 * 三个档位直接可见、一键直达（循环式单按钮要点两次才能到「跟随系统」）；
 * 文字降为 `title` / `aria-label` 悬停提示 —— 三个图标（太阳/月亮/显示器）
 * 的含义足够自明，而带文字的版本在顶栏占掉近三倍宽度。
 */
export function ThemeToggle() {
  const theme = useStore(s => s.theme)
  const setTheme = useStore(s => s.setTheme)

  return (
    <div className="flex items-center gap-0.5 bg-surface rounded-lg p-0.5">
      {modes.map(({ mode, label, icon: Icon }) => (
        <button
          key={mode}
          onClick={() => setTheme(mode)}
          aria-label={label}
          aria-pressed={theme === mode}
          className={`w-7 h-7 flex items-center justify-center rounded-md transition-all ${
            theme === mode
              ? 'bg-elevated shadow-sm text-foreground'
              : 'text-muted hover:text-dimmed hover:bg-surface-hover'
          }`}
          title={label}
        >
          <Icon size={13} />
        </button>
      ))}
    </div>
  )
}
