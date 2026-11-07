/**
 * 批量队列的落盘快照（2026-11-07）。
 *
 * 批量转录与批量生成都是串行队列，状态原本只活在 hook 的 `useState` 里 —— 刷新一次
 * 页面就什么都不剩，用户既不知道上次跑到哪一篇，也没有继续跑的入口。这个模块把
 * 队列**当前的样子**写进 localStorage，文档库挂载时读回来，提示「还有 N 篇没跑完」。
 *
 * ## 恢复的是「清单」，不是「进度」
 *
 * 读回来的只有未完成的那几项（`queued` / `running`），`running` 一律当作排队中 ——
 * 那个请求已经随着旧页面结束了。用户点「继续运行」看到的是**确认对话框**，
 * 按「开始」才真的发请求。这道确认不能省：一次批量生成是若干次计费调用。
 *
 * ## 批量导入不在其中
 *
 * 导入队列的条目持有 `File` 对象（`useBatchImport` 的 `candidatesRef`），刷新之后
 * 无法重建 —— 存进 IndexedDB 要连 2GB 的文件一起存，代价远大于收益。所以导入
 * 刷新后确实从头开始，这里不假装恢复了。
 *
 * ## 会话内的「正在跑」与盘上的快照是两件事
 *
 * 面板折叠会卸载 `DocLibrary`（`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`），
 * 但 `confirm()` 的 async 闭包还在继续发请求。此时若只看盘上快照，重新展开面板会
 * 得到一条「N 篇未完成」的横幅，点下去就是**同一批请求跑第二遍**。
 * 故加一组模块级标记：本页此刻在跑的 kind 不提供恢复项。刷新页面等于重开模块，
 * 标记自然清空，正是我们要的语义。
 */
import type { GenerateItem } from '../hooks/useBatchGenerate'
import type { TranscribeItem } from '../hooks/useBatchTranscribe'

/** 会落盘的两种队列。批量导入不在其中，理由见文件头。 */
export type QueueKind = 'transcribe' | 'generate'

/** 两种队列共用一个键：各自只是里面的一项，清掉一种不动另一种。 */
const KEY = 'mbw_batchQueues'

/** 落盘格式的版本。形状变了就把它加一，旧记录当作没有。 */
const VERSION = 1

/** 快照里每一项至少要有的字段。恢复只靠这几项重建 `open()` 的入参。 */
export interface QueuedItem {
  id: string
  title: string
  status: string
  message?: string
}

export interface QueuedGenItem extends QueuedItem {
  sourceType: string
  willRegenerate: boolean
}

interface Snapshot {
  v: number
  savedAt: string
  /**
   * `readonly` 是因为 {@link saveQueue} 收的就是只读数组，写进快照时不再拷一份。
   * 读回来只做 `filter` / `map`，两种都可读即可。
   */
  items: readonly unknown[]
}

type Book = Partial<Record<QueueKind, Snapshot>>

function storage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    // 隐私模式下一碰就抛。落盘失败只影响「刷新后能不能恢复」，不该让队列本身崩掉。
    return null
  }
}

function readBook(): Book {
  const ls = storage()
  if (!ls) return {}
  let raw: string | null
  try {
    raw = ls.getItem(KEY)
  } catch {
    return {}
  }
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Book) : {}
  } catch {
    return {}
  }
}

/** 写失败的两种可能（配额满、存储被禁用）都不该影响这一轮队列，故吞掉。 */
function writeBook(book: Book): void {
  const ls = storage()
  if (!ls) return
  try {
    ls.setItem(KEY, JSON.stringify(book))
  } catch {
    /* 存不下 = 刷新后没有恢复项，队列本身照跑 */
  }
}

/**
 * 覆盖这一种队列的快照。
 *
 * 存的是**全部项**而不是只存未完成的：快照要能回答「这一轮有几篇、跑到第几篇」，
 * 而筛选发生在读回来的时候（{@link restorableTranscribe} / {@link restorableGenerate}）。
 *
 * ⚠️ **泛型 `T` 不是为了灵活，是让调用方能把 `QueuedGenItem` 原样传进来** ——
 * 写死 `QueuedItem[]` 时，多出来的 `sourceType` / `willRegenerate` 两个字段会被
 * 对象字面量的多余属性检查挡在编译期之外。它们是 `restorableGenerate` 重建入参的
 * 必需字段，丢了就只能整条丢弃。
 */
