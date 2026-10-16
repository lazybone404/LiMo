/**
 * 主题轴注册表 ↔ CSS 的**跨语言一致性**守卫。
 *
 * 为什么需要它：`lib/theme-axes.ts` 说「accent 有 26 套」，`styles/theme-layer.css`
 * 说「[data-accent="x"] 有 26 条」—— 两份真相源各写各的，一致性没有任何东西保证。
 * 二者不一致时的症状**全是静默的**：
 *
 *   - **JS 有、CSS 没**：用户在设置页选中那套配色，**界面纹丝不动、零报错**
 *     （`data-accent` 设上了，没有规则匹配它）。加一套配色时最容易漏。
 *   - **CSS 有、JS 没**：一条永远选不到的死规则（多半是删 key 时忘了删 CSS）；
 *     还会让 `themeAxisSync` 的「CSS 里有实现的都该能选到」失去意义。
 *   - **`attr` 打错**（`data-accents`）：`cssKeysOf` 匹配到 0 条 → 下面第一条断言红。
 *
 * ⚠️ 本文件**不测色值** —— 色值只有一份（CSS），这正是 2026-10-16 档 A 的目标。
 *    它测的是**名字**（key 集合）与**连通性**（setter 真的写到了 store 与 DOM）。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { THEME_AXES, AXIS, type ThemeAxis } from '../lib/theme-axes'
import { THEME_PRESETS } from '../lib/theme-presets'
import { useStore } from '../store'

const themeLayerCss = readFileSync(resolve(process.cwd(), 'src/styles/theme-layer.css'), 'utf-8')

/**
 * 从一段 CSS 里抽出 `[data-x="…"]` 的全部取值。传 css 文本而非闭包常量，是为了能喂假串做对照。
 *
 * ⚠️ **必须先剥注释**：主题层是好写注释的地方，而注释里出现 `[data-x="…"]` 几乎总是
 * **举例说明**（theme-layer.css 第 276 行就写着「这里曾经是 `[data-font="x"] ….preview-area`
 * { font-family }」）。不剥的话它会被当成真规则，报出「死规则 x」——
 * 而那个报错的修复方向（去 CSS 里删规则）与真实情况（注释不用动）**正好相反**。
 * 这个坑是真踩出来的，不是假设。
 */
function cssKeysOf(css: string, attr: string): string[] {
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const re = new RegExp(`\\[${attr}="([\\w-]+)"\\]`, 'g')
  return [...code.matchAll(re)].map(m => m[1])
}

/** JS 里有、CSS 里没规则的 key —— 症状是「选中了却没反应」。 */
function missingCssKeys(axis: ThemeAxis, css: string): string[] {
  const have = new Set(cssKeysOf(css, axis.attr))
  return axis.options.map(o => o.key).filter(k => !have.has(k)).sort()
}

/** CSS 里有规则、JS 里选不到的 key —— 死规则。 */
function orphanCssKeys(axis: ThemeAxis, css: string): string[] {
  const js = new Set(axis.options.map(o => o.key))
  return [...new Set(cssKeysOf(css, axis.attr))].filter(k => !js.has(k)).sort()
}

/**
 * **无 CSS 规则的档** —— 每轴的「默认档」。这张表**必须显式**：不对称本身是设计事实
 * （默认档走 `:root` 的底盘，不需要自己的规则），但从 CSS 只能看出「哪个 key 没规则」，
 * 看不出「没规则是不是故意的」。写在这里 = 把「texture 少一条是故意的」变成可读事实，
 * 而不是让下一个人以为是漏写。**往这里加一项之前先问：这真的是默认档，还是漏了 CSS？**
 */
const NO_RULE_KEYS: Record<string, string[]> = {
  // light 是默认底盘（`[data-theme="dark"]` 只覆盖暗色那一侧）；
  // system 只是一个**输入**，它解析成 light/dark 之后才落到 data-theme 上。
  theme: ['light', 'system'],
  // plain = 不加任何纹理，三个纹理变量一个都不声明。
  texture: ['plain'],
}

describe('主题轴注册表 — 自证', () => {
  it('★ 五条轴（表被清空时下面的循环会全绿空转）', () => {
    expect(THEME_AXES).toHaveLength(5)
    expect(THEME_AXES.map(a => a.key)).toEqual(['theme', 'accent', 'base', 'texture', 'font'])
  })

  it('attr 是 data-* 形式且指向真正的 CSS 属性名', () => {
    for (const axis of THEME_AXES) {
      expect(axis.attr, axis.key).toMatch(/^data-[a-z]+$/)
      // attr 打错（如 data-accents）时匹配到 0 条 → 这条先红，
      // 而不是让后面「缺失 key = 全部 26 个」那种大噪音盖住根因。
      expect(cssKeysOf(themeLayerCss, axis.attr).length, `${axis.key} → [${axis.attr}] 一条都没匹配到`).toBeGreaterThan(0)
    }
  })

  it('options 非空、key 唯一、label 非空', () => {
    for (const axis of THEME_AXES) {
      expect(axis.options.length, axis.key).toBeGreaterThan(1)
      const keys = axis.options.map(o => o.key)
      expect(new Set(keys).size, `${axis.key} 有重复 key`).toBe(keys.length)
      for (const o of axis.options) expect(o.label, `${axis.key}.${o.key}`).not.toBe('')
    }
  })

  it('空 localStorage 下 saved() 落在 options 里（fallback 本身合法）', () => {
    for (const axis of THEME_AXES) {
      expect(axis.options.map(o => o.key), axis.key).toContain(axis.saved())
    }
  })
})

