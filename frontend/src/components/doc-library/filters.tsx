/**
 * 筛选区五个小节 —— 文件夹范围 / 文件类型 / 标签 / 标题关键词 / 日期范围。
 *
 * 每个都**直接读 store**，不抽成受控组件：它们本就只服务文档库，
 * 外面再套一层 props 管道会比组件本身还长，且容易在中转时漏掉一个维度。
 *
 * ⚠️ 除了「标题关键词」和「日期范围」，其余三节都是原来的常驻行搬进来的。
 * 搬动时最容易出的错是把某个条件搬丢了（比如类型筛选的「全部」与文件夹的「未分类」
 * 都是 `''`，看着像同一个值，实际一个表示「不限」一个表示「一个真实分组」）。
 */
import { useMemo, useState } from 'react'
import { useStore } from '../../store'
import { SOURCE_FILTERS, IconByName } from './docMeta'
import { collectTagFacets, countByFolder } from './useDocQuery'
import { FilterSection } from './FilterSection'
import type { SectionId } from './docLibraryPrefs'

interface SectionProps {
  expanded: boolean
  onToggle: (id: SectionId) => void
}

/**
 * 筛选 chip —— 视觉与既有的类型/标签按钮一致。
 * `value` 只用于测试定位（`[data-chip]`）：文件夹允许重名、标签可能与文件夹同名，
 * 按文本定位会命中多个元素。
 */
