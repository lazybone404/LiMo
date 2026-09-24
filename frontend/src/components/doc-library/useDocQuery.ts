/**
 * 文档库查询层 — 纯函数（filterDocs / sortDocs / buildResultSet）单独导出以便表驱动单测，
 * hook 只负责把它们接到 React 状态上。
 *
 * 为什么要「内存过滤 + 后端 FTS」混用，而不是二选一：
 * - `docs` 数组里**没有正文**（后端 index entry 是白名单序列化器），所以正文搜索
 *   在客户端**不可能**实现 —— FTS 不是优化，是唯一可行解。
 * - 但标题/类型/语言的匹配是 0ms、无网络，是「边打边筛」的体验来源，不能撤。
 * - 故：内存过滤结果**立刻**渲染，停手 250ms 后再打后端，把多出来的命中并进同一结果集。
 */
import { useEffect, useMemo, useState } from 'react'
import { searchDocs, type SearchHit } from '../../api/search'
import type { DocEntry } from '../../store/types'
import { SRC_LABEL } from './docMeta'

export type SortKey = 'date' | 'title' | 'type' | 'relevance'

export const SORT_OPTIONS: { key: SortKey; label: string; requiresRank?: boolean }[] = [
  { key: 'date', label: '时间' },
  { key: 'title', label: '标题' },
  { key: 'type', label: '类型' },
  // ⚠️ 「相关度」是**有条件出现**的（`requiresRank`）：没有 FTS 命中时它是个死键 ——
  // 所有文档的 rank 都是 MAX，排序结果退化成「保持原序」，用户点了看不到任何变化。
  // 展示与否交给 ListToolbar（读 `rankOf.size > 0`）。
  { key: 'relevance', label: '相关度', requiresRank: true },
]

/**
 * 筛选条件 —— ⚠️ **不含关键词**。这是本层的核心不变量：
 * 关键词（`query`）只影响「结果集里放哪些」，不影响「eligible 是哪些」，
 * 于是「FTS 命中的文档」也会被类型/标签/文件夹/日期约束住。
 *
 * 曾经的实现把 query 也算进内存过滤，再另起一个「原文匹配（N）」分组收容
 * FTS 里多出来的命中 —— 那个分组**不受任何筛选约束**：用户筛了「PDF」，
 * 它照样能列出网页和视频。
 */
export interface DocFilter {
  /**
   * 标题关键词 —— **纯内存、零网络、不触发 FTS**。
   * 这是它与搜索框 `query` 的分工：`query` 是「找内容」（会打 `/api/search`），
   * 它是「筛标题」（只在 `docs` 上过滤）。两者可同时生效（AND）。
   */
  title: string
  /** 来源类型筛选，'' = 全部 */
  type: string
  /** 用户标签筛选（多选，AND 语义），[] = 不限 */
  tags: string[]
  /** null = 全部；'' = 未分类；其他 = 文件夹 id（三态必须区分） */
  folderId: string | null
  /** 日期范围（闭区间，`YYYY-MM-DD`）。null = 不限 */
  dateFrom: string | null
  dateTo: string | null
}

/** 内存过滤（**不含关键词**）：文件夹 / 来源类型 / 标签 / 标题关键词 / 日期范围。0ms，不动网络。 */
export function filterDocs(docs: DocEntry[], q: DocFilter): DocEntry[] {
  let result = docs
  if (q.folderId !== null) {
    // ⚠️ 空串是有效值（未分类）。悬空 folderId 由后端归化为未分类，两侧语义一致。
    result = result.filter(d => (d.folderId || '') === q.folderId)
  }
  if (q.type) {
    result = result.filter(d => d.sourceType === q.type)
  }
  if (q.tags.length) {
    result = result.filter(d => q.tags.every(t => (d.tags || []).includes(t)))
  }
  const titleNeedle = q.title.trim().toLowerCase()
  if (titleNeedle) {
    result = result.filter(d => d.title.toLowerCase().includes(titleNeedle))
  }
  if (q.dateFrom || q.dateTo) {
    result = result.filter(d => {
      // ⚠️ 取 ISO 字符串前 10 位比较，**不 new Date()**：转成 Date 再取本地日期时
      // 时区会让「今天 00:00 导入的文档」落到昨天。与 sortDocs 的 date 键同源语义。
      const day = (d.createdAt || '').slice(0, 10)
      // 没有 createdAt 的文档在设了范围时**被排除**：无法判定它在不在范围内。
      // 「拿不准就留下」在这里是错的——用户设了「本周」却看到一堆无日期的文档，
      // 只会以为筛选坏了。空列表至少是确定的。
      if (!day) return false
      if (q.dateFrom && day < q.dateFrom) return false
      if (q.dateTo && day > q.dateTo) return false
      return true
    })
  }
  return result
}

