/**
 * on-accent 前景色 —— 主色实底之上该写白字还是深字（P1-16）
 *
 * **问题**：全库有 33 处 `bg-accent` 实底按钮，前景一律写死 `text-white`。而主色是
 * 从 HSL 原语派生的（26 套 accent × 明暗两档亮度），既定色值下白字对比度只有 ~2.9:1
 * （「暖阳」1.9:1），低于 WCAG AA 的 4.5:1，连大字档 3:1 都不够。且**没有任何一个固定
 * 前景色能在 26 套色里通用**：色相才是决定感知亮度的那个变量 —— 同一个 L=50%，黄色比
 * 蓝色亮三倍还多，前者配白字 2.3:1、后者配白字 6.1:1。所以这必须是**算出来的**。
 *
 * **为什么算在 JS 而不是 CSS**：CSS 没有「感知亮度超过阈值就取另一个颜色」的能力
 * （`color-contrast()` / 相对颜色要么支持面不够，要么在 WebView2 上版本不可控）。
 * 纯 CSS 只能给出「白」或「墨」之一，而定义域是 26×2 个点。
 *
 * **为什么 JS 不去复制色表**：色相/饱和度住在 `index.css` 的 `[data-accent]` 规则里，
 * 亮度住在 `:root` / `[data-theme="dark"]` 的 `--accent-lit`。本模块**只读**这三个 CSS
 * 变量（`--accent-hue` / `--accent-sat` / `--accent-lit`），自己一个色值都不存 —— 前端
 * 再存一份表就是模式 #54 那个「同源是搜索题不是记忆题」的副本，将来加第 27 套配色时
 * 漏改一处，症状是新配色静默用上错误的字色。
 *
 * ⚠️ 已知残余：`slate` 在 light 下（h220 s15 l50）无论白字还是墨字都到不了 4.5
 *    （白字 4.37 是两者的更优解）。这是 accent 亮度 50% 本身的天花板，「两者取优」
 *    已是该色值下的最优，要再高只能压低主色亮度 —— 属于改设计，不在本轮。
 */

/** WCAG 相对亮度（0 = 黑，1 = 白）。h 单位度，s / l 单位 %。 */
export function hslLuminance(h: number, s: number, l: number): number {
  // h=360 与 h=0 同色，但下面按区间取分量时会落到最后一个 else 分支 → 先归一化
  const hue = ((h % 360) + 360) % 360
  const sn = s / 100
  const ln = l / 100
  const c = (1 - Math.abs(2 * ln - 1)) * sn
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1))
  const m = ln - c / 2
  const [r, g, b] =
    hue < 60 ? [c, x, 0] :
    hue < 120 ? [x, c, 0] :
    hue < 180 ? [0, c, x] :
    hue < 240 ? [0, x, c] :
    hue < 300 ? [x, 0, c] :
    [c, 0, x]
  // sRGB 分量 → 线性光（WCAG 2.x 的 sRGB 线性化）
  const linear = (v: number) => {
    const u = v + m
    return u <= 0.04045 ? u / 12.92 : ((u + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
}

/** WCAG 对比度比值（1~21，与顺序无关）。 */
export function contrastRatio(l1: number, l2: number): number {
  const hi = Math.max(l1, l2)
  const lo = Math.min(l1, l2)
  return (hi + 0.05) / (lo + 0.05)
}

/** 两个候选前景 —— 纯白，与略偏冷调的墨色（比纯黑柔和，不刺眼）。 */
export const ON_ACCENT_CANDIDATES = {
  white: { value: '#FFFFFF', lum: 1 },
  ink: { value: '#1B1B1F', lum: hslLuminance(240, 5, 11) },
} as const

export interface OnAccentChoice {
  /** 选中的色值（写进 `--tk-on-accent`） */
  color: string
  /** 该色值与主色的对比度（供测试/调试；不参与运行时渲染） */
  ratio: number
  /** 选中了墨色（而非白） */
  onInk: boolean
}

/** 在「白」与「墨」之间取对比度更高的那个 —— 给定主色下的最优解。 */
export function pickOnAccent(h: number, s: number, l: number): OnAccentChoice {
  const accent = hslLuminance(h, s, l)
  const { white, ink } = ON_ACCENT_CANDIDATES
  const ratioWhite = contrastRatio(white.lum, accent)
  const ratioInk = contrastRatio(ink.lum, accent)
  return ratioInk > ratioWhite
    ? { color: ink.value, ratio: ratioInk, onInk: true }
    : { color: white.value, ratio: ratioWhite, onInk: false }
}

/**
 * 读当前 CSS 里的主色三参数 → 选优 → 内联写 `--tk-on-accent`。
 *
 * 内联样式优先级高于 `:root` 规则，故 CSS 里那份 `#FFFFFF` 是**首帧兜底**（本函数还没
 * 跑时按白字显示）。切 accent 或切明暗后都必须重算 —— 因此调用点放在 `applyAccent()` /
 * `applyTheme()` 内部，而不是某个组件里（任何走这两个入口的路径都自动带上，模式 #51：
 * 「能力存在」要保证「该路径真的在用」）。
 *
 * ⚠️ jsdom 下 `getComputedStyle` 对自定义属性返回空串 → `parseFloat` 得 NaN。这不是
 *    错误而是「没有样式引擎」，直接 return（CSS 兜底值仍在），否则测试里全是噪音。
 */
export function applyOnAccent(): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  const cs = getComputedStyle(root)
  const h = parseFloat(cs.getPropertyValue('--accent-hue'))
  const s = parseFloat(cs.getPropertyValue('--accent-sat'))
  const l = parseFloat(cs.getPropertyValue('--accent-lit'))
  if (!Number.isFinite(h) || !Number.isFinite(s) || !Number.isFinite(l)) return
  root.style.setProperty('--tk-on-accent', pickOnAccent(h, s, l).color)
}
