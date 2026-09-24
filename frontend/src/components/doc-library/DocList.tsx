/**
 * 文档列表 —— 分组渲染 / 平铺 / 分批哨兵 / 「移出文件夹」放置区。
 *
 * ⚠️ 行本身由调用方用 `renderRow` 工厂提供，本组件**不碰 DocRow 的 props**：
 * 那套 props（选中态、编辑态、动作对象、密度…）由 DocLibrary 的状态拼出来，
 * 在这里再拼一遍等于把同一份清单抄第二遍，且两边必然会漂移。
 *
 * ⚠️ 折叠的组**子行不进 DOM**（`open ? rows : []`），不是 CSS 隐藏：后者 DOM 里仍有 60 行，
 * 折叠就只是视觉骗局——`querySelectorAll` 数得到、Tab 能走进去、分批哨兵还在算它们。
 *
 * ⚠️ 分组模式下 `groups` 是**全量**的（不是 `visible.slice(0, limit)` 之后切的）：
 * 先切再分组会让「未分类」这类排在最后的组整组消失，且分组头计数变成已加载条数
 * （数字会随滚动往上跳）。故这里是**按组顺序消耗渲染配额**——分组头照常画，
 * 组内只画配额允许的行。
 */
import { useState } from 'react'
import { FolderInput } from 'lucide-react'
import type { DocEntry } from '../../store/types'
import { GroupHeader } from './GroupHeader'
import { SEARCH_LIMIT, type DocGroup } from './useDocQuery'

export interface DocListProps {
  /** null = 平铺（不分组），此时渲染 `rendered` */
  groups: DocGroup[] | null
  /** 平铺模式下已分批的行 */
  rendered: DocEntry[]
  /** 分批渲染深度 —— 分组模式下按组顺序消耗它 */
  limit: number
  collapsedGroups: string[]
  /** 分组头的徽标数字。文件夹组读**全量**计数（见 GroupHeader 文件头），类型/时间组读组内条数 */
  countOf: (group: DocGroup) => number
  /** 该文件夹的增删改进行中 —— 禁用分组头的入口 */
  folderBusy: Set<string>
  docsLoading: boolean
  hasMore: boolean
  /** 列表为空时的提示；null = 不渲染（还有别的行可显示） */
  emptyText: string | null
  /** FTS 结果被截断（取回条数达到上限）——静默截断会让用户以为「就这些」 */
  truncated: boolean
  /** 拖着**已归类**的文档时才需要「移出文件夹」这个落点 */
  draggingCategorized: boolean
  /**
   * **IDE 树形态**（简略模式）：分组子行缩进一层 + 一条缩进参考线，
   * 分组头当父节点看。骨架屏也随之换成单行条（卡片骨架配 26px 行不成比例）。
   *
   * ⚠️ 树形态下子行**刻意紧贴**（不加行间距）：IDE 文件树就是一格挨一格的，
   * 26px 行之间再插 6px 就成了一串卡片，反而看不出父子关系。
   */
  treeIndent?: boolean
  onDropToUncategorized: (e: React.DragEvent) => void
  onToggleGroup: (key: string) => void
  onFolderRename: (id: string, name: string) => Promise<boolean>
  onFolderDelete: (id: string) => void
  onFolderDrop: (folderId: string, e: React.DragEvent) => void
  renderRow: (doc: DocEntry, index: number) => React.ReactNode
  scrollRef: React.RefObject<HTMLDivElement | null>
  sentinelRef: React.RefObject<HTMLDivElement | null>
  onListKeyDown: (e: React.KeyboardEvent) => void
}

