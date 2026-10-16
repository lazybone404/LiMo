/**
 * LiMo — Accent color theme management
 *
 * 预设主题包：每个主题包含主色 + 辅色的 HSL 原语。
 * 通过 data-accent 属性驱动 CSS hsl() 派生所有语义令牌。
 * 底色保持中性灰阶不变。
 */

import { applyOnAccent } from './onAccent'

export type AccentColor =
  // 现代
  | 'sky' | 'ocean' | 'sunset' | 'forest' | 'lavender' | 'slate'
  | 'coral' | 'spearmint' | 'plum'
  // 国风
  | 'dunhuang' | 'porcelain' | 'ink' | 'rouge' | 'celadon' | 'osmanthus'
  | 'vermillion' | 'indigo' | 'sandalwood'
  // 自然
  | 'matcha' | 'cherry' | 'pine' | 'olive' | 'teal' | 'blossom'
  | 'peony' | 'rosegold'

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
      { key: 'coral',     label: '珊瑚' },
      { key: 'spearmint', label: '薄荷绿' },
      { key: 'plum',      label: '李紫' },
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
      { key: 'vermillion', label: '朱砂' },
      { key: 'indigo',     label: '靛蓝' },
      { key: 'sandalwood', label: '檀' },
    ],
  },
  {
    label: '自然',
    accents: [
      { key: 'matcha',    label: '抹茶' },
      { key: 'cherry',    label: '樱' },
      { key: 'pine',      label: '松' },
      { key: 'olive',     label: '橄榄' },
      { key: 'teal',      label: '水鸭' },
      { key: 'blossom',   label: '杏' },
      { key: 'peony',     label: '牡丹' },
      { key: 'rosegold',  label: '玫瑰金' },
    ],
  },
]

/** 全部配色的扁平列表 —— 遍历用；分组视图见 `ACCENT_GROUPS`（同一份数据）。 */
export const ACCENT_OPTIONS: AccentEntry[] = ACCENT_GROUPS.flatMap(g => g.accents)

/**
 * 合法性白名单 —— 校验「localStorage 里存的那个字符串还算不算一套配色」。
 *
 * ⚠️ **必须是 `ACCENT_OPTIONS` 的 key 集合，不能是任何色值表**。这里此前写的是
 * `ACCENT_SWATCH[v]`（色块预览色表）。色块表已于 2026-10-16 删除（色值改由
 * theme-layer.css 的 `[data-accent]` 规则提供，色块用 `data-accent` + `var()` 渲染），
 * 若校验还挂在它上面 → `ACCENT_SWATCH[v]` 恒 `undefined` → **所有已保存的配色
 * 被判非法、静默回退 `sky`**：用户配色被无声重置，**零报错**，只有当事人发现「我选的胭脂没了」。
 *
 * 判据是「这个 key 在我们支持的表里吗」，与「它长什么样」无关 —— 所以校验源是
 * **选项表**，不是色值表。
 */
const ACCENT_KEYS = new Set<string>(ACCENT_OPTIONS.map(a => a.key))

const STORAGE_KEY = 'mbw_accent'

export function getSavedAccent(): AccentColor {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v && ACCENT_KEYS.has(v)) return v as AccentColor
  } catch { /* quota exceeded — ignore */ }
  return 'sky'
}

export function applyAccent(accent: AccentColor): void {
  document.documentElement.setAttribute('data-accent', accent)
  // 主色变了 → 实底上的前景色要重算（P1-16）。挂在入口函数里而不是调用点，
  // 是为了让「切配色」的所有路径（设置页 / 一键主题包 / 启动初始化）都自动带上。
  applyOnAccent()
}

export function saveAccent(accent: AccentColor): void {
  try { localStorage.setItem(STORAGE_KEY, accent) } catch { /* quota exceeded — ignore */ }
}
