/**
 * 阅读版式 —— 偏好读写 + 版宽数学。
 *
 * 本文件最重要的一组是**零回归**：这套偏好是「用户主动调才变」的，不调时渲染结果
 * 必须与改造前**逐值相同**。它有三个出口（CSS 变量 / data-* 属性 / JS 算出的 maxWidth），
 * 每个出口的默认值都在这条链上，任一变了对所有既有用户都是一次静默的观感变更。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. `READING_DEFAULTS.lineHeight` 写成 1.7（「中文排版建议 1.5-1.8 嘛」）→ 红
 *   B. `computeContentMaxW` 的 `measurePx === null` 分支漏掉 → 返回 null →
 *      `maxWidth: "nullpx"`（**不报错**，浏览器忽略该声明 → 内容铺满全宽）→ 红
 *   C. `measure` 用 `!= null` 判断而非 `> 0` → 存进去的 0 被当成有效值 →
 *      `maxWidth: min(比例值, 0)` = 0px → **内容整块消失** → 红
 *   D. 单项损坏时整包丢弃 → 用户改过的行高被一个坏掉的 paraStyle 一并重置 → 红
 *   E. `measurePx` 忘乘 16（直接拿「字数」当 px）→ 40 字 → 40px 版宽 → 红
 *   F. `nextFontScale` 不做四舍五入 → `0.8 + 0.1*3 = 1.0999999999999999` →
 *      界面上出现「字号 110.00000000000001%」→ 红
 *   G. 字号写成 em 而非 px → 与 `.preview-content` 的内联 `fontSize: …em` **相乘** →
 *      字号随宽度档漂移 → 红
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  READING_DEFAULTS,
  LINE_HEIGHT_RANGE,
  MEASURE_RANGE,
  FONT_SCALE_RANGE,
  CONTENT_WIDTH_RATIO,
  READING_BASE_FONT_PX,
  PARA_STYLE_OPTIONS,
  computeContentMaxW,
  nextFontScale,
  measurePxOf,
  getSavedReading,
  applyReading,
  saveReading,
} from '../lib/reading-prefs'

const KEY = 'mbw_reading'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-read-para')
  document.documentElement.style.removeProperty('--read-line-height')
  document.documentElement.style.removeProperty('--read-font-size')
})

// ── 零回归：默认值必须等于改造前的现状 ──────────────────────

describe('零回归 — 默认值 = 改造前的现状', () => {
  it('READING_DEFAULTS 逐值等于既有实现的值', () => {
    // 对照 A：任何一项被「顺手改成更合理的值」→ 红
    expect(READING_DEFAULTS.lineHeight).toBe(1.5)   // = github-markdown-css 的 .markdown-body line-height
    expect(READING_DEFAULTS.measure).toBe(null)     // = 不设上限，沿用「按容器宽度取比例」制
    expect(READING_DEFAULTS.paraStyle).toBe('web')  // = text-indent:0 + margin-bottom:10px（vendor 现值）
  })

  it('无存储时 getSavedReading() 返回默认', () => {
    expect(getSavedReading()).toEqual(READING_DEFAULTS)
  })

  it('段落式只有两个取值，且 web 是第一个（默认项）', () => {
    expect(PARA_STYLE_OPTIONS.map(o => o.key)).toEqual(['web', 'book'])
  })
})

// ── 版宽数学 ────────────────────────────────────────────────

describe('computeContentMaxW', () => {
  /** 改造前的实现，逐字抄自 PreviewArea（作为对照真值，**刻意不引用 CONTENT_WIDTH_RATIO**） */
  const legacy = (w: number, ratio: number) => Math.min(w * ratio, Math.max(1100, w - 160))

  it('measurePx === null 时逐值复现旧的三档行为', () => {
    // 对照 B：null 分支漏掉/写反 → 既有行为被破坏 → 红
    for (const w of [400, 600, 800, 1200, 1920, 2560]) {
      expect(computeContentMaxW(w, CONTENT_WIDTH_RATIO.narrow, null)).toBe(legacy(w, 0.80))
      expect(computeContentMaxW(w, CONTENT_WIDTH_RATIO.normal, null)).toBe(legacy(w, 0.90))
      expect(computeContentMaxW(w, CONTENT_WIDTH_RATIO.wide, null)).toBe(legacy(w, 0.97))
    }
  })

  it('CONTENT_WIDTH_RATIO 三档与旧 WIDTH_RATIO 字面量同值', () => {
    // 这两处曾是同一份数字的两个副本；改成单源后，这条断言守住「数字本身没被改」
    expect(CONTENT_WIDTH_RATIO).toEqual({ narrow: 0.80, normal: 0.90, wide: 0.97 })
  })

  it('给定 measurePx 时取两者更小的一条（两个方向都要覆盖）', () => {
    // 对照 E：measurePx 忘乘 16 → 断具体 px 值才能抓到（只断 `<=` 会放过）
    // 1200 容器 normal：byRatio = min(1080, 1040) = 1040 > 640 → 受度量限
    expect(computeContentMaxW(1200, 0.90, 40 * READING_BASE_FONT_PX)).toBe(640)
    // 600 容器 normal：byRatio = min(540, 1100) = 540 < 640 → 受比例限
    expect(computeContentMaxW(600, 0.90, 40 * READING_BASE_FONT_PX)).toBe(540)
  })

  it('小容器下 1100px 保底不构成约束（cap 只抬高上限，不抬高内容）', () => {
    expect(computeContentMaxW(300, 0.90, null)).toBe(270)
  })

  it('极窄度量（30 字 = 480px）在宽容器上生效', () => {
    expect(computeContentMaxW(2560, 0.97, MEASURE_RANGE.min * READING_BASE_FONT_PX)).toBe(480)
  })

  it('换算基准是 16px（= .markdown-body 的实际字号，不随宽度档漂移）', () => {
    // 对照：改成随 fontSizeScale 漂移的基准 → 版宽跟着「紧凑/宽屏」档变化 → 红
    expect(READING_BASE_FONT_PX).toBe(16)
  })
})

