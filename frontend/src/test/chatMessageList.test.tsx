/**
 * 对话消息区的两条新交互 —— **Ctrl+滚轮改字号** 与 **「回到最新」浮层按钮**，
 * 外加代码块的一键复制（`ChatMarkerContent` 的 `ChatPre`）。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 滚轮 handler 用 `[]` 依赖的 effect + 闭包读 state（而不是 ref）→
 *      只能放大一档，第二次滚动纹丝不动，且**零报错** → 红（本文件第一条断言就是它）
 *   B. 滚动按钮的显隐用**另一套**判据（比如 `scrollTop === 0`）→ 与「自动跟随」
 *      的 `< 40px` 判据分家，两者在阈值附近互相矛盾 → 红
 *   C. 复制按钮挂 `code` 而不是 `pre`（`react-markdown` v10 已无 `inline` prop）→
 *      每个行内代码也冒一个按钮 → 红
 *   D. `textContent` 取自包裹层而不是 `<pre>` → 把按钮自己的文本也复制进去 → 红
 *   E. 流式中也显示按钮 → 复制到半截代码 → 红
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'

// ⚠️ `vi.hoisted`：`vi.mock` 的工厂会被提升到 import 之前，直接引用下面的
// 变量会得到「Cannot access before initialization」。
const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }))
vi.mock('../store', () => ({ useStore: { getState: () => ({ addToast }) } }))

import { MessageList } from '../components/ai-chat/MessageList'
import { ChatMarkerContent } from '../components/ai-chat/ChatMarkerContent'
import { useChatStore } from '../store/chatStore'

afterEach(cleanup)

const VAR = '--chat-font-scale'
const scaleNow = () => document.documentElement.style.getPropertyValue(VAR)
const bottomBtn = (c: HTMLElement) => c.querySelector('button[aria-label="回到最新"]')

beforeEach(() => {
  localStorage.clear()
  document.documentElement.style.removeProperty(VAR)
  addToast.mockClear()
  useChatStore.setState({ messages: [] })
})

/** 渲染 MessageList 并返回它的滚动容器 */
function renderList() {
  const { container } = render(<MessageList scale="md" />)
  const el = container.querySelector('.overflow-y-auto') as HTMLElement
  expect(el).not.toBeNull()
  return { container, el }
}

// ── Ctrl+滚轮改对话字号 ─────────────────────────────────────

describe('MessageList — Ctrl+滚轮改字号', () => {
  it('Ctrl+上滚放大一档，Ctrl+下滚缩小一档', () => {
    const { el } = renderList()
    fireEvent.wheel(el, { ctrlKey: true, deltaY: -1 })
    expect(scaleNow()).toBe('1.1')
    fireEvent.wheel(el, { ctrlKey: true, deltaY: 1 })
    expect(scaleNow()).toBe('1')
  })

  it('⚠️ 连滚两次真的再进一档（对照 A：闭包读到旧值就会卡在 1.1）', () => {
    const { el } = renderList()
    fireEvent.wheel(el, { ctrlKey: true, deltaY: -1 })
    fireEvent.wheel(el, { ctrlKey: true, deltaY: -1 })
    expect(scaleNow()).toBe('1.2')
  })

  it('不带 Ctrl 的滚轮**只管滚动**，不动字号', () => {
    const { el } = renderList()
    fireEvent.wheel(el, { deltaY: -1 })
    expect(scaleNow()).toBe('')
  })

  it('每一档发一次 toast，到顶 / 到底静默（不刷屏）', () => {
    const { el } = renderList()
    // 从 1.0 连推到上限 1.6 = 6 档 → 恰好 6 次；之后再滚 10 次不该再多
    for (let i = 0; i < 20; i++) fireEvent.wheel(el, { ctrlKey: true, deltaY: -1 })
    expect(scaleNow()).toBe('1.6')
    expect(addToast).toHaveBeenCalledTimes(6)
    expect(addToast).toHaveBeenLastCalledWith('对话字号 160%')
  })

  it('档位写回 localStorage（刷新后仍在 —— 启动时由 store apply）', () => {
    const { el } = renderList()
    fireEvent.wheel(el, { ctrlKey: true, deltaY: -1 })
    expect(localStorage.getItem('mbw_chatFont')).toBe(JSON.stringify({ fontScale: 1.1 }))
    // ⚠️ 与阅读字号各存各的 key（用户拍板「对话独立一份」）
    expect(localStorage.getItem('mbw_reading')).toBe(null)
  })
})

