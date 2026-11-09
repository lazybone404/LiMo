/**
 * `jobsSlice` —— 后台任务的前端镜像。
 *
 * 最关键的一条是**轮询是有条件的**：只在还有 `queued` / `running` 时排下一轮。
 * 永远每 1.5 秒打一次的写法不会报错，只会让一个已经全部失败的列表持续空转。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useStore } from '../store'
import type { JobInfo } from '../api/jobs'

const listJobs = vi.fn()

vi.mock('../api/jobs', () => ({
  listJobs: () => listJobs(),
  cancelJob: vi.fn(async (id: string) => ({ ...job({ id }), status: 'cancelled' as const })),
  dismissJob: vi.fn(async (id: string) => ({ ...job({ id }), dismissed: true })),
}))

function job(patch: Partial<JobInfo>): JobInfo {
  return {
    id: 'j1', kind: 'plugin.install', label: '安装插件依赖', status: 'running',
    payload: { name: 'whisper' }, progress: null, message: null, result: null,
    error: null, attempts: 1, cancel_requested: false, dismissed: false,
    created_at: Date.now(), started_at: Date.now(), finished_at: null,
    ...patch,
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  listJobs.mockReset()
  useStore.setState({ jobs: {} })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('jobsSlice', () => {
  it('refreshJobs 用后端列表整体替换本地镜像', async () => {
    listJobs.mockResolvedValue([job({ id: 'a', status: 'failed', error: 'boom' })])
    await useStore.getState().refreshJobs()
    expect(Object.keys(useStore.getState().jobs)).toEqual(['a'])
  })

  it('还有活跃任务时才排下一轮轮询', async () => {
    listJobs.mockResolvedValue([job({ id: 'a', status: 'running' })])
    await useStore.getState().refreshJobs()
    expect(listJobs).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1600)
    expect(listJobs).toHaveBeenCalledTimes(2)
  })

  it('全是终态时不再轮询（已经不会有新状态了）', async () => {
    listJobs.mockResolvedValue([job({ id: 'a', status: 'failed', error: 'boom' })])
    await useStore.getState().refreshJobs()
    await vi.advanceTimersByTimeAsync(5000)
    expect(listJobs).toHaveBeenCalledTimes(1)
  })

  it('trackJob 立刻可见，并启动轮询', async () => {
    listJobs.mockResolvedValue([job({ id: 'j1' })])
    useStore.getState().trackJob(job({ id: 'j1' }))
    expect(useStore.getState().jobs.j1).toBeDefined()

    await vi.advanceTimersByTimeAsync(1600)
    expect(listJobs).toHaveBeenCalled()
  })

  it('dismissJobById 先从本地移除，再等后台确认', async () => {
    useStore.setState({ jobs: { j1: job({ id: 'j1', status: 'failed', error: 'x' }) } })
    await useStore.getState().dismissJobById('j1')
    expect(useStore.getState().jobs.j1).toBeUndefined()
  })
})
