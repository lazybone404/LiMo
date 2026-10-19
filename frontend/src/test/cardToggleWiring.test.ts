/**
 * 「章节卡片」工具栏开关**已移除**（2026-10-20，用户要求），开关只住设置页。
 *
 * 为什么删了按钮还留一份源码级守卫：
 *   A. 它摆在正文工具栏上，而骨架只在 `summary` / `polished` 的非 Excel 路径生效 ——
 *      其余 tab（转录 / 图片 / 思维导图 / Excel 数据摘要）切了什么都不发生，是典型的
 *      **死旋钮**（模式 #53）。有人「顺手」把它加回工具栏时，这里要红。
 *   B. 按钮没了，**骨架分派点必须还在**：设置 → 阅读与产物 → 产物骨架 仍能切 `card`，
 *      分派点一旦被删，那个设置项就变成静默无效的旋钮（点了没反应、零报错）。
 *
 * ⚠️ `TabDef.isStandardMarkdown` 已随按钮一并删除（它当时的**唯一**消费者就是那个
 * 门控表达式）。将来若重新需要，请带着新的消费点一起加回来 —— 只留字段不留读者，
 * 下一个人会以为它仍在生效。
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

describe('工具栏不再有章节卡片开关', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('反向断言：正文工具栏的骨架 toggle 没有回来', () => {
    // 对照实验：把按钮加回去 → 这条红。失效形态是「多数 tab 上点了没反应」。
    expect(preview).not.toMatch(/setCard\(\{ skeleton: skeleton === 'card' \? 'paper' : 'card' \}\)/)
    // 连带的旧门控字段也不该回来（它唯一消费者就是那个按钮）
    expect(preview).not.toContain('isStandardMarkdown')
  })

  it('分派点仍在：设置页切了 `card` 必须真的成卡', () => {
    // 对照实验：删掉 `skeleton === 'card'` 分派 → 设置项变成死旋钮，而 tsc 全绿。
    expect(preview).toMatch(/skeleton === 'card'\s*\n?\s*\? <CardSections/)
  })

  it('开关的唯一写入口在设置页', () => {
    const settings = src('src/components/settings/SettingsPage.tsx')
    expect(settings).toContain('setCard({ skeleton: o.key })')
    expect(settings).toContain('SKELETON_OPTIONS')
  })
})
