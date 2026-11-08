/**
 * 折叠阅读的扫描层 —— 切节、抽一句话、判断值不值得折叠。**纯函数、零 React、零 store**。
 *
 * ## 它解决什么
 *
 * 产物动辄几千字，长文只能一路滚。折叠阅读把它压成三档：主题（卡片网格）/ 一句话
 * （每节一句）/ 全文。本模块负责前两档需要的**数据**，渲染在
 * `components/preview/FoldView.tsx`。
 *
 * ## 三条判据，各自只有一个家
 *
 * - **切节**：直接用 `splitByH2`（它底下是 `scanHeadingLines` —— 围栏内与 `limo-block`
 *   块内的 `##` 不算标题）。⚠️ **不要在这里写第二套标题正则**：前端目前唯一的标题正则
 *   是 `markdown-scan.ts` 的 `HEADING_RE`，复制一份的失效形态是「代码块里的 `##` 变成
 *   一张卡」，零报错。
 * - **一句话**：`leadOf` 是主题卡、一句话档、字数统计三处**唯一**的实现。各写一份时
 *   显示的不是同一句，不报错。
 * - **值不值得折叠**：`isFoldWorthy`，产物侧口径（去标点 ≥ 3800）。
 *
 * ## 一句话里不放 Markdown
 *
 * 折叠态**不把 Markdown 交给渲染器**：把一节缩成一句再喂给 `MarkdownPreview`，表格会散成
 * 一串竖线、代码围栏失效、而图片因为单行语法仍成立会**凭空显示一张大图**，
 * `limo-block` 的标记会被 `rehypeStripComments` 剥掉、外壳不生成、形态全丢 —— 全都零报错。
 * 所以 `leadOf` 的产出是**纯文本**：图片整段移除、公式句跳过、行内记号剥干净。
 */

import { FENCE_TOGGLE_RE, HEADING_RE, isBlockClose, isMarkerLine, matchBlockOpen, scanHeadingLines } from './markdown-scan'
import { splitByH2 } from './markdown-sections'
import { headingId, stripHeadingDecor } from './markdown-heading'

/**
 * 折叠入口的长文阈值 —— **产物侧口径，去标点字数**。
 *
 * ⚠️ 不可换用转录侧的 3000：转录→产物的压比是 0.51 而不是 0.70（去标点后 Markdown 的
 * `**` `#` `-` `|` 全被剔掉，产物掉得比转录多），两个口径互换会让一批短文被误判成长文。
 */
export const FOLD_MIN_CHARS = 3800

/** 一句话太长就截断 —— 卡片上一行放不下三行字。 */
const LEAD_MAX_CHARS = 120

export interface FoldSection {
  /** 稳定 key：`'pre'`（引言）或 `headingId`；同名标题追加 `-2` / `-3` */
  key: string
  /** 标题纯文本（不含 `#`、不含行内语法与时间戳装饰）；引言为空串 */
  heading: string
  /** 与 TOC / 渲染侧同一个 `headingId`；标题为空时为空串（不挂 id） */
  headingId: string
  /** 该节带 `card` 节修饰符 */
  card: boolean
  /** 一句话（纯文本）。该节只有图片/表格/公式时为空串 —— 渲染层回落到只显示标题 */
  lead: string
  /** 该节完整 Markdown（标题行 + 正文），展开时交给 `MarkerContent` */
  body: string
  /**
   * 除标题行之外还有内容吗。
   *
   * ⚠️ **不能拿 `body` 判**：`body` 必定含标题行，`'## 丙\n'.trim()` 永不为空，
   * 「只有标题的节」会变成可点 —— 点开只是把标题原地重画一遍，零报错。
   */
  hasBody: boolean
}

export interface FoldSplit {
  /** 第一个 H2 之前的内容（已去尾部空行）；空串时为 null */
  preamble: FoldSection | null
  sections: FoldSection[]
}

