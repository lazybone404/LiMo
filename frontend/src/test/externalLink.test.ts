/**
 * 外链统一走系统浏览器（`lib/external-link.ts`）。
 *
 * 用户反馈的原始症状：**点 AI 回复里的来源链接，直接在应用内打开、然后报错** ——
 * 桌面壳是 WebView 不是浏览器，`target="_blank"` 在那里没有「新标签页」可开，
 * 结果要么把 LiMo 界面整个顶掉，要么弹出一个小窗再报错。
 *
 * 判据只有一条：**http(s) 链接必须交给系统浏览器，其余（锚点 / 相对路径）原样放行**。
 * 拦多了的失效是「目录跳转被当成外链丢出去」——所以第二条断言与第一条同等重要。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { installExternalLinkHandler } from '../lib/external-link'

const openMock = vi.fn(async (_url: string) => undefined)

vi.mock('@tauri-apps/plugin-shell', () => ({ open: (url: string) => openMock(url) }))

let uninstall: (() => void) | null = null

/** 装一个带 href 的链接并点它 */
function clickLink(href: string, text = '链接') {
  const a = document.createElement('a')
  a.href = href
  a.textContent = text
  document.body.appendChild(a)
  const notPrevented = fireEvent.click(a)
  a.remove()
  return notPrevented          // true = 默认行为没被拦（fireEvent 返回 false 表示已被 preventDefault）
}

beforeEach(() => {
  openMock.mockClear()
  // 判据走 `api/core.ts::isTauriEnv`，这里只造那个标记
  ;(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {}
  delete (window as unknown as { open?: unknown }).open
  ;(window as unknown as { open: unknown }).open = vi.fn()
  uninstall = installExternalLinkHandler()
})

afterEach(() => {
  uninstall?.()
  uninstall = null
  delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
  vi.restoreAllMocks()
})

describe('外部 http(s) 链接', () => {
  it('交给系统浏览器，并且阻止 WebView 内导航', async () => {
    const notPrevented = clickLink('https://example.com/a?b=1')

    expect(notPrevented).toBe(false)                 // 默认导航被拦掉了
    await vi.waitFor(() => expect(openMock).toHaveBeenCalledWith('https://example.com/a?b=1'))
  })

  it('http 与 https 都接管', async () => {
    clickLink('http://example.com/x')
    await vi.waitFor(() => expect(openMock).toHaveBeenCalledWith('http://example.com/x'))
  })
})

describe('不该被接管的链接', () => {
  it('站内锚点原样放行（目录跳转不能被丢给浏览器）', () => {
    const notPrevented = clickLink('#section-3')
    expect(notPrevented).toBe(true)
    expect(openMock).not.toHaveBeenCalled()
  })

  it('相对路径原样放行', () => {
    const notPrevented = clickLink('/docs/readme')
    expect(notPrevented).toBe(true)
    expect(openMock).not.toHaveBeenCalled()
  })

  it('点击的不是链接（普通文字）什么也不发生', () => {
    const span = document.createElement('span')
    span.textContent = '普通文字'
    document.body.appendChild(span)
    fireEvent.click(span)
    span.remove()
    expect(openMock).not.toHaveBeenCalled()
  })
})

describe('安装与卸载', () => {
  it('卸载之后不再接管（严格模式下重复挂载必须可撤销）', async () => {
    uninstall?.()
    uninstall = null

    clickLink('https://example.com/z')
    await Promise.resolve()
    expect(openMock).not.toHaveBeenCalled()
  })
})

describe('浏览器环境（`npm run dev` 直接开 5173）', () => {
  it('没有 Tauri 标记时走 window.open，行为与桌面壳一致', async () => {
    uninstall?.()
    delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
    uninstall = installExternalLinkHandler()

    clickLink('https://example.com/browser')
    await vi.waitFor(() =>
      expect((window as unknown as { open: ReturnType<typeof vi.fn> }).open)
        .toHaveBeenCalledWith('https://example.com/browser', '_blank', 'noopener,noreferrer'))
    expect(openMock).not.toHaveBeenCalled()
  })
})
