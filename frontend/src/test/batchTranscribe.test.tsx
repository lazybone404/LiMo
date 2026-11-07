/**
 * `useBatchTranscribe` —— 批量转录的串行队列（2026-11-07）。
 *
 * 这里钉三件在实现上容易退化的事：
 *
 * 1. **`open()` 零请求**。打开确认对话框只是把清单摆出来，用户按「开始转录」才发
 *    第一个请求。这条与批量导入同一条纪律：一个「点开就已经在花钱」的对话框，
 *    用户关掉它时钱已经花出去了。
 * 2. **成功后每一篇都回填 `docs`**。队列刻意绕开 `useTaskRunner`，因为那里的成功回调
 *    带 `isSame()` 判断（只在「跑完的这篇仍是你正在看的那篇」时执行）。第 1 组用例把
 *    `currentDocId` 设成第一篇，于是「换回 `useTaskRunner`」这个改动会让第 2 篇变红、
 *    第 1 篇照样绿 —— 正是这个缺陷在实机上的样子（用户看着 A，B 转录完了徽章还挂着）。
 * 3. **取消不是失败**。用户按取消时 `apiFetch` 抛 `AbortError`，不判它就会被
 *    `getErrorMessage` 报成一句看不懂的话，并把「取消」计进失败数。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

import type { DocEntry } from '../api/core'

const retranscribeMock = vi.fn()
const getTranscriptMock = vi.fn()

// ⚠️ 两个模块各 mock 一个函数：批量队列从 `api/transcripts` 拿 `retranscribe`，
// 而成功后调用的 `applyTranscriptionResult` 从 `api/client` 拿 `getTranscript`。
// 只 mock 其中一个，另一个会真的发请求 —— 而测试环境没有后端，失败信息是
// 「fetch is not defined」，与「逻辑写错了」长得完全一样。
vi.mock('../api/transcripts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/transcripts')>()
  return { ...actual, retranscribe: (...a: unknown[]) => retranscribeMock(...a) }
})

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, getTranscript: (...a: unknown[]) => getTranscriptMock(...a) }
})

const { useBatchTranscribe } = await import('../hooks/useBatchTranscribe')
const { useStore } = await import('../store')

function doc(id: string, title: string): DocEntry {
  return {
    id, title, sourceType: 'video',
    sourceFileName: '', sourceUrl: '', coverUrl: '', engine: '', language: '',
    contentHash: '', tags: [], createdAt: '', folderId: '',
    transcribeState: 'pending',
  } as DocEntry
}

const A = doc('doc-a', '第一篇')
const B = doc('doc-b', '第二篇')

// 有一条用例会把 `setProcessing` 临时换成抛错的版本（模拟循环体里一处意料之外的
// 失败）。换掉的是 store 上的真实 action，不恢复会让后面的用例一起炸。
const realSetProcessing = useStore.getState().setProcessing

beforeEach(() => {
  vi.clearAllMocks()
  // 队列快照落在 localStorage，用例之间必须清干净：上一条留在盘上的记录
  // 会让「跑完清掉」那一条在前一条（`open()` 后没跑完）的残留上判出假绿。
  localStorage.clear()
  useStore.setState({
    setProcessing: realSetProcessing,
    currentDocId: A.id, currentRawText: '', docs: [A, B], processingTasks: {},
  })
})

const targets = [{ id: A.id, title: A.title }, { id: B.id, title: B.title }]

describe('useBatchTranscribe', () => {
  it('open() 只摆清单，一个请求都不发', () => {
    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })

    expect(result.current.items?.map(i => i.status)).toEqual(['queued', 'queued'])
    expect(result.current.phase).toBe('confirm')
    expect(retranscribeMock).not.toHaveBeenCalled()
  })

  it('成功后每一篇都回填 docs —— 包括用户没在看的那篇', async () => {
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: '正文' })

    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    await waitFor(() => {
      const { docs } = useStore.getState()
      expect(docs.find(d => d.id === A.id)?.transcribeState).toBe('done')
      // ⚠️ 这一行是第 2 条纪律的全部意义：`currentDocId` 是 A，B 的回填
      // 只能来自批量的那条路径（`useTaskRunner` 会在这里跳过）。
      expect(docs.find(d => d.id === B.id)?.transcribeState).toBe('done')
    })
    expect(result.current.phase).toBe('done')
  })

  it('串行：同一时刻只有一个请求在跑', async () => {
    let running = 0
    let peak = 0
    retranscribeMock.mockImplementation(async () => {
      running++
      peak = Math.max(peak, running)
      await new Promise(r => setTimeout(r, 1))
      running--
      return { status: 'done', engine: 'bcut' }
    })
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    expect(peak).toBe(1)
  })

  it('一篇失败不影响后面的，失败项带原因', async () => {
    retranscribeMock
      .mockResolvedValueOnce({ error: '视频下载失败' })
      .mockResolvedValueOnce({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    const items = result.current.items!
    expect(items[0]).toMatchObject({ status: 'failed', message: '视频下载失败' })
    expect(items[1].status).toBe('done')
    // 失败那篇保持 pending —— 后端没写盘，用户还能再点一次
    expect(useStore.getState().docs.find(d => d.id === A.id)?.transcribeState).toBe('pending')
  })

  it('回填正文失败不算这一篇转录失败，队列照常往下跑', async () => {
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    // 只有第一篇回填失败 —— 第二篇照常，才看得出「队列没被带停」
    getTranscriptMock
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    const items = result.current.items!
    // ⚠️ 是 `done` 不是 `failed`：后端已经写盘了，报失败会把用户引去重跑一次完整转录
    // （几分钟 + 可能计费），而正文其实已经在那儿了。
    expect(items[0]).toMatchObject({ status: 'done' })
    expect(items[0].message).toContain('已转录，但刷新正文失败')
    // 队列没有被这一篇带停
    expect(items[1]).toMatchObject({ status: 'done', message: '' })
    expect(result.current.phase).toBe('done')
    // 第一篇确实没被回填（这正是 `message` 要说的那件事），第二篇照常回填
    expect(useStore.getState().docs.find(d => d.id === A.id)?.transcribeState).toBe('pending')
    expect(useStore.getState().docs.find(d => d.id === B.id)?.transcribeState).toBe('done')
    // 收尾照做：快照清掉
    expect(JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}').transcribe).toBeUndefined()
  })

  it('循环里出现意料之外的抛错时，收尾照样执行', async () => {
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })

    // 造一处循环体里没被单独兜住的失败。`setProcessing` 是任务表入口，它坏了这一批
    // 就没法继续跑 —— 但**收尾必须照做**。写成循环后面的普通语句时，这个用例会留下
    // `phase === 'running'`：对话框出不来结果、快照还写着「在跑」、恢复入口也不会
    // 再出现，而界面上没有任何报错。
    act(() => {
      useStore.setState({ setProcessing: () => { throw new Error('任务表不可用') } })
    })

    await act(async () => {
      await expect(result.current.confirm()).rejects.toThrow('任务表不可用')
    })

    expect(result.current.phase).toBe('done')
    // 没跑到的两篇标成「未执行」，而不是永远停在排队态 —— 对话框已经在显示结果了
    expect(result.current.items!.map(i => [i.status, i.message]))
      .toEqual([['skipped', '未执行'], ['skipped', '未执行']])
    expect(JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}').transcribe).toBeUndefined()
  })

  it('队列快照：open() 落盘，跑完清掉', async () => {
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useBatchTranscribe())
    // 挂载那一刻 items 是 null —— 一个字节都不该写，否则上次的记录当场被抹掉。
    expect(localStorage.getItem('mbw_batchQueues')).toBeNull()

    act(() => { result.current.open(targets) })
    const saved = JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}')
    expect(saved.transcribe.items.map((i: { id: string }) => i.id)).toEqual([A.id, B.id])

    await act(async () => { await result.current.confirm() })
    // 跑完必须清，否则下次打开文档库会提示恢复一个已经跑完的队列。
    expect(JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}').transcribe).toBeUndefined()
  })

  it('confirm 阶段按取消 = 关门，不算中断', () => {
    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })
    act(() => { result.current.cancel() })

    expect(result.current.items).toBeNull()
    expect(retranscribeMock).not.toHaveBeenCalled()
  })

  it('running 阶段取消：当前篇停下，剩余标成已取消而不是失败', async () => {
    // 第一篇永不 resolve，只能靠 abort 结束 —— 模拟一个真实的慢请求。
    retranscribeMock.mockImplementation(() => new Promise(() => {}))
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useBatchTranscribe())
    act(() => { result.current.open(targets) })
    let done: Promise<void>
    act(() => { done = result.current.confirm() })
    await waitFor(() => expect(retranscribeMock).toHaveBeenCalledTimes(1))
    act(() => { result.current.cancel() })
    await act(async () => { await done })

    const items = result.current.items!
    expect(items[0]).toMatchObject({ status: 'skipped', message: '已取消' })
    expect(items[1]).toMatchObject({ status: 'skipped', message: '已取消' })
    // 两篇都没转录成功，正文状态不动
    expect(useStore.getState().docs.every(d => d.transcribeState === 'pending')).toBe(true)
  })
})
