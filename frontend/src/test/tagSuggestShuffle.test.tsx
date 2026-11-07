/**
 * 标签建议条的「换一批」（2026-11-07）。
 *
 * 钉两件事：
 *
 * 1. **候选多于一屏时出现「换一批」，点一下换到另一批**。判据用 `data-tag-suggestion`
 *    实际渲染出来的标签，不看内部状态 —— 用户看见的就是这排 chip。
 * 2. **只有一批时不渲染按钮**。点了不会有任何变化的按钮，比没有更糟。
 *
 * 对照实验（真跑过）：把 `pageCount > 1` 改成恒真 → 第 2 条红。把 `page % pageCount`
 * 换成 `Math.min(page, pageCount - 1)` → 第 1 条仍绿（只有两批时两者等价），
 * 所以这里没有覆盖「翻到底再翻回开头」那一支，它由 `pageCount` 的计算保证。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import type { DocEntry } from '../api/core'
import { TagSuggestions } from '../components/preview/TagSuggestions'
import { useStore } from '../store'

function doc(id: string, title: string, tags: string[]): DocEntry {
  return {
    id, title, tags, sourceType: 'video',
    sourceFileName: '', sourceUrl: '', coverUrl: '', engine: '', language: '',
    contentHash: '', createdAt: '', folderId: '',
  } as DocEntry
}

/** 当前文档（无标签），另一篇只用来提供词表 —— 建议从全库标签里挑。 */
function seed(vocab: string[]) {
  useStore.setState({
    currentDocId: 'd1',
    docs: [doc('d1', '', []), doc('d2', '', vocab)],
  })
}

function chips(): string[] {
  return screen
    .queryAllByRole('button')
    .filter((b) => b.hasAttribute('data-tag-suggestion'))
    .map((b) => b.getAttribute('data-tag-suggestion') || '')
}

const SIX = ['甲', '乙', '丙', '丁', '戊', '己']

beforeEach(() => {
  useStore.setState({ currentDocId: '', docs: [] })
})

describe('换一批', () => {
  it('候选超过一屏时出现按钮，点一下换到另一批', () => {
    seed(SIX)
    render(<TagSuggestions content={SIX.join(' ')} />)

    const first = chips()
    expect(first).toHaveLength(4)
    expect(screen.getByRole('button', { name: /换一批/ })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /换一批/ }))

    const second = chips()
    expect(second).toHaveLength(2)
    // 两批不重叠 —— 排序确定、截断只影响尾部，这正是 `suggestTags` 文件头
    // 承诺给分页用的那条性质
    expect(second.some((t) => first.includes(t))).toBe(false)
  })

  it('只有一批时不渲染按钮', () => {
    seed(['甲', '乙'])
    render(<TagSuggestions content="甲 乙" />)

    expect(chips()).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /换一批/ })).toBeNull()
  })
})
