import { useState, useMemo } from 'react'
import { createMarkdownComponents } from '../../lib/markdown-components'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'

export type EpubChapter = {
  index: number; title: string; sections: EpubSection[]
}
export type EpubSection = {
  index: number; title: string; level: number
  text: string; skeleton: string
}

/** EPUB 章节导航视图 — 电子书转录浏览（transcript tab 复用）。
 *  原 EpubSkeletonPanel 内嵌（2026-08-15 骨架面板砍掉后拆出独立文件）：
 *  左侧章节树 + 右侧章节内容 Markdown 渲染。showSkeleton=false 显示原文全文。 */
export function EpubChapterView({ rawText, showSkeleton = false }: {
  rawText: string; showSkeleton?: boolean
}) {
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [selectedSecIdx, setSelectedSecIdx] = useState(0)
  const components = useMemo(() => createMarkdownComponents(), [])
  let data: { chapters?: EpubChapter[]; metadata?: Record<string, string> } = {}
  try { data = JSON.parse(rawText) } catch { /* handled below */ }
  const chapters = data.chapters || []
  const metadata = data.metadata || {}

  const currentChapter = chapters[selectedIdx]
  const currentSection = currentChapter?.sections?.[selectedSecIdx]

  const navItems: { chIdx: number; secIdx: number; title: string; level: number }[] = []
  chapters.forEach((ch, ci) => {
    ch.sections?.forEach((sec, si) => {
      navItems.push({ chIdx: ci, secIdx: si, title: sec.title || ch.title, level: sec.level || 1 })
    })
  })

  const content = currentSection
    ? (showSkeleton ? currentSection.skeleton : currentSection.text) || currentSection.text
    : ''

  if (!data.chapters?.length) return <p className="text-[13px] text-disabled p-8">无章节数据</p>

  return (
    <div className="flex h-full">
      <div className="w-[220px] shrink-0 border-r border-border-light overflow-y-auto bg-surface">
        <div className="sticky top-0 bg-surface px-3 py-2 border-b border-border-light">
          <p className="text-[11px] text-disabled">
            {metadata.title || '章节目录'}
            {metadata.author && ` · ${metadata.author}`}
          </p>
          <p className="text-[10px] text-disabled mt-0.5">{chapters.length} 章 · {navItems.length} 节</p>
        </div>
        {chapters.map((ch, ci) => (
          <div key={ci}>
            <div
              onClick={() => { setSelectedIdx(ci); setSelectedSecIdx(0) }}
              className={`px-3 py-1.5 text-[12px] font-medium cursor-pointer hover:bg-surface-hover/50 transition-colors flex items-center gap-1.5 ${
                ci === selectedIdx ? 'bg-accent/10 text-accent' : 'text-foreground'
              }`}
            >
              <span className="truncate">{ch.title || `第${ci + 1}章`}</span>
            </div>
            {(ch.sections || []).map((sec, si) => (
              <div
                key={si}
                onClick={() => { setSelectedIdx(ci); setSelectedSecIdx(si) }}
                className={`pl-6 pr-3 py-1 text-[11px] cursor-pointer hover:bg-surface-hover/30 transition-colors flex items-center gap-1.5 ${
                  ci === selectedIdx && si === selectedSecIdx
                    ? 'bg-accent/5 text-accent font-medium'
                    : 'text-dimmed'
                }`}
              >
                <span className="truncate">{sec.title || `§${si + 1}`}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="max-w-[720px] mx-auto markdown-body">
          <ReactMarkdown
            remarkPlugins={REMARK_PLUGINS}
            rehypePlugins={REHYPE_PLUGINS}
            components={components}
          >
            {normalizeLatexDelimiters(content || '*（无内容）*')}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}
