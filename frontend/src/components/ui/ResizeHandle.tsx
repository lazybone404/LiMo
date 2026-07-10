import { useCallback, useRef } from 'react'

interface Props {
  onResize: (dx: number) => void
  direction?: 'horizontal' | 'vertical'
}

/**
 * 可拖拽面板分隔条 — Apple 风格
 * 参照 layout.html r-handle 交互
 */
export function ResizeHandle({ onResize, direction = 'horizontal' }: Props) {
  const activeRef = useRef(false)
  const startRef = useRef(0)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    activeRef.current = true
    startRef.current = direction === 'horizontal' ? e.clientX : e.clientY
    document.body.style.cursor = direction === 'horizontal' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'

    const onMove = (ev: MouseEvent) => {
      if (!activeRef.current) return
      const current = direction === 'horizontal' ? ev.clientX : ev.clientY
      const dx = current - startRef.current
      startRef.current = current
      onResize(dx)
    }
    const onUp = () => {
      activeRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [onResize, direction])

  const isH = direction === 'horizontal'

  return (
    <div
      onMouseDown={onMouseDown}
      className="group relative shrink-0 transition-colors z-10"
      style={{
        width: isH ? 5 : '100%',
        height: isH ? '100%' : 5,
        cursor: isH ? 'col-resize' : 'row-resize',
      }}
    >
      <div
        className="absolute rounded-full transition-colors group-hover:bg-accent/30 group-active:bg-accent/60"
        style={{
          inset: isH ? '0 2px' : '2px 0',
          background: 'var(--color-border-light)',
        }}
      />
    </div>
  )
}
