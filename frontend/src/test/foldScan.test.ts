/**
 * 折叠阅读的扫描层（`lib/fold-scan.ts`）—— 切节、抽一句话、值不值得折叠。
 *
 * ⚠️ 这三件事各有一个「看起来对、其实错」的实现，本文件的每条断言都对着其中一个：
 *
 *   A. **自己写一套标题正则**（`^## `）→ 代码块里、`limo-block` 块里的 `##` 也被当成标题，
 *      一节被切成好几张卡。第 2、3 条 + 末尾的对照实验钉这条。
 *   B. **把一节缩成一句 Markdown 再交渲染器** → 表格散成竖线、围栏失效、单行图片凭空成图。
 *      第 6 条（`leadOf` 输出无 Markdown 残留）钉的是这条口径。
 *   C. **拿标题冒充一句话** / 把表格行、公式行当正文抽出来 → 卡片上出现 `|---|` 或裸 `$`。
 *      第 4 条的内容断言钉这条。
 *
 * 对照实验（第 7 条）是**自证有区分力**的那一条：同一份样本，裸正则数出 3 个标题，
 * `foldSections` 只切出 1 节。把 `foldSections` 换成裸正则，第 2、3 条会红 —— 这里把
 * 两种计数并排断言，就是为了让「守卫真的会红」这件事留在测试里，而不是留在注释里。
 */
import { describe, it, expect } from 'vitest'
import {
  canFold,
  foldSections,
  hasRealContent,
  leadOf,
  stripInlineMd,
} from '../lib/fold-scan'

const ALERT_OPEN = '<!-- limo-block:v1 type="alert" -->'
const ALERT_CLOSE = '<!-- /limo-block -->'

/** 对照实验用：一个「看起来像」的裸实现，只用来数出它会错成什么样 */
const naiveH2Count = (md: string) => md.split('\n').filter(l => l.startsWith('## ')).length

describe('foldSections：切节', () => {
  it('普通文档：节数、标题、id、引言各自正确', () => {
    const md = `开头一段。\n\n## 甲\n甲的内容。\n\n## 乙\n乙的内容。\n`
    const { preamble, sections } = foldSections(md)
    expect(sections.length).toBe(2)
    expect(sections[0].heading).toBe('甲')
    expect(sections[1].heading).toBe('乙')
    // 与 TOC / 渲染侧同一个 headingId（同源），不是本地再算一次
    expect(sections[0].headingId).toBe('甲')
    expect(preamble).not.toBeNull()
    expect(preamble!.lead).toBe('开头一段。')
    // 无 H2 的引言没有标题 —— 渲染层只画一句话，不拿标题冒充
    expect(preamble!.heading).toBe('')
  })

  it('展开用的 body 带回原始标题行（交给 MarkerContent 后仍是那条 <h2>）', () => {
    const { sections } = foldSections(`## 甲\n甲的内容。\n`)
    expect(sections[0].body.startsWith('## 甲')).toBe(true)
    expect(sections[0].body).toContain('甲的内容。')
  })

  it('围栏内的 `## ` 不切节（对照 A）', () => {
    const md = `## 真标题\n正文。\n\n\`\`\`python\n## 假标题\nprint(1)\n\`\`\`\n`
    const { sections } = foldSections(md)
    expect(sections.length).toBe(1)
    expect(sections[0].heading).toBe('真标题')
    // 假标题是代码内容：既没被当成标题，也没被删
    expect(sections[0].body).toContain('## 假标题')
  })

  it('limo-block 块内的 `## ` 不切节（对照 A）', () => {
    const md = `## 真标题\n正文。\n\n${ALERT_OPEN}\n## 块内假标题\n警告内容\n${ALERT_CLOSE}\n`
    const { sections } = foldSections(md)
    expect(sections.length).toBe(1)
    expect(sections[0].body).toContain('块内假标题')
  })

  it('同名标题的 key 不撞（渲染层 set 的键）', () => {
    const { sections } = foldSections(`## 后果\n甲。\n\n## 后果\n乙。\n`)
    expect(sections.length).toBe(2)
    expect(sections[0].key).not.toBe(sections[1].key)
  })

  it('无 H2 → 零节、整篇进引言（不丢内容）', () => {
    const { preamble, sections } = foldSections('只有一段话。\n第二段。\n')
    expect(sections.length).toBe(0)
    expect(preamble!.body).toContain('只有一段话。')
  })

  it('对照实验：裸正则数出 3 个标题，foldSections 只切 1 节', () => {
    const md = `## 真标题\n正文。\n\n\`\`\`\n## 围栏里的\n\`\`\`\n\n${ALERT_OPEN}\n## 块里的\n${ALERT_CLOSE}\n`
    // 这两个数并排放着才是「守卫有区分力」的证据：换掉 foldSections，二者会相等
    expect(naiveH2Count(md)).toBe(3)
    expect(foldSections(md).sections.length).toBe(1)
  })
})

