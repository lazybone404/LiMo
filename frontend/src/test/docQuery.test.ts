/**
 * 文档库查询层纯函数测试 —— filterDocs / matchesQuery / sortDocs / buildResultSet /
 * collectTagFacets / countByFolder / groupDocs。
 *
 * 这些判据为什么值得单测：三条筛选（类型 / 标签 / 文件夹）各有自己的三态语义，
 * 且「空串是有效值」这个反直觉点在三处都出现过（文件夹 id、类型 key、标签查询词）。
 * 写错时后端全绿、界面不报错，只是**过滤结果悄悄多几条或少几条**。
 */
import { describe, it, expect } from 'vitest'
import {
  filterDocs, matchesQuery, sortDocs, buildResultSet, collectTagFacets, countByFolder, groupDocs,
  type DocFilter,
} from '../components/doc-library/useDocQuery'
import type { SearchHit } from '../api/search'
import type { DocEntry } from '../store/types'

function doc(over: Partial<DocEntry>): DocEntry {
  return {
    id: 'd1', title: '标题', sourceType: 'pdf', engine: 'pypdfium2',
    contentHash: 'h', tags: [], createdAt: '2026-01-01T00:00:00', ...over,
  }
}

/** ⚠️ 筛选条件**不含关键词**（`query`）——这是本层的核心不变量，见 DocFilter 的注释 */
const F = (over: Partial<DocFilter> = {}): DocFilter =>
  ({ title: '', type: '', tags: [], folderId: null, dateFrom: null, dateTo: null, ...over })

/** ⚠️ `snippet` 是**必需字段**（`SearchHit`）；传空串表示「这一条没有摘要」，
 *  正好也覆盖了 `buildResultSet` 里 `if (h.snippet)` 那条空值判据 */
const hit = (id: string, snippet = ''): SearchHit =>
  ({ id, title: `t-${id}`, snippet })

describe('filterDocs', () => {
  const docs = [
    doc({ id: 'a', title: '论文A', sourceType: 'pdf', folderId: 'f1', tags: ['重点', '待读'], language: 'zh' }),
    doc({ id: 'b', title: 'Video B', sourceType: 'video', folderId: '', tags: ['待读'], language: 'en' }),
    doc({ id: 'c', title: '网页C', sourceType: 'webpage', folderId: 'f2' }),
  ]

  it('文件夹三态：null=全部 / ""=仅未分类 / id=该文件夹', () => {
    expect(filterDocs(docs, F()).map(d => d.id)).toEqual(['a', 'b', 'c'])
    // ⚠️ 空串必须走「未分类」分支而不是被当成「不过滤」
    expect(filterDocs(docs, F({ folderId: '' })).map(d => d.id)).toEqual(['b'])
    expect(filterDocs(docs, F({ folderId: 'f1' })).map(d => d.id)).toEqual(['a'])
    expect(filterDocs(docs, F({ folderId: 'f2' })).map(d => d.id)).toEqual(['c'])
  })

  it('类型筛选只认 sourceType（不是用户标签）', () => {
    expect(filterDocs(docs, F({ type: 'video' })).map(d => d.id)).toEqual(['b'])
    // 「重点」是标签不是类型 → 类型筛选下不该命中任何文档
    expect(filterDocs(docs, F({ type: '重点' }))).toEqual([])
  })

  it('标签筛选是多选 AND（全部命中才留下）', () => {
    expect(filterDocs(docs, F({ tags: ['待读'] })).map(d => d.id)).toEqual(['a', 'b'])
    expect(filterDocs(docs, F({ tags: ['待读', '重点'] })).map(d => d.id)).toEqual(['a'])
    expect(filterDocs(docs, F({ tags: ['不存在'] }))).toEqual([])
  })

  it('类型 + 标签 + 文件夹是 AND 关系', () => {
    expect(filterDocs(docs, F({ type: 'pdf', tags: ['重点'], folderId: 'f1' })).map(d => d.id)).toEqual(['a'])
    // 任一条件不满足即空
    expect(filterDocs(docs, F({ type: 'video', tags: ['重点'] }))).toEqual([])
  })

  it('标题关键词：纯内存、大小写不敏感、只匹配标题', () => {
    expect(filterDocs(docs, F({ title: 'video' })).map(d => d.id)).toEqual(['b'])
    expect(filterDocs(docs, F({ title: '  网页  ' })).map(d => d.id)).toEqual(['c'])
    // ⚠️ 与搜索框的分工：它**不**匹配类型/语言/标签（那些是 matchesQuery 的活）
    expect(filterDocs(docs, F({ title: 'pdf' }))).toEqual([])
    expect(filterDocs(docs, F({ title: '重点' }))).toEqual([])
  })

  it('日期范围是闭区间，比较在 ISO 字符串的日粒度上做', () => {
    const dated = [
      doc({ id: 'old', createdAt: '2026-01-01T23:59:59' }),
      doc({ id: 'mid', createdAt: '2026-01-10T00:00:00' }),
      doc({ id: 'new', createdAt: '2026-01-20T12:00:00' }),
    ]
    // 闭区间：端点当天的文档必须留下（写开区间会漏掉「今天导入的」）
    expect(filterDocs(dated, F({ dateFrom: '2026-01-01', dateTo: '2026-01-10' })).map(d => d.id))
      .toEqual(['old', 'mid'])
    expect(filterDocs(dated, F({ dateFrom: '2026-01-10' })).map(d => d.id)).toEqual(['mid', 'new'])
    expect(filterDocs(dated, F({ dateTo: '2026-01-10' })).map(d => d.id)).toEqual(['old', 'mid'])
  })

  it('设了日期范围时，无 createdAt 的文档被排除（而不是「拿不准就留下」）', () => {
    const mixed = [doc({ id: 'x', createdAt: '' }), doc({ id: 'y', createdAt: '2026-01-10T00:00:00' })]
    expect(filterDocs(mixed, F({ dateFrom: '2026-01-01' })).map(d => d.id)).toEqual(['y'])
    // 没设范围时它照常出现——排除只在「无法判定」真正成为一个问题时才发生
    expect(filterDocs(mixed, F()).map(d => d.id)).toEqual(['x', 'y'])
  })
})

