import { describe, expect, it } from 'vitest'
import { countOccurrences, suggestTags } from '../lib/tag-suggest'

describe('countOccurrences', () => {
  it('ASCII 标签走词边界：AI 不命中 MAIL / said / React 中间的 Reaction', () => {
    expect(countOccurrences('MAIL said nothing', 'AI')).toBe(0)
    expect(countOccurrences('A Reaction happened', 'React')).toBe(0)
    // 对照：同样的输入换成独立出现必须命中 —— 否则上面两条可能是「永远返回 0」
    expect(countOccurrences('用 AI 做的', 'AI')).toBe(1)
    expect(countOccurrences('React 与 Vue', 'React')).toBe(1)
  })

  it('ASCII 标签在中文/标点旁仍能命中（边界只挡字母数字）', () => {
    expect(countOccurrences('（AI）', 'AI')).toBe(1)
    expect(countOccurrences('AI、ML', 'AI')).toBe(1)
    expect(countOccurrences('React.js', 'React')).toBe(1)
  })

  it('ASCII 大小写不敏感，且统计多次出现', () => {
    expect(countOccurrences('ai AI Ai', 'AI')).toBe(3)
  })

  it('中文标签走子串匹配（中文没有词边界）', () => {
    expect(countOccurrences('前端工程化实践', '前端')).toBe(1)
    expect(countOccurrences('前端、后端、前端', '前端')).toBe(2)
  })

  it('含正则元字符的标签按字面量匹配', () => {
    // `+` 不转义时 `new RegExp('C++')` 直接抛 "Nothing to repeat" —— 断言具体次数，
    // 而不只是「没抛」，否则「返回 0 也算通过」会把真正的静默失效放过去。
    expect(countOccurrences('C++ 与 C#', 'C++')).toBe(1)
    expect(countOccurrences('C++ 与 C#', 'C#')).toBe(1)
    // 词边界判据仍在：`XC++` 不算命中（转义若把整条规则退化成裸 includes，这里会是 1）
    expect(countOccurrences('XC++', 'C++')).toBe(0)
  })

  it('空输入返回 0', () => {
    expect(countOccurrences('', 'AI')).toBe(0)
    expect(countOccurrences('abc', '')).toBe(0)
  })
})

describe('suggestTags', () => {
  it('只返回正文里真的出现过的词表项', () => {
    const out = suggestTags('标题', '这篇文章讲 React 和前端', ['React', 'Vue', '前端', '数据库'])
    expect(out.map(s => s.tag).sort()).toEqual(['React', '前端'].sort())
  })

  it('排除文档已有的标签（大小写不敏感）', () => {
    const out = suggestTags('', 'react 与前端', ['React', '前端'], ['REACT'])
    expect(out.map(s => s.tag)).toEqual(['前端'])
  })

  it('标题命中排在正文命中之前', () => {
    const out = suggestTags('Kubernetes 入门', '正文里 Docker 出现了三次，Docker 很好用，Docker', ['Docker', 'Kubernetes'])
    expect(out[0]).toEqual({ tag: 'Kubernetes', inTitle: true })
    expect(out[1]).toEqual({ tag: 'Docker', inTitle: false })
  })

  it('同一层内按出现次数降序', () => {
    const out = suggestTags('', '甲 乙 乙 乙 丙 丙', ['甲', '乙', '丙'])
    expect(out.map(s => s.tag)).toEqual(['乙', '丙', '甲'])
  })

  it('去冗余：短标签的全部出现都在长标签里时丢掉短的', () => {
    const out = suggestTags('', '前端框架 前端框架', ['前端', '前端框架'])
    expect(out.map(s => s.tag)).toEqual(['前端框架'])
  })

  it('去冗余不误杀：短标签还有独立出现时保留', () => {
    const out = suggestTags('', '前端框架 与 前端', ['前端', '前端框架'])
    expect(out.map(s => s.tag).sort()).toEqual(['前端', '前端框架'].sort())
  })

  it('词表去重（大小写不同视为同一个）', () => {
    const out = suggestTags('', 'ai 很好', ['AI', 'ai'])
    expect(out).toHaveLength(1)
  })

  it('limit 生效', () => {
    const out = suggestTags('', 'a1 a2 a3 a4 a5 a6', ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'], [], 3)
    expect(out).toHaveLength(3)
  })

  it('空词表 / 空正文 / 全无命中 → 空数组（组件据此不渲染）', () => {
    expect(suggestTags('标题', '正文', [])).toEqual([])
    expect(suggestTags('标题', '正文', ['React'])).toEqual([])
    expect(suggestTags('', '', ['React'])).toEqual([])
  })

  it('标题命中但正文没命中，仍算建议', () => {
    const out = suggestTags('React 入门', '', ['React'])
    expect(out).toEqual([{ tag: 'React', inTitle: true }])
  })
})