/** 单文档的关键词内存匹配（标题/类型/语言/标签）。空关键词视为匹配。 */
export function matchesQuery(d: DocEntry, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return d.title.toLowerCase().includes(needle) ||
    d.sourceType.toLowerCase().includes(needle) ||
    (d.language || '').toLowerCase().includes(needle) ||
    (d.tags || []).some(t => t.toLowerCase().includes(needle))
}

/** 排序（返回新数组，不改入参）。`rankOf` 只对 `key === 'relevance'` 有意义。 */
export function sortDocs(docs: DocEntry[], key: SortKey, rankOf?: Map<string, number>): DocEntry[] {
  const copy = [...docs]
  if (key === 'title') {
    return copy.sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'))
  }
  if (key === 'type') {
    return copy.sort((a, b) =>
      a.sourceType.localeCompare(b.sourceType) || a.title.localeCompare(b.title, 'zh-CN'))
  }
  if (key === 'relevance') {
    // rank 取自后端 `ORDER BY rank` 的**数组索引** —— 零后端改动：它已经按相关度排好序了，
    // 我们只需要记住「谁排第几」。没被命中的（内存匹配进来的）排最后。
    // ⚠️ 用 `??` 而非 `||`：索引 0 是合法且最相关的值，`||` 会把它当成「没有」。
    const MAX = Number.MAX_SAFE_INTEGER
    return copy.sort((a, b) => (rankOf?.get(a.id) ?? MAX) - (rankOf?.get(b.id) ?? MAX))
    // 同 rank（都是 MAX）时 `sort` 保持原有相对顺序（ES2019 起稳定），不会乱。
  }
  // date：createdAt 是 ISO 字符串，字符串比较即时间序——与后端
  // store.list_transcripts 的 sorted(key=createdAt) 同源语义，不自造第二套。
  return copy.sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''))
}

export interface ResultSet {
  /** 最终结果集（未排序）：`eligible` 里「关键词内存命中 ∪ FTS 命中」的那些 */
  result: DocEntry[]
  /**
   * 「**原文**（transcript）里也有匹配」的文档 id —— 卡片据此加角标。
   *
   * ⚠️ 叫「原文匹配」而不是「正文匹配」：FTS5 索引的是 title + content **两列**，
   * 一次查询无法判定命中来自哪一列（`_escape_fts5` 用 ` AND ` 连接词条，
   * 列限定语法与之组合很脆弱；唯一能判定的 `highlight()` 会把整列 ≤50000 字符回传）。
   * 而「原文里有匹配」对所有命中都成立，措辞准确、零额外成本。
   */
  matchedIds: Set<string>
  /** 命中 id → 正文摘要（卡片第二行渲染） */
  snippetById: Map<string, string>
  /** id → 后端 rank 序（数组索引），供 `sortDocs(..., 'relevance')` 用 */
  rankOf: Map<string, number>
}

/**
 * 组装**单一结果集** —— 取代原先的「主列表 + 原文匹配独立分组」双轨。
 *
 * 规则：结果集 = `eligible` ∩（关键词内存匹配 ∪ FTS 命中）。
 * `eligible` 由 `filterDocs` 算出，**不含关键词**——这正是关键：FTS 命中的文档
 * 也要受类型/标签/文件夹/日期约束。旧实现里那个独立分组不受任何约束，
 * 用户筛了「PDF」它照样能列出网页和视频。
 *
 * ⚠️ 两项额外收益：
 * - FTS 的 `ORDER BY rank` 相关性序被**保留**为 `rankOf`（取数组索引），升格成一等排序键，
 *   不再因为「合并进主列表」而丢掉。
 * - 命中的行从此也是普通行（有复选框、右键、拖拽），不再是那种只能点不能选的裸按钮。
 */
export function buildResultSet(
  docs: DocEntry[],
  filter: DocFilter,
  query: string,
  hits: SearchHit[],
): ResultSet {
  const eligible = filterDocs(docs, filter)
  const rankOf = new Map(hits.map((h, i) => [h.id, i]))
  const snippetById = new Map<string, string>()
  for (const h of hits) { if (h.snippet) snippetById.set(h.id, h.snippet) }

  if (!query.trim()) return { result: eligible, matchedIds: new Set(), snippetById, rankOf }

  const hitIds = new Set(hits.map(h => h.id))
  const matchedIds = new Set<string>()
  const result = eligible.filter(d => {
    if (hitIds.has(d.id)) { matchedIds.add(d.id); return true }
    // 内存命中的（标题/类型/语言/标签）也留下——FTS 还在防抖里没回来时，
    // 它撑着「边打边筛」的即时反馈；回来后两者取并集，不会重复。
    return matchesQuery(d, query)
  })
  return { result, matchedIds, snippetById, rankOf }
}

