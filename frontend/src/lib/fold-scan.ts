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
 * - **能不能折叠**：`canFold`，判据只有一条 —— **有 H2**。
 *   ⚠️ **刻意不看字数**：按字数分是**提示词侧**的判据（哪些注入按长文才加），
 *   与「入口要不要出现」是两回事。短产物也有分节，折叠成几张卡扫一眼照样有用，
 *   而且默认档位是全文，多一个按钮没有副作用。
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
  /**
   * `lead` 为空时充当那一行的一句话：说明这一节里是什么（`含表格与公式`）。
   *
   * ⚠️ 没有它，一节全是图表/图片/语义块时卡片上只剩一个标题 —— 看起来像渲染坏了，
   * 而用户没有任何办法判断「值不值得点开」。它只描述**类型**，不复述内容。
   */
  hint: string
  /**
   * 该节正文（**不含标题行**），展开时交给 `MarkerContent`。
   *
   * ⚠️ **不能把标题行拼进来**：折叠视图的标题行（`.fold-head`）一直在，展开时它并不消失，
   * body 再带一次 `## 标题` 就会在同一张卡上画出两条一样的标题 —— 不报错，只是重复。
   */
  body: string
  /**
   * 除标题行、标记行、注释、分隔线之外，还有看得见的内容吗。
   *
   * ⚠️ **不能拿 `body` 判**：`body` 必定含标题行，`'## 丙\n'.trim()` 永不为空，
   * 「只有标题的节」会变成可点 —— 点开只是把标题原地重画一遍，零报错。
   * 判据见 `hasRealContent`。
   */
  hasBody: boolean
  /**
   * 展开之后**会不会看到新东西**。
   *
   * `hasBody` 只回答「有没有正文」，不回答「点开值不值」。一节正文只有一句纯文本时，
   * 展开前后看到的是同一句话（只是多了格式外壳），而折叠态已经把这句话显示出来了 ——
   * 给它绑点击就是「点了没反应」。判据见 `isExpandable`。
   */
  expandable: boolean
}

export interface FoldSplit {
  /** 第一个 H2 之前的内容（已去尾部空行）；空串时为 null */
  preamble: FoldSection | null
  sections: FoldSection[]
}

const THEMATIC_BREAK_RE = /^\s{0,3}([-*_])\s*(?:\1\s*){2,}$/

/** CJK 判定 —— 只影响「多行段落拼回时补不补空格」 */
const CJK_END_RE = /[　-鿿＀-￯]$/

/**
 * 这篇产物能不能折叠：**有 H2**。
 *
 * ⚠️ 只有这一条判据。一个 H2 都没有时，折叠档会把整篇塞进一张卡，工具栏上那个按钮
 * 点下去看不出任何变化 —— 那是最难查的一类（按钮在、行为没有）。
 * H2 判定复用 `scanHeadingLines`（围栏内与 `limo-block` 块内的 `##` 不算），不另写正则。
 *
 * ⚠️ **不按字数**。字数判据住在提示词侧（哪些注入只对长文加），与这里无关；
 * 把两者混在一起会让「同样能分节的短文」没有入口，而界面上没有任何解释。
 */
