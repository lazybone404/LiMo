/**
 * 预览区「当前看的那一版」的**接线**守卫（源码级）。
 *
 * 为什么需要这一层：版本切换改版（2026-10-10）把编辑从「新建一版」改成**就地覆盖
 * 用户正在看的那一版**，于是「前端以为用户在看哪一版」从一个小瑕疵升级成
 * **数据丢失**——它错了就覆盖掉用户没在看的版本、原文不可恢复。
 *
 * 现状是 `markdownMeta` / `markdownVersions` 单值，而 notes 与 polished 是两个独立
 * 产物位、版本号各自从 v1 起。切 tab 时不同步，就会同时坏掉两件事：版本下拉显示
 * 另一个 mode 的版本列表；编辑把另一个 mode 的版号用过来。修法是 `perModeMetaRef`
 * 按 mode 分桶 + 一个切 tab 的同步 effect。
 *
 * ⚠️ **这是结构守卫，不是行为测试**：它防的是「有人把这四处接线删了/挪了」。
 * 「顺序错了」它抓不到（例如 effect 的依赖漏了 `activeView`——那同样是不同步，
 * 但文本仍在）。真正的行为覆盖需要整树渲染 `PreviewArea`（`vi.mock` 掉一整个依赖
 * 图，脆弱且慢），本轮未做，缺口记在 `docs/code-audit-3.md`。
 * 先例：`docTabsWiring.test.ts` / `appShortcutKeys.test.ts`。
 *
 * 对照实验：
 *   A. 删掉 polished 分支里 `perModeMetaRef.current.polished = …` → 第 2 条红。
 *      症状：切到整理版看到的还是笔记的版本列表，编辑还会改错版。
 *   B. 删掉切 tab 的同步 effect → 第 3 条红。症状回到修复前：markdownMeta 滞后。
 *   C. 把 `startEditing` 的 mode 判据简化成直接用 `markdownMeta.version` → 第 5 条红。
 *      症状：`markdownMeta` 一旦滞后就把别的 mode 的版号用过来。
 *   D. 改回内联对象字面量 → 第 6 条红。症状：`chars`/`source` 静默丢失（模式 #54）。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { TabRegistry } from '../lib/tabRegistry'

function src(rel: string): string {
  return readFileSync(resolve(process.cwd(), rel), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')   // 块注释 / JSDoc / JSX 注释
    .replace(/^\s*\/\/.*$/gm, '')       // 整行行注释（`^\s*` 前缀保证不误伤字符串里的 //）
}

describe('每版真相按 mode 分桶', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('两个 mode 各自记录（notes 与 polished 都不能漏）', () => {
    // 对照 A：漏掉任一分支 → 这条红。
    // ⚠️ 改动前 polished 分支**只设内容**，从不写 meta/versions —— 这正是 P1 的根。
    expect(preview).toMatch(/perModeMetaRef\.current\.notes\s*=/)
    expect(preview).toMatch(/perModeMetaRef\.current\.polished\s*=/)
  })

  it('版本切换（loadVersion）也记进分桶', () => {
    // 用户切版后不记 → 切走再切回会退回上一次的版号（编辑跟着改错版）
    const fn = preview.match(/const loadVersion = [\s\S]*?\n  \}/)
    expect(fn, 'loadVersion 切片没取到').toBeTruthy()
    expect(fn![0]).toMatch(/perModeMetaRef\.current\[/)
  })

  it('无产物的 mode 要**清掉**分桶（否则切过去看到上一篇/上一个 mode 的版本）', () => {
    expect(preview).toMatch(/delete perModeMetaRef\.current\.(notes|polished)/)
  })
})

describe('切 tab 时同步当前视图', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('同步 effect 存在，且只对两个产物 tab 生效', () => {
    // 对照 B：删掉这个 effect → 这条红，症状回到「markdownMeta 滞后」。
    // 守卫必须**同时**排除转录/图片/思维导图：它们不持有产物版本概念，
    // 被改写成某一个 mode 的 meta 会让这几个 tab 上的导出带上无关的版本号。
    //
    // ⚠️ 判据必须走 `TabDef.htmlMode`：**笔记 tab 的 key 是 `'summary'` 不是 `'notes'`**。
    //    这条断言的初版就写成了 `activeView !== 'notes'` —— 文本上自洽、语义上永假，
    //    是 tsc 报 TS2367（两个类型无重叠）才暴露出来的。**这正是结构守卫的局限：
    //    它断得住「有没有」，断不住「对不对」。**换成 tsc 看不出的错字面量（例如
    //    `'summary2'`）就只有这条能拦。
    expect(preview).toMatch(/const mode = TabRegistry\.get\(activeView\)\?\.htmlMode/)
    expect(preview).toMatch(/if \(mode !== 'notes' && mode !== 'polished'\) return/)
    expect(preview).toMatch(/const cached = perModeMetaRef\.current\[mode\]/)
    // 依赖数组必须含 activeView —— 漏了它 effect 永不重跑，接线看起来还是完整的
    expect(preview).toMatch(/\}, \[activeView, markdownMeta, markdownVersions,/)
  })

  it('切文档时清空分桶（跨文档串味）', () => {
    // 切文档后 activeView 往往不变 → 同步 effect 不重跑 → 读到上一篇的 meta/版本列表
    expect(preview).toMatch(/perModeMetaRef\.current = \{\}/)
  })

  it('自举：外部直接写入的 markdownMeta 要被认领', () => {
    // 生成完成时 GenPanel 直接写 store（不经过本组件），F4 早退路径同理。
    // 不认领 → 下次从另一个 tab 切回来读到更早的陈旧值。
    expect(preview).toMatch(/else if \(markdownMeta && markdownMeta\.mode === mode\)/)
  })
})

describe('编辑目标版本', () => {
  const preview = src('src/components/preview/PreviewArea.tsx')

  it('startEditing 仍按 mode 校验并兜底 0', () => {
    // 对照 C：简化成 `markdownMeta?.version ?? 0` → 这条红。
    // ⚠️ 兜底 0（= 最新版）**不是无害默认值**——就地覆盖语义下它会改掉用户没在看的
    //    那一版。它只是最后一道防线，不是正常路径（正常路径由上面两条保证）。
    expect(preview).toMatch(/const version = markdownMeta && markdownMeta\.mode === mode \? markdownMeta\.version : 0/)
    // 模式必须由 activeView 推导，不能写死
    expect(preview).toMatch(/const mode: 'notes' \| 'polished' = activeView === 'polished' \? 'polished' : 'notes'/)
  })

  it('MarkdownView → MarkdownMeta 的映射收口在 toMarkdownMeta（无内联字面量）', () => {
    // 对照 D：改回内联 → 这条红。症状：`chars`/`source` 静默丢失，版本下拉那一格空白
    // 且零报错（2026-10-10 加这两个字段时的真实事故形态）。
    expect(preview).not.toMatch(/setMarkdownMeta\(\{\s*id:/)
    expect(preview).toMatch(/toMarkdownMeta\(md\)/)
  })
})

describe('上面那条 effect 判据依赖的前提', () => {
  it('笔记 tab 的 key 是 summary，其 htmlMode 才是 notes', () => {
    // ⚠️ 这是**行为**断言，不是源码断言：effect 的「只对两个产物 tab 生效」全靠这个
    // 映射。哪天笔记 tab 改名、或 htmlMode 换成别的字面量，effect 会**静默失效**
    // （不再同步 → `markdownMeta` 又开始滞后 → 编辑改错版），而源码断言照样全绿。
    expect(TabRegistry.get('summary')?.htmlMode).toBe('notes')
    expect(TabRegistry.get('polished')?.htmlMode).toBe('polished')
    // 反面：别的 tab 不能也命中，否则会被当成产物 tab 改写 markdownMeta
    expect(TabRegistry.get('transcript')?.htmlMode).not.toBe('notes')
    expect(TabRegistry.get('mindmap')?.htmlMode).not.toBe('notes')
  })
})
