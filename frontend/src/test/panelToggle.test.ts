/**
 * 左侧面板开合的**唯一判据** —— 纯函数真值表。
 *
 * 抽出来单测的理由：那三个问题（它开着吗 / 打开要打什么补丁 / 下一个激活的是谁）在
 * `uiSlice` 里被 open/close/toggle 三个入口共用，写在 slice 里就只能靠跑起来看。
 *
 * 对照实验（每条对应一个「看起来对、其实错」的实现）：
 *   A. `isPanelOpen` 只看 `collapsed`、不看 `visible` → **关过一次的生成面板再也打不开**
 *      （`visible=false` 是「彻底从布局移除」，与 `collapsed` 是两种关闭）→ 红
 *   B. `isPanelOpen` 不看 `active` → 互斥模式下两个左侧面板同时被判「开着」→ 红
 *   C. `openPatch` 漏掉 `width < 80` 的复位 → 拖窄到 40px 再打开只有一条缝 → 红
 *   D. `nextActive` 写成「再按一次切到另一个面板」→ 关掉 A 却露出 B → 红
 *   E. `openPatch` 无条件带 `width` → 用户精心拖出来的宽度每次打开都被重置 → 红
 */
import { describe, it, expect } from 'vitest'
import { isPanelOpen, openPatch, nextActive } from '../lib/panelToggle'
import { PANEL_DEFAULT_WIDTH } from '../lib/slotMetrics'
import type { SlotState } from '../store/types'

const slot = (p: Partial<SlotState> = {}): SlotState =>
  ({ width: 240, collapsed: false, visible: true, ...p })

// ── isPanelOpen ─────────────────────────────────────────────

describe('isPanelOpen — 「开着」的三重条件', () => {
  it('并排模式：可见且未折叠就是开着（与 active 无关）', () => {
    expect(isPanelOpen(slot(), false, null, 'docLib')).toBe(true)
    expect(isPanelOpen(slot(), false, 'genPanel', 'docLib')).toBe(true)
  })

  it('互斥模式：必须是当前激活的那个', () => {
    expect(isPanelOpen(slot(), true, 'docLib', 'docLib')).toBe(true)
    // 对照 B：不看 active 的实现 → 两个左侧面板都「开着」→ 互斥形同虚设
    expect(isPanelOpen(slot(), true, 'genPanel', 'docLib')).toBe(false)
    expect(isPanelOpen(slot(), true, null, 'docLib')).toBe(false)
  })

  it('visible=false 一律不算开着，哪怕它就是激活项', () => {
    // 对照 A：只看 collapsed 的实现会把它判成「开着」→ 打开一个不存在的面板
    expect(isPanelOpen(slot({ visible: false }), true, 'docLib', 'docLib')).toBe(false)
    expect(isPanelOpen(slot({ visible: false }), false, null, 'docLib')).toBe(false)
  })

  it('collapsed=true 一律不算开着', () => {
    expect(isPanelOpen(slot({ collapsed: true }), true, 'docLib', 'docLib')).toBe(false)
    expect(isPanelOpen(slot({ collapsed: true }), false, null, 'docLib')).toBe(false)
  })
})

// ── openPatch ───────────────────────────────────────────────

describe('openPatch — 「打开」要打的补丁', () => {
  it('正常宽度：只复位 visible/collapsed，**不碰宽度**', () => {
    // 对照 E：无条件带 width 的实现 → 用户拖出来的宽度被重置 → 红
    const p = openPatch(slot({ width: 380 }))
    expect(p).toEqual({ visible: true, collapsed: false })
    expect('width' in p).toBe(false)
  })

  it('⚠️ 宽度 < 80 时必须复位到默认宽', () => {
    // 对照 C：不复位的实现 → `collapsed: false` 只展开出「一条 40px 的缝」→ 红
    expect(openPatch(slot({ width: 40 }))).toEqual({
      visible: true, collapsed: false, width: PANEL_DEFAULT_WIDTH,
    })
  })

  it('边界：79 复位、80 不复位（阈值与 setSlot 的自动折叠同一条）', () => {
    // `setSlot` 在 `width < 80` 时自动置 collapsed —— 两处必须是同一个阈值，
    // 否则 79 这个宽度会出现「展开后立刻又被判折叠」的死循环观感
    expect(openPatch(slot({ width: 79 })).width).toBe(PANEL_DEFAULT_WIDTH)
    expect(openPatch(slot({ width: 80 })).width).toBeUndefined()
  })
})

// ── nextActive ──────────────────────────────────────────────

describe('nextActive — 互斥模式下按一下图标之后谁激活', () => {
  it('目标未开着 → 它成为激活项', () => {
    expect(nextActive(true, null, 'docLib', false)).toBe('docLib')
    expect(nextActive(true, 'docLib', 'genPanel', false)).toBe('genPanel')
  })

  it('目标正开着 → 谁都不激活（再按一次是关闭）', () => {
    // 对照 D：写成「切到另一个」的实现 → 关掉生成面板却露出文档库 → 红
    expect(nextActive(true, 'docLib', 'docLib', true)).toBe(null)
  })

  it('并排模式原样返回 active（那个字段只对互斥有意义）', () => {
    expect(nextActive(false, 'docLib', 'genPanel', false)).toBe('docLib')
    expect(nextActive(false, null, 'docLib', true)).toBe(null)
  })
})
