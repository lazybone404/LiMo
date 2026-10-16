/**
 * LiMo — Theme management utilities
 *
 * Dual mechanism:
 *   1. `data-theme` attribute on <html> — for [data-theme] CSS selectors
 *   2. `color-scheme` style on <html> — for CSS light-dark() function
 *
 * JS sets both from the same resolved value so they stay in sync.
 */

import { applyOnAccent } from './onAccent'

export type ThemeMode = 'light' | 'dark' | 'system'

/**
 * 明暗三档 —— **真相源**。（`ThemeToggle` 只额外持有「哪一档配哪个图标」，
 * 那是 UI 关切；名字是轴的数据，注册表与设置页都要读它。）
 */
export const THEME_OPTIONS: { key: ThemeMode; label: string }[] = [
  { key: 'light',  label: '浅色' },
  { key: 'dark',   label: '深色' },
  { key: 'system', label: '跟随系统' },
]

const STORAGE_KEY = 'mbw_theme'

export function getSavedTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    // ⚠️ 用**选项表**做 membership，不写 `v === 'light' || v === 'dark' || …` 字面量链：
    // 字面量链是第二真相源，将来加一档（如 'auto'）时会**静默**把合法值判成非法、
    // 回退 'system' —— 症状是「新档位存进去、刷新就没了」。（同 accent/base/texture/font）
    if (v && THEME_OPTIONS.some(o => o.key === v)) return v as ThemeMode
  } catch { /* quota exceeded — ignore */ }
  return 'system'
}

export function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return mode
}

export function applyTheme(resolved: 'light' | 'dark'): void {
  // data-theme attribute → for CSS [data-theme] selectors
  document.documentElement.setAttribute('data-theme', resolved)
  // color-scheme → for CSS light-dark() function
  document.documentElement.style.colorScheme = resolved
  // 明暗切换会改主色亮度（`--accent-lit` 50% ↔ 62%）→ 实底前景色跟着重算（P1-16）。
  // ⚠️ 必须在这里调而不是只依赖 `applyAccent`：切「跟随系统」时 accent 没变、
  //    变的是 data-theme，只挂后者会让 on-accent 停留在上一个明暗档的取值。
  applyOnAccent()
}

export function saveTheme(mode: ThemeMode): void {
  try { localStorage.setItem(STORAGE_KEY, mode) } catch { /* quota exceeded — ignore */ }
}