// ── 偏好读写 ────────────────────────────────────────────────

describe('getSavedReading — 逐字段校验', () => {
  it('损坏 JSON → 全部用默认（不抛异常）', () => {
    localStorage.setItem(KEY, '{not json')
    expect(getSavedReading()).toEqual(READING_DEFAULTS)
  })

  it('单个坏字段只退回该项默认，不带走另外三个', () => {
    // 对照 D：整包丢弃的实现 → lineHeight/paraStyle 也被重置 → 红
    localStorage.setItem(KEY, JSON.stringify({ lineHeight: 1.8, measure: 'x', paraStyle: 'book' }))
    expect(getSavedReading()).toEqual({ lineHeight: 1.8, measure: null, paraStyle: 'book', fontScale: 1.0 })
  })

  it('lineHeight 越界被 clamp 进区间', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, lineHeight: 9 }))
    expect(getSavedReading().lineHeight).toBe(LINE_HEIGHT_RANGE.max)
  })

  it('measure 为 0 或负数一律归为「不限」(null)', () => {
    // 对照 C：`!= null` 判断的实现会把 0 当有效值 → 版宽 0px → 内容消失 → 红
    for (const bad of [0, -5, -0.5]) {
      localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, measure: bad }))
      expect(getSavedReading().measure).toBe(null)
    }
  })

  it('measure 超上限被 clamp', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, measure: 999 }))
    expect(getSavedReading().measure).toBe(MEASURE_RANGE.max)
  })

  it('measure 有效值时取整', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, measure: 37.6 }))
    expect(getSavedReading().measure).toBe(38)
  })

  it('paraStyle 非法值退回 web', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, paraStyle: 'bookish' }))
    expect(getSavedReading().paraStyle).toBe('web')
  })
})

describe('saveReading / applyReading', () => {
  it('save → get 往返一致', () => {
    const p = { lineHeight: 1.65, measure: 36, paraStyle: 'book' as const, fontScale: 1.2 }
    saveReading(p)
    expect(getSavedReading()).toEqual(p)
  })

  it('applyReading 把行高写进 CSS 变量、把段落式写进 data-read-para', () => {
    // 两个出口形态不同（变量 vs 属性）是有意的：行高要参与 calc()，段落式是二选一。
    // 对照：把 lineHeight 也写成 data-* → CSS 取不到变量 → 回落到 :root 的 1.5 → 红
    applyReading({ lineHeight: 1.7, measure: 40, paraStyle: 'book', fontScale: 1.0 })
    expect(document.documentElement.style.getPropertyValue('--read-line-height')).toBe('1.7')
    expect(document.documentElement.getAttribute('data-read-para')).toBe('book')
  })

  it('applyReading 不把 measure 写进 DOM（它是 JS 侧算 maxWidth 的输入）', () => {
    // 做成 CSS 变量就得用 em，而 em 会随 fontSizeScale 漂移（见 lib 注释）
    applyReading({ lineHeight: 1.5, measure: 48, paraStyle: 'web', fontScale: 1.0 })
    expect(document.documentElement.getAttribute('data-read-measure')).toBe(null)
  })
})

// ── 正文字号（需求 B）───────────────────────────────────────