export function DocList({
  groups, rendered, limit, collapsedGroups, countOf, folderBusy, docsLoading, hasMore,
  emptyText, truncated, draggingCategorized, treeIndent, onDropToUncategorized, onToggleGroup,
  onFolderRename, onFolderDelete, onFolderDrop, renderRow, scrollRef, sentinelRef, onListKeyDown,
}: DocListProps) {
  const [padHover, setPadHover] = useState(false)

  // ⚠️ 冷启动不能只看「有没有行」：分组模式下 `groups` **永远非空**（按文件夹分组时
  // 至少有一个「未分类」组，哪怕 0 篇），于是「groups 非空就跳过骨架」的旧判据在默认
  // 排列下从来命不中 —— 冷启动一闪而过的是「未分类 0」分组头 + 空列表，正是 #19 要杀的
  // 那种假空态。判据改为「**有内容可显示**」：空组不算内容。
  const hasContent = rendered.length > 0 || (groups ?? []).some(g => g.docs.length > 0)
  const showSkeleton = docsLoading && !hasContent

  // ⚠️ 入场动画的序号是**全局连续**的，不按组重新从 0 数：组内重新计数会让每一组的
  // 前几行同时弹出（3 组就是 3 个「第 0 行」同时出现），看起来像批量闪一下。
  let seq = 0

  const renderGroups = () => {
    let budget = limit
    const out: React.ReactNode[] = []
    for (const g of groups ?? []) {
      const open = !collapsedGroups.includes(g.key)
      // ⚠️ 收起时**一行都不进 `rows`**：这样 `budget` 也不会被一个折叠的组吃掉
      const rows = open ? g.docs.slice(0, Math.max(0, budget)) : []
      budget -= rows.length
      out.push(
        <div key={g.key} data-doc-group={g.key}>
          <GroupHeader
            group={g}
            count={countOf(g)}
            expanded={open}
            busy={folderBusy.has(g.ref)}
            compact={treeIndent}
            onToggle={onToggleGroup}
            onRename={onFolderRename}
            onDelete={onFolderDelete}
            onDropDocs={onFolderDrop}
          />
          {/* IDE 树形态：子行缩进一层，左侧画一条 1px 参考线把「父节点」和它的孩子连起来。
              ⚠️ 线用 `aria-hidden` 纯装饰；缩进靠容器 `pl-4`，不是给每行加 className——
              每行加了的话，行自身的 hover 圆角底会跟着缩进，看起来像「缩进的块」而不是树。 */}
          {rows.length > 0 && (treeIndent ? (
            <div data-tree-indent className="relative pl-4">
              <span aria-hidden className="absolute left-2 top-0.5 bottom-0.5 w-px bg-border-light" />
              {rows.map(d => renderRow(d, seq++))}
            </div>
          ) : rows.map(d => renderRow(d, seq++)))}
        </div>,
      )
    }
    return out
  }

  return (
    <div
      ref={scrollRef}
      data-doc-list
      tabIndex={0}
      onKeyDown={onListKeyDown}
      className="flex-1 overflow-y-auto px-2.5 space-y-1.5 outline-none"
    >
      {/* ⚠️ 加载中不能显示「还没有文档」：那是**一次冷启动的假空态**——
          数据还没回来就已经告诉用户「你没有文档」，比什么都不显示更糟。 */}
      {showSkeleton && (treeIndent ? (
        /* 简略模式：单行条骨架（卡片骨架配 26px 行不成比例，加载完成时会「抖」一下） */
        <div data-list-skeleton className="space-y-1 py-1 pl-4">
          {[72, 54, 80, 46, 64, 58].map((w, i) => (
            <div key={i} className="h-6.5 rounded-md bg-surface-hover animate-pulse" style={{ width: `${w}%` }} />
          ))}
        </div>
      ) : (
        <div data-list-skeleton className="space-y-1.5 py-1">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="rounded-xl border border-transparent py-2.5 px-3 flex items-start gap-2.5 animate-pulse">
              <div className="w-4.5 h-4.5 rounded bg-surface-hover shrink-0 mt-0.5" />
              <div className="w-10 h-10 rounded-lg bg-surface-hover shrink-0" />
              <div className="flex-1 space-y-1.5 pt-0.5">
                <div className="h-3 rounded bg-surface-hover w-3/5" />
                <div className="h-2 rounded bg-surface-hover w-2/5" />
              </div>
            </div>
          ))}
        </div>
      ))}

      {!docsLoading && emptyText && <p className="text-[12px] text-disabled text-center py-8 whitespace-pre-line">{emptyText}</p>}

      {/* 骨架屏期间、以及**真的没有可显示的行**时，都不画分组头：分组模式下 `groups` 永远
          非空（至少一个 0 篇的「未分类」），照画就会得到一个「还没有文档」旁边杵着
          「未分类 0」的画面——空组的头跟假空态是同一类噪音。 */}
      {!showSkeleton && !emptyText && (groups ? renderGroups() : rendered.map(d => renderRow(d, seq++)))}

      {/* 「移出文件夹」放置区 —— 未分类分组头在列表**最后**，拖着文档滚到底部才能放下；
          这里给一个就近的落点。只在拖着已归类的文档时出现（拖未分类的是 no-op）。 */}
      {draggingCategorized && (
        <div
          data-drop-pad
          onDragOver={e => {
            if (!e.dataTransfer.types.includes('application/x-limo-doc')) return
            e.preventDefault()
            e.dataTransfer.dropEffect = 'move'
            setPadHover(true)
          }}
          onDragLeave={e => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) setPadHover(false)
          }}
          onDrop={e => { e.preventDefault(); setPadHover(false); onDropToUncategorized(e) }}
          className={`mt-2 flex items-center justify-center gap-1.5 rounded-xl border border-dashed py-2.5 text-[11px] transition-colors ${
            padHover ? 'border-accent bg-accent/10 text-accent' : 'border-border-default text-disabled'
          }`}
        >
          <FolderInput size={12} />移出文件夹（未分类）
        </div>
      )}

      {hasMore && <div ref={sentinelRef} className="h-6" />}

      {/* 截断提示 —— 只在**取回条数达到上限**时出现。判据见 useDocQuery（`>=` 而非 `>`） */}
      {truncated && (
        <p data-search-truncated className="text-[11px] text-disabled text-center py-2">
          原文匹配结果已截断，仅显示前 {SEARCH_LIMIT} 条 — 请细化关键词
        </p>
      )}
    </div>
  )
}
