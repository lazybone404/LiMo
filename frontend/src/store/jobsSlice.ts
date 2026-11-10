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
 *
 * ## 任务结束后谁把结果接回来
 *
 * 合成产出的是一篇**新文档**，而发起它的那次点击可能早就跟着页面一起没了（用户切走、
 * 刷新、折叠面板）。默认列表只带「进行中 + 未忽略的失败」，所以结束在前端只表现为
 * 「那行消失了」—— 于是 `refreshJobs` 每轮算一次差集，消失的逐条取回终态，
 * 由 `settleVanished` 按 kind 补偿（目前只有 `synthesis` 需要）。
 *
 * ⚠️ 别把这件事挪进 SSE 回调：SSE 只在「有人正在看」时存在，而这里要解决的正是
 * 「没人在看」。
 */
import type { StateCreator } from 'zustand'
import {
  cancelJob as cancelJobApi, dismissJob as dismissJobApi, getJob, listJobs, type JobInfo,
} from '../api/jobs'
import { listTranscripts } from '../api/transcripts'
import { getMarkdown } from '../api/client'
import { toMarkdownMeta } from '../lib/markdownMeta'
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

  /**
   * 一场「没人看着」的合成结束了：把它产出的文档接进列表。
   *
   * 判据是 **doc id 是否已在列表里**，不是「有没有收到过 SSE」：
   * - 流还活着时 `DocLibrary.onDone` 会先把它插进去 —— 这里什么都不做，连 toast
   *   都不发，否则同一件事提示两遍；
   * - 流早就断了（用户切走 / 刷新过页面）时列表里没有它 —— 这才是本条存在的场景。
   */
  const adoptFinishedSynthesis = async (job: JobInfo) => {
    const result = (job.result ?? {}) as { id?: unknown; title?: unknown }
    const id = typeof result.id === 'string' ? result.id : ''
    if (!id || get().docs.some(d => d.id === id)) return
    try {
      set({ docs: await listTranscripts() })
    } catch {
      // 拉不到列表就别报「完成」：用户点进去会找不到那篇。
      return
    }
    if (!get().docs.some(d => d.id === id)) return
    const title = typeof result.title === 'string' && result.title ? result.title : '对比综述'
    get().addToast(`合成完成：${title}`, 'success')
  }

  /**
   * 一本「没人看着」的电子书解读完了：把产物接回界面。
   *
   * 这段逻辑原先住在 `EpubInterpretPanel` 里（组件自己 2s 轮询到 `running=false`
   * 才执行），于是折叠侧栏就再也接不上了 —— 而解读照常在跑。现在它只依赖 `jobs`
   * 的变化，与面板在不在无关（模式 #73）。
   *
   * 两条路分开对待：
   * - **正开着这篇**：把 notes/polished 直接读进预览并跳到整理版（用户等的就是它）；
   * - **没开着**：只需要刷新文档列表 —— `modes` 是列表拉取那一刻的快照，不刷新的话
   *   切回这篇时预览区会因「没有产物」而不发请求，笔记看起来凭空消失。
   */
  const adoptFinishedEpub = async (job: JobInfo) => {
    const result = (job.result ?? {}) as {
      doc_id?: unknown; title?: unknown; chapters_failed?: unknown
    }
    const docId = typeof result.doc_id === 'string' ? result.doc_id : ''
    if (!docId) return
    const failed = typeof result.chapters_failed === 'number' ? result.chapters_failed : 0

    if (get().currentDocId === docId) {
      try {
        const [notes, polished] = await Promise.all([
          getMarkdown(docId, 'notes'), getMarkdown(docId, 'polished'),
        ])
        const st = get()
        if (notes) {
          st.setCurrentContent(notes.content)
          st.setMarkdownMeta(toMarkdownMeta(notes))
          const prev = st.markdownVersions
          st.setMarkdownVersions([...(notes.versions || []), ...prev.filter(v => v.mode !== 'notes')])
        }
        if (polished?.content) {
          st.setCurrentPolishedContent(polished.content)
          const prev = st.markdownVersions
          st.setMarkdownVersions([...(polished.versions || []), ...prev.filter(v => v.mode !== 'polished')])
        }
        // 解读完成 → 新产物无 edited → 标记自动清除（方案 5.3）
        st.setMarkdownEdited('notes', false)
        st.setMarkdownEdited('polished', false)
        // 产物已落盘 → 补记产物位。不补则「切走再切回」时 `modes` 还说「没有产物」，
        // 预览区一个请求都不发 —— 与 `DocsSlice.markArtifact` 的 docstring 同一条理由。
        if (notes) st.markArtifact(docId, 'notes')
        if (polished) st.markArtifact(docId, 'polished')
        st.setActiveView('polished')
      } catch {
        // 读不到产物就别报「完成」—— 用户点过去只会看到旧内容
        return
      }
    }

    try {
      set({ docs: await listTranscripts() })
    } catch {
      return
    }
    const title = typeof result.title === 'string' && result.title ? result.title : '电子书'
    if (failed > 0) get().addToast(`《${title}》解读完成，${failed} 章失败已跳过`, 'error')
    else get().addToast(`《${title}》解读完成`, 'success')
  }

  /**
   * 活跃任务从列表里消失之后要做的事。
   *
   * ⚠️ 消失**不等于**完成：默认列表只带「进行中 + 未忽略的失败」，所以完成、取消、
   * 被忽略三种都会让它消失。判据是随后单条取回的 `status`。
   */
  const settleVanished = async (gone: JobInfo[]) => {
    for (const old of gone) {
      let job: JobInfo
      try {
        job = await getJob(old.id)
      } catch {
        // 记录已被 `prune` 清掉（终态只留最近 200 条）。没有结果可接，不算失败。
        continue
      }
      // 失败 / 取消各有自己的出口：失败那行还留在面板上等人处理，取消是用户自己的动作。
      if (job.status !== 'done') continue
      if (job.kind === 'synthesis') await adoptFinishedSynthesis(job)
      else if (job.kind === 'epub.interpret') await adoptFinishedEpub(job)
    }
  }

  return {
    jobs: {},

    refreshJobs: async () => {
      try {
        const list = await listJobs()
        const prev = get().jobs
        const next: Record<string, JobInfo> = {}
        for (const job of list) next[job.id] = job
        set({ jobs: next })
        // 「刚才还在跑、这一轮不见了」= 它结束了 —— 整体替换把这件事直接暴露成差集，
        // 不需要另记一份「这条上次是什么状态」。
        const gone = Object.values(prev).filter(p => isJobActive(p) && !next[p.id])
        if (gone.length) void settleVanished(gone)
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
