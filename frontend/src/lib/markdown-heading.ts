/**
 * 标题文本的清洗与锚点 id —— **纯函数、零 React、零 store**。
 *
 * ⚠️ 为什么从 `markdown-components.tsx` 拆出来（2026-11-08）：渲染侧、TOC 侧、折叠阅读
 * 三处都要算同一个 id。原先它们住在组件文件里，而组件文件 import 了 `useStore` 与
 * 整条 Markdown 渲染链 —— 于是任何「只要算个 id」的纯函数模块（`lib/fold-scan.ts`）
 * 都被迫把 React 与 store 拉进依赖图。拆出来后判据仍是**一份**，只是搬了家。
 *
 * ⚠️ **三处必须同源**：源码侧（TOC 从 `## **重点** 内容` 取）拿到带标记的原文，
 * 渲染侧拿到已解析的 React 节点树，折叠阅读拿到切好的标题行 —— 任一侧多一条规则，
 * 另一侧的 `document.getElementById` 就静默落空（`?.` 吞掉 null，零报错）。
 */

/** 统一的标题 ID 生成 — 供 Markdown 渲染组件、TOC 与折叠阅读共享 */
export function headingId(text: string) {
  return text.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9一-鿿-]/g, '').replace(/-+/g, '-').replace(/^-|-$/g, '')
}

/**
 * 剥掉标题里的行内 Markdown 语法（P1-15）。
 *
 * ⚠️ 图片规则必须排在链接**之前**：`![alt](url)` 会被链接规则吃成 `![alt]`。
 */
export function inlineText(md: string): string {
  return md
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/<[^>]+>/g, '')
}

/**
 * 标题装饰剥离（时间戳 badge + 行内语法）——**渲染侧与 TOC 侧共用这一份**（P1-15）。
 *
 * ⚠️ 此前两侧各写一份、规则还有细微差别（一侧要求 `[▶ 12:30](#)` 的方括号形态，
 *    另一侧只剥行尾；`Screenshot-` 只有一侧认）→ 带时间戳的标题两侧 id 不同，
 *    而两边读起来都像「已经正确处理了时间戳」。
 *
 * 三种形态都要认（源码态 / 渲染后态各不同）：
 *   `[▶ 12:30](#)`（源码链接）→ inlineText 后是 `▶ 12:30`
 *   `Content-[12:30]` / `Screenshot-[12:30]`（badge 源码）
 *   `12:30`（渲染后 badge 只剩文本，且总在行尾）
 */
export function stripHeadingDecor(text: string): string {
  return inlineText(text)
    .replace(/\s*\*?(?:Content|Screenshot)-\[\d{2}:\d{2}\]/g, '')
    .replace(/\s*▶\s*\d{2}:\d{2}/g, '')
    .replace(/\s*\d{2}:\d{2}\s*$/, '')
    .trim()
}
