/**
 * 折叠档位的本机偏好（`lib/fold-prefs.ts`）—— 默认值、坏数据回落、写读一致。
 *
 * ⚠️ 默认值必须是 `full`（改造前现状）。改成 `topic` 的失效形态是静默的：升级后所有
 * 长文打开就只剩一排卡片，界面上看不出「这是默认值错了」。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { FOLD_DEFAULTS, getSavedFold, saveFold } from '../lib/fold-prefs'

const KEY = 'mbw_fold'

describe('fold-prefs', () => {
  beforeEach(() => { localStorage.clear() })

  it('没存过 → 全文', () => {
    expect(FOLD_DEFAULTS.level).toBe('full')
    expect(getSavedFold()).toBe('full')
  })

  it('保存后读回', () => {
    saveFold('topic')
    expect(getSavedFold()).toBe('topic')
  })

  it('坏 JSON / 未知档位 / 缺字段 → 全文', () => {
    localStorage.setItem(KEY, '{不是 JSON')
    expect(getSavedFold()).toBe('full')

    localStorage.setItem(KEY, JSON.stringify({ level: 'grid' }))
    expect(getSavedFold()).toBe('full')

    localStorage.setItem(KEY, JSON.stringify({}))
    expect(getSavedFold()).toBe('full')
  })
})
