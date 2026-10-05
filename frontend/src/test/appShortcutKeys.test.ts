/**
 * 应用快捷键的**跨语言清单守卫**。
 *
 * 同一个快捷键集合被写在两个语言、两个文件里：
 *   - `src/App.tsx::handleAppKeyDown` 的 `switch (e.key)` —— 真正干活的
 *   - `src-tauri/src/lib.rs::webview_keys::is_app_shortcut` —— 打包版里把这几个键
 *     从 WebView2 加速键手里放行到网页
 *
 * 后者**只在打包版里生效**，漏一个键的症状是「开发模式一切正常、打包版那个键没反应」，
 * 且没有任何报错 —— 属于最难自查的一类缺陷。所以两边必须由机器核对，不能靠记性
 * （本项目 #54 号模式：写下「N 处同源」的那一刻就欠了一次全仓验证）。
 *
 * 反向也测：Rust 侧放行了 App.tsx 不处理的键，同样是漂移（只是症状较轻）。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/** 剥注释 —— 两边的注释里都刻意引用了 `case '='` / `VK_OEM_PLUS` 等字样当说明 */
function stripComments(s: string): string {
  return s
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

/**
 * `e.key` 的字面量 → Win32 虚拟键码。
 *
 * ⚠️ 同一物理键的 Shift 变体映射到**同一个 VK**（VK 与 Shift 无关）：
 * `'+'` 是 Shift+`'='`、`'_'` 是 Shift+`'-'` —— 这正是 `App.tsx` 里
 * 「`=` 与 `+` 必须都列」而 Rust 侧只需一个常量的原因。
 */
const KEY_TO_VK: Record<string, number> = {
  g: 0x47,
  i: 0x49,
  k: 0x4b,
  w: 0x57,
  '0': 0x30,
  '=': 0xbb,
  '+': 0xbb,
  '-': 0xbd,
  _: 0xbd,
  '\\': 0xdc,
}

/** `handleAppKeyDown` 里 `case 'x':` 认下的所有 key 字面量（已还原转义） */
function appShortcutVks(): number[] {
  const src = stripComments(readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf-8'))
  const fn = src.match(/export function handleAppKeyDown[\s\S]*?\n\}/)
  expect(fn).toBeTruthy() // 自证：切片真的取到了函数体

  const keys = [...fn![0].matchAll(/case '((?:\\\\|\\.|[^'\\])*)':/g)].map((m) => m[1])
  expect(keys.length).toBeGreaterThan(0) // 自证：正则真的扫到 case 了（不是静默零命中）

  return keys.map((k) => {
    const ch = k.replace(/\\\\/g, '\\') // 源码里的 `'\\'` → 一个反斜杠
    const vk = KEY_TO_VK[ch]
    expect(vk, `App.tsx 里的快捷键 ${JSON.stringify(ch)} 没在 KEY_TO_VK 登记`).toBeDefined()
    return vk!
  })
}

/** Rust 侧 `is_app_shortcut` 放行的所有虚拟键码 */
function rustShortcutVks(): number[] {
  const src = stripComments(
    readFileSync(resolve(process.cwd(), 'src-tauri/src/lib.rs'), 'utf-8'),
  )

  const consts = new Map<string, number>()
  for (const m of src.matchAll(/const (VK_\w+): u32 = (0x[0-9a-fA-F]+);/g)) {
    consts.set(m[1], parseInt(m[2], 16))
  }
  expect(consts.size).toBeGreaterThan(0) // 自证：常量表解析到了

  const call = src.match(/fn is_app_shortcut\(vk: u32\) -> bool \{[\s\S]*?matches!\(([\s\S]*?)\)/)
  expect(call).toBeTruthy() // 自证：matches! 取到了

  const names = [...call![1].matchAll(/VK_\w+/g)].map((m) => m[0])
  expect(names.length).toBeGreaterThan(0)

  return names.map((n) => {
    const vk = consts.get(n)
    expect(vk, `lib.rs 的 matches! 引用了未定义的 ${n}`).toBeDefined()
    return vk!
  })
}

describe('应用快捷键清单在 App.tsx 与 lib.rs 之间同步', () => {
  it('两侧放行的键集合完全相同', () => {
    // 对照实验：删掉 Rust `matches!` 里的 `VK_W` → 这条红（打包版 Ctrl+W 会失效）
    const app = [...new Set(appShortcutVks())].sort((a, b) => a - b)
    const rust = [...new Set(rustShortcutVks())].sort((a, b) => a - b)
    expect(rust).toEqual(app)
  })

  it('App.tsx 的 Shift 变体与基础键共用同一个 VK（Rust 侧无需重复登记）', () => {
    // `'+'`/`'_'` 不是独立的物理键，写两个 Rust 常量是错的
    expect(KEY_TO_VK['+']).toBe(KEY_TO_VK['='])
    expect(KEY_TO_VK['_']).toBe(KEY_TO_VK['-'])
  })
})
