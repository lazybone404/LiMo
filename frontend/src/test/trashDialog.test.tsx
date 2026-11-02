/**
 * 回收站对话框 —— 列表 / 恢复 / 彻底删除（二次确认）/ 清空。
 *
 * 判据集中在两件「看起来等价、实则不同」的事上：
 *
 * ① **彻底删除必须先确认**：它是回收站里**唯一不可逆**的动作，一次误点就是文档真没了。
 *    所以断言的是「点了按钮之后 `purgeTrash` **还没被调用**」，而不是「弹出了确认框」——
 *    后者在「确认框渲染了但真正删的是另一个按钮」时也成立。
 *
 * ② **恢复成功必须回调 `onRestored`**：文档库的本地视图在删除时已经把它摘掉了，
 *    不重拉就是「恢复成功、列表里没有」，而两个请求都是 200。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import type { TrashItem } from '../api/trash'

const listMock = vi.fn()
const restoreMock = vi.fn()
const purgeMock = vi.fn()
const emptyMock = vi.fn()

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    listTrash: () => listMock(),
    restoreTrash: (ids: string[]) => restoreMock(ids),
    purgeTrash: (id: string) => purgeMock(id),
    emptyTrash: () => emptyMock(),
  }
})

const { TrashDialog } = await import('../components/doc-library/TrashDialog')

function makeItem(over: Partial<TrashItem> = {}): TrashItem {
  return {
    id: 'tx1', title: '被删的文档', sourceType: 'pdf',
    deletedAt: '2026-11-01T10:00:00', expiresAt: '2026-11-08T10:00:00', daysLeft: 5,
    ...over,
  }
}

beforeEach(() => {
  cleanup()
  listMock.mockReset().mockResolvedValue({ items: [makeItem()], retentionDays: 7 })
  restoreMock.mockReset().mockResolvedValue({ status: 'ok', restored: ['tx1'], failed: [] })
  purgeMock.mockReset().mockResolvedValue({ status: 'purged', id: 'tx1' })
  emptyMock.mockReset().mockResolvedValue({ status: 'purged', count: 1 })
})

describe('回收站对话框', () => {
  it('列出条目：标题 / 类型 / 剩余天数', async () => {
    render(<TrashDialog onClose={() => {}} onRestored={() => {}} />)
    await screen.findByText('被删的文档')
    expect(screen.getByText(/PDF/)).toBeTruthy()
    expect(screen.getByText(/5 天后清理/)).toBeTruthy()
  })

  it('空回收站给出空态，且「清空」按钮不可点', async () => {
    listMock.mockResolvedValue({ items: [], retentionDays: 7 })
    render(<TrashDialog onClose={() => {}} onRestored={() => {}} />)
    await screen.findByText('回收站是空的')
    const btn = document.querySelector('[data-trash-empty]') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('恢复调 restoreTrash 并回调 onRestored', async () => {
    const onRestored = vi.fn()
    render(<TrashDialog onClose={() => {}} onRestored={onRestored} />)
    await screen.findByText('被删的文档')

    fireEvent.click(document.querySelector('[data-trash-restore]') as HTMLElement)

    await waitFor(() => expect(restoreMock).toHaveBeenCalledWith(['tx1']))
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1))
  })

  it('彻底删除要先确认 —— 点按钮时不发请求', async () => {
    render(<TrashDialog onClose={() => {}} onRestored={() => {}} />)
    await screen.findByText('被删的文档')

    fireEvent.click(document.querySelector('[data-trash-purge]') as HTMLElement)
    // 确认框在场，但**还没删**
    expect(document.querySelector('[data-trash-confirm]')).toBeTruthy()
    expect(purgeMock).not.toHaveBeenCalled()

    fireEvent.click(document.querySelector('[data-trash-confirm-purge]') as HTMLElement)
    await waitFor(() => expect(purgeMock).toHaveBeenCalledWith('tx1'))
  })

  it('清空回收站同样要先确认', async () => {
    render(<TrashDialog onClose={() => {}} onRestored={() => {}} />)
    await screen.findByText('被删的文档')

    fireEvent.click(document.querySelector('[data-trash-empty]') as HTMLElement)
    expect(emptyMock).not.toHaveBeenCalled()

    fireEvent.click(document.querySelector('[data-trash-confirm-empty]') as HTMLElement)
    await waitFor(() => expect(emptyMock).toHaveBeenCalledTimes(1))
  })

  it('恢复失败时显示后端给的原因，不回调 onRestored', async () => {
    const onRestored = vi.fn()
    restoreMock.mockResolvedValue({
      status: 'ok', restored: [], failed: [{ id: 'tx1', error: '回收站里没有这篇' }],
    })
    render(<TrashDialog onClose={() => {}} onRestored={onRestored} />)
    await screen.findByText('被删的文档')

    fireEvent.click(document.querySelector('[data-trash-restore]') as HTMLElement)
    await screen.findByText('回收站里没有这篇')
    expect(onRestored).not.toHaveBeenCalled()
  })
})
