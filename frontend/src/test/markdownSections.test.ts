/**
 * H2 切分器 —— 表驱动读**跨语言共享 fixture**。
 *
 * 这个文件的价值几乎全在 fixture 本身上：切分规则有两份实现（本仓 JS 一份、
 * 后端 Python 一份，因为导出要跟随），它们必然分开演化。这份 JSON 是两侧
 * **唯一的一致性证据** —— 后端 `tests/test_markdown_sections.py` 读同一份文件。
 * 「两边写得像」不是证据。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 切分时不跳围栏 → 代码块里的 `## ` 被当成标题 → 切出**幽灵卡片**
 *      （一张只有代码片段、没有标题语义的卡）。零报错，只是卡片数量凭空多出来。
 *      `extractHeadings` 已经踩过一次同款坑（产出「点了没反应的幽灵目录项」）。
 *   B. 无 H2 时返回 `{preamble:'', sections:[]}`（「反正没节」）→ 整篇内容**凭空消失**。
 *   C. H2 前的引言不收集 → 文档开头的背景段落静默丢失。
 *   D. 把 `\r` 留在正文行尾 → 渲染上看不出来，fixture 比对上一眼现形。
 *   E. 不剥章节尾部的 `---` → 每张卡底部多一条线（`_NOTES_TASK` 第 6 条会
 *      要求在章节间写它，所以这是**常态**而不是边角）。
 *   F. 连**内部**的 `---` 一起剥 → 章节里用分隔线分隔的话题被拼成一整块。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { splitByH2 } from '../lib/markdown-sections'

/**
 * ⚠️ 用 `process.cwd()` 而非 `import.meta.url`：本文件跑在 jsdom 环境。
 * 与 `readingLayout.test.tsx` 读 `index.css` 同款（那边有同样的说明）。
 */
const FIXTURE_PATH = resolve(process.cwd(), 'src/test/fixtures/h2-sections.json')
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8')) as {
  cases: { name: string; md: string; preamble: string; sections: { heading: string; body: string }[] }[]
}

describe('splitByH2 — 跨语言共享 fixture', () => {
  it('fixture 至少覆盖到那几类关键行为（防止用例被删空后整组测试静默变成空跑）', () => {
    expect(FIXTURE.cases.length).toBeGreaterThanOrEqual(8)
    const names = FIXTURE.cases.map(c => c.name).join('\n')
    expect(names).toContain('围栏')      // A
    expect(names).toContain('无 H2')     // B
    expect(names).toContain('引言')      // C
    expect(names).toContain('CRLF')      // D
    expect(names).toContain('末尾')      // E
    expect(names).toContain('内部')      // F
  })

  for (const c of FIXTURE.cases) {
    it(c.name, () => {
      expect(splitByH2(c.md)).toEqual({ preamble: c.preamble, sections: c.sections })
    })
  }
})

describe('splitByH2 — 结构不变量', () => {
  it('切出来的每张卡拼回去等于原文（去掉尾部分隔线之外零增减）', () => {
    const md = '## 甲\n内容一\n\n## 乙\n内容二\n\n## 丙\n内容三\n'
    const { preamble, sections } = splitByH2(md)
    const joined = [preamble, ...sections.map(s => `${s.heading}\n${s.body}`)]
      .filter(s => s !== '')
      .join('\n\n')
    for (const chunk of ['## 甲', '内容一', '## 乙', '内容二', '## 丙', '内容三']) {
      expect(joined).toContain(chunk)
    }
  })

  it('heading 保留 `## ` 前缀（卡片内原样拼回 Markdown，不另画标题元素）', () => {
    const { sections } = splitByH2('## 甲\n内容\n')
    expect(sections[0].heading).toBe('## 甲')
  })

  it('不认 `# ` 与 `### `（只切 H2）', () => {
    const { preamble, sections } = splitByH2('# 大标题\n\n### 三级\n内容\n')
    expect(sections).toEqual([])
    expect(preamble).toContain('# 大标题')
  })
})
