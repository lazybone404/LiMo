/**
 * 按指定层级标题切分 Markdown —— 产物骨架轴（`card`）与对话卡片组共用。**纯函数，独立单测**。
 *
 * ⚠️ 与后端 `app/services/markdown_sections.py::split_by_h2` **判据同构**（同一条围栏正则、
 * 同样只认 `##`、同样剥尾部分隔线、同样判 `card` 修饰符）。跨语言必然两份实现，靠
 * `test/fixtures/h2-sections.json` 钉住一致 —— 不靠「写得像」。
 *
 * ⚠️ **层级是参数**（2026-10-11 泛化）：产物侧消费 `level=2`（按 H2 分节），
 * 对话卡片组消费 `level=3`（块内每个 `###` 一张卡）。泛化**刻意做在这里**而不是在对话
 * 组件里另写一遍 —— 围栏感知（代码块里的 `###` 不是标题）只该有一个家，
 * 两条实现漂移的症状是「卡数对不上」，**零报错**（模式 #54）。
 * 后端**不跟**：对话只在前端渲染，多一份 Python 实现才是负担。
 *
 * ⚠️ **`card` 是节修饰符，不是「切分级别」**（2026-10-11 起）：切分**永远按标题层级**
 * 进行（产物侧 = 每个 H2 一个 `Section`，**不因为有没有标记而变**），`card` 只决定
 * 这一节**渲染成什么**（卡片 or 普通正文）。两者分开的理由是下游的消费方式完全不同 ——
 * 导出侧要按节拼 HTML、TOC 要按节列条目，若「没标记的节根本不存在」，两处都得改。
 * 于是判据收在这一个字段里，`Section.card` 之外形状不变。
 *
 * ⚠️ **`heading` 保留原始标题行（含 `#` 前缀），不在这里剥装饰**。
 * 卡片渲染时把 `heading + '\n' + body` 原样交给既有渲染器，于是卡片内的标题
 * **仍然是那条 `<h2>`/`<h3>`** —— badge、锚点 id、TOC 跳转、字号全部与今天逐一致。
 * 若在这里剥成纯文本另画一个标题元素，就等于**新起第二条标题渲染路径**，
 * 而「同一个渲染决策只写在一个地方」正是本轮要守的。
 *
 * 边界：
 *  - 无该级标题 → `{ preamble: md, sections: [] }`（调用方回落长文）；
 *  - 首个标题之前的引言 → `preamble`（单独成块，**不丢弃**）；
 *  - 该级标题内的更深标题 → **留在卡片内**（卡片是容器，不是细分器）。
 */

import { isSectionCard, scanHeadingLines } from './markdown-scan'

/**
 * 分隔线行（CommonMark thematic break：三个以上 `-` / `*` / `_`，可带空白间隔）。
 * ⚠️ 用反向引用 `\1` 而不是把三类字符各写一遍 —— 后者会漏掉混写之外的边角，
 * 且三份规则本身就该是同一份。
 * ⚠️ 这份正则**后端有一份 Python 同构**（`markdown_sections.py`），靠共享 fixture 钉住。
 */
const THEMATIC_BREAK_RE = /^\s{0,3}([-*_])\s*(?:\1\s*){2,}$/

export interface Section {
  /** 原始标题行（**含** `## ` 前缀），卡片内原样拼回 */
  heading: string
  /** 该节正文：标题行之后、下一个 H2 之前的行；已去首尾空行与尾部分隔线 */
  body: string
  /**
   * 这一节是否被 `card` **节修饰符**标记（`<!-- limo-block:v1 type="card" -->`
   * 写在标题行之后）。标记行**已从 `body` 剥掉** —— 它不该流到渲染器。
   * ⚠️ 与后端 `markdown_sections.py::split_by_h2` 的第三元组元素同源（fixture 钉住）。
   */
  card: boolean
}

export interface SplitResult {
  /** 第一个 H2 之前的内容（已去尾部空行）。无 H2 时等于整篇 */
  preamble: string
  sections: Section[]
}

