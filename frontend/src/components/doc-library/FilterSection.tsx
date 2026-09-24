/**
 * 筛选区小节容器 —— 通用可折叠小节（标题行 + 激活摘要 + 折叠箭头 + 内容槽）。
 *
 * ⚠️ 收起时**子内容不进 DOM**（而不是 `hidden` / `h-0` 这类 CSS 隐藏）：CSS 隐藏时
 * 输入框和 chip 仍留在树里，「收起」就只是视觉骗局——Tab 能走进去、`querySelectorAll`
 * 数得到、将来若包进 form 还会跟着提交。判据必须能区分这两者，故测试断言的是
 * `[data-filter-section="type"]` 下 `input`/`button` 的**数量为 0**。
 *
 * ⚠️ 摘要必须带**具体值**（`PDF`，而不是「已筛选」）：小节一收起，它就是用户唯一能
 * 知道「我正在筛什么」的地方。写个「已筛选」有文案无信息，等于把筛选项藏起来还不说藏了什么。
 */
import { ChevronDown, ChevronRight, X } from 'lucide-react'
import type { SectionId } from './docLibraryPrefs'

export interface FilterSectionProps {
  /** 小节 id（同时作为 `data-filter-section` 的值与持久化的 key） */
  id: SectionId
  label: string
  /** 激活摘要。无激活条件时传 null —— 此时标题行右侧完全留空，不占位 */
  summary?: string | null
  /** 摘要右侧的「×」回调；不传则不渲染该按钮 */
  onClear?: () => void
  expanded: boolean
  onToggle: (id: SectionId) => void
  children: React.ReactNode
}

export function FilterSection({
  id, label, summary, onClear, expanded, onToggle, children,
}: FilterSectionProps) {
  const Chevron = expanded ? ChevronDown : ChevronRight
  return (
    <div data-filter-section={id}>
      <div className="flex items-center gap-1 px-3">
        <button
          data-filter-section-toggle={id}
          onClick={() => onToggle(id)}
          aria-expanded={expanded}
          className="flex items-center gap-1 flex-1 min-w-0 py-1 text-left rounded hover:bg-surface-hover"
        >
          <Chevron size={11} className="text-disabled shrink-0" />
          <span className="text-[11px] text-dimmed truncate">{label}</span>
        </button>
        {summary && (
          <span
            data-filter-summary={id}
            className="flex items-center gap-0.5 min-w-0 text-[11px] text-accent shrink-0 max-w-[60%]"
          >
            <span className="truncate">{summary}</span>
            {onClear && (
              <button
                data-filter-clear={id}
                onClick={onClear}
                title={`清除${label}`}
                className="shrink-0 rounded hover:bg-surface-hover p-px"
              >
                <X size={10} />
              </button>
            )}
          </span>
        )}
      </div>
      {expanded && <div className="px-3 pb-1.5 flex flex-wrap items-center gap-1">{children}</div>}
    </div>
  )
}
