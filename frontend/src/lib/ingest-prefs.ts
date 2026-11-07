import { useSyncExternalStore } from 'react'

/**
 * LiMo — 导入侧的**两档偏好**（2026-11-07，「导入与转录分开」第一步）。
 *
 * ## 它在管什么
 *
 * 导入一件事原本做三件事：下载/抓取 → 转录 → 落盘。拆开之后「转录」那一步可以
 * 留到用户点「开始转录」再做，而**要不要留**按材料的成本分两档：
 *
 * | 项 | 默认 | 管哪些 adapter | 为什么是这个默认 |
 * |---|---|---|---|
 * | `autoTranscribeMedia` | **`false`** | `video` | 要下载文件 + 跑 ASR，慢、可能云端计费；典型场景就是「先囤着」 |
 * | `autoTranscribeDoc` | **`true`** | `document` / `arxiv` / `webpage` | 抓取与解析都在本地跑，毫秒到秒级，拆开没什么收益 |
 *
 * ⚠️ **这一层只是「用户的偏好」，不是「哪个 adapter 归哪一档」**。分档规则住在
 * 后端 [`_resolve_probe_only`](../../../backend/app/routers/ingest.py)：前端拿到一个
 * URL 时**根本不知道它是 B 站视频还是博客文章**，只有 adapter 选出来才谈得上分档。
 * 前端只传两个布尔值，按哪个档由后端决定。别在前端写一份「url 里含 bilibili 就是视频」
 * 的判据 —— 那是第二份真相源，且它一定落后于后端的 adapter 注册表。
 *
 * ⚠️ **`image` / `text` 不参与**：它们没有「转录」这一步，两个参数对它们无效。
 * 后端 `_resolve_probe_only` 对档位表之外的 adapter 恒返回 `False`。
 *
 * ## 为什么是 localStorage 而不是 config.yaml
 *
 * 1. 它是**本机偏好**，与 `export-prefs` / `chat-prefs` / `marker-prefs` 同族；
 * 2. 走 config 要动 `DEFAULT_CONFIG` + `ConfigUpdate`（`extra="forbid"`，漏了直接 422）
 *    + GET 回显，为一个布尔开三个落点；
 * 3. 下一步的批量导入要在前端**按文件类型逐个**决定，偏好留在前端才不用再搬一次。
 *
 * ## 为什么需要订阅器
 *
 * 有**两个**入口会改它（设置页）而**三个**入口会读它（GenPanel 的三个导入调用点）。
 * 「各自 `useState(loadIngestPrefs())`」的失效形态很具体：设置页改完之后，**已经挂载**的
 * GenPanel 不会重新挂载，仍按旧值发请求 —— 用户以为自己开了「导入视频后自动转录」，
 * 实际导入的视频还是只收纳，界面上完全看不出来。
 *
 * 形态照抄 [`chat-prefs.ts`](./chat-prefs.ts)（`useSyncExternalStore` + 模块级订阅 +
 * 合并式保存），理由与那里逐字相同。
 */

export interface IngestPrefs {
  /** `true` = 导入视频后自动转录（默认 `false`，即只收纳） */
  autoTranscribeMedia: boolean
  /** `true` = 导入文档/网页后自动提取正文（默认 `true`） */
  autoTranscribeDoc: boolean
}

/** ⚠️ 改这里就是改所有新用户的行为；`README.md` 里写着「视频默认只收纳」，两处必须一致 */
export const INGEST_DEFAULTS: IngestPrefs = {
  autoTranscribeMedia: false,
  autoTranscribeDoc: true,
}

const INGEST_KEY = 'mbw_ingestPrefs'

/** 原始字符串 → 偏好。逐字段校验，坏值只退回**该项**默认（`null` = 没有存储） */
function parsePrefs(raw: string | null): IngestPrefs {
  if (!raw) return { ...INGEST_DEFAULTS }
  try {
    const v = JSON.parse(raw) as Partial<IngestPrefs>
    return {
      autoTranscribeMedia:
        typeof v.autoTranscribeMedia === 'boolean'
          ? v.autoTranscribeMedia
          : INGEST_DEFAULTS.autoTranscribeMedia,
      autoTranscribeDoc:
        typeof v.autoTranscribeDoc === 'boolean'
          ? v.autoTranscribeDoc
          : INGEST_DEFAULTS.autoTranscribeDoc,
    }
  } catch {
    return { ...INGEST_DEFAULTS } // 损坏 JSON → 全部用默认
  }
}

// ⚠️ **缓存以「磁盘上的原始字符串」为键**，不是一次性缓存：
//  · `useSyncExternalStore` 的 `getSnapshot` 必须返回**同一个引用**（每次 `parse`
//    出一个新对象 = 无限重渲染），所以要缓存；
//  · 但缓存不能无视磁盘变化 —— 测试 `localStorage.clear()` / 手改 / 另一标签页写入
//    都必须立刻读到新值。比对字符串同时满足两者。
//  · 哨兵用 `undefined`（不是 `null`）：首帧必须解析一次，而「没有存储」本身就是 `null`。
let _cachedRaw: string | null | undefined = undefined
let _cached: IngestPrefs = { ...INGEST_DEFAULTS }

/**
 * 读 localStorage + 逐字段校验（真相源；返回稳定引用，可作 `useSyncExternalStore` 的快照）。
 *
 * ⚠️ 发请求的那一刻也要调它（不要在渲染时缓存一份传下去）：请求要的是「此刻的值」。
 */
export function loadIngestPrefs(): IngestPrefs {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(INGEST_KEY)
  } catch {
    /* localStorage 不可用 → 默认 */
  }
  if (raw !== _cachedRaw) {
    _cachedRaw = raw
    _cached = parsePrefs(raw)
  }
  return _cached
}

const _listeners = new Set<() => void>()

function subscribeIngestPrefs(fn: () => void): () => void {
  _listeners.add(fn)
  return () => {
    _listeners.delete(fn)
  }
}

// ⚠️ **跨标签页同步**：`useSyncExternalStore` 不会自己再调 `getSnapshot` —— 没有订阅通知
// 就没有重渲染。`storage` 事件**只在别的标签页/窗口写入时触发**（本页写入不触发），
// 所以它与 `saveIngestPrefs` 末尾的手动通知不重叠。`e.key === null` 是 `localStorage.clear()`。
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== null && e.key !== INGEST_KEY) return
    for (const fn of _listeners) fn()
  })
}

/**
 * 订阅导入偏好的 hook —— 设置页与 GenPanel **看到同一份状态**的唯一办法。
 *
 * ⚠️ 设置页改完之后已挂载的 GenPanel 不会重挂载，各自 `useState` 就是「显示一档、
 * 实际按另一档跑」。不要退回那种写法。
 */
export function useIngestPrefs(): IngestPrefs {
  return useSyncExternalStore(subscribeIngestPrefs, loadIngestPrefs, loadIngestPrefs)
}

/**
 * 保存（**合并式**，参数是 `Partial`）。
 *
 * ⚠️ 合并放在函数内部而不是让调用方展开：忘了展开的后果是**另一个开关被静默重置**
 * （打开「自动转录视频」时把「文档一键」也关回去），既没报错也没提示。
 */
export function saveIngestPrefs(p: Partial<IngestPrefs>): void {
  try {
    localStorage.setItem(INGEST_KEY, JSON.stringify({ ...loadIngestPrefs(), ...p }))
  } catch {
    /* quota */
  }
  for (const fn of _listeners) fn()
}
