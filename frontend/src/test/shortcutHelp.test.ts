/**
 * 快捷键一览（`lib/shortcuts.ts`）↔ `App.tsx::handleAppKeyDown` 的**单向**交叉守卫。
 *
 * ⚠️ 文件名刻意**不是** `shortcutKeys.test.ts` —— 它和既有的
 * `appShortcutKeys.test.ts` 只差一个前缀，将来一定有人把它们看混。那两个文件管的是
 * 完全不同的两件事：
 *   · `appShortcutKeys.test.ts` —— App.tsx ↔ Rust 白名单（**执行**是否生效，打包版才暴露）
 *   · 本文件                     —— App.tsx ↔ 展示表（**帮助面板说得对不对**，用户才暴露）
 *
 * 判据：App.tsx 认下的**每一个** `case 'x'` 都必须在 `SHORTCUTS` 里出现，
 * 且因为 `handleAppKeyDown` 开头就是 `if (!(e.metaKey || e.ctrlKey)) return`，
 * 表里这些条目的 `mods` 必须含 `'ctrl'`。
 *
 * **反向不校验**：局部快捷键要不要收录是产品取舍，不是漂移 —— 逼着两边一一对应
 * 会让表里塞满 `Esc` 这种一键三义的条目。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { SHORTCUTS } from '../lib/shortcuts'

/** 剥注释 —— App.tsx 的注释里刻意引用了 `case '\\'` 等字样当说明 */
function stripComments(s: string): string {
  return s
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

/** `handleAppKeyDown` 里所有 `case 'x':` 的 `e.key` 字面量（已还原转义） */
function appShortcutKeys(): string[] {
  const src = stripComments(readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf-8'))
  const fn = src.match(/export function handleAppKeyDown[\s\S]*?\n\}/)
  expect(fn).toBeTruthy() // 自证：切片真的取到了函数体

  const keys = [...fn![0].matchAll(/case '((?:\\\\|\\.|[^'\\])*)':/g)].map(m => m[1])
  expect(keys.length).toBeGreaterThan(0) // 自证：正则扫到了 case（不是静默零命中）

  return keys.map(k => k.replace(/\\\\/g, '\\'))
}

describe('快捷键帮助表与 App.tsx 的真实绑定一致', () => {
  it('App.tsx 认的每个键都在 SHORTCUTS 里有一条（且都带 Ctrl）', () => {
    // 对照实验：从 `lib/shortcuts.ts` 删掉 `keys: ['/']` 那条 → 这里红
    const tableKeys = new Set(SHORTCUTS.flatMap(s => s.keys))
    const tableCtrlKeys = new Set(
      SHORTCUTS.filter(s => s.mods.includes('ctrl')).flatMap(s => s.keys),
    )

    for (const k of appShortcutKeys()) {
      expect(tableKeys.has(k), `App.tsx 里的快捷键 ${JSON.stringify(k)} 没在 SHORTCUTS 里登记`).toBe(true)
      expect(
        tableCtrlKeys.has(k),
        `${JSON.stringify(k)} 在 App.tsx 里要求 Ctrl/Meta，表里却没标 ctrl —— 帮助面板会写成裸键`,
      ).toBe(true)
    }
  })

  it('同一分组内没有重复条目', () => {
    const seen = new Set<string>()
    for (const s of SHORTCUTS) {
      for (const k of s.keys) {
        // ⚠️ id **带 group**：同一个 key 跨分组出现是合法的 —— `Escape` 既是「退出沉浸」
        // 也是「清空选择」，`Ctrl+滚轮` 在预览区与消息区各调各的字号。按全局去重会把
        // 这类刻意分家的条目判成重复，逼人删掉一条。
        const id = `${s.group}|${s.mods.slice().sort().join('+')}|${k}`
        expect(seen.has(id), `分组内重复登记：${id}`).toBe(false)
        seen.add(id)
      }
    }
  })

  it('每条都有 label 与非空 keys（空 label 会在面板上留一行空白）', () => {
    for (const s of SHORTCUTS) {
      expect(s.label.trim().length).toBeGreaterThan(0)
      expect(s.keys.length).toBeGreaterThan(0)
    }
  })
})
