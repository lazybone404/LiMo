import React, { useEffect, useState } from 'react'
import { Copy, Check, Play, Camera, Link2, ChevronDown, ChevronUp, Code } from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { appUrl } from '../api/client'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import { useStore } from '../store'

/** 统一的标题 ID 生成 — 供 Markdown 渲染组件和 TOC 共享 */
export function headingId(text: string) {
  return text.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9一-鿿-]/g, '').replace(/-+/g, '-').replace(/^-|-$/g, '')
}

/**
 * 递归取 React 子节点的纯文本（P1-15）。
 *
 * ⚠️ 此前渲染侧对 children 直接 `Array.isArray ? children.join('')`，而标题里的
 *    `**加粗**` 经 markdown 解析后是**React 元素**而不是字符串 → 拼出
 *    `[object Object]` → 渲染 id 成 `object-object-内容`，与 TOC 从源码算出的
 *    `重点-内容` 不符。TOC 点击走 `document.getElementById(id)?.` —— **null 被 `?.`
 *    静默吞掉**，表现是「点了目录没反应」，零报错、零控制台痕迹。
 */
export function nodeText(node: any): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (typeof node === 'object' && 'props' in node) return nodeText(node.props?.children)
  return ''
}

/**
 * 剥掉标题里的行内 Markdown 语法（P1-15）。
 *
 * 源码侧（TOC 从 `## **重点** 内容` 取）拿到的是带标记的原文，渲染侧拿到的是已
 * 解析的节点树 —— 不剥的话两侧必然算出不同 id。
 *
 * ⚠️ 图片规则必须排在链接**之前**：`![alt](url)` 会被链接规则吃成 `![alt]`。
 */
export function inlineText(md: string): string {
  return md
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/<[^>]+>/g, '')
}

/**
 * 标题装饰剥离（时间戳 badge + 行内语法）——**渲染侧与 TOC 侧共用这一份**（P1-15）。
 *
 * ⚠️ 此前两侧各写一份、规则还有细微差别（一侧要求 `[▶ 12:30](#)` 的方括号形态，
 *    另一侧只剥行尾；`Screenshot-` 只有一侧认）→ 带时间戳的标题两侧 id 不同，
 *    而两边读起来都像「已经正确处理了时间戳」。
 *
 * 三种形态都要认（源码态 / 渲染后态各不同）：
 *   `[▶ 12:30](#)`（源码链接）→ inlineText 后是 `▶ 12:30`
 *   `Content-[12:30]` / `Screenshot-[12:30]`（badge 源码）
 *   `12:30`（渲染后 badge 只剩文本，且总在行尾）
 */
export function stripHeadingDecor(text: string): string {
  return inlineText(text)
    .replace(/\s*\*?(?:Content|Screenshot)-\[\d{2}:\d{2}\]/g, '')
    .replace(/\s*▶\s*\d{2}:\d{2}/g, '')
    .replace(/\s*\d{2}:\d{2}\s*$/, '')
    .trim()
}

