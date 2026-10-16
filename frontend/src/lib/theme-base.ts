/**
 * LiMo — 界面基调管理 (data-base)
 *
 * 控制全局 --tk-* 底色变量。**14 种**基调覆盖冷暖 + 偏色（真相源是下方 BASE_OPTIONS）。
 * data-base 独立于 existing 四轴 (theme × accent × texture × font)
 *
 * ⚠️ **本模块零色值**（2026-10-16 档 A）。每种基调的 surface / elevated / hover / border
 * 只有一份真相源 —— [`styles/theme-layer.css`](../styles/theme-layer.css) 的
 * `[data-base="x"]` 规则。消费方（设置页色卡 / 预设卡）一律
 * `<el data-base="x">` + `var(--tk-surface)`，**不要在前端再存一份色值表**。
 *
 * ⚠️ 它只提供**浅色模式**的底色偏色。对应的 `[data-base="…"]` 规则必须住在
 * `theme-layer.css` 的 `:root` 与 `[data-theme="dark"]` **之间** —— 三块特异性同为
 * (0,1,0) 且作用在同一个 `<html>` 上，谁生效完全由**顺序**决定（2026-10-16 修：这条
 * 写反过，整轴静默失效）。有测试钉住相对顺序，别挪。
 */

export type BasePreset = 'warm' | 'neutral' | 'cream' | 'cool' | 'rose' | 'sage' | 'jade' | 'amber' | 'pearl' | 'mint' | 'sand' | 'lilac' | 'cloud' | 'blush'

interface BaseEntry {
  key: BasePreset
  label: string
}

export const BASE_OPTIONS: BaseEntry[] = [
  { key: 'warm',    label: '暖灰' },
  { key: 'neutral', label: '中性白' },
  { key: 'cream',   label: '奶油' },
  { key: 'cool',    label: '冷灰' },
  { key: 'rose',    label: '玫瑰' },
  { key: 'sage',    label: '鼠尾草' },
  { key: 'jade',    label: '柔绿' },
  { key: 'amber',   label: '琥珀' },
  { key: 'pearl',   label: '珍珠白' },
  { key: 'mint',    label: '薄荷' },
  { key: 'sand',    label: '沙色' },
  { key: 'lilac',   label: '丁香' },
  { key: 'cloud',   label: '云白' },
  { key: 'blush',   label: '腮红' },
]

const STORAGE_KEY = 'mbw_base'

export function getSavedBase(): BasePreset {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v && BASE_OPTIONS.some(b => b.key === v)) return v as BasePreset
  } catch { /* quota */ }
  return 'warm'
}

export function applyBase(b: BasePreset): void {
  document.documentElement.setAttribute('data-base', b)
}

export function saveBase(b: BasePreset): void {
  try { localStorage.setItem(STORAGE_KEY, b) } catch { /* quota */ }
}
