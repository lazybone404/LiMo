/**
 * 消息气泡的归属（2026-11-17 用户要求）：**用户消息有气泡，助手消息没有。**
 *
 * 助手回答是最长的正文，外面套一层带底色的圆角框会把它读成「被关在对话框里」，
 * 可用宽度也少一圈；用户消息通常只有一两行，反而需要气泡来与回答区分开。
 *
 * ⚠️ 这条规矩没有行为对照物 —— 改回「两边都带气泡」时所有用例照绿，界面上只是
 * 多出一颗框，不报错也不会有人发现。故在这里钉住两侧的 className。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { MessageItem } from '../components/ai-chat/MessageItem'
import type { ChatMsg } from '../store/chatStore'

const msg = (role: 'user' | 'assistant', content: string): ChatMsg => ({
  id: `${role}-1`, role, content, date: '2026-11-17 10:00',
})

/** 正文所在的容器 —— 用户侧与助手侧都是消息列里的第一个 div 的直接子级 */
const bodyOf = (c: HTMLElement) => c.querySelector('.group > div:last-child > div')!

beforeEach(() => { localStorage.clear() })

describe('用户消息', () => {
  it('带气泡（底色 + 圆角 + 内边距）', () => {
    const { container } = render(<MessageItem msg={msg('user', '问题')} scale="md" />)
    const body = bodyOf(container)
    expect(body.className).toContain('bg-accent')
    expect(body.className).toContain('rounded-2xl')
    expect(body.className).toContain('px-3.5')
  })
})

describe('助手消息', () => {
  it('不带气泡（没有底色、没有圆角、没有内边距）', () => {
    const { container } = render(<MessageItem msg={msg('assistant', '回答')} scale="md" />)
    const body = bodyOf(container)
    // 对照实验：把 className 改回 `bg-surface rounded-2xl px-3.5 py-2` → 三条红
    expect(body.className).not.toContain('bg-surface')
    expect(body.className).not.toContain('rounded-2xl')
    expect(body.className).not.toContain('px-3.5')
  })

  it('正文仍然是 Markdown 渲染（去掉气泡不改变内容通路）', () => {
    const { container } = render(<MessageItem msg={msg('assistant', '**重点**')} scale="md" />)
    expect(container.querySelector('.markdown-chat strong')?.textContent).toBe('重点')
  })
})
