/**
 * 正文字号的**键盘接线**测试（需求 B）。
 *
 * 为什么单开一个文件、而且断的是 `handleAppKeyDown` 而不是 store 动作：
 * `stepFontScale` 本身已经被 `readingPrefs.test.ts` + store 覆盖得好好的，但
 * 「Ctrl+= 到底有没有接在它上面」是**另一回事**——这是本项目 #51 号模式
 * （「能力存在 ≠ 该路径在用它」）：换算函数写得再对、`stepFontScale` 再正确，
 * 只要 switch 里漏了个 case，用户按下去就是没反应，而所有 store 测试照样全绿。
 *
 * `handleAppKeyDown` 是 `export` 的（App.tsx 顶部注释写明：抽出来就是为了能直接喂
 * 一个假事件），所以这里跑的是**真实实现**，不是复刻的表达式。
 *
 * 对照实验：
 *   A. 删掉 `case '+':`（只留 `'='`）→ 「Shift+等号」用例红。
 *      这不是学究：`+` 是 Shift+`=` 的 `e.key`，只写 `=` 会让「按住 Shift 放大」
 *      静默失效，而按住 Shift 恰恰是多数人敲 `+` 的方式。
 *   B. 删掉 `e.preventDefault()` → 对应用例红。键盘路径的 preventDefault 就是
 *      「不要让 WebView2 缩放整个应用」的唯一防线，漏了它症状是**双份缩放**。
 *   C. 把 `if (!(e.metaKey || e.ctrlKey)) return` 改成只看 ctrlKey → Meta 用例红。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useStore } from '../store'
import { handleAppKeyDown } from '../App'
import { FONT_SCALE_RANGE, READING_DEFAULTS } from '../lib/reading-prefs'

// App 顶层会 import 一串组件与网络模块；这里只要那个导出的纯函数，
// 把带副作用的两个入口挡掉，免得 jsdom 里跑出无关的报错。
vi.mock('../lib/health', () => ({ startHealthPoll: () => () => {} }))
vi.mock('../lib/configEvents', () => ({ onConfigChanged: () => () => {} }))
vi.mock('../api/client', () => ({
  fetchCapabilities: vi.fn(),
  listTranscripts: vi.fn(),
  listFolders: vi.fn(),
  getMarkdown: vi.fn(),
  getTranscript: vi.fn(),
  apiUrl: (p: string) => p,
}))

/** 最小可用的 React 合成事件替身——`handleAppKeyDown` 只读 key / 修饰键 / preventDefault */
function key(k: string, mods: { ctrl?: boolean; meta?: boolean; shift?: boolean } = {}): ReactKeyboardEvent {
  return {
    key: k,
    ctrlKey: !!mods.ctrl,
    metaKey: !!mods.meta,
    shiftKey: !!mods.shift,
    preventDefault: vi.fn(),
  } as unknown as ReactKeyboardEvent
}

const scale = () => useStore.getState().reading.fontScale
const toasts = () => useStore.getState().toasts

beforeEach(() => {
  useStore.setState({ reading: { ...READING_DEFAULTS }, toasts: [] })
})

describe('Ctrl+ 键盘路径 → 字号', () => {
  it('Ctrl+= 放大一档，并阻止默认行为', () => {
    const e = key('=', { ctrl: true })
    handleAppKeyDown(e)
    expect(scale()).toBe(1.1)
    // 对照 B：preventDefault 漏掉 → 这条红。它是「WebView2 别缩放整个应用」的唯一防线
    expect(e.preventDefault).toHaveBeenCalled()
  })

  it('⚠️ Ctrl++ （Shift+等号）与 Ctrl+= 等价', () => {
    // 对照 A：只写 case '=' 的实现 → 这条红。`+` 是 Shift+`=` 的 e.key，
    // 而按住 Shift 敲 `+` 正是多数人的习惯动作
    const e = key('+', { ctrl: true, shift: true })
    handleAppKeyDown(e)
    expect(scale()).toBe(1.1)
    expect(e.preventDefault).toHaveBeenCalled()
  })

  it('Ctrl+- 与 Ctrl+_ 都缩小一档', () => {
    useStore.setState({ reading: { ...READING_DEFAULTS, fontScale: 1.2 } })
    handleAppKeyDown(key('-', { ctrl: true }))
    expect(scale()).toBe(1.1)
    handleAppKeyDown(key('_', { ctrl: true, shift: true }))
    expect(scale()).toBe(1.0)
  })

  it('Ctrl+0 重置为 100%', () => {
    useStore.setState({ reading: { ...READING_DEFAULTS, fontScale: 1.4 } })
    const e = key('0', { ctrl: true })
    handleAppKeyDown(e)
    expect(scale()).toBe(READING_DEFAULTS.fontScale)
    expect(e.preventDefault).toHaveBeenCalled()
  })

  it('Meta（macOS Cmd）与 Ctrl 等效', () => {
    // 对照 C：守卫写成只看 ctrlKey → 这条红
    handleAppKeyDown(key('=', { meta: true }))
    expect(scale()).toBe(1.1)
  })

  it('无修饰键时**什么都不做**，且不吞掉按键', () => {
    // 裸 `=` / `0` 是正常打字输入，劫持它们会让输入框打不出等号和零
    const e = key('=')
    handleAppKeyDown(e)
    expect(scale()).toBe(1.0)
    expect(e.preventDefault).not.toHaveBeenCalled()
  })

  it('到顶后再按 → 值不变、且不再弹 toast（滚轮刷屏防线）', () => {
    useStore.setState({ reading: { ...READING_DEFAULTS, fontScale: FONT_SCALE_RANGE.max }, toasts: [] })
    handleAppKeyDown(key('=', { ctrl: true }))
    expect(scale()).toBe(FONT_SCALE_RANGE.max)
    expect(toasts()).toHaveLength(0)
  })

  it('值真的变化时才弹 toast（数字与用户看到的一致）', () => {
    handleAppKeyDown(key('=', { ctrl: true }))
    expect(toasts().map(t => t.message)).toEqual(['字号 110%'])
  })
})
