/**
 * 输入区的「回答设置」浮层（2026-11-17）。
 *
 * 此前是五颗并排图标：@ 引用 / 追问建议 / 联网 / 深度联网 / 回答依据。用户反馈三条：
 * **一排挤太满**（浮窗变窄时输入框被压成一条缝）、**某个图标认不出来**（雷达那类隐喻
 * 说不出「这是深度联网」）、**交互不清楚**（开没开只靠图标底色深浅，看不出来）。
 *
 * 收进一张浮层后，每条断言对应三条反馈之一：
 *
 *  · 收起时只有两颗按钮 → 不再挤（第 1 组）
 *  · 每项都有中文名字与一句话说明 → 不靠图标猜（第 2 组）
 *  · 开/关由复选框承担，且关掉时写明原因 → 状态看得见（第 3 组）
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { useChatStore } from '../store/chatStore'
import { useStore } from '../store'
import { loadChatPrefs, saveChatPrefs } from '../lib/chat-prefs'
import type { Capabilities } from '../api/core'
import { ChatInput } from '../components/ai-chat/ChatInput'

const setDeepWebCapability = (available: boolean) =>
  useStore.setState({ capabilities: { chatDeepWebAvailable: available } as unknown as Capabilities })

beforeEach(() => {
  localStorage.clear()
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
    contextWindow: null, budget: null, lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 }, compressedHint: null,
    clearContextIndex: null, priceIn: null, priceOut: null,
  })
  useStore.setState({ currentDocId: null, docs: [], capabilities: null })
})

afterEach(() => { vi.restoreAllMocks() })

const trigger = (c: HTMLElement) => c.querySelector('[data-settings-toggle]')!
const panel = (c: HTMLElement) => c.querySelector('[data-settings-panel]')
const openMenu = (c: HTMLElement) => fireEvent.click(trigger(c))

// ═══════════════════════════════════════════════════════════
// 1. 收起态：一行只有两颗按钮
// ═══════════════════════════════════════════════════════════

describe('收起时不占宽度', () => {
  it('默认只渲染 @ 与「回答设置」两颗，四组开关都不在 DOM 里', () => {
    setDeepWebCapability(true)
    const { container } = render(<ChatInput scale="md" />)

    expect(trigger(container)).toBeTruthy()
    expect(panel(container)).toBeNull()
    // 对照实验：把这几个开关改回并排渲染 → 这三条红，而症状正是「输入框被挤窄」
    expect(container.querySelector('[data-websearch-toggle]')).toBeNull()
    expect(container.querySelector('[data-followup-toggle]')).toBeNull()
    expect(container.querySelector('[data-deepweb-toggle]')).toBeNull()
  })

  it('点开后再点一次收起（不是只能点外面）', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)
    expect(panel(container)).not.toBeNull()
    openMenu(container)
    expect(panel(container)).toBeNull()
  })

  it('点面板内部不收起（点开关本身不该把面板关掉）', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)
    fireEvent.mouseDown(container.querySelector('[data-websearch-toggle]')!)
    expect(panel(container)).not.toBeNull()
  })

  it('Esc 收起', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(panel(container)).toBeNull()
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 每一项都有中文名字 —— 用户不必从图标猜它是干什么的
// ═══════════════════════════════════════════════════════════

describe('每一项都写名字', () => {
  it('四组的名字与说明都在面板里', () => {
    setDeepWebCapability(true)
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)

    const text = panel(container)!.textContent!
    for (const s of ['联网', '深度联网', '追问建议', '回答依据']) expect(text).toContain(s)
    // 深度联网必须写明「本轮不使用知识库工具」—— 否则用户会以为库里的文档照样能搜
    expect(text).toContain('本轮不使用知识库工具')
  })

  it('模型不支持深度联网时，那一行也不出现（与按钮时代的判据一致）', () => {
    setDeepWebCapability(false)
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)

    expect(panel(container)!.textContent).not.toContain('深度联网')
    expect(container.querySelector('[data-deepweb-toggle]')).toBeNull()
  })
})

// ═══════════════════════════════════════════════════════════
// 3. 开/关看得见，关掉时说明原因
// ═══════════════════════════════════════════════════════════

describe('开关状态与联动', () => {
  it('点复选框即落盘，反射到 data-* 与偏好', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)

    const web = () => container.querySelector('[data-websearch-toggle]')!
    expect(web().getAttribute('data-websearch-toggle')).toBe('on')   // 默认开

    fireEvent.click(web())
    expect(loadChatPrefs().webSearch).toBe(false)
    expect(web().getAttribute('data-websearch-toggle')).toBe('off')
  })

  it('联网关着时深度联网那一行置灰，并**写出原因**（不只是变灰）', () => {
    setDeepWebCapability(true)
    saveChatPrefs({ webSearch: false })
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)

    expect(container.querySelector('[data-deepweb-toggle]')!.hasAttribute('disabled')).toBe(true)
    expect(panel(container)!.textContent).toContain('需先开启')
  })

  it('⚠️ 四项全默认时图标不点亮，任一偏离即点亮', () => {
    // ⚠️ 外部的 `saveChatPrefs` 要包 `act`：它是另一个入口（设置页）的写法，
    // 不包的话重渲染排在断言之后，读到的是旧 class（看起来像「点亮失效」）。
    const { container } = render(<ChatInput scale="md" />)
    expect(trigger(container).className).not.toContain('bg-accent/10')

    // 关掉联网（默认开）也算偏离
    act(() => { saveChatPrefs({ webSearch: false }) })
    expect(trigger(container).className).toContain('bg-accent/10')

    act(() => { saveChatPrefs({ webSearch: true }) })
    expect(trigger(container).className).not.toContain('bg-accent/10')

    act(() => { saveChatPrefs({ answerMode: 'free' }) })
    expect(trigger(container).className).toContain('bg-accent/10')
  })
})
