import { useState, useCallback } from 'react'
import { useStore } from '../../store'
import { ChatCore } from './ChatCore'
import { X, Minus } from 'lucide-react'

export function FloatingAI() {
  const toggleAI = useStore(s => s.toggleAI)
  const setAiFloating = useStore(s => s.setAiFloating)
  const pos = useStore(s => s.aiFloatingPos)
  const size = useStore(s => s.aiFloatingSize)
  const setPos = useStore(s => s.setAiFloatingPos)
  const setSize = useStore(s => s.setAiFloatingSize)

  const [listCollapsed, setListCollapsed] = useState(false)

  const W = size.width
  const listWidth = listCollapsed ? 0 : Math.max(80, W * 0.3)
  const showNav = !listCollapsed && W > 400
  const navWidth = showNav ? Math.max(80, W * 0.18) : 0
  const compact = listWidth < 80
  const scale = W < 420 ? 'sm' : W < 700 ? 'md' : 'lg' as 'sm' | 'md' | 'lg'

  // 标题栏拖拽
  const handleTitleMouseDown = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName === 'BUTTON' || (e.target as HTMLElement).closest('button')) return
    const sx = e.clientX - pos.x; const sy = e.clientY - pos.y
    document.body.style.cursor = 'move'; document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => { setPos({ x: ev.clientX - sx, y: ev.clientY - sy }) }
    const onUp = () => { document.body.style.cursor = ''; document.body.style.userSelect = ''; document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
  }, [pos.x, pos.y, setPos])

  return (
    <aside className="fixed rounded-2xl shadow-2xl border border-border-default bg-elevated/95 backdrop-blur-[20px] flex flex-col overflow-hidden z-[100] animate-[fadeIn_0.2s_ease-out]"
      style={{ left: pos.x, top: pos.y, width: size.width, height: size.height, minWidth: 360, minHeight: 300 }}>
      <ChatCore
        compact={compact}
        listWidth={listWidth}
        navWidth={navWidth}
        showNav={showNav}
        listCollapsed={listCollapsed}
        onListCollapsedChange={setListCollapsed}
        scale={scale}
        onTitleMouseDown={handleTitleMouseDown}
        titleRight={
          <>
            <button onClick={() => setAiFloating(false)} className="p-0.5 rounded-md hover:bg-surface-hover text-muted" title="停靠"><Minus size={13} /></button>
            <button onClick={toggleAI} className="p-0.5 rounded-md hover:bg-surface-hover text-muted"><X size={13} /></button>
          </>
        }
      />

      {/* Resize 手柄 */}
      <div className="absolute right-1 bottom-1 w-5 h-5 cursor-se-resize z-10"
        style={{ background: 'linear-gradient(135deg, transparent 50%, var(--color-border-default) 50%, transparent 60%)', borderRadius: '0 0 12px 0' }}
        onMouseDown={(e) => {
          e.stopPropagation()
          const sx = e.clientX; const sy = e.clientY; const sw = size.width; const sh = size.height
          const onMove = (ev: MouseEvent) => {
            setSize({ width: Math.max(280, sw + ev.clientX - sx), height: Math.max(200, sh + ev.clientY - sy) })
          }
          const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }
          document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
        }}
      />
    </aside>
  )
}
