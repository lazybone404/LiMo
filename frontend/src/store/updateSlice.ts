/**
 * 应用更新流程的状态 —— **一份状态、三个视图**（设置·通用的更新卡 / 任务面板 / 顶栏提示）。
 *
 * ## 为什么状态必须在这里，不能留在卡片里
 *
 * 安装包约 150MB，下载常要好几分钟，用户会离开设置页去看别的。此前这些状态是
 * `UpdateCard` 的局部 `useState`，卡片一卸载 `aliveRef` 就置 false，Rust 侧推来的
 * 每一条进度都被丢掉 —— 而下载与安装**照常推进**（命令跑在 Rust 侧），最后进程被
 * `exit(0)` 换成新版本。用户看到的是「我什么都没点，它自己关了」。
 *
 * 所以这里定的不是「把进度显示在别处」，而是**流程本身不再依赖某个组件是否挂载**。
 *
 * ## 阶段（照 P-ai 的拆法，2026-11-08）
 *
 * ```
 * idle → checking → { up-to-date | available }
 * available → downloading → ready → installing →（应用退出）
 *       任何一步 → failed
 * ```
 *
 * `ready` 单独成一阶是这个设计的关键：**下载安全，安装才退出应用**。合成一步时，
 * 用户没法「先下好、忙完再装」，安装失败还要重下 150 MB。拆开之后第一步只是下载，
 * 第二步（`applyUpdate`）才是不可撤销的那一下。
 *
 * ## 三条约定
 *
 * ① `checkNow()` / `startDownload()` / `applyUpdate()` 是仅有的三个动作入口。卡片、
 *    启动链、任务面板、顶栏都调它们，谁都不直接碰 `api/update` —— 第二处调用就会
 *    长出第二份对流程的假设。
 * ② **进行中不接受并发的第二件事**：下载 / 就绪 / 安装时不再重查，也不重复下载。
 *    并发检查会把进度状态冲掉，并发下载会同时下两份 150MB。
 * ③ **失败留在界面上**：失败最常发生在用户已经离开设置页的时候，所以它落成任务面板里
 *    一条可重试的条目，而不是一条三秒就消失的 toast。
 */
import type { StateCreator } from 'zustand'
import {
  applyUpdate as applyUpdateApi,
  checkForUpdate,
  downloadUpdate,
  getPreparedUpdate,
  type UpdateStatus,
} from '../api/update'
import {
  clearSkipped as clearSkippedPref,
  getUpdatePrefs,
  markChecked,
  skipVersion as skipVersionPref,
} from '../lib/update-prefs'
import type { Store } from './types'

export type UpdatePhase =
  | 'idle'        // 还没查过
  | 'checking'
  | 'up-to-date'
  | 'available'   // 查到新版本，等用户点下载
  | 'downloading'
  | 'ready'       // 包已下好、验过签，等人点重启安装
  | 'installing'
  | 'failed'

export interface UpdateFlowState {
  phase: UpdatePhase
  /** 当前安装的版本（`check_update` 带回来的）；failure / 检查前为 null */
  currentVersion: string | null
  /** 目标版本（available 之后各阶段都有） */
  version: string | null
  date: string | null
  notes: string | null
  downloaded: number
  total: number | null
  viaProxy: boolean
  /** 下载开始时刻（epoch ms），任务面板据此显示「已用」 */
  startedAt: number | null
  error: string | null
  /**
   * 用户点过「跳过此版本」的那个版本号（持久化在 `mbw_update.skippedVersion`）。
   *
   * 它进 store 而不是留在卡片里，是因为现在有**三个**视图要看它：更新卡（换成
   * 「取消跳过」）、顶栏提示（跳过的版本不该再催）、任务面板（与它无关）。留一份在卡片里，
   * 顶栏那份就只能去读 `localStorage` 且**订阅不到变化** —— 用户刚点了跳过，顶栏提示
   * 还在那儿挂着，直到下一次无关的重渲染。
   */
  skippedVersion: string
}

export const UPDATE_IDLE: UpdateFlowState = {
  phase: 'idle', currentVersion: null, version: null, date: null, notes: null,
  downloaded: 0, total: null, viaProxy: false, startedAt: null, error: null,
  skippedVersion: '',
}

/**
 * 任务面板该不该为更新留一行（也决定入口按钮的角标计数与顶栏提示）。
 *
 * 判据是「**已经开始、还在进行、或刚失败需要有人处理**」—— `available` 刻意不在内：
 * 那时用户还没决定下不下，它属于「有一件事可以做」，不是「正在跑什么」。把它也塞进
 * 任务面板，面板就从任务列表退化成通知中心。
 */
export function isUpdateBusy(u: UpdateFlowState): boolean {
  return isUpdateLocked(u) || u.phase === 'failed'
}

/**
 * 「正在进行中」的那三阶 —— 其间不接受重查、也不接受重复下载。
 *
 * 与 `isUpdateBusy` 的差别只有 `failed`：失败要留在面板上（busy），但失败之后必须
 * **允许**重查与重下（不算 locked）。两者混为一谈的后果是失败一次就再也点不动。
 */
export function isUpdateLocked(u: UpdateFlowState): boolean {
  return u.phase === 'downloading' || u.phase === 'ready' || u.phase === 'installing'
}

/** Tauri 的 `invoke` 失败时 reject 的是**字符串**（Rust 的 `Err(String)`），不是 `Error`。
 *  `getErrorMessage` 会截到 150 字，够不到「所有线路都失败了」后面那半句出路。 */
function errText(e: unknown): string {
  if (typeof e === 'string') return e
  if (e instanceof Error) return e.message
  return String(e)
}

