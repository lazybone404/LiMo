/**
 * LiMo — 预览区纹理 & 字体管理
 *
 * data-texture: 6 种纸张纹理，纯 CSS SVG feTurbulence 实现
 * data-font: 5 种阅读字体（4 系统 + 1 Web），后续可扩展
 * 四轴独立：theme × accent × texture × font
 */

export type TexturePreset = 'plain' | 'xuan' | 'kraft' | 'parchment' | 'bamboo' | 'letter'
export type FontPreset = 'system' | 'kaiti' | 'songti' | 'fangsong' | 'wenkai'

interface TextureEntry { key: TexturePreset; label: string }
interface FontEntry { key: FontPreset; label: string; category: 'system' | 'web' }

export const TEXTURE_OPTIONS: TextureEntry[] = [
  { key: 'plain',     label: '无纹理' },
  { key: 'xuan',      label: '宣纸' },
  { key: 'kraft',     label: '牛皮纸' },
  { key: 'parchment', label: '羊皮纸' },
  { key: 'bamboo',    label: '竹纸' },
  { key: 'letter',    label: '信笺' },
]

export const FONT_OPTIONS: FontEntry[] = [
  { key: 'system',   label: '系统默认',     category: 'system' },
  { key: 'kaiti',    label: '楷体',         category: 'system' },
  { key: 'songti',   label: '宋体',         category: 'system' },
  { key: 'fangsong', label: '仿宋',         category: 'system' },
  { key: 'wenkai',   label: '霞鹜文楷',     category: 'web'   },
]

/** 纹理预览色 — 与 CSS 实际底色同步的色卡 */
export const TEXTURE_SWATCH: Record<TexturePreset, string> = {
  plain:     '#F4F1EE',
  xuan:      '#F9F4EC',
  kraft:     '#F7F1E8',
  parchment: '#F9F5EE',
  bamboo:    '#F6F7F0',
  letter:    '#F8F5F8',
}

const TEXTURE_KEY = 'mbw_texture'
const FONT_KEY = 'mbw_font'
const INTENSITY_KEY = 'mbw_texture_intensity'

// ── Texture ──────────────────────────────────────────────

export function getSavedTexture(): TexturePreset {
  try {
    const v = localStorage.getItem(TEXTURE_KEY)
    if (v && TEXTURE_OPTIONS.some(t => t.key === v)) return v as TexturePreset
  } catch { /* quota exceeded */ }
  return 'plain'
}

export function applyTexture(t: TexturePreset): void {
  document.documentElement.setAttribute('data-texture', t)
}

export function saveTexture(t: TexturePreset): void {
  try { localStorage.setItem(TEXTURE_KEY, t) } catch { /* quota */ }
}

// ── Texture Intensity (0-100, 默认 50) ──────────────────

export function getSavedIntensity(): number {
  try {
    const v = localStorage.getItem(INTENSITY_KEY)
    if (v != null) return Math.max(0, Math.min(100, parseInt(v) || 50))
  } catch { /* quota */ }
  return 50
}

export function applyIntensity(value: number): void {
  // value: 10-100, 反转: 10→0.9(弱) 100→0(强)
  document.documentElement.style.setProperty('--texture-opacity', String((100 - value) / 100))
}

export function saveIntensity(value: number): void {
  try { localStorage.setItem(INTENSITY_KEY, String(value)) } catch { /* quota */ }
}

// ── Font ─────────────────────────────────────────────────

export function getSavedFont(): FontPreset {
  try {
    const v = localStorage.getItem(FONT_KEY)
    if (v && FONT_OPTIONS.some(f => f.key === v)) return v as FontPreset
  } catch { /* quota exceeded */ }
  return 'system'
}

export function applyFont(f: FontPreset): void {
  document.documentElement.setAttribute('data-font', f)
}

export function saveFont(f: FontPreset): void {
  try { localStorage.setItem(FONT_KEY, f) } catch { /* quota */ }
}
