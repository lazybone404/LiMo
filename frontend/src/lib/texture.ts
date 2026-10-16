/**
 * LiMo — 预览区纹理 & 字体管理
 *
 * data-texture: 6 种纸张纹理，纯 CSS SVG feTurbulence 实现
 * data-font: 5 种阅读字体（4 系统 + 1 Web），后续可扩展
 * 四轴独立：theme × accent × texture × font
 *
 * ⚠️ **本模块零色值**（2026-10-16 档 A）。纹理的底色 / 图案 / blend-mode 只有一份
 * 真相源 —— [`styles/theme-layer.css`](../styles/theme-layer.css) 的
 * `[data-texture="x"]` 规则，以三个变量声明：
 *     --texture-bg-color / --texture-bg-image / --texture-blend
 * 消费方（设置页色卡 / 预览区 / 导出）一律 `<el data-texture="x">` + `var()`，
 * **不要在前端再存一份色值表** —— 此前那份 `TEXTURE_SWATCH` 与 CSS 实际底色
 * **6/6 全不符**（色卡与实机对不上，零报错）。
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

/**
 * 强度滑条值（10-100）→ `--texture-opacity` 取值。
 *
 * ⚠️ **反转映射**：`10 → 0.9`（弱）`100 → 0`（强）。这个映射只有这一份 ——
 * DOM 写入（`applyIntensity`）与导出参数（`ExportMenu` / `ExportPanel` 的
 * `texture_opacity`）**共用它**。两处各写一遍 `(100-v)/100` 的话，改映射必漏一处，
 * 症状是「预览里调了强度，导出还是老样子」而两边都不报错。
 *
 * `toFixed(2)` 是给导出侧看的：后端 `_TEXTURE_OPACITY_RE` 是纯数字白名单
 * （`^(?:0|1|0?\.\d+)$`），固定两位小数让它**不必容忍科学计数法**（滑条若不是
 * 整数步长，`String()` 可能给出 `5e-2` 这类形态）。对 `opacity` 而言两位小数
 * 语义无损，故 DOM 侧直接用同一个字符串，不存在「预览与导出强度差一点」。
 */
export function textureOpacity(value: number): string {
  return ((100 - value) / 100).toFixed(2)
}

export function applyIntensity(value: number): void {
  document.documentElement.style.setProperty('--texture-opacity', textureOpacity(value))
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