export function canFold(md: string | null | undefined): boolean {
  if (!md) return false
  // 生成器只走到第一个 H2 就停，不必扫完整篇
  return !scanHeadingLines(md, 2, 2).next().done
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
 *   1. 跳过空行、标题行、围栏内、`limo-block` 块内、表格行、分隔线；
 *   2. 优先普通段落；段落里取第一句不含 `$` / 图片的话；
 *   3. 没有可用段落 → 引用块（`>`）里的句子；
 *   4. 再没有 → 第一个列表项；
 *   5. 都没有 → 空串（**不要拿标题冒充一句话**）。
 *
 * ⚠️ **引用块必须可用**：产物的引言常常是一段 `>` 摘要（尤其在第一个 H2 之前），
 * 早先把它整行跳过，于是一个有内容的引言在主题档里渲染成一张什么都没有的卡。
 */
export function leadOf(body: string): string {
  const src = body.replace(/\r\n?/g, '\n')
  const lines = src.split('\n')
  const paras: string[] = []
  const quotes: string[] = []
  const items: string[] = []
  let para: string[] = []
  let item: string[] | null = null
  let inFence = false
  let inBlock = false
  let inComment = false

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
    // limo-meta / 其它 HTML 注释跳过（不是内容）。跨行注释要跟状态，
    // 只判「整行注释」时中间那几行会被当成段落，抽出一句注释文字。
    if (inComment) {
      if (t.includes('-->')) inComment = false
      continue
    }
    if (t.startsWith('<!--')) {
      flushPara(); flushItem()
      if (!t.includes('-->')) inComment = true
      continue
    }
    if (HEADING_RE.test(t)) { flushPara(); flushItem(); continue }
    if (t.startsWith('|')) { flushPara(); flushItem(); continue }
    if (THEMATIC_BREAK_RE.test(t)) { flushPara(); flushItem(); continue }
    const q = /^>\s?(.*)$/.exec(t)
    if (q) {
      flushPara(); flushItem()
      if (q[1].trim()) quotes.push(q[1].trim())
      continue
    }
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
  for (const p of quotes) {
    const s = firstCleanSentence(p)
    if (s) return s
  }
  for (const p of items) {
    const s = firstCleanSentence(p)
    if (s) return s
  }
  return ''
}

/** 围栏（代码块）整段 —— 描述内容类型时先把围栏挖掉，免得把代码里的 `|` 当表格 */
const FENCED_BLOCK_RE = /^[ \t]{0,3}```[\s\S]*?^[ \t]{0,3}```/gm
/** 只判「有没有代码块」。⚠️ 不复用上面那个：它带 `/g`，`test` 会留 `lastIndex` 状态 */
const FENCE_START_RE = /^[ \t]{0,3}```/m

/**
 * 抽不出一句话时，用「里面是什么」充当那一行。
 *
 * 只在 `leadOf` 返回空串时使用（整节是图片 / 表格 / 公式 / 代码 / 语义块）。
 * 顺序即展示顺序，认不出任何类型时返回空串（那说明这一节真的只有标题）。
 */
export function contentHint(body: string): string {
  const kinds: string[] = []
  // 围栏内的 `|` / `$` 是代码内容，不是表格与公式 —— 先把围栏挖掉再看
  const outsideFences = body.replace(FENCED_BLOCK_RE, '')
  if (/!\[[^\]]*\]\([^)]*\)/.test(outsideFences)) kinds.push('图片')
  if (/^[ \t]*\|.+\|[ \t]*$/m.test(outsideFences)) kinds.push('表格')
  if (/\$\$/.test(outsideFences)) kinds.push('公式')
  if (FENCE_START_RE.test(body)) kinds.push('代码')
  if (/^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S/m.test(outsideFences)) kinds.push('列表')
  if (/<!--[ \t]*limo-block:v\d+/.test(body)) kinds.push('要点块')
  return kinds.length ? `含${kinds.join('、')}` : ''
}

/**
 * 这一节除了标题之外，还有**看得见的内容**吗。
 *
 * 「看得见」= 渲染出来之后屏幕上会多出东西。标题行、`limo-block` 标记行、HTML 注释、
 * 水平分隔线、空行都不算 —— 一节如果只剩这些，展开与不展开看到的是同一张只有标题的卡。
 *
 * ⚠️ **不能拿 `body.trim() !== ''` 判**。`body` 必定含标题行，那句话永远为真；
 * 于是「只有标题的节」被当成有正文：主题档里它占一个整格、显示成一个点开什么都没有的
 * 空卡。截图上那张只有箭头的卡就是这一类（引言里只写了 `# 一级标题`）。
 *
 * 围栏里的内容（代码 / `fig` / `chart`）算内容 —— 判「有没有」只看围栏里是否至少有一行，
 * 不解析它是什么。
 */
export function hasRealContent(body: string): boolean {
  if (!body.trim()) return false
  const lines = body.split('\n')
  const headingLines = new Set<number>()
  for (const h of scanHeadingLines(body, 1, 6)) headingLines.add(h.line)

  let inFence = false
  let inComment = false
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].replace(/\r$/, '').trim()
    // 围栏开关先看：围栏里的 `<!--` 是代码内容，不是注释开头
    if (FENCE_TOGGLE_RE.test(t)) { inFence = !inFence; continue }
    if (inFence) { if (t) return true; continue }
    if (inComment) {
      if (t.includes('-->')) inComment = false
      continue
    }
    if (t.startsWith('<!--')) {
      if (!t.includes('-->')) inComment = true
      continue
    }
    if (!t) continue
    if (headingLines.has(i)) continue
    if (isMarkerLine(t)) continue
    if (THEMATIC_BREAK_RE.test(t)) continue
    return true
  }
  return false
}

