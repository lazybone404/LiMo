/**
 * 产物侧样式的**形态**守卫（2026-10-17，批 2）。
 *
 * 与 `chatCardShape.test.ts` 成对：那边管对话侧（不进导出），这边管**产物侧**
 * （`blocks.css` / `cards.css` 是导出 HTML / PDF / Word 的**单一真相源**，
 * 后端 `_load_block_css()` / `_load_card_css()` 从磁盘读并内联）。
 *
 * 能钉的仍然只有「规则在不在、写成什么样」；观感靠实机与导出对照。
 *
 * ⚠️ 读 CSS 前剥注释：注释里**故意**写着 `--tk-success`（说明「由它改成 accent」）
 * 与 `auto-fit` 这类「别这么写」的词，不剥的话断言测的是注释措辞。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), 'utf-8')
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

const BLOCKS = stripComments(read('src/styles/blocks.css'))
const CARDS = stripComments(read('src/styles/cards.css'))

/** 取某条规则的声明体（块内无嵌套 `}`，够用） */
function ruleBody(css: string, selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const m = css.match(new RegExp(`${esc}\\s*\\{([\\s\\S]*?)\\}`))
  expect(m, `规则不存在：${selector}`).not.toBeNull()
  return m![1]
}

describe('metric 指标数字 —— 随主题 accent', () => {
  const body = ruleBody(BLOCKS, '.md-block[data-block-type="metric"] h3 + p')

  it('用 `--tk-accent`，不再写死 success', () => {
    // 写死 success 的失效形态：换任何一套 accent，指标数字永远是同一块孤立的绿，
    // 而用户以为「指标卡没跟着主题走」是 bug 却找不到配置项。
    expect(body).toContain('var(--tk-accent)')
    expect(body).not.toContain('--tk-success')
  })

  it('`tabular-nums`（多指标纵向排列时数字等宽对齐）', () => {
    expect(body).toContain('font-variant-numeric: tabular-nums')
  })

  it('没有引入 `--tk-metric` 这类新令牌（导出侧不认识的令牌会静默落空）', () => {
    expect(BLOCKS).not.toContain('--tk-metric')
  })
})

describe('内容卡片 —— 宽表与大图', () => {
  it('宽表在块级容器横向滚动（导出侧是裸 `<table>`，没有预览的 wrapper）', () => {
    // ⚠️ 同一选择器有多条规则（本体 / 首尾 margin / 这条），不能用 ruleBody 取第一条
    // —— 那会把「这条在不在」测成「它是不是第一条」。
    expect(BLOCKS).toMatch(/\.md-block\[data-block-type="table"\]\s*\{[^}]*overflow-x: auto/)
  })

  it('横向溢出容器必须 `flex-shrink: 0`（否则被预览区的 flex column 压扁）', () => {
    // ⚠️ 对照实验：删掉 `flex-shrink: 0`（`overflow-x: auto` 留着）→ 红。
    // 失效形态（2026-10-18 实测）：预览区内容容器是 `flex flex-col overflow-y-scroll`，
    // 而 overflow 非 visible 的 flex item 自动最小尺寸按 **0** 算 —— 表格块被压到几行高、
    // 内部出现纵向滚动条。内容完整、零报错，只是读者「看不全还得多滚一次」。
    // 与上一条成对：`overflow-x` 是「宽表别撑破版心」，这一条是「别把自己缩没了」。
    expect(BLOCKS).toMatch(/\.md-block\[data-block-type="table"\]\s*\{[^}]*flex-shrink: 0/)
  })

  it('首列 sticky，且 `td` 自带不透明底色（否则滚动时叠字）', () => {
    expect(BLOCKS).toContain('.md-block[data-block-type="table"] thead th:first-child')
    expect(BLOCKS).toContain('.md-block[data-block-type="table"] tbody td:first-child')
    // 组合规则跨两行（`th,` 换行 `td`）——用宽松正则，别把文件换行符（LF/CRLF）
    // 写进断言：那会让这条守卫在另一台机器上因换行风格不同而红。
    expect(BLOCKS).toMatch(
      /\.md-block\[data-block-type="table"\][^{]*first-child[^{]*\{[^}]*position: sticky[^}]*left: 0/,
    )
    // 同样按「存在一条带 background 的规则」判，不取第一条（第一条是 th,td 组合规则）
    expect(BLOCKS).toMatch(
      /\.md-block\[data-block-type="table"\] tbody td:first-child\s*\{[^}]*background: var\(--tk-elevated\)/,
    )
  })

  it('任何 `.md-block` 本体上的非 visible overflow 都必须配 `flex-shrink: 0`', () => {
    // 上一条只钉 table；这条是它的**通用版**，防止将来给别的块加 `overflow` 时漏配套。
    // 判据：非 visible 的 overflow 让该盒的自动最小尺寸按 **0** 解析 ⇒ 在预览区的
    // `flex flex-col` 里可被压到 0。2026-10-18 实测（600px 容器 / 24 行正文 + 5 行表格）：
    //   修复后   装饰开 268px / 装饰关 242px（完整）
    //   去掉这条 装饰开  26px（只剩 padding+border，被读成「很小的一行」）
    //            装饰关   0px（padding 一并归零 ⇒ 整块消失，内容仍在 scrollHeight 里）
    const bodies = [...BLOCKS.matchAll(/(?:^|\n)\s*\.md-block\[data-block-type="[a-z-]+"\]\s*\{([^}]*)\}/g)]
    const risky = bodies.filter((m) => /overflow(?:-[xy])?:\s*(?:auto|scroll|hidden)/.test(m[1]))
    expect(risky.length, '一个溢出容器都没找到，正则或文件结构变了').toBeGreaterThan(0)
    for (const m of risky) {
      expect(m[1], `规则缺 flex-shrink: 0：${m[0].trim().split('\n')[0]}`).toContain('flex-shrink: 0')
    }
  })

  it('大图限高且等比缩放（长截图不把一张卡拉成几个屏幕）', () => {
    const body = ruleBody(BLOCKS, '.md-block[data-block-type="image"] img')
    expect(body).toContain('max-height: 70vh')
    expect(body).toContain('width: auto')
    expect(body).toContain('max-width: 100%')
  })
})

