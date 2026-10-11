/**
 * 简略模式下的筛选入口 —— 一个图标按钮 + 浮层，**内容复用同一批 `filters.tsx` 小节**。
 *
 * ⚠️ 浮层是**卸载式**的（`open && <div/>`），不是 CSS 隐藏：简略模式的全部意义是
 * 「省纵向空间、一屏看更多」，把 5 个小节留在 DOM 里就白省了；而且测试判据
 * 「compact 下 `[data-filter-section]` 数为 0」正是在钉住这一点——改成 `hidden`
 * 会让这条判据失效（DOM 仍在，数得出来）。
 *
 * ⚠️ 浮层内小节默认**全展开**（与详细模式相反）：详细模式空间固定、默认收起以免撑开面板；
 * 浮层是用户主动点开的、且高度自适应，此时把内容再藏一层等于让用户点两次才看到东西。
 */
import { useEffect, useRef, useState } from 'react'
import { SlidersHorizontal, X } from 'lucide-react'
import { useStore } from '../../store'
import { DateFilter, FolderFilter, TagFilter, TitleFilter, TypeFilter } from './filters'
import { clearAllFilters } from './FilterArea'
import { SECTION_IDS, type SectionId } from './docLibraryPrefs'

export function FilterPopover() {
  const [open, setOpen] = useState(false)
  // 浮层内小节一律默认展开；开合状态留在本地——浮层一关就没了，没有「下次打开还记得」的语义
  const [openSections, setOpenSections] = useState<string[]>([...SECTION_IDS])
  // 按钮 + 浮层的共同祖先：判断「点在内部还是外部」的界
  const rootRef = useRef<HTMLDivElement>(null)

  // ⚠️ 点浮层外关闭走 **document 监听**，不再是透明遮罩（2026-10-11 改）：
  // 本组件挂在文档库的 `<aside>` 里，而那个 aside 带 `backdrop-blur-[20px]`——
  // `backdrop-filter` 会**创建包含块**，把 `fixed` 后代锁进面板自身的大小，于是
  // `fixed inset-0` 遮罩实际只盖住侧栏那一条：点预览区根本关不掉浮层（模式 #67，
  // 与用户报的「浮层卡在文档栏里」同源）。而把浮层整个 Portal 到 body 也不可行——
  // 它是 `absolute` 定位于这个按钮的下拉，脱离 `relative` 父就失去定位基准。
  // 原注释担心的「点按钮本身也算外部」由 `contains` 解决：按钮在 rootRef 内。
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // ⚠️ 选择器返回**数字**：返回对象时 zustand 用 Object.is 比较，每次都算新对象 → 无限重渲染
  const activeCount = useStore(s =>
    (s.activeFolderId !== null ? 1 : 0) +
    (s.activeType ? 1 : 0) +
    (s.activeTags.length ? 1 : 0) +
    (s.titleFilter.trim() ? 1 : 0) +
    (s.dateFrom !== null || s.dateTo !== null ? 1 : 0),
  )

  const toggleSection = (id: SectionId) => setOpenSections(s =>
    s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  const isOpen = (id: SectionId) => openSections.includes(id)

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        data-filter-popover-toggle
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className={`h-full flex items-center gap-1 px-2 rounded-lg transition-colors ${
          activeCount > 0 ? 'bg-accent/10 text-accent' : 'bg-surface text-muted hover:bg-surface-hover'
        }`}
        title="筛选"
      >
        <SlidersHorizontal size={14} />
        {activeCount > 0 && <span data-filter-badge className="text-[10px] font-medium">{activeCount}</span>}
      </button>

      {open && (
        <>
          <div
            data-filter-popover
            className="absolute right-0 top-full mt-1 z-50 w-[264px] max-h-[420px] overflow-y-auto rounded-xl border border-border-light bg-elevated shadow-2xl py-1"
          >
            <div className="flex items-center justify-between px-3 py-1">
              <span className="text-[11px] font-medium text-dimmed">筛选</span>
              <span className="flex items-center gap-2">
                {activeCount > 0 && (
                  <button
                    data-filter-popover-clear
                    onClick={clearAllFilters}
                    className="text-[11px] text-muted hover:text-dimmed"
                  >
                    清除全部
                  </button>
                )}
                <button onClick={() => setOpen(false)} title="关闭">
                  <X size={12} className="text-disabled hover:text-dimmed" />
                </button>
              </span>
            </div>
            <FolderFilter expanded={isOpen('folder')} onToggle={toggleSection} />
            <TypeFilter expanded={isOpen('type')} onToggle={toggleSection} />
            <TagFilter expanded={isOpen('tag')} onToggle={toggleSection} />
            <TitleFilter expanded={isOpen('title')} onToggle={toggleSection} />
            <DateFilter expanded={isOpen('date')} onToggle={toggleSection} />
          </div>
        </>
      )}
    </div>
  )
}
