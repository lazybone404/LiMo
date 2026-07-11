import { useEffect, useRef } from 'react'
import { Markmap } from 'markmap-view'
import { transformer } from '../../lib/markmap'
import { useStore } from '../../store'

interface MindmapViewProps {
  value: string
}

/** 清理不适合思维导图的内容：代码块、图片、HTML 标签、BibTeX 条目 */
function cleanMindmapContent(md: string): string {
  let text = md || ''
  text = text.replace(/```[\s\S]*?```/g, '')
  text = text.replace(/<[^>]*>/g, '')
  text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, '')
  text = text.replace(/^@\w+\{[\s\S]*?\n\s*\}/gm, '')
  return text
}

export default function MindmapView({ value }: MindmapViewProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const mmRef = useRef<Markmap | undefined>(undefined)
  const resolvedTheme = useStore(s => s.resolvedTheme)
  const prevThemeRef = useRef(resolvedTheme)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return

    const themeChanged = prevThemeRef.current !== resolvedTheme
    prevThemeRef.current = resolvedTheme

    // 主题变了 → 销毁旧实例，重建以应用新颜色
    if (themeChanged && mmRef.current) {
      try { mmRef.current.destroy() } catch { /* ignore */ }
      mmRef.current = undefined
      svg.innerHTML = ''
    }

    let mm = mmRef.current
    if (!mm) {
      mm = Markmap.create(svg, { duration: themeChanged ? 0 : 300 })
      mmRef.current = mm
    }

    const { root } = transformer.transform(cleanMindmapContent(value || ''))
    mm.setData(root).then(() => {
      // 延迟到下一帧执行 fit()，避免布局未完成时触发页面滚动
      requestAnimationFrame(() => mm.fit())
    })

    return () => {
      if (svg) svg.innerHTML = ''
      mmRef.current = undefined
    }
  }, [value, resolvedTheme])

  return (
    <div className={`absolute inset-0 overflow-hidden ${resolvedTheme === 'dark' ? 'markmap-dark' : ''}`}>
      <svg ref={svgRef} style={{ display: 'block', width: '100%', height: '100%' }} />
    </div>
  )
}
