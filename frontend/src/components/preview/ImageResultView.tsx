/**
 * 图片处理结果视图 — extract/table/digitize/understand 有结果分支。
 * props 驱动，无 store 依赖。
 */
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'
import type { createMarkdownComponents } from '../../lib/markdown-components'

const MODE_LABELS: Record<string, string> = {
  extract: '提取文字', table: '表格数据', digitize: '文档数字化', understand: '理解内容',
}

export function ImageResultView({ result, mode, title, createdAt, markdownComponents }: {
  result: { text: string; engine: string; id: string }
  mode: string
  title?: string
  createdAt?: string
  markdownComponents: ReturnType<typeof createMarkdownComponents>
}) {
  return (
    <div className="mx-auto px-10 py-8">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-[20px] font-semibold">{title}</h2>
        <span className="text-[12px] text-success font-medium">
          {result.engine} · {MODE_LABELS[mode] || mode}
        </span>
      </div>
      <div className="text-[12px] text-disabled mb-4 flex items-center gap-3">
        <span>{MODE_LABELS[mode]}</span>
        {createdAt && (
          <><span>·</span><span>{new Date(createdAt).toLocaleDateString('zh-CN')}</span></>
        )}
      </div>
      <div className="markdown-body">
        <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={markdownComponents}>
          {normalizeLatexDelimiters(result.text)}
        </ReactMarkdown>
      </div>
    </div>
  )
}