function Chip({ value, on, onClick, children }: {
  value: string
  on: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      data-chip={value}
      onClick={onClick}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] transition-colors ${
        on ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover'
      }`}
    >
      {children}
    </button>
  )
}

// ── 文件夹范围 ────────────────────────────────────────────────

export function FolderFilter({ expanded, onToggle }: SectionProps) {
  const docs = useStore(s => s.docs)
  const folders = useStore(s => s.folders)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolderId = useStore(s => s.setActiveFolderId)
  const { byFolder, uncategorized } = useMemo(() => countByFolder(docs, folders), [docs, folders])

  // 摘要写**具体名字**（写「已筛选」等于什么都没说——收起后这是唯一的线索）
  const summary = activeFolderId === null ? null
    : activeFolderId === '' ? '未分类'
      : folders.find(f => f.id === activeFolderId)?.name || '已删除的文件夹'

  return (
    <FilterSection
      id="folder" label="文件夹范围" summary={summary} expanded={expanded} onToggle={onToggle}
      onClear={activeFolderId !== null ? () => setActiveFolderId(null) : undefined}
    >
      <Chip value="__all__" on={activeFolderId === null} onClick={() => setActiveFolderId(null)}>全部</Chip>
      {/* ⚠️ 哨兵值 `__uncat__` 仅为测试定位：`data-chip=""` 已经被「全部」占了，
          而这一项的真实值是 `''`（未分类），两个 `''` 在 DOM 上无法区分。 */}
      <Chip value="__uncat__" on={activeFolderId === ''} onClick={() => setActiveFolderId('')}>
        未分类 <span className="text-disabled">{uncategorized}</span>
      </Chip>
      {folders.map(f => (
        <Chip
          key={f.id} value={f.id} on={activeFolderId === f.id}
          onClick={() => setActiveFolderId(activeFolderId === f.id ? null : f.id)}
        >
          {f.name} <span className="text-disabled">{byFolder.get(f.id) || 0}</span>
        </Chip>
      ))}
    </FilterSection>
  )
}

// ── 文件类型 ──────────────────────────────────────────────────

export function TypeFilter({ expanded, onToggle }: SectionProps) {
  const activeType = useStore(s => s.activeType)
  const setActiveType = useStore(s => s.setActiveType)
  const summary = activeType
    ? SOURCE_FILTERS.find(t => t.key === activeType)?.label || activeType
    : null

  return (
    <FilterSection
      id="type" label="文件类型" summary={summary} expanded={expanded} onToggle={onToggle}
      onClear={activeType ? () => setActiveType('') : undefined}
    >
      {SOURCE_FILTERS.map(t => (
        // ⚠️ 「全部」的 key 是 `''`，与文件夹的「未分类」同为 `''` 但语义完全不同
        // （这里是不限，那里是一个真实分组），故哨兵值也分开取。
        <Chip key={t.key} value={t.key || '__all__'} on={activeType === t.key} onClick={() => setActiveType(t.key)}>
          {t.icon && <IconByName name={t.icon} size={13} />} {t.label}
        </Chip>
      ))}
    </FilterSection>
  )
}

// ── 标签（用户自定义，多选 AND）─────────────────────────────────

/** 超过这个数才给过滤输入框——不到 20 个标签时，输入框比标签本身还占地方 */
const TAG_SEARCH_THRESHOLD = 20

export function TagFilter({ expanded, onToggle }: SectionProps) {
  const docs = useStore(s => s.docs)
  const activeTags = useStore(s => s.activeTags)
  const setActiveTags = useStore(s => s.setActiveTags)
  const [needle, setNeedle] = useState('')

  const facets = useMemo(() => collectTagFacets(docs), [docs])

  // ⚠️ 已选**置顶**，且在下面按关键词过滤时**永不消失**：这是「取消 12 个上限」之外
  // 唯一能保证「已激活的标签始终看得见、点得到」的机制——否则用户输入过滤词后，
  // 自己刚选的标签被滤掉了，连取消都取消不了。
  const ordered = useMemo(() => {
    const on = facets.filter(f => activeTags.includes(f.tag))
    const off = facets.filter(f => !activeTags.includes(f.tag))
    return [...on, ...off]
  }, [facets, activeTags])

  const n = needle.trim().toLowerCase()
  const shown = n
    ? ordered.filter(f => activeTags.includes(f.tag) || f.tag.toLowerCase().includes(n))
    : ordered

  if (!facets.length) return null

  const summary = activeTags.length === 0 ? null
    : activeTags.length === 1 ? activeTags[0]
      : `${activeTags[0]} 等 ${activeTags.length} 个`

  return (
    <FilterSection
      id="tag" label="标签" summary={summary} expanded={expanded} onToggle={onToggle}
      onClear={() => setActiveTags([])}
    >
      {facets.length > TAG_SEARCH_THRESHOLD && (
        <input
          data-tag-filter-search
          value={needle}
          onChange={e => setNeedle(e.target.value)}
          placeholder="过滤标签…"
          className="w-full px-2 py-0.5 mb-0.5 text-[11px] bg-surface rounded-md outline-none placeholder:text-disabled"
        />
      )}
      <div className="flex flex-wrap gap-1 w-full max-h-[120px] overflow-y-auto content-start">
        {shown.map(({ tag, count }) => {
          const on = activeTags.includes(tag)
          return (
            <Chip
              key={tag} value={tag} on={on}
              onClick={() => setActiveTags(on ? activeTags.filter(t => t !== tag) : [...activeTags, tag])}
            >
              {tag} <span className="text-disabled">{count}</span>
            </Chip>
          )
        })}
      </div>
    </FilterSection>
  )
}

// ── 标题关键词 ────────────────────────────────────────────────

export function TitleFilter({ expanded, onToggle }: SectionProps) {
  const titleFilter = useStore(s => s.titleFilter)
  const setTitleFilter = useStore(s => s.setTitleFilter)
  const v = titleFilter.trim()

  return (
    <FilterSection
      id="title" label="标题关键词" summary={v ? `「${v}」` : null}
      expanded={expanded} onToggle={onToggle} onClear={() => setTitleFilter('')}
    >
      {/* 与搜索框的分工写在 placeholder 里：那个找内容，这个只筛标题 */}
      <input
        data-title-filter
        value={titleFilter}
        onChange={e => setTitleFilter(e.target.value)}
        placeholder="只匹配标题…"
        className="w-full px-2 py-1 text-[11px] bg-surface rounded-md outline-none placeholder:text-disabled"
      />
    </FilterSection>
  )
}

// ── 日期范围 ──────────────────────────────────────────────────

const pad = (n: number) => String(n).padStart(2, '0')
/** 本地日期 → `YYYY-MM-DD`。⚠️ 不用 `toISOString()`：那是 UTC，东八区的今天 00:00 会变成昨天 */
const toDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

interface QuickRange { key: string; label: string; from: string; to: string }

function quickRanges(): QuickRange[] {
  const now = new Date()
  const today = toDay(now)
  const weekStart = new Date(now)
  // ⚠️ 「本周」= 本**自然周**（周一起），不是「最近 7 天」——后者在周一那天会带出上周一二的文档
  weekStart.setDate(now.getDate() - ((now.getDay() + 6) % 7))
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1)
  return [
    { key: 'today', label: '今天', from: today, to: today },
    { key: 'week', label: '本周', from: toDay(weekStart), to: today },
    { key: 'month', label: '本月', from: toDay(monthStart), to: today },
  ]
}

export function DateFilter({ expanded, onToggle }: SectionProps) {
  const dateFrom = useStore(s => s.dateFrom)
  const dateTo = useStore(s => s.dateTo)
  const setDateRange = useStore(s => s.setDateRange)

  // 空依赖：只在挂载时算一次。跨午夜后快捷项的边界会过期，但用户下次打开面板就更新了，
  // 为此挂一个每分钟跳动的定时器不划算。
  const ranges = useMemo(() => quickRanges(), [])
  const cur = ranges.find(r => r.from === dateFrom && r.to === dateTo)
  const active = dateFrom !== null || dateTo !== null

  const summary = active
    ? cur ? cur.label : `${dateFrom || '…'} ~ ${dateTo || '…'}`
    : null

  return (
    <FilterSection
      id="date" label="日期范围" summary={summary} expanded={expanded} onToggle={onToggle}
      onClear={() => setDateRange(null, null)}
    >
      <div className="flex flex-wrap gap-1 w-full">
        {ranges.map(r => (
          <Chip
            key={r.key} value={r.key} on={cur?.key === r.key}
            // 再点一次已激活的快捷项 = 取消（与 chip 的通用习惯一致）
            onClick={() => cur?.key === r.key ? setDateRange(null, null) : setDateRange(r.from, r.to)}
          >
            {r.label}
          </Chip>
        ))}
        <Chip value="__all__" on={!active} onClick={() => setDateRange(null, null)}>不限</Chip>
      </div>
      <div className="flex items-center gap-1 w-full mt-0.5">
        <input
          type="date" data-date-from value={dateFrom || ''}
          onChange={e => setDateRange(e.target.value || null, dateTo)}
          className="flex-1 min-w-0 px-1.5 py-0.5 text-[11px] bg-surface rounded-md outline-none"
        />
        <span className="text-[10px] text-disabled shrink-0">~</span>
        <input
          type="date" data-date-to value={dateTo || ''}
          onChange={e => setDateRange(dateFrom, e.target.value || null)}
          className="flex-1 min-w-0 px-1.5 py-0.5 text-[11px] bg-surface rounded-md outline-none"
        />
      </div>
    </FilterSection>
  )
}