/** 从 Markdown 源码提取标题（供 TOC 使用） */
export function extractHeadings(md: string) {
  const items: { level: number; text: string; id: string }[] = []
  // ⚠️ 逐行扫 + 跳过围栏代码块（P1-15）：此前是一条多行正则，于是代码块里的
  //    `## ` 也被当成标题 → 目录多出「点了没反应」的幽灵项（没有对应的 DOM 节点）。
  let inFence = false
  for (const rawLine of md.split('\n')) {
    const line = rawLine.replace(/\r$/, '')   // CRLF：不剥会让 id 尾多一个 `-`
    if (/^\s*(?:```|~~~)/.test(line)) { inFence = !inFence; continue }
    if (inFence) continue
    const m = /^(#{2,4})\s+(.+?)\s*$/.exec(line)
    if (!m) continue
    const text = stripHeadingDecor(m[2])
    if (!text) continue                       // 纯时间戳标题（`## ▶ 12:30`）无锚点可言
    items.push({ level: m[1].length, text, id: headingId(text) })
  }
  return items
}

/** Markdown 组件 — github-markdown-css 打底 + 特殊元素自定义 */
function cleanHeading(children: any): { clean: React.ReactNode; text: string } {
  // ⚠️ `nodeText` 而非 `children.join('')`（P1-15）：标题里的 `**加粗**` 是 React
  //    元素，join 产出 `[object Object]` → id 与 TOC 对不上（详见 nodeText 注释）。
  const str = nodeText(children)
  const timeMatch = str.match(/(?:▶ |\*?Content-\[)?(\d{2}):(\d{2})(?:\]|$)/)
  // ⚠️ 剥离规则与 TOC **共用** `stripHeadingDecor`（两侧各写一份 = 漂移温床）
  const cleanText = stripHeadingDecor(str)
  if (timeMatch) {
    const time = `${timeMatch[1]}:${timeMatch[2]}`
    return {
      clean: <>{cleanText} <span className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-foreground align-middle select-none"><Play size={9} className="text-accent" />{time}</span></>,
      text: cleanText,
    }
  }
  return { clean: children, text: cleanText }
}

/** chart 围栏源码块 — 默认折叠为占位条。
 * chart 是图表内部协议标记（LLM 与后端的握手，pi-docs/图表围栏内部协议设计说明.md），
 * 最终会被后端 render_charts 替换为 PNG 图片；流式/预览期间展示数百行源码既卡顿
 * 又无阅读价值，默认只显示一行占位 + 可展开查看源码。
 * 只用于图表内部协议围栏（```fig / ```chart）；svg/xml/json 是用户内容空间，
 * 不走此组件（普通代码块展示）。
 */
function ChartCodeBlock({ lang, codeContent }: { lang: string; codeContent: string }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const lineCount = codeContent.split('\n').length
  const copy = () => {
    navigator.clipboard.writeText(codeContent).then(
      () => setCopied(true),
      () => useStore.getState().addToast('复制失败，请手动复制', 'error'),
    )
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div className="group relative my-4 overflow-hidden rounded-lg border border-border-light">
      <div className="flex items-center justify-between bg-surface px-4 py-1.5 border-b border-border-light">
        <span className="text-[11px] font-medium text-dimmed">图表</span>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-[11px] text-dimmed hover:text-foreground transition-colors"
          >
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {expanded ? '收起源码' : '展开源码'}
          </button>
          <button
            onClick={copy}
            className="flex items-center gap-1 text-[11px] text-dimmed hover:text-foreground transition-colors"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
      </div>
      {expanded ? (
        <pre
          style={{ margin: 0, padding: '1rem', background: 'var(--color-surface)', fontSize: '0.85rem', overflowX: 'auto' }}
          className="font-mono"
        >
          <code className={`language-${lang}`}>{codeContent}</code>
        </pre>
      ) : (
        <div className="flex items-center gap-2 bg-surface/50 px-4 py-2.5 text-[12px] text-dimmed select-none">
          <Code size={13} />
          图表数据（{lineCount} 行，保存后渲染为图片）
        </div>
      )}
    </div>
  )
}

/** 独立代码块组件 — 提取以满足 React hooks 命名规则 */
function CodeBlock({ inline, className, children, ...props }: any) {
  const match = /language-(\w+)/.exec(className || '')
  const codeContent = String(children).replace(/\n$/, '')
  const [copied, setCopied] = useState(false)
  const resolvedTheme = useStore(s => s.resolvedTheme)
  const syntaxStyle = resolvedTheme === 'dark' ? oneDark : oneLight

  if (!inline && match) {
    const lang = match[1]
    // 图表围栏（内部协议标记）默认折叠为占位条：最终会被后端渲染成图片，
    // 流式期间展示源码既卡顿又无阅读价值。两种围栏都要折叠——
    // fig（语义 JSON，现行协议）/ chart（手写 SVG，legacy 兜底）。
    // svg/xml/json 是用户内容空间，不折叠。
    if (lang === 'chart' || lang === 'fig') {
      return <ChartCodeBlock lang={lang} codeContent={codeContent} />
    }
    return (
      <div className="group relative my-4 overflow-hidden rounded-lg border border-border-light">
        <div className="flex items-center justify-between bg-surface px-4 py-1.5 border-b border-border-light">
          <span className="text-[11px] font-medium text-dimmed">{lang}</span>
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
          language={lang}
          PreTag="div"
          showLineNumbers
          wrapLongLines
          lineNumberStyle={{
            minWidth: '2.25em',
            paddingRight: '1em',
            color: 'var(--color-disabled)',
            textAlign: 'right',
            userSelect: 'none',
          }}
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
  const currentDocId = useStore(s => s.currentDocId)
  const [broken, setBroken] = useState(false)
  let imgSrc = (typeof src === 'string') ? appUrl(src) : src

  // 流式 buffer / 转录等未经过后端 rewrite_asset_urls 的内容，图片是
  // assets/xxx 相对路径 → 补全为 /api/assets/{tx_id}/xxx 才能加载。
  // 已 rewrite 的 /api/assets/... 与外部 http URL 不受影响。
  if (typeof imgSrc === 'string' && imgSrc.startsWith('assets/') && currentDocId) {
    imgSrc = appUrl(`/api/assets/${currentDocId}/${imgSrc.slice('assets/'.length)}`)
  }

  // src 变化（生成中 URL 可能从相对 → 绝对 / 文件就绪）→ 重置失败状态
  useEffect(() => { setBroken(false) }, [imgSrc])

  // 加载失败自动重试（2026-08-06 修复）：生成完成前图片文件可能未就绪
  // （URL 已出现但 404），成功后自动显示，无需手动刷新
  useEffect(() => {
    if (!broken) return
    const t = setTimeout(() => setBroken(false), 2000)
    return () => clearTimeout(t)
  }, [broken])

  // 空 src 会导致浏览器重新加载整页，不渲染该元素。放在两个 useEffect 之后——
  // 提前 return 会让 hook 数量随 imgSrc 空/非空在 2↔4 间变化，React 抛
  // 「Rendered more hooks than during the previous render」崩溃（P0-1）。
  // 空 src 时两个 effect 均无害：setBroken(false) 幂等，重试 effect 早退。
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

export function createMarkdownComponents(sourceUrl?: string, isVideo?: boolean) {
  // 仅视频文档构造时间戳跳转链接
  const makeTimeLink = (mm: string, ss: string) => {
    if (!sourceUrl || !isVideo) return null
    const totalSec = parseInt(mm) * 60 + parseInt(ss)
    const u = sourceUrl
    if (u.includes('bilibili.com')) return `${u.replace(/[?&]spm_id_from=.*/, '').replace(/\/\?.*$/, '')}?t=${totalSec}`
    if (u.includes('youtube.com') || u.includes('youtu.be')) return `${u.split('?')[0]}?t=${totalSec}`
    return `${u}#t=${totalSec}`
  }
  // p/li 共用的时间戳徽章处理：Content-[mm:ss] → 跳转 badge / Screenshot-[mm:ss] → 截图标。
  // baseKey 由调用组件传入（useId），避免两处重复的徽章渲染逻辑。
  const renderTimestampBadges = (text: string, baseKey: string): React.ReactNode => {
    const parts = text.split(/(\*?(?:Content|Screenshot)-\[\d{2}:\d{2}\])/g)
    return parts.map((part, i) => {
      const time = part.match(/\[(\d{1,2}):(\d{1,2})\]/)
      const mm = time?.[1] || ''
      const ss = time?.[2] || ''
      const timeText = time ? `${mm}:${ss}` : ''
      const link = time && sourceUrl ? makeTimeLink(mm, ss) : null
      if (part.includes('Content-[')) {
        const badge = <span className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Play size={9} className="text-accent" />{timeText}</span>
        return link ? <a key={`${baseKey}-${i}`} href={link} target="_blank" rel="noopener noreferrer" className="no-underline">{badge}</a> : <span key={`${baseKey}-${i}`}>{badge}</span>
      }
      if (part.includes('Screenshot-[')) {
        return <span key={`${baseKey}-${i}`} className="inline-flex items-center gap-0.5 rounded-full bg-warning/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Camera size={9} className="text-warning" />{timeText}</span>
      }
      return part
    })
  }
  return {
    h2: ({ children, ...props }: any) => {
      const { clean, text } = cleanHeading(children)
      const id = headingId(text)
      const scrollTo = (e: React.MouseEvent) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
      return <h2 id={id} className="group relative" {...props}>
        {clean}
        <a href={`#${id}`} onClick={scrollTo} className="absolute -left-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-disabled hover:text-accent" aria-label="链接到此标题">
          <Link2 size={14} />
        </a>
      </h2>
    },
    h3: ({ children, ...props }: any) => {
      const { clean, text } = cleanHeading(children)
      const id = headingId(text)
      const scrollTo = (e: React.MouseEvent) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
      return <h3 id={id} className="group relative" {...props}>
        {clean}
        <a href={`#${id}`} onClick={scrollTo} className="absolute -left-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-disabled hover:text-accent" aria-label="链接到此标题">
          <Link2 size={13} />
        </a>
      </h3>
    },
    h4: ({ children, ...props }: any) => {
      const { clean, text } = cleanHeading(children)
      const id = headingId(text)
      const scrollTo = (e: React.MouseEvent) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
      return <h4 id={id} className="group relative" {...props}>
        {clean}
        <a href={`#${id}`} onClick={scrollTo} className="absolute -left-6 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity text-disabled hover:text-accent" aria-label="链接到此标题">
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
      // 空锚点链接不作跳转（旧产物残留的 [#](#)）避免页面滚动
      const isDeadHash = href === '#' && !props.id
      const text = typeof children === 'string' ? children : String(children?.[0] ?? '')
      const isOriginLink = text.startsWith('原片 @') || text.includes('*Content-[') || text.startsWith('▶ ')

      if (isDeadHash) {
        // 旧产物残留的 [#](#) 链接，不跳转
        return <span className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[12px] font-medium text-foreground align-middle select-none"><Play size={9} className="text-accent" />{text.replace('▶ ', '')}</span>
      }
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
    p: function MarkdownParagraph({ children, ...props }: any) {
      const pid = React.useId()
      const processed = React.Children.map(children, child =>
        typeof child === 'string' ? renderTimestampBadges(child, pid) : child)
      // ⚠️ 段落含**链接 / 行内代码 / 内联图片**时（components.a / .code / .img 都是函数），
      // `hasBlockChild` 为真 → 整段走 <div> 分支。技术笔记里这是**绝大多数**段落。
      // 故阅读版式（首行缩进 / 段距 / 行高）必须挂在一个**两个分支都带**的类名上 ——
      // 只给 <p> 挂的话，会出现「纯文本段落缩进了、带行内代码的段落没缩进」的随机抖动，
      // 而且看起来完全像渲染正常。
      const hasBlockChild = React.Children.toArray(children).some(
        c => React.isValidElement(c) && typeof c.type === 'function'
      )
      const cls = [props.className, 'md-para'].filter(Boolean).join(' ')
      return hasBlockChild
        ? <div {...props} className={cls}>{processed}</div>
        : <p {...props} className={cls}>{processed}</p>
    },
    li: function MarkdownListItem({ children, ...props }: any) {
      const lid = React.useId()
      const processed = React.Children.map(children, child =>
        typeof child === 'string' ? renderTimestampBadges(child, lid) : child)
      return <li {...props}>{processed}</li>
    },
    table: ({ children }: any) => (
      <div className="md-table-wrap overflow-x-auto my-4 rounded-lg border border-border-light">
        <table className="min-w-full">{children}</table>
      </div>
    ),
  }
}
