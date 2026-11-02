/**
 * 快捷键一览的渲染与关闭。
 *
 * ⚠️ 与 `shortcutHelp.test.ts` 分工不同：那个是**源码守卫**（App.tsx 认的键在不在表里），
 * 本文件看的是**表能不能出现在屏幕上** —— 表对了但面板没渲染，用户看到的仍是一片空白，
 * 而两边的报错都是零。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ShortcutHelpDialog } from '../components/ui/ShortcutHelpDialog'
import { SHORTCUTS, GROUP_LABELS, GROUP_ORDER, formatShortcut } from '../lib/shortcuts'

afterEach(cleanup)

describe('formatShortcut', () => {
  it('Ctrl + 字母 → 大写拼写', () => {
    expect(formatShortcut({ keys: ['g'], mods: ['ctrl'], label: '', group: 'global' })).toBe('Ctrl+G')
  })
  it('多个等价键只展示第一个（`=` / `+` 展示成 Ctrl+=）', () => {
    expect(formatShortcut({ keys: ['=', '+'], mods: ['ctrl'], label: '', group: 'edit' })).toBe('Ctrl+=')
  })
  it('无修饰键时就是键本身，Escape 展示成 Esc', () => {
    expect(formatShortcut({ keys: ['Escape'], mods: [], label: '', group: 'global' })).toBe('Esc')
  })
})

describe('ShortcutHelpDialog', () => {
  it('每个非空分组都渲染出标题，且每条 label 都在面板里', () => {
    render(<ShortcutHelpDialog onClose={() => {}} />)

    const usedGroups = GROUP_ORDER.filter(g => SHORTCUTS.some(s => s.group === g))
    for (const g of usedGroups) {
      expect(screen.getByText(GROUP_LABELS[g])).toBeTruthy()
    }
    // 逐条 label 必须都在 DOM 里 —— 漏渲染一条在界面上是「帮助里少说一句」，零报错
    for (const s of SHORTCUTS) {
      expect(screen.getByText(s.label)).toBeTruthy()
    }
  })

  it('Esc 关闭', () => {
    const onClose = vi.fn()
    render(<ShortcutHelpDialog onClose={onClose} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('再按一次 Ctrl+/ 也能关（用户最可能重复按的就是它）', () => {
    const onClose = vi.fn()
    render(<ShortcutHelpDialog onClose={onClose} />)
    fireEvent.keyDown(document, { key: '/', ctrlKey: true })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('点关闭按钮关闭；点面板内部不关、点遮罩关闭', () => {
    const onClose = vi.fn()
    render(<ShortcutHelpDialog onClose={onClose} />)

    fireEvent.click(screen.getByText(SHORTCUTS[0].label))
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.click(document.querySelector('[data-shortcut-help]')!)
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.click(document.querySelector('[data-shortcut-help-close]')!)
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
