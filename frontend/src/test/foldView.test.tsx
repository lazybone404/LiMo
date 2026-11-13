/**
 * 折叠视图（`components/preview/FoldView.tsx`）—— 卡片数、展开/收起、只有标题的节不可点。
 *
 * ⚠️ 断言的重点不是「组件挂上了」，而是**展开的内容走的是 `MarkerContent`**。
 * 直接交给 `MarkdownPreview` 也能把文字画出来，页面看上去正常，但十二种 `limo-block`
 * 的外壳全部消失 —— 那是本仓库最常见的一类静默失效。所以展开后断的是
 * `[data-block-type="alert"]` 这个外壳，不是「文字在某处」。
 *
 * 对照实验（每条对应一个「看起来对、其实错」的实现）：
 *   A. 未展开时也把整节 Markdown 交给渲染器 → 第 1 条的「第二段不可见」会红。
 *   B. 展开时用 `MarkdownPreview` 代替 `MarkerContent` → 第 2 条的 `data-block-type` 会红。
 *   C. 用 `body` 判可点（它在切分器里含标题行，永不为空）→ 第 4 条会红。
 *   D. 字号写死 `16px` → 第 6 条（CSS 文本）会红。
 */
import { createRef } from 'react'
import { describe, it, expect } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { FoldView, type FoldViewHandle } from '../components/preview/FoldView'

const META = { style: 'plain', mode: 'notes', version: 1 }

const ALERT_OPEN = '<!-- limo-block:v1 type="alert" -->'
const ALERT_CLOSE = '<!-- /limo-block -->'

const MD = `## 甲
第一段。

${ALERT_OPEN}
风险内容
${ALERT_CLOSE}

第二段。

## 乙
乙的一段。
`

describe('FoldView：主题档', () => {
  it('每节一张卡、标题带 id、未展开只露一句话', () => {
    const { container } = render(<FoldView variant="topic" content={MD} meta={META} />)
    expect(container.querySelectorAll('.md-card').length).toBe(2)
    // 空壳检测：两张卡里各有标题
    expect(container.querySelectorAll('.md-card h2').length).toBe(2)
    // 标题的 id 与 TOC 侧同源 —— 折叠态下目录仍能跳到 H2
    const h2 = container.querySelectorAll('.md-card h2')
    expect(h2[0].id).toBe('甲')
    expect(h2[1].id).toBe('乙')

    // 可点的地方有标识。⚠️ 只有「甲」有：乙的正文是一句纯文本，展开前后逐字一样，
    // 给了标识就是「看起来能点、点开没新东西」。
    expect(container.querySelectorAll('.fold-hint').length).toBe(1)
    // 标识在标题行里，不是浮在卡片角落
    expect(container.querySelectorAll('.fold-head .fold-hint').length).toBe(1)
    const cards = container.querySelectorAll('.md-card')
    expect(cards[0].getAttribute('data-openable')).toBe('true')
    expect(cards[1].getAttribute('data-openable')).toBeNull()

    // 对照 A：一句话在，正文其余部分**不在**（折叠态不渲染 Markdown）
    expect(container.textContent).toContain('第一段。')
    expect(container.textContent).not.toContain('第二段。')
    // 语义块内容也不在（整块不可见，不是只藏了外壳）
    expect(container.textContent).not.toContain('风险内容')
    expect(container.querySelectorAll('[data-block-type]').length).toBe(0)
  })

  it('点开一张卡 → 出现完整正文（走 MarkerContent，语义块外壳在）与「收起」', () => {
    const { container, getByText } = render(<FoldView variant="topic" content={MD} meta={META} />)
    const first = container.querySelectorAll('.md-card')[0]
    fireEvent.click(first)

    // 对照 B：外壳存在 = 走的是 MarkerContent，不是 MarkdownPreview
    expect(container.querySelectorAll('[data-block-type="alert"]').length).toBe(1)
    expect(container.querySelectorAll('.fold-body .markdown-body').length).toBeGreaterThan(0)
    // 展开后整节正文都在（含折叠时藏起来的第二段）
    expect(container.textContent).toContain('第二段。')
    expect(container.textContent).toContain('风险内容')
    // 标题只出现一次：标题行一直在，body 里不该再画一条同名标题
    expect(container.querySelectorAll('.fold-body h2').length).toBe(0)
    expect(getByText('收起')).toBeTruthy()
    // 「收起」在标题行里（不是正文末尾），且这一节的展开提示同时消失
    expect(container.querySelectorAll('.fold-head .fold-close').length).toBe(1)
    expect(container.querySelectorAll('.fold-hint').length).toBe(0)
    // 只有被点的那一节展开，另一节仍是卡
    expect(container.querySelectorAll('.md-card').length).toBe(2)
  })

  it('点「收起」→ 回到原来的卡片，正文重新收起', () => {
    const { container, getByText } = render(<FoldView variant="topic" content={MD} meta={META} />)
    fireEvent.click(container.querySelectorAll('.md-card')[0])
    fireEvent.click(getByText('收起'))

    expect(container.querySelectorAll('.md-card').length).toBe(2)
    expect(container.querySelectorAll('.fold-close').length).toBe(0)
    expect(container.textContent).not.toContain('第二段。')
  })

  it('只有标题的节不可点（对照 C）', () => {
    const { container } = render(
      <FoldView variant="topic" content={`## 甲\n内容一。\n\n内容二。\n\n## 丙\n`} meta={META} />,
    )
    const cards = container.querySelectorAll('.md-card')
    expect(cards.length).toBe(2)
    expect(cards[0].getAttribute('data-openable')).toBe('true')
    expect(cards[1].getAttribute('data-openable')).toBeNull()
    // 只有标题的节做成整行分隔条，不占一个格子
    expect(cards[1].classList.contains('fold-hollow')).toBe(true)
    expect(cards[0].classList.contains('fold-hollow')).toBe(false)
    // 不可点的节连展开标识都不给 —— 给了就是「看起来能点、点了没反应」
    expect(cards[1].querySelectorAll('.fold-hint').length).toBe(0)
    // 点它什么也不会发生（不变成空展开）
    fireEvent.click(cards[1])
    expect(container.querySelectorAll('.fold-close').length).toBe(0)
  })

  it('引言里的 `# 文档标题` 显示在卡片上，不是一张只有箭头的空卡', () => {
    const { container } = render(
      <FoldView variant="topic" content={`# 标题\n\n## 甲\n第一段。\n\n第二段。\n`} meta={META} />,
    )
    expect(container.querySelectorAll('.md-card').length).toBe(2)
    const pre = container.querySelectorAll('.md-card')[0]
    // 标题可见（此前它被整张卡丢掉，或只剩一个展开箭头）
    expect(pre.textContent).toContain('标题')
    expect(pre.classList.contains('fold-preamble')).toBe(true)
    // 没有正文 → 不绑点击、不给展开标识
    expect(pre.getAttribute('data-openable')).toBeNull()
    expect(pre.querySelectorAll('.fold-hint').length).toBe(0)
  })

  it('引言是引用块 → 一句话来自引用，卡片不空', () => {
    const { container } = render(
      <FoldView variant="topic" content={`> 摘要一句。\n\n## 甲\n第一段。\n\n第二段。\n`} meta={META} />,
    )
    expect(container.textContent).toContain('摘要一句。')
  })

  it('整节只有表格时，卡片上写着里面是什么（不是只剩标题）', () => {
    const { container } = render(
      <FoldView variant="topic" content={`## 甲\n| 列 | 值 |\n|---|---|\n| a | 1 |\n`} meta={META} />,
    )
    expect(container.textContent).toContain('含表格')
    expect(container.querySelectorAll('.fold-lead--kind').length).toBe(1)
  })

  it('无 H2 → 回落整篇（不渲染一片空白）', () => {
    const { container } = render(
      <FoldView variant="topic" content={'只有一段话。\n第二段。\n'} meta={META} />,
    )
    expect(container.querySelectorAll('.md-card').length).toBe(0)
    expect(container.textContent).toContain('只有一段话。')
    expect(container.textContent).toContain('第二段。')
  })
})

