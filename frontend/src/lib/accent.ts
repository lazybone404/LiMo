/**
 * LiMo — Accent color theme management
 *
 * 预设主题包：每个主题包含主色 + 辅色的 HSL 原语。
 * 通过 data-accent 属性驱动 CSS hsl() 派生所有语义令牌。
 * 底色保持中性灰阶不变。
 */

export type AccentColor =
  // 现代
  | 'sky' | 'ocean' | 'sunset' | 'forest' | 'lavender' | 'slate'
  // 国风
  | 'dunhuang' | 'porcelain' | 'ink' | 'rouge' | 'celadon' | 'osmanthus'
  // 自然
  | 'matcha' | 'cherry' | 'pine'

interface AccentEntry {
  key: AccentColor
  label: string
}

interface AccentGroup {
  label: string
  accents: AccentEntry[]
}

export const ACCENT_GROUPS: AccentGroup[] = [
  {
    label: '现代',
    accents: [
      { key: 'sky',      label: '天际' },
      { key: 'ocean',    label: '深海' },
      { key: 'sunset',   label: '落日' },
      { key: 'forest',   label: '森林' },
      { key: 'lavender', label: '薰衣草' },
      { key: 'slate',    label: '岩板' },
    ],
  },
  {
    label: '国风',
    accents: [
      { key: 'dunhuang',   label: '敦煌' },
      { key: 'porcelain',  label: '青花' },
      { key: 'ink',        label: '墨韵' },
      { key: 'rouge',      label: '胭脂' },
      { key: 'celadon',    label: '青瓷' },
      { key: 'osmanthus',  label: '金桂' },
    ],
  },
  {
    label: '自然',
    accents: [
      { key: 'matcha',    label: '抹茶' },
      { key: 'cherry',    label: '樱' },
      { key: 'pine',      label: '松' },
    ],
  },
]

/** 色块预览色 — 主色和辅色的代表性 hsl 值 */
export const ACCENT_SWATCH: Record<AccentColor, { primary: string; secondary: string }> = {
  sky:        { primary: 'hsl(211,100%,50%)', secondary: 'hsl(195,80%,46%)' },
  ocean:      { primary: 'hsl(225,60%,44%)',  secondary: 'hsl(210,50%,48%)' },
  sunset:     { primary: 'hsl(30,100%,48%)',  secondary: 'hsl(345,70%,55%)' },
  forest:     { primary: 'hsl(145,60%,38%)',  secondary: 'hsl(160,45%,42%)' },
  lavender:   { primary: 'hsl(272,60%,50%)',  secondary: 'hsl(340,50%,55%)' },
  slate:      { primary: 'hsl(220,15%,42%)',  secondary: 'hsl(215,25%,48%)' },
  dunhuang:   { primary: 'hsl(2,80%,48%)',    secondary: 'hsl(40,75%,45%)' },
  porcelain:  { primary: 'hsl(225,50%,42%)',  secondary: 'hsl(215,22%,50%)' },
  ink:        { primary: 'hsl(30,10%,40%)',   secondary: 'hsl(125,30%,36%)' },
  rouge:      { primary: 'hsl(350,70%,52%)',  secondary: 'hsl(285,25%,48%)' },
  celadon:    { primary: 'hsl(170,30%,38%)',  secondary: 'hsl(215,20%,50%)' },
  osmanthus:  { primary: 'hsl(40,75%,45%)',   secondary: 'hsl(5,50%,48%)' },
  matcha:     { primary: 'hsl(135,35%,38%)', secondary: 'hsl(155,25%,42%)' },
  cherry:     { primary: 'hsl(355,75%,60%)', secondary: 'hsl(340,55%,55%)' },
  pine:       { primary: 'hsl(130,25%,32%)', secondary: 'hsl(150,20%,38%)' },
}

const STORAGE_KEY = 'mbw_accent'

export function getSavedAccent(): AccentColor {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v && ACCENT_SWATCH[v as AccentColor]) return v as AccentColor
  } catch { /* quota exceeded — ignore */ }
  return 'sky'
}

export function applyAccent(accent: AccentColor): void {
  document.documentElement.setAttribute('data-accent', accent)
}

export function saveAccent(accent: AccentColor): void {
  try { localStorage.setItem(STORAGE_KEY, accent) } catch { /* quota exceeded — ignore */ }
}
