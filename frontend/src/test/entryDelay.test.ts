import { describe, it, expect } from 'vitest'
import { entryDelay, ENTER_STAGGER, ENTER_STEP_MS } from '../components/doc-library/entryDelay'

/**
 * 入场延迟必须与列表长度解耦。
 *
 * ⚠️ 对照（本用例的区分力来源）：把实现换成修复前的 `i * 50`，
 * 「有界」那两条立刻红（1000 → 50000ms）。若换成恒返回 0，
 * 前两条「错位仍在」会红。两个方向都堵住。
 */
describe('entryDelay', () => {
  it('首个卡片无延迟', () => {
    expect(entryDelay(0)).toBe(0)
  })

  it('前 N 个卡片仍有错位（不是恒 0）', () => {
    expect(entryDelay(1)).toBe(ENTER_STEP_MS)
    expect(entryDelay(ENTER_STAGGER - 1)).toBe((ENTER_STAGGER - 1) * ENTER_STEP_MS)
    // 至少有两个不同的延迟值，才能形成错位感
    const distinct = new Set(Array.from({ length: ENTER_STAGGER }, (_, i) => entryDelay(i)))
    expect(distinct.size).toBe(ENTER_STAGGER)
  })

  it('按 ENTER_STAGGER 循环', () => {
    expect(entryDelay(ENTER_STAGGER)).toBe(entryDelay(0))
    expect(entryDelay(ENTER_STAGGER + 3)).toBe(entryDelay(3))
  })

  it('延迟有上界且与列表长度无关（曾 i*50ms → 第 1000 个要等 50 秒）', () => {
    const cap = (ENTER_STAGGER - 1) * ENTER_STEP_MS
    expect(entryDelay(999)).toBeLessThanOrEqual(cap)
    expect(entryDelay(9999)).toBeLessThanOrEqual(cap)
    // 遍历大范围，任何一项都不许超过上界（0.5s 是人能接受的入场动画尺度）
    for (let i = 0; i < 5000; i++) {
      expect(entryDelay(i)).toBeLessThanOrEqual(500)
    }
  })
})
