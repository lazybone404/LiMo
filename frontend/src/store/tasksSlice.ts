/**
 * 任务 slice — 导入进度 + 图片处理结果 + 处理任务追踪 + 任务面板。
 */
import type { StateCreator } from 'zustand'
import type { Store, ImageResult, TaskInfo } from './types'

export interface TasksSlice {
  // ── 图片处理结果: docId → mode → { text, engine, id } ──
  imageResults: Record<string, Record<string, ImageResult>>
  setImageResult: (docId: string, mode: string, result: ImageResult) => void
  loadImageResults: (docId: string, results: { mode: string; text: string; engine: string; id: string }[]) => void

  // ── 处理任务追踪 ──
  processingTasks: Record<string, TaskInfo>
  /** taskKey = `${docId}__${task.kind}` 或裸 docId（导入） */
  setProcessing: (taskKey: string, task: TaskInfo | null) => void
  /** 将任务标记为失败并保留 error 信息 */
  failTask: (taskKey: string, error: string) => void
  clearDocTasks: (docId: string) => void
  cancelOneTask: (taskKey: string) => void

  // ── 任务面板 ──
  taskPanelOpen: boolean
  setTaskPanelOpen: (v: boolean) => void
}

export const createTasksSlice: StateCreator<Store, [], [], TasksSlice> = (set) => ({
  imageResults: {},
  setImageResult: (docId, mode, result) => set(s => ({
    imageResults: {
      ...s.imageResults,
      [docId]: { ...(s.imageResults[docId] || {}), [mode]: result },
    },
  })),
  loadImageResults: (docId, results) => set(s => {
    const byMode: Record<string, ImageResult> = {}
    for (const r of results) {
      byMode[r.mode] = { text: r.text, engine: r.engine, id: r.id }
    }
    return { imageResults: { ...s.imageResults, [docId]: byMode } }
  }),

  processingTasks: {},
  setProcessing: (taskKey, task) => set(s => {
    const tasks = { ...s.processingTasks }
    if (task) {
      // `startedAt` 是**唯一**做继承的字段（其余仍是全量替换）：进度事件每秒来好几条，
      // 每次都取 `Date.now()` 会让「已用时」永远停在 0 秒。
      // ⚠️ 继承判据必须是「上一条任务是 running」，**不能**只看「这个 key 有任务」：
      // `failTask` 保留条目 5 秒且不清 `startedAt`，而「重试」最可能的用法正是失败后
      // 5 秒内点 —— 只看「有任务」会让重试的计时从**上一次任务**的开头起算。
      const prev = s.processingTasks[taskKey]
      tasks[taskKey] = {
        status: 'running', ...task, title: task.title || '',
        startedAt: task.startedAt ?? (prev?.status === 'running' ? prev.startedAt : Date.now()),
      }
    }
    else {
      Object.keys(tasks).forEach(k => {
        if (k.startsWith(taskKey + '__') || k === taskKey) delete tasks[k]
      })
    }
    return { processingTasks: tasks }
  }),
  /** 将任务标记为失败，5 秒后自动清除 */
  failTask: (taskKey, error) => set(s => {
    const tasks = { ...s.processingTasks }
    if (tasks[taskKey]) {
      tasks[taskKey] = { ...tasks[taskKey], status: 'failed', error }
    }
    // 5 秒后自动清除
    setTimeout(() => {
      set(s2 => {
        const t = { ...s2.processingTasks }
        if (t[taskKey]?.status === 'failed') delete t[taskKey]
        return { processingTasks: t }
      })
    }, 5000)
    return { processingTasks: tasks }
  }),
  clearDocTasks: (docId) => set(s => {
    const tasks = { ...s.processingTasks }
    const prefix = docId + '__'
    Object.keys(tasks).forEach(k => {
      if (k === docId || k.startsWith(prefix)) delete tasks[k]
    })
    return { processingTasks: tasks }
  }),
  /** 取消单个任务（精确 key 匹配，用于 TaskPanel 逐任务取消） */
  cancelOneTask: (taskKey) => set(s => {
    const tasks = { ...s.processingTasks }
    delete tasks[taskKey]
    return { processingTasks: tasks }
  }),

  taskPanelOpen: false,
  setTaskPanelOpen: (v) => set({ taskPanelOpen: v }),
})
