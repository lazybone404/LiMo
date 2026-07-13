import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, REMARK_PLUGINS_PLAIN, REHYPE_PLUGINS_PLAIN } from '../../lib/markdown-plugins'
import { createMarkdownComponents } from '../../lib/markdown-components'
import { EpubChapterView } from './EpubSkeletonPanel'

export function TranscriptView({ rawText, sourceType }: { rawText: string; sourceType?: string }) {
  const components = useMemo(() => createMarkdownComponents(), [])
  // 网页/纯文本内容不含公式，用纯文本插件避免 KaTeX 误识别 base64/CSS 残留
  const isPlain = sourceType === 'webpage' || sourceType === 'text'
  const remark = isPlain ? REMARK_PLUGINS_PLAIN : REMARK_PLUGINS
  const rehype = isPlain ? REHYPE_PLUGINS_PLAIN : REHYPE_PLUGINS

  // 尝试 JSON 解析
  let parsed: any = null
  try { parsed = JSON.parse(rawText) } catch { /* 非 JSON → Markdown 渲染 */ }

  if (parsed) {
    if (parsed.chapters && !parsed.metadata?.isMarkdown) {
      return <EpubChapterView rawText={rawText} showSkeleton={false} />
    }
    if (parsed.segments) {
      const segments = parsed.segments as Array<{ start: number; end: number; text: string }>
      const duration = parsed.duration ? Number(parsed.duration) : null
      const total = segments.reduce((sum: number, s: { text?: string }) => sum + (s.text?.length || 0), 0)
      return (
        <>
          {duration ? (
            <p className="text-[12px] text-disabled mb-3">
              时长 {Math.floor(duration / 60)} 分钟 · {total.toLocaleString()} 字 · {segments.length} 段
            </p>
          ) : (
            <p className="text-[12px] text-disabled mb-3">{total.toLocaleString()} 字 · {segments.length} 段</p>
          )}
          {segments.map((s, i) => {
            const mm = Math.floor(s.start / 60)
            const ss = Math.floor(s.start % 60)
            const time = `${mm.toString().padStart(2, '0')}:${ss.toString().padStart(2, '0')}`
            return (
              <p key={i} className="text-[14px] leading-relaxed mb-2">
                <span className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none mr-2">
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3" /></svg>
                  {time}
                </span>
                {s.text}
              </p>
            )
          })}
        </>
      )
    }
    return <pre className="text-[12px] whitespace-pre-wrap">{JSON.stringify(parsed, null, 2)}</pre>
  }

  // 网页/文本：纯文本渲染，跳过 Markdown 解析
  // ReactMarkdown 会把 #、|、* 等字符意外解析为标题/表格/斜体
  if (isPlain) {
    return (
      <div className="text-[14px] leading-relaxed whitespace-pre-wrap break-words max-w-[720px] mx-auto">
        {rawText}
      </div>
    )
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={remark} rehypePlugins={rehype} components={components}>
        {rawText}
      </ReactMarkdown>
    </div>
  )
}
