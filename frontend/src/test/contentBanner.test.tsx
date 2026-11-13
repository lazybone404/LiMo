/**
 * 顶部窄条（`components/preview/ContentBanner.tsx`）—— 三个动作槽的**位置**。
 *
 * 为什么值得测位置：这一行的三个动作用途不同（读这篇 / 看原文 / 改这一版），
 * 而 DOM 顺序就是视觉顺序 —— 把折叠动作放到「原链接」右边不会报错、也不会点不动，
 * 只是用户要找的「全部展开」跑到了另一个位置。
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ContentBanner } from '../components/preview/ContentBanner'

const follows = (a: HTMLElement, b: HTMLElement) =>
  (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0

describe('ContentBanner：动作顺序', () => {
  it('折叠动作 → 原链接 → 编辑动作（从左到右）', () => {
    render(
      <ContentBanner
        title="标题"
        sourceUrl="https://example.com/a"
        foldActions={<button type="button">全部展开</button>}
        actions={<button type="button">编辑</button>}
      />,
    )
    const fold = screen.getByText('全部展开')
    const link = screen.getByText('原链接')
    const edit = screen.getByText('编辑')
    expect(follows(fold, link)).toBe(true)
    expect(follows(link, edit)).toBe(true)
  })

  it('没有原链接时，右侧动作组照样渲染（判据是三者任一存在）', () => {
    render(<ContentBanner title="标题" foldActions={<button type="button">全部展开</button>} />)
    expect(screen.getByText('全部展开')).toBeTruthy()
  })

  it('三个都没有 → 不渲染空的右侧动作组', () => {
    const { container } = render(<ContentBanner title="标题" />)
    expect(container.querySelector('.ml-auto')).toBeNull()
  })
})