describe('expandable：展开后会不会看到新东西', () => {
  const sec = (body: string) => foldSections(`## 甲\n${body}\n`).sections[0]

  it('只有标题 → 无正文、不可展开', () => {
    const s = sec('')
    expect(s.hasBody).toBe(false)
    expect(s.expandable).toBe(false)
  })

  it('一句纯文本 → 有正文但展开前后一样，不可展开', () => {
    const s = sec('就这一句话。')
    expect(s.hasBody).toBe(true)
    expect(s.expandable).toBe(false)
  })

  it('单行里含链接 / 加粗 / 行内代码 → 可展开（折叠态把它们剥成纯文本了）', () => {
    expect(sec('看 [这个](https://x)。').expandable).toBe(true)
    expect(sec('**加粗**的一句。').expandable).toBe(true)
    expect(sec('含 `代码` 的一句。').expandable).toBe(true)
    expect(sec('含公式 $x$ 的一句。').expandable).toBe(true)
  })

  it('单段被截断（超 120 字）→ 可展开（折叠态只显示前半句）', () => {
    expect(sec(`${'甲'.repeat(121)}。`).expandable).toBe(true)
  })

  it('多行 / 列表 / 表格 / 围栏 / 语义块 → 可展开', () => {
    expect(sec('第一段。\n\n第二段。').expandable).toBe(true)
    expect(sec('- 一项\n- 两项').expandable).toBe(true)
    expect(sec('| 甲 | 乙 |\n|---|---|\n| 1 | 2 |').expandable).toBe(true)
    expect(sec('```\ncode\n```').expandable).toBe(true)
    expect(sec(`${ALERT_OPEN}\n警告内容\n${ALERT_CLOSE}`).expandable).toBe(true)
  })
})

describe('hint：抽不出一句话时说明里面是什么', () => {
  const sec = (body: string) => foldSections(`## 甲\n${body}\n`).sections[0]

  it('有可读句子时为空（不与一句话重复）', () => {
    expect(sec('正文一句。').hint).toBe('')
  })

  it('整节是表格 / 图片 / 公式 / 代码时给出类型', () => {
    expect(sec('| 甲 | 乙 |\n|---|---|\n| 1 | 2 |').hint).toBe('含表格')
    expect(sec('![图](/a.png)').hint).toBe('含图片')
    expect(sec('$$x^2$$').hint).toBe('含公式')
    expect(sec('```py\nprint(1)\n```').hint).toBe('含代码')
  })

  it('多种并存时按固定顺序列出', () => {
    expect(sec('![图](/a.png)\n\n| 甲 |\n|---|\n| 1 |').hint).toBe('含图片、表格')
  })

  it('代码块里的 `|` 不被当成表格', () => {
    expect(sec('```\n| a | b |\n```').hint).toBe('含代码')
  })

  it('只有标题 → 没有 hint（那一档走整行分隔条）', () => {
    expect(sec('').hint).toBe('')
  })

  it('有内容但既抽不出一句话、也认不出类型 → 兜底「展开查看」', () => {
    // 整段只有一个行内公式：句子被跳过，类型也认不出（`$$` 才算公式块）
    const s = sec('$x^2$')
    expect(s.hasBody).toBe(true)
    expect(s.lead).toBe('')
    expect(s.hint).toBe('展开查看')
  })
})

describe('hasRealContent：除标题外还有看得见的东西吗', () => {
  it('正文、列表、表格、图片、围栏里的代码都算内容', () => {
    expect(hasRealContent('一句话。')).toBe(true)
    expect(hasRealContent('- 一项')).toBe(true)
    expect(hasRealContent('| 甲 | 乙 |\n|---|---|')).toBe(true)
    expect(hasRealContent('![图](/a.png)')).toBe(true)
    expect(hasRealContent('```\ncode\n```')).toBe(true)
    expect(hasRealContent(`${ALERT_OPEN}\n警告\n${ALERT_CLOSE}`)).toBe(true)
  })

  it('标题行、标记行、注释、分隔线、空行都不算', () => {
    expect(hasRealContent('')).toBe(false)
    expect(hasRealContent('   \n\n')).toBe(false)
    expect(hasRealContent('# 一级标题')).toBe(false)
    expect(hasRealContent('### 三级标题\n')).toBe(false)
    expect(hasRealContent(`${ALERT_OPEN}\n${ALERT_CLOSE}`)).toBe(false)
    expect(hasRealContent('<!-- 一句注释 -->')).toBe(false)
    expect(hasRealContent('<!--\n跨行注释\n-->')).toBe(false)
    expect(hasRealContent('---')).toBe(false)
    expect(hasRealContent('# 标题\n\n---\n<!-- 注释 -->')).toBe(false)
  })

  it('对照：拿 `trim()` 判会把「只有标题」当有内容（这正是空卡的来源）', () => {
    const onlyHeading = '## 丙\n'
    expect(onlyHeading.trim() !== '').toBe(true)
    expect(hasRealContent(onlyHeading)).toBe(false)
  })
})

