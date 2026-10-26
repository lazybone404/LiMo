/**
 * 文档库顶部「检索索引不可用」横幅。
 *
 * 它补的是**推送**那一侧：设置页的卡是拉取（用户主动走进去才看得见），
 * 而索引坏掉时用户先遇到的是「搜不到」——那时候他已经开始怀疑自己的库了。
 *
 * 三条判据在这里钉住：
 * ① `available === false` 才渲染（`null` / 取不到 → 静默，见下方「问不到 ≠ 坏了」）；
 * ② 刷新失败**保持上一次快照**，不能把「请求挂了」渲染成「已经好了」；
 * ③ 30s TTL 去重 —— 挂载一次就一个请求，不轮询。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup, waitFor, screen, act } from '@testing-library/react'
import type { SearchStatus } from '../api/search'

const statusMock = vi.fn()
const reindexMock = vi.fn()
vi.mock('../api/search', () => ({
  getSearchStatus: () => statusMock(),
  reindexSearch: () => reindexMock(),
}))

import { SearchIndexBanner } from '../components/doc-library/SearchIndexBanner'
import {
  refreshSearchHealth, setSearchHealth, __resetSearchHealthForTest,
} from '../lib/search-health'

const BAD: SearchStatus = {
  available: false, docCount: 0, sizeBytes: 0,
  lastError: '索引库打不开：database disk image is malformed', rebuiltAt: null,
}
const GOOD: SearchStatus = {
  available: true, docCount: 3, sizeBytes: 12_288, lastError: null,
  rebuiltAt: '2026-10-26T09:31:00',
}

beforeEach(() => {
  statusMock.mockReset()
  reindexMock.mockReset()
  // 默认给一个「正常」的响应：组件挂载时一定会懒探测一次，未设返回值的用例
  // 会拿到 `undefined` 而不是 Promise（症状是 `Cannot read properties of undefined`，
  // 与被测逻辑毫无关系）。各用例按需覆盖。
  statusMock.mockResolvedValue(GOOD)
  __resetSearchHealthForTest()
})
afterEach(cleanup)

describe('索引健康横幅', () => {
  it('不可用 → 横幅出现，并带上原因', async () => {
    setSearchHealth(BAD)
    render(<SearchIndexBanner />)
    expect(screen.getByText('全文检索不可用')).toBeInTheDocument()
    expect(screen.getByText(/database disk image is malformed/)).toBeInTheDocument()
  })

  it('可用 → 一个字都不渲染（零回归：索引正常时界面与改造前逐字相同）', () => {
    setSearchHealth(GOOD)
    const { container } = render(<SearchIndexBanner />)
    expect(container).toBeEmptyDOMElement()
  })

  it('冷启动（状态还没取到）→ 不渲染 —— 不把「不知道」显示成「坏了」', () => {
    // 用一个永不 resolve 的请求模拟「还在路上」：后端没起的那几十秒正是这个形态。
    statusMock.mockImplementation(() => new Promise(() => {}))
    const { container } = render(<SearchIndexBanner />)
    expect(container).toBeEmptyDOMElement()
  })

  it('挂载时懒探测一次，取到不可用后横幅自己浮出来', async () => {
    statusMock.mockResolvedValue(BAD)
    render(<SearchIndexBanner />)
    await waitFor(() => expect(statusMock).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('全文检索不可用')).toBeInTheDocument())
  })

  it('点「重建索引」→ 调后端；成功后横幅立刻消失（不需要再刷一次状态）', async () => {
    setSearchHealth(BAD)
    reindexMock.mockResolvedValue({ ...BAD, available: true, lastError: null })
    render(<SearchIndexBanner />)
    fireEvent.click(screen.getByRole('button', { name: /重建索引/ }))
    await waitFor(() => expect(screen.queryByText('全文检索不可用')).toBeNull())
    expect(reindexMock).toHaveBeenCalledTimes(1)
  })

  it('重建失败 → 横幅留着，并把失败原因说出来（不是静默再试）', async () => {
    setSearchHealth(BAD)
    reindexMock.mockRejectedValue(new Error('磁盘已满'))
    render(<SearchIndexBanner />)
    fireEvent.click(screen.getByRole('button', { name: /重建索引/ }))
    await waitFor(() => expect(screen.getByText(/重建失败：磁盘已满/)).toBeInTheDocument())
    expect(screen.getByText('全文检索不可用')).toBeInTheDocument()
  })

  it('关闭 → 本次不再提示', () => {
    setSearchHealth(BAD)
    render(<SearchIndexBanner />)
    fireEvent.click(screen.getByTitle('本次不再提示'))
    expect(screen.queryByText('全文检索不可用')).toBeNull()
  })
})

describe('状态缓存的判据', () => {
  it('30s 内不重复请求；force 才真的打', async () => {
    setSearchHealth(GOOD)                 // 刚取过 ⇒ TTL 生效
    await refreshSearchHealth()
    expect(statusMock).not.toHaveBeenCalled()

    statusMock.mockResolvedValue(GOOD)
    await act(async () => { await refreshSearchHealth(true) })
    expect(statusMock).toHaveBeenCalledTimes(1)
  })

  it('刷新失败时保持上一次快照 —— 「问不到」不等于「已经好了」', async () => {
    setSearchHealth(BAD)
    statusMock.mockRejectedValue(new Error('network down'))
    render(<SearchIndexBanner />)
    await act(async () => { await refreshSearchHealth(true) })
    // 反向对照：若把 catch 写成「失败即清空 / 失败即视为可用」，这条会红
    expect(screen.getByText('全文检索不可用')).toBeInTheDocument()
  })
})