describe('注册表 ↔ theme-layer.css', () => {
  for (const axis of THEME_AXES) {
    it(`${axis.key}: 除默认档外，每个 key 都有 CSS 规则`, () => {
      expect(missingCssKeys(axis, themeLayerCss)).toEqual(NO_RULE_KEYS[axis.key] ?? [])
    })

    it(`${axis.key}: CSS 里的 key 都能被用户选到（无死规则）`, () => {
      expect(orphanCssKeys(axis, themeLayerCss)).toEqual([])
    })
  }

  it('★ 对照：判据抓得住「新增 key 却没写 CSS」与「CSS 里的死规则」', () => {
    // 判据必须自证有区分力 —— 否则「全部通过」可能只是因为它什么都不检查
    // （模式 #38：判据要看它以为的那个输入）。
    const partial = '[data-accent="sky"] { --accent-hue: 211; }'
    expect(missingCssKeys(AXIS.accent, partial).length).toBe(AXIS.accent.options.length - 1)

    const withGhost = '[data-accent="sky"] {} [data-accent="ghost"] {}'
    expect(orphanCssKeys(AXIS.accent, withGhost)).toEqual(['ghost'])
    // 反向自证：真 CSS 下两条判据都干净（不是「怎么喂都报同一堆」）
    expect(orphanCssKeys(AXIS.accent, withGhost)).not.toEqual(orphanCssKeys(AXIS.accent, themeLayerCss))
  })

  it('★ 对照：注释里的举例不算规则（这个坑真踩过）', () => {
    const css = '/* 这里曾经是 [data-font="x"] .preview-area { … } */\n[data-font="system"] {}'
    expect(cssKeysOf(css, 'data-font')).toEqual(['system'])
    // 不剥注释的话会得到 ['x', 'system'] → 报一条假的「死规则 x」
    expect(cssKeysOf(css, 'data-font')).not.toContain('x')
  })
})

describe('注册表 ↔ store / 预设', () => {
  it('每条轴的 key 就是 store 的字段名（setAxis 的 `[axis.key]: v` 靠这个约定）', () => {
    const s = useStore.getState()
    for (const axis of THEME_AXES) {
      expect(axis.key in s, `store 上没有字段 ${axis.key}`).toBe(true)
    }
  })

  it('★ THEME_PRESETS 的轴字段都在注册表里（否则那条配置被 applyPreset 静默忽略）', () => {
    // applyPreset 的判据是 `axis.key in p`，所以「预设里有、注册表没有」的字段
    // **不会被报错**，只是永远不生效。
    const known = new Set<string>(THEME_AXES.map(a => a.key))
    for (const p of THEME_PRESETS) {
      for (const k of Object.keys(p)) {
        if (k === 'name' || k === 'label') continue
        expect(known.has(k), `预设 ${p.name} 带了注册表不认识的轴字段 ${k}`).toBe(true)
      }
    }
  })

  it('★ 预设里的取值都是该轴的合法 key', () => {
    for (const p of THEME_PRESETS) {
      for (const axis of THEME_AXES) {
        const v = (p as unknown as Record<string, string>)[axis.key]
        if (v === undefined) continue   // 预设不覆盖这条轴（如 theme），正常
        expect(axis.options.map(o => o.key), `${p.name}.${axis.key}`).toContain(v)
      }
    }
  })
})

describe('setter 真的连通（store / DOM / localStorage 三件套）', () => {
  it('setAccent：三处都到位', () => {
    useStore.getState().setAccent('rouge')
    expect(useStore.getState().accent).toBe('rouge')
    expect(document.documentElement.getAttribute('data-accent')).toBe('rouge')
    // ⚠️ 存储键写成字面量是**刻意的**：它是对外契约（改名 = 所有老用户的选择被静默清空），
    //    这条断言就是钉住它别被顺手重构。
    expect(localStorage.getItem('mbw_accent')).toBe('rouge')
  })

  it('setTheme：store 里同时写 theme 与派生字段 resolvedTheme', () => {
    useStore.getState().setTheme('dark')
    expect(useStore.getState().theme).toBe('dark')
    expect(useStore.getState().resolvedTheme).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    // 'system' 是唯一需要派生的一档：DOM 上落的是**解析后**的值
    // （setup.ts 的 matchMedia mock 断言 dark 为真）
    useStore.getState().setTheme('system')
    expect(useStore.getState().theme).toBe('system')
    expect(useStore.getState().resolvedTheme).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    useStore.getState().setTheme('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })
})
