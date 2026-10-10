/**
 * 流式「半截标记行」的抑制 —— `limo-block` 在**流式渲染**下的新问题。
 *
 * `parseMarkers` 是为**完整字符串**设计的，从没在 partial 上跑过。chunk 会把开标记
 * 切在中间（`<!-- limo-block:v1 type="card-`），这种半截行**既不匹配 `BLOCK_OPEN_RE`
 * （缺 `-->`）也不匹配 `MARKER_LINE_RE`** → 落到「普通正文」分支被渲染出来，
 * 用户看着原始协议文本从眼前飘过。这正是本轮要治的核心症状。
 *
 * 对照实验（对应一个「看起来对、其实错」的实现）：
 *   A. 朴素判据「以 `<!--` 开头且没有 `-->` 就算半截」→ 把**正在输入的合法 HTML 注释**
 *      （`<!-- 这是一段说明`）一起吞掉，正文瞬断。下面的对照用例专门证明这条判据
 *      **确实有区分力**（不是写个恒真函数骗过自己）。
 *   B. 削**任意**一行的半截标记而不是只削尾行 → 正文中间被挖出空洞
 *      （半截行必定在末尾：chunk 只向后追加，下一个 chunk 到齐它就补全了）。
 */

import { describe, it, expect } from 'vitest'
import { isPartialMarkerLine, suppressTrailingPartialMarker } from '../lib/markdown-scan'

/** 对照实验用的**朴素**判据（刻意不认命名空间） */
const naivePartial = (line: string) => line.trimStart().startsWith('<!--') && !line.includes('-->')

describe('isPartialMarkerLine — 该抑制的', () => {
  it('刚敲出 `<!--`', () => {
    expect(isPartialMarkerLine('<!--')).toBe(true)
  })

  it('标记名写到一半（前缀）', () => {
    expect(isPartialMarkerLine('<!-- limo')).toBe(true)
    expect(isPartialMarkerLine('<!-- limo-bl')).toBe(true)
    expect(isPartialMarkerLine('<!--limo-bl')).toBe(true)
  })

  it('标记名已完整、属性还没写完', () => {
    expect(isPartialMarkerLine('<!-- limo-block:v1 type="card-')).toBe(true)
    expect(isPartialMarkerLine('<!-- limo-block:v1 type=')).toBe(true)
  })

  it('闭标记写到一半（可选斜杠与空白都要能跳过）', () => {
    expect(isPartialMarkerLine('<!-- /limo-bl')).toBe(true)
    expect(isPartialMarkerLine('<!--/limo')).toBe(true)
  })

  it('前导空白不影响判定', () => {
    expect(isPartialMarkerLine('   <!-- limo-block')).toBe(true)
  })
})

describe('isPartialMarkerLine — **不**该抑制的', () => {
  it('已写完的合法 HTML 注释', () => {
    expect(isPartialMarkerLine('<!-- note -->')).toBe(false)
  })

  it('⚠️ 正在输入、尚未敲 `-->` 的**合法**注释（朴素判据会在这里出错）', () => {
    expect(isPartialMarkerLine('<!-- 这是一段说明')).toBe(false)
    expect(isPartialMarkerLine('<!-- TODO: 待补')).toBe(false)
  })

  it('行内出现的注释（不是独占一行）', () => {
    expect(isPartialMarkerLine('正文 <!-- 内联')).toBe(false)
  })

  it('普通文字 / 空行', () => {
    expect(isPartialMarkerLine('普通正文')).toBe(false)
    expect(isPartialMarkerLine('')).toBe(false)
  })

  it('词根相近但不匹配命名空间', () => {
    expect(isPartialMarkerLine('<!-- notlimo-block')).toBe(false)
    expect(isPartialMarkerLine('<!-- limo-blocks')).toBe(false)
  })
})

describe('对照实验 —— 命名空间判据**确实**与朴素判据不同', () => {
  it('朴素判据会吞掉正在输入的合法注释，我们的不会', () => {
    // ⚠️ 这条是「判据有区分力」的证据：如果两个函数在这里行为相同，
    // 说明命名空间感知形同虚设，前面那组「不该抑制」的用例也就没有在测任何东西。
    const legal = '<!-- 这是一段说明'
    expect(naivePartial(legal)).toBe(true)
    expect(isPartialMarkerLine(legal)).toBe(false)
  })
})

describe('suppressTrailingPartialMarker', () => {
  it('尾行是半截标记 → 连同它前面的换行一起削掉', () => {
    expect(suppressTrailingPartialMarker('正文\n<!-- limo-block:v1 type="card-')).toBe('正文')
  })

  it('段落分隔保留：只削最后一行，不吞前面的空行', () => {
    expect(suppressTrailingPartialMarker('正文\n\n<!-- limo')).toBe('正文\n')
  })

  it('整串就是半截标记 → 空串', () => {
    expect(suppressTrailingPartialMarker('<!-- limo')).toBe('')
  })

  it('无需抑制时**原样返回入参引用**（流式每帧都调用，省掉无谓的重建）', () => {
    const md = '正文\n第二行'
    expect(suppressTrailingPartialMarker(md)).toBe(md)
  })

  it('末尾空行不动（最常见的「什么都没发生」）', () => {
    const md = '正文\n\n'
    expect(suppressTrailingPartialMarker(md)).toBe(md)
  })

  it('空串 / 无换行的普通文本都不崩', () => {
    expect(suppressTrailingPartialMarker('')).toBe('')
    expect(suppressTrailingPartialMarker('普通正文')).toBe('普通正文')
  })

  it('对照 B：**中间**的半截标记行不被削（削了会在正文里挖出空洞）', () => {
    const md = '<!-- limo\n正文'
    expect(suppressTrailingPartialMarker(md)).toBe(md)
  })

  it('只影响渲染用字符串 —— 入参本身不变', () => {
    const md = '正文\n<!-- limo'
    suppressTrailingPartialMarker(md)
    expect(md).toBe('正文\n<!-- limo')
  })
})
