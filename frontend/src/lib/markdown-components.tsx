import React, { useState } from 'react'
import { Copy, Check, Play, Camera, Link2 } from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { appUrl } from '../api/client'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import { useStore } from '../store'

/** 统一的标题 ID 生成 — 供 Markdown 渲染组件和 TOC 共享 */
export function headingId(text: string) {
  return text.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9一-鿿-]/g, '').replace(/-+/g, '-').replace(/^-|-$/g, '')
}

/** 从 Markdown 源码提取标题（供 TOC 使用） */
export function extractHeadings(md: string) {
  const re = /^(#{2,4})\s+(.+)$/gm
  const items: { level: number; text: string; id: string }[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(md)) !== null) {
    const clean = m[2].replace(/\s*\[▶ \d{2}:\d{2}\]\(#\)/, '').replace(/\s*\*?(?:Content|Screenshot)-\[\d{2}:\d{2}\]/, '')
    items.push({ level: m[1].length, text: clean, id: headingId(clean) })
  }
  return items
}

/** Markdown 组件 — github-markdown-css 打底 + 特殊元素自定义 */
function cleanHeading(children: any): { clean: React.ReactNode; text: string } {
  const str = typeof children === 'string' ? children : (Array.isArray(children) ? children.join('') : String(children ?? ''))
  const timeMatch = str.match(/(?:▶ |\*?Content-\[)?(\d{2}):(\d{2})(?:\]|$)/)
  const cleanText = str.replace(/\s*(?:▶ \d{2}:\d{2}|\*?Content-\[\d{2}:\d{2}\])\s*$/, '')
  if (timeMatch) {
    const time = `${timeMatch[1]}:${timeMatch[2]}`
    return {
      clean: <>{cleanText} <span className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-foreground align-middle select-none"><Play size={9} className="text-accent" />{time}</span></>,
      text: cleanText,
    }
  }
  return { clean: children, text: str }
}

/** 独立代码块组件 — 提取以满足 React hooks 命名规则 */
function CodeBlock({ inline, className, children, ...props }: any) {
  const match = /language-(\w+)/.exec(className || '')
  const codeContent = String(children).replace(/\n$/, '')
  const [copied, setCopied] = useState(false)
  const resolvedTheme = useStore(s => s.resolvedTheme)
  const syntaxStyle = resolvedTheme === 'dark' ? oneDark : oneLight

  if (!inline && match) {
    return (
      <div className="group relative my-4 overflow-hidden rounded-lg border border-border-light">
        <div className="flex items-center justify-between bg-surface px-4 py-1.5 border-b border-border-light">
          <span className="text-[11px] font-medium text-dimmed">{match[1]}</span>
          <button
            onClick={() => {
              navigator.clipboard.writeText(codeContent).then(
                () => setCopied(true),
                () => useStore.getState().addToast('复制失败，请手动复制', 'error'),
              )
              setTimeout(() => setCopied(false), 2000)
            }}
            className="flex items-center gap-1 text-[11px] text-dimmed hover:text-foreground transition-colors"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
        <SyntaxHighlighter
          style={syntaxStyle}
          language={match[1]}
          PreTag="div"
          customStyle={{ margin: 0, padding: '1rem', background: 'var(--color-surface)', fontSize: '0.85rem' }}
          {...props}
        >
          {codeContent}
        </SyntaxHighlighter>
      </div>
    )
  }

  return <code {...props}>{children}</code>
}

/** 图片组件 — 加载失败时显示占位符，而非静默隐藏 */
function MarkdownImage({ src, alt, ...props }: any) {
  const [broken, setBroken] = useState(false)
  const imgSrc = (typeof src === 'string') ? appUrl(src) : src

  // 空 src 会导致浏览器重新加载整页，不渲染该元素
  if (!imgSrc || imgSrc === '') return null

  if (broken) {
    return (
      <div className="my-6 flex justify-center">
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-light bg-surface px-6 py-8 text-dimmed min-w-[200px]">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>
            <circle cx="9" cy="9" r="2"/>
            <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>
          </svg>
          <span className="text-[13px] text-center max-w-[300px] break-all px-2">{alt || '图片加载失败'}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="my-6 flex justify-center">
      <Zoom>
        <img src={imgSrc} alt={alt} className="max-w-full cursor-zoom-in rounded-lg shadow-sm transition-shadow hover:shadow-md" style={{ maxHeight: '500px' }} loading="lazy"
          onError={() => setBroken(true)}
          {...props} />
      </Zoom>
    </div>
  )
}

export function createMarkdownComponents() {
  return {
    h2: ({ children, ...props }: any) => {
      const { clean, text } = cleanHeading(children)
      const id = headingId(text)
      return <h2 id={id} className="group relative" {...props}>
        {clean}
        <a href={`#${id}`} className="absolute -left-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-disabled hover:text-accent" aria-label="链接到此标题">
          <Link2 size={14} />
        </a>
      </h2>
    },
    h3: ({ children, ...props }: any) => {
      const { clean, text } = cleanHeading(children)
      const id = headingId(text)
      return <h3 id={id} className="group relative" {...props}>
        {clean}
        <a href={`#${id}`} className="absolute -left-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-disabled hover:text-accent" aria-label="链接到此标题">
          <Link2 size={13} />
        </a>
      </h3>
    },
    h4: ({ children, ...props }: any) => {
      const { clean, text } = cleanHeading(children)
      const id = headingId(text)
      return <h4 id={id} className="group relative" {...props}>
        {clean}
        <a href={`#${id}`} className="absolute -left-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-disabled hover:text-accent" aria-label="链接到此标题">
          <Link2 size={12} />
        </a>
      </h4>
    },
    code: CodeBlock,
    a({ href, children, ...props }: any) {
      // 安全校验：拒绝 javascript:/data:/vbscript: 等危险协议
      const safe = typeof href === 'string' && /^(https?:|\/|#|mailto:)/.test(href)
      if (!safe) {
        return <span {...props}>{children}</span>
      }
      const text = typeof children === 'string' ? children : String(children?.[0] ?? '')
      const isOriginLink = text.startsWith('原片 @') || text.includes('*Content-[') || text.startsWith('▶ ')

      if (isOriginLink) {
        const timeMatch = text.match(/(\d{2}:\d{2})/)
        const timeText = timeMatch ? timeMatch[1] : '原片'
        return (
          <a href={href} target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-3 py-0.5 text-[13px] font-medium text-foreground hover:bg-accent/10 transition-colors no-underline"
            {...props}><Play size={10} className="text-accent" />{timeText}</a>
        )
      }
      return <a href={href} target={href?.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer" {...props}>{children}</a>
    },
    img: MarkdownImage,
    p({ children, ...props }: any) {
      const pid = React.useId()
      function renderContent(text: unknown): React.ReactNode {
        if (typeof text !== 'string') return text as React.ReactNode
        const parts = text.split(/(\*?(?:Content|Screenshot)-\[\d{2}:\d{2}\])/g)
        return parts.map((part, i) => {
          const time = part.match(/\[(\d{2}:\d{2})\]/)?.[1] || ''
          if (part.includes('Content-[')) {
            return <span key={`${pid}-${i}`} className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Play size={9} className="text-accent" />{time}</span>
          }
          if (part.includes('Screenshot-[')) {
            return <span key={`${pid}-${i}`} className="inline-flex items-center gap-0.5 rounded-full bg-warning/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Camera size={9} className="text-warning" />{time}</span>
          }
          return part
        })
      }
      const processed = React.Children.map(children, child => renderContent(child))
      const hasBlockChild = React.Children.toArray(children).some(
        c => React.isValidElement(c) && typeof c.type === 'function'
      )
      return hasBlockChild ? <div {...props}>{processed}</div> : <p {...props}>{processed}</p>
    },
    li({ children, ...props }: any) {
      const lid = React.useId()
      const processed = React.Children.map(children, child => {
        if (typeof child === 'string') {
          const parts = child.split(/(\*?(?:Content|Screenshot)-\[\d{2}:\d{2}\])/g)
          return parts.map((part, i) => {
            const time = part.match(/\[(\d{2}:\d{2})\]/)?.[1] || ''
            if (part.includes('Content-[')) {
              return <span key={`${lid}-${i}`} className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Play size={9} className="text-accent" />{time}</span>
            }
            if (part.includes('Screenshot-[')) {
              return <span key={`${lid}-${i}`} className="inline-flex items-center gap-0.5 rounded-full bg-warning/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Camera size={9} className="text-warning" />{time}</span>
            }
            return part
          })
        }
        return child
      })
      return <li {...props}>{processed}</li>
    },
  }
}
