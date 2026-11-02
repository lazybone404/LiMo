/**
 * 「最近打开」存取层。
 *
 * 重点不在「能存能读」，而在三条**坏输入下不许炸**的边界 —— 读取方是
 * `selectDoc` 的调用链（打开文档），抛错会让一次正常的切文档失败：
 * 坏 JSON / 非数组 / 残缺条目都要静默跳过，只丢便利列表。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  loadRecentDocs, pushRecentDoc, clearRecentDocs, RECENT_DOCS_KEY, RECENT_DOCS_MAX,
} from '../lib/recent-docs'

beforeEach(() => {
  localStorage.clear()
})

describe('recent-docs', () => {
  it('空存储返回空数组', () => {
    expect(loadRecentDocs()).toEqual([])
  })

  it('前插：后推的在前', () => {
    pushRecentDoc('a', 'A')
    pushRecentDoc('b', 'B')
    expect(loadRecentDocs().map(r => r.id)).toEqual(['b', 'a'])
  })

  it('重复 id 只前移、不重复', () => {
    pushRecentDoc('a', 'A')
    pushRecentDoc('b', 'B')
    pushRecentDoc('a', 'A')
    const list = loadRecentDocs()
    expect(list.map(r => r.id)).toEqual(['a', 'b'])
    expect(list).toHaveLength(2)
  })

  it(`上限 ${RECENT_DOCS_MAX} 条，最旧的被挤掉`, () => {
    for (let i = 0; i < RECENT_DOCS_MAX + 5; i++) pushRecentDoc(`id${i}`, `T${i}`)
    const list = loadRecentDocs()
    expect(list).toHaveLength(RECENT_DOCS_MAX)
    expect(list[0].id).toBe(`id${RECENT_DOCS_MAX + 4}`)
    expect(list.some(r => r.id === 'id0')).toBe(false)
  })

  it('空标题不覆盖已有标题（启动恢复那一刻 docs 还没拉回来）', () => {
    pushRecentDoc('a', '真标题')
    pushRecentDoc('a', '')
    expect(loadRecentDocs()[0].title).toBe('真标题')
  })

  it('坏 JSON 不抛错，按空处理', () => {
    localStorage.setItem(RECENT_DOCS_KEY, '{不是 json')
    expect(loadRecentDocs()).toEqual([])
    // 坏值之后仍能正常写入
    expect(pushRecentDoc('a', 'A')[0].id).toBe('a')
  })

  it('非数组 / 残缺条目被过滤，不连坐整张表', () => {
    localStorage.setItem(RECENT_DOCS_KEY, JSON.stringify([
      { id: 'ok', title: '好条目', at: 1 },
      { title: '没有 id' },
      { id: '' },
      null,
      'str',
      { id: 'nofield' },
    ]))
    const list = loadRecentDocs()
    expect(list.map(r => r.id)).toEqual(['ok', 'nofield'])
    // 缺 title / at 的补默认值，而不是变成 undefined
    expect(list[1]).toEqual({ id: 'nofield', title: '', at: 0 })
  })

  it('clearRecentDocs 清空', () => {
    pushRecentDoc('a', 'A')
    clearRecentDocs()
    expect(loadRecentDocs()).toEqual([])
  })
})
