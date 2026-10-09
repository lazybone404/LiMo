/**
 * 侧栏布局偏好 —— 读写 + 逐字段校验。
 *
 * ⚠️ 与 `reading-prefs` 相反：那边「默认 = 改造前的现状」（零回归），这边**默认互斥
 * 是一次有意的默认变更**（2026-10-10 用户拍板）。所以这里的默认值断言不是防回归，
 * 而是防「顺手改回并排」——那会让已经习惯活动栏的用户每次升级都看到旧布局。
 *
 * 对照实验：
 *   A. `{...DEFAULTS, ...JSON.parse(raw)}` 浅合并 → `"leftPanelsExclusive": "no"`
 *      被原样带进来（字符串 truthy），界面按「并排」渲染而设置页显示「互斥」→ 红
 *   B. 整个 JSON 解析失败时抛异常 → 应用启动即崩（localStorage 是用户可改的）→ 红
 *   C. `LAYOUT_OPTIONS[0]` 不是默认值 → 设置页把当前项显示成未选中 → 红
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { LAYOUT_DEFAULTS, LAYOUT_OPTIONS, getSavedLayout, saveLayout } from '../lib/layout-prefs'

const KEY = 'mbw_layout'

beforeEach(() => localStorage.clear())

describe('默认值', () => {
  it('默认互斥切换（= 用户拍板的默认变更，不是笔误）', () => {
    expect(LAYOUT_DEFAULTS.leftPanelsExclusive).toBe(true)
  })

  it('无存储时 getSavedLayout() 返回默认', () => {
    expect(getSavedLayout()).toEqual(LAYOUT_DEFAULTS)
  })

  it('选项表第一项就是默认值（设置页的高亮靠它）', () => {
    // 对照 C：把「允许并排」排第一 → 设置页默认项显示成未选中 → 红
    expect(LAYOUT_OPTIONS[0].key).toBe(LAYOUT_DEFAULTS.leftPanelsExclusive)
    expect(LAYOUT_OPTIONS.map(o => o.key)).toEqual([true, false])
  })
})

describe('getSavedLayout — 逐字段校验', () => {
  it('损坏 JSON → 全部用默认（不抛异常）', () => {
    // 对照 B：抛异常的实现 → 用户手改坏 localStorage 后应用起不来 → 红
    localStorage.setItem(KEY, '{not json')
    expect(getSavedLayout()).toEqual(LAYOUT_DEFAULTS)
  })

  it('⚠️ 字符串 "no" 不被当成合法值 —— 浅合并的实现在这里红', () => {
    // 对照 A：`"no"` 是 truthy，浅合并后 `leftPanelsExclusive === true` 为 false
    // → 界面按「并排」渲染 → 红。判据必须是 `typeof === 'boolean'`。
    localStorage.setItem(KEY, JSON.stringify({ leftPanelsExclusive: 'no' }))
    expect(getSavedLayout().leftPanelsExclusive).toBe(true)
  })

  it('字符串 "yes" / 数字 / null 一律退回默认', () => {
    for (const bad of ['yes', 1, 0, null, {}, []]) {
      localStorage.setItem(KEY, JSON.stringify({ leftPanelsExclusive: bad }))
      expect(getSavedLayout().leftPanelsExclusive).toBe(LAYOUT_DEFAULTS.leftPanelsExclusive)
    }
  })

  it('字段缺失 → 退回默认', () => {
    localStorage.setItem(KEY, JSON.stringify({}))
    expect(getSavedLayout()).toEqual(LAYOUT_DEFAULTS)
  })

  it('合法的 false 被原样保留（不能把「假值」和「坏值」混为一谈）', () => {
    localStorage.setItem(KEY, JSON.stringify({ leftPanelsExclusive: false }))
    expect(getSavedLayout().leftPanelsExclusive).toBe(false)
  })
})

describe('saveLayout — 往返', () => {
  it('save → get 往返一致（两个方向都要）', () => {
    for (const v of [true, false]) {
      saveLayout({ leftPanelsExclusive: v })
      expect(getSavedLayout().leftPanelsExclusive).toBe(v)
    }
  })

  it('写入的是 JSON（不是 "[object Object]"）', () => {
    saveLayout({ leftPanelsExclusive: false })
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ leftPanelsExclusive: false })
  })
})
