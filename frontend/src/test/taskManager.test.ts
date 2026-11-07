/**
 * `api/taskManager.ts` 的导入表释放（2026-11-07）。
 *
 * 导入任务的 key 是 `__import__{自增序号}__import`，序号只增不减 —— 结算后不释放，
 * 每导入一次就在模块级 Map 里留一条**永远不再被查询**的条目。它不会被界面看见，
 * 也没有任何报错，只有会话越用越久时那点内存能说明问题。
 *
 * 判据靠 `cancelTask` 的返回值：它找不到 controller 时返回 `false`。
 */
import { describe, it, expect } from 'vitest'

import { registerAbort, unregisterAbort, cancelTask } from '../api/taskManager'

describe('导入任务的 AbortController 释放', () => {
  it('释放之后 cancelTask 找不到它（返回 false，且 signal 没被 abort）', () => {
    const taskKey = '__import__900__import'
    const ctrl = registerAbort(taskKey, new AbortController())
    expect(cancelTask(taskKey)).toBe(true)

    // 直接用序号形态再测一次释放
    const key2 = '__import__901__import'
    const ctrl2 = registerAbort(key2, new AbortController())
    unregisterAbort(key2)
    expect(ctrl2.signal.aborted).toBe(false)
    expect(cancelTask(key2)).toBe(false)
    expect(ctrl.signal.aborted).toBe(true)
  })

  it('释放只影响自己那一条，并发的其他导入仍可取消', () => {
    const a = '__import__902__import'
    const b = '__import__903__import'
    const ctrlB = registerAbort(b, new AbortController())
    registerAbort(a, new AbortController())

    unregisterAbort(a)

    expect(cancelTask(b)).toBe(true)
    expect(ctrlB.signal.aborted).toBe(true)
  })

  it('释放一个没注册过的 key 不抛错（幂等）', () => {
    expect(() => unregisterAbort('__import__999__import')).not.toThrow()
  })
})
