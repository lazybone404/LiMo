/**
 * LiMo — 一键主题包
 *
 * 20 个预设，一键设置 base + accent + texture + font 四轴。
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
  // ── 护眼系列 ──
  {
    name: 'roulv', label: '柔绿',
    base: 'jade', accent: 'matcha', texture: 'bamboo', font: 'system',
  },
  {
    name: 'nuanyang', label: '暖阳',
    base: 'amber', accent: 'osmanthus', texture: 'kraft', font: 'system',
  },
  {
    name: 'zhenzhu', label: '珍珠',
    base: 'pearl', accent: 'slate', texture: 'plain', font: 'system',
  },
  {
    name: 'bohe', label: '薄荷',
    base: 'mint', accent: 'spearmint', texture: 'plain', font: 'system',
  },
  // ── 经典系列 ──
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
  // ── 粉色系列 ──
  {
    name: 'yingchui', label: '樱吹',
    base: 'blush', accent: 'cherry', texture: 'plain', font: 'kaiti',
  },
  {
    name: 'taoyao', label: '桃夭',
    base: 'rose', accent: 'blossom', texture: 'letter', font: 'fangsong',
  },
  {
    name: 'dingxiang', label: '丁香',
    base: 'lilac', accent: 'lavender', texture: 'plain', font: 'kaiti',
  },
  {
    name: 'mudan', label: '牡丹',
    base: 'pearl', accent: 'peony', texture: 'plain', font: 'songti',
  },
  // ── 雅致系列 ──
  {
    name: 'songyan', label: '松烟',
    base: 'sage', accent: 'pine', texture: 'xuan', font: 'songti',
  },
  {
    name: 'tanxiang', label: '檀香',
    base: 'sand', accent: 'sandalwood', texture: 'parchment', font: 'songti',
  },
  {
    name: 'yunxiu', label: '云岫',
    base: 'cloud', accent: 'indigo', texture: 'plain', font: 'kaiti',
  },
  {
    name: 'shazhou', label: '沙洲',
    base: 'sand', accent: 'coral', texture: 'kraft', font: 'system',
  },
  // ── 国风系列 ──
  {
    name: 'zhusha', label: '朱砂',
    base: 'warm', accent: 'vermillion', texture: 'xuan', font: 'songti',
  },
  {
    name: 'dianlan', label: '靛蓝',
    base: 'cool', accent: 'indigo', texture: 'letter', font: 'kaiti',
  },
  {
    name: 'mailang', label: '麦浪',
    base: 'cream', accent: 'sunset', texture: 'kraft', font: 'system',
  },
  {
    name: 'meigui', label: '玫瑰金',
    base: 'blush', accent: 'rosegold', texture: 'plain', font: 'wenkai',
  },
]
