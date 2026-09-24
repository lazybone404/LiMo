/**
 * 文档行 — 两种密度：`detailed`（卡片，默认）与 `compact`（26px 单行）。
 * 从 DocLibrary.tsx 抽出，`React.memo` 包裹。
 *
 * ⚠️ 紧凑行高 **26px 与分组头同高**（`GroupHeader` 的 `compact`）：简略模式的形态是
 * **IDE 文件树**——文件夹是可折叠的父节点、文档是缩进一层的子节点，两者同高看起来才是
 * 一棵树而不是「一条标题 + 一串卡片」。改行高时**必须同时改 GroupHeader 的那个值**，
 * 否则父节点比子节点矮，树就散了。
 *
 * ⚠️ `active`（预览目标）与 `selected`（批量操作目标）是**两个正交维度**，视觉必须能
 * 叠加共存。曾共用一套样式（`selected || active` 走同一个 className），于是「正在看 A、
 * 勾了 B」时两行长得一模一样——用户分不清哪个是打开着的、哪个是要被批量删的。
 *
 * ⚠️ 分批渲染下每多画一行都要付一次 render 成本，而列表里绝大多数行在选中/编辑变化时
 * 内容完全没变 —— 故所有回调走**引用稳定的 `actions` 对象**（父组件 useMemo），
 * 让浅比较真的能拦住重渲染。
 */
import { memo } from 'react'
import { appUrl } from '../../api/client'
import { CheckSquare, Square, Pencil, Trash2, Search } from 'lucide-react'
import { SearchSnippet } from '../ui/SearchSnippet'
import type { DocEntry } from '../../store/types'
import type { Density } from './docLibraryPrefs'
import { entryDelay } from './entryDelay'
import { IconByName, SRC_LABEL, TYPE_COLORS, iconForType } from './docMeta'

export interface DocRowActions {
  /**
   * 行点击 —— 修饰键语义在 `useDocSelection` 里：普通单击**打开**，Ctrl/Cmd 加选、
   * Shift 区间。故这里必须收下 MouseEvent，不能只传 id。
   */
  onRowClick: (e: React.MouseEvent, id: string) => void
  onToggleSelect: (id: string) => void
  onContextMenu: (e: React.MouseEvent, docId: string) => void
  onEditTitle: (doc: DocEntry) => void
  onEditTags: (doc: DocEntry) => void
  onDelete: (id: string) => void
  onDragStart: (e: React.DragEvent, doc: DocEntry) => void
  onDragEnd: () => void
  onTitleDraftChange: (v: string) => void
  onTitleCommit: () => void
  onTitleCancel: () => void
  onTagsDraftChange: (v: string) => void
  onTagsCommit: () => void
  onTagsCancel: () => void
}

export interface DocRowProps {
  doc: DocEntry
  /** 列表索引，仅用于入场错位延迟（已确保有界，见 entryDelay） */
  index: number
  active: boolean
  selected: boolean
  density: Density
  /** 该文档的**原文**（transcript）里有搜索匹配 → 加角标 */
  matched: boolean
  /** 搜索命中时的原文摘要（由 `buildResultSet` 从 FTS 命中里取） */
  snippet?: string
  /** 非 null = 正在编辑标题，值为草稿 */
  titleDraft: string | null
  /** 非 null = 正在编辑标签，值为草稿 */
  tagsDraft: string | null
  actions: DocRowActions
}

