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
  title: string
  run: () => void
}

const queues: Record<QueueKind, Job[]> = { import: [], generate: [] }
let subscribed = false

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

/** 只给测试用：清空队列，避免用例之间互相污染。 */
export function clearQueue(kind?: QueueKind): void {
  if (kind) queues[kind] = []
  else { queues.import = []; queues.generate = [] }
}

function drain(kind: QueueKind): void {
  while (queues[kind].length > 0 && runningCount(kind) < MAX_CONCURRENT[kind]) {
    const job = queues[kind].shift()!
    try {
      job.run()
    } catch {
      // 启动失败不该拖住后面的任务：这一条已经出了队，下一个会继续被调度。
      // 真正的失败由处理器自己的 onError 上报（与直接提交那条路同源）。
      continue
    }
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
  queues[kind].push({ title, run })
  return true
}
