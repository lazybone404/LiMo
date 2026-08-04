/**
 * FloatingAI — AI 对话浮窗（2026-08-04 起为唯一形态，插槽面板已移除）。
 *
 * 优化：位置/尺寸持久化（uiSlice aiPos/aiSize → localStorage）、拖拽边界钳制
 * （标题栏不脱屏）、resize 不超视口、会话列表默认折叠（浮窗优先对话内容）。
 */
import { useState, useCallback } from 'react'
import { useStore } from '../../store'
import { ChatCore } from './ChatCore'
import { X } from 'lucide-react'

export function FloatingAI() {
  const toggleAI = useStore(s => s.toggleAI)
  const pos = useStore(s => s.aiPos)
  const size = useStore(s => s.aiSize)
  const setPos = useStore(s => s.setAiPos)
  const setSize = useStore(s => s.setAiSize)

  // 会话列表默认折叠 — 浮窗优先展示对话内容，需要时点窄条展开
  const [listCollapsed, setListCollapsed] = useState(true)

  const W = size.width
  const listWidth = listCollapsed ? 0 : Math.max(80, W * 0.3)
  const showNav = !listCollapsed && W > 400
  const navWidth = showNav ? Math.max(80, W * 0.18) : 0
  const compact = listWidth < 80
  const scale = W < 420 ? 'sm' : W < 700 ? 'md' : 'lg' as 'sm' | 'md' | 'lg'

  // 标题栏拖拽 + 边界钳制：标题栏（顶部 40px）始终留在屏幕内，防拖丢
  const handleTitleMouseDown = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName === 'BUTTON' || (e.target as HTMLElement).closest('button')) return
    const sx = e.clientX - pos.x; const sy = e.clientY - pos.y
    const w = size.width
    document.body.style.cursor = 'move'; document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => {
      const nx = Math.min(Math.max(ev.clientX - sx, -w + 80), window.innerWidth - 80)
      const ny = Math.min(Math.max(ev.clientY - sy, 0), window.innerHeight - 40)
      setPos({ x: nx, y: ny })
    }
    const onUp = () => { document.body.style.cursor = ''; document.body.style.userSelect = ''; document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
  }, [pos.x, pos.y, size.width, setPos])

  // 右下角 resize + 边界：不超视口（以浮窗左上角为锚）
  const handleResizeDown = (e: React.MouseEvent) => {
    e.stopPropagation()
    const sx = e.clientX; const sy = e.clientY; const sw = size.width; const sh = size.height
    const onMove = (ev: MouseEvent) => {
      const w = Math.max(280, Math.min(sw + ev.clientX - sx, window.innerWidth - pos.x))
      const h = Math.max(200, Math.min(sh + ev.clientY - sy, window.innerHeight - pos.y))
      setSize({ width: w, height: h })
    }
    const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
  }

  return (
    <aside className="fixed z-[100] flex flex-col overflow-hidden rounded-2xl border border-border-default bg-elevated/95 shadow-2xl backdrop-blur-[20px] animate-[fadeIn_0.2s_ease-out]"
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
          <button onClick={toggleAI} className="p-0.5 rounded-md hover:bg-surface-hover text-muted" title="关闭"><X size={13} /></button>
        }
      />

      {/* Resize 手柄 */}
      <div className="absolute right-1 bottom-1 w-5 h-5 cursor-se-resize z-10"
        style={{ background: 'linear-gradient(135deg, transparent 50%, var(--color-border-default) 50%, transparent 60%)', borderRadius: '0 0 12px 0' }}
        onMouseDown={handleResizeDown}
      />
    </aside>
  )
}
