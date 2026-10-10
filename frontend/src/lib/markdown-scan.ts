/**
 * Markdown 源码扫描 —— **协议元素感知**的唯一真相源。
 *
 * ⚠️ 为什么值得单独一个文件：代码块里的 `## ` **不是标题**。这一点上项目已经踩过一次
 * 真坑 —— `extractHeadings` 原先是条多行正则，于是代码块里的 `## ` 被当成标题，
 * 目录里长出「点了没反应的幽灵项」（没有对应的 DOM 节点），**零报错**。
 *
 * 本轮产物骨架要在同一份源码上做第二件事（按 H2 切卡），两处各写一份围栏扫描
 * = 同一个坑再踩一次（模式 #54：同一契约写在两处时，不同源的那处不会报错）。
 * 故抽成此处，`markdown-components.tsx::extractHeadings` 与
 * `markdown-sections.ts::splitByH2` **都消费它，都不再自带循环**。
 *
 * ⚠️ 同一纪律的第二个元素是 **`limo-block` 语义标记**（`<!-- limo-block:v1 … -->`）：
 * 块内的 `##` **不是文档结构**，块内代码围栏里的闭标记也**不算闭合**。标记正则、协议
 * 版本与类型白名单都定义在这里（「这一行是什么」的判据只有一个家），
 * `markdown-markers.ts::parseMarkers` 消费它们做分段。
 *
 * ⚠️ 后端有一份 Python 同构实现（`app/core/artifact_blocks.py` +
 * `app/services/markdown_sections.py`）—— 跨语言无法共享代码，靠
 * `test/fixtures/h2-sections.json` 与 `test/fixtures/limo-blocks.json` 钉住两侧一致。
 * **改这里的判据必须同步改那两份 fixture 能覆盖的行为**。
 */

/** 围栏开/闭行（``` 或 ~~~，允许前导空白）。本扫描只做「切换」，不配对符号类型 */
export const FENCE_TOGGLE_RE = /^\s*(?:```|~~~)/

/** ATX 标题行：`#`~`######` + 空白 + 文本 */
export const HEADING_RE = /^(#{1,6})\s+(.+?)\s*$/

/** `limo-block` 协议版本。⚠️ 改语法时新增 `v2`，`v1` 解析器**照旧工作** */
export const PROTOCOL_VERSION = 'v1'

/**
 * 已知的块类型白名单。
 * ⚠️ **第一版刻意只有 1 个** —— 加一个类型的成本不在代码，而在 prompt 每轮都要重新
 * 教育模型（且类型越多、模型越容易「到处都用」）。先看这一个的真实遵循率与滥用率。
 * ⚠️ 与后端 `artifact_blocks.py::KNOWN_BLOCK_TYPES` 同源，由 `limo-blocks.json` 的
 * `meta.knownTypes` 钉住。
 */
export const KNOWN_BLOCK_TYPES: ReadonlySet<string> = new Set(['key-takeaway'])

/**
 * 开标记：`<!-- limo-block:vN type="xxx" -->`，独占一行。
 * ⚠️ 与后端 `artifact_blocks.py::OPEN_RE` 同构。`type` 值限定小写连字符 —— 属性值含 `>`
 * 时本正则不匹配，自然退化为「未知行」被剥掉。
 */
export const BLOCK_OPEN_RE =
  /^[ \t]*<!--[ \t]*limo-block:(v\d+)[ \t]+type="([a-z][a-z0-9-]*)"[ \t]*-->[ \t]*$/

/** 闭标记：`<!-- /limo-block -->`，独占一行。与后端 `CLOSE_RE` 同构 */
export const BLOCK_CLOSE_RE = /^[ \t]*<!--[ \t]*\/limo-block[ \t]*-->[ \t]*$/

/**
 * **该剥掉哪一行**的判据 —— 比开/闭标记正则都宽：任何**独占一行的、含 `limo-block`
 * 的 HTML 注释**（含语法畸形、未知协议版本、孤儿闭标记）。
 *
 * ⚠️ **为什么必须放宽**：`BLOCK_OPEN_RE` 严格（`type` 值只允许 `[a-z][a-z0-9-]*`），
 * 于是 `<!-- limo-block:v1 type="a>b" -->` 这种模型笔误既不匹配开标记、也不匹配闭标记 ——
 * 若只按这两个正则剥，它会**原样留在正文里给读者看**。这正是「解析失败的标记不该出现在
 * 读者眼前」要挡的现象。`limo-block` 是专属命名空间，剥掉一行注释不丢任何内容。
 * 与后端 `artifact_blocks.py::MARKER_LINE_RE` 同构。
 */
export const MARKER_LINE_RE = /^[ \t]*<!--[ \t]*\/?limo-block.*-->[ \t]*$/

/**
 * 匹配开标记；**协议版本不符时返回 `null`**（该行按「未知」剥掉，内容不吞）。
 * ⚠️ 与后端 `artifact_blocks.py::match_open` 同源。
 */
export function matchBlockOpen(line: string): { type: string } | null {
  const m = BLOCK_OPEN_RE.exec(line)
  return m && m[1] === PROTOCOL_VERSION ? { type: m[2] } : null
}

/** 该行是否闭合标记块 */
export function isBlockClose(line: string): boolean {
  return BLOCK_CLOSE_RE.test(line)
}

/** 该行是不是「该被剥掉的标记行」（含畸形 / 未知版本 / 孤儿闭标记） */
export function isMarkerLine(line: string): boolean {
  return MARKER_LINE_RE.test(line)
}

export interface HeadingLine {
  /** 1-6 */
  level: number
  /** 标题文本（**不含** `#` 前缀与首尾空白），未剥行内语法与装饰 */
  text: string
  /** 0-based 行号（`split('\n')` 的下标，可直接用于 `lines[i]`） */
  line: number
}

/**
 * 逐行扫出标题（围栏内与标记块内一律跳过）。
 *
 * ⚠️ CRLF：`\r` 必须剥掉再匹配，否则 `(.+?)\s*$` 虽然能吃掉它，但 `line` 落在
 * `split('\n')` 的分段上时 `lines[i]` 仍带 `\r`（`splitByH2` 直接用 `lines[i]` 取原始行）。
 */
export function* scanHeadingLines(
  md: string,
  minLevel = 1,
  maxLevel = 6,
): Generator<HeadingLine> {
  const lines = md.split('\n')
  let inFence = false
  let inBlock = false
  for (let line = 0; line < lines.length; line++) {
    const raw = lines[line]
    const text = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (FENCE_TOGGLE_RE.test(text)) { inFence = !inFence; continue }
    if (inFence) continue
    // ⚠️ **标记块内的 `##` 不是文档结构** —— 它属于「重点块」内部。在这里切它会切出
    // 一张内容是半截强调块的卡（TOC 还会多一个点了没反应的幽灵项）。
    // 与后端 `markdown_sections.py::iter_headings` 同构。
    if (inBlock) {
      if (isBlockClose(text)) inBlock = false
      continue
    }
    if (matchBlockOpen(text)) { inBlock = true; continue }
    const m = HEADING_RE.exec(text)
    if (!m) continue
    const level = m[1].length
    if (level < minLevel || level > maxLevel) continue
    yield { level, text: m[2], line }
  }
}
