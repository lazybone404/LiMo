/**
 * 文档标签栏 —— **文档之间**的切换（VS Code 双标签模型），位于预览区顶部。
 *
 * ⚠️ 与预览区已有的「内容视图 tab」（笔记 / 整理版原文 / 转录 / 思维导图…）是**两层不同的东西**：
 * 那层由 `lib/tabRegistry.ts` + 全局 `activeView` 驱动，管一篇文档**内部**的视图切换；
 * 这层管**打开哪一篇文档**，真相源是 `currentDocId` + `store/docTabs`。
 * 命名一律 `docTab` / `DocTabBar`，不要和 `activeView` 混。
 *
 * 语义（见 docsSlice 的长注释）：
 * - **单击行** = 打开为**预览标签**（斜体，全组至多一个，会被下一次单击替换）
 * - **双击行** / 右键菜单 = **常驻标签**
 * - 标签组**不持久化**：刷新后只有上次看的那一篇（由 `commitTabChange` 的兜底追加产生）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Pin, X } from 'lucide-react'
import { useStore } from '../../store'
import { ContextMenu, type MenuItem } from '../ui/ContextMenu'
import { IconByName, iconForType, TYPE_COLORS } from '../doc-library/docMeta'

export function DocTabBar() {
  const docTabs = useStore(s => s.docTabs)
  const currentDocId = useStore(s => s.currentDocId)
  const docs = useStore(s => s.docs)
  const dirtyEditingDocId = useStore(s => s.dirtyEditingDocId)
  const openDocTab = useStore(s => s.openDocTab)
  const closeDocTab = useStore(s => s.closeDocTab)
  const closeOtherDocTabs = useStore(s => s.closeOtherDocTabs)
  const closeAllDocTabs = useStore(s => s.closeAllDocTabs)
  const pinDocTab = useStore(s => s.pinDocTab)

  const [menu, setMenu] = useState<{ x: number; y: number; docId: string } | null>(null)
  // 稳定引用：ContextMenu 的 effect 依赖 onClose，每次 render 新建函数会让它反复
  // 拆装 document 上的监听器（打开菜单期间任何 state 变化都会短暂失聪）
  const closeMenu = useCallback(() => setMenu(null), [])

  /** 激活标签自动滚入可视区（标签一多就溢出到横向滚动里了） */
  const activeRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // `block: 'nearest'` 必须有——只给 `inline` 时 block 默认是 'start'，会让整个预览区纵向跳动
    activeRef.current?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
  }, [currentDocId])

  // ⚠️ 标题**现取**而不是存进 tab：重命名立刻生效，也不会有快照过期。
  // 用 Map 而非数组 find：标签栏每帧都要查，文档上千时 O(n×m) 是白烧的。
  const docById = useMemo(() => new Map(docs.map(d => [d.id, d])), [docs])

  const menuTab = menu ? docTabs.find(t => t.docId === menu.docId) : undefined
  const menuItems: MenuItem[] = menu ? [
    { label: '关闭', icon: <X size={14} />, onClick: () => closeDocTab(menu.docId) },
    { label: '关闭其他', onClick: () => closeOtherDocTabs(menu.docId) },
    { label: '关闭全部', onClick: () => closeAllDocTabs() },
    // 「固定」只对预览槽有意义。**不做「取消固定」**——与 VS Code 一致：
    // 只有 Keep Open，没有 Unpin。
    ...(menuTab && !menuTab.pinned
      ? [{ label: '', onClick: () => {}, divider: true },
         { label: '固定为常驻标签', icon: <Pin size={14} />, onClick: () => pinDocTab(menu.docId) }]
      : []),
  ] : []

  // 欢迎页不该出现一条空标签栏
  if (!docTabs.length) return null

  return (
    <>
      <div
        data-doc-tab-bar
        className="flex items-stretch gap-0.5 px-2 pt-1.5 border-b border-border-light shrink-0 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {docTabs.map(t => {
          const doc = docById.get(t.docId)
          const active = t.docId === currentDocId
          const dirty = dirtyEditingDocId === t.docId
          const colors = TYPE_COLORS[doc?.sourceType || ''] || TYPE_COLORS.text
          const title = doc?.title || '已删除的文档'
          return (
            <div
              key={t.docId}
              ref={active ? activeRef : undefined}
              data-doc-tab={t.docId}
              data-active={active ? 'true' : undefined}
              data-preview={t.pinned ? undefined : 'true'}
              role="button"
              tabIndex={0}
              title={title}
              // 点标签 = 切过去（若它已是常驻，`openDocTab` 的「已在组内」分支只做 selectDoc，
              // 不会把它降级成预览槽）。走 `openDocTab` 而非裸 `selectDoc` 是为了同时清掉
              // 在途的 pending 意图 —— 用户的这次点击已经取代了它们。
              onClick={() => openDocTab(t.docId, 'preview')}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDocTab(t.docId, 'preview') }
              }}
              // 中键关闭（浏览器/IDE 通用惯例）。⚠️ `preventDefault` 不能省 ——
              // 否则 WebView2 会进入「中键自动滚动」模式。
              onAuxClick={e => {
                if (e.button === 1) { e.preventDefault(); closeDocTab(t.docId) }
              }}
              onContextMenu={e => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, docId: t.docId }) }}
              className={[
                'group/tab relative flex items-center gap-1.5 px-3 h-8 rounded-t-md',
                'text-[12px] max-w-[200px] min-w-[90px] cursor-pointer select-none transition-colors',
                active
                  ? 'bg-elevated text-foreground shadow-sm'
                  : 'text-dimmed hover:bg-surface-hover hover:text-foreground',
              ].join(' ')}
            >
              {active && (
                <span aria-hidden className="absolute top-0 left-2 right-2 h-0.5 rounded-full bg-accent" />
              )}
              {/* 类型图标与文档库同源（docMeta 的 iconForType + TYPE_COLORS），
                  保证同一篇文档在列表行与标签上是同一个视觉身份 */}
              <span className="shrink-0 flex items-center" style={{ color: colors.text }}>
                <IconByName name={iconForType(doc?.sourceType || '')} size={12} />
              </span>
              {/* 预览标签用**斜体**标记（VS Code 同款）：一眼看出哪个会被下一次单击顶掉 */}
              <span className={`flex-1 min-w-0 truncate ${t.pinned ? '' : 'italic'}`}>{title}</span>
              {/* ⚠️ 不能只写 `opacity-0` —— 那是「看不见却仍可点」（DocRow 的既有教训），
                  必须配 `pointer-events-none`，hover / 键盘聚焦时才恢复。
                  有未保存编辑时默认显示**圆点**、hover 才变 X：误关会丢内容。 */}
              <button
                data-tab-close
                aria-label={`关闭 ${title}`}
                onClick={e => { e.stopPropagation(); closeDocTab(t.docId) }}
                className={`shrink-0 w-4 h-4 rounded flex items-center justify-center hover:bg-surface-hover transition-opacity ${
                  dirty
                    ? ''
                    : 'opacity-0 pointer-events-none group-hover/tab:opacity-100 group-hover/tab:pointer-events-auto focus-visible:opacity-100 focus-visible:pointer-events-auto'
                }`}
              >
                {dirty && <span className="w-1.5 h-1.5 rounded-full bg-accent group-hover/tab:hidden" />}
                <X size={11} className={dirty ? 'hidden group-hover/tab:block' : ''} />
              </button>
            </div>
          )
        })}
      </div>
      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems} onClose={closeMenu} />}
    </>
  )
}