describe('foldSections：引言里只剩标题时整张卡不渲染', () => {
  it('只有 `# 一级标题` 的引言 → null（不生成一张什么都没有的卡）', () => {
    expect(foldSections('# 标题\n\n## 甲\n甲的内容。\n').preamble).toBeNull()
  })

  it('只有注释 / 标记行的引言 → null', () => {
    expect(foldSections('<!-- limo-meta -->\n\n## 甲\n甲的内容。\n').preamble).toBeNull()
  })

  it('引用块写成的引言 → 保留，一句话来自引用', () => {
    const p = foldSections('> 摘要一句。\n\n## 甲\n甲的内容。\n').preamble
    expect(p).not.toBeNull()
    expect(p!.lead).toBe('摘要一句。')
  })

  it('只有注释 / 分隔线的节 → 无正文（走整行分隔条，不是可点空卡）', () => {
    const s = foldSections('## 甲\n<!-- 注释 -->\n\n---\n').sections[0]
    expect(s.hasBody).toBe(false)
    expect(s.hint).toBe('')
  })
})

describe('leadOf：一句话', () => {
  it('段落优先于列表', () => {
    expect(leadOf('正文一句。\n\n- 列表项\n')).toBe('正文一句。')
  })

  it('无段落时取第一个列表项', () => {
    expect(leadOf('- 第一项\n- 第二项\n')).toBe('第一项')
  })

  it('跳过表格、围栏、分隔线', () => {
    const body = '| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n\n---\n\n```\ncode\n```\n'
    expect(leadOf(body)).toBe('')
  })

  it('没有可用段落时取引用块里的句子（引言常常就是一段 `>` 摘要）', () => {
    expect(leadOf('> 这是一段摘要。\n>\n> 第二段。\n')).toBe('这是一段摘要。')
    // 段落优先于引用
    expect(leadOf('正文一句。\n\n> 引用一句。\n')).toBe('正文一句。')
    // 只有 `>` 的空引用不算
    expect(leadOf('>\n> \n')).toBe('')
  })

  it('跨行 HTML 注释里的话不被抽出来', () => {
    expect(leadOf('<!--\n注释里的一句话。\n-->\n正文一句。')).toBe('正文一句。')
  })

  it('图片段落整段跳过，取下一段的句子', () => {
    expect(leadOf('![图](/api/assets/x.png)\n\n正文一句。第二句。\n')).toBe('正文一句。')
  })

  it('含公式的句子整句跳过（不留裸 `$`）', () => {
    expect(leadOf('含公式 $x^2$ 的句子。这是干净的一句。\n')).toBe('这是干净的一句。')
  })

  it('超长句截断并加省略号', () => {
    const s = leadOf(`${'甲'.repeat(200)}。\n`)
    expect(s.length).toBeLessThanOrEqual(121)
    expect(s.endsWith('…')).toBe(true)
  })

  it('只有标题与标记行 → 空串（不拿标题冒充一句话）', () => {
    expect(leadOf(`\n${ALERT_OPEN}\n块内容\n${ALERT_CLOSE}\n`)).toBe('')
  })

  it('输出里没有任何 Markdown 残留（对照 B）', () => {
    const body = [
      `## 甲`,
      `![图](/api/assets/x.png)`,
      ``,
      `**加粗**的段落，含 \`代码\` 与 [链接](https://example.com)。`,
      ``,
      `| 列 | 值 |`,
      `|---|---|`,
      `| a | 1 |`,
    ].join('\n')
    const s = leadOf(body)
    expect(s).toBe('加粗的段落，含 代码 与 链接。')
    for (const bad of ['![', '/api/assets/', '$', '**', '`', '](', '|---']) {
      expect(s).not.toContain(bad)
    }
    expect(s.startsWith('- ')).toBe(false)
    expect(s.startsWith('|')).toBe(false)
  })
})

describe('stripInlineMd', () => {
  it('图片整段移除（不保留 alt），链接保留文字', () => {
    expect(stripInlineMd('前 ![图](/a.png) 后 [文字](https://x)')).toBe('前 后 文字')
  })

  it('剥掉加粗 / 删除线 / 反引号 / HTML 注释 / 行内标签', () => {
    expect(stripInlineMd('<!-- c -->**甲**~~乙~~`丙`<b>丁</b>')).toBe('甲乙丙丁')
  })
})

describe('canFold：入口判据', () => {
  it('有 H2 → 能折叠，与字数无关（两句话的产物照样有按钮）', () => {
    expect(canFold('## 甲\n一句话。\n\n## 乙\n另一句。\n')).toBe(true)
    expect(canFold(`## 标题\n${'字'.repeat(50)}`)).toBe(true)
  })

  it('没有 H2 → 不能折叠（切不出节，按钮点了看不出变化）', () => {
    expect(canFold('只有一段话。\n第二段。\n')).toBe(false)
    // 围栏内的 `##` 不算标题
    expect(canFold('```\n## 假标题\n```\n')).toBe(false)
    // 块内的 `##` 也不算
    expect(canFold(`${ALERT_OPEN}\n## 块里的\n${ALERT_CLOSE}\n`)).toBe(false)
  })

  it('null / 空串 → false', () => {
    expect(canFold(null)).toBe(false)
    expect(canFold('')).toBe(false)
  })
})
