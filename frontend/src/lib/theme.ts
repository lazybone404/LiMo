/**
 * LiMo — Theme management utilities
 *
 * Dual mechanism:
 *   1. `data-theme` attribute on <html> — for [data-theme] CSS selectors
 *   2. `color-scheme` style on <html> — for CSS light-dark() function
 *
 * JS sets both from the same resolved value so they stay in sync.
 */

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'mbw_theme'

export function getSavedTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
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
}

export function saveTheme(mode: ThemeMode): void {
  try { localStorage.setItem(STORAGE_KEY, mode) } catch { /* quota exceeded — ignore */ }
}
