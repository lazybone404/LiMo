/**
 * 任务面板里的后端任务行（`POST /api/jobs` 那条链路的前端一半）。
 *
 * 这三条验证的是「集中显示」的三个必要条件：看得见（行存在）、接得住（失败留在
 * 面板里且能关）、算得对（行数与入口角标、安装按钮的禁用判据同源）。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TaskPanel, TaskPanelToggle } from '../components/ui/TaskPanel'
import { useStore } from '../store'
import { UPDATE_IDLE } from '../store/updateSlice'
import type { JobInfo } from '../api/jobs'

vi.mock('../api/jobs', () => ({
  listJobs: vi.fn(async () => []),
  cancelJob: vi.fn(async (id: string) => job({ id, status: 'cancelled' })),
  dismissJob: vi.fn(async (id: string) => job({ id, status: 'failed', dismissed: true })),
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
  useStore.setState({
    processingTasks: {},
    taskPanelOpen: true,
    update: { ...UPDATE_IDLE },
    jobs: {},
  })
})

describe('任务面板里的后端任务', () => {
  it('进行中：标题带插件名、显示进度，并且有取消按钮', () => {
    useStore.setState({ jobs: { j1: job({ progress: 0.4, message: '正在安装依赖…' }) } })
    render(<TaskPanel />)

    expect(screen.getByText('安装插件依赖 · whisper')).toBeInTheDocument()
    // `message` 优先于默认的「进行中」，并附百分比
    expect(screen.getByText(/正在安装依赖… 40%/)).toBeInTheDocument()
    expect(screen.getByTitle('取消任务')).toBeInTheDocument()
  })

  it('排队中：显示「排队中」而不是空白', () => {
    useStore.setState({
      jobs: { j1: job({ status: 'queued', started_at: null, progress: null }) },
    })
    render(<TaskPanel />)
    expect(screen.getByText('排队中')).toBeInTheDocument()
  })

  it('失败：原因留在面板里，只能关闭（重试入口在插件自己的页面）', async () => {
    useStore.setState({
      jobs: { j1: job({ status: 'failed', error: 'pip 退出码 1' }) },
    })
    render(<TaskPanel />)

    expect(screen.getByText('pip 退出码 1')).toBeInTheDocument()
    expect(screen.getByTitle('关闭')).toBeInTheDocument()
    expect(screen.queryByTitle('取消任务')).toBeNull()

    fireEvent.click(screen.getByTitle('关闭'))
    // 后台确认之后本地必然移除（请求失败也一样，见 `dismissJobById`）
    await waitFor(() => expect(useStore.getState().jobs.j1).toBeUndefined())
  })

  it('入口角标把后端任务算进去', () => {
    useStore.setState({ jobs: { j1: job({}) } })
    render(<TaskPanelToggle />)
    expect(screen.getByTitle('任务 (1)')).toBeInTheDocument()
  })

  it('有后端任务在跑时，更新安装按钮禁用（安装会让它断在半路）', () => {
    useStore.setState({
      jobs: { j1: job({}) },
      update: { ...UPDATE_IDLE, phase: 'ready', version: '0.5.25' },
    })
    render(<TaskPanel />)
    expect(document.querySelector('[data-update-apply]')).toBeDisabled()
  })
})
