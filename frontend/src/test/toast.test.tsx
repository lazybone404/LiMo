/**
 * Toast 的两种新能力 —— 回收站（删除撤销）依赖它们。
 *
 * 三个判据各自对应一个**看起来等效、实则不同**的实现：
 *  ① `info` 有自己的一支视觉。此前它落进最终的 `else`，渲染成红色报错 ——
 *     一条中性提示长得和「导入失败」一模一样，而调用方写的是 `'info'`。
 *  ② 动作按钮（「撤销」）真的渲染且真的调用回调；点完 toast 自行消失
 *     （否则用户点完撤销，那条提示还挂在那里等他再点一次）。
 *  ③ **只有文案没有回调时不渲染按钮** —— 一个点不动的按钮比没有按钮更糟。
 *
 * ⚠️ 判据落在 **DOM 的 class** 上而不只是「元素存在」：`info` 与 `error`
 * 都是「一个 div 里一行字 + 图标」，只断言存在的话，红色报错也全绿。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

import { Toast } from '../components/ui/Toast'
import { useStore } from '../store'

beforeEach(() => {
  useStore.setState({ toasts: [] })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

/** toast 卡片的根 div（`closest` 从文字往上找第一层带 border 的容器） */
function cardOf(text: string): HTMLElement {
  const el = screen.getByText(text).closest('div') as HTMLElement
  expect(el).not.toBeNull()
  return el
}

describe('Toast 的类型视觉', () => {
  it('info 用 accent 而不是红色报错', () => {
    render(<Toast />)
    act(() => { useStore.getState().addToast('检索没生效，等后台建完就好', 'info') })

    const card = cardOf('检索没生效，等后台建完就好')
    expect(card.className).toContain('border-accent')
    expect(card.className).not.toContain('border-error')
  })

  it('error 仍是红色（对照：上面那条不是把所有类型都改成 accent）', () => {
    render(<Toast />)
    act(() => { useStore.getState().addToast('导入失败', 'error') })

    const card = cardOf('导入失败')
    expect(card.className).toContain('border-error')
    expect(card.className).not.toContain('border-accent')
  })
})

describe('Toast 的动作按钮', () => {
  it('文案 + 回调都在时渲染，点击调用回调并让 toast 消失', () => {
    const onAction = vi.fn()
    render(<Toast />)
    act(() => {
      useStore.getState().addToast('已删除 3 个', 'success',
        { actionLabel: '撤销', onAction, duration: 8000 })
    })

    const btn = screen.getByRole('button', { name: '撤销' })
    fireEvent.click(btn)

    expect(onAction).toHaveBeenCalledTimes(1)
    // 点完自己消失 —— 不靠调用方再 dismiss 一次
    expect(screen.queryByText('已删除 3 个')).toBeNull()
  })

  it('只有文案、没有回调时不渲染按钮（点不动的按钮比没有更糟）', () => {
    render(<Toast />)
    act(() => {
      useStore.getState().addToast('已删除 3 个', 'success', { actionLabel: '撤销' })
    })

    expect(screen.queryByRole('button', { name: '撤销' })).toBeNull()
    // 提示本身还在（只是没有动作）
    expect(screen.getByText('已删除 3 个')).toBeTruthy()
  })

  it('既有的三参数调用一行都不用改，且不带按钮', () => {
    render(<Toast />)
    act(() => { useStore.getState().addToast('普通提示', 'success') })

    expect(screen.getByText('普通提示')).toBeTruthy()
    expect(document.querySelector('[data-toast-action]')).toBeNull()
  })
})

describe('Toast 的存活时长', () => {
  it('自定义 duration 覆盖按 type 取的默认值', () => {
    vi.useFakeTimers()
    render(<Toast />)
    act(() => { useStore.getState().addToast('八秒', 'success', { duration: 8000 }) })

    act(() => { vi.advanceTimersByTime(7999) })
    expect(screen.queryByText('八秒')).not.toBeNull()

    act(() => { vi.advanceTimersByTime(1) })
    expect(screen.queryByText('八秒')).toBeNull()
  })
})