describe('FoldView：一句话档', () => {
  it('每节一行，展开后出现完整正文', () => {
    const { container } = render(<FoldView variant="lead" content={MD} meta={META} />)
    const rows = container.querySelectorAll('.fold-row')
    expect(rows.length).toBe(2)
    expect(container.querySelectorAll('.fold-title').length).toBe(2)
    expect(container.textContent).not.toContain('第二段。')

    fireEvent.click(rows[0])
    expect(container.textContent).toContain('第二段。')
    expect(container.querySelectorAll('.fold-close').length).toBe(1)
  })
})

describe('FoldView：全部展开 / 全部收起', () => {
  const MIXED = `## 甲
第一段。

第二段。

## 乙
一句话。
`

  it('expandAll 只展开真会多出东西的节，collapseAll 全部收回', () => {
    const ref = createRef<FoldViewHandle>()
    const { container } = render(<FoldView ref={ref} variant="topic" content={MIXED} meta={META} />)

    act(() => ref.current!.expandAll())
    // 甲有两段 → 展开；乙只有一句话 → 展开前后逐字一样，不展开（对照：按 sections
    // 全展开时这里会是 2，卡片上出现一张「点了像没点」的空展开）
    expect(container.querySelectorAll('.fold-close').length).toBe(1)
    expect(container.textContent).toContain('第二段。')

    act(() => ref.current!.collapseAll())
    expect(container.querySelectorAll('.fold-close').length).toBe(0)
    expect(container.textContent).not.toContain('第二段。')
  })

  it('⚠️ 回传 expandable / expanded —— 按钮文案靠它切，不是靠父组件另存一份', () => {
    const seen: { expandable: number; expanded: number }[] = []
    const cb = (s: { expandable: number; expanded: number }) => { seen.push(s) }
    const ref = createRef<FoldViewHandle>()
    const { container } = render(
      <FoldView ref={ref} variant="topic" content={MIXED} meta={META} onExpandStateChange={cb} />,
    )
    expect(seen[seen.length - 1]).toEqual({ expandable: 1, expanded: 0 })

    act(() => ref.current!.expandAll())
    expect(seen[seen.length - 1]).toEqual({ expandable: 1, expanded: 1 })

    // 手动点开再点收起，同样的数字要跟着回来（否则窄条会停在错误文案上）
    act(() => ref.current!.collapseAll())
    fireEvent.click(container.querySelectorAll('.md-card')[0])
    expect(seen[seen.length - 1]).toEqual({ expandable: 1, expanded: 1 })
  })
})

describe('fold.css', () => {
  const css = readFileSync(resolve(process.cwd(), 'src/styles/fold.css'), 'utf-8')

  it('字号接在 --read-font-size 上（Ctrl+滚轮跟手），不是写死的 px', () => {
    // 对照 D
    expect(css).toContain('font-size: var(--read-font-size)')
  })

  it('网格列宽用 em（字号放大时单卡同步变宽、自动减列）', () => {
    expect(css).toContain('grid-template-columns')
    expect(css).toContain('minmax(max(15em')
  })

  it('零硬编码色值', () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })
})