/**
 * 去首尾空行；`stripBreak` 为真时再把**尾部的分隔线行**（及其上方空行）一并去掉。
 *
 * ⚠️ **`stripBreak` 由「这一节是否成卡」决定**（2026-10-11）：分隔线的功能是「把这一节
 * 与下一节分开」，而**成卡的节已经有了卡的下边界**，那条 `---` 就只剩「卡片底部多一条线」。
 * 未标记的节没有卡边界，`---` 仍是它唯一的分隔手段 —— 一并剥掉会让「开了卡片模式，
 * 普通章节之间的分隔线却消失了」，而那是与骨架轴无关的损失。
 */
function trimBody(lines: string[], stripBreak: boolean): string {
  let end = lines.length
  while (end > 0 && lines[end - 1].trim() === '') end--
  if (stripBreak) {
    while (end > 0 && THEMATIC_BREAK_RE.test(lines[end - 1])) {
      end--
      while (end > 0 && lines[end - 1].trim() === '') end--
    }
  }
  let start = 0
  while (start < end && lines[start].trim() === '') start++
  return lines.slice(start, end).join('\n')
}

/** 按 `level` 级标题切分（`level` 1-6）。围栏内与标记块内的同名标题**不算**切点。 */
export function splitByHeading(md: string, level: number): SplitResult {
  // ⚠️ **先整体规范化 CRLF**（`\r\n` 与孤立 `\r`）。只在匹配时剥 `\r` 是不够的：
  // `lines[h.line]` 与 body 的**每一行**都可能带尾 `\r`，症状是卡片正文行尾多一个
  // 不可见字符（渲染上看不出来，fixture 比对上一眼就现形）。
  // 行数不变，故 `scanHeadingLines` 给出的行号在规范化前后一致。
  const src = md.replace(/\r\n?/g, '\n')
  const lines = src.split('\n')
  const marks = [...scanHeadingLines(src, level, level)]
  if (marks.length === 0) return { preamble: src, sections: [] }

  const sections: Section[] = marks.map((h, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].line : lines.length
    let bodyLines = lines.slice(h.line + 1, end)
    // 节修饰符 —— 标题行之后**第一个非空行**是 `card` 标记 → 这一节成卡，并把该行剥掉。
    // ⚠️ 判据**只认「标题之后」**：写在标题**之前**的标记在 `scanHeadingLines` 里对结构
    // 完全透明（`SECTION_CARD_TYPE` 解释了为什么），于是它退化成「这一节不成卡、结构与
    // 内容一字不少」。宽容写在**降级**这一侧（写错位置不丢东西），不写在判据这一侧
    // （判据多一条分支就多一处可能不同源）。
    const at = bodyLines.findIndex(l => l.trim() !== '')
    const card = at >= 0 && isSectionCard(bodyLines[at])
    if (card) bodyLines = [...bodyLines.slice(0, at), ...bodyLines.slice(at + 1)]
    return { heading: lines[h.line], body: trimBody(bodyLines, card), card }
  })

  return { preamble: lines.slice(0, marks[0].line).join('\n').replace(/\s+$/, ''), sections }
}

/** 产物侧切分器（骨架轴 `card`）：按 H2 分节，**由节内的 `card` 修饰符决定哪几节成卡**。 */
export function splitByH2(md: string): SplitResult {
  return splitByHeading(md, 2)
}

/**
 * ⚠️ **为什么尾部分隔线要在这里剥**（而不是渲染时忽略）：
 *
 * `_NOTES_TASK` 第 6 条逐字要求「章节或话题之间用分割线（`---`）分隔」，于是产物里
 * `---` 会出现在**每个 H2 章节的末尾**。切成卡片之后，章节之间的分隔功能已经由
 * **卡片间距**承担，那条 `---` 只剩下「每张卡底部多一条线」这一个效果 —— 是冗余噪音，
 * 不是内容。
 *
 * ⚠️ 放在**切分器**而不是组件里，是因为导出侧（第 4 部分）也要剥：两处各写一遍
 * 就是「同源漂移」的经典入口（模式 #54）。放在这里则前后端天然共用同一条规则，
 * 并被同一份 fixture 钉住。
 *
 * ⚠️ **只剥尾部**：章节**内部**的 `---`（分隔话题）原样保留 —— 那是有意义的排版。
 * ⚠️ **只在 `card` 骨架下发生**：`paper` 走的是完全不调用本函数的渲染路径
 * （见 `PreviewArea` 的分派），故分隔线在长文里一字不少。
 */
