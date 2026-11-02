/**
 * 「最近打开」—— 纯 localStorage 的便利入口，**不是**文档库的第二份真相。
 *
 * 只记 `{id, title, at}` 三元组：
 * - `title` 也存一份，因为文档可能**已被删除**（此时 id 在 `docs` 里查不到，但用户
 *   仍想知道「我昨天看的是哪篇」）。渲染时若 `docs` 里有这篇就现取标题覆盖，
 *   所以重命名自动生效 —— 这里是**快照兜底**，不是权威。
 * - `at` 只用于排序（前插即最新，存它是为了将来换「按时间分组」时不用改格式）。
 *
 * ⚠️ `selectDoc` 是高频调用点（每次切文档一次同步写）。12 条的小 JSON，可忽略。
 * ⚠️ 所有读取都对坏值容错：localStorage 可能被手改、被别的版本写坏、
 * 或者是 `"null"` 这种合法但不是数组的东西 —— 抛错会让**打开文档**这个动作失败，
 * 代价远大于丢掉一个便利列表。
 */

export const RECENT_DOCS_KEY = 'mbw_recentDocs'
/** 上限。列表是浮层，超过 12 条要滚动，「最近」的意义也就没了 */
export const RECENT_DOCS_MAX = 12

export interface RecentDoc {
  id: string
  /** 快照标题；文档还在时 UI 用 `docs` 里的现值覆盖它 */
  title: string
  /** epoch ms，前插即最新，但保留以便将来分组 */
  at: number
}

export function loadRecentDocs(): RecentDoc[] {
  try {
    const raw = localStorage.getItem(RECENT_DOCS_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const out: RecentDoc[] = []
    for (const it of parsed) {
      if (!it || typeof it !== 'object') continue
      const r = it as Record<string, unknown>
      // id 是必需项，其余给默认值 —— 一条残缺记录不该让整张表作废
      if (typeof r.id !== 'string' || !r.id) continue
      out.push({
        id: r.id,
        title: typeof r.title === 'string' ? r.title : '',
        at: typeof r.at === 'number' ? r.at : 0,
      })
    }
    return out.slice(0, RECENT_DOCS_MAX)
  } catch {
    return []
  }
}

/**
 * 把一篇文档推到最前，返回新列表（同时也写盘）。
 *
 * @param title 空串表示「调用方查不到标题」—— 此时**保留旧条目的标题**。
 *   传空串的原因见 `docsSlice::selectDoc`：它只收 id，标题得在 store 里现查，
 *   而启动恢复那一刻 `docs` 可能还没拉回来。
 */
export function pushRecentDoc(id: string, title: string): RecentDoc[] {
  if (!id) return loadRecentDocs()
  const prev = loadRecentDocs()
  const old = prev.find(r => r.id === id)
  const next: RecentDoc[] = [
    { id, title: title || old?.title || '', at: Date.now() },
    ...prev.filter(r => r.id !== id),
  ].slice(0, RECENT_DOCS_MAX)
  try {
    localStorage.setItem(RECENT_DOCS_KEY, JSON.stringify(next))
  } catch { /* quota exceeded —— 丢一个便利列表，不影响打开文档 */ }
  return next
}

/** 清空（目前无 UI 入口，留给将来的「清空最近」）。 */
export function clearRecentDocs() {
  try { localStorage.removeItem(RECENT_DOCS_KEY) } catch { /* ignore */ }
}
