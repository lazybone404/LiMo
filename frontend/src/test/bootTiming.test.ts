/**
 * 首屏可交互打点（`lib/bootTiming.ts`）。
 *
 * 本文件管三件事：
 *   1. **判据**（`isMainUiClickable`）：中心点命中测试，而不是「元素存在」或
 *      `DOMContentLoaded`。失效形态是**静默**的 —— 判据放宽成「按钮在 DOM 里」，
 *      加载屏盖着时也会上报一条漂亮的短耗时，此后再没人知道启动到底慢在哪。
 *   2. **只上报一次**：effect 重跑、组件重挂载不得让统计多出一条。
 *   3. **拿不到就不报**：等满帧数仍不可点就放弃（记一条「没画完」比不记更坏）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  BOOT_REPORT_NAME, MAX_FRAMES, _resetBootTimingForTest,
  isMainUiClickable, reportBootInteractive, reportFirstInteractive,
} from '../lib/bootTiming'

/** jsdom 的 `getBoundingClientRect` 恒为零尺寸 —— 这里给一个真的矩形。 */
function mountButton(): HTMLElement {
  const btn = document.createElement('button')
  btn.setAttribute('data-activity-item', 'genPanel')
  btn.getBoundingClientRect = () => ({
    left: 10, top: 10, width: 20, height: 20,
    right: 30, bottom: 30, x: 10, y: 10, toJSON: () => ({}),
  }) as DOMRect
  document.body.appendChild(btn)
  return btn
}

/** jsdom 没有实现 `elementFromPoint` —— 测什么就装什么。 */
function stubHitTest(hit: Element | null): void {
  Object.defineProperty(document, 'elementFromPoint', {
    configurable: true, writable: true, value: () => hit,
  })
}

const fetchCalls: Array<{ url: string; init: RequestInit }> = []

beforeEach(() => {
  _resetBootTimingForTest()
  fetchCalls.length = 0
  document.body.innerHTML = ''
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    fetchCalls.push({ url: String(url), init })
    return new Response('{"ok":true}', { status: 200 })
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('isMainUiClickable —— 命中测试，不是「元素存在」', () => {
  it('活动栏按钮不在 DOM 里 → 不可点', () => {
    stubHitTest(document.body)
    expect(isMainUiClickable()).toBe(false)
  })

  it('按钮在 DOM 但零尺寸（还没布局）→ 不可点', () => {
    const btn = mountButton()
    btn.getBoundingClientRect = () => ({
      left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0, x: 0, y: 0,
      toJSON: () => ({}),
    }) as DOMRect
    stubHitTest(btn)
    expect(isMainUiClickable()).toBe(false)
  })

  it('中心点命中按钮自己 → 可点', () => {
    const btn = mountButton()
    stubHitTest(btn)
    expect(isMainUiClickable()).toBe(true)
  })

  it('中心点命中按钮内部的图标 → 可点（图标是子元素）', () => {
    const btn = mountButton()
    const icon = document.createElement('span')
    btn.appendChild(icon)
    stubHitTest(icon)
    expect(isMainUiClickable()).toBe(true)
  })

  it('⚠️ 中心点被别的元素盖住（加载屏 / 向导 / 遮罩）→ 不可点', () => {
    // 对照：判据放宽成「按钮在 DOM 里」时，这条会变成 true 而上报一条假短耗时
    mountButton()
    stubHitTest(document.body)
    expect(isMainUiClickable()).toBe(false)
  })

  it('环境没有 elementFromPoint（jsdom）→ 判否，不伪造可点', () => {
    mountButton()
    // @ts-expect-error —— 故意删掉，模拟不支持命中测试的环境
    delete document.elementFromPoint
    expect(isMainUiClickable()).toBe(false)
  })
})

describe('reportBootInteractive', () => {
  it('可点 → 上报一次：名字走白名单常量，ms 取整', async () => {
    const btn = mountButton()
    stubHitTest(btn)
    expect(await reportBootInteractive(1234.7)).toBe(true)
    expect(fetchCalls).toHaveLength(1)
    expect(fetchCalls[0].url).toContain('/perf/client')
    expect(fetchCalls[0].init.method).toBe('POST')
    expect(JSON.parse(String(fetchCalls[0].init.body))).toEqual({
      name: BOOT_REPORT_NAME, ms: 1235,
    })
  })

  it('⚠️ 只上报一次 —— 重挂载 / effect 重跑不得让统计多一条', async () => {
    const btn = mountButton()
    stubHitTest(btn)
    expect(await reportBootInteractive(100)).toBe(true)
    expect(await reportBootInteractive(200)).toBe(false)
    expect(fetchCalls).toHaveLength(1)
  })

  it('不可点 → 一条都不发', async () => {
    mountButton()
    stubHitTest(document.body)
    expect(await reportBootInteractive(100)).toBe(false)
    expect(fetchCalls).toHaveLength(0)
  })

  it('⚠️ 上报失败静默：不抛错、不影响功能，也不重试', async () => {
    const btn = mountButton()
    stubHitTest(btn)
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    await expect(reportBootInteractive(100)).resolves.toBe(false)
  })
})

describe('reportFirstInteractive —— 逐帧轮询', () => {
  /** 把 rAF 换成可手动冲刷的队列（jsdom 的 rAF 要等真实帧，测试里会挂） */
  function queueRaf() {
    const q: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { q.push(cb); return q.length })
    return () => { const cbs = q.splice(0, q.length); cbs.forEach(cb => cb(0)) }
  }

  it('前两帧不可点、第三帧可点 → 只在最后一帧上报', async () => {
    const flush = queueRaf()
    mountButton()
    stubHitTest(document.body)
    reportFirstInteractive()
    flush()                        // 第 1 帧：不可点
    flush()                        // 第 2 帧：不可点
    expect(fetchCalls).toHaveLength(0)
    stubHitTest(document.querySelector('[data-activity-item]'))
    flush()                        // 第 3 帧：可点
    await Promise.resolve()
    expect(fetchCalls).toHaveLength(1)
    expect(JSON.parse(String(fetchCalls[0].init.body)).name).toBe(BOOT_REPORT_NAME)
  })

  it('⚠️ 一直不可点 → 到帧数上限就放弃，一条都不发', async () => {
    const flush = queueRaf()
    mountButton()
    stubHitTest(document.body)
    reportFirstInteractive()
    for (let i = 0; i < MAX_FRAMES + 5; i++) flush()
    await Promise.resolve()
    expect(fetchCalls).toHaveLength(0)
  })
})

describe('接线守卫（源码）', () => {
  const app = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf-8')

  it('⚠️ 三个条件都在：后端就绪 / 无失败屏 / 向导已关', () => {
    // 少任何一个都会在「界面还点不动」时上报 —— 那是一条假数据，且没人会发现
    expect(app).toContain('reportFirstInteractive()')
    expect(app).toMatch(/if \(!backendReady \|\| startupFailure \|\| showSetup\) return/)
  })
})
