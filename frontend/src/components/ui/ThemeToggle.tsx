import { useStore } from '../../store'
import { THEME_OPTIONS, type ThemeMode } from '../../lib/theme'
import { Sun, Moon, Monitor } from 'lucide-react'

/**
 * 档位 → 图标。**只有图标住在这个文件里** —— 三档的名字是**轴的数据**，
 * 住在 `lib/theme.ts` 的 `THEME_OPTIONS`（另四条轴同款，注册表要读它；
 * 名字写两份的下场见 CLAUDE.md 的「加一条轴 = 改 N 处」）。
 */
const ICONS: Record<ThemeMode, typeof Sun> = { light: Sun, dark: Moon, system: Monitor }

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
      {THEME_OPTIONS.map(({ key: mode, label }) => {
        const Icon = ICONS[mode]
        return (
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
        )
      })}
    </div>
  )
}
