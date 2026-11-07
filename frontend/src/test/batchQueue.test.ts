/**
 * 批量队列的落盘快照（`lib/batch-queue.ts`，2026-11-07）。
 *
 * 这个模块只在一种时刻被用到：刷新页面之后。因此每条测试都在回答
 * 「用户重新打开文档库时会看到什么」，而不是「函数返回了什么」。
 *
 * 对照实验（每条都真跑过）：
 *   A. `UNFINISHED` 加上 `'failed'` → 「失败的篇不算未完成」那条红。后果是刷新后
 *      横幅把上一轮已经报过错的篇又列一遍，用户点「继续运行」就把失败的那几篇
 *      重跑一次（计费）。
 *   B. 去掉 `isQueueRunningHere` 那道判断 → 「本页正在跑时不给恢复项」红。后果是
 *      面板折叠再展开，同一批请求跑第二遍。
 *   C. `clearQueue` 改成整键覆盖 `{}` → 「清一种不动另一种」那两条都红。后果是忽略
 *      转录横幅，生成横幅也跟着消失。
 */
import { describe, it, expect, beforeEach } from 'vitest'

import {
  clearQueue,
  markQueueIdle,
  markQueueRunning,
  restorableGenerate,
  restorableTranscribe,
  saveQueue,
  unfinished,
} from '../lib/batch-queue'

const KEY = 'mbw_batchQueues'

beforeEach(() => {
  localStorage.clear()
  // 模块级标记跨用例存活：不清掉的话，某条测试里 markQueueRunning 一次，
  // 后面所有用例的恢复项都会被挡住。
  markQueueIdle('transcribe')
  markQueueIdle('generate')
})

describe('落盘与读回', () => {
  it('存进去的项能原样读回来', () => {
    saveQueue('transcribe', [
      { id: 'a', title: '甲', status: 'queued', message: '' },
      { id: 'b', title: '乙', status: 'done', message: '' },
    ])
    const got = restorableTranscribe()
    expect(got.map(i => i.id)).toEqual(['a'])
  })

  it('running 读回来降级成 queued', () => {
    // 那条请求已经随着旧页面结束了，实际停在排队的位置。
    saveQueue('transcribe', [{ id: 'a', title: '甲', status: 'running', message: '' }])
    expect(restorableTranscribe()[0].status).toBe('queued')
  })

  it('done / failed / skipped 都不算未完成', () => {
    // 对照 A：UNFINISHED 加上 'failed' → 这条红
    const items = [
      { id: 'a', title: '甲', status: 'failed' },
      { id: 'b', title: '乙', status: 'skipped' },
      { id: 'c', title: '丙', status: 'done' },
      { id: 'd', title: '丁', status: 'queued' },
    ]
    expect(unfinished(items).map(i => i.id)).toEqual(['d'])
  })

  it('坏 JSON / 版本不符都读成空，不抛错', () => {
    localStorage.setItem(KEY, '{ 这不是 JSON')
    expect(restorableTranscribe()).toEqual([])

    localStorage.setItem(KEY, JSON.stringify({
      transcribe: { v: 999, savedAt: '', items: [{ id: 'a', title: '甲', status: 'queued' }] },
    }))
    expect(restorableTranscribe()).toEqual([])
  })
})

describe('清一种不动另一种', () => {
  it('clearQueue 只删指定的那一种', () => {
    // 对照 C：clearQueue 改成整键覆盖 → 这条红
    saveQueue('transcribe', [{ id: 'a', title: '甲', status: 'queued' }])
    saveQueue('generate', [{ id: 'b', title: '乙', status: 'queued', sourceType: 'video', willRegenerate: false }])

    clearQueue('transcribe')

    expect(restorableTranscribe()).toEqual([])
    expect(restorableGenerate().map(i => i.id)).toEqual(['b'])
  })

  it('清一种之后，另一种的其余字段还在', () => {
    saveQueue('generate', [
      { id: 'b', title: '乙', status: 'queued', sourceType: 'video', willRegenerate: true },
    ])
    clearQueue('transcribe')
    const [it] = restorableGenerate()
    expect(it.sourceType).toBe('video')
    expect(it.willRegenerate).toBe(true)
  })

  it('本页正在跑时不给恢复项，标记清掉之后又给', () => {
    // 对照 B：去掉 isQueueRunningHere 判断 → 这条红
    saveQueue('transcribe', [{ id: 'a', title: '甲', status: 'queued' }])
    markQueueRunning('transcribe')
    expect(restorableTranscribe()).toEqual([])

    markQueueIdle('transcribe')
    expect(restorableTranscribe().map(i => i.id)).toEqual(['a'])
  })
})

describe('脏条目被丢弃', () => {
  it('缺 id 的项读不回来', () => {
    // id 是重建入参与去重的唯一依据，空的进去只会发出一批无效请求。
    localStorage.setItem(KEY, JSON.stringify({
      transcribe: { v: 1, savedAt: '', items: [
        { title: '没有 id', status: 'queued' },
        { id: '', title: '空 id', status: 'queued' },
        { id: 'ok', title: '正常', status: 'queued' },
      ] },
    }))
    expect(restorableTranscribe().map(i => i.id)).toEqual(['ok'])
  })

  it('生成项缺 sourceType 就丢掉那一项，其余照常', () => {
    // sourceType 决定产物名与格式清单。用空串去 getTypeMeta 会拿到默认值，
    // 请求照发，产物却可能不是用户选的那一份。
    localStorage.setItem(KEY, JSON.stringify({
      generate: { v: 1, savedAt: '', items: [
        { id: 'a', title: '甲', status: 'queued', willRegenerate: false },
        { id: 'b', title: '乙', status: 'queued', sourceType: 'video', willRegenerate: true },
      ] },
    }))
    expect(restorableGenerate().map(i => i.id)).toEqual(['b'])
  })
})
