import type { LucideIcon } from 'lucide-react'

interface Props {
  label: string
  side: 'left' | 'right'
  icon: LucideIcon
  onClick: () => void
}

/**
 * 面板折叠后的窄条指示器 — 对标 BiliNote 面板展开按钮
 * 面板折叠后出现在原位置，点击可展开，配有描述性图标
 */
export function PanelStrip({ label, side: _side, icon: Icon, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className="flex flex-col items-center justify-center gap-1.5 shrink-0 bg-elevated/40 hover:bg-elevated/60 transition-colors cursor-pointer border-r border-border-light group"
      style={{ width: 34 }}
      title={`展开${label}`}
    >
      <Icon size={16} className="text-muted group-hover:text-accent transition-colors" />
      <span
        className="text-[9px] text-disabled group-hover:text-dimmed select-none transition-colors"
        style={{ writingMode: 'vertical-rl' }}
      >
        {label}
      </span>
    </div>
  )
}
