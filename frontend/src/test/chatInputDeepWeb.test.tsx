/**
 * 联网的三种形态（关闭 / 本地检索 / 服务端检索）与**发送闸门**。
 *
 * 2026-11-24 之前这里是「联网」+「深度联网」两个复选框。两个布尔量在存储与协议上都
 * 合法，但会派生出「关了联网却开着服务端检索」这种没有意义的组合 —— 于是三处各写
 * 一遍防御（`deepWeb && webSearch`、按钮置灰、后端 `bool(deep_web) and allow_web`）。
 * 收成一个三选一之后，那个组合在界面上**产生不出来**。
 *
 * 两件事放一个文件，因为它们共用同一份状态的两个消费方：
 *
 *  · **选哪个** —— 由 `capabilities.chatDeepWebAvailable` 决定「服务端检索」那一项
 *    出不出现（后端按当前对话模型解析，前端不自己看模型名猜）。
 *  · **请求带什么** —— 三态映射成 `web_search` / `deep_web` 两个参数。
 *
 * ⚠️ 要防的失效是「选项在那儿、选了却被静默降级」：模型换了之后能力位变假，而
 * localStorage 里存的还是 `server`。两处判据都走 `effectiveWebMode`，一处归一掉。
 *
 * ⚠️ **不 mock `api/chat`**：第 2 组断言的就是 `streamChat` 真实拼出来的 URL
 * （mock 掉等于把被测的那一层换掉了，同 `chatWebSearch.test.tsx` 的理由）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { useChatStore } from '../store/chatStore'
import { useStore } from '../store'
import { loadChatPrefs, saveChatPrefs } from '../lib/chat-prefs'
import type { Capabilities } from '../api/core'
import { ChatInput } from '../components/ai-chat/ChatInput'

let fetchUrls: string[] = []

/** 只把能力位塞进去 —— 其余字段本文件不读，故用类型断言而不是造一份完整 capabilities */
const setDeepWebCapability = (available: boolean) =>
  useStore.setState({ capabilities: { chatDeepWebAvailable: available } as unknown as Capabilities })

beforeEach(() => {
  localStorage.clear()
  fetchUrls = []
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
    contextWindow: null, budget: null, lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 }, compressedHint: null,
    clearContextIndex: null, priceIn: null, priceOut: null,
  })
  useStore.setState({ currentDocId: null, docs: [], capabilities: null })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    fetchUrls.push(String(input))
    return { ok: true, status: 200, body: new ReadableStream() } as unknown as Response
  })
})

afterEach(() => { vi.restoreAllMocks() })

const opt = (c: HTMLElement, key: string) =>
  c.querySelector(`[data-web-mode-option="${key}"]`) as HTMLButtonElement | null
const checked = (c: HTMLElement, key: string) =>
  opt(c, key)?.getAttribute('aria-checked') === 'true'

/**
 * 打开「回答设置」浮层 —— 联网 / 追问 / 回答依据都住在里面（2026-11-17 起）。
 * ⚠️ 按钮「在不在」的断言同样要先打开浮层：浮层没开时整张面板都不在 DOM 里，
 * 不打开就断言 `toBeNull()` 会**永远通过**，与选项是否真的没渲染无关。
 */
const openSettings = (c: HTMLElement) =>
  fireEvent.click(c.querySelector('[data-settings-toggle]')!)

// ═══════════════════════════════════════════════════════════
// 1. 三选一：出哪几项、默认哪项、选了落什么
// ═══════════════════════════════════════════════════════════

