/**
 * 插槽注册表 — 面板自包含声明，替代 App.tsx 硬编码的面板。
 *
 * **新增一个面板 = 在这里注册一段**（连同组件本体）。左侧活动栏的图标也从
 * `grouped().left` 派生，所以注册完就自动有了入口。
 *
 * ⚠️ 这正是 2026-10-10 补上的那一半：此前组件本体硬编码在 `SlotLayout` 的
 * `COMPONENTS` 映射里，「注册一个 SlotDef」只完成了元数据，加面板实际要改两处。
 *
 * 设计约束：
 * - `side` 驱动方向感知 resize（left 面板 dx 取正，right 取反）与活动栏归属
 * - ⚠️ **本模块持有页面组件**，故 `store/` 侧（`uiSlice` / `panelToggle`）**不得 import 它**
 *   —— 组件 import store，反向引用即成环（判据的原始形态见 `slotMetrics.ts` 的头注释）。
 *   store 需要的尺寸与开合判据走零依赖的 `slotMetrics` / `panelToggle`，它们只认
 *   `SlotState`，不认 `SlotDef`。
 * - 只需**类型**的消费方请写 `import type { SlotDef }`，别把三个页面组件拖进无关模块
 * - TOC 暂不走 slot 系统，仍在 PreviewArea 内部自管
 */

import type { FC } from 'react'
import { PenLine, Library } from 'lucide-react'
import { PANEL_DEFAULT_WIDTH, PANEL_MIN_WIDTH, PANEL_MAX_WIDTH } from './slotMetrics'
import { GenPanel } from '../components/gen-panel/GenPanel'
import { DocLibrary } from '../components/doc-library/DocLibrary'
import { PreviewArea } from '../components/preview/PreviewArea'

// ── 类型 ──

export type SlotSide = 'left' | 'center' | 'right'

export interface SlotDef {
  /** 唯一标识，对应 store.slots[id] */
  id: string
  /** 面板所在侧 */
  side: SlotSide
  /**
   * 面板组件本体（无 props —— 面板自己从 store 取需要的一切）。
   *
   * ⚠️ **必填**，不是可选：`SlotLayout` 直接渲染它，注册表里漏写组件的表现是
   * 「面板注册了但渲染不出来」，那种失败没有报错、只在界面上少一块。
   */
  component: FC
  /** PanelStrip 折叠态标签 */
  label: string
  /** PanelStrip 图标 */
  icon: FC<{ size?: number; className?: string }>
  /** 展开时的默认宽度 px */
  defaultWidth: number
  /** 最小宽度 px（小于 80 自动折叠） */
  minWidth: number
  /** 最大宽度 px */
  maxWidth: number
  /** 是否可关闭（preview 区不可关闭） */
  closable?: boolean
  /** store 中控制可见性的字段路径。留空 = 始终可见 */
  visibleKey?: 'genPanel'
}

// ── 注册表 ──

class SlotRegistryImpl {
  private _slots: SlotDef[] = []

  register(slot: SlotDef): void {
    const idx = this._slots.findIndex(s => s.id === slot.id)
    if (idx >= 0) {
      this._slots[idx] = slot
    } else {
      this._slots.push(slot)
    }
  }

  get(id: string): SlotDef | undefined {
    return this._slots.find(s => s.id === id)
  }

  /** 按 side 分组返回：{ left: [...], center: [...], right: [...] } */
  grouped(): Record<SlotSide, SlotDef[]> {
    const result: Record<SlotSide, SlotDef[]> = { left: [], center: [], right: [] }
    for (const s of this._slots) {
      result[s.side].push(s)
    }
    return result
  }

  all(): readonly SlotDef[] {
    return this._slots
  }
}

export const SlotRegistry = new SlotRegistryImpl()

// ═══════════════════════════════════════════
// 注册内置面板（left 侧的顺序 = 活动栏图标的顺序）
// ═══════════════════════════════════════════

SlotRegistry.register({
  id: 'genPanel',
  side: 'left',
  component: GenPanel,
  label: '生成',
  icon: PenLine,
  defaultWidth: PANEL_DEFAULT_WIDTH,
  minWidth: PANEL_MIN_WIDTH,
  maxWidth: PANEL_MAX_WIDTH,
  closable: true,
  visibleKey: 'genPanel',
})

SlotRegistry.register({
  id: 'docLib',
  side: 'left',
  component: DocLibrary,
  label: '文档库',
  icon: Library,
  defaultWidth: PANEL_DEFAULT_WIDTH,
  minWidth: PANEL_MIN_WIDTH,
  maxWidth: PANEL_MAX_WIDTH,
})

SlotRegistry.register({
  id: 'preview',
  side: 'center',
  component: PreviewArea,
  label: '预览',
  icon: PenLine,
  defaultWidth: 0,
  minWidth: 200,
  maxWidth: Infinity,
})
