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
 * **产物侧**（预览 / 导出）真写了渲染分支的块类型。
 *
 * ⚠️ **这六种全部是「纯 CSS 形态」**（`styles/blocks.css`）—— 产物侧**刻意不做 DOM
 * 结构分派**。理由是那份 CSS 同时是导出侧读的**单一真相源**，而导出的 HTML 由 Python
 * 的 `render_blocks_html` 生成：一旦某个 type 的结构与别的不一样，Python 就得复刻一遍
 * React 的结构（模式 #54），「预览所见 = 导出所得」也变成靠人工对齐维持。
 * 需要结构差异的形态（如指标卡并排）因此**不做**，退化为纯 CSS 能表达的样子。
 * 详见 `styles/blocks.css` 文件头。
 *
 * ⚠️ `steps` / `timeline`（2026-10-11 补）**替代图表管道里的简单线性图** ——
 * 横/竖两种单链在 `fig` 里要付一整条渲染链（语义 JSON → `chart_layout` → PIL → PNG），
 * 而它们表达的关系用 Markdown 列表就能保真：`steps` = 块内无序列表（横排 chip），
 * `timeline` = 块内有序列表（圆编号 + 竖连线）。`get_chart_format` 已删掉 hchain /
 * timeline 两种布局的引导（九种 → 七种），留下的都是列表表达不了的关系
 * （分支 / 环 / 二维 / 时序）。
 *
 * ⚠️ `table` / `code` / `image`（2026-10-11 补）与上面六个**性质不同族**：
 * 前六个标记的是**普通段落**——「这一段文字是什么性质」（结论 / 风险 / 建议 / 数字 /
 * 步骤 / 阶段），块内原本就是散文。这三个标记的是**富内容**——表格、代码块、图片
 * 本来就与正文不同质，加标记的**全部作用**是外面多一层壳，块内一个字节都不动。
 * 故它们在 `blocks.css` 里**不换 `display`、不换子元素**，只做容器装饰。
 * ⚠️ 这一族**刻意不是「把表格/代码改写成卡片形状」**——图鉴对此有明确判词
 * （「不要为了统一视觉而把代码改成普通文字卡片，否则会影响可读性和可用性」）。
 * 保住等宽、缩进、复制、横向滚动，是这三个 type 能成立的前提。
 */
export const ARTIFACT_BLOCK_TYPES: ReadonlySet<string> = new Set([
  'key-takeaway',
  'alert',
  'recommend',
  'metric',
  'steps',
  'timeline',
  'table',
  'code',
  'image',
])

/**
 * **对话侧**（`ai-chat/ChatMarkerContent.tsx`）真写了渲染分支的块类型。
 * 与 `ARTIFACT_BLOCK_TYPES` **刻意不相交** —— 产物 prompt 与对话 prompt 各教各的。
 *
 * ⚠️ 这侧**不受**上面那条「只做 CSS 形态」的约束：`chat-blocks.css` 明说不进导出
 * （对话只有 md / json 两条导出路径），故可以自由用 React 结构与图标。
 */
export const CHAT_BLOCK_TYPES: ReadonlySet<string> = new Set([
  'card-group',
  'follow-up',
  'info',
  'digest',
])

/**
 * v1 协议定义的**全部**块类型 = 上两者之并。
 *
 * ⚠️ **「协议知道这个 type」与「某一侧实现了渲染」是两件事**，故拆成三个集合。混为一谈
 * 的失效方式是静默的：把 type 加进这里就等于宣称「有人处理它」，而某个渲染方其实没有
 * 分支 → 该 type 被原样当正文吐出来，**零报错**。拆开后测试可以机器核对
 * 「并集相等 + 两两不相交」，`limo-blocks.json` 的 `meta` 钉住（模式 #54）。
 *
 * ⚠️ 收到**不属于自己那侧**的 type 时，两侧的行为都是**当普通内容渲染**（零丢失）
 * 而非报错 —— 判据是「内容永远不该因为类型不认识而消失」。
 *
 * ⚠️ 与后端 `artifact_blocks.py::KNOWN_BLOCK_TYPES` 同源。
 */
export const KNOWN_BLOCK_TYPES: ReadonlySet<string> = new Set([
  ...ARTIFACT_BLOCK_TYPES,
  ...CHAT_BLOCK_TYPES,
])

/**
 * **节级修饰符** `card` —— 唯一的「不是块」的 type（2026-10-11）。
 *
 * ⚠️ 它与上面两族 type 的区别是**粒度**：那两族标记的是**块**（一段内容），`card`
 * 标记的是**节**（一个 `##` 标题及其全部后续内容，直到下一个同级标题）。语义是
 * **「这一节值得单独成一张卡片」**，故标记写在标题行的**下面一行**。
 *
 * ⚠️ 为什么要有这个区分（不是洁癖）：模型很可能会把它**写在标题之前**（其余 type 都是
 * 包裹式，写成 `<!-- card -->\n## 标题` 是它的本能）。若当成块，`scanHeadingLines`
 * 会进入块内状态、把 `## 标题` 整个跳过 —— 这一节从文档结构里消失，切分器连"节"都
 * 看不到。当作**节修饰符**（`matchBlockOpen` 返回 `null`，标记行对标题扫描完全透明）
 * 时，两种写法都退化成同一件事：**该节不成卡，但结构与内容一字不少**。
 *
 * ⚠️ 标记行仍被 `MARKER_LINE_RE` 剥掉（零残留）—— 渲染时它照旧不会出现在正文里，
 * 与「解析失败的标记不该出现在读者眼前」这条既有判据同源。
 *
 * ⚠️ 与后端 `artifact_blocks.py::SECTION_CARD_TYPE` 同源。
 */
