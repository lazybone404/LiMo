/**
 * 通用空态 / 引导页 — props 驱动，无 store 依赖。
 * 替代 PreviewArea 中 8 处重复的内联空态 JSX。
 *
 * title/description/hint 均为 ReactNode：需要特殊视觉（h2 大标题、双段说明）时
 * 由调用方传自定制节点；普通空态直接传 string 即可。
 */
import type { ReactNode } from 'react'

export interface EmptyStateProps {
  icon?: ReactNode
  title?: ReactNode
  description?: ReactNode
  /** 次级小字（如引导页的双段说明） */
  hint?: ReactNode
  /** 操作按钮区 */
  action?: ReactNode
}

export function EmptyState({ icon, title, description, hint, action }: EmptyStateProps) {
  return (
    <div className="mx-auto px-10 py-16 text-center">
      {icon && <div className="mb-6">{icon}</div>}
      {title && <div className="mb-3">{title}</div>}
      {description && <p className="text-[13px] text-disabled mb-3">{description}</p>}
      {hint && <p className="text-[11px] text-disabled mb-6 max-w-md mx-auto">{hint}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}
