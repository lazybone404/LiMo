/**
 * 索引健康状态 —— 一次探测 + 事件驱动刷新，**刻意不轮询**。
 *
 * **为什么需要它**：设置页那张「检索索引」卡只有用户主动走进去才看得见；
 * 而索引坏掉时，用户最先遇到的是一句「没搜到」—— 那时候他已经开始怀疑自己的库了。
 * 这条状态让「索引不可用」提前到**打开文档库那一刻**。
 *
 * **为什么不做 10s 轮询**（对照 [lib/health.ts](./health.ts) 的后端探活）：
 * 后端进程会掉；索引不会自己坏掉又自己好 —— 它的可用性只在三个时刻变化：
 * ① 进程启动时的那次重建（前端此时刚挂载，取一次就是最新的）、② 用户手动重建、
 * ③ `search.db` 被外部删掉。轮询的收益接近零，代价是常驻一个定时器。
 * 所以：挂载时取一次（TTL 去重）+ 搜索失败时强制刷新 + 重建后由调用方写回。
 *
 * ⚠️ **取不到状态 ≠ 索引坏了**：请求失败时**保持上一次快照**（初值为 `null`），
 * 调用方据此不渲染任何东西。反过来写（失败即视为不可用）会在后端没起的几十秒里
 * 挂出一条「索引不可用，去重建」——而用户此刻根本点不动那个按钮。
 */
import { useEffect, useSyncExternalStore } from 'react'
import { getSearchStatus, type SearchStatus } from '../api/search'

/** 缓存有效期：多个组件同时挂载时不重复打请求。 */
const TTL_MS = 30_000

let snapshot: SearchStatus | null = null
let fetchedAt = 0
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

/**
 * 订阅索引健康状态。
 *
 * 挂载时懒触发一次探测（`TTL_MS` 内的重复挂载共用一个结果）——
 * 因此**不需要在 App 启动链里加一行**：状态唯一的消费方就在文档库里。
 */
export function useSearchHealth(): SearchStatus | null {
  const s = useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
  useEffect(() => { void refreshSearchHealth() }, [])
  return s
}

/**
 * 拉一次状态。
 * @param force 跳过 TTL 缓存（搜索刚失败 / 刚重建完，这两种情况下缓存一定是旧的）。
 */
export function refreshSearchHealth(force = false): Promise<void> {
  if (!force && snapshot && Date.now() - fetchedAt < TTL_MS) return Promise.resolve()
  if (inflight) return inflight
  inflight = getSearchStatus()
    .then(s => {
      snapshot = s
      fetchedAt = Date.now()
      emit()
    })
    .catch(() => {
      // 见文件头：失败就维持现状，不把「问不到」渲染成「坏了」。
      // 真正需要让用户知道的失败（搜索请求本身 503）由列表底部那条提示承担。
    })
    .finally(() => { inflight = null })
  return inflight
}

/**
 * 直接写入状态（手动重建成功后由调用方带返回值调）。
 *
 * 有它才能让「重建完成」这件事**立刻**反映到横幅上 —— 否则要等下一次
 * `refreshSearchHealth(true)`，而那条路径可能根本不会发生（用户不搜也不再打开设置）。
 */
export function setSearchHealth(s: SearchStatus) {
  snapshot = s
  fetchedAt = Date.now()
  emit()
}

/**
 * 仅供测试：把模块状态清回「从未取过」（= 冷启动）。
 *
 * 需要它是因为状态住**模块级变量**里（不是 store 也不是 context），
 * 用例之间会互相污染；而「snapshot 为 null 时不渲染」这条判据只有从冷启动
 * 才测得到。生产代码不得调用。
 */
export function __resetSearchHealthForTest() {
  snapshot = null
  fetchedAt = 0
  inflight = null
}
