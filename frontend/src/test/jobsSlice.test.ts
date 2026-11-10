/**
 * `jobsSlice` —— 后台任务的前端镜像。
 *
 * 最关键的一条是**轮询是有条件的**：只在还有 `queued` / `running` 时排下一轮。
 * 永远每 1.5 秒打一次的写法不会报错，只会让一个已经全部失败的列表持续空转。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useStore, type DocEntry } from '../store'
import type { JobInfo } from '../api/jobs'

const listJobs = vi.fn()
const getJob = vi.fn()
const listTranscripts = vi.fn()
const getMarkdown = vi.fn()

vi.mock('../api/jobs', () => ({
  listJobs: () => listJobs(),
  getJob: (id: string) => getJob(id),
  cancelJob: vi.fn(async (id: string) => ({ ...job({ id }), status: 'cancelled' as const })),
  dismissJob: vi.fn(async (id: string) => ({ ...job({ id }), dismissed: true })),
}))

vi.mock('../api/transcripts', () => ({
  listTranscripts: () => listTranscripts(),
}))

// 只替换 `getMarkdown`：`api/client` 还导出 `getErrorMessage` 等，整模块替掉会连带
// 打断其它 slice 的 import（同一份 store 在这里被真实加载）。
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, getMarkdown: (id: string, mode: string) => getMarkdown(id, mode) }
})

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
  getJob.mockReset()
  listTranscripts.mockReset()
  getMarkdown.mockReset()
  useStore.setState({ jobs: {}, docs: [], toasts: [] })
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

/** 冲掉 `settleVanished` 那几层 await（它是 fire-and-forget）。 */
const flush = () => vi.advanceTimersByTimeAsync(10)

const doneSynthesis = (patch: Partial<JobInfo> = {}) => job({
  id: 's1', kind: 'synthesis', label: '合成对比综述', status: 'done',
  result: { id: 'd1', title: '三篇对比综述', mode: 'map_reduce', degraded: [], excluded: [] },
  finished_at: Date.now(),
  ...patch,
})

const doc = (id: string, title = '文档'): DocEntry => ({ id, title } as unknown as DocEntry)

describe('任务结束后的补偿', () => {
  it('消失的合成任务：把产出接进列表并提示（没人看着的那条路）', async () => {
    useStore.setState({ jobs: { s1: job({ id: 's1', kind: 'synthesis' }) } })
    // 默认列表只带「进行中 + 未忽略的失败」—— 结束之后它就不在这里了
    listJobs.mockResolvedValue([])
    getJob.mockResolvedValue(doneSynthesis())
    listTranscripts.mockResolvedValue([doc('d1', '三篇对比综述')])

    await useStore.getState().refreshJobs()
    await flush()

    expect(getJob).toHaveBeenCalledWith('s1')
    expect(useStore.getState().docs.map(d => d.id)).toEqual(['d1'])
    expect(useStore.getState().toasts.some(t => t.message.includes('合成完成'))).toBe(true)
  })

  it('产出已经在列表里（流还活着，onDone 先插过）→ 不重拉、不重复提示', async () => {
    useStore.setState({
      jobs: { s1: job({ id: 's1', kind: 'synthesis' }) },
      docs: [doc('d1', '三篇对比综述')],
    })
    listJobs.mockResolvedValue([])
    getJob.mockResolvedValue(doneSynthesis())

    await useStore.getState().refreshJobs()
    await flush()

    expect(listTranscripts).not.toHaveBeenCalled()
    expect(useStore.getState().toasts).toHaveLength(0)
  })

  it('消失但状态是失败 → 不碰文档列表（失败那行还留在面板上）', async () => {
    useStore.setState({ jobs: { s1: job({ id: 's1', kind: 'synthesis' }) } })
    listJobs.mockResolvedValue([])
    // 失败的任务默认留在列表里等人处理；消失只发生在用户点了 × 之后
    getJob.mockResolvedValue(job({
      id: 's1', kind: 'synthesis', status: 'failed', error: '超时', dismissed: true,
    }))

    await useStore.getState().refreshJobs()
    await flush()

    expect(listTranscripts).not.toHaveBeenCalled()
  })

  it('插件安装结束不碰文档列表（补偿按 kind 分，不是「所有任务都刷一遍」）', async () => {
    useStore.setState({ jobs: { p1: job({ id: 'p1' }) } })
    listJobs.mockResolvedValue([])
    getJob.mockResolvedValue(job({ id: 'p1', status: 'done', result: { status: 'ok' } }))

    await useStore.getState().refreshJobs()
    await flush()

    expect(listTranscripts).not.toHaveBeenCalled()
  })
})