/** 全文搜索防抖延时（毫秒）——停手多久后才打后端 */
export const SEARCH_DEBOUNCE_MS = 250

/** FTS 单次取回的条数上限。超过就截断，并在列表底部明示（静默截断 = 用户以为「就这些」）。 */
export const SEARCH_LIMIT = 100

export interface DocQueryInput extends DocFilter {
  docs: DocEntry[]
  /** 搜索框关键词 */
  query: string
  sort: SortKey
}

export function useDocQuery(input: DocQueryInput) {
  const { docs, query, title, type, tags, folderId, dateFrom, dateTo, sort } = input

  const [hits, setHits] = useState<SearchHit[]>([])
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (!q) { setHits([]); setSearching(false); return }
    const ctl = new AbortController()
    const timer = setTimeout(async () => {
      setSearching(true)
      try {
        const r = await searchDocs(q, SEARCH_LIMIT, ctl.signal)
        // ⚠️ 竞态：换关键词会 abort 上一次请求，但它的 catch/finally 仍会执行。
        // 不做 aborted 判定的话，旧请求会把新请求的结果清成空数组。
        if (!ctl.signal.aborted) setHits(r)
      } catch {
        if (!ctl.signal.aborted) setHits([])
      } finally {
        if (!ctl.signal.aborted) setSearching(false)
      }
    }, SEARCH_DEBOUNCE_MS)
    return () => { clearTimeout(timer); ctl.abort() }
  }, [query])

  // 结果集 + 排序：每次按键立即重算（O(n log n)，千级文档 <1ms）
  // ⚠️ `tags` 是数组：调用方必须传引用稳定的值（从 store 取，勿在渲染里写 `[]` 字面量），
  // 否则每次渲染都重算一遍过滤。
  const { result, matchedIds, snippetById, rankOf } = useMemo(
    () => buildResultSet(docs, { title, type, tags, folderId, dateFrom, dateTo }, query, hits),
    [docs, title, type, tags, folderId, dateFrom, dateTo, query, hits],
  )
  const visible = useMemo(() => sortDocs(result, sort, rankOf), [result, sort, rankOf])

  /**
   * 「FTS 结果被截断」——恰好取回 `SEARCH_LIMIT` 条时判定为可能还有更多。
   *
   * ⚠️ 判据是 `>= SEARCH_LIMIT` 而不是 `> SEARCH_LIMIT`：后端按 limit 返回，**上限就是**
   * limit 条，永远不可能大于它；写 `>` 等于这个提示一次都不会出现。
   * 代价是「恰好 100 条」时会多提示一次——那正是应该提示的情形。
   */
  const hitsTruncated = query.trim() !== '' && hits.length >= SEARCH_LIMIT

  // `rankOf` 也回传：调用方用它判断「这次搜索有没有真命中」——
  // 「相关度」排序键该不该出现在选项里，只有它知道（见 SORT_OPTIONS）。
  return { visible, matchedIds, snippetById, rankOf, searching, hitsTruncated }
}

/** 从文档集合聚合用户标签频次（用于标签筛选行的 facet），频次降序、同频按字典序。 */
export function collectTagFacets(docs: DocEntry[]): { tag: string; count: number }[] {
  const counts = new Map<string, number>()
  for (const d of docs) {
    for (const t of d.tags || []) counts.set(t, (counts.get(t) || 0) + 1)
  }
  return [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag, 'zh-CN'))
}

export interface FolderCounts {
  /** 文件夹 id → 文档数 */
  byFolder: Map<string, number>
  /** 未分类（含 folderId 悬空的——后端删夹时会把文档归化，但前端可能先于刷新拿到中间态） */
  uncategorized: number
}

/**
 * 文件夹计数 —— **唯一来源**。筛选小节的 chip、列表分组头、工具栏总数全部读它。
 *
 * ⚠️ 不能读后端的 `folder.count`：那是上一次拉取的快照，而本地的 `moveDocs` 是乐观更新
 * （移完立刻改 `docs` 里的 folderId，不 refetch）。两边各读各的，移动瞬间计数互相矛盾——
 * 典型表现是「拖进文件夹，文档行已经进去了，文件夹后面的数字还是旧的」。
 */
export function countByFolder(docs: DocEntry[], folders: { id: string }[]): FolderCounts {
  const ids = new Set(folders.map(f => f.id))
  const byFolder = new Map<string, number>()
  let uncategorized = 0
  for (const d of docs) {
    const fid = d.folderId || ''
    if (!fid || !ids.has(fid)) { uncategorized++; continue }
    byFolder.set(fid, (byFolder.get(fid) || 0) + 1)
  }
  return { byFolder, uncategorized }
}

