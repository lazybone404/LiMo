/**
 * LiMo — 一键主题包
 *
 * 5 个预设，一键设置 base + accent + texture + font 四轴。
 * 用户仍可手动微调任一轴（微调后自动切到"自定义"）。
 */

import type { BasePreset } from './theme-base'
import type { AccentColor } from './accent'
import type { TexturePreset, FontPreset } from './texture'

export interface ThemePreset {
  name: string
  label: string
  base: BasePreset
  accent: AccentColor
  texture: TexturePreset
  font: FontPreset
}

export const THEME_PRESETS: ThemePreset[] = [
  {
    name: 'shuxiang', label: '书香',
    base: 'warm', accent: 'ink', texture: 'xuan', font: 'wenkai',
  },
  {
    name: 'jianjing', label: '简净',
    base: 'neutral', accent: 'sky', texture: 'plain', font: 'system',
  },
  {
    name: 'fugu', label: '复古',
    base: 'cream', accent: 'osmanthus', texture: 'parchment', font: 'songti',
  },
  {
    name: 'qingzhu', label: '青竹',
    base: 'warm', accent: 'forest', texture: 'bamboo', font: 'fangsong',
  },
]
