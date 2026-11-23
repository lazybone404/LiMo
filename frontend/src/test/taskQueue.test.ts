/**
 * 并发上限之外的排队（`lib/task-queue.ts`，2026-11-23）。
 *
 * 改之前的行为是**直接拒绝**：弹一条「同时最多 N 个任务，请等待完成后再试」然后什么都不发生。
 * 用户的要求是不要被拦下来，自动挂起，前面跑完自己开始。
 *
 * ⚠️ 这组用例守的是三件事：上限只数 running（失败条目不算）、排队保持提交顺序、
 * 前面结束之后有人把队列推起来。第三件最容易漏——队列建了但没有消费者，
 * 表现是「提示已排队，然后再也没动静」，而它不报错。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useStore } from '../store'
import { submitTask, pendingCount, pendingTitles, clearQueue, cancelQueued, MAX_CONCURRENT } from '../lib/task-queue'

/** 把一个「正在跑」的任务塞进 store，模拟占位 */
function occupy(kind: 'import' | 'generate', n: number) {
  const tasks: Record<string, { kind: string; status: string; title: string }> = {}
  for (let i = 0; i < n; i++) tasks[`t${i}__${kind}`] = { kind, status: 'running', title: `T${i}` }
  useStore.setState({ processingTasks: tasks as never })
}

/** 清空在跑的任务（模拟它们跑完），触发一次 store 变更让队列出队 */
function releaseAll() {
  useStore.setState({ processingTasks: {} as never })
}

beforeEach(() => { clearQueue(); useStore.setState({ processingTasks: {} as never }) })
afterEach(() => { vi.restoreAllMocks() })

describe('有空位时直接跑', () => {
  it('未到上限 → 立刻执行且不进队列', () => {
    const run = vi.fn()
    expect(submitTask('generate', 'A', run)).toBe(false)
    expect(run).toHaveBeenCalledTimes(1)
    expect(pendingCount('generate')).toBe(0)
  })
})

describe('到上限时排队', () => {
  it('满了 → 不执行，进队列并返回 true', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    const run = vi.fn()
    expect(submitTask('generate', '排队的那篇', run)).toBe(true)
    expect(run).not.toHaveBeenCalled()
    expect(pendingCount('generate')).toBe(1)
    expect(pendingTitles('generate')).toEqual(['排队的那篇'])
  })

  it('前面的任务跑完之后，排队的那一个自动开始', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    const run = vi.fn()
    submitTask('generate', 'B', run)
    expect(run).not.toHaveBeenCalled()

    // 对照组：不释放就永远不会跑；释放之后必须自动跑起来。
    // 少了「订阅 store 出队」这一环，这里会一直停在「没被调用」。
    releaseAll()
    expect(run).toHaveBeenCalledTimes(1)
    expect(pendingCount('generate')).toBe(0)
  })

  it('按提交顺序出队，且槽位没空出来就不会多跑', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    const order: string[] = []
    // 真正的处理器启动时会 setProcessing，槽位立刻被占住。这里照做，否则队列会以为
    // 「什么都没在跑」而一口气把三条全放出来 —— 那条断言测的就不是顺序了。
    const run = (name: string) => () => {
      order.push(name)
      const cur = useStore.getState().processingTasks as Record<string, unknown>
      useStore.setState({ processingTasks: { ...cur, [`${name}__generate`]: { kind: 'generate', status: 'running', title: name } } as never })
      useStore.setState({ processingTasks: { ...(useStore.getState().processingTasks as Record<string, unknown>) } as never })
    }
    submitTask('generate', '一', run('一'))
    submitTask('generate', '二', run('二'))
    submitTask('generate', '三', run('三'))
    expect(pendingTitles('generate')).toEqual(['一', '二', '三'])

    releaseAll()
    expect(order).toEqual(['一', '二'])
    expect(pendingTitles('generate')).toEqual(['三'])
  })

  it('导入与生成各算各的，互不占位', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    const run = vi.fn()
    expect(submitTask('import', '文档', run)).toBe(false)
    expect(run).toHaveBeenCalledTimes(1)
  })
})

describe('上限只数 running', () => {
  it('失败条目不算在跑（否则「两次都失败后重排」会被一条不存在的上限挡住）', () => {
    const tasks: Record<string, { kind: string; status: string; title: string }> = {}
    for (let i = 0; i < MAX_CONCURRENT.generate; i++) {
      tasks[`f${i}__generate`] = { kind: 'generate', status: 'failed', title: `F${i}` }
    }
    useStore.setState({ processingTasks: tasks as never })

    const run = vi.fn()
    expect(submitTask('generate', 'C', run)).toBe(false)
    expect(run).toHaveBeenCalledTimes(1)
  })
})

// ═══════════════════════════════════════════════════════════
// 排队在面板里可见（2026-11-24）
//
// 只弹一条 toast 时，任务面板里什么都没有 —— 用户收起面板再打开，
// 看不到任何「我提交过」的痕迹。排队条目进 store 之后，它与正在跑的任务
// 在同一个列表里，只是状态不同。
// ═══════════════════════════════════════════════════════════

describe('排队可见', () => {
  it('排队时 store 里有一条 status=queued 的条目', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    submitTask('generate', '看不见的那篇', vi.fn())

    const queued = Object.values(useStore.getState().processingTasks)
      .filter(t => t.status === 'queued') as { title: string }[]
    expect(queued.map(t => t.title)).toEqual(['看不见的那篇'])
  })

  it('⚠️ 排队条目不计入并发上限（否则队列自己把自己堵死）', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    submitTask('generate', 'A', vi.fn())
    submitTask('generate', 'B', vi.fn())

    // 上限只数 running：两条 queued 不占位，剩下的两条仍按顺序排着
    expect(pendingCount('generate')).toBe(2)
  })

  it('出队后不再有 queued 条目', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    const run = vi.fn()
    submitTask('generate', 'C', run)

    releaseAll()
    expect(run).toHaveBeenCalledTimes(1)
    const left = Object.values(useStore.getState().processingTasks)
      .filter(t => t.status === 'queued')
    expect(left).toEqual([])
  })

  it('⚠️ 取消排队 → 摘出队列，轮到它也不会跑', () => {
    occupy('generate', MAX_CONCURRENT.generate)
    const run = vi.fn()
    submitTask('generate', 'D', run)

    const key = Object.keys(useStore.getState().processingTasks)
      .find(k => k.startsWith('__queued__'))!
    expect(cancelQueued(key)).toBe(true)

    releaseAll()
    expect(run).not.toHaveBeenCalled(), '取消之后队列里不该还留着它'
    expect(pendingCount('generate')).toBe(0)
  })

  it('对照：cancelQueued 对不在队列里的 key 返回 false', () => {
    expect(cancelQueued('doc1__generate')).toBe(false)
  })
})