describe('fontScale — 状态与落点', () => {
  it('默认 1.0，且它就是现状的 16px（零回归）', () => {
    // 对照：默认写成 1.1「因为 16px 有点小」→ 所有既有用户的观感被静默改动 → 红
    expect(READING_DEFAULTS.fontScale).toBe(1.0)
    expect(getSavedReading().fontScale).toBe(1.0)
  })

  it('applyReading(READING_DEFAULTS) 写出的 --read-font-size 就是 16px', () => {
    applyReading(READING_DEFAULTS)
    expect(document.documentElement.style.getPropertyValue('--read-font-size')).toBe('16px')
  })

  it('--read-font-size 随 fontScale 等比变化，且单位是 px', () => {
    // 对照 G：写成 em 的实现 → 这里拿到 '1.5em' → 红。
    // 值本身也要断：写死 16px 而不乘 scale 的实现只会在这里红。
    applyReading({ ...READING_DEFAULTS, fontScale: 1.5 })
    expect(document.documentElement.style.getPropertyValue('--read-font-size')).toBe('24px')
    applyReading({ ...READING_DEFAULTS, fontScale: 0.8 })
    expect(document.documentElement.style.getPropertyValue('--read-font-size')).toBe('12.8px')
  })

  it('取值越界被 clamp 进 [0.8, 1.6]', () => {
    for (const [bad, want] of [[0.5, FONT_SCALE_RANGE.min], [5, FONT_SCALE_RANGE.max]] as const) {
      localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, fontScale: bad }))
      expect(getSavedReading().fontScale).toBe(want)
    }
  })

  it('非法类型 / NaN / null 一律回落 1.0', () => {
    for (const bad of ['1.5', NaN, null, {}, true]) {
      localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, fontScale: bad }))
      expect(getSavedReading().fontScale).toBe(READING_DEFAULTS.fontScale)
    }
  })

  it('合法值归一到两位小数（防滑条浮点尾巴）', () => {
    localStorage.setItem(KEY, JSON.stringify({ ...READING_DEFAULTS, fontScale: 1.23456 }))
    expect(getSavedReading().fontScale).toBe(1.23)
  })

  it('坏 fontScale 不带走另外三个', () => {
    localStorage.setItem(KEY, JSON.stringify({ lineHeight: 1.8, measure: 36, paraStyle: 'book', fontScale: 'x' }))
    expect(getSavedReading()).toEqual({ lineHeight: 1.8, measure: 36, paraStyle: 'book', fontScale: 1.0 })
  })
})

describe('nextFontScale — 档位换算（键盘与滚轮共用同一份）', () => {
  it('逐档推进 / 回退', () => {
    expect(nextFontScale(1.0, 1)).toBe(1.1)
    expect(nextFontScale(1.0, -1)).toBe(0.9)
  })

  it('⚠️ 无浮点漂移：从下限连推 8 档恰好落在上限', () => {
    // 对照 F：不 round 的实现 → 0.8+0.1*3 = 1.0999999999999999 → 红
    // `as const` 让 FONT_SCALE_RANGE.min 是字面量类型 0.8，必须显式放宽成 number
    let v: number = FONT_SCALE_RANGE.min
    for (let i = 0; i < 8; i++) v = nextFontScale(v, 1)
    expect(v).toBe(FONT_SCALE_RANGE.max)
    expect(String(v)).toBe('1.6')
  })

  it('到顶 / 到底返回原值（调用方据此静默，滚轮不会刷一屏 toast）', () => {
    expect(nextFontScale(FONT_SCALE_RANGE.max, 1)).toBe(FONT_SCALE_RANGE.max)
    expect(nextFontScale(FONT_SCALE_RANGE.min, -1)).toBe(FONT_SCALE_RANGE.min)
  })

  it('区间是 9 档 80%–160%', () => {
    expect(FONT_SCALE_RANGE).toEqual({ min: 0.8, max: 1.6, step: 0.1 })
    expect(Math.round((FONT_SCALE_RANGE.max - FONT_SCALE_RANGE.min) / FONT_SCALE_RANGE.step) + 1).toBe(9)
  })
})

describe('measurePxOf — 版宽上限与字号同源', () => {
  it('40 字在 1.0 档 = 640px；1.6 档 = 1024px', () => {
    expect(measurePxOf(40, 1.0)).toBe(640)
    expect(measurePxOf(40, 1.6)).toBe(1024)
  })

  it('⚠️ 同一 measure 下不同 fontScale 必须得到**不同**版宽', () => {
    // 对照：漏乘 fontScale 的实现 → 两者都是 640 → 红。
    // 症状是方向反的 —— 字号放大后每行放得下的字更少，版宽却纹丝不动，越放大越挤
    expect(measurePxOf(40, 1.0)).not.toBe(measurePxOf(40, 1.6))
    expect(measurePxOf(30, 0.8)).toBe(384)
  })

  it('measure 为 null → null（沿用「按容器宽度取比例」制，零回归）', () => {
    // 对照 B 的同族：这里返回 0 会让 maxWidth 变成 0px → 内容整块消失
    for (const s of [0.8, 1.0, 1.6]) expect(measurePxOf(null, s)).toBe(null)
  })

  it('与 computeContentMaxW 串起来：放大字号后版宽上限更早生效', () => {
    // 1200 宽容器 / normal 档：byRatio = min(1080, 1040) = 1040
    // 1.0 档 640 < 1040 → 受度量限；0.8 档 512 → 更窄
    expect(computeContentMaxW(1200, 0.90, measurePxOf(40, 1.0)!)).toBe(640)
    expect(computeContentMaxW(1200, 0.90, measurePxOf(40, 0.8)!)).toBe(512)
  })
})
