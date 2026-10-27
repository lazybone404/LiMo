// LogPanel 的「性能」标签页 —— 打点数据的消费端
//
// 背景：`backend/app/core/perf.py` 是个**只记录、不判断**的打点层，落盘
// `{data_dir}/perf.log`（JSON Lines，按天滚动 14 天）+ `GET /api/perf` 给进程内分位数。
// 没有这个面板，用户想知道「哪类操作慢」只能开终端 curl —— 那条链路太长了。
//
// ⚠️ 本文件钉住的**不是**「表格能渲染」，而是三件容易静默失效的事：
//   1. **不做颜色分级**。染红某个数字就是在替用户决定「多少算慢」，而 `perf.py`
//      的整个设计前提就是「阈值该由看数据的人定」。改回去应当判红。
//   2. **`logFile: null` 要如实说**。落盘不可用（目录不可写）时后端只记一次 warning，
//      前端若照样写「完整留档 perf.log」，用户会以为有文件可看 —— 与 `perf.py`
//      的「失败显式化」直接冲突。
//   3. **`[ttfb]` 要有说明**。那些是流式端点的**建连时间**（2–14ms），不是流总时长；
//      不给一句话，用户会把 `generate/stream p95 = 8ms` 读成「生成很快」。
//
// ⚠️ 每条用例都配了对照实验（把实现改回错误版本，用例确实会判红）。

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { LogPanel } from '../components/ui/LogPanel'
import { apiFetch } from '../api/client'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, apiFetch: vi.fn() }
})

const mockFetch = vi.mocked(apiFetch)

/** `apiFetch` 的最小替身：组件只用到 `.json()`。 */
function jsonOk(data: unknown) {
  return Promise.resolve({ json: () => Promise.resolve(data) } as unknown as Response)
}

const EMPTY_PERF = {
  groups: [],
  totalSamples: 0,
  logFile: '/home/u/.limo/perf.log',
  keepDays: 14,
  startedAt: '2026-10-27T10:00:00',
}

const SAMPLE_PERF = {
  groups: [
    { name: 'GET /generate/{tx_id}/stream [ttfb]', count: 3, min: 2.1, p50: 4.0, p95: 8.4, max: 8.4, avg: 4.8 },
    { name: 'store.save_index', count: 12, min: 3.0, p50: 9.0, p95: 41.0, max: 61.0, avg: 12.5 },
  ],
  totalSamples: 15,
  logFile: '/home/u/.limo/perf.log',
  keepDays: 14,
  startedAt: '2026-10-27T10:00:00',
}

/** 让 `/logs` 恒为空、`/perf` 返回给定快照（按路径分派，不依赖调用顺序）。 */
function routeFetch(perf: unknown) {
  mockFetch.mockImplementation((url: string) => {
    if (String(url).includes('/perf')) return jsonOk(perf)
    return jsonOk({ logs: [] })
  })
}

beforeEach(() => {
  mockFetch.mockReset()
})

describe('LogPanel · 性能标签页', () => {
  it('默认停在日志页，不发 /perf 请求', async () => {
    routeFetch(SAMPLE_PERF)
    render(<LogPanel onClose={() => {}} />)
    await waitFor(() => expect(mockFetch).toHaveBeenCalled())
    expect(mockFetch.mock.calls.every(c => !String(c[0]).includes('/perf'))).toBe(true)
  })

  it('切到性能页后拉 /perf 并渲染分组', async () => {
    routeFetch(SAMPLE_PERF)
    render(<LogPanel onClose={() => {}} />)

    fireEvent.click(screen.getByRole('tab', { name: '性能' }))

    await waitFor(() => expect(screen.getByText('store.save_index')).toBeInTheDocument())
    expect(screen.getByText('GET /generate/{tx_id}/stream [ttfb]')).toBeInTheDocument()
    // 表头四列都在
    for (const h of ['打点', '次数', 'p50', 'p95', 'max']) {
      expect(screen.getByText(h)).toBeInTheDocument()
    }
  })

  it('⚠️ 不做颜色分级 —— 再慢的数字也不染成 error/warning', async () => {
    // 对照实验：给 p95 加一个 `>= 1000 ? 'text-error' : ''` 的分级，本条判红。
    // 理由见 perf.py 模块 docstring：「阈值该由看数据的人按分位数决定，
    // 而不是由写代码的人猜一个 2000ms 写死」。
    routeFetch({
      ...EMPTY_PERF,
      groups: [{ name: 'some.slow', count: 1, min: 9999, p50: 9999, p95: 9999, max: 9999, avg: 9999 }],
      totalSamples: 1,
    })
    render(<LogPanel onClose={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: '性能' }))

    // p50 / p95 / max 三格都是 9999ms → 三处「10.0s」，故用 findAll
    const cells = await screen.findAllByText('10.0s')
    expect(cells).toHaveLength(3)
    for (const c of cells) expect(c.className).not.toMatch(/text-error|text-warning/)
  })

  it('空数据说「暂无打点数据」，不渲染空表头', async () => {
    routeFetch(EMPTY_PERF)
    render(<LogPanel onClose={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: '性能' }))

    await waitFor(() => expect(screen.getByText(/暂无打点数据/)).toBeInTheDocument())
    expect(screen.queryByText('p95')).not.toBeInTheDocument()
  })

  it('⚠️ logFile 为 null 时如实说「落盘不可用」，不冒充有文件', async () => {
    // 对照实验：永远渲染「完整留档 perf.log」，本条判红。
    // 落盘不可用时后端只记一次 warning、进程内统计照常 —— 前端说反了就是骗人。
    routeFetch({ ...EMPTY_PERF, logFile: null })
    render(<LogPanel onClose={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: '性能' }))

    await waitFor(() => expect(screen.getByText(/落盘不可用/)).toBeInTheDocument())
    expect(screen.queryByText(/完整留档/)).not.toBeInTheDocument()
  })

  it('存在 [ttfb] 分组时给出「建连时间」的说明', async () => {
    routeFetch(SAMPLE_PERF)
    render(<LogPanel onClose={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: '性能' }))

    await waitFor(() => expect(screen.getByText(/建连时间而非流总时长/)).toBeInTheDocument())
  })

  it('没有 [ttfb] 分组时不摆那句说明（省得让人去找不存在的东西）', async () => {
    routeFetch({
      ...EMPTY_PERF,
      groups: [{ name: 'store.save_index', count: 1, min: 1, p50: 1, p95: 1, max: 1, avg: 1 }],
      totalSamples: 1,
    })
    render(<LogPanel onClose={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: '性能' }))

    await waitFor(() => expect(screen.getByText('store.save_index')).toBeInTheDocument())
    expect(screen.queryByText(/建连时间而非流总时长/)).not.toBeInTheDocument()
  })

  it('复制性能页 → 剪贴板是可直接贴给 agent 的 Markdown 表', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    routeFetch(SAMPLE_PERF)
    render(<LogPanel onClose={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: '性能' }))
    await waitFor(() => expect(screen.getByText('store.save_index')).toBeInTheDocument())

    fireEvent.click(screen.getByTitle('复制性能数据（Markdown）'))

    await waitFor(() => expect(writeText).toHaveBeenCalled())
    const text = writeText.mock.calls[0][0] as string
    expect(text).toContain('15 条样本')
    expect(text).toContain('/home/u/.limo/perf.log')
    expect(text).toContain('| 打点 | 次数 | p50 | p95 | max |')
    expect(text).toContain('| store.save_index | 12 | 9 | 41 | 61 |')
  })
})