// ── 列表排列（独立于筛选的旋钮）──────────────────────────────────

/** 列表排列方式：none = 平铺不分组 */
export type GroupBy = 'none' | 'folder' | 'type' | 'date'

export const GROUP_OPTIONS: { key: GroupBy; label: string }[] = [
  { key: 'none', label: '平铺' },
  { key: 'folder', label: '按文件夹' },
  { key: 'type', label: '按类型' },
  { key: 'date', label: '按时间' },
]

export interface DocGroup {
  /** ⚠️ **必须带类型前缀**（`f:` / `t:` / `d:`）：分组头与文档行在同一个数组里渲染，
   *  裸的文件夹 id 可能与某个文档 id 相同，React 的 key 就会撞。 */
  key: string
  label: string
  /** 该组对应的原始值：文件夹组的 folderId（`''` = 未分类）、类型组的 sourceType、时间组的桶 key */
  ref: string
  /** 只有文件夹组可增删改 —— 类型/时间是**派生值**，改名删「组」没有语义。
   *  ⚠️ 用显式标志而不是让调用方 `groupBy === 'folder'` 到处判：后者散落三处，必然漏一处。 */
  editable: boolean
  docs: DocEntry[]
}

/** 时间分桶：今天 / 本周 / 本月 / 更早。⚠️「本周」= 本自然周（周一起），与日期筛选同义 */
function dateBucket(createdAt: string, now: Date): { key: string; label: string } {
  const day = (createdAt || '').slice(0, 10)
  const toDay = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const today = toDay(now)
  if (!day) return { key: 'unknown', label: '无日期' }
  if (day === today) return { key: 'today', label: '今天' }
  const weekStart = new Date(now)
  weekStart.setDate(now.getDate() - ((now.getDay() + 6) % 7))
  if (day >= toDay(weekStart)) return { key: 'week', label: '本周' }
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1)
  if (day >= toDay(monthStart)) return { key: 'month', label: '本月' }
  return { key: 'earlier', label: '更早' }
}

/**
 * 按 `by` 把**已排好序**的文档切成组 —— 组内保持入参顺序（顺序由 `sortDocs` 决定，
 * 本函数只做切分，故「相关度排序 + 按文件夹排列」时组内第一行就是该组最相关的那篇）。
 *
 * ⚠️ 空组的去留按维度区分，不是一刀切：
 * - **文件夹组保留空组**：它是拖拽的放置目标（也是「新建了文件夹但还没往里放东西」的
 *   正常状态）。隐藏它等于让用户没法把文档拖进去，也没法确认文件夹真的建好了。
 * - **类型/时间组隐藏空组**：它们是派生值，展示一个「视频 (0)」只是噪音。
 *   （`none` 由调用方直接走平铺，不到这里。）
 *
 * 「未分类」固定**最后**：它不是一个用户建的容器，排在中间会把真实文件夹挤散。
 */
export function groupDocs(
  docs: DocEntry[],
  by: Exclude<GroupBy, 'none'>,
  folders: { id: string; name: string }[],
  now: Date = new Date(),
): DocGroup[] {
  if (by === 'folder') {
    const ids = new Set(folders.map(f => f.id))
    const buckets = new Map<string, DocEntry[]>()
    for (const f of folders) buckets.set(f.id, [])
    buckets.set('', [])                        // 未分类
    for (const d of docs) {
      // 悬空 folderId 归入未分类 —— 与后端删夹时的归化、与 countByFolder 三处同源
      const fid = d.folderId && ids.has(d.folderId) ? d.folderId : ''
      buckets.get(fid)!.push(d)
    }
    const named = folders
      .filter(f => buckets.has(f.id))
      .map(f => ({
        key: `f:${f.id}`, label: f.name, ref: f.id, editable: true, docs: buckets.get(f.id)!,
      }))
    return [...named, {
      key: 'f:', label: '未分类', ref: '', editable: false, docs: buckets.get('')!,
    }]
  }

  // type / date：按**首次出现顺序**建组，空组天然不存在
  const buckets = new Map<string, DocGroup>()
  for (const d of docs) {
    const g = by === 'type'
      ? { key: `t:${d.sourceType}`, label: SRC_LABEL[d.sourceType] || d.sourceType, ref: d.sourceType }
      : (() => { const b = dateBucket(d.createdAt, now); return { key: `d:${b.key}`, label: b.label, ref: b.key } })()
    const cur = buckets.get(g.key)
    if (cur) cur.docs.push(d)
    else buckets.set(g.key, { ...g, editable: false, docs: [d] })
  }
  return [...buckets.values()]
}
