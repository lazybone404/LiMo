/**
 * 文档库查询层纯函数测试 —— filterDocs / sortDocs / mergeHits / collectTagFacets。
 *
 * 这些判据为什么值得单测：三条筛选（类型 / 标签 / 文件夹）各有自己的三态语义，
 * 且「空串是有效值」这个反直觉点在三处都出现过（文件夹 id、类型 key、标签查询词）。
 * 写错时后端全绿、界面不报错，只是**过滤结果悄悄多几条或少几条**。
 */
import { describe, it, expect } from 'vitest'
import { filterDocs, sortDocs, mergeHits, collectTagFacets, type DocQuery } from '../components/doc-library/useDocQuery'
import type { DocEntry } from '../store/types'

function doc(over: Partial<DocEntry>): DocEntry {
  return {
    id: 'd1', title: '标题', sourceType: 'pdf', engine: 'pypdfium2',
    contentHash: 'h', tags: [], createdAt: '2026-01-01T00:00:00', ...over,
  }
}

const Q = (over: Partial<DocQuery> = {}): DocQuery =>
  ({ query: '', type: '', tags: [], folderId: null, ...over })

describe('filterDocs', () => {
  const docs = [
    doc({ id: 'a', title: '论文A', sourceType: 'pdf', folderId: 'f1', tags: ['重点', '待读'], language: 'zh' }),
    doc({ id: 'b', title: 'Video B', sourceType: 'video', folderId: '', tags: ['待读'], language: 'en' }),
    doc({ id: 'c', title: '网页C', sourceType: 'webpage', folderId: 'f2' }),
  ]

  it('文件夹三态：null=全部 / ""=仅未分类 / id=该文件夹', () => {
    expect(filterDocs(docs, Q()).map(d => d.id)).toEqual(['a', 'b', 'c'])
    // ⚠️ 空串必须走「未分类」分支而不是被当成「不过滤」
    expect(filterDocs(docs, Q({ folderId: '' })).map(d => d.id)).toEqual(['b'])
    expect(filterDocs(docs, Q({ folderId: 'f1' })).map(d => d.id)).toEqual(['a'])
    expect(filterDocs(docs, Q({ folderId: 'f2' })).map(d => d.id)).toEqual(['c'])
  })

  it('类型筛选只认 sourceType（不是用户标签）', () => {
    expect(filterDocs(docs, Q({ type: 'video' })).map(d => d.id)).toEqual(['b'])
    // 「重点」是标签不是类型 → 类型筛选下不该命中任何文档
    expect(filterDocs(docs, Q({ type: '重点' }))).toEqual([])
  })

  it('标签筛选是多选 AND（全部命中才留下）', () => {
    expect(filterDocs(docs, Q({ tags: ['待读'] })).map(d => d.id)).toEqual(['a', 'b'])
    expect(filterDocs(docs, Q({ tags: ['待读', '重点'] })).map(d => d.id)).toEqual(['a'])
    expect(filterDocs(docs, Q({ tags: ['不存在'] }))).toEqual([])
  })

  it('类型 + 标签 + 文件夹是 AND 关系', () => {
    expect(filterDocs(docs, Q({ type: 'pdf', tags: ['重点'], folderId: 'f1' })).map(d => d.id)).toEqual(['a'])
    // 任一条件不满足即空
    expect(filterDocs(docs, Q({ type: 'video', tags: ['重点'] }))).toEqual([])
  })

  it('查询词大小写不敏感，覆盖标题/类型/语言/标签', () => {
    expect(filterDocs(docs, Q({ query: 'video' })).map(d => d.id)).toEqual(['b'])
    expect(filterDocs(docs, Q({ query: 'VIDEO' })).map(d => d.id)).toEqual(['b'])
    expect(filterDocs(docs, Q({ query: '论文' })).map(d => d.id)).toEqual(['a'])
    expect(filterDocs(docs, Q({ query: 'zh' })).map(d => d.id)).toEqual(['a'])
    expect(filterDocs(docs, Q({ query: '重点' })).map(d => d.id)).toEqual(['a'])
    // 前后空白不该把结果清空（否则用户复制粘贴带空格的关键词就搜不到）
    expect(filterDocs(docs, Q({ query: '  网页  ' })).map(d => d.id)).toEqual(['c'])
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

  it('不改入参（返回新数组）', () => {
    const input = [...docs]
    sortDocs(input, 'title')
    expect(input.map(d => d.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('mergeHits', () => {
  const visible = [doc({ id: 'a', title: '命中标题的文档' }), doc({ id: 'b', title: '另一篇' })]

  it('在列表中的命中 → matchedIds（加角标），不重复出现在分组里', () => {
    const { matchedIds, contentOnly } = mergeHits(visible, [
      { id: 'a', title: '命中标题的文档', snippet: 'x<mark>y</mark>z' },
    ])
    // ⚠️ 双命中（标题+正文都有）的文档只能出现一次：在列表里加角标，
    // 不能同时进「原文匹配」分组——那会让同一篇文档在同一屏出现两次。
    expect([...matchedIds]).toEqual(['a'])
    expect(contentOnly).toEqual([])
  })

  it('不在列表中的命中 → contentOnly（纯原文命中，按 rank 顺序保留）', () => {
    const { matchedIds, contentOnly } = mergeHits(visible, [
      { id: 'z1', title: '第一篇', snippet: 's1' },
      { id: 'z2', title: '第二篇', snippet: 's2' },
    ])
    expect([...matchedIds]).toEqual([])
    expect(contentOnly.map(h => h.id)).toEqual(['z1', 'z2'])
  })

  it('两类命中混在一起时各归各位', () => {
    const { matchedIds, contentOnly } = mergeHits(visible, [
      { id: 'a', title: '命中标题的文档', snippet: 's' },
      { id: 'z1', title: '纯正文', snippet: 's' },
      { id: 'b', title: '另一篇', snippet: 's' },
    ])
    expect([...matchedIds].sort()).toEqual(['a', 'b'])
    expect(contentOnly.map(h => h.id)).toEqual(['z1'])
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