describe('内容块装饰开关 —— 只收装饰，绝不吞内容', () => {
  // 开关语义（`lib/marker-prefs.ts` 文件头）是「隐藏标记产生的附加物」，内容一律保留。
  // 归零只允许改 margin / padding / border / border-radius / background / 伪元素 content。
  const offBodies = [...BLOCKS.matchAll(/\[data-marker-decor="off"\][^{]*\{([^}]*)\}/g)].map((m) => m[1])

  it('关装饰规则里不得出现 `display: none`', () => {
    // 用 display:none 实现归零 = 「关掉装饰后整块消失」，正是 2026-10-18 用户报告的现象
    // （那一例的真实成因是 flex 压到 0，见上面通用守卫；这条挡住下一次用更直白的方式复现）。
    expect(offBodies.length, '一条关装饰规则都没有，判据失效').toBeGreaterThan(5)
    for (const body of offBodies) expect(body).not.toMatch(/display:\s*none/)
  })

  it('关装饰规则不得改 `visibility` / `opacity` / `height`（同样会隐形）', () => {
    // `height: 0` 与 `display: none` 对读者是同一件事；`opacity: 0` 更糟（还在占位）。
    for (const body of offBodies) {
      expect(body).not.toMatch(/visibility:\s*hidden/)
      expect(body).not.toMatch(/(?:^|;)\s*opacity:\s*0\b/)
      expect(body).not.toMatch(/(?:^|;)\s*height:\s*0(?:px)?\b/)
    }
  })
})

describe('章节卡片 —— 长串与标题', () => {
  it('`.md-card` 有 `overflow-wrap`（长 URL 不撑破整节卡）', () => {
    const body = ruleBody(CARDS, '[data-skeleton="card"] .md-card')
    expect(body).toContain('overflow-wrap: break-word')
  })

  it('卡片标题 `text-wrap: balance`（两行标题断得均匀）', () => {
    expect(CARDS).toMatch(/\[data-skeleton="card"\] \.md-card h3[^{]*\{[^}]*text-wrap: balance/)
  })

  it('两条健壮性都**不**加篇幅变体（章节卡片由 `--card-padding` 与 8 套方案控制）', () => {
    // 给 `.md-card` 叠 `:has()` 篇幅规则会与骨架轴/方案变量打架，而它不存在
    // 「组内一张特别长」的相对关系（它本来就是一整节）。
    expect(CARDS).not.toMatch(/\[data-skeleton="card"\] \.md-card:has\(/)
  })
})