/**
 * 展开有没有意义。
 *
 * 判据是「展开后的渲染结果与折叠态显示的那句话是否等价」。**宁可多给点击，不可少给**：
 * 折叠态是纯文本，凡是展开后会多出信息的东西都必须算可展开，否则那些信息就永远看不到。
 *
 * 因此下面每一种情况都返回 true：
 *   1. 正文不止一行（多段 / 列表 / 表格 / 围栏）—— 行数本身就是折叠态丢掉的结构；
 *   2. 单行但是围栏 / `limo-block` 标记 —— 展开才看得见块；
 *   3. 单行里含行内记号（图片、链接、反引号、强调、删除线、`$`、HTML 标签）——
 *      折叠态把它们剥成纯文本，展开后链接可点、强调可见、公式排版；
 *   4. 单行太长被 `LEAD_MAX_CHARS` 截断 —— 折叠态只显示前半句。
 *
 * 只剩「单行、纯文本、不超长」这一种返回 false：那种节的展开前后逐字一样。
 */
function isExpandable(body: string): boolean {
  const lines = body.split('\n').filter(l => l.trim() !== '')
  if (lines.length === 0) return false
  if (lines.length > 1) return true
  const line = lines[0].trim()
  if (FENCE_TOGGLE_RE.test(line) || matchBlockOpen(line) || isMarkerLine(line)) return true
  if (/!\[|\[[^\]]*\]\(|`|\*\*|~~|\$|<\w/.test(line)) return true
  return stripInlineMd(line).length > LEAD_MAX_CHARS
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
    const lead = leadOf(s.body)
    const hasBody = hasRealContent(s.body)
    const hint = lead ? '' : contentHint(s.body)
    return {
      key: n === 1 ? base : `${base}-${n}`,
      heading: text,
      headingId: id,
      card: s.card,
      lead,
      // 兜底：有内容、但既抽不出一句话也认不出类型（例如整段只有一个行内公式）。
      // 不给兜底的话这张卡只有一个标题 —— 又是「看着像空卡、点开有内容」那类。
      hint: lead ? '' : (hint || (hasBody ? '展开查看' : '')),
      // 标题由 `FoldView` 的标题行画（带 id），这里只放正文
      body: s.body,
      hasBody,
      expandable: isExpandable(s.body),
    }
  })

  const pre = preamble.trim()
  // 引言常以 `# 文档标题` 开头：把第一个标题行抽出来当卡片的标题。
  // ⚠️ 不抽的话它在折叠态完全不可见（正文只有展开才渲染），用户看到的第一张卡
  // 叫不出名字；只剩这一个标题的引言还会变成一张什么都没有的空卡。
  let preHeading = ''
  let preHeadingId = ''
  let preBody = pre
  if (pre) {
    const first = scanHeadingLines(pre, 1, 6).next()
    if (!first.done) {
      const text = stripHeadingDecor(first.value.text)
      if (text) {
        preHeading = text
        preHeadingId = headingId(text)
        const line = first.value.line
        preBody = pre.split('\n').filter((_, i) => i !== line).join('\n').trim()
      }
    }
  }
  const preLead = preBody ? leadOf(preBody) : ''
  const preHasBody = hasRealContent(preBody)
  const preHint = preLead ? '' : contentHint(preBody)
  // 只剩注释 / 标记行 / 分隔线的引言不渲染 —— 它没有标题也没有内容，就是一张空卡
  const preContent: FoldSection | null = pre && (preHeading || preHasBody)
    ? {
        key: 'pre',
        heading: preHeading,
        headingId: preHeadingId,
        card: false,
        lead: preLead,
        hint: preLead ? '' : (preHint || (preHasBody ? '展开查看' : '')),
        body: preBody,
        hasBody: preHasBody,
        expandable: isExpandable(preBody),
      }
    : null
  return {
    // 引言没有 `##` 标题：`heading` 通常是空的，渲染层只画正文的一句话；
    // 若引言开头是 `# 文档标题`，它被抽成 `heading` 显示在卡片上。
    preamble: preContent,
    sections: out,
  }
}
