/**
 * 卡片骨架的渲染容器 —— 「卡片真的渲染出来了、且里面有东西」。
 *
 * ⚠️ 这组断言的重点不是「组件挂上了」，而是**空壳检测**。项目有过病史：
 * 「渲染成功、几何全对、断言全过，用户拿到一张一个字都没有的图」。
 * 所以这里断的是 `.md-card` 的**数量**与卡内**标题真的在**，
 * 而不是 `getByText('甲')` 这种「文本在页面某处」——后者在「所有内容挤在一张卡里」
 * 时照样绿。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 切分器不跳围栏 → 代码块里的 `## 假标题` 也切出一张卡 → 数量断言红。
 *   B. 无 H2 时不回落、直接渲染空容器 → 长文**整篇消失**（页面空白，零报错）。
 *   C. 卡片标题另画一个元素、body 里又保留 `## ` → 同一标题渲染两遍（可见的重复）。
 *   D. 把分派写进 `MarkdownPreview` 内部（而非接入点）→ 本文件仍绿，
 *      但 Excel 数据摘要与原始转录会被一起改掉 —— 那个后果由
 *      `PreviewArea` 的接入点约束兜（本文件测不到，故在此注明）。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { CardSections } from '../components/preview/CardSections'

const META = { style: 'plain', mode: 'notes', version: 1 }

describe('CardSections', () => {
  it('多个 H2 → 每个 H2 一张卡，且卡内标题真的在（空壳检测）', () => {
    const { container } = render(
      <CardSections content={'## 甲\n内容一\n\n## 乙\n内容二\n\n## 丙\n内容三\n'} meta={META} />,
    )
    expect(container.querySelectorAll('.md-card').length).toBe(3)
    // ⚠️ 空壳检测：三张卡里各自有 <h2>，不是三个空 section
    expect(container.querySelectorAll('.md-card h2').length).toBe(3)
    expect(container.textContent).toContain('内容一')
    expect(container.textContent).toContain('内容三')
  })

  it('无 H2 → 回落长文：零卡片，但内容**一字不少**', () => {
    // 对照 B：不回落会让整篇消失
    const { container } = render(<CardSections content={'只有一段话\n第二段\n'} meta={META} />)
    expect(container.querySelectorAll('.md-card').length).toBe(0)
    expect(container.textContent).toContain('只有一段话')
    expect(container.textContent).toContain('第二段')
  })

  it('围栏内的 `## 假标题` 不切卡', () => {
    // 对照 A
    const md = '## 真标题\n正文\n\n```\n## 假标题\n```\n'
    const { container } = render(<CardSections content={md} meta={META} />)
    expect(container.querySelectorAll('.md-card').length).toBe(1)
    expect(container.textContent).toContain('真标题')
    // 假标题是代码内容，仍在（说明只是没被当成标题，而不是被删了）
    expect(container.textContent).toContain('假标题')
  })

  it('H2 前的引言不被吞掉', () => {
    const { container } = render(<CardSections content={'开头一段。\n\n## 甲\n内容一\n'} meta={META} />)
    expect(container.querySelectorAll('.md-card').length).toBe(1)
    expect(container.textContent).toContain('开头一段。')
  })

  it('标题只出现一次（不另画标题元素）', () => {
    // 对照 C：body 里保留 `## 甲` + 卡片头另画一个 → 计数变 2
    const { container } = render(<CardSections content={'## 甲\n内容一\n'} meta={META} />)
    const h2 = container.querySelectorAll('.md-card h2')
    expect(h2.length).toBe(1)
    expect(h2[0].textContent).toContain('甲')
  })

  it('content 为 null → 交给 MarkdownPreview 渲染加载态，不崩', () => {
    const { container } = render(<CardSections content={null} meta={null} />)
    expect(container.querySelectorAll('.md-card').length).toBe(0)
    expect(container.textContent).toContain('加载中')
  })
})
