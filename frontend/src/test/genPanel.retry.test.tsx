/**
 * 生成失败红框的「重试」—— 第 3 条 UX 优化，钉在 DOM 上。
 *
 * 三个判据各自对应一个**看起来等效、实则不同**的实现：
 *  ① 「重试」按钮真的渲染出来（只在内部维护一个 `retry` 变量不算）；
 *  ② 点它真的**再发一次生成**（写死成「清掉错误」也长得一样）；
 *  ③ 未保存编辑时**仍然弹 confirm**，且取消后一次都不发 ——
 *     跳过拦截 = 在用户最没防备的时刻静默吞掉他刚写下的东西。
 *
 * ⚠️ 还钉了一条**跨模块**的：失败条目在 `failTask` 后保留 5 秒（`tasksSlice.ts`），
 * 而「失败 → 立刻点重试」正是这个按钮最可能的用法。若 `docTasks` / `activeGenerates`
 * 不做 `status === 'running'` 过滤，`handleGenerate` 首行 `if (isGenerating) return`
 * 会把这一下**静默**拦掉（连 toast 都没有）—— 不点按钮就看不出来。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react'

const streamGenerateMock = vi.fn()

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    streamGenerate: (...args: unknown[]) => streamGenerateMock(...args),
    listEnabledModels: vi.fn().mockResolvedValue([]),
    listProviders: vi.fn().mockResolvedValue([]),
    getMarkdown: vi.fn().mockResolvedValue(null),
    getTranscript: vi.fn().mockResolvedValue(null),
  }
})

import { useStore } from '../store'
import { GenPanel } from '../components/gen-panel/GenPanel'

const DOC = {
  id: 'd1', title: '测试文档', sourceType: 'webpage', sourceFileName: '', sourceUrl: '',
  coverUrl: '', engine: '', language: '', contentHash: '', tags: [], createdAt: '2026-01-01T00:00:00Z',
}

/** 让这次生成失败（后端信封形状：message + hint + detail + recoverable）。 */
function failNextGeneration() {
  streamGenerateMock.mockImplementationOnce((_id: string, _opts: unknown, cb: {
    onError: (msg: string, info?: { hint?: string; detail?: string; recoverable?: boolean }) => void
  }) => {
    cb.onError('模型不存在或已下架', {
      hint: '请到「设置 → 模型」里重新选择一个模型',
      detail: 'MODEL_NOT_FOUND',
      recoverable: true,
    })
  })
}

beforeEach(() => {
  streamGenerateMock.mockReset()
  localStorage.clear()
  useStore.setState({
    currentDocId: 'd1',
    docs: [DOC] as never,
    currentRawText: '正文内容',
    processingTasks: {},
    dirtyEditingDocId: null,
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('生成失败红框的重试', () => {
  it('失败后红框里渲染「重试」，并带上 hint / detail', async () => {
    failNextGeneration()
    render(<GenPanel />)

    fireEvent.click(screen.getByRole('button', { name: '生成' }))

    expect(streamGenerateMock).toHaveBeenCalledTimes(1)
    const msg = await screen.findByText('模型不存在或已下架')
    // 三行各在自己的容器里（message / hint / detail），但都在同一张卡片内
    const card = msg.closest('div.rounded-lg') as HTMLElement
    expect(card).not.toBeNull()
    expect(within(card).getByText('请到「设置 → 模型」里重新选择一个模型')).toBeTruthy()
    expect(within(card).getByText('MODEL_NOT_FOUND')).toBeTruthy()
    expect(within(card).getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('点「重试」真的再发一次生成（失败条目还留在 processingTasks 里也不拦）', async () => {
    failNextGeneration()
    render(<GenPanel />)

    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(streamGenerateMock).toHaveBeenCalledTimes(1)

    // `failTask` 把条目改成 failed 并保留 5 秒 —— 此刻它**仍在** processingTasks 里。
    // 若 `docTasks` 不做 running 过滤，下一次点击会被静默拦下，这条断言就会停在 1。
    expect(Object.keys(useStore.getState().processingTasks).length).toBeGreaterThan(0)

    fireEvent.click(await screen.findByRole('button', { name: '重试' }))

    await waitFor(() => expect(streamGenerateMock).toHaveBeenCalledTimes(2))
  })

  it('两条生成刚失败（条目还在 5 秒保留期内），点重试仍能发出', async () => {
    failNextGeneration()
    render(<GenPanel />)
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(streamGenerateMock).toHaveBeenCalledTimes(1)

    // 模拟「两条生成都刚失败、条目还没被 5 秒定时器清掉」。`activeGenerates` 若只按
    // 形状数而不看 `status`，这里会说「同时最多 2 个生成任务，请等待完成后再试」——
    // 而**根本没有任何东西在跑**，用户点重试只会看到一条莫名其妙的提示。
    useStore.setState({
      processingTasks: {
        'a__generate': { kind: 'generate', status: 'failed', title: 'A' },
        'b__generate': { kind: 'generate', status: 'failed', title: 'B' },
      } as never,
    })

    fireEvent.click(await screen.findByRole('button', { name: '重试' }))
    await waitFor(() => expect(streamGenerateMock).toHaveBeenCalledTimes(2))
  })

  it('未保存编辑时点「重试」仍弹 confirm；取消则一次都不发', async () => {
    failNextGeneration()
    render(<GenPanel />)
    // 第一次生成时还没有未保存编辑（否则它自己就会被拦在 confirm 上）
    fireEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(streamGenerateMock).toHaveBeenCalledTimes(1)

    // 失败后用户去编辑器里改了几笔又回来点重试 —— 正是 confirm 必须拦住的那一刻
    useStore.setState({ dirtyEditingDocId: 'd1' })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    fireEvent.click(await screen.findByRole('button', { name: '重试' }))

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0][0]).toContain('未保存')
    // 取消 = 什么都没发生：没有第二次请求
    expect(streamGenerateMock).toHaveBeenCalledTimes(1)
  })

  it('recoverable === false 时不渲染「重试」（纯前端校验没有可重试的动作）', async () => {
    // 无有效转录内容 → 纯前端校验，标了 recoverable: false
    useStore.setState({ currentRawText: '' })
    render(<GenPanel />)

    fireEvent.click(screen.getByRole('button', { name: '生成' }))

    await screen.findByText(/无有效转录内容/)
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
    expect(streamGenerateMock).not.toHaveBeenCalled()
  })
})