/** 紧凑日期 `M-D`。⚠️ 用本地时间取月日，不用 `toISOString()`（那是 UTC，会差一天） */
function shortDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}-${d.getDate()}`
}

/** active / selected 两个维度的底色 —— 两处密度共用，避免各写一份后长歪 */
function rowTone(active: boolean, selected: boolean): string {
  if (active) return 'border-accent/30 bg-accent/5 shadow-sm'
  if (selected) return 'border-transparent bg-surface-hover'
  return 'border-transparent hover:border-border-light hover:bg-surface-hover'
}

function DocRowImpl({
  doc, index, active, selected, density, matched, snippet, titleDraft, tagsDraft, actions,
}: DocRowProps) {
  const colors = TYPE_COLORS[doc.sourceType] || TYPE_COLORS.text
  const selectedBox = (
    <button
      data-select-toggle
      aria-label={selected ? `取消选择 ${doc.title}` : `选择 ${doc.title}`}
      aria-pressed={selected}
      onClick={e => { e.stopPropagation(); actions.onToggleSelect(doc.id) }}
      className="shrink-0 flex items-center"
    >
      {selected
        ? <CheckSquare size={density === 'compact' ? 14 : 18} className="text-accent" />
        : <Square size={density === 'compact' ? 14 : 18} className="text-disabled" />}
    </button>
  )

  // ── 紧凑：26px 单行（与分组头同高，见文件头） ──
  if (density === 'compact') {
    return (
      <div
        data-doc-row
        draggable={!titleDraft}
        onDragStart={e => actions.onDragStart(e, doc)}
        onDragEnd={actions.onDragEnd}
        onClick={e => actions.onRowClick(e, doc.id)}
        onContextMenu={e => actions.onContextMenu(e, doc.id)}
        data-active={active ? 'true' : undefined}
        data-selected={selected ? 'true' : undefined}
        className={[
          'group relative flex items-center gap-2 h-6.5 px-2 rounded-md cursor-pointer transition-colors border',
          rowTone(active, selected),
          selected ? 'ring-1 ring-accent/30' : '',
        ].filter(Boolean).join(' ')}
      >
        {active && (
          <span aria-hidden className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-accent" />
        )}
        {titleDraft === null ? (
          <>
            {selectedBox}
            {/* 类型用 4×16 竖条而不是 16×16 方块：省 12px 横向空间，26px 行里这很值 */}
            <span aria-hidden className="w-1 h-4 rounded-full shrink-0" style={{ backgroundColor: colors.dot }} />
            <span className="flex-1 min-w-0 text-[12px] truncate">{doc.title}</span>
            {matched && <Search size={11} className="text-accent/80 shrink-0" />}
            {/* ⚠️ 右侧固定 40px：日期与操作组**互斥显示**（hover 覆盖而非并排），
                宽度固定住标题才不会在 hover 时左右抽动。`hidden`（display:none）
                同时也解决了「看不见却仍可点」——它不像 `opacity-0` 那样只隐藏视觉。 */}
            <span className="shrink-0 w-10 flex justify-end">
              {doc.createdAt && (
                <span className="text-[10px] text-disabled group-hover:hidden">{shortDate(doc.createdAt)}</span>
              )}
              <span className="hidden group-hover:flex items-center gap-0.5">
                <button
                  onClick={e => { e.stopPropagation(); actions.onEditTitle(doc) }}
                  className="w-4.5 h-4.5 rounded hover:bg-surface-hover flex items-center justify-center"
                  title="编辑标题"
                >
                  <Pencil size={12} className="text-disabled" />
                </button>
                <button
                  onClick={e => { e.stopPropagation(); actions.onDelete(doc.id) }}
                  className="w-4.5 h-4.5 rounded hover:bg-error/10 flex items-center justify-center"
                  title="删除"
                >
                  <Trash2 size={12} className="text-disabled hover:text-error" />
                </button>
              </span>
            </span>
          </>
        ) : (
          <input
            value={titleDraft}
            onChange={e => actions.onTitleDraftChange(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') actions.onTitleCommit()
              if (e.key === 'Escape') actions.onTitleCancel()
            }}
            onBlur={actions.onTitleCommit}
            autoFocus
            onClick={e => e.stopPropagation()}
            className="flex-1 px-2 py-0.5 text-[12px] bg-elevated border border-accent rounded-md outline-none"
          />
        )}
      </div>
    )
  }

  // ── 详细：卡片 ──
  const typeIcon = iconForType(doc.sourceType)
  return (
    <div
      data-doc-row
      draggable={!titleDraft}
      onDragStart={e => actions.onDragStart(e, doc)}
      onDragEnd={actions.onDragEnd}
      onClick={e => actions.onRowClick(e, doc.id)}
      onContextMenu={e => actions.onContextMenu(e, doc.id)}
      data-active={active ? 'true' : undefined}
      data-selected={selected ? 'true' : undefined}
      className={[
        'group relative cursor-pointer transition-all animate-[card-enter_0.3s_ease-out_backwards]',
        'rounded-xl border py-2.5 px-3',
        rowTone(active, selected),
        // 底色互斥避免 Tailwind 同时挂两个 bg-* 靠 CSS 顺序分胜负，ring 则始终跟随勾选
        selected ? 'ring-1 ring-accent/30' : '',
      ].filter(Boolean).join(' ')}
      style={{ animationDelay: `${entryDelay(index)}ms` }}
    >
      {/* 「当前预览」的标识 —— 竖条比整块底色更轻，且能与勾选态叠加共存 */}
      {active && (
        <span aria-hidden className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-accent" />
      )}
      {/* 编辑标题 — 内联 */}
      {titleDraft !== null ? (
        <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
          <input
            value={titleDraft}
            onChange={e => actions.onTitleDraftChange(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') actions.onTitleCommit()
              if (e.key === 'Escape') actions.onTitleCancel()
            }}
            onBlur={actions.onTitleCommit}
            autoFocus
            className="flex-1 px-2 py-1 text-[13px] bg-elevated border border-accent rounded-md outline-none"
          />
        </div>
      ) : (
        <>
          {/* 主行：复选框 + 缩略图 + 标题 + 操作按钮 */}
          <div className="flex items-start gap-2.5">
            {/* 复选框**常驻**（不是只在某个「批量模式」里出现）：它既是鼠标用户
                唯一「只选不开」的路径，也是触屏与键盘用户的唯一选择入口。 */}
            <span className="mt-0.5">{selectedBox}</span>
            {doc.coverUrl ? (
              <img src={appUrl(doc.coverUrl)} alt="" referrerPolicy="no-referrer"
                className="w-10 h-10 rounded-lg object-cover shrink-0 mt-0.5" />
            ) : (
              <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                style={{ backgroundColor: colors.bg, color: colors.text }}>
                <IconByName name={typeIcon} size={18} />
              </div>
            )}
            <div className="flex-1 min-w-0">
              <span className="text-[13px] font-medium leading-snug line-clamp-2">{doc.title}</span>
              {/* 搜索命中时把原文片段显示在标题下方 —— 并入主列表后它依然要能被看见，
                  否则用户只知道「这篇命中了」，不知道命中的是哪一句。
                  ⚠️ 必须走 SearchSnippet（唯一放行 `<mark>` 的地方）：snippet 来自用户导入的
                  原文，直接塞进 DOM 会带回文中的 `<img onerror=…>`。 */}
              {snippet && (
                <SearchSnippet raw={snippet} className="block text-[11px] text-disabled mt-0.5 line-clamp-2" />
              )}
            </div>
            {/* ⚠️ `opacity-0` 只隐藏视觉，**不阻止点击** —— 没有 `pointer-events-none`
                时这些按钮在看不见的状态下依然可点（误触删除）。`pointer-events` 管鼠标、
                不管键盘，所以还要 `focus-within` 给 Tab 用户一条能看见它们的路径。 */}
            <div className="opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto transition-opacity flex items-center gap-0.5 shrink-0">
              <button onClick={e => { e.stopPropagation(); actions.onEditTitle(doc) }}
                className="w-5 h-5 rounded hover:bg-surface-hover flex items-center justify-center" title="编辑标题">
                <Pencil size={11} className="text-disabled" />
              </button>
              <button onClick={e => { e.stopPropagation(); actions.onDelete(doc.id) }}
                className="w-5 h-5 rounded hover:bg-error/10 flex items-center justify-center" title="删除">
                <Trash2 size={11} className="text-disabled hover:text-error" />
              </button>
            </div>
          </div>

          {/* 底部元数据行：日期 · 语言 · 类型 · 自定义标签 · 原文命中 */}
          <div className="flex items-center gap-1.5 mt-2 flex-wrap">
            {doc.createdAt && (
              <span className="text-[11px] text-disabled">
                {new Date(doc.createdAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
              </span>
            )}
            {doc.language && (
              <span className="text-[10px] text-disabled uppercase font-medium px-1.5 py-0.5 bg-surface-hover rounded-full">{doc.language}</span>
            )}
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: colors.bg, color: colors.text }}>
              {SRC_LABEL[doc.sourceType] || doc.sourceType}
            </span>
            {(doc.tags || []).slice(0, 2).map(t => (
              <button key={t} onClick={e => { e.stopPropagation(); actions.onEditTags(doc) }}
                className="px-1.5 py-0.5 bg-surface-hover text-disabled rounded-full text-[10px] hover:text-dimmed">
                {t}
              </button>
            ))}
            {/* ⚠️ 措辞是「原文」不是「正文」：FTS5 索引 title+content 两列，
                一次查询判不出命中列，而「原文里有匹配」对所有命中都成立 */}
            {matched && (
              <span className="flex items-center gap-0.5 text-[10px] text-accent/80" title="原文中有匹配">
                <Search size={10} />原文
              </span>
            )}
          </div>
        </>
      )}

      {/* 标签编辑 — 内联 */}
      {tagsDraft !== null && (
        <div className="mt-1 flex items-center gap-1" onClick={e => e.stopPropagation()}>
          <input
            value={tagsDraft}
            onChange={e => actions.onTagsDraftChange(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') actions.onTagsCommit()
              if (e.key === 'Escape') actions.onTagsCancel()
            }}
            onBlur={actions.onTagsCommit}
            autoFocus
            placeholder="标签, 逗号分隔"
            className="flex-1 px-2 py-0.5 text-[11px] bg-elevated border border-accent rounded-md outline-none"
          />
        </div>
      )}
    </div>
  )
}

export const DocRow = memo(DocRowImpl)
