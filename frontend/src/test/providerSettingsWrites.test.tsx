// ProviderSettings — 写操作失败必须可见（P1-13）
//
// 背景：这个组件有 18 个异步处理器，修复前**只有 3 个**自己 catch（报告写「19 个
// 处理器 / 6 处 catch」，其中 2 处 catch 不在处理器内）。其余在后端 400（Key 为空 /
// 厂商不存在）或网络不可达时抛出的异常**无人接住** —— 既没有成功 toast 也没有失败
// toast，而输入框里刚打的 Key 仍在显示（值来自本地 state）→ 看起来就是保存成功了，
// 直到某次生成报鉴权错。
//
// 修复 = 统一走 `runWrite(label, fn)`（catch → error toast）。本文件两条判据成对：
//
//   · **行为**：保存失败真的出 error toast（且成功时不误报 error）；
//   · **守卫**：所有异步处理器都经 runWrite —— 否则「今天全包上了」这件事没有任何
//     机制保证，下一个新增的处理器照旧可以裸写 `async () => {}`。
//
// ⚠️ 对照实验（模式 #38，都实测过）：
//   · 撤掉 `runWrite` 的 catch → 第 1 条红（且 promise rejection 逃逸）；
//   · 把任一处理器改回 `= async () => {` → 守卫条红（集合多出一项）；
//   · 把 `runWrite` 里的 `'error'` 改成 `'success'` → 第 1 条红。

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { ProviderSettings } from '../components/settings/ProviderSettings'
import { useStore } from '../store'

// 可变实现：本文件需要让某次调用**失败**，而 `vi.mock` 工厂被提升到文件顶部，
// 只能经 `vi.hoisted` 的容器间接引用（直接引用外部变量会报「提升越界」）。
const h = vi.hoisted(() => ({
  updateProvider: async (..._a: unknown[]) => ({}),
}))

vi.mock('../api/client', () => ({
  listProviders: async () => [{ id: 'deepseek', name: 'DeepSeek', type: 'built-in', enabled: 1, api_key: '' }],
  listEnabledModels: async () => [],
  getSceneModels: async () => ({ generate: null, chat: null, image_process: null }),
  listEngineProviders: async () => [],
  listEngineModels: async () => [],
  getEngineBindings: async () => ({}),
  getEngineRegistry: async () => ({}),
  updateProvider: (...a: unknown[]) => h.updateProvider(...a),
  addProvider: async () => ({}),
  deleteProvider: async () => ({}),
  testProviderConnection: async () => ({ ok: true }),
  refreshModels: async () => ({ models: [] }),
  addModel: async () => ({}),
  deleteModel: async () => ({}),
  updateModel: async () => ({}),
  setSceneModels: async () => ({}),
  getConfig: async () => ({}),
  updateConfig: async () => ({}),
  addEngineProvider: async () => ({}),
  deleteEngineProvider: async () => ({}),
  deleteEngineModel: async () => ({}),
  saveEngineBindings: async () => ({}),
  apiUrl: (p: string) => p,
  apiFetch: async () => ({}),
}))

// ⚠️ 用 `process.cwd()` 而非 `import.meta.url` —— 后者在 jsdom 下是 http:// 协议，
//    `fileURLToPath` 会直接抛「URL must be of scheme file」（与 appShortcutKeys.test.ts 同法）
const SRC = resolve(process.cwd(), 'src/components/settings/ProviderSettings.tsx')

/** 填 Key 后失焦 —— 组件的保存是 `onBlur` 触发（不是点按钮）。 */
async function typeKeyAndBlur(getAllByPlaceholderText: (t: string) => HTMLElement[], value: string) {
  const input = await vi.waitFor(() => {
    const els = getAllByPlaceholderText('API Key（输入后自动保存）')
    expect(els.length).toBeGreaterThan(0)
    return els[0]
  })
  fireEvent.change(input, { target: { value } })
  fireEvent.blur(input)
  await vi.waitFor(() => expect(useStore.getState().toasts.length).toBeGreaterThan(0))
}

describe('ProviderSettings — 写操作失败可见（P1-13）', () => {
  beforeEach(() => {
    useStore.setState({ toasts: [], capabilities: null })
    h.updateProvider = async () => ({})
  })

  it('保存失败 → error toast 带后端原因（不再是「看起来保存成功了」）', async () => {
    h.updateProvider = async () => { throw new Error('HTTP 400: 该厂商不存在') }

    const { getAllByPlaceholderText } = render(<ProviderSettings />)
    await typeKeyAndBlur(getAllByPlaceholderText, 'sk-会失败')

    const t = useStore.getState().toasts
    expect(t[0].type).toBe('error')
    // 文案要能读懂：动作名 + 后端原话（「保存 API Key失败：HTTP 400: 该厂商不存在」）
    expect(t[0].message).toContain('保存 API Key失败')
    expect(t[0].message).toContain('该厂商不存在')
  })

  it('对照：保存成功 → success toast，不产生 error', async () => {
    // 防「一律弹 error」也能过第一条 —— 判据要能区分成败两种结果。
    const { getAllByPlaceholderText } = render(<ProviderSettings />)
    await typeKeyAndBlur(getAllByPlaceholderText, 'sk-正常')

    const t = useStore.getState().toasts
    expect(t.some(x => x.message.includes('API Key 已保存'))).toBe(true)
    expect(t.filter(x => x.type === 'error')).toEqual([])
  })
})

describe('ProviderSettings — 处理器包装守卫（P1-13）', () => {
  it('所有异步处理器都经 runWrite（新增处理器漏包装必红）', () => {
    // 判据是**集合相等**而不是「出现过 runWrite」：后者在一个处理器都没包上、
    // 只留 `runWrite` 定义时也成立。
    //
    // 对照：把任一处 `= () => runWrite(` 改回 `= async () => {` → 集合多出那一个名字。
    const src = readFileSync(SRC, 'utf-8')
    const names = [...src.matchAll(/const\s+(\w+)\s*=\s*async\s*\(/g)].map(m => m[1])
    expect(new Set(names)).toEqual(new Set(['loadAll', 'runWrite']))
  })

  it('runWrite 自己确实 catch 并出 error toast', () => {
    // ⚠️ 这条单独存在的理由：上一守卫只查「调没调 runWrite」。若 runWrite 内部被
    //    改成 `await fn()`（不 catch），所有处理器照样「都经 runWrite」、上一守卫
    //    全绿，而失败重新变得无声 —— 这正是「断言存在 ≠ 断言有效」（模式 #38）。
    const src = readFileSync(SRC, 'utf-8')
    const body = src.match(/const runWrite = async[\s\S]*?\n  \}/)
    expect(body, 'runWrite 定义没匹配到 —— 结构改了就该同步改本守卫').toBeTruthy()
    expect(body![0]).toContain('catch')
    expect(body![0]).toContain("'error'")
  })
})
