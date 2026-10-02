/**
 * 主题六轴 slice — theme/accent/texture/font/base + 一键主题包。
 * 完全自包含：只依赖 lib/ 主题模块，零跨域引用。
 */
import type { StateCreator } from 'zustand'
import { type ThemeMode, getSavedTheme, resolveTheme, applyTheme, saveTheme } from '../lib/theme'
import { type AccentColor, getSavedAccent, applyAccent, saveAccent } from '../lib/accent'
import { type TexturePreset, type FontPreset, getSavedTexture, getSavedFont, applyTexture, applyFont, saveTexture, saveFont } from '../lib/texture'
import { type BasePreset, getSavedBase, applyBase, saveBase } from '../lib/theme-base'
import { type ReadingPrefs, getSavedReading, applyReading, saveReading } from '../lib/reading-prefs'
import { THEME_PRESETS } from '../lib/theme-presets'
import type { Store } from './types'

export interface ThemeSlice {
  // ── 主题 ──
  theme: ThemeMode
  resolvedTheme: 'light' | 'dark'
  setTheme: (t: ThemeMode) => void

  // ── 主色调 ──
  accent: AccentColor
  setAccent: (a: AccentColor) => void

  // ── 预览区纹理 ──
  texture: TexturePreset
  setTexture: (t: TexturePreset) => void

  // ── 阅读字体 ──
  font: FontPreset
  setFont: (f: FontPreset) => void

  // ── 界面基调 ──
  base: BasePreset
  setBase: (b: BasePreset) => void

  // ── 阅读版式（行高 / 每行字数 / 段落区隔） ──
  // ⚠️ 放这里不是因为它属于「六轴」——六轴描述界面，版式描述正文。
  // 同族的原因是它与 texture/font 一样是「影响预览观感的外观设置」。
  // **刻意不进 THEME_PRESETS**（applyPreset 不碰它）：版式关乎阅读生理，
  // 不该被「换一个主题包」连带重置。镜像进 store 的用途是**设置页能渲染当前选择**
  // ——这些值只被 CSS 变量消费，改它们本身不需要 React 重渲染。
  reading: ReadingPrefs
  setReading: (patch: Partial<ReadingPrefs>) => void

  // ── 一键主题包 ──
  applyPreset: (name: string) => void
}

export const createThemeSlice: StateCreator<Store, [], [], ThemeSlice> = (set) => ({
  theme: getSavedTheme(),
  resolvedTheme: resolveTheme(getSavedTheme()),
  setTheme: (mode) => {
    saveTheme(mode)
    const resolved = resolveTheme(mode)
    applyTheme(resolved)
    set({ theme: mode, resolvedTheme: resolved })
  },

  accent: getSavedAccent(),
  setAccent: (accent) => {
    saveAccent(accent)
    applyAccent(accent)
    set({ accent })
  },

  texture: getSavedTexture(),
  setTexture: (texture) => {
    saveTexture(texture)
    applyTexture(texture)
    set({ texture })
  },

  font: getSavedFont(),
  setFont: (font) => {
    saveFont(font)
    applyFont(font)
    set({ font })
  },

  base: getSavedBase(),
  setBase: (base) => {
    saveBase(base)
    applyBase(base)
    set({ base })
  },

  // patch 形式（三个值合成一个 JSON key 落盘）；用函数式 set 拿到上一个值做合并。
  // ⚠️ 启动链（store/index.ts）另有 applyReading —— **这一行不能省**，
  // 初值只把值读进内存，DOM 上的 --read-line-height / data-read-para 得靠启动链写。
  // 这是 --texture-opacity 冷启动 bug 的同一形态（值在 store 里、DOM 没被写）。
  reading: getSavedReading(),
  setReading: (patch) => set(s => {
    const next = { ...s.reading, ...patch }
    saveReading(next)
    applyReading(next)
    return { reading: next }
  }),

  applyPreset: (name) => {
    const p = THEME_PRESETS.find(pr => pr.name === name)
    if (!p) return
    applyBase(p.base); saveBase(p.base)
    applyAccent(p.accent); saveAccent(p.accent)
    applyTexture(p.texture); saveTexture(p.texture)
    applyFont(p.font); saveFont(p.font)
    set({ base: p.base, accent: p.accent, texture: p.texture, font: p.font })
  },
})
