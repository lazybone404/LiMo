import { useMemo } from 'react'
import { parseMarkers } from '../../lib/markdown-markers'
import { MarkdownPreview } from './MarkdownPreview'

/**
 * 语义标记感知的正文渲染 —— 把一段 Markdown 按 `limo-block` 切成若干段，标记段包一层
 * `<div class="md-block" data-block-type="…">`。
 *
 * ⚠️ **每段各跑一个既有的 `MarkdownPreview`**（不是新写一个渲染器）：表格 / 代码 / 引用 /
 * 公式 / 图片 / 时间戳 badge / 标题锚点全部**与今天逐一致**。与 `CardSections` 同款策略。
 *
 * ⚠️ **接入点只有两处，且都在 `preview/` 内**：`PreviewArea`（paper 分支）与
 * `CardSections`（每张卡）。**绝不要放进 `MarkdownPreview` 内部** —— 那会静默扩散到
 * `PreviewArea` 里另外两处调用点（Excel 数据摘要 / 原始转录），而那两处都不是 LLM 产物，
 * 模型也不会在那里写标记（`CardSections.tsx` 文件头有同款警告）。
 *
 * ⚠️ **无标记时的渲染树必须与改造前逐字相同**：走下面的快路径，产出一个
 * `MarkdownPreview`，且 `content` 传**原文**（不是 `segment.content`）——
 * 后者是 CRLF 规范化过的，虽然渲染上看不出差别，但「零回归」应当是**可断言的**，
 * 不是「渲染出来一样就行」。
 */
interface MarkerContentProps {
  content: string | null
  meta: { style: string; mode: string; version: number } | null
  sourceUrl?: string
  isVideo?: boolean
}

export function MarkerContent({ content, meta, sourceUrl, isVideo }: MarkerContentProps) {
  const segments = useMemo(() => parseMarkers(content ?? ''), [content])

  // ⚠️ 快路径：无标记 = 单个 text 段（且不是 `content === null` 时的空段）——
  // 绝大多数产物走这里。
  if (segments.length === 1 && segments[0].kind === 'text') {
    return <MarkdownPreview content={content} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
  }

  return (
    <>
      {segments.map((seg, i) => {
        // ⚠️ 空段必须丢掉：`MarkdownPreview` 对空 `content` 渲染的是「加载中...」，
        // 一个恰好被标记切出来的空壳会把它显示在正文中间（零报错）。
        if (seg.content.trim() === '') return null
        if (seg.kind === 'text') {
          return <MarkdownPreview key={i} content={seg.content} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
        }
        return (
          // ⚠️ `data-block-type` 原样透传解析出来的类型（**包括白名单外的**）——
          // 不认识它的 CSS 不匹配，于是它退化成一块普通内容，而不是消失。
          // 白名单判定属于**渲染形态**的事，不属于解析。
          <div key={i} className="md-block" data-block-type={seg.type}>
            <MarkdownPreview content={seg.content} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
          </div>
        )
      })}
    </>
  )
}
