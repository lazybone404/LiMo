/**
 * 文档库查询层 — 纯函数（filterDocs / sortDocs / mergeHits）单独导出以便表驱动单测，
 * hook 只负责把它们接到 React 状态上。
 *
 * 为什么要「内存过滤 + 后端 FTS」混用，而不是二选一：
 * - `docs` 数组里**没有正文**（后端 index entry 是白名单序列化器），所以正文搜索
 *   在客户端**不可能**实现 —— FTS 不是优化，是唯一可行解。
 * - 但标题/类型/语言的匹配是 0ms、无网络，是「边打边筛」的体验来源，不能撤。
 * - 故：内存过滤结果**立刻**渲染，停手 250ms 后再打后端，把多出来的命中追加在下方。
 */
import { useEffect, useMemo, useState } from 'react'
import { searchDocs, type SearchHit } from '../../api/search'
import type { DocEntry } from '../../store/types'

export type SortKey = 'date' | 'title' | 'type'

export const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'date', label: '时间' },
  { key: 'title', label: '标题' },
  { key: 'type', label: '类型' },
]

export interface DocQuery {
  query: string
  /** 来源类型筛选，'' = 全部 */
  type: string
  /** 用户标签筛选（多选，AND 语义），[] = 不限 */
  tags: string[]
  /** null = 全部；'' = 未分类；其他 = 文件夹 id（三态必须区分） */
  folderId: string | null
}

/** 内存过滤：标题 / 来源类型 / 语言 / 用户标签。0ms，不动网络。 */
export function filterDocs(docs: DocEntry[], q: DocQuery): DocEntry[] {
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
  const needle = q.query.trim().toLowerCase()
  if (needle) {
    result = result.filter(d =>
      d.title.toLowerCase().includes(needle) ||
      d.sourceType.toLowerCase().includes(needle) ||
      (d.language || '').toLowerCase().includes(needle) ||
      (d.tags || []).some(t => t.toLowerCase().includes(needle))
    )
  }
  return result
}

/** 排序（返回新数组，不改入参）。 */
export function sortDocs(docs: DocEntry[], key: SortKey): DocEntry[] {
  const copy = [...docs]
  if (key === 'title') {
    return copy.sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'))
  }
  if (key === 'type') {
    return copy.sort((a, b) =>
      a.sourceType.localeCompare(b.sourceType) || a.title.localeCompare(b.title, 'zh-CN'))
  }
  // date：createdAt 是 ISO 字符串，字符串比较即时间序——与后端
  // store.list_transcripts 的 sorted(key=createdAt) 同源语义，不自造第二套。
  return copy.sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''))
}

export interface MergedHits {
  /**
   * 主列表中「**原文**（transcript）里也有匹配」的文档 id —— 卡片据此加角标。
   *
   * ⚠️ 叫「原文匹配」而不是「正文匹配」：FTS5 索引的是 title + content **两列**，
   * 一次查询无法判定命中来自哪一列（`_escape_fts5` 用 ` AND ` 连接词条，
   * 列限定语法与之组合很脆弱；唯一能判定的 `highlight()` 会把整列 ≤50000 字符回传）。
   * 而「原文里有匹配」对所有命中都成立，措辞准确、零额外成本。
   */
  matchedIds: Set<string>
  /**
   * 只被原文命中、**不在主列表里**的文档（内存过滤覆盖不到的那部分），按 rank 排序。
   * 固定追加在列表下方，不参与用户排序——它没有可比的 createdAt 上下文。
   */
  contentOnly: SearchHit[]
}

export function mergeHits(visible: DocEntry[], hits: SearchHit[]): MergedHits {
  const shown = new Set(visible.map(d => d.id))
  const matchedIds = new Set<string>()
  const contentOnly: SearchHit[] = []
  for (const h of hits) {
    if (shown.has(h.id)) matchedIds.add(h.id)
    else contentOnly.push(h)
  }
  return { matchedIds, contentOnly }
}

/** 全文搜索防抖延时（毫秒）——停手多久后才打后端 */
export const SEARCH_DEBOUNCE_MS = 250

export interface DocQueryInput extends DocQuery {
  docs: DocEntry[]
  sort: SortKey
}

export function useDocQuery(input: DocQueryInput) {
  const { docs, query, type, tags, folderId, sort } = input

  // 内存过滤 + 排序：每次按键立即重算（O(n log n)，千级文档 <1ms）
  // ⚠️ `tags` 是数组：调用方必须传引用稳定的值（从 store 取，勿在渲染里写 `[]` 字面量），
  // 否则每次渲染都重算一遍过滤。
  const visible = useMemo(
    () => sortDocs(filterDocs(docs, { query, type, tags, folderId }), sort),
    [docs, query, type, tags, folderId, sort],
  )

  const [hits, setHits] = useState<SearchHit[]>([])
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (!q) { setHits([]); setSearching(false); return }
    const ctl = new AbortController()
    const timer = setTimeout(async () => {
      setSearching(true)
      try {
        const r = await searchDocs(q, 50, ctl.signal)
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

  const { matchedIds, contentOnly } = useMemo(() => mergeHits(visible, hits), [visible, hits])

  return { visible, matchedIds, contentOnly, searching }
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
