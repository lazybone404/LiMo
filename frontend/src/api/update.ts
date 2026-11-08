/**
 * 应用内更新的前端入口 —— 薄薄一层，把两个 Tauri 命令包成普通 Promise。
 *
 * ## 为什么所有实现都在 Rust 侧
 *
 * 检查（线路轮转、超时）、下载（进度、验签）、安装（杀 sidecar、启动安装器、退出进程）
 * 全在 `src-tauri/src/updater.rs`。这里**一个字的业务逻辑都没有** —— 面板里每加一条
 * 判断，就会多一处「前端以为的更新流程」与「真实流程」分叉的机会。
 *
 * ## 判据：只在桌面版可用
 *
 * `isDesktop` 就是 `api/core.ts::isTauriEnv` —— 判据全仓只写一份。
 * ⚠️ 浏览器里跑 `npm run dev` 时这两个函数会**抛错**而不是假装成功 —— 更新装不上
 * 是比「提示一句不支持」严重得多的失败。卡片自己先用 `isDesktop()` 把按钮挡掉
 * （见 `UpdateCard.tsx`），正常不会走到抛错这一支。
 */

import { isTauriEnv } from './core'

/** 与 `updater.rs::UpdateInfo` 的序列化形状一致（`rename_all = "camelCase"`）。 */
export interface UpdateInfo {
  version: string
  /** RFC3339 字符串；清单里没写就是 null */
  date: string | null
  /** Release notes 全文（`release.py` 取的是 `docs/changelog/releases/v*.md`） */
  notes: string | null
}

export interface UpdateStatus {
  /** 当前安装的版本，由 Rust 侧 `package_info()` 给出 */
  currentVersion: string
  /** null = 已是最新 */
  update: UpdateInfo | null
}

/**
 * 进度事件。⚠️ 字段名以 Rust 侧为准 —— 那边 enum 上的 `rename_all` 只改变体名，
 * 字符串字段是**逐个**标注 camelCase 的（`viaProxy`），加字段时两边一起看。
 */
export type UpdateProgress =
  | { kind: 'progress'; downloaded: number; total: number | null; viaProxy: boolean }
  | { kind: 'installing'; version: string }

/** 只在 Tauri 里为真。实现在 `api/core.ts::isTauriEnv`（全仓唯一判据）。 */
export const isDesktop = isTauriEnv

/**
 * 官方发布页（**直连**）。
 * ⚠️ 不能拿下载用的中转前缀去拼网页地址 —— 中转只代理资源文件，打开会是空白页
 * 或者一段错误信息（P-ai v0.42.0 踩过）。仓库地址与 `updater.rs` 的
 * `CARGO_PKG_REPOSITORY` 是同一个，改仓库时两处一起看。
 */
export const RELEASES_URL = 'https://github.com/lazybone404/LiMo/releases'

/**
 * 查有没有新版本。**没有节流、没有缓存** —— 每次调用都真的发一次请求。
 * 节流只属于启动时的静默检查（`lib/update-prefs.ts`）：用户主动点「检查更新」
 * 时返回一个几分钟前的结论，会让人以为新版本还没发出去。
 *
 * 无更新时返回 `{ update: null }`；连不上任何线路时 reject（错误文案由 Rust 给出）。
 */
export async function checkForUpdate(): Promise<UpdateStatus> {
  if (!isDesktop()) throw new Error('仅桌面版支持检查更新')
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke<UpdateStatus>('check_update')
}

/**
 * 下载并安装。进度通过 `Channel` 逐条推过来（512 KiB 一条）。
 *
 * ⚠️ **Windows 上这个 Promise 永远不会 resolve**：安装器起来之后插件直接
 * `std::process::exit(0)`，进程在 Promise 结算之前就没了。所以调用方只能用
 * `.catch()` 处理失败，**不能**在 `.then()` 里写「安装完成」—— 那行代码不会执行，
 * 于是「装完了」的状态永远停在界面上。成功路径的可见信号是「应用重启了」。
 */
export async function installUpdate(
  onProgress: (p: UpdateProgress) => void,
): Promise<void> {
  if (!isDesktop()) throw new Error('仅桌面版支持安装更新')
  const { Channel, invoke } = await import('@tauri-apps/api/core')
  const channel = new Channel<UpdateProgress>()
  channel.onmessage = onProgress
  await invoke('install_update', { onEvent: channel })
}
