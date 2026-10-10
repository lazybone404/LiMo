/**
 * 按 H2 切分 Markdown —— 产物骨架轴（`card`）的切分器。**纯函数，独立单测**。
 *
 * ⚠️ 与后端 `app/services/markdown_sections.py::split_by_h2` **判据同构**（同一条围栏正则、
 * 同样只认 `##`、同样剥尾部分隔线）。跨语言必然两份实现，靠
 * `test/fixtures/h2-sections.json` 钉住一致 —— 不靠「写得像」。
 *
 * ⚠️ **`heading` 保留原始标题行（含 `## ` 前缀），不在这里剥装饰**。
 * 卡片渲染时把 `heading + '\n' + body` 原样交给既有 `MarkdownPreview`，于是卡片内的标题
 * **仍然是那条 `<h2>`** —— badge、锚点 id、TOC 跳转、字号全部与今天逐一致。
 * 若在这里剥成纯文本另画一个标题元素，就等于**新起第二条标题渲染路径**，
 * 而「同一个渲染决策只写在一个地方」正是本轮要守的。
 *
 * 边界：
 *  - 无 H2 → `{ preamble: md, sections: [] }`（调用方回落长文）；
 *  - H2 前的引言 → `preamble`（单独成块，**不丢弃**）；
 *  - H2 内的 H3/H4 → **留在卡片内**（卡片是容器，不是细分器；节内二级切分本轮不做）。
 */

import { scanHeadingLines } from './markdown-scan'

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
}

export interface SplitResult {
  /** 第一个 H2 之前的内容（已去尾部空行）。无 H2 时等于整篇 */
  preamble: string
  sections: Section[]
}

/** 去首尾空行；再把**尾部的分隔线行**（及其上方空行）一并去掉 */
function trimBody(lines: string[]): string {
  let end = lines.length
  while (end > 0 && lines[end - 1].trim() === '') end--
  while (end > 0 && THEMATIC_BREAK_RE.test(lines[end - 1])) {
    end--
    while (end > 0 && lines[end - 1].trim() === '') end--
  }
  let start = 0
  while (start < end && lines[start].trim() === '') start++
  return lines.slice(start, end).join('\n')
}

export function splitByH2(md: string): SplitResult {
  // ⚠️ **先整体规范化 CRLF**（`\r\n` 与孤立 `\r`）。只在匹配时剥 `\r` 是不够的：
  // `lines[h.line]` 与 body 的**每一行**都可能带尾 `\r`，症状是卡片正文行尾多一个
  // 不可见字符（渲染上看不出来，fixture 比对上一眼就现形）。
  // 行数不变，故 `scanHeadingLines` 给出的行号在规范化前后一致。
  const src = md.replace(/\r\n?/g, '\n')
  const lines = src.split('\n')
  const marks = [...scanHeadingLines(src, 2, 2)]
  if (marks.length === 0) return { preamble: src, sections: [] }

  const sections: Section[] = marks.map((h, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].line : lines.length
    return { heading: lines[h.line], body: trimBody(lines.slice(h.line + 1, end)) }
  })

  return { preamble: lines.slice(0, marks[0].line).join('\n').replace(/\s+$/, ''), sections }
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
