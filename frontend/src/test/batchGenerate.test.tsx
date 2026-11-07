/**
 * `useBatchGenerate` —— 批量生成的串行队列（2026-11-07）。
 *
 * 钉五件在实现上容易退化的事：
 *
 * 1. **`open()` 零请求**。打开确认对话框只摆清单，用户按「开始生成」才发第一个请求。
 * 2. **成功后每一篇都刷新产物状态**。队列绕开 `useTaskRunner`，因为那里的成功回调带
 *    `isSame()` 判断（只在「跑完的这篇仍是你正在看的那篇」时执行）。把 `currentDocId`
 *    设成第一篇，于是「换回 `useTaskRunner`」会让第二篇变红 —— 正是这个缺陷在实机上的
 *    样子：生成完、切走、切回，那篇的笔记看起来凭空消失。
 * 3. **取消不是失败**。用户按取消时 `streamGenerate` 一个回调都不发（它的 `.catch` 里
 *    `AbortError` 直接 return），只靠 `onDone` / `onError` 结算会让队列永久卡在当前篇。
 * 4. **产物名按每篇的类型取**。`genPrefs.selectedCore` 是跨类型持久化的，Excel 的产物
 *    叫 `both`、合成文档只有 `notes`，直接拼 `selectedCore` 会把别的类型的选择送错。
 * 5. **串行**。并发跑会同时吃满云端并发额度。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

import type { DocEntry } from '../api/core'
import type { GenPrefs } from '../components/gen-panel/genPrefs'

const streamGenerateMock = vi.fn()
const getMarkdownMock = vi.fn()
const listEnabledModelsMock = vi.fn()

let prefs: Partial<GenPrefs>

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    streamGenerate: (...a: unknown[]) => streamGenerateMock(...a),
    getMarkdown: (...a: unknown[]) => getMarkdownMock(...a),
    listEnabledModels: (...a: unknown[]) => listEnabledModelsMock(...a),
    // 真实实现从 JSON 信封里取 message；测试里抛的只有 Error，取 message 等价。
    getErrorMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  }
})

// `registerAbort` 只把 controller 记进模块级表；测试里没有重试按钮要接管它。
vi.mock('../api/taskManager', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/taskManager')>()
  return { ...actual, registerAbort: () => {} }
})

// 偏好走 mock 而不是真的写 localStorage：这个队列的契约是「用当前偏好」，
// 偏好本身怎么持久化由 `genPrefs` 自己的测试负责。
// ⚠️ 打底用 `actual.loadGenPrefs()` 而不是手写一份默认值 —— `DEFAULTS` 没有导出，
// 抄一份就是第二份真相源，`selectedCore` 的默认值改了就再也不会被发现。
vi.mock('../components/gen-panel/genPrefs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components/gen-panel/genPrefs')>()
  return { ...actual, loadGenPrefs: () => ({ ...actual.loadGenPrefs(), ...prefs }) }
})

const { useBatchGenerate } = await import('../hooks/useBatchGenerate')
const { useStore } = await import('../store')

function doc(id: string, title: string, sourceType = 'video'): DocEntry {
  return {
    id, title, sourceType,
    sourceFileName: '', sourceUrl: '', coverUrl: '', engine: '', language: '',
    contentHash: '', tags: [], createdAt: '', folderId: '',
    transcribeState: 'done',
    // `modes: []` = 确定没有产物。**不能省成 `undefined`**：那表示「不知道有没有」，
    // 而 `markArtifact` 对未知状态刻意不改写（见 docsSlice），断言就会一直是 undefined。
    modes: [],
  } as DocEntry
}

const A = doc('doc-a', '第一篇')
const B = doc('doc-b', '第二篇')

// 有一条用例会把 `setProcessing` 临时换成抛错的版本（模拟循环体里一处意料之外的
// 失败）。换掉的是 store 上的真实 action，不恢复会让后面的用例一起炸。
const realSetProcessing = useStore.getState().setProcessing

/** 每次生成都成功，并返回一个可在「下一条断言之前」等到的 Promise。 */
function streamOk(delay = 0) {
  return (_id: string, _options: unknown, cb: { onDone: () => void }) =>
    new Promise<void>((res) => {
      setTimeout(() => { cb.onDone(); res() }, delay)
    })
}

