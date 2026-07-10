import { useEffect, useRef, type ReactNode } from 'react'

interface MenuItem {
  label: string
  icon?: ReactNode
  onClick: () => void
  danger?: boolean
  divider?: boolean
}

interface Props {
  x: number
  y: number
  items: MenuItem[]
  onClose: () => void
}

/**
 * 右键菜单 — Apple 风格
 * 参照 components.md §12.2
 */
export function ContextMenu({ x, y, items, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    // 延迟绑定避免立即触发
    const timer = setTimeout(() => {
      document.addEventListener('click', handler)
      document.addEventListener('contextmenu', handler)
    }, 0)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('click', handler)
      document.removeEventListener('contextmenu', handler)
    }
  }, [onClose])

  // 智能翻转：空间不够时菜单出现在鼠标另一侧，始终贴近光标
  const menuWidth = 180
  const menuHeight = items.length * 36 + 16
  const adjustedX = x + menuWidth > window.innerWidth ? x - menuWidth : x
  const adjustedY = y + menuHeight > window.innerHeight ? y - menuHeight : y

  return (
    <div
      ref={menuRef}
      className="fixed z-[200] bg-elevated/95 backdrop-blur-[20px] rounded-xl shadow-lg border border-border-light py-1 min-w-40 animate-[fadeIn_0.12s_ease-out]"
      style={{ left: adjustedX, top: adjustedY }}
    >
      {items.map((item, i) => (
        <button
          key={i}
          onClick={() => { item.onClick(); onClose() }}
          className={`w-full text-left px-3 py-2 text-[13px] transition-colors flex items-center gap-2 ${
            item.danger
              ? 'text-error hover:bg-error/5'
              : 'text-foreground hover:bg-surface-hover'
          }`}
        >
          {item.icon && <span className="text-muted w-4 flex items-center justify-center">{item.icon}</span>}
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  )
}