const doneEpub = (patch: Partial<JobInfo> = {}) => job({
  id: 'e1', kind: 'epub.interpret', label: '电子书一键解读', status: 'done',
  payload: { doc_id: 'd1', title: '测试书' },
  result: { doc_id: 'd1', title: '测试书', chapters_done: 3, chapters_failed: 0 },
  finished_at: Date.now(),
  ...patch,
})

const activeEpub = () => job({
  id: 'e1', kind: 'epub.interpret', payload: { doc_id: 'd1' }, status: 'running',
})

describe('电子书解读结束后的补偿', () => {
  it('没开着这篇：只刷新文档列表并提示，不去读产物', async () => {
    useStore.setState({ jobs: { e1: activeEpub() } })
    listJobs.mockResolvedValue([])
    getJob.mockResolvedValue(doneEpub())
    listTranscripts.mockResolvedValue([doc('d1', '测试书')])

    await useStore.getState().refreshJobs()
    await flush()

    // `modes` 是列表拉取那一刻的快照 —— 不刷新的话切回这篇时预览区以为「没有产物」
    expect(listTranscripts).toHaveBeenCalled()
    expect(getMarkdown).not.toHaveBeenCalled()
    expect(useStore.getState().toasts.some(t => t.message.includes('解读完成'))).toBe(true)
  })

  it('正开着这篇：把 notes/polished 接进预览并跳到整理版', async () => {
    useStore.setState({ jobs: { e1: activeEpub() }, currentDocId: 'd1' })
    listJobs.mockResolvedValue([])
    getJob.mockResolvedValue(doneEpub())
    listTranscripts.mockResolvedValue([doc('d1', '测试书')])
    getMarkdown.mockImplementation(async (_id: string, mode: string) => ({
      id: mode, mode, style: '', version: 1, edited: false, versions: [],
      content: mode === 'notes' ? '# 导读' : '## 第1章',
    }))

    await useStore.getState().refreshJobs()
    await flush()

    const st = useStore.getState()
    expect(getMarkdown).toHaveBeenCalledWith('d1', 'notes')
    expect(getMarkdown).toHaveBeenCalledWith('d1', 'polished')
    expect(st.currentContent).toBe('# 导读')
    expect(st.currentPolishedContent).toBe('## 第1章')
    // 列表也要重拉：`modes` 是拉取那一刻的快照，产物落盘后不刷新的话切回来会以为没有
    expect(listTranscripts).toHaveBeenCalled()
    expect(st.activeView).toBe('polished')
  })

  it('有失败章时提示不是「全部成功」（口径跟着 result 走）', async () => {
    useStore.setState({ jobs: { e1: activeEpub() } })
    listJobs.mockResolvedValue([])
    getJob.mockResolvedValue(doneEpub({
      result: { doc_id: 'd1', title: '测试书', chapters_done: 2, chapters_failed: 1 },
    }))
    listTranscripts.mockResolvedValue([doc('d1', '测试书')])

    await useStore.getState().refreshJobs()
    await flush()

    expect(useStore.getState().toasts.some(t => t.message.includes('1 章失败'))).toBe(true)
  })
})
