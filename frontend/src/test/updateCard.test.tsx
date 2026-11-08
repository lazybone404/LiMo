/**
 * 设置 → 通用 → 应用更新 的界面判据。
 *
 * 这张卡的失效形态全是「界面说一切都好」：新版本看不见、跳过之后按钮还摆在那儿、
 * 有任务在跑却让人点了重启、失败只弹一句 toast 而卡片停在「可更新」。
 * 所以下面每条都对着一个具体的错法写，而不是泛泛地「渲染没崩」。
 *
 * ⚠️ 下载与安装是**两个**动作（2026-11-08）：`available --下载--> ready --重启--> installing`。
 * 测试必须分别盯住两段，否则「下载完就直接装」这种回退不会被发现。
 *
 * ⚠️ 这里 mock 的是 `../api/update`，保留真函数 `isDesktop` —— 否则「谁是 Tauri 环境」
 * 这条判据会散成两份（组件里一份、测试里一份）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { UpdateCard } from '../components/settings/UpdateCard'
import { applyUpdate, checkForUpdate, downloadUpdate } from '../api/update'
import { useStore } from '../store'
import { UPDATE_IDLE } from '../store/updateSlice'
import type { UpdateProgress, UpdateStatus } from '../api/update'

vi.mock('../api/update', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/update')>()
  return {
    ...actual,
    checkForUpdate: vi.fn(),
    downloadUpdate: vi.fn(),
    applyUpdate: vi.fn(),
    getPreparedUpdate: vi.fn().mockResolvedValue(null),
  }
})

const AVAILABLE: UpdateStatus = {
  currentVersion: '0.5.22',
  update: { version: '0.5.23', date: '2026-11-08T12:00:00Z', notes: '修了若干问题' },
}
const UP_TO_DATE: UpdateStatus = { currentVersion: '0.5.22', update: null }

const mockedCheck = vi.mocked(checkForUpdate)
const mockedDownload = vi.mocked(downloadUpdate)
const mockedApply = vi.mocked(applyUpdate)

/** 让 `isDesktop()` 为真。它读的是 `window.__TAURI_INTERNALS__` 这个**键在不在**。 */
function enterDesktop() {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
}

function leaveDesktop() {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
}

beforeEach(() => {
  localStorage.clear()
  mockedCheck.mockReset()
  mockedDownload.mockReset()
  mockedApply.mockReset()
  useStore.setState({ processingTasks: {}, update: { ...UPDATE_IDLE } })
  enterDesktop()
})

afterEach(() => {
  leaveDesktop()
})

describe('环境判据', () => {
  it('非桌面版：只说明原因，一个按钮都不渲染', () => {
    leaveDesktop()
    render(<UpdateCard />)
    expect(screen.getByText(/需要桌面版的安装环境/)).toBeInTheDocument()
    expect(screen.queryByText('检查更新')).toBeNull()
    // 连检查都不该发出去 —— 浏览器里 `invoke` 不存在，发了只会得到一条无意义的报错。
    expect(mockedCheck).not.toHaveBeenCalled()
  })
})

describe('检查结果', () => {
  it('已是最新 → 说「已是最新版本」，且带上当前版本号', async () => {
    mockedCheck.mockResolvedValue(UP_TO_DATE)
    render(<UpdateCard />)
    expect(await screen.findByText('已是最新版本')).toBeInTheDocument()
    // 当前版本在前端别处看不到，它是这张卡顺带承担的一件事。
    expect(screen.getByText('当前版本 v0.5.22')).toBeInTheDocument()
  })

  it('有新版本 → 版本号 + 发布说明 + 下载与跳过两个按钮', async () => {
    mockedCheck.mockResolvedValue(AVAILABLE)
    render(<UpdateCard />)
    expect(await screen.findByText('发现新版本 v0.5.23')).toBeInTheDocument()
    expect(screen.getByText('修了若干问题')).toBeInTheDocument()
    expect(screen.getByText('下载更新')).toBeInTheDocument()
    expect(screen.getByText('跳过此版本')).toBeInTheDocument()
    // 「下载」这一步不伤害任何东西，所以**不该**要求二次确认。
    expect(screen.queryByText('开始安装')).toBeNull()
  })

  it('检查失败 → 就地显示原因与 Releases 出路，不吞掉', async () => {
    // 卡片整个界面就是「现在是什么状态」，把错误丢进 toast 等于让它继续显示可更新结论。
    mockedCheck.mockRejectedValue('所有下载线路都失败了')
    render(<UpdateCard />)
    expect(await screen.findByText('所有下载线路都失败了')).toBeInTheDocument()
    expect(screen.getByText('在浏览器中打开 Releases')).toBeInTheDocument()
  })
})

