/**
 * 页面重新加载之后，更新流程的状态能从哪里读回来 —— 判据是 `get_update_state`。
 *
 * 失效形态全是「界面空闲、后台在跑」：进度回调走的是发起 `invoke` 的那个页面上的
 * `Channel`，刷新即断；Rust 侧的下载照常推进，下完还会把进程换成新版本。界面若显示
 * 「已是最新版本」，用户完全不知道刚才那个「什么都没发生」其实是一次更新。
 *
 * 所以这里不测渲染（那是 `updateCard.test.tsx` 的事），只测 store 与快照之间的往返。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useStore } from '../store'
import { UPDATE_IDLE } from '../store/updateSlice'
import { getUpdateState, resetUpdateState, type UpdateSnapshot } from '../api/update'

vi.mock('../api/update', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/update')>()
  return {
    ...actual,
    checkForUpdate: vi.fn(),
    downloadUpdate: vi.fn(),
    applyUpdate: vi.fn(),
    getUpdateState: vi.fn().mockResolvedValue(null),
    resetUpdateState: vi.fn().mockResolvedValue(undefined),
  }
})

const mockedState = vi.mocked(getUpdateState)
const mockedReset = vi.mocked(resetUpdateState)

function snap(partial: Partial<UpdateSnapshot>): UpdateSnapshot {
  return {
    phase: 'idle', version: null, downloaded: 0, total: null, viaProxy: false, error: null,
    ...partial,
  }
}

beforeEach(() => {
  localStorage.clear()
  mockedState.mockReset()
  mockedState.mockResolvedValue(null)
  mockedReset.mockReset()
  mockedReset.mockResolvedValue(undefined)
  useStore.setState({ update: { ...UPDATE_IDLE } })
})

afterEach(() => {
  vi.useRealTimers()
  // 每个用例都要把轮询停掉：store 是单例，定时器会跨用例活下来。
  useStore.getState().dismissUpdate()
})

describe('recoverState', () => {
  it('下载到一半刷新页面 → 进度仍然显示', async () => {
    mockedState.mockResolvedValue(snap({
      phase: 'downloading', version: '0.5.23', downloaded: 512, total: 1024,
    }))
    await useStore.getState().recoverState()

    const u = useStore.getState().update
    expect(u.phase).toBe('downloading')
    expect(u.version).toBe('0.5.23')
    expect(u.downloaded).toBe(512)
    expect(u.total).toBe(1024)
    // 「已用多久」要接着算，不能从刷新那一刻重新归零。
    expect(u.startedAt).not.toBeNull()
  })

  it('包已下好、等人点重启 → 捡回 ready', async () => {
    mockedState.mockResolvedValue(snap({
      phase: 'ready', version: '0.5.23', downloaded: 1024, total: 1024,
    }))
    await useStore.getState().recoverState()

    expect(useStore.getState().update.phase).toBe('ready')
  })

  it('上次失败 → 连同原因一起捡回来（否则刷新一次失败就消失了）', async () => {
    mockedState.mockResolvedValue(snap({
      phase: 'failed', version: '0.5.23', error: '所有下载线路都失败了',
    }))
    await useStore.getState().recoverState()

    const u = useStore.getState().update
    expect(u.phase).toBe('failed')
    expect(u.error).toBe('所有下载线路都失败了')
  })

  it('快照是 idle → 不动界面（没有可恢复的流程）', async () => {
    useStore.setState({ update: { ...UPDATE_IDLE, phase: 'available', version: '0.5.23' } })
    mockedState.mockResolvedValue(snap({ phase: 'idle' }))
    await useStore.getState().recoverState()

    expect(useStore.getState().update.phase).toBe('available')
  })

  it('读不到快照（浏览器 / 命令不存在）→ 保持原状，不抛错', async () => {
    mockedState.mockRejectedValue('command not found')
    await expect(useStore.getState().recoverState()).resolves.toBeUndefined()
    expect(useStore.getState().update.phase).toBe('idle')
  })
})

describe('下载途中的轮询', () => {
  it('还在下载就接着轮询，结束后停下来', async () => {
    vi.useFakeTimers()
    mockedState
      .mockResolvedValueOnce(snap({ phase: 'downloading', downloaded: 256, total: 1024 }))
      .mockResolvedValue(snap({ phase: 'ready', downloaded: 1024, total: 1024 }))

    await useStore.getState().recoverState()
    expect(useStore.getState().update.phase).toBe('downloading')

    await vi.advanceTimersByTimeAsync(1000)
    expect(useStore.getState().update.phase).toBe('ready')

    // `ready` 之后不该再有请求 —— 一个已经结束的流程不会有新状态。
    const callsAfterReady = mockedState.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(mockedState.mock.calls.length).toBe(callsAfterReady)
  })
})

describe('清快照', () => {
  it('点掉失败记录 → 同时清 Rust 快照，刷新后它不会再冒出来', () => {
    useStore.setState({ update: { ...UPDATE_IDLE, phase: 'failed', error: 'x' } })
    useStore.getState().dismissUpdate()

    expect(useStore.getState().update.phase).toBe('idle')
    expect(mockedReset).toHaveBeenCalled()
  })
})
