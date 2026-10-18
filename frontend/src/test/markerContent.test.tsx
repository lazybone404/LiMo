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

  it('五种语义强调块：alert / recommend / metric / steps / timeline 都渲染成带 data-block-type 的块', () => {
    // ⚠️ 表驱动而非复制五遍：这五种在**渲染层完全没有分支**（形态差异全在 blocks.css），
    // 于是唯一的证据就是「标记被识别成块 + type 属性写对了 + 块内文字还在」。重复五遍
    // 只是噪音，真正会坏的两种情形（漏进白名单 / 解析器按 type 分派）这两种写法一样能抓。
    const cases = [
      { type: 'alert', body: '**风险**\n\n并发写入时会丢数据。', has: ['风险', '并发写入时会丢数据。'] },
      { type: 'recommend', body: '**推荐方案**：用 A。', has: ['推荐方案', '用 A。'] },
      { type: 'metric', body: '### 已处理文档\n128 份', has: ['已处理文档', '128 份'] },
      { type: 'steps', body: '- 输入\n- 清洗\n- 输出', has: ['输入', '清洗', '输出'] },
      { type: 'timeline', body: '1. **导入**\n2. **解析**', has: ['导入', '解析'] },
    ]
    for (const c of cases) {
      const md = `开头。\n\n<!-- limo-block:v1 type="${c.type}" -->\n${c.body}\n<!-- /limo-block -->\n\n结尾。`
      const { container } = render(<MarkerContent content={md} meta={META} />)
      expect(container.querySelectorAll('.md-block').length, c.type).toBe(1)
      const block = container.querySelector('.md-block')!
      expect(block.getAttribute('data-block-type'), c.type).toBe(c.type)
      for (const s of c.has) expect(block.textContent, `${c.type}: ${s}`).toContain(s)
      // 块外正文也没丢
      expect(container.textContent).toContain('开头。')
      expect(container.textContent).toContain('结尾。')
    }
  })

  it('metric：`### 名称` 与紧随其后的数字渲染成相邻的 h3 与 p（CSS 判据依赖的结构）', () => {
    // ⚠️ `blocks.css` 里 metric 的形态靠 **`h3 + p` 相邻兄弟选择器**把数字放大。
    // 那是 CSS 层的事、测试看不到效果，但**它依赖的 DOM 结构**必须在这里钉住：
    // 哪天 MarkdownPreview 换了渲染器让 `### ` 不再产出 `<h3>`，样式会**静默失效**
    // （块还在、文字还在、只是不再是「小标签 + 大数字」），没有任何断言会红。
    const md = '<!-- limo-block:v1 type="metric" -->\n### 已处理文档\n128 份\n<!-- /limo-block -->'
    const { container } = render(<MarkerContent content={md} meta={META} />)
    const h3 = container.querySelector('.md-block h3')
    expect(h3, '`### 名称` 没有渲染成 h3 —— metric 的 CSS 从此静默失效').not.toBeNull()
    expect(h3!.nextElementSibling?.tagName).toBe('P')
    expect(h3!.nextElementSibling?.textContent).toContain('128 份')
  })

  it('steps / timeline：块内列表类型是 CSS 判据依赖的结构（<ul> vs <ol>）', () => {
    // ⚠️ 这两个 type 的**形状差异完全落在块内子元素类型上**（`blocks.css` 里用
    // `.md-block[data-block-type="steps"] ul` / `… timeline"] ol` 选择器区分），
    // 渲染层一行分支都没有。故这里钉住的是**它们所依赖的 DOM 结构** ——
    // 哪天 MarkdownPreview 换了渲染器让 `- ` / `1. ` 不再产出 `<ul>` / `<ol>`，
    // 样式会**静默失效**（块还在、文字还在、只是不再是步骤条/时间线的样子），
    // 上面那条表驱动断言会全绿地放过它。
    const cases = [
      { type: 'steps', body: '- 输入\n- 清洗', want: 'UL', not: 'OL' },
      { type: 'timeline', body: '1. **导入**\n2. **解析**', want: 'OL', not: 'UL' },
    ]
    for (const c of cases) {
      const md = `<!-- limo-block:v1 type="${c.type}" -->\n${c.body}\n<!-- /limo-block -->`
      const { container } = render(<MarkerContent content={md} meta={META} />)
      const block = container.querySelector('.md-block')!
      expect(block.querySelector(c.want), `${c.type} 块内应有 <${c.want.toLowerCase()}>`).not.toBeNull()
      // ⚠️ 反面对照放在**块内**：`querySelector` 整页找会命中无关元素，
      // 而「两者都渲染成同一个标签」正是这条要挡的失效（只断 want 在场是抓不到的）。
      expect(block.querySelector(c.not), `${c.type} 块内不该有 <${c.not.toLowerCase()}>`).toBeNull()
    }
  })

  it('内容卡片三种：块内那份富内容原样穿过渲染层（真表格 / 真代码 / 真图片）', () => {
    // ⚠️ 这三个的整个价值就是「内容还是原来那份内容」—— 表格必须仍是 `<table>`
    // （列对齐、斑马纹、横向滚动），代码必须仍是 `<pre><code>`（等宽、缩进、复制
    // 按钮），图片必须仍是 `<img>`。若哪一步把它们压成纯文本，上面那条包装断言
    // 照样绿（壳还在、字也在，只是不再是一张表 / 一段代码 / 一张图）。
    // ⚠️ 也不能断 `textContent` 含图片说明 —— `alt` 是属性、不进 textContent。
    const cases = [
      { type: 'table', body: '| 方案 | 成本 |\n| --- | --- |\n| A | 低 |', sel: 'table' },
      { type: 'code', body: '```python\ncfg = load()\n```', sel: 'pre code' },
      { type: 'image', body: '![架构图](assets/arch.png)', sel: 'img' },
    ]
    for (const c of cases) {
      const md = `<!-- limo-block:v1 type="${c.type}" -->\n${c.body}\n<!-- /limo-block -->`
      const { container } = render(<MarkerContent content={md} meta={META} />)
      const block = container.querySelector(`.md-block[data-block-type="${c.type}"]`)
      expect(block, `${c.type} 没有渲染成带类型的块`).not.toBeNull()
      expect(block!.querySelector(c.sel), `${c.type} 块内的 ${c.sel} 丢了`).not.toBeNull()
    }
  })

  it('content 为 null → 交给 MarkdownPreview 渲染加载态，不崩', () => {
    const { container } = render(<MarkerContent content={null} meta={null} />)
    expect(container.querySelectorAll('.md-block').length).toBe(0)
    expect(container.textContent).toContain('加载中')
  })
})

