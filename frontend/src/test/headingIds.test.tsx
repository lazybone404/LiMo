// 标题锚点 id —— TOC（源码侧）与渲染侧必须算出**同一个** id（P1-15）
//
// 背景：TOC 从 Markdown **源码**正则取标题文本，渲染侧从 children 取。修复前渲染侧
// 对 children 做 `join('')`，而 `**加粗**` 解析后是 React 元素 → `[object Object]`
// → 渲染 id = `object-object-内容`，TOC id = `重点-内容`。TOC 点击走
// `document.getElementById(id)?.`，**null 被 `?.` 静默吞掉** —— 症状是「点目录没反应」，
// 零报错。凡标题含行内加粗/链接/行内代码，全部命中。
//
// ⚠️ 三处同源（前端 TOC / 前端渲染 / 后端 `markdown_render.add_heading_ids`），
//    本文件覆盖前两处，后端那处由 `backend/tests/test_heading_ids.py` 覆盖同一批样例。
// ⚠️ 对照：把 `nodeText(children)` 改回 `children.join('')` → 第 1 条红。

import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { createMarkdownComponents, extractHeadings, nodeText } from '../lib/markdown-components'

/** 复刻 ReactMarkdown 交给 h2 的 children（渲染后的节点树，不是源码）。 */
function renderHeadingId(children: unknown): string {
  const comps = createMarkdownComponents() as any
  const { container } = render(<>{comps.h2({ children })}</>)
  return container.querySelector('h2')!.id
}

describe('标题锚点 id — TOC 与渲染侧同源（P1-15）', () => {
  it('含行内加粗：两侧 id 相同（修复前渲染侧是 `object-object-…`）', () => {
    const toc = extractHeadings('## **重点** 内容')[0]
    const domId = renderHeadingId([<strong key="s">重点</strong>, ' 内容'])
    expect(domId).toBe(toc.id)
    expect(domId).toBe('重点-内容')
  })

  it('含行内链接 / 行内代码：同样对齐', () => {
    // 源码侧要剥 `[文本](url)` 与反引号，渲染侧天然只有文本
    const toc = extractHeadings('### 见 [文档](https://example.com/a) 与 `code`')[0]
    const domId = renderHeadingId([`见 `, <a key="a" href="https://example.com/a">文档</a>, ' 与 ', <code key="c">code</code>])
    expect(domId).toBe(toc.id)
  })

  it('带时间戳 badge：源码形态与渲染后形态落到同一个 id', () => {
    // 源码：视频转录的 `[▶ 12:30](#)`；渲染后：badge span 已被 cleanHeading 摘走，只剩纯文本
    const toc = extractHeadings('## 开场 [▶ 12:30](#)')[0]
    expect(toc.id).toBe(renderHeadingId([`开场 `, <span key="t">12:30</span>]))
    expect(toc.id).toBe('开场')
  })

  it('围栏代码块里的 `## ` 不产生目录项（否则是点了没反应的幽灵项）', () => {
    const md = ['## 真标题', '', '```bash', '## 这是代码里的注释，不是标题', '```', '', '### 另一个真标题'].join('\n')
    expect(extractHeadings(md).map(h => h.text)).toEqual(['真标题', '另一个真标题'])
  })

  it('对照：nodeText 会递归进 React 元素（这正是 join 做不到的）', () => {
    // 修复前 `children.join('')` 对这些输入产出 `[object Object], 尾` —— 本断言把
    // 「递归取文本」这件事本身钉住，读代码时不必回看 React 的类型系统。
    expect(nodeText([{ props: { children: '重' }, type: 'strong' }, '点'])).toBe('重点')
    expect(nodeText(['a', ['b', 'c']])).toBe('abc')
    expect(nodeText(null)).toBe('')
  })
})
