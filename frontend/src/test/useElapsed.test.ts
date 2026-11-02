/**
 * 已用时的格式化与 tick。
 *
 * ⚠️ 这两件事必须分开测：格式化是纯函数（表驱动、零环境依赖），tick 是行为
 * （要假时钟）。混在一个用例里，tick 失败时看不出是「格式错了」还是「没在走」。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { formatElapsed, useElapsed } from '../hooks/useElapsed'

afterEach(() => {
  vi.useRealTimers()
})

describe('formatElapsed', () => {
  it.each([
    [0, '0 秒'],
    [999, '0 秒'],
    [1000, '1 秒'],
    [59_000, '59 秒'],
    [60_000, '1 分 00 秒'],
    [61_000, '1 分 01 秒'],
    [3_605_000, '1 小时 00 分'],
    [3_665_000, '1 小时 01 分'],
  ])('%i ms → %s', (ms, want) => {
    expect(formatElapsed(ms)).toBe(want)
  })

  it('负数按 0 处理（时钟回拨 / 系统时间被改）', () => {
    expect(formatElapsed(-5000)).toBe('0 秒')
  })
})

describe('useElapsed', () => {
  it('startedAt 为空返回 null（调用方据此不渲染）', () => {
    const { result } = renderHook(() => useElapsed(undefined))
    expect(result.current).toBeNull()
  })

  it('随时间推进更新，卸载后停止 tick', async () => {
    // ⚠️ 用 `useFakeTimers({ now })` 而不是 `vi.setSystemTime`：后者只改 `Date`，
    // 之后的 `advanceTimersByTime` 不会把它一起推进 —— 计时器在走而 `Date.now()`
    // 停在原地，断言会得到「0 秒」（本用例第一版正是栽在这里）。
    vi.useFakeTimers({ now: new Date('2026-11-03T10:00:00') })

    // ⚠️ 传一个**固定**的 startedAt，而不是 `Date.now()`。真实调用方给的也是 store 里的
    // 稳定值；写成 `Date.now()` 会让每次重渲染都算出新依赖，effect 反复重建 interval。
    const t0 = Date.now()
    const { result, unmount } = renderHook(() => useElapsed(t0))
    expect(result.current).toBe('0 秒')

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(result.current).toBe('3 秒')

    await act(async () => { await vi.advanceTimersByTimeAsync(59_000) })
    expect(result.current).toBe('1 分 02 秒')

    unmount()
    // 卸载后再推进时钟不应再触发……
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    // ……而组件已卸载，这里只是确认没有「卸载后 setState」的告警路径
    expect(vi.getTimerCount()).toBe(0)
  })
})
