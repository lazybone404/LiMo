import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'

export function MarkdownPreview({ content, meta: _meta, sourceUrl, isVideo }: { content: string | null; meta: { style: string; mode: string; version: number } | null; sourceUrl?: string; isVideo?: boolean }) {
  const components = useMemo(() => createMarkdownComponents(sourceUrl, isVideo), [sourceUrl, isVideo])

  if (!content) {
    return <p className="text-[13px] text-disabled p-8">加载中...</p>
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={components}>
        {normalizeLatexDelimiters(content)}
      </ReactMarkdown>
    </div>
  )
}
