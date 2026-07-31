/**
 * 主题六轴 slice — theme/accent/texture/font/base + 一键主题包。
 * 完全自包含：只依赖 lib/ 主题模块，零跨域引用。
 */
import type { StateCreator } from 'zustand'
import { type ThemeMode, getSavedTheme, resolveTheme, applyTheme, saveTheme } from '../lib/theme'
import { type AccentColor, getSavedAccent, applyAccent, saveAccent } from '../lib/accent'
import { type TexturePreset, type FontPreset, getSavedTexture, getSavedFont, applyTexture, applyFont, saveTexture, saveFont } from '../lib/texture'
import { type BasePreset, getSavedBase, applyBase, saveBase } from '../lib/theme-base'
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
