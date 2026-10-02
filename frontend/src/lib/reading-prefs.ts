/**
 * LiMo — 阅读版式（「纸感」的实质维度）
 *
 * 与六轴的关系：**不是第七轴**。六轴描述「界面长什么样」，版式描述「正文怎么排」。
 * 放在同一族（themeSlice）只因为它与 texture / font 一样是「影响预览观感的外观设置」。
 * ⚠️ **刻意不进 `THEME_PRESETS`**：版式关乎阅读生理（行高、每行字数），
 * 不该被「换一个主题包」连带重置。
 *
 * ⚠️⚠️ **默认值 = 现状**（等于 `github-markdown-css` 的硬编码）：
 *
 *   lineHeight 1.5   ← vendor `.markdown-body { line-height: 1.5 }`
 *   paraStyle  'web' ← text-indent:0 + margin-bottom:10px，正是 vendor 的现值
 *   measure    null  ← 不设上限，沿用现有的「按容器宽度取比例」制
 *
 * 所以**什么都没调时，渲染结果与改造前逐值相同**。这不是巧合而是设计约束：
 * 版式是「用户主动调才变」的偏好，不是强加的新默认观感。
 * 见 `test/readingPrefs.test.ts` —— 零回归是被断言钉住的，不是靠自觉。
 *
 * 依据：W3C《中文排版需求》(clreq) + GB/T 15834-2011。
 * 参数取值见计划 §16.3（字号 16-18px / 行高 1.5-1.8 / 每行 30-40 中文字 / 段距≈一个行高）。
 */

export type ParaStyle = 'web' | 'book'

export interface ReadingPrefs {
  /** 正文行高（1.4-2.0）。默认 1.5 = vendor 现值 */
  lineHeight: number
  /** 每行字数上限。`null` = 不设上限（沿用现有比例制）。作为 `min(比例值, 本值×16px)` 的上限使用 */
  measure: number | null
  /**
   * 段落区隔方式 —— clreq 明确「首行缩进」与「加大段距」是**两套并行的传统，不建议混用**
   * （又缩排又加大段距 → 段落起点被拉太靠后，两种区隔信号叠加不加乘，只显累赘）。
   * 故这里是**二选一的单选**，而不是「缩进」+「段距」两个自由旋钮 —— 后者能拼出被禁止的组合。
   */
  paraStyle: ParaStyle
}

/** ⚠️ 每一项都必须等于「改造前的现状」—— 改这里就是改变所有用户的默认观感 */
export const READING_DEFAULTS: ReadingPrefs = {
  lineHeight: 1.5,
  measure: null,
  paraStyle: 'web',
}

export const LINE_HEIGHT_RANGE = { min: 1.4, max: 2.0, step: 0.05 } as const
/** 每行字数区间。中文排版建议 30-40，两端留余量；下限 30 是「再窄就不成句」的实用边界 */
export const MEASURE_RANGE = { min: 30, max: 90, step: 2 } as const
/** 勾选「限制每行字数」时的初值 —— 40 字 ≈ 640px，落在中文建议区间内 */
export const MEASURE_DEFAULT_ON = 40

/**
 * 版宽换算的基准字号。
 * ⚠️ **必须写死 16**，不能取当前渲染字号：`.markdown-body` 的字号被 vendor 绝对固定在
 * `16px`，而 `.preview-content` 上的内联 `fontSize: fontSizeScale[...]em`（0.92/1.0/1.06）
 * 对正文**根本不生效**。若跟着后者算，版宽会随「紧凑/常规/宽松」档漂移 —— 方向正好反了。
 */
export const READING_BASE_FONT_PX = 16

export const PARA_STYLE_OPTIONS: { key: ParaStyle; label: string; hint: string }[] = [
  { key: 'web', label: '网页式', hint: '不缩进 + 段间距 ≈ 一个行高。屏幕长文，扫描快' },
  { key: 'book', label: '书籍式', hint: '首行缩进 2 字 + 段距 ≈ 行距。中文出版物传统，最像纸书' },
]