describe('matchesQuery（关键词内存匹配，FTS 未回时撑着即时反馈）', () => {
  const d = doc({ id: 'a', title: '论文A', sourceType: 'pdf', language: 'zh', tags: ['重点'] })

  it('覆盖标题/类型/语言/标签，大小写不敏感', () => {
    expect(matchesQuery(d, '论文')).toBe(true)
    expect(matchesQuery(d, 'PDF')).toBe(true)
    expect(matchesQuery(d, 'zh')).toBe(true)
    expect(matchesQuery(d, '重点')).toBe(true)
    expect(matchesQuery(d, '不存在')).toBe(false)
  })

  it('空关键词视为匹配（调用方靠它短路）', () => {
    expect(matchesQuery(d, '')).toBe(true)
    expect(matchesQuery(d, '   ')).toBe(true)
  })
})

describe('sortDocs', () => {
  const docs = [
    doc({ id: 'a', title: 'banana', sourceType: 'pdf', createdAt: '2026-01-03T00:00:00' }),
    doc({ id: 'b', title: 'apple', sourceType: 'video', createdAt: '2026-01-01T00:00:00' }),
    doc({ id: 'c', title: 'cherry', sourceType: 'webpage', createdAt: '2026-01-02T00:00:00' }),
  ]

  it('date：新 → 旧', () => {
    expect(sortDocs(docs, 'date').map(d => d.id)).toEqual(['a', 'c', 'b'])
  })

  it('title：字典序', () => {
    // apple(b) < banana(a) < cherry(c)
    expect(sortDocs(docs, 'title').map(d => d.id)).toEqual(['b', 'a', 'c'])
  })

  it('type：同类型内按标题，保证同类型顺序稳定', () => {
    const same = [doc({ id: 'x', title: '乙', sourceType: 'pdf' }), doc({ id: 'y', title: '甲', sourceType: 'pdf' })]
    expect(sortDocs(same, 'type').map(d => d.id)).toEqual(['y', 'x'])
  })

  it('relevance：按 rankOf 升序，未命中的排最后', () => {
    const rankOf = new Map([['c', 0], ['a', 1]])
    expect(sortDocs(docs, 'relevance', rankOf).map(d => d.id)).toEqual(['c', 'a', 'b'])
  })

  it('relevance：索引 0 是合法排名（用 `||` 实现会把它当成「没有」）', () => {
    // 只有一篇命中，且它排第 0 位 —— 若实现写成 `rankOf.get(id) || MAX`，
    // 0 是 falsy 会被替换成 MAX，首行就变成了未命中的那篇
    const rankOf = new Map([['b', 0]])
    expect(sortDocs(docs, 'relevance', rankOf)[0].id).toBe('b')
  })

  it('relevance：没有 rankOf 时保持原序（不是抛错也不是全乱）', () => {
    expect(sortDocs(docs, 'relevance').map(d => d.id)).toEqual(['a', 'b', 'c'])
  })

  it('不改入参（返回新数组）', () => {
    const input = [...docs]
    sortDocs(input, 'title')
    expect(input.map(d => d.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('buildResultSet', () => {
  const docs = [
    doc({ id: 'a', title: '命中标题的文档', sourceType: 'pdf', folderId: 'f1' }),
    doc({ id: 'b', title: '另一篇论文', sourceType: 'pdf' }),
    doc({ id: 'c', title: '网页C', sourceType: 'webpage' }),
  ]

  it('无关键词时 = 纯筛选结果，且不带任何命中标记', () => {
    const r = buildResultSet(docs, F({ type: 'pdf' }), '', [hit('a', 's')])
    expect(r.result.map(d => d.id)).toEqual(['a', 'b'])
    expect([...r.matchedIds]).toEqual([])
  })

  it('FTS 命中的文档也必须落在 eligible 里 —— 筛选「PDF」不能列出网页', () => {
    // 这是本层最核心的一条：旧的「原文匹配（N）」独立分组不受任何筛选约束，
    // 用户筛了 PDF，它照样能列出网页和视频
    const r = buildResultSet(docs, F({ type: 'pdf' }), 'x', [hit('a'), hit('c')])
    expect(r.result.map(d => d.id)).toEqual(['a'])
    expect([...r.matchedIds]).toEqual(['a'])
  })

  it('关键词内存命中 ∪ FTS 命中取并集，同一篇只出现一次', () => {
    // 'a' 内存命中标题且被 FTS 命中 → 只出现一次；'b' 仅内存命中（FTS 还没回来/没收录）
    const r = buildResultSet(docs, F(), '论文', [hit('a', 's')])
    expect(r.result.map(d => d.id)).toEqual(['a', 'b'])
  })

  it('matchedIds 只收 FTS 命中，不收纯内存命中（否则「原文」角标是假的）', () => {
    const r = buildResultSet(docs, F(), '命中标题', [])
    expect(r.result.map(d => d.id)).toEqual(['a'])
    expect([...r.matchedIds]).toEqual([])
  })

  it('rankOf 取 hits 数组索引，且只对有 snippet 的命中建摘要映射', () => {
    const r = buildResultSet(docs, F(), 'x', [hit('c', 'sa'), hit('a')])
    expect(r.rankOf.get('c')).toBe(0)
    expect(r.rankOf.get('a')).toBe(1)
    expect(r.snippetById.get('c')).toBe('sa')
    expect(r.snippetById.has('a')).toBe(false)
  })
})

describe('collectTagFacets', () => {
  it('按频次降序，同频按字典序', () => {
    const docs = [
      doc({ id: 'a', tags: ['重点', '待读'] }),
      doc({ id: 'b', tags: ['重点'] }),
      doc({ id: 'c', tags: ['冷门'] }),
    ]
    expect(collectTagFacets(docs)).toEqual([
      { tag: '重点', count: 2 },
      { tag: '待读', count: 1 },
      { tag: '冷门', count: 1 },
    ])
  })

  it('无标签文档不产生 facet', () => {
    expect(collectTagFacets([doc({ id: 'a' })])).toEqual([])
  })
})

describe('countByFolder', () => {
  it('悬空 folderId 归入未分类（与后端删夹时的归化、与 groupDocs 三处同源）', () => {
    const folders = [{ id: 'f1' }, { id: 'f2' }]
    const docs = [
      doc({ id: 'a', folderId: 'f1' }),
      doc({ id: 'b', folderId: 'f1' }),
      doc({ id: 'c', folderId: 'f2' }),
      doc({ id: 'd', folderId: '' }),
      doc({ id: 'e', folderId: 'ghost' }),   // 文件夹已删，索引还没刷新
    ]
    const { byFolder, uncategorized } = countByFolder(docs, folders)
    expect(byFolder.get('f1')).toBe(2)
    expect(byFolder.get('f2')).toBe(1)
    expect(uncategorized).toBe(2)
  })
})

describe('groupDocs', () => {
  const folders = [{ id: 'f1', name: '工作' }, { id: 'f2', name: '学习' }]
  const groupOf = (docs: DocEntry[], by: 'folder' | 'type' | 'date', now?: Date) =>
    groupDocs(docs, by, folders, now).map(g => [g.key, g.label, g.docs.map(d => d.id)])

  it('按文件夹：空组保留（它是拖放目标），未分类固定最后', () => {
    const docs = [doc({ id: 'a', folderId: 'f2' }), doc({ id: 'b', folderId: '' })]
    expect(groupOf(docs, 'folder')).toEqual([
      ['f:f1', '工作', []],                     // ⚠️ 空组必须留着：新建了文件夹还没放东西是正常状态
      ['f:f2', '学习', ['a']],
      ['f:', '未分类', ['b']],
    ])
  })

  it('按文件夹：组内保持入参顺序（顺序由 sortDocs 决定，切分不改它）', () => {
    const docs = [doc({ id: 'a', folderId: 'f1' }), doc({ id: 'z', folderId: 'f1' }), doc({ id: 'm', folderId: 'f1' })]
    expect(groupOf(docs, 'folder')[0][2]).toEqual(['a', 'z', 'm'])
  })

  it('按文件夹：folderId 悬空 → 归入未分类', () => {
    expect(groupOf([doc({ id: 'a', folderId: 'ghost' })], 'folder')[2][2]).toEqual(['a'])
  })

  it('按类型：空组天然不存在，且只有文件夹组可编辑', () => {
    const docs = [doc({ id: 'a', sourceType: 'pdf' }), doc({ id: 'b', sourceType: 'video' }), doc({ id: 'c', sourceType: 'pdf' })]
    expect(groupOf(docs, 'type')).toEqual([
      ['t:pdf', 'PDF', ['a', 'c']],
      ['t:video', '视频', ['b']],
    ])
    const groups = groupDocs(docs, 'type', folders)
    expect(groups.every(g => g.editable === false)).toBe(true)
    expect(groupDocs(docs, 'folder', folders).slice(0, 2).every(g => g.editable)).toBe(true)
  })

  it('组 key 带维度前缀 —— 分组头与文档行在同一数组里渲染，裸 key 会撞', () => {
    // 文档 id 恰好等于文件夹 id 的场景：key 不带前缀时 React 会警告重复 key
    const docs = [doc({ id: 'f1', folderId: 'f1' })]
    expect(groupOf(docs, 'folder')[0][0]).toBe('f:f1')
    expect(groupDocs(docs, 'type', folders)[0].key).toBe('t:pdf')
    expect(groupDocs(docs, 'date', folders, new Date('2026-01-01T12:00:00'))[0].key).toBe('d:today')
  })

  it('按时间：今天 / 本周 / 本月 / 更早（周一起算，不是「最近 7 天」）', () => {
    // 2026-01-15 是周四 → 本周从 01-12（周一）起
    const now = new Date('2026-01-15T12:00:00')
    const docs = [
      doc({ id: 'today', createdAt: '2026-01-15T08:00:00' }),
      doc({ id: 'week', createdAt: '2026-01-12T00:00:00' }),
      doc({ id: 'month', createdAt: '2026-01-05T00:00:00' }),
      doc({ id: 'earlier', createdAt: '2025-12-31T00:00:00' }),
    ]
    expect(groupOf(docs, 'date', now)).toEqual([
      ['d:today', '今天', ['today']],
      ['d:week', '本周', ['week']],
      ['d:month', '本月', ['month']],
      ['d:earlier', '更早', ['earlier']],
    ])
  })

  it('按时间：无 createdAt 的文档单独成组，不会被塞进「更早」', () => {
    const now = new Date('2026-01-15T12:00:00')
    expect(groupOf([doc({ id: 'x', createdAt: '' })], 'date', now))
      .toEqual([['d:unknown', '无日期', ['x']]])
  })
})
