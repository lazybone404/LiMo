import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight } from 'lucide-react'

interface MenuItem {
  label: string
  icon?: ReactNode
  /** 有 submenu 时可不传——父项本身不执行动作 */
  onClick?: () => void
  danger?: boolean
  divider?: boolean
  /** 二级菜单（hover 展开）。用于「移动到文件夹 ▸」这类**必须存在的非拖拽路径** */
  submenu?: MenuItem[]
}

interface Props {
  x: number
  y: number
  items: MenuItem[]
  onClose: () => void
}

/**
 * 右键菜单 — Apple 风格，Portal 到 body 避免父元素 transform/filter 干扰定位
 */
export function ContextMenu({ x, y, items, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null)
  const [openSub, setOpenSub] = useState<number | null>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
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

  // 智能翻转：空间不够时菜单出现在鼠标另一侧
  const menuWidth = 180
  const menuHeight = items.length * 36 + 16
  const adjustedX = x + menuWidth > window.innerWidth ? x - menuWidth : x
  const adjustedY = y + menuHeight > window.innerHeight ? y - menuHeight : y

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-[200] bg-elevated/95 backdrop-blur-[20px] rounded-xl shadow-lg border border-border-light py-1 min-w-40 animate-[fadeIn_0.12s_ease-out]"
      style={{ left: adjustedX, top: adjustedY }}
    >
      {items.map((item, i) => {
        const rowClass = `w-full text-left px-3 py-2 text-[13px] transition-colors flex items-center gap-2 ${
          item.danger ? 'text-error hover:bg-error/5' : 'text-foreground hover:bg-surface-hover'
        }`
        if (item.submenu) {
          // 子菜单固定朝右展开；右侧空间不足时翻转到左侧（避免贴边溢出）
          const flip = adjustedX + menuWidth * 2 > window.innerWidth
          return (
            <div key={i} className="relative"
              onMouseEnter={() => setOpenSub(i)}
              onMouseLeave={() => setOpenSub(null)}>
              <button className={rowClass}>
                {item.icon && <span className="text-muted w-4 flex items-center justify-center">{item.icon}</span>}
                <span className="flex-1">{item.label}</span>
                <ChevronRight size={13} className="text-disabled" />
              </button>
              {openSub === i && (
                <div className={`absolute top-0 -mt-1 py-1 min-w-36 max-h-60 overflow-y-auto rounded-xl bg-elevated/95 backdrop-blur-[20px] shadow-lg border border-border-light ${
                  flip ? 'right-full mr-0.5' : 'left-full ml-0.5'
                }`}>
                  {item.submenu.map((sub, j) => (
                    <button
                      key={j}
                      onClick={() => { sub.onClick?.(); onClose() }}
                      className={`w-full text-left px-3 py-1.5 text-[13px] transition-colors truncate ${
                        sub.danger ? 'text-error hover:bg-error/5' : 'text-foreground hover:bg-surface-hover'
                      }`}
                    >
                      {sub.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        }
        return (
          <button key={i} onClick={() => { item.onClick?.(); onClose() }} className={rowClass}>
            {item.icon && <span className="text-muted w-4 flex items-center justify-center">{item.icon}</span>}
            <span>{item.label}</span>
          </button>
        )
      })}
    </div>,
    document.body,
  )
}
