/**
 * 「章节卡片」开关的**接线 + 门控同源**断言（源码级）。
 *
 * 为什么需要这一层：这个按钮的风险不是「画不出来」，而是**门控写宽**——它摆在正文
 * 工具栏上，而骨架只在 `summary` / `polished` 的非 Excel 路径生效（见下方分派点）。
 * 门控一旦放宽到所有 tab，用户会在转录 / 图片 / 思维导图 / Excel 数据摘要上看到一个
 * **点了什么都不发生**的旋钮（模式 #53），而 tsc / 渲染测试全绿。
 *
 * 三条判据（每条都对应一个「看起来对、其实错」的写法）：
 *   A. 门控条件写成 `activeTabDef?.isStandardMarkdown`（漏 `!typeMeta.isExcel`）
 *      → Excel 的「数据摘要」tab 上多一个死旋钮（它走 MarkdownPreview，无骨架）。
 *   B. 没有门控、按钮常驻 → 同 A 但更广。
 *   C. `tabRegistry` 里给第三个 tab 标上 `isStandardMarkdown: true` → 门控的语义
 *      悄悄变了（A/B 都不会红，因为表达式没动），故必须把**注册表事实**也钉住。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/** 读源码并剥注释 —— 本文件断言的字符串在文档注释里也出现过（照 docTabsWiring 纪律）。 */
function src(rel: string): string {
  return readFileSync(resolve(process.cwd(), rel), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

describe('章节卡片开关的接线', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('按钮存在且切换的是骨架轴（不是风格轴）', () => {
    // 自证唯一：工具栏里只有一个骨架开关，出现两次说明有人加了第二个入口（模式 #54）
    const toggles = [...preview.matchAll(/setCard\(\{ skeleton: skeleton === 'card' \? 'paper' : 'card' \}\)/g)]
    expect(toggles).toHaveLength(1)
  })

  it('⚠️ 门控与骨架分派同源：summary/polished 且非 Excel', () => {
    // 对照 A：去掉 `&& !typeMeta.isExcel` → 这条红
    // 对照 B：去掉整个门控 → 这条红
    expect(preview).toMatch(/\{activeTabDef\?\.isStandardMarkdown && !typeMeta\.isExcel && \(/)
    // 分派点仍在（按钮指向的那个作用域真实存在）
    expect(preview).toMatch(/skeleton === 'card'\s*\n?\s*\? <CardSections/)
  })

  it('高亮 = 已改变默认（默认 paper 时不高亮）', () => {
    // 与右侧装饰开关同一套语义：常驻高亮会变成视觉噪音
    expect(preview).toMatch(/\$\{skeleton === 'card' \? 'bg-accent\/10 text-accent' : 'text-dimmed hover:bg-surface-hover'\}/)
  })
})

describe('门控所依赖的注册表事实', () => {
  it('`isStandardMarkdown: true` 恰好是 summary + polished', () => {
    // 对照 C：给第三个 tab 也标上 → 这条红。
    // 判据链是「门控用到 isStandardMarkdown」⇒「该字段的语义必须仍是
    // 『summary/polished 这条标准 Markdown 渲染路径』」，两边任一漂移都要有人复核。
    const reg = src('src/lib/tabRegistry.ts')
    const keys = [...reg.matchAll(/key: '([\w-]+)'[\s\S]*?isStandardMarkdown: true/g)].map(m => m[1])
    expect(keys.sort()).toEqual(['polished', 'summary'])
  })
})
