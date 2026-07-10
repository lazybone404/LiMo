import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'

export function MarkdownPreview({ content, meta: _meta }: { content: string | null; meta: { style: string; mode: string; version: number } | null }) {
  const components = useMemo(() => createMarkdownComponents(), [])

  if (!content) {
    return <p className="text-[13px] text-disabled p-8">加载中...</p>
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
