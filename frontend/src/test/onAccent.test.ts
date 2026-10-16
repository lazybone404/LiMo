/**
 * P1-16 — 主色实底上的前景色是**算出来的**，不是写死的。
 *
 * 修复前 33 处 `bg-accent` 一律配 `text-white`。而主色是从 HSL 原语派生的
 * （26 套配色 × 明暗两档亮度），白字在 dark 下（亮度 62%）对比度只有 ~2.9:1，
 * 最差的「金桂」1.87:1 —— 低于 WCAG AA 的 4.5:1，也低于大字档 3:1。
 *
 * ⚠️ **本文件的配色表是从 `styles/theme-layer.css` 解析出来的，不是抄的**：抄一份的话，
 *    新增第 27 套配色时这里不会红、只是**少覆盖一套**（静默漏检）。解析则自动纳入，
 *    且解析失败会以「数量不对」直接暴露（下面第一条断言就是干这个的）——模式 #54。
 *
 *    ⚠️ 2026-10-16 起这份表住在 `theme-layer.css`（原 `index.css`）—— 搬家的理由是
 *    同一份数据要同时给后端导出用。**路径写错不会报错，只会解析出 0 套**，
 *    而 `expect(ACCENTS.length).toBe(26)` 会当场红 —— 这条断言正是为这个而设。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { hslLuminance, contrastRatio, pickOnAccent, applyOnAccent, ON_ACCENT_CANDIDATES } from '../lib/onAccent'

const themeLayerCss = readFileSync(resolve(process.cwd(), 'src/styles/theme-layer.css'), 'utf-8')

/** 从 `[data-accent="x"] { --accent-hue: N; --accent-sat: M; … }` 解析出全部配色。 */
const ACCENTS = [...themeLayerCss.matchAll(
  /\[data-accent="([\w-]+)"\]\s*\{\s*--accent-hue:\s*(\d+);\s*--accent-sat:\s*(\d+);/g,
)].map(m => ({ key: m[1], h: Number(m[2]), s: Number(m[3]) }))

/** 两档主色亮度 —— 与主题层的 `--accent-lit`（`:root` 50% / `dark` 62%）同值。 */
const LIGHT_L = 50
const DARK_L = 62

/**
 * 合格线。WCAG AA 正文要 4.5:1；这里取 4.3 是因为 **`slate` 在 light 下**
 * （h220 s15 l50）无论白字还是墨字都到不了 4.5（白字 4.37 已是两者更优）——
 * 那是主色亮度 50% 本身的天花板，「两者取优」在该色值下已是最优解。
 * ⚠️ 这个例外是**记录在案**的，不是把线调低到「怎么都能过」：改回写死白字时，
 *    dark 档最差的 1.87 会远远低于它（见下面那条对照）。
 */
const MIN_RATIO = 4.3

describe('P1-16 — on-accent 前景色选择', () => {
  it('★ 配色表解析自 theme-layer.css（解析挂了这条先红，避免下面空转恒真）', () => {
    expect(ACCENTS.length).toBe(26)
    expect(ACCENTS.map(a => a.key)).toContain('sky')
    expect(ACCENTS.map(a => a.key)).toContain('osmanthus')
  })

  it('★ 26 套 × 明暗两档：选出的前景都过合格线', () => {
    const bad: string[] = []
    for (const { key, h, s } of ACCENTS) {
      for (const l of [LIGHT_L, DARK_L]) {
        const { ratio, onInk } = pickOnAccent(h, s, l)
        if (ratio < MIN_RATIO) bad.push(`${key}@l${l}: ${ratio.toFixed(2)} (${onInk ? '墨' : '白'})`)
      }
    }
    expect(bad, `这些配色选出的前景仍不达标：\n${bad.join('\n')}`).toEqual([])
  })

  it('★ 亮主色配深字、暗主色配白字 —— 同一亮度下色相才是决定因素', () => {
    // 同为 L=50%：黄色比蓝色亮三倍还多。这正是「一个固定前景色通吃 26 套」不成立的原因，
    // 也是「按色相分档写死两张表」也不行（26 套要分 26 档）的原因。
    expect(pickOnAccent(40, 75, LIGHT_L).onInk).toBe(true)    // 金桂：亮黄 → 深字
    expect(pickOnAccent(225, 60, LIGHT_L).onInk).toBe(false)  // 深海：蓝 → 白字
    // 同一套配色换明暗档，选择可以翻转（金桂 dark 更亮，墨字对比度更高）
    expect(pickOnAccent(40, 75, DARK_L).onInk).toBe(true)
  })

  it('★ 对照：修复前的「一律白字」在 dark 下大面积不达标', () => {
    // 这条是「为什么要改」的实证 —— 报告给的 2.9:1 / 1.9:1 在这里变成可执行的断言
    const whiteOnDark = ACCENTS.map(a => contrastRatio(ON_ACCENT_CANDIDATES.white.lum, hslLuminance(a.h, a.s, DARK_L)))
    expect(Math.min(...whiteOnDark)).toBeLessThan(3)              // 最差的连大字档都够不上
    expect(whiteOnDark.filter(r => r < 4.5).length).toBeGreaterThan(20)  // 绝大多数不达 AA
  })

  it('纯函数自证：亮度与对比度的量纲正确', () => {
    expect(hslLuminance(0, 0, 100)).toBeCloseTo(1, 3)   // 白
    expect(hslLuminance(0, 0, 0)).toBeCloseTo(0, 3)     // 黑
    expect(hslLuminance(0, 100, 50)).toBeCloseTo(0.2126, 3)  // 纯红 = 红通道系数
    // h=360 与 h=0 同色 —— 不归一化的话会落进最后一个区间分支，算出别的颜色
    expect(hslLuminance(360, 100, 50)).toBeCloseTo(hslLuminance(0, 100, 50), 6)
    // 对比度与顺序无关，且黑白之间为 21:1
    expect(contrastRatio(1, 0)).toBeCloseTo(21, 1)
    expect(contrastRatio(0, 1)).toBeCloseTo(contrastRatio(1, 0), 6)
  })

  it('applyOnAccent 在无样式引擎的环境下静默返回（不抛、不写坏 token）', () => {
    // jsdom 的 getComputedStyle 对自定义属性返回空串 → parseFloat 得 NaN。
    // 没有这条守卫，测试环境里每次 applyTheme/applyAccent 都会写进一个无意义的值。
    expect(() => applyOnAccent()).not.toThrow()
  })
})
