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
import { type Skeleton, type CardStyle, type CardPrefs, getSavedCard, applyCard, saveCard } from '../lib/card-prefs'
import { type MarkerPrefs, getSavedMarkers, applyMarkers, saveMarkers } from '../lib/marker-prefs'
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

  // ── 产物骨架 + 卡片视觉方案（`paper`/`card` × 8 套） ──
  // ⚠️ 同上：不是「第七轴」，是「产物正文怎么组织」。**刻意不进 THEME_PRESETS**
  // ——换主题包不该把骨架连带重置。镜像进 store 的用途是设置页能渲染当前选择。
  // ⚠️ 拆成两个扁平字段而非一个嵌套对象：消费者只需要其中一个，扁平字段的选择器
  // 返回值是字符串（引用稳定），嵌套对象会在每次 set 时换引用。
  skeleton: Skeleton
  cardStyle: CardStyle
  // ── 卡片逐维度微调（`null` = 跟随视觉方案）──
  // 与上面两个字段同款：扁平、可序列化、引用稳定。它们同样只被 CSS 变量消费，
  // 镜像进 store 的唯一用途是**设置页的控件能显示当前选择**。
  cardBg: string | null
  cardLift: number | null
  cardBorder: string | null
  setCard: (patch: Partial<CardPrefs>) => void

  // ── 语义标记的装饰开关（`limo-block`） ──
  // ⚠️ 语义是「隐藏标记产生的附加物」而**不是**「隐藏标记」：内容一律保留。
  // 与骨架同款：不是新的一轴，**刻意不进 THEME_PRESETS**（换主题包不该重置它）。
  markerDecor: boolean
  setMarker: (patch: Partial<MarkerPrefs>) => void

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

  // ⚠️ 启动链（store/index.ts）另有 applyCard —— **这一行不能省**：初值只把值读进内存，
  // DOM 上的 data-skeleton / data-card-style 得靠启动链写（--texture-opacity 冷启动 bug 的同一形态）。
  // 两次 getSavedCard() 是同一份纯读，不存在读出不一致的可能。
  skeleton: getSavedCard().skeleton,
  cardStyle: getSavedCard().style,
  cardBg: getSavedCard().bg,
  cardLift: getSavedCard().lift,
  cardBorder: getSavedCard().border,
  setCard: (patch) => set(s => {
    // ⚠️ 合并的**源必须是 store 里的全部字段**而不是只挑两项：漏一个字段的表现是
    // 「改完骨架，微调悄悄回到跟随方案」——patch 里没有的那个字段被默认值覆盖。
    const next: CardPrefs = {
      skeleton: s.skeleton, style: s.cardStyle,
      bg: s.cardBg, lift: s.cardLift, border: s.cardBorder,
      ...patch,
    }
    saveCard(next)
    applyCard(next)
    return {
      skeleton: next.skeleton, cardStyle: next.style,
      cardBg: next.bg, cardLift: next.lift, cardBorder: next.border,
    }
  }),

  // ⚠️ 同上：启动链另有 applyMarkers（DOM 上的 data-marker-decor 靠它写）。
  markerDecor: getSavedMarkers().decor,
  setMarker: (patch) => set(s => {
    const next: MarkerPrefs = { decor: s.markerDecor, ...patch }
    saveMarkers(next)
    applyMarkers(next)
    return { markerDecor: next.decor }
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
