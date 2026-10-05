/**
 * P2-21 — 风格表只有 `capabilities` 一个来源。
 *
 * 缺陷形态：`GenPanel` 里曾有三组各 5 项的硬编码 fallback，与后端真表**漂移**——
 * 后端 video 10 档 / doc 9 档，前端各写 5 档且顺序不同（后端 plain 之后按使用频率排）。
 * 它是 `caps.x || [...]` 形态，所以只有后端没起时才生效，**平时看不出来**；
 * 真生效时用户会以为「就这几种风格」。
 *
 * 本文件测两件事：
 *   ① `pickStyleKeys` / `styleOptions` 的纯函数语义（含「未到位」这个信号本身）
 *   ② 源码守卫：`GenPanel` 不再内联风格表，且保留了「caps 未到位不动 style」的早退
 *
 * 对照实验（证明本文件有区分力）：
 *   A. 把 `pickStyleKeys` 的 `keys?.length ? keys : undefined` 改回 `?? []` 之类
 *      → 「空表也算未到位」红
 *   B. 把 `styleOptions` 改回 `['none', ...(styleKeys ?? FALLBACK_5)]`
 *      → 「不掺进任何硬编码档」红
 *   C. 删掉 `GenPanel` 的 `if (!styleKeys?.length) return`
 *      → 「caps 未到位不动档」红
 */
import { describe, it, expect } from 'vitest'
import { pickStyleKeys, styleOptions, DEFAULT_STYLE, reconcileStyle } from '../components/gen-panel/constants'
import genPanelSrc from '../components/gen-panel/GenPanel.tsx?raw'

/** 后端 capabilities 的真实形态（三张表） */
const CAPS = {
  styles: ['plain', 'detailed', 'business', 'academic', 'tutorial', 'task_oriented', 'minimal', 'meeting', 'life', 'xiaohongshu'],
  docStyles: ['plain', 'detailed', 'business', 'academic', 'tutorial', 'task_oriented', 'minimal', 'meeting', 'imrad'],
  excelStyles: ['detailed', 'plain', 'minimal', 'business'],
}

describe('P2-21 — pickStyleKeys：三路选择', () => {
  it('按 scope 取对应的那张表', () => {
    expect(pickStyleKeys(CAPS, 'media')).toEqual(CAPS.styles)
    expect(pickStyleKeys(CAPS, 'doc')).toEqual(CAPS.docStyles)
    expect(pickStyleKeys(CAPS, 'excel')).toEqual(CAPS.excelStyles)
  })

  it('顺序原样保留（后端按使用频率排，不是字母序）', () => {
    expect(pickStyleKeys(CAPS, 'excel')![0]).toBe('detailed')
    expect(pickStyleKeys(CAPS, 'doc')![0]).toBe('plain')
  })

  it('★ caps 未到位 → undefined（三种形态都算）', () => {
    // 这是本项的核心信号：调用方靠它决定「不拿假表做判定」
    expect(pickStyleKeys(undefined, 'media')).toBeUndefined()
    expect(pickStyleKeys(null, 'media')).toBeUndefined()
    expect(pickStyleKeys({}, 'media')).toBeUndefined()
    expect(pickStyleKeys({ styles: [] }, 'media')).toBeUndefined()   // 空表同「没有」
  })

  it('对照：漏掉「空表也算未到位」这一条就会红', () => {
    // 把实现写成 `return caps?.styles`（不判 length）时空数组会漏出去，
    // 于是 styleOptions 拿到 `[]` → 只渲染 none，且 effect 的早退判据失效。
    const broken = (caps: { styles?: string[] }) => caps?.styles
    expect(broken({ styles: [] })).toEqual([])
    expect(pickStyleKeys({ styles: [] }, 'media')).toBeUndefined()   // 真实实现不含糊
  })
})

describe('P2-21 — styleOptions：未到位时只给默认档', () => {
  it('caps 到位：none + 后端表，**不掺任何硬编码档**', () => {
    const opts = styleOptions(pickStyleKeys(CAPS, 'media'))
    expect(opts).toEqual(['none', ...CAPS.styles])
    // 后端 10 档 → 前端 11 项，一个不少
    expect(opts).toHaveLength(11)
  })

  it('caps 未到位：退化为 none + 默认档（渲染得出选中态即可）', () => {
    expect(styleOptions(undefined)).toEqual(['none', DEFAULT_STYLE])
  })

  it('★ 未到位时不掺进任何「看起来像完整表」的档', () => {
    const opts = styleOptions(undefined)
    // 旧 fallback 里的档一个都不该出现（它们只在真表里由后端下发）
    for (const ghost of ['detailed', 'academic', 'life', 'tutorial', 'imrad', 'business']) {
      expect(opts, `${ghost} 不应出现在未到位的兜底里`).not.toContain(ghost)
    }
  })

  it('★ caps 未到位时，用户的档位**不该被判定为非法**', () => {
    // GenPanel 的 effect 依赖这个性质：它早退，所以 reconcileStyle 不会被调用。
    // 这里从另一侧证明——若有人拿兜底表去判，用户存的 imrad 会被改成 plain，
    // 且后端起来后**不会复原**（值已经变了）。
    const fallback = styleOptions(undefined)
    expect(reconcileStyle('imrad', fallback)).toBe(DEFAULT_STYLE)   // 这就是「拿假表判」的后果
    // 所以生产代码必须早退，而不是「用兜底表判一下也无妨」
  })
})

describe('P2-21 — 源码守卫（模式 #54）', () => {
  it('★ GenPanel 不再内联风格表', () => {
    const ghosts = [
      "'plain', 'minimal', 'detailed', 'academic', 'life'",
      "'plain', 'imrad', 'detailed', 'business', 'tutorial'",
      "'plain', 'detailed', 'minimal', 'business'",
    ]
    for (const g of ghosts) {
      expect(genPanelSrc, `旧 fallback 表残留在 GenPanel：${g}`).not.toContain(g)
    }
    // 三种形态的键名也不该单独出现（防止换个写法又抄一遍）
    expect(genPanelSrc).not.toContain('excelStyles ||')
    expect(genPanelSrc).not.toContain('docStyles ||')
  })

  it('★ caps 未到位时不动 style（早退必须还在）', () => {
    // ⚠️ 这是「后端慢一点启动 = 我的风格没了」的唯一防线。它很容易在后来的
    // 重构里被当成冗余判断删掉，而删掉之后**没有任何功能会坏**——
    // 只在后端冷启动的那几秒里悄悄改掉用户的档位，且不可逆。
    expect(genPanelSrc).toMatch(/if \(!styleKeys\?\.length\) return/)
  })

  it('对照：守卫对旧形态确实会红（否则上面两条是恒真）', () => {
    const old = "caps?.docStyles || ['plain', 'imrad', 'detailed', 'business', 'tutorial']"
    expect(old).toContain('docStyles ||')
    expect(old).toContain("'plain', 'imrad', 'detailed', 'business', 'tutorial'")
    expect("const next = reconcileStyle(style, styleKeys)").not.toMatch(
      /if \(!styleKeys\?\.length\) return/)
  })
})
