/**
 * 文档卡片（单行）— 从 DocLibrary.tsx 抽出，`React.memo` 包裹。
 *
 * 分批渲染下每多画一行都要付一次 render 成本，而列表里绝大多数行在选中/编辑
 * 变化时内容完全没变——故所有回调走**引用稳定的 `actions` 对象**（父组件 useMemo），
 * 让浅比较真的能拦住重渲染。
 */
import { memo } from 'react'
import { appUrl } from '../../api/client'
import { CheckSquare, Square, Pencil, Trash2, Search } from 'lucide-react'
import type { DocEntry } from '../../store/types'
import { entryDelay } from './entryDelay'
import { IconByName, SRC_LABEL, TYPE_COLORS, iconForType } from './docMeta'

export interface DocRowActions {
  onSelect: (id: string) => void
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
  selectMode: boolean
  /** 该文档的**原文**（transcript）里有搜索匹配 → 加角标 */
  matched: boolean
  /** 非 null = 正在编辑标题，值为草稿 */
  titleDraft: string | null
  /** 非 null = 正在编辑标签，值为草稿 */
  tagsDraft: string | null
  actions: DocRowActions
}

function DocRowImpl({
  doc, index, active, selected, selectMode, matched, titleDraft, tagsDraft, actions,
}: DocRowProps) {
  const colors = TYPE_COLORS[doc.sourceType] || TYPE_COLORS.text
  const typeIcon = iconForType(doc.sourceType)

  return (
    <div
      data-doc-row
      draggable={!titleDraft && !selectMode}
      onDragStart={e => actions.onDragStart(e, doc)}
      onDragEnd={actions.onDragEnd}
      onClick={() => selectMode ? actions.onToggleSelect(doc.id) : actions.onSelect(doc.id)}
      onContextMenu={e => actions.onContextMenu(e, doc.id)}
      className={`group relative cursor-pointer transition-all animate-[card-enter_0.3s_ease-out_backwards]
        rounded-xl border py-2.5 px-3
        ${selected || active
          ? 'border-accent/30 bg-accent/5 shadow-sm'
          : 'border-transparent hover:border-border-light hover:bg-surface-hover'
        }`}
      style={{ animationDelay: `${entryDelay(index)}ms` }}
    >
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
            {selectMode && (
              <button
                onClick={e => { e.stopPropagation(); actions.onToggleSelect(doc.id) }}
                className="shrink-0 mt-0.5"
              >
                {selected
                  ? <CheckSquare size={18} className="text-accent" />
                  : <Square size={18} className="text-disabled" />}
              </button>
            )}
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
            </div>
            <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 shrink-0">
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
