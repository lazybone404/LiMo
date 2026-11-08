/**
 * 任务面板里的「应用更新」那一行。
 *
 * 这一行存在的理由只有一条：**下载与安装不依赖设置页是否还开着**。此前更新状态住在
 * `UpdateCard` 的局部 state 里，卡片一卸载进度就断了，而 Rust 侧的下载照常推进，
 * 最后应用无声退出。所以下面每条都在验证「离开设置页之后，用户仍然看得见、接得住」。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TaskPanel, TaskPanelToggle } from '../components/ui/TaskPanel'
import { useStore } from '../store'
import { UPDATE_IDLE, type UpdateFlowState } from '../store/updateSlice'

function updateState(patch: Partial<UpdateFlowState>): UpdateFlowState {
  return { ...UPDATE_IDLE, ...patch }
}

beforeEach(() => {
  useStore.setState({
    processingTasks: {},
    taskPanelOpen: true,
    update: { ...UPDATE_IDLE },
  })
})

describe('任务面板里的应用更新', () => {
  it('下载中：一行、带百分比与流量，且**没有**取消按钮', () => {
    useStore.setState({
      update: updateState({
        phase: 'downloading', version: '0.5.25',
        downloaded: 512, total: 1024, startedAt: Date.now(),
      }),
    })
    render(<TaskPanel />)

    expect(screen.getByText('应用更新 v0.5.25')).toBeInTheDocument()
    expect(screen.getByText(/下载中 50%/)).toBeInTheDocument()
    // 下载走插件整体 `download()`，没有中断点 —— 摆一个点了没反应的叉比不摆更糟。
    expect(screen.queryByTitle('取消任务')).toBeNull()
  })

  it('已下载：给出重启入口；有任务在跑时它是禁用的', () => {
    useStore.setState({
      processingTasks: { d1__generate: { kind: 'generate', status: 'running' } },
      update: updateState({ phase: 'ready', version: '0.5.25' }),
    })
    render(<TaskPanel />)

    expect(screen.getByText('已下载完成 · 等待重启安装')).toBeInTheDocument()
    expect(document.querySelector('[data-update-apply]')).toBeDisabled()
  })

  it('失败：显示原因，并给「重新下载」与「关闭」两条出路', () => {
    useStore.setState({
      update: updateState({ phase: 'failed', version: '0.5.25', error: '直连下载失败：连接超时' }),
    })
    render(<TaskPanel />)

    expect(screen.getByText('直连下载失败：连接超时')).toBeInTheDocument()
    expect(document.querySelector('[data-update-retry]')).not.toBeNull()
    expect(screen.getByTitle('关闭')).toBeInTheDocument()
  })

  it('入口角标把更新算进去（面板里有一行，入口就显示 1）', () => {
    useStore.setState({ update: updateState({ phase: 'downloading', version: '0.5.25' }) })
    render(<TaskPanelToggle />)
    expect(screen.getByTitle('任务 (1)')).toBeInTheDocument()
  })

  it('没有更新任务时角标不出现', () => {
    render(<TaskPanelToggle />)
    expect(screen.getByTitle('任务 (0)')).toBeInTheDocument()
    expect(screen.queryByText('1')).toBeNull()
  })
})