describe('跳过此版本', () => {
  it('点了之后变成「已跳过」，并写进 localStorage 跨会话存活', async () => {
    mockedCheck.mockResolvedValue(AVAILABLE)
    render(<UpdateCard />)
    fireEvent.click(await screen.findByText('跳过此版本'))

    expect(screen.getByText('已跳过')).toBeInTheDocument()
    // 跳过的意义就是「别再问我」—— 按钮必须消失，否则等于没听。
    expect(screen.queryByText('下载更新')).toBeNull()
    expect(JSON.parse(localStorage.getItem('mbw_update') || '{}').skippedVersion).toBe('0.5.23')
  })

  it('「取消跳过」清掉记录，按钮回来', async () => {
    mockedCheck.mockResolvedValue(AVAILABLE)
    render(<UpdateCard />)
    fireEvent.click(await screen.findByText('跳过此版本'))
    fireEvent.click(screen.getByText('取消跳过'))

    expect(screen.getByText('下载更新')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('mbw_update') || '{}').skippedVersion).toBe('')
  })
})

describe('下载', () => {
  it('进度事件 → 百分比与流量；走中转时多一句说明', async () => {
    mockedCheck.mockResolvedValue(AVAILABLE)
    let emit: ((p: UpdateProgress) => void) | null = null
    mockedDownload.mockImplementation((cb) => {
      emit = cb
      return new Promise<void>(() => {})
    })
    render(<UpdateCard />)
    fireEvent.click(await screen.findByText('下载更新'))
    await waitFor(() => expect(emit).not.toBeNull())

    act(() => emit!({ kind: 'progress', downloaded: 340, total: 1000, viaProxy: true }))
    expect(screen.getByText(/34%/)).toBeInTheDocument()
    expect(screen.getByText(/340 B \/ 1000 B/)).toBeInTheDocument()
    expect(screen.getByText(/正在通过中转线路下载/)).toBeInTheDocument()
  })

  it('下载完成 → 进入「已下载」而**不是**自动安装', async () => {
    mockedCheck.mockResolvedValue(AVAILABLE)
    mockedDownload.mockResolvedValue(undefined)
    render(<UpdateCard />)
    fireEvent.click(await screen.findByText('下载更新'))

    expect(await screen.findByText(/已下载完成/)).toBeInTheDocument()
    // 下载成功绝不能顺手把应用重启掉 —— 这是拆两个命令的全部意义。
    expect(mockedApply).not.toHaveBeenCalled()
    expect(screen.getByText('重启并安装')).toBeInTheDocument()
  })

  it('下载失败 → 就地显示原因，并给「重新下载」', async () => {
    mockedCheck.mockResolvedValue(AVAILABLE)
    mockedDownload.mockRejectedValue('所有下载线路都失败了')
    render(<UpdateCard />)
    fireEvent.click(await screen.findByText('下载更新'))

    expect(await screen.findByText('所有下载线路都失败了')).toBeInTheDocument()
    expect(screen.getByText('重新下载')).toBeInTheDocument()
    // 失败后必须能再点一次「检查更新」—— 卡在进度条上就再也点不动了。
    expect(screen.getByText('检查更新').closest('button')).not.toBeDisabled()
  })
})

describe('安装', () => {
  /** 先走到 `ready`：查到新版 → 下载完成。 */
  async function reachReady() {
    mockedCheck.mockResolvedValue(AVAILABLE)
    mockedDownload.mockResolvedValue(undefined)
    render(<UpdateCard />)
    fireEvent.click(await screen.findByText('下载更新'))
    await screen.findByText(/已下载完成/)
  }

  it('两步确认：先点「重启并安装」，再点「开始安装」才真的调用', async () => {
    await reachReady()
    // 永不结算 —— 模拟 Windows 上 `install()` 直接 `exit(0)`、Promise 没有下文。
    mockedApply.mockImplementation(() => new Promise<void>(() => {}))
    fireEvent.click(screen.getByText('重启并安装'))

    expect(mockedApply).not.toHaveBeenCalled()
    expect(screen.getByText('开始安装')).toBeInTheDocument()

    fireEvent.click(screen.getByText('开始安装'))
    await waitFor(() => expect(mockedApply).toHaveBeenCalledTimes(1))
  })

  it('有任务在跑 → 「开始安装」禁用（安装会让生成 / 转录断在半路）', async () => {
    useStore.setState({ processingTasks: { d1__generate: { kind: 'generate', status: 'running' } } })
    await reachReady()

    // 直接点「重启并安装」的按钮就已经禁用，连确认框都进不去。
    expect(screen.getByText('重启并安装').closest('button')).toBeDisabled()
    expect(screen.getByText('有任务正在运行')).toBeInTheDocument()
  })

  it('安装失败 → 回到可重试状态并显示原因（不能停在「正在安装」）', async () => {
    await reachReady()
    mockedApply.mockRejectedValue('安装更新失败：签名校验失败')
    fireEvent.click(screen.getByText('重启并安装'))
    fireEvent.click(screen.getByText('开始安装'))

    expect(await screen.findByText('安装更新失败：签名校验失败')).toBeInTheDocument()
    expect(screen.getByText('重新下载')).toBeInTheDocument()
  })
})