export interface UpdateSlice {
  update: UpdateFlowState
  /**
   * 查一次。返回 `null` 表示「没查成」（失败，或正在进行中不该重查）。
   *
   * `silent` = 启动链的静默检查：失败**不改变界面状态**。没网、公司网络挡了 GitHub
   * 都是常态，为它留一条失败条目等于每次开机都在任务面板里报一条假警。
   * 手动检查（`silent=false`）才把失败摆出来。
   */
  checkNow: (silent?: boolean) => Promise<UpdateStatus | null>
  /** 下载安装包（含验签）。进行中再点会被忽略。成功后进入 `ready`。 */
  startDownload: () => Promise<void>
  /** 安装已下载的更新并重启。Windows 上进程会被安装器接管。 */
  applyUpdate: () => Promise<void>
  /** 跳过某个版本（写 localStorage + 改 store 状态，两件事一起做） */
  skipVersion: (version: string) => void
  unskipVersion: () => void
  /** 页面重新加载后把「已下载待安装」捡回来（字节还在 Rust 进程里）。 */
  recoverPrepared: () => Promise<void>
  /** 关掉任务面板里那条失败条目 */
  dismissUpdate: () => void
}

export const createUpdateSlice: StateCreator<Store, [], [], UpdateSlice> = (set, get) => ({
  // 跳过记录是持久化的，冷启动时先读一次 —— 否则「已跳过」的版本在第一次检查回来之前
  // 会短暂地显示成「可以下载」。
  update: { ...UPDATE_IDLE, skippedVersion: getUpdatePrefs().skippedVersion },

  checkNow: async (silent = false) => {
    const prev = get().update
    // `ready` 也挡住：这时有一个已下载的包在 Rust 侧等着，重查会把界面换成
    // 「已是最新 / 发现新版本」，而那个包还在 —— 两个状态对不上。
    if (isUpdateLocked(prev)) return null
    set({ update: { ...prev, phase: 'checking', error: null } })
    try {
      const st = await checkForUpdate()
      const skippedVersion = getUpdatePrefs().skippedVersion
      set({
        update: st.update
          ? {
              ...UPDATE_IDLE, skippedVersion, phase: 'available',
              currentVersion: st.currentVersion,
              version: st.update.version, date: st.update.date, notes: st.update.notes,
            }
          : { ...UPDATE_IDLE, skippedVersion, phase: 'up-to-date', currentVersion: st.currentVersion },
      })
      return st
    } catch (e) {
      const skippedVersion = getUpdatePrefs().skippedVersion
      set({
        update: silent
          // 静默失败：退回查之前的样子（上一次「可更新」的结论不该被一次没网抹掉）
          ? { ...prev, skippedVersion, error: null }
          : { ...prev, skippedVersion, phase: 'failed', error: errText(e) },
      })
      return null
    } finally {
      // 失败也记时间：不记的话，断网时每次启动都要把三条线路的超时跑满。
      markChecked()
    }
  },

  startDownload: async () => {
    const cur = get().update
    if (isUpdateLocked(cur)) return
    set({
      update: {
        ...cur, phase: 'downloading', downloaded: 0, total: null,
        viaProxy: false, startedAt: Date.now(), error: null,
      },
    })
    try {
      await downloadUpdate(p => {
        if (p.kind === 'progress') {
          set(s => ({
            update: { ...s.update, downloaded: p.downloaded, total: p.total, viaProxy: p.viaProxy },
          }))
        }
      })
      // 走到这里 = 包已下好并验过签。**不要**在这里装 —— 装是用户的下一步动作。
      set(s => ({ update: { ...s.update, phase: 'ready', total: s.update.total } }))
    } catch (e) {
      set(s => ({ update: { ...s.update, phase: 'failed', error: errText(e) } }))
    }
  },

  applyUpdate: async () => {
    const cur = get().update
    if (cur.phase === 'installing') return
    if (cur.phase !== 'ready') return
    set({ update: { ...cur, phase: 'installing', error: null } })
    try {
      await applyUpdateApi(p => {
        if (p.kind === 'installing') {
          set(s => ({ update: { ...s.update, phase: 'installing', version: p.version || s.update.version } }))
        }
      })
      // 正常情况下走不到这里：Windows 上进程被 `exit(0)` 换掉，其余平台 `restart()`。
      // **不要**在这里写「安装完成」—— 那行不会执行，而写着它会让后来人以为能等。
    } catch (e) {
      set(s => ({ update: { ...s.update, phase: 'failed', error: errText(e) } }))
    }
  },

  recoverPrepared: async () => {
    const cur = get().update
    // 只捡「什么都没在跑」的时候：正在下载 / 正准备装的状态比 Rust 侧那份更新。
    if (cur.phase !== 'idle' && cur.phase !== 'up-to-date' && cur.phase !== 'available') return
    let version: string | null = null
    try {
      version = await getPreparedUpdate()
    } catch {
      return
    }
    if (!version) return
    set(s => ({
      update: { ...s.update, phase: 'ready', version, downloaded: 0, total: null, error: null },
    }))
  },

  skipVersion: (version) => {
    if (!version) return
    skipVersionPref(version)
    set(s => ({ update: { ...s.update, skippedVersion: version } }))
  },

  unskipVersion: () => {
    clearSkippedPref()
    set(s => ({ update: { ...s.update, skippedVersion: '' } }))
  },

  dismissUpdate: () => set(s => ({
    update: {
      ...UPDATE_IDLE,
      currentVersion: s.update.currentVersion,
      skippedVersion: s.update.skippedVersion,
    },
  })),
})
