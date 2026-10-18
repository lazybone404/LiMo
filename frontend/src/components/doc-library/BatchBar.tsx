/**
 * 批量操作条 — 选中若干文档后出现的工具栏。
 *
 * 按钮按**用途分三段**（从左到右）：产出（合成 / 导出）｜整理（移动 / 打标签 / 智能分类）
 * ｜危险（删除）。分段的依据是「误点的代价」：产出段点错只是多一份产物，整理段点错是
 * 改了用户的分类，危险段点错要重新导入。混排成一长条时眼睛没有落点，删除和打标签挨着。
 *
 * ⚠️ 「全选」= **当前筛选结果的全部匹配项**（跨分批），不是「已渲染的那几十条」。
 * 分批渲染只是少画 DOM，不代表数据只有那么多——工具栏因此必须显示真实选中数，
 * 并在「选中数 > 已渲染条数」时补一句「含未显示项」，否则用户会以为只选中了看得见的那些。
 *
 * ⚠️ 移动改用 `FolderPicker` 浮层（不再是内联的扁平列表）：文件夹可嵌套后，扁平列表
 * 里 `前端` / `后端` / `React` 全在同一层，选中哪个全凭记忆。浮层照旧带
 * `data-batch-move-panel` / `data-move-target` 两个既有定位锚。
 */
import { useState } from 'react'
import { Archive, FolderInput, Tag, Trash2, X, CheckSquare, Square, Layers, Sparkles } from 'lucide-react'
import type { FolderEntry } from '../../store/types'
import { FolderPicker } from './FolderPicker'

export interface BatchBarProps {
  selectedCount: number
  /** 当前已渲染的行数（分批渲染下的可见条数） */
  renderedCount: number
  totalMatches: number
  folders: FolderEntry[]
  busy: boolean
  allSelected: boolean
  onToggleSelectAll: () => void
  onExport: () => void
  /** 多篇合成对比综述（未选够 2 篇时按钮禁用，见下方 title） */
  onSynthesize: () => void
  onMove: (folderId: string) => void
  onTag: (tags: string[], mode: 'add' | 'replace') => void
  /** 智能分类（模型建议 + 用户审定）。**1 篇也能用**，没有合成那样的篇数门槛 */
  onClassify: () => void
  onDelete: () => void
  onClear: () => void
  /** 移动浮层里的「新建文件夹…」（建在根级并立即选中）。不传则不给这一行 */
  onCreateFolder?: (name: string) => Promise<string | null>
}

/** 分组之间的竖分隔（视觉上把「产出 / 整理 / 危险」断开） */
function Sep() {
  return <span aria-hidden className="w-px h-3.5 bg-border-light shrink-0" />
}

