/**
 * 产物骨架轴 —— 偏好读写 + 落 DOM + **CSS 源约束**。
 *
 * 本文件最重要的一组是**零回归**：`skeleton` 默认 `paper`，即「与改造前逐像素一致」。
 * 但它比阅读版式那套更强 —— 那边靠「取值相同」，这边靠**选择器根本不匹配**
 * （`paper` 时 `data-skeleton` 属性被整个移除，`cards.css` 里所有卡片规则永远命不中）。
 * 这是有意的：属性写 `="paper"` 时，任何一条漏写作用域前缀的卡片规则都会漏出来。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. `CARD_DEFAULTS.skeleton` 顺手写成 `'card'`（「新功能当然要开」）→ 红。
 *      后果是**每一个既有用户**打开就换了排版，零报错、零提示。
 *   B. `paper` 时写 `setAttribute('data-skeleton', 'paper')` 而不是 `removeAttribute`
 *      → 「零回归」从「选择器不匹配」退化成「取值相同」→ 红。
 *   C. 属性名写成 `data-card-skeleton`（或 CSS 里写另一个名字）→ 样式**永远不生效**，
 *      不报错。故这里把三处（applyCard / cards.css / 导出）的名字钉住。
 *   D. 某条 `.md-card` 规则漏写 `[data-skeleton="card"]` 前缀 → 红。
 *   E. 往 `--card-*` 里写裸 hex / rgb()（「就一个颜色嘛」）→ 红。
 *      这会打破「一套骨架 × 任意主题」，且暗色下必然失效。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  CARD_DEFAULTS,
  CARD_STYLE_OPTIONS,
  getSavedCard,
  applyCard,
  saveCard,
} from '../lib/card-prefs'

const KEY = 'mbw_card'
const root = () => document.documentElement
/** ⚠️ 与 `readingLayout.test.tsx` 读 `index.css` 同款（jsdom 下用 process.cwd()） */
const cardsCss = readFileSync(resolve(process.cwd(), 'src/styles/cards.css'), 'utf-8')
/** 剥注释后再做源约束断言 —— 注释里举例提到色值不等于规则用了色值 */
const cssNoComments = cardsCss.replace(/\/\*[\s\S]*?\*\//g, '')

beforeEach(() => {
  localStorage.clear()
  root().removeAttribute('data-skeleton')
  root().removeAttribute('data-card-style')
})

// ── 零回归：默认值必须等于改造前的现状 ──────────────────────

describe('零回归 — 默认 = 改造前的现状', () => {
  it('CARD_DEFAULTS 逐值等于「关掉卡片」', () => {
    // 对照 A：任何一项被「顺手改成新功能的值」→ 红
    expect(CARD_DEFAULTS.skeleton).toBe('paper')
    expect(CARD_DEFAULTS.style).toBe('gpt')   // 卡片没打开，这一项此时不生效
  })

  it('无存储时 getSavedCard() 返回默认', () => {
    expect(getSavedCard()).toEqual(CARD_DEFAULTS)
  })

  it('paper 时两个属性都**不存在**（而不是等于 "paper"）', () => {
    // 对照 B：写 setAttribute(...,'paper') → 红。这是零回归从「取值相同」
    // 升级为「选择器不匹配」的那一步，退化掉就等于少了一层保证。
    applyCard({ skeleton: 'paper', style: 'gpt' })
    expect(root().hasAttribute('data-skeleton')).toBe(false)
    expect(root().hasAttribute('data-card-style')).toBe(false)
  })

  it('从 card 切回 paper 时属性被清掉（不留残余）', () => {
    applyCard({ skeleton: 'card', style: 'guofeng' })
    applyCard({ skeleton: 'paper', style: 'guofeng' })
    expect(root().hasAttribute('data-skeleton')).toBe(false)
    expect(root().hasAttribute('data-card-style')).toBe(false)
  })
})

// ── 写 DOM ────────────────────────────────────────────────

describe('applyCard — 写 DOM', () => {
  it('card 时两个属性同时写（缺任何一个都会静默失效）', () => {
    // 对照 C：属性名与 cards.css 的选择器不同源 → 样式永不生效且不报错
    applyCard({ skeleton: 'card', style: 'seal' })
    expect(root().getAttribute('data-skeleton')).toBe('card')
    expect(root().getAttribute('data-card-style')).toBe('seal')
  })

  it('骨架切换不丢视觉方案（切回来还是原来那套）', () => {
    applyCard({ skeleton: 'card', style: 'ink' })
    applyCard({ skeleton: 'paper', style: 'ink' })
    applyCard({ skeleton: 'card', style: 'ink' })
    expect(root().getAttribute('data-card-style')).toBe('ink')
  })
})

// ── 读写往返 + 逐字段校验 ──────────────────────────────────

describe('读写往返与容错', () => {
  it('save → get 往返一致', () => {
    saveCard({ skeleton: 'card', style: 'apple' })
    expect(getSavedCard()).toEqual({ skeleton: 'card', style: 'apple' })
  })

  it('损坏的 JSON → 整体落回默认（不抛）', () => {
    localStorage.setItem(KEY, '{不是 json')
    expect(getSavedCard()).toEqual(CARD_DEFAULTS)
  })

  it('单项非法只退该项，不带走另一项', () => {
    // 对照：整包丢弃 → 用户设好的 style 被一个坏的 skeleton 一并重置
    localStorage.setItem(KEY, JSON.stringify({ skeleton: 'card', style: '不存在' }))
    expect(getSavedCard()).toEqual({ skeleton: 'card', style: 'gpt' })
  })

  it('不认识的骨架值 → 落回 paper（而不是当成 card）', () => {
    localStorage.setItem(KEY, JSON.stringify({ skeleton: 'CARD', style: 'ink' }))
    expect(getSavedCard().skeleton).toBe('paper')
  })
})

// ── CSS 源约束（零硬编码色值 + 前缀齐全） ────────────────────

describe('cards.css 源约束', () => {
  it('除 `--sh-*` 等级层外不出现任何裸色值', () => {
    // 对照 E：`--card-*` 只允许引用 --tk-* / --sh-N / 纯几何量。
    // 唯一例外是投影等级层（影子本质是黑，且必须随明暗换值）。
    const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/
    const offenders = cssNoComments
      .split('\n')
      .filter(l => COLOR_RE.test(l) && !/^\s*--sh-[0-3]\s*:/.test(l))
    expect(offenders).toEqual([])
  })

  it('四个投影等级在明暗两侧都有定义', () => {
    for (const n of [0, 1, 2, 3]) {
      expect(cssNoComments).toContain(`--sh-${n}:`)
    }
    // 暗色必须给 --sh-1~3 换成更深的值（同一个 6% 黑在深色底上看不见）
    const darkBlock = cssNoComments.slice(cssNoComments.indexOf('[data-theme="dark"]'))
    for (const n of [1, 2, 3]) {
      expect(darkBlock).toContain(`--sh-${n}:`)
    }
  })

  it('8 套视觉方案全在 CSS 里有定义（标签表与 CSS 不同源会静默少一套）', () => {
    for (const o of CARD_STYLE_OPTIONS) {
      expect(cssNoComments, `缺少 [data-card-style="${o.key}"]`).toContain(`[data-card-style="${o.key}"]`)
    }
    expect(CARD_STYLE_OPTIONS.length).toBe(8)
  })

  it('全部 `.md-card*` 规则都带 `[data-skeleton="card"]` 前缀', () => {
    // 对照 D：漏一条前缀 → 该规则在 paper 下也生效 → 既破坏零回归，也不报错
    const lines = cssNoComments
      .split('\n')
      .map(l => l.trim())
      .filter(l => /\.md-card/.test(l))
    expect(lines.length).toBeGreaterThan(0)
    for (const l of lines) {
      expect(l.startsWith('[data-skeleton="card"]'), `规则行缺前缀: ${l}`).toBe(true)
    }
  })

  it('规则里不引用 `--tk-surface-hover`（曾是「9 处引用、0 处定义」的失效变量）', () => {
    // ⚠️ 断的是**剥掉注释后**的正文：文件头那段注释**故意**写了这个名字
    //（提醒后来者别照抄），那是说明不是引用。
    expect(cssNoComments).not.toContain('--tk-surface-hover')
  })
})

// ── 设置页的迷你视觉小样（2026-10-11）────────────────────────

describe('设置页迷你视觉小样', () => {
  const settings = readFileSync(
    resolve(process.cwd(), 'src/components/settings/SettingsPage.tsx'), 'utf-8')

  it('小样消费与 `.md-card` 相同的变量（没有第二份真相源）', () => {
    // 对照：小样自己写死一组色值 → 改方案时卡片变了、小样没变（或反之），零报错。
    // 断`.card-preview` 块里真的引用了这几个变量，而不是「CSS 里有 .card-preview 这个词」。
    const block = /\.card-preview\s*\{([\s\S]*?)\}/.exec(cssNoComments)
    expect(block, 'cards.css 里没有 .card-preview 规则').not.toBeNull()
    for (const v of ['--card-bg', '--card-border', '--card-radius', '--card-shadow']) {
      // ⚠️ 带右括号匹配，而不是 `var(--card-bg` —— 后者会被 `var(--card-bg-base)`
      // 满足，判据就失去了区分力（同款失误在本文件里已有先例）。
      expect(block![1], `.card-preview 没有消费 ${v}`).toContain(`var(${v})`)
    }
    // ⚠️ 它**刻意不带** `[data-skeleton="card"]` 前缀：那个属性挂在 `<html>` 上，
    // 设置页里这个前提不成立 —— 带了就永远不匹配，小样退化成看不见的空方块。
    expect(cssNoComments).toContain('.card-preview {')
  })

  it('设置页真的把小样用起来了，且每个方案各带自己的皮肤属性', () => {
    // ⚠️ 这条防的正是「CSS 已就位、JSX 还没用」这个中间状态 —— 它零报错、
    // 界面照常渲染（只是每个方案按钮旁边没有小样），肉眼看才发现。
    // ⚠️ 判据带**词边界 + class 属性**：裸子串 `card-preview` 会被 `card-previewX`
    // 满足（本项目点名过的「判据没有区分力」，已在对照实验里实测到）。
    expect(settings).toMatch(/className="[^"]*\bcard-preview\b/)
    expect(settings).toContain('data-card-style={o.key}')
  })
})