export const SECTION_CARD_TYPE = 'card'

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
 * 匹配**块**开标记；**协议版本不符、或该 type 是节修饰符时返回 `null`**
 * （协议不符的行按「未知」剥掉、内容不吞；节修饰符则根本不开块 —— 见 `SECTION_CARD_TYPE`）。
 * ⚠️ 与后端 `artifact_blocks.py::match_open` 同源。
 */
export function matchBlockOpen(line: string): { type: string } | null {
  const m = BLOCK_OPEN_RE.exec(line)
  if (!m || m[1] !== PROTOCOL_VERSION || m[2] === SECTION_CARD_TYPE) return null
  return { type: m[2] }
}

/**
 * 该行是不是「把这一节标成卡片」的**节修饰符**（`type="card"` 的开标记）。
 *
 * ⚠️ 与 `matchBlockOpen` **刻意分开**：那个回答「这里是不是块的开头」（节修饰符不是），
 * 这个回答「这一节要不要成卡」（只有切分器问）。合成一个函数会让
 * 「card 不开块」与「card 标记了节」两条互不相干的判据绑在一起。
 * ⚠️ 认**当前协议版本**：将来 `v2` 改语法时，`v1` 的 card 标记按未知行剥掉（零残留），
 * 与 `matchBlockOpen` 的版本纪律一致。
 */
export function isSectionCard(line: string): boolean {
  const m = BLOCK_OPEN_RE.exec(line)
  return m !== null && m[1] === PROTOCOL_VERSION && m[2] === SECTION_CARD_TYPE
}

/** 该行是否闭合标记块 */
export function isBlockClose(line: string): boolean {
  return BLOCK_CLOSE_RE.test(line)
}

/** 该行是不是「该被剥掉的标记行」（含畸形 / 未知版本 / 孤儿闭标记） */
export function isMarkerLine(line: string): boolean {
  return MARKER_LINE_RE.test(line)
}

/**
 * 该行是不是**写到一半**的 `limo-block` 标记行（流式专用）。
 *
 * ⚠️ **为什么需要它**：流式渲染时 chunk 会把开标记切在中间
 * （`<!-- limo-block:v1 type="card-`），这种半截行**既不匹配 `BLOCK_OPEN_RE`
 * （缺 `-->`）也不匹配 `MARKER_LINE_RE`** → 落到「普通正文」分支被渲染出来，
 * 用户看着原始协议文本从眼前飘过。
 *
 * ⚠️ **为什么判据要认命名空间**（而不是朴素的「以 `<!--` 开头且没有 `-->`」）：
 * 后者会把**正在输入的合法 HTML 注释**（`<!-- 这是说明`，还没敲完 `-->`）一起吞掉 ——
 * 正文瞬断。`limo-block` 是专属命名空间，只认它的前缀才与 `MARKER_LINE_RE`
 * 「零误伤用户内容」这条既有判据同源。
 *
 * ⚠️ **只在流式渲染时调用**（见 `suppressTrailingPartialMarker`）。
 */
export function isPartialMarkerLine(line: string): boolean {
  const t = line.trimStart()
  if (!t.startsWith('<!--')) return false
  if (t.includes('-->')) return false
  // 剥掉 `<!--`，再剥可选的前导 `/`（闭标记也可能只写了一半）与空白
  const body = t.slice(4).replace(/^[ \t]*\/?[ \t]*/, '')
  // 要么是 `limo-block` 的前缀（`limo-bl`），要么已经写到了属性段（`limo-block:v1 type="card-`）。
  // ⚠️ 第二条用 `'limo-block:'` 而不是裸 `'limo-block'`：后者会把 `limo-blocks` 也吞掉
  // （`'limo-blocks'.startsWith('limo-block')` 为真）—— 那不是本协议的行。
  return body.length === 0 || 'limo-block'.startsWith(body) || body.startsWith('limo-block:')
}

/**
 * 流式渲染专用：**最后一行**若是半截标记行，把它从**渲染用**的字符串里去掉。
 *
 * ⚠️ **只影响渲染，不改存储** —— 入参是 `msg.content` 的副本，消息本体一字不动。
 * ⚠️ **只削最后一行**：chunk 只会向后追加，半截行必定在末尾；等下一个 chunk 到齐它就被
 * 补全了。削中间行会在正文里挖出空洞。
 * ⚠️ **削不掉就原样返回**（含「末尾是空行」这种最常见的情况）—— 这个函数在流式的每一帧
 * 都会被调用，无谓的重建字符串是纯浪费。
 */
export function suppressTrailingPartialMarker(md: string): string {
  const nl = md.lastIndexOf('\n')
  const tail = nl < 0 ? md : md.slice(nl + 1)
  if (!isPartialMarkerLine(tail)) return md
  return nl < 0 ? '' : md.slice(0, nl)
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
    // ⚠️ **标记块内的 `##` 不是文档结构** —— 它属于「内容块」内部（强调块 / 内容卡片
    // 都算）。在这里切它会切出一张内容是半截块的卡（TOC 还会多一个点了没反应的幽灵项）。
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