describe('联网三态', () => {
  it('模型不支持 → 「服务端检索」整项不出现（不是置灰）', () => {
    // 置灰会让用户以为「配置一下就能开」，而他做不了任何事 —— 能开它的唯一办法是
    // 换对话模型，那个入口在设置页，不在这张浮层里。
    setDeepWebCapability(false)
    const { container } = render(<ChatInput scale="md" />)
    openSettings(container)

    expect(opt(container, 'server')).toBeNull()
    expect(opt(container, 'off')).not.toBeNull()
    expect(opt(container, 'local')).not.toBeNull()
  })

  it('模型支持 → 三项都在，默认选中「本地检索」（= 改造前的联网开）', () => {
    setDeepWebCapability(true)
    const { container } = render(<ChatInput scale="md" />)
    openSettings(container)

    expect(checked(container, 'local')).toBe(true)
    expect(checked(container, 'off')).toBe(false)
    expect(checked(container, 'server')).toBe(false)
  })

  it('三选一互斥：选谁就落谁，两个布尔量由一次写入决定', () => {
    setDeepWebCapability(true)
    const { container } = render(<ChatInput scale="md" />)
    openSettings(container)

    fireEvent.click(opt(container, 'off')!)
    expect(loadChatPrefs()).toMatchObject({ webSearch: false, deepWeb: false })
    expect(checked(container, 'off')).toBe(true)
    expect(checked(container, 'local')).toBe(false)

    fireEvent.click(opt(container, 'server')!)
    expect(loadChatPrefs()).toMatchObject({ webSearch: true, deepWeb: true })
    expect(checked(container, 'server')).toBe(true)

    fireEvent.click(opt(container, 'local')!)
    expect(loadChatPrefs()).toMatchObject({ webSearch: true, deepWeb: false })
    expect(checked(container, 'local')).toBe(true)
  })

  it('⚠️ 旧残局（联网关着但 deepWeb 为真）按「关闭」显示，不发服务端', () => {
    // 存储与协议都允许这个组合（老版本或手改 localStorage 都可能留下它）。
    // `webModeOf` 归一成 off —— 界面上不该出现「服务端检索已选中、但其实没联网」。
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: false, deepWeb: true })
    const { container } = render(<ChatInput scale="md" />)
    openSettings(container)

    expect(checked(container, 'off')).toBe(true)
    expect(checked(container, 'server')).toBe(false)
  })

  it('⚠️ 存着 server 但模型不支持 → 显示「本地检索」（不是「服务端」）', () => {
    // 用户在会话中途换了模型。选中态与发送参数必须同一条判据（`effectiveWebMode`），
    // 否则按钮说服务端、实际按本地发，或者反过来发出去被后端每轮回退。
    saveChatPrefs({ webSearch: true, deepWeb: true })
    setDeepWebCapability(false)
    const { container } = render(<ChatInput scale="md" />)
    openSettings(container)

    expect(checked(container, 'local')).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 发送闸门：三态 → 两个请求参数
//
// ⚠️ 参数只在**偏离默认**时才发出去（默认不带，同 `web_search=0` 的纪律）。
// ═══════════════════════════════════════════════════════════

describe('请求参数', () => {
  const send = () => {
    useChatStore.getState().sendMessage('问题')
    return fetchUrls[fetchUrls.length - 1]
  }

  it('默认（本地检索）→ 两个参数都不带', () => {
    setDeepWebCapability(true)
    const url = send()
    expect(url).not.toContain('deep_web')
    expect(url).not.toContain('web_search=0')
  })

  it('关闭 → 带 web_search=0，且不带 deep_web', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: false, deepWeb: false })
    const url = send()
    expect(url).toContain('web_search=0')
    expect(url).not.toContain('deep_web')
  })

  it('服务端检索 + 能力支持 → 带 deep_web=1', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: true, deepWeb: true })
    expect(send()).toContain('deep_web=1')
  })

  it('⚠️ 能力不支持时**不发**（否则后端每轮回退、每轮弹一条提示）', () => {
    setDeepWebCapability(false)
    saveChatPrefs({ webSearch: true, deepWeb: true })
    expect(send()).not.toContain('deep_web')
  })

  it('⚠️ 旧残局（联网关着但 deepWeb 为真）**不发**服务端参数', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: false, deepWeb: true })
    const url = send()
    expect(url).toContain('web_search=0')
    expect(url).not.toContain('deep_web')
  })

  it('三项偏好互不干扰', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: true, deepWeb: true, followUp: true, answerMode: 'free' })
    const url = send()
    expect(url).toContain('deep_web=1')
    expect(url).toContain('follow_up=1')
    expect(url).toContain('answer_mode=free')
  })
})
