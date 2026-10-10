/**
 * 语义标记的渲染容器 —— 「强调块真的渲染出来了、且里面有内容」。
 *
 * ⚠️ 与 `cardSections.test.tsx` 同款：重点是**空壳检测**（项目病史：「渲染成功、
 * 几何全对、断言全过，用户拿到一张一个字都没有的图」）。这里断 `.md-block` 的
 * **数量**与**块内文字真的在**，并补一条反面对照：标记**不**在页面上裸露。
 *
 * ⚠️ **装饰开关是纯 CSS 层的事**（`data-marker-decor` 写 `<html>`，见
 * `lib/marker-prefs.ts`），所以这里不断言「关掉后没有 `.md-block`」—— 那会把实现锁进
 * 「JS 分支不渲染包裹层」，而后端导出侧就得再实现一遍同样的分支。开关的落 DOM 与
 * CSS 规则由 `markerPrefs.test.ts` 钉住，视觉结果由实机验证。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 解析器不跳围栏 → 用户示例代码里的标记被当真标记 → 分段数变多、代码被切碎 → 红。
 *   B. 无标记时也走「多段渲染」而不是快路径 → 渲染树与改造前不同（`.markdown-body`
 *      从 1 个变成 N 个），DOM 结构的零回归被破坏 → 红。
 *   C. 标记行没被剥掉（直接交给 react-markdown）→ 页面上出现一行
 *      `<!-- limo-block:v1 … -->` 的**可见文字** → 红。
 *      这正是用户最初提出的现象。
 *   D. 未闭合时内容被一起吞掉 → 块内文字从页面上消失 → 红。
 *   E. 空段不过滤 → `MarkdownPreview` 对空 content 渲染「加载中...」，
 *      正文中间凭空多一句加载提示 → 红。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { MarkerContent } from '../components/preview/MarkerContent'

const META = { style: 'plain', mode: 'notes', version: 1 }

describe('MarkerContent', () => {
  it('无标记 → 单个 .markdown-body，且渲染树与改造前一致（零回归快路径）', () => {
    // 对照 B：走多段渲染 → .markdown-body 的数量与结构都会变
    const { container } = render(<MarkerContent content={'# 标题\n\n正文\n'} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(0)
    expect(container.querySelectorAll('.markdown-body').length).toBe(1)
    expect(container.textContent).toContain('正文')
  })

  it('单块 → 一个 .md-block，块内文字在（空壳检测）', () => {
    const md = '开头。\n\n<!-- limo-block:v1 type="key-takeaway" -->\n**核心结论**\n\n采用异步任务。\n<!-- /limo-block -->\n\n结尾。'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(1)
    const block = container.querySelector('.md-block')!
    expect(block.getAttribute('data-block-type')).toBe('key-takeaway')
    // ⚠️ 空壳检测：块内**真的有字**，不是一个空 div
    expect(block.textContent).toContain('核心结论')
    expect(block.textContent).toContain('采用异步任务。')
    // 块外的正文也没丢
    expect(container.textContent).toContain('开头。')
    expect(container.textContent).toContain('结尾。')
  })

  it('标记行不出现在页面上（用户最初提出的现象）', () => {
    // 对照 C：不预解析 → react-markdown 把注释渲染成可见文字
    const md = '正文\n<!-- limo-block:v1 type="key-takeaway" -->\n块内\n<!-- /limo-block -->'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.textContent).not.toContain('limo-block')
    expect(container.textContent).not.toContain('<!--')
  })

  it('多块 → 每块一个 .md-block，中间正文各归其位', () => {
    const md = '<!-- limo-block:v1 type="key-takeaway" -->\n甲\n<!-- /limo-block -->\n中间\n<!-- limo-block:v1 type="key-takeaway" -->\n乙\n<!-- /limo-block -->'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(2)
    expect(container.textContent).toContain('甲')
    expect(container.textContent).toContain('中间')
    expect(container.textContent).toContain('乙')
  })

  it('未闭合 → 降级为正文，内容**一字不少**', () => {
    // 对照 D：连内容一起吞 → 「模型少写一行闭标记」= 读者永久看不到那段结论
    const md = '正文一\n\n<!-- limo-block:v1 type="key-takeaway" -->\n块内文字'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(0)
    expect(container.textContent).toContain('正文一')
    expect(container.textContent).toContain('块内文字')
  })

  it('围栏内的假标记不切块，示例代码原样保留', () => {
    // 对照 A：不跳围栏 → 用户贴的示例代码被切碎、代码行跑到块外
    const md = '说明\n\n```\n<!-- limo-block:v1 type="key-takeaway" -->\n```\n'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(0)
    expect(container.textContent).toContain('说明')
    expect(container.querySelector('code')?.textContent).toContain('limo-block')
  })

  it('未知类型照样渲染成一个块（不认识 ≠ 内容消失）', () => {
    const md = '<!-- limo-block:v1 type="warning" -->\n警告内容\n<!-- /limo-block -->'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(1)
    expect(container.querySelector('.md-block')!.getAttribute('data-block-type')).toBe('warning')
    expect(container.textContent).toContain('警告内容')
  })

  it('空块不渲染（否则正文中间会冒出一句「加载中...」）', () => {
    // 对照 E：空段不过滤 → MarkdownPreview 对空 content 渲染加载态
    const md = '前\n\n<!-- limo-block:v1 type="key-takeaway" -->\n<!-- /limo-block -->\n\n后'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('.md-block').length).toBe(0)
    expect(container.textContent).not.toContain('加载中')
    expect(container.textContent).toContain('前')
    expect(container.textContent).toContain('后')
  })

  it('content 为 null → 交给 MarkdownPreview 渲染加载态，不崩', () => {
    const { container } = render(<MarkerContent content={null} meta={null} />)
    expect(container.querySelectorAll('.md-block').length).toBe(0)
    expect(container.textContent).toContain('加载中')
  })
})
