/**
 * 列表工具栏 —— 「排列」与「排序」两个**互不干扰**的旋钮。
 *
 * - **排列**（`groupBy`）管横向归类：怎么分组呈现
 * - **排序**（`sort`）管**组内**纵向顺序
 *
 * 两者正交，刻意不做成「筛选时退化成扁平」那种联动：筛了「PDF」而排列仍是「按文件夹」时，
 * 列表按文件夹分组、每组里只显示 PDF、空组自动隐藏——用户能一眼看到「这些 PDF 分别在
 * 哪些文件夹里」。加了联动反而多出一个「树和筛选打架」的状态要解释。
 */
import { ArrowUpDown } from 'lucide-react'
import { GROUP_OPTIONS, SORT_OPTIONS, type GroupBy, type SortKey } from './useDocQuery'

export interface ListToolbarProps {
  groupBy: GroupBy
  sort: SortKey
  onGroupBy: (g: GroupBy) => void
  onSort: (s: SortKey) => void
  /** 本次搜索有真命中（`rankOf.size > 0`）—— 决定「相关度」键出不出现在选项里 */
  showRelevance: boolean
}

export function ListToolbar({ groupBy, sort, onGroupBy, onSort, showRelevance }: ListToolbarProps) {
  const chip = (on: boolean) =>
    `px-1.5 py-0.5 rounded text-[11px] transition-colors ${
      on ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover'
    }`

  return (
    <div data-list-toolbar className="shrink-0 flex items-center gap-1 flex-wrap px-3 pb-1">
      <span className="text-[10px] text-disabled shrink-0">排列</span>
      {GROUP_OPTIONS.map(o => (
        <button key={o.key} data-group-by={o.key} onClick={() => onGroupBy(o.key)} className={chip(groupBy === o.key)}>
          {o.label}
        </button>
      ))}
      <span className="ml-auto flex items-center gap-0.5 shrink-0">
        <ArrowUpDown size={11} className="text-disabled" />
        {SORT_OPTIONS.filter(o => !o.requiresRank || showRelevance).map(o => (
          <button key={o.key} data-sort-key={o.key} onClick={() => onSort(o.key)} className={chip(sort === o.key)}>
            {o.label}
          </button>
        ))}
      </span>
    </div>
  )
}