beforeEach(() => {
  vi.clearAllMocks()
  // 队列快照落在 localStorage，用例之间必须清干净：上一条留在盘上的记录
  // 会让「跑完清掉」那一条在前一条的残留上判出假绿。
  localStorage.clear()
  prefs = {}
  listEnabledModelsMock.mockResolvedValue([])
  getMarkdownMock.mockImplementation(async (_id: string, mode: string) => ({
    id: 'md', mode, version: 1, content: `正文 ${mode}`, style: 'plain', versions: [],
  }))
  useStore.setState({
    setProcessing: realSetProcessing,
    currentDocId: A.id, currentRawText: '', docs: [A, B], processingTasks: {},
  })
})

const targets = [
  { id: A.id, title: A.title, sourceType: A.sourceType, willRegenerate: false },
  { id: B.id, title: B.title, sourceType: B.sourceType, willRegenerate: true },
]

describe('useBatchGenerate', () => {
  it('open() 只摆清单，一个请求都不发', () => {
    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })

    expect(result.current.items?.map(i => i.status)).toEqual(['queued', 'queued'])
    expect(result.current.items?.[1].willRegenerate).toBe(true)
    expect(result.current.phase).toBe('confirm')
    expect(streamGenerateMock).not.toHaveBeenCalled()
  })

  it('队列快照：open() 落盘，跑完清掉', async () => {
    streamGenerateMock.mockImplementation(streamOk())

    const { result } = renderHook(() => useBatchGenerate())
    // 挂载那一刻 items 是 null —— 一个字节都不该写，否则上次的记录当场被抹掉。
    expect(localStorage.getItem('mbw_batchQueues')).toBeNull()

    act(() => { result.current.open(targets) })
    const saved = JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}')
    expect(saved.generate.items.map((i: { id: string }) => i.id)).toEqual([A.id, B.id])
    // `sourceType` 与 `willRegenerate` 必须一起落盘：恢复时靠它们决定产物名与按钮文案。
    expect(saved.generate.items[1]).toMatchObject({ sourceType: 'video', willRegenerate: true })

    await act(async () => { await result.current.confirm() })
    // 跑完必须清，否则下次打开文档库会提示恢复一个已经跑完的队列。
    const after = JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}')
    expect(after.generate).toBeUndefined()
  })

  it('成功后每一篇都刷新产物 —— 包括用户没在看的那篇', async () => {
    streamGenerateMock.mockImplementation(streamOk())

    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    await waitFor(() => {
      const { docs } = useStore.getState()
      expect(docs.find(d => d.id === A.id)?.modes).toEqual(['notes', 'polished'])
      // ⚠️ 这一行是第 2 条纪律的全部意义：`currentDocId` 是 A，B 的产物状态
      // 只能来自批量的那条路径（`useTaskRunner` 会在这里跳过）。
      expect(docs.find(d => d.id === B.id)?.modes).toEqual(['notes', 'polished'])
    })
    expect(result.current.phase).toBe('done')
  })

  it('串行：同一时刻只有一个请求在跑', async () => {
    let running = 0
    let peak = 0
    streamGenerateMock.mockImplementation(
      (_id: string, _options: unknown, cb: { onDone: () => void }) =>
        new Promise<void>((res) => {
          running++
          peak = Math.max(peak, running)
          setTimeout(() => { running--; cb.onDone(); res() }, 1)
        }),
    )

    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    expect(peak).toBe(1)
  })

  it('一篇失败不影响后面的，失败项带原因', async () => {
    streamGenerateMock
      .mockImplementationOnce((_id: string, _o: unknown, cb: { onError: (m: string) => void }) => {
        cb.onError('模型额度已用尽')
      })
      .mockImplementationOnce(streamOk())

    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    const items = result.current.items!
    expect(items[0]).toMatchObject({ status: 'failed', message: '模型额度已用尽' })
    expect(items[1].status).toBe('done')
    // 失败那篇的任务条目留着（`failTask` 刻意保留 5 秒，让用户能点重试）
    expect(useStore.getState().processingTasks[`${A.id}__generate`]?.status).toBe('failed')
  })

  it('running 阶段取消：当前篇与剩余篇都标成已取消，不计失败', async () => {
    // 永不结算 —— 只能靠 abort 结束，模拟一个真实的慢请求。
    streamGenerateMock.mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })
    let done: Promise<void>
    act(() => { done = result.current.confirm() })
    await waitFor(() => expect(streamGenerateMock).toHaveBeenCalledTimes(1))
    act(() => { result.current.cancel() })
    await act(async () => { await done })

    const items = result.current.items!
    expect(items[0]).toMatchObject({ status: 'skipped', message: '已取消' })
    expect(items[1]).toMatchObject({ status: 'skipped', message: '已取消' })
    // 取消不是失败：失败收集器里不该留下这两篇
    expect(useStore.getState().processingTasks[`${A.id}__generate`]).toBeUndefined()
  })

  it('产物名按每篇的类型取：Excel 下发 both', async () => {
    streamGenerateMock.mockImplementation(streamOk())
    const xlsx = doc('doc-x', '表格', 'xlsx')

    const { result } = renderHook(() => useBatchGenerate())
    act(() => {
      result.current.open([{ id: xlsx.id, title: xlsx.title, sourceType: 'xlsx', willRegenerate: false }])
    })
    await act(async () => { await result.current.confirm() })

    const options = streamGenerateMock.mock.calls[0][1] as { mode: string }
    // `DEFAULTS.selectedCore` 是 notes+polished，对 xlsx 两者都不合法 ⇒ 退回该类型
    // 唯一的产物名。直接拼 `selectedCore` 会下发 `notes,polished`。
    expect(options.mode).toBe('both')
  })

  it('一个产物都没勾时不开跑，只提示', async () => {
    prefs = { selectedCore: [] }

    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    expect(streamGenerateMock).not.toHaveBeenCalled()
    expect(result.current.phase).toBe('confirm')
    const toasts = useStore.getState().toasts
    expect(toasts[toasts.length - 1].message).toContain('没有勾选任何产物')
  })

  it('刷新产物失败不算这一篇生成失败，队列照常往下跑', async () => {
    streamGenerateMock.mockImplementation(streamOk())
    // 后端返回了意料之外的形状：`versions` 不是数组时下面那行展开会抛错。
    // 这比「桩掉 getMarkdown 让它 reject」更接近真实 —— 后者已经被
    // `refreshArtifacts` 内部的 try/catch 吞掉，走不到这里的判据。
    getMarkdownMock.mockImplementation(async (_id: string, mode: string) => ({
      id: 'md', mode, version: 1, content: `正文 ${mode}`, style: 'plain',
      versions: {} as unknown as never[],
    }))

    const { result } = renderHook(() => useBatchGenerate())
    act(() => { result.current.open(targets) })
    await act(async () => { await result.current.confirm() })

    const items = result.current.items!
    // ⚠️ 是 `done` 不是 `failed`：产物已经在盘上，报失败会把用户引去重跑一次完整生成
    // （要花钱），而笔记其实已经写好了。
    expect(items[0]).toMatchObject({ status: 'done' })
    expect(items[0].message).toContain('已生成，但刷新产物失败')
    // ⚠️ `markArtifact` 排在刷新预览之前，所以状态已经回填了 —— 用户切回这篇
    // 不会看到「没有产物」，只是正文可能还是旧的。
    expect(useStore.getState().docs.find(d => d.id === A.id)?.modes).toContain('notes')
    // 队列没有被这一篇带停
    expect(items[1]).toMatchObject({ status: 'done', message: '' })
    expect(result.current.phase).toBe('done')
    // 收尾照做：快照清掉
    expect(JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}').generate).toBeUndefined()
  })

  it('循环里出现意料之外的抛错时，收尾照样执行', async () => {
    streamGenerateMock.mockImplementation(streamOk())

    const { result } = renderHook(() => useBatchGenerate())
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
    expect(JSON.parse(localStorage.getItem('mbw_batchQueues') || '{}').generate).toBeUndefined()
  })
})