export function BatchBar({
  selectedCount, renderedCount, totalMatches, folders, busy, allSelected,
  onToggleSelectAll, onExport, onMove, onTag, onClassify, onDelete, onClear, onSynthesize,
  onCreateFolder,
}: BatchBarProps) {
  const [open, setOpen] = useState<'move' | 'tag' | null>(null)
  /** 移动浮层的锚点（视口坐标；按钮 rect 的右下角） */
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null)
  const [tagDraft, setTagDraft] = useState('')
  const [tagMode, setTagMode] = useState<'add' | 'replace'>('add')

  const hiddenNote = selectedCount > renderedCount ? '（含未显示项）' : ''

  const commitTag = () => {
    const tags = tagDraft.split(/[,，]/).map(t => t.trim()).filter(Boolean)
    if (!tags.length) return
    setOpen(null)
    setTagDraft('')
    onTag(tags, tagMode)
  }

  return (
    <div className="px-3 py-2 border-b border-border-light shrink-0">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[12px] font-medium text-accent">
          已选 {selectedCount} 项{hiddenNote}
        </span>
        <button
          onClick={onToggleSelectAll}
          disabled={busy}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-dimmed disabled:opacity-50"
          title={allSelected ? '取消全选' : `全选当前筛选结果（${totalMatches} 项）`}
        >
          {allSelected ? <CheckSquare size={12} /> : <Square size={12} />}
          {allSelected ? '取消全选' : '全选'}
        </button>

        <div className="flex-1" />

        {/* ── 产出段：产生新东西（点错的代价只是多一份产物）── */}
        <button onClick={onSynthesize} disabled={busy || selectedCount < 2}
          title={selectedCount < 2 ? '至少选择 2 篇才能生成对比综述' : `把选中的 ${selectedCount} 篇合成一篇对比综述`}
          className="w-6 h-6 rounded-md hover:bg-accent/10 flex items-center justify-center disabled:opacity-50 disabled:hover:bg-transparent">
          <Layers size={13} className="text-accent" />
        </button>
        <button onClick={onExport} disabled={busy} title="导出 ZIP"
          className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center disabled:opacity-50">
          <Archive size={13} className="text-muted" />
        </button>

        <Sep />

        {/* ── 整理段：改的是用户的分类与元数据 ── */}
        <button
          onClick={e => {
            const r = e.currentTarget.getBoundingClientRect()
            setAnchor({ x: r.left, y: r.bottom + 4 })
            setOpen(open === 'move' ? null : 'move')
          }}
          disabled={busy}
          title="移动到文件夹"
          className={`w-6 h-6 rounded-md flex items-center justify-center disabled:opacity-50 ${open === 'move' ? 'bg-accent/10 text-accent' : 'hover:bg-surface-hover'}`}>
          <FolderInput size={13} className={open === 'move' ? 'text-accent' : 'text-muted'} />
        </button>
        <button onClick={() => setOpen(open === 'tag' ? null : 'tag')} disabled={busy}
          title="批量打标签"
          className={`w-6 h-6 rounded-md flex items-center justify-center disabled:opacity-50 ${open === 'tag' ? 'bg-accent/10 text-accent' : 'hover:bg-surface-hover'}`}>
          <Tag size={13} className={open === 'tag' ? 'text-accent' : 'text-muted'} />
        </button>
        {/* 智能分类：与上面两个同属「整理既有文档」，跟在打标签之后。
            ⚠️ **不设篇数门槛**（1 篇也能整理），与合成按钮的 `selectedCount < 2` 刻意不同 */}
        <button onClick={onClassify} disabled={busy} data-batch-classify
          title={`让模型为选中的 ${selectedCount} 篇建议文件夹与标签`}
          className="w-6 h-6 rounded-md hover:bg-accent/10 flex items-center justify-center disabled:opacity-50 disabled:hover:bg-transparent">
          <Sparkles size={13} className="text-accent" />
        </button>

        <Sep />

        {/* ── 危险段：不可从界面撤销 ── */}
        <button onClick={onDelete} disabled={busy} title="删除选中文档"
          className="w-6 h-6 rounded-md hover:bg-error/10 flex items-center justify-center disabled:opacity-50">
          <Trash2 size={13} className="text-error" />
        </button>
        <button onClick={onClear} disabled={busy} title="取消选择"
          className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center disabled:opacity-50">
          <X size={13} className="text-muted" />
        </button>
      </div>

      {/* ⚠️ 移动是**浮层**（Portal 到 body），不是内联面板：它要长列表滚动与折叠，
          塞在这条工具栏下方会把列表挤走一半。`batchPanel` 让根节点带
          `data-batch-move-panel`，既有测试按它定位。 */}
      {open === 'move' && anchor && (
        <FolderPicker
          folders={folders}
          rootLabel="未分类（移出文件夹）"
          x={anchor.x}
          y={anchor.y}
          batchPanel
          onPick={id => { setOpen(null); onMove(id) }}
          onClose={() => setOpen(null)}
          onCreate={onCreateFolder}
        />
      )}

      {open === 'tag' && (
        <div className="mt-1.5 flex items-center gap-1">
          <input
            value={tagDraft}
            onChange={e => setTagDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commitTag() }}
            autoFocus
            placeholder="标签, 逗号分隔"
            className="flex-1 px-2 py-1 text-[12px] bg-elevated border border-accent rounded-md outline-none"
          />
          <select value={tagMode} onChange={e => setTagMode(e.target.value as 'add' | 'replace')}
            className="text-[11px] bg-elevated border border-border-light rounded-md px-1 py-1 outline-none">
            <option value="add">追加</option>
            <option value="replace">替换</option>
          </select>
          <button onClick={commitTag}
            className="px-2 py-1 text-[11px] rounded-md bg-accent text-on-accent hover:bg-accent-hover">
            应用
          </button>
        </div>
      )}
    </div>
  )
}
