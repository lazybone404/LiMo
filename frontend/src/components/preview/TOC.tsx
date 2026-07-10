import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../../store'
import { extractHeadings } from '../../lib/markdown-components'
import { ChevronLeft } from 'lucide-react'

interface TocItem {
  id: string
  text: string
  level: number
}

/**
 * TOC 侧栏 — 从 Markdown 内容提取标题，scroll-spy 高亮
 */
export function TOC({ width = 160 }: { width?: number }) {
  const activeView = useStore(s => s.activeView)
  const notesContent = useStore(s => s.currentContent)
  const polishedContent = useStore(s => s.currentPolishedContent)
  const setPanelCollapsed = useStore(s => s.setPanelCollapsed)
  const content = activeView === 'polished' ? polishedContent : notesContent
  const [activeId, setActiveId] = useState<string | null>(null)
  const observerRef = useRef<IntersectionObserver | null>(null)

  // 从 Markdown 中提取标题
  const items: TocItem[] = useMemo(() => {
    if (!content) return []
    return extractHeadings(content)
  }, [content])

  // IntersectionObserver scroll-spy
  useEffect(() => {
    if (items.length === 0) return

    // 延迟等待 DOM 渲染
    const timer = setTimeout(() => {
      observerRef.current?.disconnect()
      const observer = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (entry.isIntersecting) {
              setActiveId(entry.target.id)
            }
          }
        },
        { rootMargin: '-80px 0px -60% 0px', threshold: 0 }
      )
      items.forEach(item => {
        const el = document.getElementById(item.id)
        if (el) observer.observe(el)
      })
      observerRef.current = observer
    }, 200)

    return () => {
      clearTimeout(timer)
      observerRef.current?.disconnect()
    }
  }, [items, content])

  if (items.length === 0) return null

  return (
    <aside className="border-r border-border-light shrink-0 overflow-y-auto px-3 py-4 bg-elevated/60 backdrop-blur-[20px]" style={{ width, minWidth: 120, maxWidth: 400 }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-disabled tracking-wide uppercase">目录</span>
        <button
          onClick={() => setPanelCollapsed('toc', true)}
          className="w-5 h-5 rounded hover:bg-surface-hover flex items-center justify-center"
          title="收起目录"
        >
          <ChevronLeft size={12} className="text-muted" />
        </button>
      </div>
      <nav className="space-y-0.5">
        {items.map(item => (
          <a
            key={item.id}
            href={`#${item.id}`}
            onClick={(e) => {
              e.preventDefault()
              document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
            }}
            className={`block text-[12px] py-0.5 leading-tight transition-colors hover:text-accent ${
              activeId === item.id
                ? 'text-accent font-medium'
                : 'text-disabled'
            }`}
            style={{ paddingLeft: `${(item.level - 2) * 10}px` }}
          >
            {item.text}
          </a>
        ))}
      </nav>
    </aside>
  )
}
