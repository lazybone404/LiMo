/**
 * 并发上限之外的**排队**（2026-11-23）。
 *
 * 此前到上限是直接拒绝：弹一条「同时最多 N 个任务，请等待完成后再试」，然后什么都不发生。
 * 用户的诉求是不要被拦下来，而是自动挂起，等前面跑完自己开始。
 *
 * ## 为什么队列住在模块里，不放在组件里
 *
 * `GenPanel` 会随左侧面板收起而卸载（`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`），
 * 队列若住在 `useState` 里，收起面板就等于把排队中的任务扔了 —— 而它们还没开始跑，
 * 用户会以为「点了却没反应」。放模块级 + 直接订阅 store，卸载不影响调度。
 *
 * ## 为什么存闭包而不是参数
 *
 * 提交那一刻，处理器已经把「哪篇文档、哪些选项」都读进了局部变量。闭包捕获取值时的
 * 快照，出队时用它启动，就不会被「用户中途切了文档」影响。存参数再重新读一遍状态，
 * 恰好会犯这个错，而症状是「排队的任务跑到了别的文档上」。
 *
 * ## 上限只数 running
 *
 * 与原先的判据一致：`failTask` 的条目会保留 5 秒，把它算进「在跑」会让「两次都失败、
 * 立刻重排第三个」被一条不存在的上限挡住。
 */

import { useStore } from '../store'

export type QueueKind = 'import' | 'generate'

/** 同时进行的上限。导入与生成各自独立计数。 */
export const MAX_CONCURRENT: Record<QueueKind, number> = {
  import: 3,
  generate: 2,
}

interface Job {
  /** 排队条目的 store key。与处理器自己的 taskKey 不同 —— 那个要等它开跑才知道。 */
  key: string
  title: string
  run: () => void
}

const queues: Record<QueueKind, Job[]> = { import: [], generate: [] }
let subscribed = false
let seq = 0

function runningCount(kind: QueueKind): number {
  const tasks = useStore.getState().processingTasks as Record<string, { kind?: string; status?: string }>
  return Object.values(tasks).filter(t => t.kind === kind && t.status === 'running').length
}

/** 已经排在这个任务前面的数量（不含它自己）。 */
export function pendingCount(kind: QueueKind): number {
  return queues[kind].length
}

/** 队列里排着的标题，给界面展示「还有哪几个在等」。 */
export function pendingTitles(kind: QueueKind): string[] {
  return queues[kind].map(j => j.title)
}

/** 只给测试用：清空队列，避免用例之间互相污染（连同 store 里的排队条目）。 */
export function clearQueue(kind?: QueueKind): void {
  const kinds: QueueKind[] = kind ? [kind] : ['import', 'generate']
  for (const k of kinds) {
    for (const j of queues[k]) useStore.getState().setProcessing(j.key, null)
    queues[k] = []
  }
}

/**
 * 取消一条**还在排队**的任务：从队列里摘掉，返回是否摘到了。
 *
 * ⚠️ 不能走 `cancelTask`（后端）：那时还没有任务可取消，调它只会返回一个「查无此
 * 任务」。也不能只删面板那一行 —— 队列里还留着，轮到它照样会开跑，用户看到的
 * 是「我取消了，它自己又开始了」。
 */
export function cancelQueued(taskKey: string): boolean {
  for (const k of ['import', 'generate'] as QueueKind[]) {
    const i = queues[k].findIndex(j => j.key === taskKey)
    if (i >= 0) {
      queues[k].splice(i, 1)
      return true
    }
  }
  return false
}

//: 防重入：出队过程中要改 store（删排队行、处理器写 running），而每次改 store
//: 都会触发 `drainAll`。不挡住的话队列会被**嵌套**消费 —— 实测表现是三条排队任务
//: 只跑了一条，另外两条在嵌套的 drain 里被 shift 掉却没执行（顺序也乱）。
let draining = false

function drain(kind: QueueKind): void {
  if (draining) return
  draining = true
  try {
    while (queues[kind].length > 0 && runningCount(kind) < MAX_CONCURRENT[kind]) {
      const job = queues[kind].shift()!
      // 排队那一行让位给处理器自己写的那条（它有真实的 taskKey，也带进度）。
      useStore.getState().setProcessing(job.key, null)
      try {
        job.run()
      } catch {
        // 启动失败不该拖住后面的任务：这一条已经出了队，下一个会继续被调度。
        // 真正的失败由处理器自己的 onError 上报（与直接提交那条路同源）。
        continue
      }
    }
  } finally {
    draining = false
  }
}

function drainAll(): void {
  drain('import')
  drain('generate')
}

function ensureSubscribed(): void {
  if (subscribed) return
  subscribed = true
  // 任何一次 store 变更都试一次出队：任务开始 / 结束 / 失败都会走这里。
  // drain 自己会再判一次 runningCount，所以多余的调用不会多启动任务。
  useStore.subscribe(drainAll)
}

/**
 * 提交一个任务：有空位就直接跑，没有就排队。
 *
 * 返回 `true` 表示进了队列（调用方据此提示「已排队」），`false` 表示已经开始跑。
 */
export function submitTask(kind: QueueKind, title: string, run: () => void): boolean {
  ensureSubscribed()
  if (runningCount(kind) < MAX_CONCURRENT[kind] && queues[kind].length === 0) {
    run()
    return false
  }
  const key = `__queued__${kind}__${++seq}`
  queues[kind].push({ key, title, run })
  // ⚠️ 排队条目要**立刻进 store**：只弹一条 toast 时，面板里什么都看不到，
  // 用户以为「点了没反应」（2026-11-23 反馈）。
  useStore.getState().queueTask(key, kind, title)
  return true
}
