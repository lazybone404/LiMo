/**
 * 标签建议 —— **纯本地词表命中**：零 LLM、零网络、零磁盘、零落盘。
 *
 * ## 为什么是本地匹配而不是让模型顺便判定
 *
 * 让模型在生成正文时顺手写标签，成本上看几乎免费（输出几十 token），但真正的代价
 * 不在钱：它把**注意力**从正文上分走，而产物上完全看不出异常——没有对照组能证明
 * 「正文质量没下降」。同一个坑追问侧已经踩过（思考模式会把输出预算烧在推理链上）。
 *
 * 本地命中的代价是**只能复用库里已有的标签**（发现不了新词）。这与既有产品判据
 * 一致：智能分类的 `tagMode` 默认就是「优先复用」，新词的发现交给智能分类
 * （用户主动触发、一次调用处理 N 篇、带审定环节）。
 *
 * ⚠️ **刻意不落盘**。建议是**可重算的派生视图**——正文一改（编辑就地覆盖当前版）、
 * 词表一变（用户手打新标签），缓存的建议立刻过时且看不出来。这与 `chars` / `edited`
 * 现算、`followUps` 落旁路文件是同一条判据的两端：**重算要花钱的才存**。
 *
 * ## 匹配规则
 *
 * 1. **中文等非 ASCII 标签**走子串匹配（中文没有词边界概念）。
 * 2. **ASCII 标签**必须走词边界：`AI` 不能命中 `MAIL`、`React` 不能命中 `Reaction`。
 *    ⚠️ 这条不是洁癖——库里最常见的标签恰好是英文缩写，漏了它整排建议都是噪音。
 * 3. 标题命中优先于正文命中（缓解「正文顺带提了一句 React」）。
 * 4. 去冗余：`前端` 与 `前端框架` 同时命中、且 `前端` 的每次出现都在 `前端框架` 里
 *    （出现次数相同）⇒ 丢掉 `前端`。否则一排 chip 里一半是彼此的前缀。
 */

export interface TagSuggestion {
  tag: string
  /** 在标题里出现过 —— UI 可以据此加一层强调 */
  inTitle: boolean
}

/**
 * 统计 `needle` 在 `haystack` 里的出现次数。0 = 没命中。
 *
 * ⚠️ **ASCII / 非 ASCII 两支判据不同源是刻意的**：中文没有词边界，
 * 套 `\b` 只会让「前端」这种正常标签永远匹配不上。
 */
export function countOccurrences(haystack: string, needle: string): number {
  if (!haystack || !needle) return 0
  // ASCII（可打印字符）：两侧不能紧邻字母数字，防短缩写命中长单词的中间
  if (/^[\x20-\x7e]+$/.test(needle)) {
    const esc = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const re = new RegExp(`(?<![A-Za-z0-9])${esc}(?![A-Za-z0-9])`, 'gi')
    return haystack.match(re)?.length ?? 0
  }
  let n = 0
  let i = haystack.indexOf(needle)
  while (i !== -1) {
    n++
    i = haystack.indexOf(needle, i + needle.length)
  }
  return n
}

/**
 * 从库里已有的标签中，挑出正文里真的出现过的那几个。
 *
 * @param title    文档标题（命中优先）
 * @param content  这篇产物的正文（**当前 tab 的**，不是原文）
 * @param vocab    库里全部标签（跨文档去重后）
 * @param existing 本文档**已有**的标签 —— 已打的不再建议
 * @param limit    最多给几个（默认 4：再多就是「一排噪音」，这正是要避免的）
 */
export function suggestTags(
  title: string,
  content: string,
  vocab: string[],
  existing: string[] = [],
  limit = 4,
): TagSuggestion[] {
  const t = title || ''
  const c = content || ''
  const have = new Set(existing.map((x) => (x || '').trim().toLowerCase()).filter(Boolean))
  const seen = new Set<string>()
  const scored: { tag: string; inTitle: boolean; count: number }[] = []

  for (const raw of vocab) {
    const tag = (raw || '').trim()
    if (!tag) continue
    const key = tag.toLowerCase()
    if (have.has(key) || seen.has(key)) continue
    seen.add(key)
    const inTitle = countOccurrences(t, tag) > 0
    const count = countOccurrences(c, tag)
    if (!inTitle && !count) continue
    scored.push({ tag, inTitle, count })
  }

  // 去冗余：A 是 B 的子串、且正文出现次数相同 ⇒ A 的每次出现都在 B 内部。
  // ⚠️ 只在**同一命中层级**内比较（标题命中与正文命中不互相吞），否则
  //    「标题里出现过一次的『AI』」会被「正文里的『AI 工具』」误杀。
  const kept = scored.filter((a) => !scored.some((b) =>
    b.tag !== a.tag
    && b.inTitle === a.inTitle
    && b.tag.length > a.tag.length
    && b.tag.includes(a.tag)
    && b.count === a.count))

  kept.sort((a, b) =>
    Number(b.inTitle) - Number(a.inTitle)
    || b.count - a.count
    || b.tag.length - a.tag.length
    || a.tag.localeCompare(b.tag))

  return kept.slice(0, Math.max(0, limit)).map(({ tag, inTitle }) => ({ tag, inTitle }))
}