const THEMATIC_BREAK_RE = /^\s{0,3}([-*_])\s*(?:\1\s*){2,}$/

/** CJK 判定 —— 只影响「多行段落拼回时补不补空格」 */
const CJK_END_RE = /[　-鿿＀-￯]$/

/** 按 `lines` 的字数（去标点、去空白、去 HTML 注释） */
export function foldCharCount(md: string): number {
  return md
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/[^\p{L}\p{N}]/gu, '')
    .length
}

/**
 * 这篇产物值不值得折叠。
 *
 * ⚠️ 前置短路用的是**原始长度**：去标点字数必定 ≤ 原始长度，所以「原始长度都不够」
 * 就可以直接判 false，不必跑正则。流式期间每一帧都会调用它，省这一次是必要的。
 */
export function isFoldWorthy(md: string | null | undefined): boolean {
  if (!md) return false
  if (md.length < FOLD_MIN_CHARS) return false
  return foldCharCount(md) >= FOLD_MIN_CHARS
}

/**
 * 这篇产物能不能折叠：**够长**且**有 H2**。
 *
 * ⚠️ 两个判据缺一不可。只有「够长」而一个 H2 都没有时，折叠档会把整篇塞进一张卡，
 * 工具栏上那个按钮点下去看不出任何变化 —— 那是最难查的一类（按钮在、行为没有）。
 * H2 判定复用 `scanHeadingLines`，不另写正则。
 */
export function canFold(md: string | null | undefined): boolean {
  if (!isFoldWorthy(md)) return false
  // 生成器只走到第一个 H2 就停 —— 长文上这也是一次 O(n) 的行扫描，别再叠一次 find
  return !scanHeadingLines(md!, 2, 2).next().done
}

/**
 * 剥掉一句话里的行内 Markdown 记号。
 *
 * ⚠️ 图片必须**整段移除**（不是保留 alt）：`![图](/api/assets/xxx.png)` 保留 alt 时
 * 卡片上会漏出库内路径，而路径在折叠态毫无意义。
 */
export function stripInlineMd(s: string): string {
  return s
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** 多行段落拼回：中文直接接，西文补一个空格（与 CommonMark 的软换行语义一致） */
function joinLines(lines: string[]): string {
  let out = ''
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) continue
    if (!out) { out = line; continue }
    out += CJK_END_RE.test(out) ? line : ` ${line}`
  }
  return out
}

/** 从一段文本里取第一句「干净」的话：不含公式、不含图片、剥完行内记号后非空 */
function firstCleanSentence(text: string): string {
  // ⚠️ **先剥行内记号，再切句**。反过来的话 `![图](…)` 里的 `!` 会被当成感叹号，
  // 一段只有图片的文字会切出一个残句 `'!'` —— 卡片上就显示一个感叹号，零报错。
  const flat = stripInlineMd(text)
  if (!flat) return ''
  // 句末标点后切分；英文句点要求后跟空白或结尾，避免把 3.2 / v1.0 切开
  const parts = flat.split(/(?<=[。！？!?；;])|(?<=\.)\s+(?=[A-Z])/)
  for (const part of parts) {
    const s = part.trim()
    if (!s) continue
    // 公式行内语法 —— 一句话里出现 `$` 只会是裸记号，整句跳过
    if (s.includes('$')) continue
    return s.length > LEAD_MAX_CHARS ? `${s.slice(0, LEAD_MAX_CHARS)}…` : s
  }
  return ''
}

/**
 * 从一节正文里抽一句话。
 *
 * 规则（见文件头「一句话里不放 Markdown」）：
 *   1. 跳过空行、标题行、围栏内、`limo-block` 块内、表格行、分隔线、引用行；
 *   2. 优先普通段落；段落里取第一句不含 `$` / 图片的话；
 *   3. 没有可用段落 → 第一个列表项；
 *   4. 都没有 → 空串（**不要拿标题冒充一句话**）。
 */
