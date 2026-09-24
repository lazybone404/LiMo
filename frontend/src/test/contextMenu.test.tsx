/**
 * ContextMenu 分隔项渲染。
 *
 * 独立成文件的原因：菜单 `createPortal` 到 `document.body`，`render()` 返回的
 * `container` 里查不到任何东西——必须查 `document.body`。
 *
 * 缺陷形态是「看着没错的静默错」：`divider: true` 的项只有一个空 label，
 * 漏掉短路分支后会渲染成一个**空文案、可点、点了还关菜单**的按钮。
 * 菜单本身照常显示、照常能用，只有细看才会发现多出一行空白。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { ContextMenu } from '../components/ui/ContextMenu'

afterEach(() => cleanup())

const items = [
  { label: '编辑标题', onClick: vi.fn() },
  { label: '', divider: true },
  { label: '删除', danger: true, onClick: vi.fn() },
]

describe('ContextMenu — 分隔项', () => {
  it('渲染成 separator，而不是一个空文案的按钮', () => {
    render(<ContextMenu x={100} y={100} items={items} onClose={vi.fn()} />)

    // ⚠️ 对照（已验证有区分力）：去掉 `if (item.divider)` 短路后 separator 数为 0，本行红。
    expect(document.body.querySelectorAll('[role="separator"]').length).toBe(1)
    // 分隔项**不该**是按钮：落进按钮分支会多出一个可点元素
    expect(document.body.querySelectorAll('button').length).toBe(2)
  })

  it('点分隔项不执行动作、也不关菜单', () => {
    const onClose = vi.fn()
    render(<ContextMenu x={100} y={100} items={items} onClose={onClose} />)

    fireEvent.click(document.body.querySelector('[role="separator"]') as HTMLElement)

    // ⚠️ 对照（已验证有区分力）：去掉短路时分隔项落进按钮分支、继承其 `onClick`，
    // 点击即触发 onClose，本断言变红。这是「不可点」这一半的性质——
    // 上一条只保证它**不是按钮**，这条保证它**真的不响应点击**。
    expect(onClose).not.toHaveBeenCalled()
  })

  it('点普通项执行动作并关菜单（保住正常路径）', () => {
    const onClose = vi.fn()
    const onClick = vi.fn()
    render(<ContextMenu x={100} y={100} items={[{ label: '编辑标题', onClick }]} onClose={onClose} />)

    fireEvent.click(document.body.querySelector('button') as HTMLElement)
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