const READING_KEY = 'mbw_reading'

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v))
}

/**
 * 读 localStorage + **逐字段校验**。
 * 单项越界/类型错只退回**该项**默认值，不整包丢弃 —— 一个坏字段不该带走另外两个。
 */
export function getSavedReading(): ReadingPrefs {
  try {
    const raw = localStorage.getItem(READING_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<ReadingPrefs>
      return {
        lineHeight: typeof v.lineHeight === 'number' && Number.isFinite(v.lineHeight)
          ? clamp(v.lineHeight, LINE_HEIGHT_RANGE.min, LINE_HEIGHT_RANGE.max)
          : READING_DEFAULTS.lineHeight,
        // ⚠️ 判 `> 0` 而非 `!= null`：0 与负数都不是有效字号，一律归为「不限」
        measure: typeof v.measure === 'number' && Number.isFinite(v.measure) && v.measure > 0
          ? clamp(Math.round(v.measure), MEASURE_RANGE.min, MEASURE_RANGE.max)
          : null,
        paraStyle: v.paraStyle === 'book' || v.paraStyle === 'web'
          ? v.paraStyle
          : READING_DEFAULTS.paraStyle,
      }
    }
  } catch { /* 损坏 JSON → 全部用默认 */ }
  return { ...READING_DEFAULTS }
}

/**
 * 写 DOM —— 两种写入方式本项目都用过（texture/font 走 `data-*`，intensity 走 CSS 变量），
 * 这里两者都需要：行高要参与 `calc()` 所以是变量，段落样式是二选一所以是属性。
 */
export function applyReading(p: ReadingPrefs): void {
  const root = document.documentElement
  root.style.setProperty('--read-line-height', String(p.lineHeight))
  root.setAttribute('data-read-para', p.paraStyle)
  // 注意：measure 不写 DOM —— 它是 JS 侧计算 maxWidth 的输入（见 PreviewArea 的
  // computeContentMaxW）。若做成 CSS 变量就得用 em，而 em 会随上面的 fontSizeScale 漂移。
}

export function saveReading(p: ReadingPrefs): void {
  try { localStorage.setItem(READING_KEY, JSON.stringify(p)) } catch { /* quota */ }
}

// ── 阅读版宽 ────────────────────────────────────────────────

/**
 * 内容宽度三档的「容器占比」。
 * ⚠️ 与 `PreviewArea.tsx` 的 `WIDTH_RATIO` **同源**（那边直接引用这里，不再写字面量）——
 * 这两处曾经是同一份数字的两个副本，改一处忘另一处会让「版宽 = 比例 与 上限取小」
 * 的语义悄悄分叉。
 */
export const CONTENT_WIDTH_RATIO: Record<string, number> = {
  narrow: 0.80,
  normal: 0.90,
  wide: 0.97,
}

/**
 * 计算预览内容区的 `maxWidth`（px）。**纯函数**，抽出来是为了能单测。
 *
 * `measurePx === null` → **逐值复现改造前的比例制**（零回归）；给定时取两者更小的一条。
 * 上限的物理含义：每行中文字数 × 16px（`.markdown-body` 的实际字号）。
 *
 * ⚠️ **为什么不在 CSS 里用 em 表达每行字数**：`maxWidth` 应用在 `.preview-content` 上，
 * 而它带内联 `fontSize: fontSizeScale[contentWidth]em`（0.92 / 1.0 / 1.06）——写成
 * `34em` 会随宽度档漂移成 ~500/544/577px，**版宽跟着宽度模式变，方向正好反了**。
 * 且 `.markdown-body` 里 vendor 硬写 `font-size:16px`，那是**另一个** em 基准。
 * 故在 JS 里用固定 16px 换算。
 */
export function computeContentMaxW(containerWidth: number, ratio: number, measurePx: number | null): number {
  const cap = Math.max(1100, containerWidth - 160)
  const byRatio = Math.min(containerWidth * ratio, cap)
  return measurePx ? Math.min(byRatio, measurePx) : byRatio
}

