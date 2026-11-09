/**
 * 后台任务的**前端镜像** —— 真正的状态在后端 `jobs.db`，这里只是它的一份可渲染副本。
 *
 * ## 与 `processingTasks` 的分工
 *
 * - `processingTasks`：导入 / 转录 / 生成 / 图片处理。它们的生命周期**跟着文档**，
 *   跑在发起它们的 SSE 连接上，连接断了就该停 —— 所以状态住在发起它的那条链里是对的。
 * - `jobs`：插件依赖安装、合成这类**与某次页面交互无关**的长操作。它们由后端队列
 *   持有，页面卸载不该影响，组件只能「订阅」而不能「拥有」。
 *
 * ## 轮询是**有条件的**
 *
 * 只在存在 `queued` / `running` 的任务时才有下一轮。失败 / 完成的任务留在列表里等
 * 用户处理或忽略，但不再花请求去查它 —— 一个已经结束的任务不会有新状态。
 *
 * ## 为什么 `refreshJobs` 每轮整体替换 Map
 *
 * 增量合并要处理「后端删了 / 忽略了」这几种情况，而这些都是**以服务端为准**的事实。
 * 整体替换让「前端显示的任务」恒等于「后端那一秒的任务集合」，不会出现一条谁都没在
 * 跑的幽灵记录。
 */
import type { StateCreator } from 'zustand'
import { cancelJob as cancelJobApi, dismissJob as dismissJobApi, listJobs, type JobInfo } from '../api/jobs'
import type { Store } from './types'

/** 轮询间隔。任务进度的上报粒度是「一次 pip 步骤 / 一次 LLM 调用」，1.5 秒完全够。 */
const POLL_MS = 1500

const ACTIVE: JobInfo['status'][] = ['queued', 'running']

export function isJobActive(job: JobInfo): boolean {
  return ACTIVE.includes(job.status)
}

/** 模块级定时器：store 是单例，轮询也只能有一份。 */
let timer: ReturnType<typeof setTimeout> | null = null

export interface JobsSlice {
  /** 任务 id → 任务。只含「进行中 + 未忽略的失败」。 */
  jobs: Record<string, JobInfo>
  /** 从后端拉一次并安排下一轮（有活跃任务时）。 */
  refreshJobs: () => Promise<void>
  /**
   * 订阅一条刚提交的任务。提交接口已经带回了任务本体，直接放进来 ——
   * 等下一轮轮询再出现会有最多 1.5 秒的「点了没反应」。
   */
  trackJob: (job: JobInfo) => void
  cancelJobById: (id: string) => Promise<void>
  dismissJobById: (id: string) => Promise<void>
}

export const createJobsSlice: StateCreator<Store, [], [], JobsSlice> = (set, get) => {
  const scheduleNext = () => {
    if (timer !== null) return
    timer = setTimeout(() => {
      timer = null
      const hasActive = Object.values(get().jobs).some(isJobActive)
      if (hasActive) void get().refreshJobs()
    }, POLL_MS)
  }

  const stopPolling = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  return {
    jobs: {},

    refreshJobs: async () => {
      try {
        const list = await listJobs()
        const next: Record<string, JobInfo> = {}
        for (const job of list) next[job.id] = job
        set({ jobs: next })
        // ⚠️ 只有还有活的才排下一轮；否则一个「全是失败」的列表会永远每 1.5 秒
        // 打一次请求 —— 而它已经不会有任何变化了。
        if (list.some(isJobActive)) scheduleNext()
        else stopPolling()
      } catch {
        // 后端没起来 / 中途重启：静默，稍后再试。这里弹 toast 只会在启动瞬间刷屏，
        // 而任务本身没有丢 —— 它住在后端的 `jobs.db` 里。
        // ⚠️ **不能在这里 stopPolling**：如果本地还留着活跃任务，那它大概率还在
        // 跑，停掉轮询等于「一次网络抖动之后，那个安装再也不会出现在面板上」。
        if (Object.values(get().jobs).some(isJobActive)) scheduleNext()
        else stopPolling()
      }
    },

    trackJob: (job) => {
      set(s => ({ jobs: { ...s.jobs, [job.id]: job } }))
      if (isJobActive(job)) scheduleNext()
    },

    cancelJobById: async (id) => {
      try {
        const job = await cancelJobApi(id)
        set(s => ({ jobs: { ...s.jobs, [job.id]: job } }))
        scheduleNext()
      } catch (e) {
        get().addToast(`取消失败：${e instanceof Error ? e.message : String(e)}`, 'error')
      }
    },

    dismissJobById: async (id) => {
      try {
        await dismissJobApi(id)
      } catch {
        // 忽略失败（任务可能已经被清理）：本地的删除仍然要做，否则那条记录
        // 会留在界面上直到下次刷新 —— 「点了 × 没反应」比一次后台失败更糟。
      }
      set(s => {
        const next = { ...s.jobs }
        delete next[id]
        return { jobs: next }
      })
    },
  }
}