// ── 跨语言守卫（照 `appShortcutKeys.test.ts` 读 Rust 源码的先例）────────

describe('与后端导出侧同源', () => {
  it('后端 `_CARD_STYLES` 的键集合 == 前端标签表的 key 集合', () => {
    // 两处都是「有哪些皮肤」的清单，必然分开演化：前端加了第 9 套、后端白名单没跟上
    // → 导出时那套被**静默回退成 gpt**（`card_style not in _CARD_STYLES`），
    // 预览是对的、导出是错的，且零报错。反之后端多一套则前端永远给不出来。
    // ⚠️ 比的是**集合**而不是长度：长度相同但拼写不同（`gpt` vs `GPT`）同样会静默回退。
    const py = readFileSync(resolve(process.cwd(), '../backend/app/routers/export.py'), 'utf-8')
    const m = /_CARD_STYLES\s*=\s*frozenset\(\{([\s\S]*?)\}\)/.exec(py)
    expect(m, '未在 export.py 找到 _CARD_STYLES 定义（改名或挪走了？）').not.toBeNull()
    const backendKeys = [...m![1].matchAll(/"([^"]+)"/g)].map(x => x[1]).sort()
    expect(backendKeys).toEqual(CARD_STYLE_OPTIONS.map(o => o.key).sort())
  })
})
