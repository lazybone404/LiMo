/**
 * 「深度联网」按钮与**发送闸门**。
 *
 * 两件事放一个文件，因为它们共用同一个判据的两个消费方：
 *
 *  · **按钮渲不渲染** —— 由 `capabilities.chatDeepWebAvailable` 决定（后端按当前对话模型
 *    解析出来的，前端不自己看模型名猜）。
 *  · **请求带不带 `deep_web=1`** —— 三个条件同时为真：偏好开着、联网开着、能力支持。
 *
 * ⚠️ 要防的失效是「按钮在那儿、点了却被静默忽略」：只要两处判据不同源（比如按钮只看偏好、
 * 发送只看能力，或反过来），就会出现「界面显示已开启，后端每轮回退普通模式」——不报错，
 * 只有一条一闪而过的 info toast。故本文件的每条断言都对应「两处之一写错就会红」。
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

const btn = (c: HTMLElement) => c.querySelector('[data-deepweb-toggle]')
const stateOf = (c: HTMLElement) => btn(c)?.getAttribute('data-deepweb-toggle')

// ═══════════════════════════════════════════════════════════
// 1. 按钮：渲不渲染、能不能点
// ═══════════════════════════════════════════════════════════

describe('ChatInput 的深度联网按钮', () => {
  it('模型不支持 → 整个按钮不渲染（不是置灰）', () => {
    // 置灰会让用户以为「配置一下就能开」，而他做不了任何事 —— 能开它的唯一办法是
    // 换对话模型，那个入口在设置页，不在这个按钮上。
    setDeepWebCapability(false)
    const { container } = render(<ChatInput scale="md" />)
    expect(btn(container)).toBeNull()
  })

  it('模型支持 → 默认关闭态', () => {
    setDeepWebCapability(true)
    const { container } = render(<ChatInput scale="md" />)
    expect(stateOf(container)).toBe('off')
  })

  it('点一下 → 落盘并变开启态', () => {
    setDeepWebCapability(true)
    const { container } = render(<ChatInput scale="md" />)
    fireEvent.click(btn(container)!)

    expect(loadChatPrefs().deepWeb).toBe(true)
    expect(stateOf(container)).toBe('on')

    fireEvent.click(btn(container)!)
    expect(loadChatPrefs().deepWeb).toBe(false)
    expect(stateOf(container)).toBe('off')
  })

  it('联网关着 → 置灰且点了无效（不是隐藏）', () => {
    // 隐藏会让「联网一关，旁边那个按钮不见了」看起来像应用坏了；
    // 而置灰的同时**必须真的点不动** —— title 里写着「需先开启允许联网」却仍然能切换，
    // 是更坏的状态（用户开了深度联网却以为它没开）。
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: false, deepWeb: true })
    const { container } = render(<ChatInput scale="md" />)

    expect(btn(container)!.hasAttribute('disabled')).toBe(true)
    // 开着联网时偏好为真 —— 但因为联动闸门，按钮此刻显示 off（不会真发出去）
    expect(stateOf(container)).toBe('off')

    fireEvent.click(btn(container)!)
    expect(stateOf(container)).toBe('off')
    expect(loadChatPrefs().deepWeb).toBe(true)   // disabled 的按钮不该落盘任何东西
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 发送闸门：三个条件全真才带 `deep_web=1`
//
// ⚠️ 参数只在**偏离默认**时才发出去（默认不带，同 `web_search=0` 的纪律）。
// ═══════════════════════════════════════════════════════════

describe('deep_web 请求参数', () => {
  const send = () => {
    useChatStore.getState().sendMessage('问题')
    return fetchUrls[fetchUrls.length - 1]
  }

  it('默认（关）→ URL 不含 deep_web', () => {
    setDeepWebCapability(true)
    expect(send()).not.toContain('deep_web')
  })

  it('偏好开 + 联网开 + 能力支持 → 带 deep_web=1', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ deepWeb: true })
    expect(send()).toContain('deep_web=1')
  })

  it('⚠️ 能力不支持时**不发**（否则后端每轮回退、每轮弹一条提示）', () => {
    setDeepWebCapability(false)
    saveChatPrefs({ deepWeb: true })
    expect(send()).not.toContain('deep_web')
  })

  it('⚠️ 联网关着时**不发**（深度联网的前提就是联网）', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ deepWeb: true, webSearch: false })
    const url = send()
    expect(url).toContain('web_search=0')
    expect(url).not.toContain('deep_web')
  })

  it('三项偏好互不干扰', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ deepWeb: true, followUp: true, answerMode: 'free' })
    const url = send()
    expect(url).toContain('deep_web=1')
    expect(url).toContain('follow_up=1')
    expect(url).toContain('answer_mode=free')
  })
})
