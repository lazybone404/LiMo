/**
 * 更新的本机偏好：上次检查时间 / 已提醒过的版本 / 主动跳过的版本。
 *
 * ## 三个字段各自拦一件事
 *
 * - `lastCheckAt` —— **24 小时**的自动检查节流。启动时静默检查一次就够了，每次开
 *   应用都去 GitHub 拿一份 1 KB 的清单没有收益，而在网络不稳的环境里会变成每次启动
 *   多等一个超时。
 * - `lastNotifiedVersion` —— 同一个版本只弹一次「发现新版本」的提醒。少了它，用户在
 *   24 小时节流到期后会被同一个版本再提醒一次，提醒本身就成了噪音。
 * - `skippedVersion` —— 用户明确说了「这个版本我不想要」。它必须**跨会话**存活，
 *   否则重启一次提醒就回来了，而「跳过」这个动作的全部意义就是不再提。
 *
 * ## 节流只归自动检查，手动检查不吃
 *
 * `shouldAutoCheck()` 只被启动链调用。设置页的「检查更新」按钮直接走 `checkForUpdate()`，
 * 一次节流都不读 —— 手动点的人要的是「此刻」的真实结果。P-ai 第一版把节流做在返回值上，
 * 结果在冷却窗口里点「检查更新」看到的是旧结论（v0.19.0 修的就是这个）。
 *
 * 形态对齐 `export-prefs.ts`：`mbw_` 前缀、逐字段类型校验、读写失败一律吞掉
 * （localStorage 坏了不该让更新功能整个用不了，最坏退回「每次启动都检查一次」）。
 */

export interface UpdatePrefs {
  /** 上次完成检查的时刻（epoch ms）。0 = 从没查过 */
  lastCheckAt: number
  /** 已经弹过提醒的版本号，如 `0.5.23` */
  lastNotifiedVersion: string
  /** 用户点过「跳过此版本」的版本号 */
  skippedVersion: string
}

export const UPDATE_DEFAULTS: UpdatePrefs = {
  lastCheckAt: 0,
  lastNotifiedVersion: '',
  skippedVersion: '',
}

/** 自动检查的最短间隔。24 小时 = 「一天最多烦你一次」。 */
export const AUTO_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000

const UPDATE_KEY = 'mbw_update'

function readShaped(): UpdatePrefs {
  try {
    const raw = localStorage.getItem(UPDATE_KEY)
    if (!raw) return { ...UPDATE_DEFAULTS }
    const v = JSON.parse(raw) as Partial<UpdatePrefs>
    // 逐字段校验：某一项坏了只退回该项默认值，不整包丢弃 ——
    // 否则一个手改坏的数字会把「跳过这个版本」一起清掉。
    return {
      lastCheckAt: typeof v.lastCheckAt === 'number' && Number.isFinite(v.lastCheckAt)
        ? v.lastCheckAt
        : UPDATE_DEFAULTS.lastCheckAt,
      lastNotifiedVersion: typeof v.lastNotifiedVersion === 'string'
        ? v.lastNotifiedVersion
        : UPDATE_DEFAULTS.lastNotifiedVersion,
      skippedVersion: typeof v.skippedVersion === 'string'
        ? v.skippedVersion
        : UPDATE_DEFAULTS.skippedVersion,
    }
  } catch {
    return { ...UPDATE_DEFAULTS }
  }
}

export function getUpdatePrefs(): UpdatePrefs {
  return readShaped()
}

/** 合并写：调用方只给要改的那几项。返回写完之后的值，省得调用方再读一次。 */
export function saveUpdatePrefs(patch: Partial<UpdatePrefs>): UpdatePrefs {
  const next = { ...readShaped(), ...patch }
  try {
    localStorage.setItem(UPDATE_KEY, JSON.stringify(next))
  } catch { /* quota / 私密模式 —— 最坏退化成「每次启动都检查」 */ }
  return next
}

/**
 * 该不该做启动时的静默检查。
 *
 * ⚠️ **检查失败也记时间**（见 `markChecked`）：不记的话，断网环境下每次启动都会
 * 完整跑完三条线路的超时，启动链被拖住而用户什么也没得到。
 */
export function shouldAutoCheck(now: number = Date.now()): boolean {
  return now - getUpdatePrefs().lastCheckAt >= AUTO_CHECK_INTERVAL_MS
}

/** 记一次「检查已完成」。成功与失败都调 —— 理由见 `shouldAutoCheck`。 */
export function markChecked(at: number = Date.now()): void {
  saveUpdatePrefs({ lastCheckAt: at })
}

/** 记一次「这个版本已经提醒过了」，同一版本不再弹。 */
export function markNotified(version: string): void {
  saveUpdatePrefs({ lastNotifiedVersion: version })
}

/** 跳过这个版本：既不再弹提醒，卡片里也显示成「已跳过」。 */
export function skipVersion(version: string): void {
  saveUpdatePrefs({ skippedVersion: version })
}

/** 清掉跳过记录（卡片上的「重新检查」按钮）。 */
export function clearSkipped(): void {
  saveUpdatePrefs({ skippedVersion: '' })
}
