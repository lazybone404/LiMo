import { useCallback, useEffect, useImperativeHandle, useMemo, useState, type Ref } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { foldSections, type FoldSection } from '../../lib/fold-scan'
import { MarkerContent } from './MarkerContent'

/**
 * 产物折叠阅读 —— 主题（卡片网格）/ 一句话（每节一句）两种排布。
 *
 * ## 为什么两档合在一个组件里
 *
 * 展开态、展开内容、收起按钮是**同一件事**。两档只在「未展开时长什么样」上分叉
 * （网格卡 vs 单行）。拆成两个组件就要把展开逻辑写两遍，而两份的漂移形态是
 * 「一种档位展开后内容不一样」，零报错。
 *
 * ## 展开的内容走 `MarkerContent`
 *
 * ⚠️ **不是 `MarkdownPreview`**：`limo-block` 的十二种语义块、`evidence` 的 `<details>`、
 * 卡片内表格/代码/公式/图片，全部靠 `MarkerContent` 切段渲染。直接给 `MarkdownPreview`
 * 会让语义块的外壳整个消失，而页面照样正常显示 —— 静默失效。
 *
 * ## 折叠态不渲染 Markdown
 *
 * 未展开时只有标题（React 直接画，带 `id` 供 TOC 跳转）与一句**纯文本**。
 * 把一节缩成一句再交给 Markdown 渲染器会坏掉表格、代码围栏，并让行内图片凭空成图
 * （`![...](...)` 单行语法仍成立）。所以 `lead` 由 `lib/fold-scan.ts::leadOf` 产出纯文本。
 *
 * ## 尺寸全部跟着 Ctrl+滚轮
 *
 * 根容器的 `font-size: var(--read-font-size)` 是唯一接点（见 `styles/fold.css` 文件头）。
 */

/** 窄条上「全部展开 / 全部收起」需要的两个动作（状态仍住在本组件）。 */
export interface FoldViewHandle {
  expandAll: () => void
  collapseAll: () => void
}

interface FoldViewProps {
  variant: 'topic' | 'lead'
  content: string | null
  meta: { style: string; mode: string; version: number } | null
  sourceUrl?: string
  isVideo?: boolean
  /** 给窄条上的「全部展开 / 全部收起」用。React 19 起 `ref` 是普通 prop，不必 forwardRef。 */
  ref?: Ref<FoldViewHandle>
  /**
   * 展开状态回传 —— 按钮要据此在「全部展开」「全部收起」之间切换。
   * ⚠️ 调用方必须用 `useCallback` 给它一个稳定引用：它进了 effect 依赖，
   * 每次渲染换一个函数会让「上报 → setState → 重渲染」每帧跑一轮。
   */
  onExpandStateChange?: (state: { expandable: number; expanded: number }) => void
}

