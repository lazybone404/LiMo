/**
 * `setProcessing` 对 `startedAt` 的继承语义 —— 唯一一个**不全量替换**的字段。
 *
 * 三条判据，每一条都对应一个真会发生的场景：
 * 1. 同一任务连续推事件（生成时每秒好几条）→ 计时起点不变（否则永远显示 0 秒）
 * 2. **失败后 5 秒内重试** → 起点重算（`failTask` 保留条目且不清 `startedAt`，
 *    只看「这个 key 有任务」会让重试一上来就显示「已用 3 分 05 秒」）
 * 3. 清除后重建 → 起点重算（同一个自然场景：取消后再发起）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useStore } from '../store'

const KEY = 'doc1__generate'

beforeEach(() => {
  useStore.setState({ processingTasks: {} })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('setProcessing 的 startedAt 继承', () => {
  it('首次创建时取当前时间', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-03T10:00:00'))
    useStore.getState().setProcessing(KEY, { kind: 'generate' })
    expect(useStore.getState().processingTasks[KEY].startedAt).toBe(Date.now())
  })

  it('running 中继续推事件 → 起点不变', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-03T10:00:00'))
    useStore.getState().setProcessing(KEY, { kind: 'generate' })
    const first = useStore.getState().processingTasks[KEY].startedAt

    vi.setSystemTime(new Date('2026-11-03T10:00:05'))
    // 第二条事件带上了新进度，但**没带** startedAt
    useStore.getState().setProcessing(KEY, { kind: 'generate', progress: { notes: 120 } })

    const t = useStore.getState().processingTasks[KEY]
    expect(t.startedAt).toBe(first)
    expect(t.progress?.notes).toBe(120)   // 其余字段仍是全量替换
  })

  it('failed 后重建 → 起点重算（不从上一次任务的开头算）', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-03T10:00:00'))
    useStore.getState().setProcessing(KEY, { kind: 'generate' })
    const first = useStore.getState().processingTasks[KEY].startedAt

    vi.setSystemTime(new Date('2026-11-03T10:03:05'))
    useStore.getState().failTask(KEY, '模型超时')
    expect(useStore.getState().processingTasks[KEY].status).toBe('failed')
    // failTask 不清 startedAt（它只改 status）
    expect(useStore.getState().processingTasks[KEY].startedAt).toBe(first)

    useStore.getState().setProcessing(KEY, { kind: 'generate' })
    expect(useStore.getState().processingTasks[KEY].startedAt).toBe(Date.now())
    expect(useStore.getState().processingTasks[KEY].startedAt).not.toBe(first)
  })

  it('清除后重建 → 起点重算', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-03T10:00:00'))
    useStore.getState().setProcessing(KEY, { kind: 'generate' })
    const first = useStore.getState().processingTasks[KEY].startedAt

    useStore.getState().setProcessing(KEY, null)
    expect(useStore.getState().processingTasks[KEY]).toBeUndefined()

    vi.setSystemTime(new Date('2026-11-03T10:01:00'))
    useStore.getState().setProcessing(KEY, { kind: 'generate' })
    expect(useStore.getState().processingTasks[KEY].startedAt).toBe(Date.now())
    expect(useStore.getState().processingTasks[KEY].startedAt).not.toBe(first)
  })

  it('调用方显式传 startedAt 时以传入为准（续跑场景）', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-03T10:00:00'))
    const explicit = Date.now() - 30_000
    useStore.getState().setProcessing(KEY, { kind: 'generate', startedAt: explicit })
    expect(useStore.getState().processingTasks[KEY].startedAt).toBe(explicit)
  })
})
