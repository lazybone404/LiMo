/**
 * `limo-block` 语义标记 —— 前端**唯一**的分段与剥离实现。
 *
 * 后端同构实现是 `app/core/artifact_blocks.py::parse_blocks` / `strip_blocks` ——
 * 跨语言无法共享代码，靠 `test/fixtures/limo-blocks.json` 钉住两侧一致
 * （两侧各读同一份文件、各跑各的断言）。**改这里的判据必须同步改那份 fixture**。
 *
 * ⚠️ **正则、协议版本、类型白名单一律从 `markdown-scan.ts` import**，不在这里再定义
 * 一份 —— 「这一行是什么」的分类只该有一个家（`splitByH2` / `extractHeadings` / 本模块
 * 都要知道「标记块开始了」）。两份扫描规则的漂移是静默的：不报错，只是切出来的卡片
 * 数量对不上（模式 #54）。
 *
 * ⚠️ **为什么必须在字符串层解析**（而不是交给 react-markdown）：标记是**成对**的
 * （开标记 `<!-- limo-block:v1 … -->` + 闭合标记 `<!-- /limo-block -->`），要在两者
 * **之间**生成一个块级容器 —— 这是**结构变换**，交给 rehype 等于手写一遍节点树改写，
 * 且会与后端 `artifact_blocks.py` 各维护一份实现。字符串层解析一次，判据还能与后端
 * 共用同一份 `limo-blocks.json` fixture 钉住（跨语言无法共享代码，只能共享用例）。
 *
 * ⚠️ 顺带澄清一条易混的事实：**注释本身不会漏进渲染** —— `markdown-plugins.ts` 的
 * `rehypeStripComments` 已统一剥掉（react-markdown 不启用 `rehype-raw` 时，注释以
 * `raw` 节点走到渲染阶段、被**转义**成可见文本，而不是被丢弃）。两件事分工不同：
 * 「不让它显示」用剥的；「让这两行之间成为一个块」只能靠字符串层解析。
 */

import {
  FENCE_TOGGLE_RE,
  isBlockClose,
  isMarkerLine,
  matchBlockOpen,
} from './markdown-scan'

/** 块分段：`type` 是**语法提取**的结果，不认识也要交出去（渲染方决定怎么包） */
export interface BlockSegment {
  kind: 'block'
  type: string
  content: string
}

/** 普通正文分段 */
export interface TextSegment {
  kind: 'text'
  content: string
}

export type Segment = TextSegment | BlockSegment

/** CRLF → LF。⚠️ 入口整体规范化（与后端 `_normalize` 同款）：只在匹配时剥 `\r`
 *  是不够的，分段内容里的**每一行**都可能带尾 `\r`。行数不变，行号语义不受影响。 */
function normalize(md: string): string {
  return md.replace(/\r\n?/g, '\n')
}

/** 廉价早短路 —— 只做子串判定，**不做围栏感知**。
 *
 *  误判（用户正文里恰好写着 `limo-block` 这个词）只会多走一次慢路径，代价可忽略；
 *  反之漏判才是 bug，而子串判定不会漏。 */
export function hasBlocks(md: string): boolean {
  return md.includes('limo-block')
}

/**
 * 把正文切成 `text` / `block` 交替的分段序列。
 *
 * ⚠️ **无标记时返回单个 text 段，内容与输入逐字相同**（仅 CRLF 被规范化）——
 * 这是「零回归」的机器可验证形式，也是前端预览与后端导出**共用同一条降级路径**的依据。
 *
 * ⚠️ **`content` 是块内原文**（不 trim 内部、不剥 `**` 加粗）—— 它要原样交给
 * `MarkdownPreview`，任何预处理都会让「块内渲染」与「块外渲染」分叉。
 */
export function parseMarkers(md: string): Segment[] {
  const src = normalize(md)
  const out: Segment[] = []
  let buf: string[] = []
  let blockLines: string[] = []
  let blockType: string | null = null
  let inFence = false

  const flushText = () => {
    if (buf.length) {
      out.push({ kind: 'text', content: buf.join('\n') })
      buf = []
    }
  }

  for (const raw of src.split('\n')) {
    // ⚠️ 围栏判定**排在标记判定之前**，且对块内同样生效 —— 块内代码块里的
    // `<!-- /limo-block -->` 是用户内容，不该提前闭合这个块。
    if (FENCE_TOGGLE_RE.test(raw)) {
      inFence = !inFence
      ;(blockType !== null ? blockLines : buf).push(raw)
      continue
    }
    if (inFence) {
      ;(blockType !== null ? blockLines : buf).push(raw)
      continue
    }

    if (blockType === null) {
      const m = matchBlockOpen(raw)
      if (m !== null) {
        flushText()
        blockType = m.type
        blockLines = []
        continue
      }
      // 未知协议版本 / 孤儿闭标记 / 语法畸形的标记行 → 剥掉该行（内容不吞）
      if (isMarkerLine(raw)) continue
      buf.push(raw)
    } else {
      if (isBlockClose(raw)) {
        out.push({ kind: 'block', type: blockType, content: blockLines.join('\n') })
        blockType = null
        blockLines = []
        continue
      }
      // 块内的**又一枚标记行**（嵌套 / 畸形）→ 剥掉该行，内容并入外层
      if (isMarkerLine(raw)) continue
      blockLines.push(raw)
    }
  }

  if (blockType !== null) {
    // ⚠️ **未闭合**（有开无闭）→ 降级为普通正文：开标记行已剥，内容一字不丢。
    flushText()
    out.push({ kind: 'text', content: blockLines.join('\n') })
  } else {
    flushText()
  }
  return out
}

/**
 * 剥掉全部标记行，**保留内容**。
 *
 * 供「需要干净正文」的消费点使用：md / 纯文本导出、字数统计。
 *
 * ⚠️ **无标记时原样返回入参**（连 CRLF 都不动）—— 零回归；有标记时才走规范化，
 * 因为要逐行重建。
 * ⚠️ **未闭合的开标记同样被剥掉** —— 与 `parseMarkers` 的降级口径一致，
 * 两侧不同源的症状是「预览里正常、导出里多一行注释」。
 */
export function stripMarkers(md: string): string {
  if (!hasBlocks(md)) return md
  const out: string[] = []
  let inFence = false
  for (const raw of normalize(md).split('\n')) {
    if (FENCE_TOGGLE_RE.test(raw)) {
      inFence = !inFence
      out.push(raw)
      continue
    }
    if (inFence) {
      out.push(raw)
      continue
    }
    if (isMarkerLine(raw)) continue
    out.push(raw)
  }
  return out.join('\n')
}
