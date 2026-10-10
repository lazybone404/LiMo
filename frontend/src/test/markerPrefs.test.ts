/**
 * 语义标记的装饰开关 —— 偏好读写 + 落 DOM + **CSS 源约束**。
 *
 * 本文件最重要的一组是**零回归**：`decor` 默认 `true`，且 `true` 时属性被整个
 * `removeAttribute` —— 于是 `blocks.css` 里带 `[data-marker-decor="off"]` 前缀的规则
 * 永远命不中。与 `cardPrefs.test.ts` 的「属性缺失即 paper」是**同一个机制**
 * （比「取值相同」强：任何一条漏写前缀的规则都不可能漏出来）。
 *
 * ⚠️ **开关语义是「隐藏标记产生的附加物」，不是「隐藏标记」**：关掉后 DOM 里
 * `.md-block` 照样在、文字一字不少，只是 CSS 把边框与底色归零。故本文件**不断言**
 * 「关掉后没有 `.md-block`」—— 那条断言会把实现锁进「JS 分支不渲染包裹层」，
 * 而后端导出侧就必须再实现一遍同样的分支（预览与导出的第二份实现）。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. `MARKER_DEFAULTS.decor` 顺手写成 `false`（「默认清爽一点」）→ 红。
 *      后果是模型写的强调块**永远不显形**，功能白做，且界面上看不出异常。
 *   B. `decor: true` 时写 `setAttribute('data-marker-decor','on')` 而不是
 *      `removeAttribute` → 「零回归」从「选择器不匹配」退化成「取值相同」→ 红。
 *   C. 属性名在 applyMarkers / blocks.css 之间不同源（`data-marker-decor` vs
 *      `data-marker-decor-off`）→ 关掉装饰**什么都不发生**，不报错。
 *   D. 顺手把 `cards.css` 的 `[data-skeleton="card"]` 前缀抄进 blocks.css 的默认规则
 *      → `paper` 下强调块完全没有样式（而 `paper` 恰恰是默认骨架）→ 红。
 *   E. 往 blocks.css 里写裸 hex / rgb()（「就一个颜色嘛」）→ 红。
 *      这会打破「一套标记 × 任意主题」，且暗色下必然失效。
 *   F. CSS 里写了一个 `KNOWN_BLOCK_TYPES` 之外的 `data-block-type="…"` → 那条规则
 *      永远不会命中（解析器根本产不出这个类型）→ 红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { MARKER_DEFAULTS, getSavedMarkers, applyMarkers, saveMarkers } from '../lib/marker-prefs'
import { KNOWN_BLOCK_TYPES } from '../lib/markdown-scan'

const KEY = 'mbw_markers'
const ATTR = 'data-marker-decor'
const root = () => document.documentElement
/** ⚠️ 与 `cardPrefs.test.ts` 读 cards.css 同款（jsdom 下用 process.cwd()） */
const blocksCss = readFileSync(resolve(process.cwd(), 'src/styles/blocks.css'), 'utf-8')
/** 剥注释后再做源约束断言 —— 注释里举例提到色值不等于规则用了色值 */
const cssNoComments = blocksCss.replace(/\/\*[\s\S]*?\*\//g, '')

beforeEach(() => {
  localStorage.clear()
  root().removeAttribute(ATTR)
})

// ── 零回归：默认值必须等于改造前的现状 ──────────────────────

describe('零回归 — 默认 = 改造前的现状', () => {
  it('MARKER_DEFAULTS 默认**显示**装饰', () => {
    // 对照 A：写成 false → 模型写的强调块永远不显形（功能白做、零报错）
    expect(MARKER_DEFAULTS.decor).toBe(true)
  })

  it('无存储时 getSavedMarkers() 返回默认', () => {
    expect(getSavedMarkers()).toEqual(MARKER_DEFAULTS)
  })

  it('默认态下属性**不存在**（而不是等于 "on"）', () => {
    // 对照 B：写 setAttribute(...,'on') → 红。这一步退化掉就等于少了一层保证。
    applyMarkers({ decor: true })
    expect(root().hasAttribute(ATTR)).toBe(false)
  })

  it('关掉再打开时属性被清掉（不留残余）', () => {
    applyMarkers({ decor: false })
    applyMarkers({ decor: true })
    expect(root().hasAttribute(ATTR)).toBe(false)
  })
})

// ── 写 DOM ────────────────────────────────────────────────

describe('applyMarkers — 写 DOM', () => {
  it('关掉装饰时属性值为 "off"（与 CSS 选择器同源）', () => {
    // 对照 C：属性名/取值与 blocks.css 不同源 → 关掉装饰什么都不发生
    applyMarkers({ decor: false })
    expect(root().getAttribute(ATTR)).toBe('off')
  })

  it('幂等：重复写同一个值结果相同', () => {
    applyMarkers({ decor: false })
    applyMarkers({ decor: false })
    expect(root().getAttribute(ATTR)).toBe('off')
  })
})

// ── 读写往返 + 逐字段校验 ──────────────────────────────────

describe('读写往返与容错', () => {
  it('save → get 往返一致', () => {
    saveMarkers({ decor: false })
    expect(getSavedMarkers()).toEqual({ decor: false })
  })

  it('损坏的 JSON → 落回默认（不抛）', () => {
    localStorage.setItem(KEY, '{不是 json')
    expect(getSavedMarkers()).toEqual(MARKER_DEFAULTS)
  })

  it('非布尔值 → 落回默认（而不是被当成 false）', () => {
    localStorage.setItem(KEY, JSON.stringify({ decor: 'no' }))
    expect(getSavedMarkers().decor).toBe(true)
  })
})

// ── CSS 源约束 ────────────────────────────────────────────

describe('blocks.css 源约束', () => {
  it('不出现任何裸色值（零硬编码铁律）', () => {
    // 对照 E：全部走 --tk-*，否则「一套标记 × 任意主题」不成立、暗色必然失效
    const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/
    const offenders = cssNoComments.split('\n').filter(l => COLOR_RE.test(l))
    expect(offenders).toEqual([])
  })

  it('.md-block 的规则**不带** `[data-skeleton="card"]` 前缀', () => {
    // 对照 D：抄了 cards.css 的前缀 → paper（默认骨架）下毫无样式，零报错。
    // ⚠️ 这是与 cards.css **相反**的约束，写在这里正是为了别让下一个人顺手抄过去。
    expect(cssNoComments).not.toContain('[data-skeleton')
    const rules = cssNoComments.split('\n').map(l => l.trim()).filter(l => /\.md-block/.test(l))
    expect(rules.length).toBeGreaterThan(0)
  })

  it('关掉装饰的规则**都**挂在 `[data-marker-decor="off"]` 下', () => {
    // 判据：凡出现「归零装饰」的声明（border-left: none / background: none）的那条
    // 选择器，必须带属性前缀 —— 漏了的话默认态也被归零（强调块永远没有装饰）。
    const lines = cssNoComments.split('\n').map(l => l.trim())
    const idx = lines.findIndex(l => l.includes('border-left: none'))
    expect(idx, '未找到关掉装饰的规则（改名或删了？）').toBeGreaterThan(0)
    // 选择器行是该声明之前最近的、以 `{` 结尾或含选择器的那一行
    const selector = lines.slice(0, idx).reverse().find(l => l.includes('.md-block'))
    expect(selector).toContain('[data-marker-decor="off"]')
    expect(lines.slice(idx, idx + 5).some(l => l.includes('background: none'))).toBe(true)
  })

  it('CSS 里出现的每个 `data-block-type` 值都在白名单里', () => {
    // 对照 F：写了一个解析器产不出的类型 → 那条规则永远不命中，且不报错
    const used = [...cssNoComments.matchAll(/data-block-type="([^"]+)"/g)].map(m => m[1])
    expect(used.length).toBeGreaterThan(0)
    for (const t of used) expect(KNOWN_BLOCK_TYPES.has(t), `CSS 里的类型不在白名单：${t}`).toBe(true)
  })

  it('强调块刻意**不是**卡片形态（无投影）—— 避免卡中卡', () => {
    // `card` 骨架下强调块长在卡片内部，若它也带投影就会看起来像「一张卡里套着一张卡」
    expect(cssNoComments).not.toContain('box-shadow')
  })
})
