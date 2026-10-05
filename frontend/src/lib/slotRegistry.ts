/**
 * 插槽注册表 — 面板自包含声明，替代 App.tsx 硬编码的 4 个面板。
 *
 * Phase 2 核心：新增面板只需在此注册一个 SlotDef，App.tsx 零改动。
 *
 * 设计约束：
 * - 纯数据，不 import React 组件（组件在 SlotLayout 中通过动态查找渲染）
 * - side 字段驱动方向感知 resize（left 面板 dx 取正，right 面板 dx 取反）
 * - TOC 暂不走 slot 系统，仍在 PreviewArea 内部自管
 */

import type { FC } from 'react'
import { PenLine, Library } from 'lucide-react'
import { PANEL_DEFAULT_WIDTH, PANEL_MIN_WIDTH, PANEL_MAX_WIDTH } from './slotMetrics'

// ── 类型 ──

export type SlotSide = 'left' | 'center' | 'right'

export interface SlotDef {
  /** 唯一标识，对应 store.slots[id] */
  id: string
  /** 面板所在侧 */
  side: SlotSide
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
// 注册 4 个内置面板
// ═══════════════════════════════════════════

SlotRegistry.register({
  id: 'genPanel',
  side: 'left',
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
  label: '文档库',
  icon: Library,
  defaultWidth: PANEL_DEFAULT_WIDTH,
  minWidth: PANEL_MIN_WIDTH,
  maxWidth: PANEL_MAX_WIDTH,
})

SlotRegistry.register({
  id: 'preview',
  side: 'center',
  label: '预览',
  icon: PenLine,
  defaultWidth: 0,
  minWidth: 200,
  maxWidth: Infinity,
})