export function leadOf(body: string): string {
  const src = body.replace(/\r\n?/g, '\n')
  const lines = src.split('\n')
  const paras: string[] = []
  const items: string[] = []
  let para: string[] = []
  let item: string[] | null = null
  let inFence = false
  let inBlock = false

  const flushPara = () => {
    if (para.length) { paras.push(joinLines(para)); para = [] }
  }
  const flushItem = () => {
    if (item && item.length) { items.push(joinLines(item)) }
    item = null
  }

  for (const raw of lines) {
    const line = raw
    const t = line.trim()
    if (FENCE_TOGGLE_RE.test(line)) { inFence = !inFence; flushPara(); flushItem(); continue }
    if (inFence) continue
    if (inBlock) {
      if (isBlockClose(line)) inBlock = false
      continue
    }
    if (matchBlockOpen(line)) { inBlock = true; flushPara(); flushItem(); continue }
    if (isMarkerLine(line)) continue
    if (!t) { flushPara(); flushItem(); continue }
    // limo-meta / 其它 HTML 注释整行跳过（不是内容）
    if (t.startsWith('<!--') && t.endsWith('-->')) { flushPara(); flushItem(); continue }
    if (HEADING_RE.test(t)) { flushPara(); flushItem(); continue }
    if (t.startsWith('|')) { flushPara(); flushItem(); continue }
    if (THEMATIC_BREAK_RE.test(t)) { flushPara(); flushItem(); continue }
    if (t.startsWith('>')) { flushPara(); flushItem(); continue }
    const li = /^(?:[-*+]|\d+[.)])\s+(.+)$/.exec(t)
    if (li) {
      flushPara()
      flushItem()
      item = [li[1]]
      continue
    }
    if (item) { item.push(t); continue }
    para.push(t)
  }
  flushPara()
  flushItem()

  for (const p of paras) {
    const s = firstCleanSentence(p)
    if (s) return s
  }
  for (const p of items) {
    const s = firstCleanSentence(p)
    if (s) return s
  }
  return ''
}

/** 标题行 → { text, id }，与 TOC / 渲染侧同一份清洗 */
function headingOf(line: string): { text: string; id: string } {
  const m = HEADING_RE.exec(line)
  const raw = m ? m[2] : line
  const text = stripHeadingDecor(raw)
  return { text, id: text ? headingId(text) : '' }
}

/**
 * 切节 + 抽一句话。
 *
 * 只按 **H2 切一层**（H3/H4 留在节内，展开后正常显示）—— 与现有 `CardSections` 一致。
 * 不按「标题编号的段数」推断层级：模型常把 `3.2` 与 `3.2.1` 写成同一级 `####`，
 * 但本方案只做一层，那条判据没有消费方。
 */
export function foldSections(md: string | null | undefined): FoldSplit {
  const src = md ?? ''
  const { preamble, sections } = splitByH2(src)

  const seen = new Map<string, number>()
  const out: FoldSection[] = sections.map((s, i) => {
    const { text, id } = headingOf(s.heading)
    const base = id || `h2-${i}`
    const n = (seen.get(base) ?? 0) + 1
    seen.set(base, n)
    return {
      key: n === 1 ? base : `${base}-${n}`,
      heading: text,
      headingId: id,
      card: s.card,
      lead: leadOf(s.body),
      // 展开时把**原始标题行**拼回去（含 `##`），交给 `MarkerContent` 后仍是那条 `<h2>`
      body: `${s.heading}\n${s.body}`,
      hasBody: s.body.trim() !== '',
    }
  })

  const pre = preamble.trim()
  return {
    // 引言没有标题：`heading` 为空串，渲染层只画正文的一句话
    preamble: pre
      ? {
          key: 'pre', heading: '', headingId: '', card: false,
          lead: leadOf(pre), body: pre, hasBody: true,
        }
      : null,
    sections: out,
  }
}
