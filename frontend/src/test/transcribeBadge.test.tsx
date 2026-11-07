/**
 * 文档行里的「待转录」徽章（2026-11-07）。
 *
 * ## 这里守的是什么
 *
 * 导入与转录分开之后，库里会长期存在 `transcribeState === 'pending'` 的文档。它们的
 * 正文是占位注释，与正常文档在列表里长得一模一样 —— 徽章是用户**唯一**能看出
 * 「这篇还不能读 / 还不能生成」的地方。
 *
 * ## 两种密度都要有，且判据是 `=== 'pending'`
 *
 * 1. 详细是**默认密度**。徽章只加在简略分支里，等于多数用户看不见；
 * 2. `transcribeState` 缺键 = 已转录（历史条目零迁移）。写成 `!== 'done'` 会让**全部
 *    历史文档**都挂上「待转录」，而它们其实随时能读、能生成。
 *
 * 对照实验：删掉详细分支里那个 `<span>` → 第 1 组红，其余组照旧绿（这正是缺陷
 * 难以察觉的原因：它只影响一种密度，而另一种看起来完全正常）。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'

import { DocRow, type DocRowActions } from '../components/doc-library/DocRow'
import type { DocEntry } from '../api/core'

const noop = () => {}
const actions: DocRowActions = {
  onRowClick: noop, onRowDoubleClick: noop, onToggleSelect: noop, onRowMouseDown: noop,
  onContextMenu: noop, onEditTitle: noop, onEditTags: noop, onDelete: noop,
  onDragStart: noop, onDragEnd: noop, onTitleDraftChange: noop, onTitleCommit: noop,
  onTitleCancel: noop, onTagsDraftChange: noop, onTagsCommit: noop, onTagsCancel: noop,
}

function doc(overrides: Partial<DocEntry> = {}): DocEntry {
  return {
    id: 'doc-1', title: '一篇视频', sourceType: 'video',
    sourceFileName: '', sourceUrl: '', coverUrl: '', engine: '', language: '',
    contentHash: '', tags: [], createdAt: '2026-11-07T10:00:00Z', folderId: '',
    ...overrides,
  } as DocEntry
}

function renderRow(d: DocEntry, density: 'detailed' | 'compact') {
  return render(
    <DocRow doc={d} index={0} active={false} selected={false} density={density}
      matched={false} titleDraft={null} tagsDraft={null} actions={actions} />,
  ).container
}

describe('「待转录」徽章', () => {
  it('详细密度（默认）也渲染', () => {
    expect(renderRow(doc({ transcribeState: 'pending' }), 'detailed').textContent)
      .toContain('待转录')
  })

  it('简略密度也渲染', () => {
    expect(renderRow(doc({ transcribeState: 'pending' }), 'compact').textContent)
      .toContain('待转录')
  })

  it('已转录不渲染', () => {
    expect(renderRow(doc({ transcribeState: 'done' }), 'detailed').textContent)
      .not.toContain('待转录')
  })

  it('缺键（历史条目）读作已转录，不渲染', () => {
    // 反过来写（`!== 'done'`）会让升级后全部历史文档突然挂上「待转录」。
    expect(renderRow(doc(), 'detailed').textContent).not.toContain('待转录')
  })
})
