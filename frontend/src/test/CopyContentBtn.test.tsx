/**
 * CopyContentBtn 组件测试 — 验证 CONTENT_GETTERS 驱动的三种内容源行为。
 * 这是本项目第一个组件测试（@testing-library/react + jsdom 已配置）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { CopyContentBtn } from '../components/preview/CopyContentBtn'
import type { ContentGetterCtx } from '../lib/contentSources'

const baseCtx: ContentGetterCtx = {
  currentDocId: 'doc1',
  activeView: 'polished',
  currentContent: null,
  currentPolishedContent: 'polished text',
  currentRawText: null,
  currentFactCheckContent: null,
  imageResults: {},
}

describe('CopyContentBtn', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
  })

  it('polished tab 显示复制按钮并复制对应内容', () => {
    render(<CopyContentBtn ctx={{ ...baseCtx }} />)
    const btn = screen.getByTitle('复制内容')
    expect(btn).toBeInTheDocument()
    fireEvent.click(btn)
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('polished text')
  })

  it('none 源 tab（original）不渲染按钮', () => {
    const { container } = render(<CopyContentBtn ctx={{ ...baseCtx, activeView: 'original' }} />)
    expect(container.querySelector('button')).toBeNull()
  })

  it('imageResult tab 有结果时复制 OCR 文本', () => {
    render(<CopyContentBtn ctx={{
      ...baseCtx,
      activeView: 'extract',
      imageResults: { doc1: { extract: { text: 'OCR 内容', engine: 'tesseract', id: 'r1' } } },
    }} />)
    const btn = screen.getByTitle('复制内容')
    fireEvent.click(btn)
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('OCR 内容')
  })
})
