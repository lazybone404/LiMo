/**
 * `useStartTranscription` —— 「转录完之后刷新什么」的唯一实现（2026-11-07）。
 *
 * 这件事有两个入口：生成面板的「⟳ 重新转录」和预览区的「待转录」卡片。两份实现
 * 迟早会分叉，而分叉的形态**完全没有报错**：后端已写回真正文、预览区也显示了，
 * 但 `docs` 里那条还挂着 `transcribeState: 'pending'` ⇒ **列表徽章继续显示「待转录」**，
 * 直到用户切去文档库重新拉一次列表。
 *
 * 所以这里钉的就是那一行：转录成功之后 `docs` 里该条必须变成 `'done'`。
 *
 * 对照实验：把 hook 里那行 `setDocs(...)` 删掉 → 第 1 组红；其余用例全绿
 * （它们测的是「转录本身跑通了」，与徽章无关 —— 这正是缺陷难看出来的原因）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

import type { DocEntry } from '../api/core'

const retranscribeMock = vi.fn()
const getTranscriptMock = vi.fn()

// 只替换两个函数，其余导出原样保留（`useStore` 也 import 这个 barrel，
// 整体 mock 掉会让与它们无关的模块在导入期就拿到 undefined）。
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    retranscribe: (...a: unknown[]) => retranscribeMock(...a),
    getTranscript: (...a: unknown[]) => getTranscriptMock(...a),
  }
})

// 必须在 mock 之后导入（模块图里 hook → api/client 已被替换）
const { useStartTranscription } = await import('../hooks/useStartTranscription')
const { useStore } = await import('../store')

const DOC_ID = 'doc-1'

function seed() {
  const doc = {
    id: DOC_ID, title: '待转录的一篇', sourceType: 'video',
    sourceFileName: '', sourceUrl: '', coverUrl: '', engine: '', language: '',
    contentHash: '', tags: [], createdAt: '', folderId: '',
    transcribeState: 'pending',
  } as DocEntry
  useStore.setState({ currentDocId: DOC_ID, currentRawText: '', docs: [doc] })
}

beforeEach(() => {
  vi.clearAllMocks()
  seed()
})

describe('useStartTranscription', () => {
  it('成功后把 docs 里该条改回 done，并回填正文', async () => {
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: '真正的正文' })

    const { result } = renderHook(() => useStartTranscription())
    await act(async () => {
      await result.current.start(DOC_ID)
    })

    const doc = useStore.getState().docs.find(d => d.id === DOC_ID)
    expect(doc?.transcribeState).toBe('done')
    expect(useStore.getState().currentRawText).toBe('真正的正文')
  })

  it('只改这一条，不动别的文档', async () => {
    useStore.setState({
      docs: [
        ...useStore.getState().docs,
        { id: 'other', title: 'o', sourceType: 'text', transcribeState: 'pending' } as DocEntry,
      ],
    })
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })

    const { result } = renderHook(() => useStartTranscription())
    await act(async () => { await result.current.start(DOC_ID) })

    expect(useStore.getState().docs.find(d => d.id === 'other')?.transcribeState).toBe('pending')
  })

  it('失败时 docs 保持 pending（后端本来就没写盘）', async () => {
    retranscribeMock.mockResolvedValue({ error: '视频下载失败' })
    const onError = vi.fn()

    const { result } = renderHook(() => useStartTranscription())
    await act(async () => { await result.current.start(DOC_ID, { onError }) })

    expect(onError).toHaveBeenCalledWith('视频下载失败')
    expect(useStore.getState().docs.find(d => d.id === DOC_ID)?.transcribeState).toBe('pending')
  })

  it('onProducts 拿到的是同一个 docId（面板据此重载笔记产物）', async () => {
    retranscribeMock.mockResolvedValue({ status: 'done', engine: 'bcut' })
    getTranscriptMock.mockResolvedValue({ rawText: 'x' })
    const onProducts = vi.fn()

    const { result } = renderHook(() => useStartTranscription())
    await act(async () => { await result.current.start(DOC_ID, { onProducts }) })

    await waitFor(() => expect(onProducts).toHaveBeenCalledWith(DOC_ID))
  })
})