export function saveQueue<T extends QueuedItem>(kind: QueueKind, items: readonly T[]): void {
  const book = readBook()
  book[kind] = { v: VERSION, savedAt: new Date().toISOString(), items }
  writeBook(book)
}

/** 清掉这一种队列的快照。另一种不受影响 —— 两个横幅各自「忽略」。 */
export function clearQueue(kind: QueueKind): void {
  const book = readBook()
  if (!(kind in book)) return
  delete book[kind]
  writeBook(book)
}

function isQueuedItem(v: unknown): v is QueuedItem {
  if (!v || typeof v !== 'object') return false
  const it = v as Record<string, unknown>
  // `title` 允许空串（标题可能真的取不到），但 `id` 是重建入参与去重的唯一依据，不能空。
  return typeof it.id === 'string' && it.id !== '' && typeof it.title === 'string'
}

function isQueuedGenItem(v: QueuedItem): v is QueuedGenItem {
  const it = v as unknown as Record<string, unknown>
  // `sourceType` 决定这一篇走哪个类型的产物名与格式清单，缺了就只能丢掉这一项：
  // 拿空串去 `getTypeMeta` 会得到一份默认值，请求照发、产物可能不是用户要的。
  return typeof it.sourceType === 'string' && it.sourceType !== ''
    && typeof it.willRegenerate === 'boolean'
}

function loadItems(kind: QueueKind): QueuedItem[] {
  const snap = readBook()[kind]
  if (!snap || snap.v !== VERSION || !Array.isArray(snap.items)) return []
  return snap.items.filter(isQueuedItem)
}

/**
 * 刷新后还可以继续跑的状态。
 *
 * `running` 算在内：那次请求随着旧页面一起结束了，这一项实际停在排队的位置。
 * `failed` / `skipped` / `done` 不算 —— 它们上一轮已经有了结论，用户当时也看见过。
 */
const UNFINISHED = new Set(['queued', 'running'])

export function unfinished<T extends QueuedItem>(items: readonly T[]): T[] {
  return items.filter(it => UNFINISHED.has(it.status))
}

// ── 会话级标记：本页此刻正在跑哪几种队列 ────────────────────────────────

const runningHere = new Set<QueueKind>()

export function markQueueRunning(kind: QueueKind): void {
  runningHere.add(kind)
}

export function markQueueIdle(kind: QueueKind): void {
  runningHere.delete(kind)
}

export function isQueueRunningHere(kind: QueueKind): boolean {
  return runningHere.has(kind)
}

/**
 * 读回「上次没跑完的转录清单」。
 *
 * 本页此刻正在跑这一种队列时返回空 —— 那些项不是「上次的残留」，是**正在跑的**，
 * 再提供一次恢复入口就是让它们跑第二遍。
 */
export function restorableTranscribe(): TranscribeItem[] {
  if (isQueueRunningHere('transcribe')) return []
  return unfinished(loadItems('transcribe')).map(it => ({
    id: it.id,
    title: it.title,
    status: (it.status === 'running' ? 'queued' : it.status) as TranscribeItem['status'],
    message: '',
  }))
}

/** 读回「上次没跑完的生成清单」。`willRegenerate` 决定对话框上写「生成」还是「重新生成」。 */
export function restorableGenerate(): GenerateItem[] {
  if (isQueueRunningHere('generate')) return []
  return unfinished(loadItems('generate'))
    .filter(isQueuedGenItem)
    .map(it => ({
      id: it.id,
      title: it.title,
      sourceType: it.sourceType,
      willRegenerate: it.willRegenerate,
      status: (it.status === 'running' ? 'queued' : it.status) as GenerateItem['status'],
      message: '',
    }))
}
