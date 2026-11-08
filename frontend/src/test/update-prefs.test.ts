/**
 * 更新偏好的三个字段，每个都拦一件具体的事：节流、重复提醒、跳过版本。
 *
 * 这三条全都**不报错**地失效 —— 坏掉的表现分别是「每次启动都查一次」「同一个版本
 * 反复提醒」「跳过之后重启又回来了」，而界面上一切正常。所以每条都要有守卫。
 *
 * 对照实验（把实现改坏，确认这些用例真的会红）：
 *   A. `shouldAutoCheck` 改成 `return true` → 「一小时内查过 → 不查」红。
 *   B. `skippedVersion` 的 typeof 校验换成 `v.skippedVersion ?? ''` →
 *      「非字符串的版本号不被收下」红（数字 `123` 会被原样收下）。
 *   C. `skipVersion` 改成覆盖整包（而不是合并写）→ 「跳过不影响已提醒记录」红。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  AUTO_CHECK_INTERVAL_MS, UPDATE_DEFAULTS,
  clearSkipped, getUpdatePrefs, markChecked, markNotified,
  saveUpdatePrefs, shouldAutoCheck, skipVersion,
} from '../lib/update-prefs'

const KEY = 'mbw_update'

beforeEach(() => localStorage.clear())

describe('默认值', () => {
  it('无存储时三个字段都是空/零', () => {
    expect(getUpdatePrefs()).toEqual(UPDATE_DEFAULTS)
  })

  it('节流窗口是 24 小时', () => {
    // 这个常量是「一天最多烦你一次」的唯一出处，改小等于改行为，值得钉住。
    expect(AUTO_CHECK_INTERVAL_MS).toBe(24 * 60 * 60 * 1000)
  })
})

describe('读写与容错', () => {
  it('合并写：只给一个字段，其余保持原值', () => {
    saveUpdatePrefs({ skippedVersion: '0.5.23' })
    saveUpdatePrefs({ lastNotifiedVersion: '0.5.24' })
    expect(getUpdatePrefs().skippedVersion).toBe('0.5.23')
    expect(getUpdatePrefs().lastNotifiedVersion).toBe('0.5.24')
  })

  it('损坏的 JSON → 整体落回默认（不抛）', () => {
    localStorage.setItem(KEY, '{不是 json')
    expect(getUpdatePrefs()).toEqual(UPDATE_DEFAULTS)
  })

  it('单个字段类型不对 → 只重置那一项，其余保留', () => {
    // 手改坏一个数字不该把「跳过这个版本」一起清掉 —— 那会让被跳过的版本重新弹提醒。
    localStorage.setItem(KEY, JSON.stringify({ skippedVersion: '0.5.23', lastCheckAt: 'yesterday' }))
    const p = getUpdatePrefs()
    expect(p.lastCheckAt).toBe(UPDATE_DEFAULTS.lastCheckAt)
    expect(p.skippedVersion).toBe('0.5.23')
  })

  it('非字符串的版本号不被收下（对照 C）', () => {
    localStorage.setItem(KEY, JSON.stringify({ skippedVersion: 123 }))
    expect(getUpdatePrefs().skippedVersion).toBe('')
  })

  it('NaN / Infinity 也不被当成有效时间', () => {
    localStorage.setItem(KEY, JSON.stringify({ lastCheckAt: Number.NaN }))
    expect(getUpdatePrefs().lastCheckAt).toBe(UPDATE_DEFAULTS.lastCheckAt)
  })
})

describe('节流', () => {
  const NOW = 1_800_000_000_000

  it('从没查过 → 该查', () => {
    expect(shouldAutoCheck(NOW)).toBe(true)
  })

  it('一小时内查过 → 不查', () => {
    markChecked(NOW - 60 * 60 * 1000)
    expect(shouldAutoCheck(NOW)).toBe(false)
  })

  it('刚好满 24 小时 → 该查（边界取「大于等于」）', () => {
    markChecked(NOW - AUTO_CHECK_INTERVAL_MS)
    expect(shouldAutoCheck(NOW)).toBe(true)
  })

  it('超过 24 小时 → 该查', () => {
    markChecked(NOW - AUTO_CHECK_INTERVAL_MS - 1)
    expect(shouldAutoCheck(NOW)).toBe(true)
  })
})

describe('提醒与跳过', () => {
  it('markNotified 记下版本号', () => {
    markNotified('0.5.23')
    expect(getUpdatePrefs().lastNotifiedVersion).toBe('0.5.23')
  })

  it('skipVersion 记下版本号，clearSkipped 清掉它', () => {
    skipVersion('0.5.23')
    expect(getUpdatePrefs().skippedVersion).toBe('0.5.23')
    clearSkipped()
    expect(getUpdatePrefs().skippedVersion).toBe('')
  })

  it('跳过不影响已提醒记录（两个闸门各管各的）', () => {
    markNotified('0.5.23')
    skipVersion('0.5.24')
    const p = getUpdatePrefs()
    expect(p.lastNotifiedVersion).toBe('0.5.23')
    expect(p.skippedVersion).toBe('0.5.24')
  })
})
