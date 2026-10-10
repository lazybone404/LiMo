/**
 * 对话卡片块（`card-group` / `follow-up`）的**内容解析** —— 纯函数，独立单测。
 *
 * ⚠️ **为什么不把块内容直接交给 Markdown 渲染器**：`follow-up` 的每一项要变成一颗
 * **可点击的按钮**，需要拿到「项文本」这个**值**，而不是它渲染出来的 DOM。所以解析停在
 * 「值」这一层，渲染交给组件 —— 与 `markdown-sections.ts`（切卡）同一分工。
 *
 * ⚠️ **围栏感知从 `markdown-scan.ts` 借**（`FENCE_TOGGLE_RE`）：块内代码示例里的
 * `- 这不是追问` 不该变成一颗按钮。该判据与切卡、与标记扫描**同源**（模式 #54：
 * 这里另写一份围栏正则，漂移是静默的）。
 */

import { FENCE_TOGGLE_RE } from './markdown-scan'

/** 列表项：`-` / `*` / `+`，或 `1.` / `1)`，后跟至少一个空白 */
const LIST_ITEM_RE = /^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$/

/** 追问建议最多取几条 —— 比 prompt 要求的「2~4 条」留出余量；再多会把输入区挤爆 */
export const MAX_FOLLOW_UPS = 6

/**
 * 从 `follow-up` 块内容里抽追问项。
 *
 * 解析不到（空块 / 不是列表）→ `[]`，调用方据此**退化为普通正文**（内容一字不丢）。
 * ⚠️ 不在这里截断项的**长度** —— 长问题该完整显示，挤不下是 CSS 的事。
 */
export function parseFollowUpItems(content: string): string[] {
  const out: string[] = []
  let inFence = false
  for (const raw of content.replace(/\r\n?/g, '\n').split('\n')) {
    if (FENCE_TOGGLE_RE.test(raw)) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    const m = LIST_ITEM_RE.exec(raw)
    if (!m) continue
    const text = m[1].trim()
    if (text) out.push(text)
    if (out.length >= MAX_FOLLOW_UPS) break
  }
  return out
}
