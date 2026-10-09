/**
 * 活动栏 — 常驻最左的竖排图标列（对标 VS Code activity bar）。
 *
 * **面板项从 `SlotRegistry.grouped().left` 派生**，不硬编码 —— 在注册表里加一个
 * `side: 'left'` 的面板，这里自动多一个图标，`App.tsx` 零改动。底部的动作项
 * （AI 对话 / 设置）是**动作而非面板**（它们不是 slot，AI 是可拖浮窗、设置是 overlay），
 * 故单独成组、不进注册表。
 *
 * ⚠️ 左侧面板的激活判据走 `isPanelOpen`（与 `uiSlice` 同源），**不在这里另写一份**
 * 「slot 可见就是激活」—— 互斥模式下那不是激活，只是碰巧没被收起。
 */

import type { FC } from 'react'
import { Settings, MessageSquare } from 'lucide-react'
import { useStore } from '../../store'
import { SlotRegistry } from '../../lib/slotRegistry'
import { isPanelOpen } from '../../lib/panelToggle'

interface Props {
  /**
   * 打开设置。**刻意留在 `App.tsx` 的局部 state 里**（以 props 传入，不进 store）——
   * 设置是 overlay 不是 slot，且只有这一对父子在用，全局化是 over-reach。
   */
  onOpenSettings: () => void
}

export function ActivityBar({ onOpenSettings }: Props) {
  const slots = useStore(s => s.slots)
  const exclusive = useStore(s => s.layout.leftPanelsExclusive)
  const activeLeftPanel = useStore(s => s.activeLeftPanel)
  const togglePanel = useStore(s => s.togglePanel)
  const aiVisible = useStore(s => s.aiVisible)
  const toggleAI = useStore(s => s.toggleAI)

  // 顺序即注册顺序（`register` 按 push 追加）
  const leftDefs = SlotRegistry.grouped().left

  return (
    <nav
      aria-label="活动栏"
      className="flex flex-col items-center shrink-0 w-12 bg-surface border-r border-border-light"
    >
      <div className="flex flex-col items-center gap-0.5 py-1.5">
        {leftDefs.map(def => {
          const st = slots[def.id]
          // 面板可能未在 store 里建 slot（注册了但没建状态）——按未激活处理，不做兜底状态对象
          const open = !!st && isPanelOpen(st, exclusive, activeLeftPanel, def.id)
          return (
            <ActivityItem
              key={def.id}
              id={def.id}
              icon={def.icon}
              label={def.label}
              active={open}
              onClick={() => togglePanel(def.id)}
            />
          )
        })}
      </div>

      <div className="flex-1" />

      <div className="flex flex-col items-center gap-0.5 py-1.5">
        <ActivityItem id="ai" icon={MessageSquare} label="AI 对话" active={aiVisible} onClick={toggleAI} />
        {/* `active` 不传 = 动作按钮（不做 aria-pressed 的切换语义声明） */}
        <ActivityItem id="settings" icon={Settings} label="设置" onClick={onOpenSettings} />
      </div>
    </nav>
  )
}

/**
 * 单个图标按钮。
 *
 * `data-activity-item` 供测试断言「图标集 == 注册表的派生结果」（加注册项 → 图标集随之变），
 * 这样测试不必硬编码图标个数 —— `SlotRegistry` 是可变单例且没有 `unregister`。
 */
function ActivityItem({ id, icon: Icon, label, active, onClick }: {
  id: string
  icon: FC<{ size?: number; className?: string }>
  label: string
  /** 省略 = 动作项（无切换语义）；`true/false` = 切换项，会写 `aria-pressed` */
  active?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      data-activity-item={id}
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={`relative flex items-center justify-center w-12 h-11 transition-colors ${
        active ? 'text-accent' : 'text-muted hover:text-foreground hover:bg-surface-hover'
      }`}
    >
      {active && (
        <span
          aria-hidden="true"
          className="absolute left-0 top-1/2 -translate-y-1/2 h-6 w-[2px] rounded-r bg-accent"
        />
      )}
      <Icon size={19} />
    </button>
  )
}
