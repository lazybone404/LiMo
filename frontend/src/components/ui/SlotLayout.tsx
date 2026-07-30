/**
 * 插槽布局引擎 — 声明式面板布局，替代 App.tsx 手动放置的面板。
 */

import { useCallback } from 'react'
import { useStore } from '../../store'
import { SlotRegistry } from '../../lib/slotRegistry'
import { ResizeHandle } from './ResizeHandle'
import { PanelStrip } from './PanelStrip'
import { GenPanel } from '../gen-panel/GenPanel'
import { DocLibrary } from '../doc-library/DocLibrary'
import { PreviewArea } from '../preview/PreviewArea'
import { AIPanel } from '../ai-chat/AIPanel'

const COMPONENTS: Record<string, React.FC> = {
  genPanel: GenPanel, docLib: DocLibrary, preview: PreviewArea, aiPanel: AIPanel,
}

/**
 * 将注册的面板展开为扁平的可见项列表，每项标记侧边和是否需要 resize handle。
 *
 * 布局顺序: [left panels] → center → [right panels]
 * 每对相邻的可见+未折叠面板之间插入 ResizeHandle。
 */
export function SlotLayout({ hideSlots }: { hideSlots?: string[] }) {
  const slots = useStore(s => s.slots)
  const setSlot = useStore(s => s.setSlot)

  // 按侧分组 + 展开为扁平列表
  const grouped = SlotRegistry.grouped()
  const allDefs = [...grouped.left, ...grouped.center, ...grouped.right]

  // 过滤出"占用空间"的面板（可见且未折叠，或可见且关闭）
  const hidden = new Set(hideSlots || [])
  const items = allDefs.map(def => {
    const s = slots[def.id]
    if (!s || hidden.has(def.id)) return null
    const visible = def.closable ? (def.visibleKey ? s.visible : true) : true
    if (!visible) return null
    return { def, state: s }
  }).filter(Boolean) as Array<{ def: typeof allDefs[number]; state: typeof slots[string] }>

  // 方向感知 resize（right 侧 dx 取反）
  const makeResize = useCallback((slotId: string, side: string) => {
    return (dx: number) => {
      const st = useStore.getState()
      const s = st.slots[slotId]
      if (!s || s.collapsed) return
      const adjusted = side === 'right' ? -dx : dx
      const w = Math.max(50, Math.min(600, s.width + adjusted))
      st.setSlot(slotId, { width: w })
    }
  }, [])

  return (
    <>
      {items.map(({ def, state }, i) => {
        const needsResize = i < items.length - 1 && !state.collapsed

        return (
          <PanelItem key={def.id}
            def={def}
            collapsed={state.collapsed}
            width={state.width}
            needsResize={needsResize}
            onResize={makeResize(def.id, def.side)}
            onExpand={() => setSlot(def.id, { collapsed: false, width: def.defaultWidth })}
            onCollapse={() => setSlot(def.id, { collapsed: true })}
          />
        )
      })}
    </>
  )
}

/** 单个面板 + 可选的 ResizeHandle */
function PanelItem({ def, collapsed, width, needsResize, onResize, onExpand }: {
  def: typeof SlotRegistry extends { all(): (infer T)[] } ? T : any
  collapsed: boolean; width: number; needsResize: boolean
  onResize: (dx: number) => void; onExpand: () => void; onCollapse?: () => void
}) {
  const Comp = COMPONENTS[def.id]
  if (!Comp) return null
  const isCenter = def.side === 'center'

  // 中心面板：始终可见，不折叠
  if (isCenter) {
    return (
      <div className="flex-1 flex overflow-hidden min-w-[200px]">
        <Comp />
      </div>
    )
  }

  return (
    <>
      {collapsed ? (
        <PanelStrip
          label={def.label}
          side={def.side === 'right' ? 'right' : 'left'}
          icon={def.icon}
          onClick={onExpand}
        />
      ) : (
        <div style={{ width, minWidth: 80, maxWidth: 600 }} className="shrink-0">
          <Comp />
        </div>
      )}
      {needsResize && <ResizeHandle onResize={onResize} />}
    </>
  )
}
