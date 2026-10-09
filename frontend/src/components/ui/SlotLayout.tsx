/**
 * 插槽布局引擎 — 声明式面板布局，替代 App.tsx 手动放置的面板。
 */

import { useCallback } from 'react'
import { useStore } from '../../store'
import { SlotRegistry } from '../../lib/slotRegistry'
import { isPanelOpen } from '../../lib/panelToggle'
import { ResizeHandle } from './ResizeHandle'
import { PanelStrip } from './PanelStrip'

/**
 * 将注册的面板展开为扁平的可见项列表，每项标记侧边和是否需要 resize handle。
 *
 * 布局顺序: [left panels] → center → [right panels]
 * 每对相邻的可见+未折叠面板之间插入 ResizeHandle。
 *
 * **组件本体来自 `def.component`**（注册在 `slotRegistry`），本文件不再持有映射表
 * —— 加一个面板只需在注册表写一段。
 */
export function SlotLayout({ hideSlots }: { hideSlots?: string[] }) {
  const slots = useStore(s => s.slots)
  const setSlot = useStore(s => s.setSlot)
  const exclusive = useStore(s => s.layout.leftPanelsExclusive)
  const activeLeftPanel = useStore(s => s.activeLeftPanel)

  // 按侧分组 + 展开为扁平列表
  const grouped = SlotRegistry.grouped()
  const allDefs = [...grouped.left, ...grouped.center, ...grouped.right]

  // 过滤出"占用空间"的面板（可见且未折叠，或可见且关闭）
  const hidden = new Set(hideSlots || [])
  const items = allDefs.map(def => {
    const s = slots[def.id]
    if (!s || hidden.has(def.id)) return null
    if (exclusive && def.side === 'left') {
      // ⚠️ **互斥模式下左侧谁在布局里，由 `activeLeftPanel` 这个选择器决定**，
      // 判据与 `uiSlice` 同源（`isPanelOpen`）—— 不能在这里另写一份「两个都可见就
      // 都渲染」的逻辑，那正是「打开甲时遍历把乙收起」被绕过之后的同一个洞。
      // 未激活的左侧面板**整项不出现**（连 PanelStrip 都不给：一次只该有一个面板，
      // 34px 窄条在这个模式下没有意义）。center/right 侧不受互斥影响。
      if (!isPanelOpen(s, true, activeLeftPanel, def.id)) return null
    } else {
      const visible = def.closable ? (def.visibleKey ? s.visible : true) : true
      if (!visible) return null
    }
    return { def, state: s }
  }).filter(Boolean) as Array<{ def: typeof allDefs[number]; state: typeof slots[string] }>

  // 方向感知 resize（right 侧 dx 取反），限制取自 SlotDef
  const makeResize = useCallback((def: typeof allDefs[number]) => {
    return (dx: number) => {
      const st = useStore.getState()
      const s = st.slots[def.id]
      if (!s || s.collapsed) return
      const adjusted = def.side === 'right' ? -dx : dx
      const w = Math.max(def.minWidth, Math.min(def.maxWidth, s.width + adjusted))
      st.setSlot(def.id, { width: w })
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
            onResize={makeResize(def)}
            onExpand={() => setSlot(def.id, { collapsed: false, width: def.defaultWidth })}
          />
        )
      })}
    </>
  )
}

/**
 * 单个面板 + 可选的 ResizeHandle。
 *
 * ⚠️ 折叠 = **卸载组件**（`collapsed ? <PanelStrip/> : <Comp/>`），不是 CSS 隐藏。
 * 这条契约要求面板里「重开后不该重置」的状态一律离开组件本地 state——文档库的选择集、
 * 分批深度、文件夹常驻禁用态、筛选条件等都是因此迁进 store 的（2026-09-25）。
 *
 * **为什么刻意不把这里改成 CSS 隐藏**（一处改动就能保住所有面板的本地 state）：那些状态
 * 的需求不止「折叠后别丢」，还要跨组件共享（工具栏 / 批量条 / 每一行都要读选择集），
 * 还要与筛选 setter **原子发生修改**（改筛选时清空选择必须同一个 `set` 内完成，否则
 * 会露出「筛选已变、选择还是旧的」的中间帧）。纯 CSS 隐藏解决不了这些。
 */

function PanelItem({ def, collapsed, width, needsResize, onResize, onExpand }: {
  def: typeof SlotRegistry extends { all(): (infer T)[] } ? T : any
  collapsed: boolean; width: number; needsResize: boolean
  onResize: (dx: number) => void; onExpand: () => void
}) {
  const Comp = def.component
  const isCenter = def.side === 'center'

  if (isCenter) {
    return (
      <>
        <div data-panel={def.id} className="flex-1 flex overflow-hidden min-w-[200px]">
          <Comp />
        </div>
        {needsResize && <ResizeHandle onResize={onResize} />}
      </>
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
        // `data-panel` 是测试钩子：「哪个面板真的进了布局」只能这样问 —— 互斥模式的
        // 核心承诺（同一时刻只有一个左侧面板在 DOM 里）必须**断在渲染结果上**，
        // 只断 `activeLeftPanel` 的值的话，布局层没用它就是绿的。
        <div data-panel={def.id} style={{ width, minWidth: def.minWidth, maxWidth: def.maxWidth }} className="shrink-0">
          <Comp />
        </div>
      )}
      {needsResize && <ResizeHandle onResize={onResize} />}
    </>
  )
}