/**
 * 证据块（`evidence`，2026-10-18，多篇合成专用）—— 唯一一个在**预览里有结构**而不只是
 * CSS 外壳的产物侧块：折叠本身就是语义（用户拍板「正文流畅优先，依据可展开」）。
 *
 * ⚠️ 与其余九种的关键差别：它们的失效是「样式没生效」，证据块的失效是**内容看不见**
 * 或**编号错位**。两条都必须断在这里（编号由导出侧的 `linearizeEvidence` 生成，
 * 预览侧**刻意不编号** —— `CardSections` 对每节各调一次本组件，组件内部按局部顺序
 * 编号会跨节重号）。
 */
describe('MarkerContent — 证据块（合成专用）', () => {
  const EV = (body: string) => `<!-- limo-block:v1 type="evidence" -->\n${body}\n<!-- /limo-block -->`

  it('渲染成 <details>，且块内内容**真的在 DOM 里**（折叠 ≠ 内容缺席）', () => {
    // ⚠️ 这是本类型的空壳检测：`<details>` 没展开时视觉上是收起的，若实现不小心
    // 把 body 挂到别处（或只在 open 时渲染），页面能显示、DOM 里却没有内容 ——
    // 而「折叠卡里有东西」正是这个形态存在的全部意义。
    const md = `论断 [文档 2]。\n\n${EV('- 样本：N=120\n- 局限：单一中心')}\n`
    const { container } = render(<MarkerContent content={md} meta={META} />)
    const details = container.querySelector('details.md-evidence')
    expect(details, '证据块没有渲染成 <details>').not.toBeNull()
    expect(details!.getAttribute('data-block-type')).toBe('evidence')
    const body = details!.querySelector('.md-evidence-body')
    expect(body, '缺少 .md-evidence-body').not.toBeNull()
    expect(body!.textContent).toContain('样本：N=120')
    expect(body!.textContent).toContain('局限：单一中心')
    // 块外正文一个字不少
    expect(container.textContent).toContain('论断 [文档 2]。')
  })

  it('summary 显示「依据 · 文档 N」但**不含序号**（编号只在导出侧生成）', () => {
    const md = `论断 [文档 2]。\n\n${EV('- 样本：N=120')}\n`
    const { container } = render(<MarkerContent content={md} meta={META} />)
    const summary = container.querySelector('.md-evidence-summary')!
    expect(summary.textContent).toMatch(/^依据/)
    expect(summary.textContent).toContain('文档 2')   // 来源取自块前的正文段落
    // ⚠️ 断言「没有 `证据 N`」而不是「没有数字」—— 来源标注本身就带数字（文档 2）。
    expect(summary.textContent).not.toMatch(/证据\s*\d/)
  })

  it('来源写在块内时优先用块内的锚点', () => {
    const md = `论断。\n\n${EV('- 来自 [文档 3] 的访谈')}\n`
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelector('.md-evidence-summary')!.textContent).toContain('文档 3')
  })

  it('两侧都提不到来源时 summary 只剩「依据」（不伪造来源）', () => {
    const md = `${EV('- 样本：N=120')}\n`
    const { container } = render(<MarkerContent content={md} meta={META} />)
    const summary = container.querySelector('.md-evidence-summary')!
    expect(summary.textContent!.trim()).toBe('依据')
  })

  it('两个相邻证据块：第二个不继承前一个的依据（前一段不是正文）', () => {
    // ⚠️ 与后端 fixture 的 `two-adjacent` 对应。若实现成「往前找第一个含锚点的段」，
    // 第二个块会顶着第一个块的来源 —— 读者会以为两条证据出自同一篇文档。
    const md = `论断 [文档 2]。\n\n${EV('甲')}\n\n${EV('乙')}\n`
    const { container } = render(<MarkerContent content={md} meta={META} />)
    const summaries = [...container.querySelectorAll('.md-evidence-summary')].map(e => e.textContent!.trim())
    expect(summaries[0]).toContain('文档 2')
    expect(summaries[1]).toBe('依据')
  })

  it('evidence 与普通强调块共存 —— 各渲染成各自形态，内容都在', () => {
    const md = `${EV('样本：N=40')}\n\n<!-- limo-block:v1 type="key-takeaway" -->\n重点\n<!-- /limo-block -->\n`
    const { container } = render(<MarkerContent content={md} meta={META} />)
    expect(container.querySelectorAll('details.md-evidence').length).toBe(1)
    expect(container.querySelectorAll('div.md-block[data-block-type="key-takeaway"]').length).toBe(1)
    expect(container.textContent).toContain('样本：N=40')
    expect(container.textContent).toContain('重点')
  })

  it('装饰开关关掉时，内容与折叠结构仍在 DOM（开关是纯 CSS 层，不在 JS 里藏内容）', () => {
    // ⚠️ 与文件头那条同款：`data-marker-decor="off"` 由 `blocks.css` 消费，组件**不读**它。
    // 若哪天有人为了「省掉箭头」在 JS 里改成条件渲染，后端导出侧就得再实现一遍，
    // 且「关掉装饰 = 内容少一点」是产品上不能接受的（关掉的只是装饰）。
    document.documentElement.setAttribute('data-marker-decor', 'off')
    try {
      const md = `论断 [文档 2]。\n\n${EV('- 样本：N=120')}\n`
      const { container } = render(<MarkerContent content={md} meta={META} />)
      expect(container.querySelector('details.md-evidence')).not.toBeNull()
      expect(container.querySelector('.md-evidence-body')!.textContent).toContain('样本：N=120')
    } finally {
      document.documentElement.removeAttribute('data-marker-decor')
    }
  })

  it('没有标记的文档不会凭空出现证据卡', () => {
    const { container } = render(<MarkerContent content={'# 标题\n\n普通正文。\n'} meta={META} />)
    expect(container.querySelectorAll('.md-evidence').length).toBe(0)
  })
})