// ── 「回到最新」按钮 ────────────────────────────────────────

describe('MessageList — 回到最新', () => {
  /** jsdom 里 scrollHeight/clientHeight 恒为 0、scrollTo 不存在 —— 都要造出来 */
  function armScroller(el: HTMLElement, { scrollHeight = 1000, clientHeight = 300 } = {}) {
    const scrollTo = vi.fn()
    Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
    Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
    Object.defineProperty(el, 'scrollTo', { value: scrollTo, configurable: true })
    return scrollTo
  }

  it('初始在底部 → 不显示按钮', () => {
    const { container } = renderList()
    expect(bottomBtn(container)).toBeNull()
  })

  it('上滚后出现，点击滚到底并立即隐藏', () => {
    const { container, el } = renderList()
    const scrollTo = armScroller(el)

    el.scrollTop = 0            // 滚到顶：1000 - 0 - 300 = 700 > 40
    fireEvent.scroll(el)
    const btn = bottomBtn(container)
    expect(btn).not.toBeNull()

    fireEvent.click(btn!)
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: 'smooth' })
    expect(bottomBtn(container)).toBeNull()
  })

  it('⚠️ 阈值与自动跟随同源：差 39px 视为在底部，差 41px 不算（对照 B）', () => {
    const { container, el } = renderList()
    armScroller(el)

    el.scrollTop = 700 - 39       // 距底 39px
    fireEvent.scroll(el)
    expect(bottomBtn(container)).toBeNull()

    el.scrollTop = 700 - 41       // 距底 41px
    fireEvent.scroll(el)
    expect(bottomBtn(container)).not.toBeNull()
  })

  it('滚回底部后按钮自动消失（不需要点它）', () => {
    const { container, el } = renderList()
    armScroller(el)
    el.scrollTop = 0
    fireEvent.scroll(el)
    expect(bottomBtn(container)).not.toBeNull()

    el.scrollTop = 1000 - 300     // 见底
    fireEvent.scroll(el)
    expect(bottomBtn(container)).toBeNull()
  })
})

// ── 代码块一键复制 ──────────────────────────────────────────

describe('ChatMarkerContent — 代码块一键复制', () => {
  const renderMd = (md: string, streaming = false) =>
    render(<ChatMarkerContent content={md} streaming={streaming} statusText="" />)

  const copyBtn = (c: HTMLElement) => c.querySelector('button[aria-label="复制代码"]')

  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
  })

  it('块级代码有按钮，复制到的**只有代码本身**（对照 D）', () => {
    const { container } = renderMd('```\nconst a = 1\n```')
    const btn = copyBtn(container)
    expect(btn).not.toBeNull()

    fireEvent.click(btn!)
    const [text] = (navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(text.trim()).toBe('const a = 1')
    // 按钮自己的文案不能混进去（`textContent` 若取包裹层就会带上「复制代码」）
    expect(text).not.toContain('复制')
  })

  it('⚠️ 行内代码**不**配按钮（对照 C：挂 pre 而非 code）', () => {
    const { container } = renderMd('这是一段 `inline` 代码。')
    expect(copyBtn(container)).toBeNull()
  })

  it('流式中不显示按钮（对照 E：内容是半截的）', () => {
    const { container } = renderMd('```\nconst a = ', true)
    expect(copyBtn(container)).toBeNull()
  })

  it('流式结束（同一棵树上 switching）→ 按钮出现', () => {
    const { container, rerender } = renderMd('```\nconst a = 1\n```', true)
    expect(copyBtn(container)).toBeNull()
    rerender(<ChatMarkerContent content={'```\nconst a = 1\n```'} streaming={false} statusText="" />)
    expect(copyBtn(container)).not.toBeNull()
  })

  it('复制失败走 toast，不静默', async () => {
    ;(navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('denied'))
    const { container } = renderMd('```\nx = 1\n```')
    fireEvent.click(copyBtn(container)!)
    await vi.waitFor(() => expect(addToast).toHaveBeenCalledWith('复制失败，请手动复制', 'error'))
  })
})
