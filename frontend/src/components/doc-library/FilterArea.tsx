/**
 * 筛选区容器 —— 两级折叠的**外层**（一级：整区可收；二级：各小节，见 `FilterSection`）。
 *
 * ⚠️ 「已激活 N 项」与「清除全部」必须留在**头部**：整区收起后，头部是用户唯一还能
 * 看到的东西。把「清除全部」放区里，收起状态下就成了「用户知道有筛选、也知道该清除，
 * 但够不到按钮」。
 */
import { ChevronDown, ChevronRight, X } from 'lucide-react'
import { useStore } from '../../store'
import { DateFilter, FolderFilter, TagFilter, TitleFilter, TypeFilter } from './filters'
import type { SectionId } from './docLibraryPrefs'

export interface FilterAreaProps {
  collapsed: boolean
  /** 展开时筛选区的固定高度（px），内部滚动；列表区吃掉剩余空间 */
  height: number
  expandedSections: string[]
  onToggleArea: () => void
  onToggleSection: (id: SectionId) => void
}

export function FilterArea({
  collapsed, height, expandedSections, onToggleArea, onToggleSection,
}: FilterAreaProps) {
  // ⚠️ 选择器返回**数字**（原始值）才安全：返回 `{...}` 时 zustand 用 `Object.is` 比较，
  // 每次都是新对象 → 无限重渲染。这里恰好可以在选择器里直接算完。
  const activeCount = useStore(s =>
    (s.activeFolderId !== null ? 1 : 0) +
    (s.activeType ? 1 : 0) +
    (s.activeTags.length ? 1 : 0) +
    (s.titleFilter.trim() ? 1 : 0) +
    (s.dateFrom !== null || s.dateTo !== null ? 1 : 0),
  )

  const Chevron = collapsed ? ChevronRight : ChevronDown
  const isOpen = (id: SectionId) => expandedSections.includes(id)

  return (
    <div data-filter-area className="shrink-0 flex flex-col border-b border-border-light">
      <div className="flex items-center gap-1 pl-2 pr-3 py-1">
        <button
          data-filter-area-toggle
          onClick={onToggleArea}
          aria-expanded={!collapsed}
          className="flex items-center gap-1 flex-1 min-w-0 py-0.5 text-left rounded hover:bg-surface-hover"
        >
          <Chevron size={11} className="text-disabled shrink-0" />
          <span className="text-[11px] font-medium text-dimmed">筛选</span>
        </button>
        {activeCount > 0 && (
          <>
            <span data-filter-count className="text-[11px] text-accent shrink-0">
              已激活 {activeCount} 项
            </span>
            <button
              data-filter-clear-all
              onClick={clearAllFilters}
              title="清除全部筛选"
              className="shrink-0 flex items-center gap-0.5 text-[11px] text-muted hover:text-dimmed"
            >
              <X size={10} />清除全部
            </button>
          </>
        )}
      </div>

      {!collapsed && (
        <div data-filter-body className="overflow-y-auto space-y-0.5" style={{ height }}>
          <FolderFilter expanded={isOpen('folder')} onToggle={onToggleSection} />
          <TypeFilter expanded={isOpen('type')} onToggle={onToggleSection} />
          <TagFilter expanded={isOpen('tag')} onToggle={onToggleSection} />
          <TitleFilter expanded={isOpen('title')} onToggle={onToggleSection} />
          <DateFilter expanded={isOpen('date')} onToggle={onToggleSection} />
        </div>
      )}
    </div>
  )
}

/**
 * 清除全部筛选 —— 逐个走 store 的 setter，**不**在这里 `set({...})` 一把梭。
 *
 * 每个 setter 内部已经把「清空选择」和筛选变更放在同一次 `set` 里（见 docsSlice）；
 * 绕过它们直接 set 就绕过了清选那一半，恰好复活了本轮要消灭的时序窗口。
 * 搜索框**不在清除范围内**：它在筛选区之外，是独立的一行，用户点「清除全部筛选」
 * 时不会预期自己的搜索词也没了。
 */
export function clearAllFilters() {
  const s = useStore.getState()
  s.setActiveFolderId(null)
  s.setActiveType('')
  s.setActiveTags([])
  s.setTitleFilter('')
  s.setDateRange(null, null)
}