export function FoldView({ variant, content, meta, sourceUrl, isVideo, ref, onExpandStateChange }: FoldViewProps) {
  const { preamble, sections } = useMemo(() => foldSections(content), [content])

  /**
   * 当前展开的节（`key` 集合）。
   * ⚠️ **内容变了就清空**：切版本、切文档都会换掉 `content`，此时旧的 key 可能在新
   * 内容里指向另一节 —— 留着会让「换了个版本，某一节自己开着」。走 effect 而不是在
   * 每个调用点手动重置，是因为 `content` 的写入点有七八处（生成 / 切版本 / 切文档 /
   * 流式完成），漏一处就是上面那个症状。
   */
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  useEffect(() => { setOpen(new Set()) }, [content])

  const toggle = useCallback((key: string) => {
    setOpen(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const items: FoldSection[] = useMemo(
    () => (preamble ? [preamble, ...sections] : sections),
    [preamble, sections],
  )

  /**
   * 「全部展开」只展开真的会多出东西的节（`hasBody && expandable`）。
   * ⚠️ 名单与卡片上的可点判据是**同一对条件**：只按 `sections` 全展开的话，
   * 只剩标题的节会变成一张空卡、只有一句话的节展开前后逐字一样 —— 都是
   * 「看起来展开了、其实什么也没多」。
   */
  const openableKeys = useMemo(
    () => items.filter(sec => sec.hasBody && sec.expandable).map(sec => sec.key),
    [items],
  )

  useImperativeHandle(ref, () => ({
    expandAll: () => setOpen(new Set(openableKeys)),
    collapseAll: () => setOpen(new Set()),
  }), [openableKeys])

  useEffect(() => {
    onExpandStateChange?.({
      expandable: openableKeys.length,
      expanded: openableKeys.filter(k => open.has(k)).length,
    })
  }, [openableKeys, open, onExpandStateChange])

  // 没有 H2 时折叠无从谈起 —— 回落整篇。`PreviewArea` 的 `canFold` 已经挡了一道，
  // 这里再挡一道是为了「调用方换了判据」时不至于渲染出一片空白。
  if (sections.length === 0) {
    return <MarkerContent content={content} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
  }

  return (
    // `data-skeleton="card"` 只挂在主题档：让 `.md-card` 吃到 8 套视觉方案
    // （与骨架轴同一批 `--card-*` 变量）。档位优先于骨架轴 —— 主题档就是卡片，
    // 不看用户在设置里选的 paper / card。
    <div className="fold-root" data-fold={variant} data-skeleton={variant === 'topic' ? 'card' : undefined}>
      <div className={variant === 'topic' ? 'fold-grid' : 'fold-leads'}>
        {items.map(sec => {
          const isOpen = open.has(sec.key)
          // 可点 = 展开后真的会多出东西。两条都不够单独用：`hasBody` 只说「有内容」，
          // 「只有一句话」的节展开前后逐字一样；`expandable` 不说「有没有内容」，
          // 只剩标记行的节也会被判可展开。两者同时成立才绑点击。
          const openable = sec.hasBody && sec.expandable && !isOpen
          const onSelect = () => toggle(sec.key)

          return (
            <section
              key={sec.key}
              className={
                variant === 'topic'
                  // 只有标题的节做成**整行分隔条**：它没有内容，占满一个格子会让网格
                  // 里出现一张空卡，读起来像渲染坏了。
                  // `fold-preamble`：引言抽出的 `# 文档标题` 不是节标题，整行一条、字号大一档
                  ? `md-card fold-card${isOpen ? ' is-open' : ''}${sec.hasBody ? '' : ' fold-hollow'}${sec.key === 'pre' ? ' fold-preamble' : ''}`
                  : `fold-row${sec.key === 'pre' ? ' fold-preamble' : ''}`
              }
              data-openable={openable ? 'true' : undefined}
              role={openable ? 'button' : undefined}
              tabIndex={openable ? 0 : undefined}
              onClick={openable ? onSelect : undefined}
              onKeyDown={openable
                ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() }
                  }
                : undefined}
            >
              {/* 标题与「展开 / 收起」同在标题行 —— 动作标识必须贴在被作用的那一行上，
                  放在正文末尾时用户读到底部才找得到它。 */}
              <div className="fold-head">
                {sec.heading
                  ? <h2 className="fold-title" id={sec.headingId || undefined}>{sec.heading}</h2>
                  : null}
                {isOpen
                  ? (
                    <button
                      type="button"
                      className="fold-close"
                      onClick={(e) => { e.stopPropagation(); toggle(sec.key) }}
                    >
                      <ChevronUp size={14} aria-hidden />收起
                    </button>
                  )
                  : openable
                    // 纯提示，不参与交互：section 自己已经是 role=button，
                    // 里面再放一个可聚焦元素会让键盘出现两个焦点站
                    ? <span className="fold-hint" aria-hidden><ChevronDown size={14} /></span>
                    : null}
              </div>
              {isOpen
                ? (
                  <div className="fold-body">
                    <MarkerContent content={sec.body} meta={meta} sourceUrl={sourceUrl} isVideo={isVideo} />
                  </div>
                )
                : sec.lead
                  ? <p className="fold-lead">{sec.lead}</p>
                  // 抽不出一句话（整节是图片/表格/公式）：说明里面是什么，
                  // 否则卡片上只有一个标题，看起来像渲染坏了
                  : sec.hint ? <p className="fold-lead fold-lead--kind">{sec.hint}</p>
                  : null}
            </section>
          )
        })}
      </div>
    </div>
  )
}
