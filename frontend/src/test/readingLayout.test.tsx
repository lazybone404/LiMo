/**
 * 阅读版式的两根接合处 —— 这里断的不是「函数算得对不对」，而是
 * 「DOM 上真的挂着钩子」「CSS 真的按预期写」，也就是**判据跑到了没有**。
 *
 * 背景：`MarkdownParagraph`（markdown-components.tsx）有两个返回分支 ——
 * `components.a` / `.code` / `.img` 都是函数，所以**段落里只要有链接、行内代码或
 * 内联图片，整段就渲染成 `<div>` 而不是 `<p>`**。在技术笔记里这是绝大多数段落。
 *
 * 对照实验：
 *   A. 只给 `<p>` 分支挂 `md-para`（原实现）→ 「富段落」用例红。
 *      ⚠️ 这个缺陷**不会以任何形式报错**：纯文本段落缩进了、富段落没缩进，
 *      看起来完全像渲染正常，只有把两种段落并排看才察觉。
 *   B. 断言写成 `container.querySelector('p')` 而非 `firstElementChild` →
 *      富段落走 div 分支时 querySelector('p') 返回 null（断言崩），
 *      恰说明「用标签名找元素」本身就是这个 bug 的一部分。
 *   C. CSS 里把 `text-indent` 挪到 `.markdown-body` → 最后一条用例红。
 *      该写法**会继承**：标题、列表标记、代码、引用首行被一起缩进。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { FC, ReactNode } from 'react'
import { createMarkdownComponents } from '../lib/markdown-components'

/** 冒充 components.a / .code —— 判据是 `typeof c.type === 'function'`，只要求是个函数组件 */
const Inline: FC = () => <span>inline</span>

const P = createMarkdownComponents().p as FC<{ children?: ReactNode; className?: string }>

describe('MarkdownParagraph 的 md-para 钩子', () => {
  it('纯文本段落 → <p class="md-para">', () => {
    const { container } = render(<P>{'一段没有行内元素的纯文本'}</P>)
    const el = container.firstElementChild as HTMLElement
    expect(el.tagName).toBe('P')
    expect(el.className.split(' ')).toContain('md-para')
  })

  it('含函数子元素（链接/行内代码）的段落 → <div>，**且仍带 md-para**', () => {
    // 对照 A：只给 <p> 分支挂类 → 这条红。这是整个前置改动存在的理由。
    const { container } = render(<P>{['前', <Inline key="x" />, '后']}</P>)
    const el = container.firstElementChild as HTMLElement
    expect(el.tagName).toBe('DIV')            // 确认它真的走了 div 分支
    expect(el.className.split(' ')).toContain('md-para')  // 且钩子照样在
  })

  it('不吞掉上层传下来的 className', () => {
    const { container } = render(<P className="custom-cls">{'文本'}</P>)
    const cls = (container.firstElementChild as HTMLElement).className.split(' ')
    expect(cls).toContain('custom-cls')
    expect(cls).toContain('md-para')
  })
})

// ── CSS 层：jsdom 不做外部样式表的级联计算，只能对源文件本身断言 ──

describe('index.css 的阅读版式规则', () => {
  /** 剥掉 /* … *\/ 注释再解析 —— 注释里出现 text-indent 字样是刻意的（说明为什么不能乱设）。
   *  ⚠️ 用 process.cwd() 而非 import.meta.url：本文件跑在 jsdom 环境，import.meta.url 是
   *  http(s) 协议（URL 构造会抛「The URL must be of scheme file」）。vitest 的 cwd 即 frontend/ */
  const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')

  it('--read-line-height 的兜底值是 1.5（= vendor 现值）', () => {
    expect(css).toMatch(/--read-line-height:\s*1\.5\s*;/)
  })

  it('两种段落式互斥：book = 缩进 2em **且** 段距 0（不能只改一半）', () => {
    // 对照：clreq 明令禁止「又缩排又加大段距」的组合。只断言缩进的测试
    // 会放过一个同时保留 margin-bottom:10px 的实现 —— 那正是被禁止的形态。
    const m = css.match(/\[data-read-para="book"\][^{]*\{([^}]*)\}/)
    expect(m).toBeTruthy()
    const body = m![1]
    expect(body).toMatch(/text-indent:\s*2em/)
    expect(body).toMatch(/margin-bottom:\s*0/)
  })

  it('web 段落式 = vendor 现值（indent 0 + 10px），零回归', () => {
    const m = css.match(/\[data-read-para="web"\][^{]*\{([^}]*)\}/)
    expect(m).toBeTruthy()
    expect(m![1]).toMatch(/text-indent:\s*0/)
    expect(m![1]).toMatch(/margin-bottom:\s*10px/)
  })

  it('text-indent 只出现在 .md-para 规则上（绝不设在 .markdown-body / .preview-content）', () => {
    // 对照 C：设在 .markdown-body 会**继承**给标题/列表标记/代码/引用首行
    const blocks = css.match(/[^{}]+\{[^{}]*\}/g) ?? []
    const withIndent = blocks.filter(b => /text-indent\s*:/.test(b))
    expect(withIndent.length).toBeGreaterThan(0)   // 自证：真找到了规则，而不是正则没匹配上
    for (const b of withIndent) {
      const selector = b.slice(0, b.indexOf('{'))
      expect(selector).toContain('.md-para')
    }
  })

  it('行高不下放到 .markdown-body（否则代码块/表格行被一起拉开）', () => {
    const blocks = css.match(/[^{}]+\{[^{}]*\}/g) ?? []
    const onBody = blocks.filter(b => {
      const sel = b.slice(0, b.indexOf('{'))
      return /^\.markdown-body\s*$/.test(sel.trim()) && /line-height\s*:/.test(b)
    })
    expect(onBody).toEqual([])
  })
})
