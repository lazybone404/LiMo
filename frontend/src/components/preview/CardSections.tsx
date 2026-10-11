import { useMemo } from 'react'
import { splitByH2 } from '../../lib/markdown-sections'
import { MarkerContent } from './MarkerContent'

/**
 * 卡片骨架的渲染容器 —— 按 `##` 分节，**只有被标记的节**渲染成卡片。
 *
 * ⚠️ **2026-10-11 起不再是「每个 H2 一张卡」**：用户判定「一律按标题切卡太简单粗暴，
 * 卡片应该用在需要强调的地方」。现在哪几节成卡由**模型**决定 —— 它在产物里把
 * `<!-- limo-block:v1 type="card" -->` 写在某个 `##` 标题之下，那一节就成卡；其余章节
 * 保持普通正文，与 `paper` 下的观感一致。判据收在 `splitByH2` 的 `Section.card`
 * （前后端同一份，见 `lib/markdown-sections.ts`）。
 *
 * ⚠️ **每张卡各跑一个既有的 `MarkdownPreview`**（不是新写一个渲染器）：表格 / 代码 /
 * 引用 / 公式 / 图片 / 时间戳 badge / 标题锚点全部**与今天逐一致**。
 * 新起第二个 Markdown 渲染器是这类改动最常见的自伤方式 —— 它会立刻在「某个元素在卡片里
 * 长得不一样」上暴露出来，而且只有一部分元素会暴露。
 *
 * ⚠️ **没有 `.md-cards` 外层容器了**：卡片与普通章节现在是**混排**的兄弟节点，套一层
 * flex 会把普通章节的每行内容当成独立 flex item。卡片间距改由 `cards.css` 的
 * `margin-block` 承担（普通流里相邻卡片的 margin 会折叠，故两张卡之间不会翻倍）。
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
      {sections.map((s, i) => {
        // ⚠️ `heading` 与 `body` 拼回即是该节完整 Markdown（`SplitResult` 的约定）——
        // 标题留在里面，因为卡片头就是那个 `##`。
        const inner = `${s.heading}\n${s.body}`
        // ⚠️ key 用下标而非 heading 文本：同名 H2 会撞 key，而同名 H2 在产物里
        // 是**允许**的（模型不保证标题唯一）。下标在此安全 —— 内容变化时整篇重渲染。
        return s.card ? (
          <section key={i} className="md-card">
            <MarkerContent content={inner} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
          </section>
        ) : (
          <MarkerContent key={i} content={inner} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
        )
      })}
    </>
  )
}
