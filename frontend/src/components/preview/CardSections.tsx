import { useMemo } from 'react'
import { splitByH2 } from '../../lib/markdown-sections'
import { MarkerContent } from './MarkerContent'

/**
 * 卡片骨架的渲染容器 —— 把一篇 Markdown 按 `##` 切成若干张卡。
 *
 * ⚠️ **每张卡各跑一个既有的 `MarkdownPreview`**（不是新写一个渲染器）：表格 / 代码 /
 * 引用 / 公式 / 图片 / 时间戳 badge / 标题锚点全部**与今天逐一致**。
 * 新起第二个 Markdown 渲染器是这类改动最常见的自伤方式 —— 它会立刻在「某个元素在卡片里
 * 长得不一样」上暴露出来，而且只有一部分元素会暴露。
 *
 * ⚠️ 本组件**只应该在 `skeleton === 'card'` 时分派**（`PreviewArea` 的 notes/polished
 * 只读态，唯一接入点）。**不要在 `MarkdownPreview` 内部按骨架轴分派** —— 那会静默扩散到
 * 另外两处调用点（Excel 数据摘要 / 原始转录），而那两处都不是 LLM 产物。
 */
interface CardSectionsProps {
  content: string | null
  meta: { style: string; mode: string; version: number } | null
  sourceUrl?: string
  isVideo?: boolean
}

export function CardSections({ content, meta, sourceUrl, isVideo }: CardSectionsProps) {
  const { preamble, sections } = useMemo(() => splitByH2(content ?? ''), [content])

  // 无 H2（或内容还没来）→ **回落长文**，与 paper 走完全相同的渲染路径。
  // ⚠️ `content` 为空时也走这里，交给 MarkdownPreview 渲染它自己的「加载中...」。
  if (!content || sections.length === 0) {
    return <MarkerContent content={content} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
  }

  return (
    <>
      {preamble.trim() !== '' && (
        <MarkerContent content={preamble} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
      )}
      <div className="md-cards">
        {sections.map((s, i) => (
          // ⚠️ key 用下标而非 heading 文本：同名 H2 会撞 key，而同名 H2 在产物里
          // 是**允许**的（模型不保证标题唯一）。下标在此安全 —— 内容变化时整篇重渲染。
          <section key={i} className="md-card">
            <MarkerContent
              content={`${s.heading}\n${s.body}`}
              meta={meta}
              sourceUrl={sourceUrl}
              isVideo={isVideo}
            />
          </section>
        ))}
      </div>
    </>
  )
}
