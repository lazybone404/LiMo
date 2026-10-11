/**
 * 卡片骨架的渲染容器 —— 「**被标记的**章节真的渲染成了卡片、且里面有东西」。
 *
 * ⚠️ 2026-10-11 起骨架轴由**标记**驱动：只有带 `card` 节修饰符的 `##` 成卡，其余章节
 * 保持普通正文（用户判「每个 H2 一张卡太简单粗暴」）。所以本文件的每份样本都必须
 * 交代「哪几节标了」—— 用不带标记的样本去数卡片，数出来恒为 0，断言会绿得毫无意义
 * （或者红得毫无意义，看写的是哪一侧）。
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
 *   E. **把 `card` 判据写反 / 忽略**（一律成卡 或 一律不成卡）→ 第一条用例的
 *      「2 张卡 + 未标记那节的结构仍在」成对断言会红。单看数量是分辨不出来的：
 *      「一律成卡」给出 3 张、「一律不成卡」给出 0 张，只有配上 h2 总数才闭环。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { CardSections } from '../components/preview/CardSections'

const META = { style: 'plain', mode: 'notes', version: 1 }

/** 节修饰符 —— 写在 `##` 标题的**下面一行**（不是包裹式，没有闭合标记）。 */
const CARD = '<!-- limo-block:v1 type="card" -->'

describe('CardSections', () => {
  it('被标记的 H2 成卡、未标记的保持普通正文（混排）', () => {
    const md = `## 甲\n${CARD}\n内容一\n\n## 乙\n内容二\n\n## 丙\n${CARD}\n内容三\n`
    const { container } = render(<CardSections content={md} meta={META} />)
    expect(container.querySelectorAll('.md-card').length).toBe(2)
    // ⚠️ 空壳检测：两张卡里各自有 <h2>，不是两个空 section
    expect(container.querySelectorAll('.md-card h2').length).toBe(2)
    // ⚠️ 结构侧成对断言：三节的标题**都**在（未标记的乙节只是没卡，不是被丢了）
    expect(container.querySelectorAll('h2').length).toBe(3)
    for (const t of ['内容一', '内容二', '内容三']) {
      expect(container.textContent).toContain(t)
    }
  })

  it('标记行不落进页面（零残留）—— 节修饰符是内部协议', () => {
    // ⚠️ 这里是**端到端**的观察（页面上没有这个字符串）；真正剥它的位置在解析层
    // （`parseMarkers` 的标记行剥除），精确的断言在 `markdownMarkers.test.ts` 的
    // fixture 用例里。两条一起才闭环 —— 只断这里的话，「ReactMarkdown 把原始 HTML
    // 注释丢弃了」也能让它绿，而下游的 md 导出仍会漏出那一行。
    const md = `## 甲\n${CARD}\n内容一\n`
    const { container } = render(<CardSections content={md} meta={META} />)
    expect(container.textContent).not.toContain('limo-block')
  })

  it('无标记 → 零卡片，内容**一字不少**（与 paper 骨架的观感一致）', () => {
    const { container } = render(
      <CardSections content={'## 甲\n内容一\n\n## 乙\n内容二\n'} meta={META} />,
    )
    expect(container.querySelectorAll('.md-card').length).toBe(0)
    expect(container.querySelectorAll('h2').length).toBe(2)
    expect(container.textContent).toContain('内容一')
    expect(container.textContent).toContain('内容二')
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
    const md = `## 真标题\n${CARD}\n正文\n\n\`\`\`\n## 假标题\n\`\`\`\n`
    const { container } = render(<CardSections content={md} meta={META} />)
    expect(container.querySelectorAll('.md-card').length).toBe(1)
    expect(container.textContent).toContain('真标题')
    // 假标题是代码内容，仍在（说明只是没被当成标题，而不是被删了）
    expect(container.textContent).toContain('假标题')
  })

  it('H2 前的引言不被吞掉', () => {
    const { container } = render(
      <CardSections content={`开头一段。\n\n## 甲\n${CARD}\n内容一\n`} meta={META} />,
    )
    expect(container.querySelectorAll('.md-card').length).toBe(1)
    expect(container.textContent).toContain('开头一段。')
  })

  it('标题只出现一次（不另画标题元素）', () => {
    // 对照 C：body 里保留 `## 甲` + 卡片头另画一个 → 计数变 2
    const { container } = render(
      <CardSections content={`## 甲\n${CARD}\n内容一\n`} meta={META} />,
    )
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
