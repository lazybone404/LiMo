/**
 * FloatingAI — AI 对话浮窗（2026-08-04 起为唯一形态，插槽面板已移除）。
 *
 * 优化：位置/尺寸持久化（uiSlice aiPos/aiSize → localStorage）、拖拽边界钳制
 * （标题栏不脱屏）、resize 不超视口、会话列表默认折叠（浮窗优先对话内容）。
 *
 * 内部栏（会话列表 / 导航侧栏）可拖拽调宽 + 直接折叠：宽度/折叠状态在此受控，
 * ChatCore 用 ResizeHandle 驱动 onResizeList/onResizeNav，拖到 <80 自动折叠。
 */
import { useState, useCallback } from 'react'
import { useStore } from '../../store'
import { ChatCore } from './ChatCore'
import { X } from 'lucide-react'
import type { ChatScale } from './scale'

export function FloatingAI() {
  const toggleAI = useStore(s => s.toggleAI)
  const pos = useStore(s => s.aiPos)
  const size = useStore(s => s.aiSize)
  const setPos = useStore(s => s.setAiPos)
  const setSize = useStore(s => s.setAiSize)

  const W = size.width
  const scale: ChatScale = W < 420 ? 'sm' : W < 700 ? 'md' : 'lg'

  // ── 会话列表：默认折叠，宽度可拖拽（范围 80 ~ 60% 容器宽）──
  const [listCollapsed, setListCollapsed] = useState(true)
  const [listWidth, setListWidth] = useState(() => Math.round(W * 0.3))
  // ── 导航侧栏：默认折叠，宽度可拖拽 ──
  const [navCollapsed, setNavCollapsed] = useState(true)
  const [navWidth, setNavWidth] = useState(() => Math.round(W * 0.18))

  const showNav = !listCollapsed && W > 400

  // 会话列表拖拽：右拖变宽；<80 自动折叠（重置为默认宽，展开时恢复）
  const onResizeList = useCallback((dx: number) => {
    setListWidth(prev => {
      const w = prev + dx
      if (w < 80) { setListCollapsed(true); return Math.round(size.width * 0.3) }
      return Math.min(w, Math.round(size.width * 0.6))
    })
  }, [size.width])
  const onToggleList = () => {
    if (listCollapsed) { setListCollapsed(false); setListWidth(Math.round(size.width * 0.3)) }
    else setListCollapsed(true)
  }
  const onExpandList = () => { setListCollapsed(false); setListWidth(Math.round(size.width * 0.3)) }

  // 导航拖拽：右拖变窄（导航在右）；<80 自动折叠
  const onResizeNav = useCallback((dx: number) => {
    setNavWidth(prev => {
      const w = prev - dx
      if (w < 80) { setNavCollapsed(true); return Math.round(size.width * 0.18) }
      return Math.min(w, Math.round(size.width * 0.3))
    })
  }, [size.width])
  const onToggleNav = () => {
    if (navCollapsed) { setNavCollapsed(false); setNavWidth(Math.round(size.width * 0.18)) }
    else setNavCollapsed(true)
  }
  const onExpandNav = () => { setNavCollapsed(false); setNavWidth(Math.round(size.width * 0.18)) }

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
        scale={scale}
        onTitleMouseDown={handleTitleMouseDown}
        titleRight={<button onClick={toggleAI} className="p-0.5 rounded-md hover:bg-surface-hover text-muted" title="关闭"><X size={13} /></button>}
        listCollapsed={listCollapsed}
        listWidth={listWidth}
        onToggleList={onToggleList}
        onResizeList={onResizeList}
        onExpandList={onExpandList}
        showNav={showNav}
        navCollapsed={navCollapsed}
        navWidth={navWidth}
        onToggleNav={onToggleNav}
        onResizeNav={onResizeNav}
        onExpandNav={onExpandNav}
      />

      {/* Resize 手柄 */}
      <div className="absolute right-1 bottom-1 w-5 h-5 cursor-se-resize z-10"
        style={{ background: 'linear-gradient(135deg, transparent 50%, var(--color-border-default) 50%, transparent 60%)', borderRadius: '0 0 12px 0' }}
        onMouseDown={handleResizeDown}
      />
    </aside>
  )
}
