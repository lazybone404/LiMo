/**
 * 对话侧的语义标记渲染 —— `MessageItem` 的**助手正文**专用。
 *
 * ⚠️ **为什么不复用 `preview/MarkerContent.tsx`**：那个内部硬编码 `MarkdownPreview`，
 * 它带 `components` prop（代码高亮 / 图片 zoom / 标题锚点 id / 时间戳 badge / 链接安全校验），
 * 是**产物**的渲染路径。而对话正文是裸 `ReactMarkdown`（**没有** `components`）——
 * 复用等于把全部历史消息的正文换成产物渲染路径：语法高亮凭空出现、间距与表格全变，
 * 是**波及所有历史消息的静默回归**（不是「新消息样式不同」这种一眼能看出的差别）。
 *
 * ⚠️ **共享的是判据，不是渲染器**（与后端 `render_blocks_html(md, render_fragment)`
 * 「渲染交给调用方」同款纪律）：分段复用 `parseMarkers`，切卡复用 `splitByHeading`。
 * 「块类型 → 渲染形态」的映射是本文件自己的事 —— 与 `MarkerContent` 各有一份
 * **是设计**：两侧的块类型集合刻意不相交（`ARTIFACT_BLOCK_TYPES` / `CHAT_BLOCK_TYPES`）。
 *
 * ⚠️ **不认识自己的类型一律走 `ChatText`**（含产物侧 `key-takeaway` 与任何未知 type）：
 * 内容原样吐出、零丢失。判据是「内容永远不该因为类型不认识而消失」。
 *
 * ⚠️ **七种块**（2026-10-12：原四种 + `caution` / `how-to` / `quote`）分两档：
 * `card-group` / `follow-up` **要解析子结构**（`###` 切卡 / 逐行取问题）；其余五种
 * **不解析**，块内就是普通 Markdown，形态差异全在外壳（图标 + 语义色）。
 *
 * ⚠️ **刻意不用 `md-block` 类名** —— 那会命中 `blocks.css` 的产物强调块样式
 * （左边框 + 淡底 + 16px 外边距 + 圆角）。对话要紧凑，且「对话里的块」与
 * 「产物里的块」本就该是两种观感（产品场景不同）。对话样式全在 `styles/chat-blocks.css`。
 */

import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import { AlertTriangle, Check, ChevronDown, ChevronRight, ClipboardList, Copy, Info, ListOrdered, Quote, RefreshCw } from 'lucide-react'
import { parseMarkers } from '../../lib/markdown-markers'
import { splitByHeading } from '../../lib/markdown-sections'
import { suppressTrailingPartialMarker } from '../../lib/markdown-scan'
import { parseFollowUpItems } from '../../lib/chat-block-content'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'
import { useChatStore } from '../../store/chatStore'
import { useStore } from '../../store'

interface ChatMarkerContentProps {
  /** `msg.content` 原文 */
  content: string
  /** 该条消息是否仍在流式接收 */
  streaming: boolean
  /** 正文为空时的占位文案（沿用 `MessageItem` 现状：状态条或 `…`） */
  statusText: string
}

/** 「复制这块内容」按钮 —— `ChatPre` 与各内容卡**共用**（2026-10-12）。
 *
 * ⚠️ 复制的是 **DOM `textContent`**（渲染后的文本），不是 Markdown 原文：与 `ChatPre`
 * 改造前的行为一致（那边也没有 Markdown 源可拿）。对话里的复制用途是「直接拿去用」，
 * 渲染后的文本正是要用的那份；代价是围栏 / 表格竖线这类**结构记号**不出现在结果里。
 *
 * ⚠️ **显隐由调用方透传的 `className` 决定**，本组件不预设：代码块靠 hover 浮现
 * 是因为它占地明显，卡片则常驻 —— 那是布局决策，不是「复制」这件事的一部分。
 * ⚠️ `getText` 而不是 `text`：复制时才取 `textContent`，避免每次渲染都读一次 DOM。 */
function ChatCopyButton({ getText, label, className }: {
  getText: () => string
  label: string
  className: string
}) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(getText()).then(
      () => { setCopied(true); window.setTimeout(() => setCopied(false), 1500) },
      () => useStore.getState().addToast('复制失败，请手动复制', 'error'),
    )
  }
  return (
    <button type="button" onClick={copy} title={label} aria-label={label} className={className}>
      {copied ? <Check size={12} /> : <Copy size={12} />}
    </button>
  )
}

/** react-markdown 传给 `pre` 的 children 是那个 `<code>` 元素（语言标在它的 className 上） */
interface CodeChild { props?: { className?: string } }

/** 代码块 —— 外壳（圆角 + 底色由 CSS 给）+ 语言标签 + 一键复制（2026-10-12）。
 *
 * ⚠️ **挂 `pre` 而不是 `code`**：`react-markdown` v10 已移除 `inline` prop，
 * 块级代码是 `pre > code`，而**行内**代码是裸 `code` —— 挂 `code` 会给每个 `\`x\``
 * 也配一个复制按钮。
 *
 * ⚠️ **刻意不复用产物侧的 `markdown-components.tsx::CodeBlock`**：那个带
 * `SyntaxHighlighter`（语言注册 + 主题）+ 标题栏 + 行号，是**产物**的渲染路径。
 * 挂进来 = 把全部历史消息的代码块换成另一套观感 —— 与本文件开头那条
 * 「不复用 `preview/MarkerContent`」要挡的是同一类静默回归。
 * 这里只要「原样的 `<pre>` + 一个按钮」，于是代码文本就是 `textContent`，
 * 不必从 React children 递归提取。
 *
 * ⚠️ **语言标签只能从 `children` 取**（不能从 `<pre>` 的 props）：react-markdown 把
 * `className="language-xxx"` 挂在 `<code>` 上。取不到就**不显示标签** —— 没有语言
 * 信息时写一个「text」是编造。
 *
 * ⚠️ **不转发 react-markdown 给的 `props` 到 `<pre>`**：它会塞一个 `node`（hast 节点）
 * 进来，展开到 DOM 就是 React 的 unknown-prop 警告。需要给 `<pre>` 加类名时**自己写**
 * （下面那个 `chat-has-lang` 就是），别把整包 props 摊上去。
 * ⚠️ **语言标签是绝对定位的、会压住代码首行**，故由 `chat-has-lang` 给它腾出一行的
 * `padding-top`（CSS 里那条 `:has()` 的替代方案 —— 少一个宿主环境依赖）。
 *
 * ⚠️ **流式中不显示按钮**：代码还在往后长，复制到的会是半截内容。
 * ⚠️ 按钮用 `opacity` 显隐而**不是** `hidden` —— 后者会让代码块在 hover 时高度跳动；
 * `focus-visible` 一并放行，键盘用户 tab 到它仍可见（消息操作栏的「复制」是另一条兜底）。
 * ⚠️ 与卡片上那个常驻按钮**刻意不同**（那边不 hover 浮现，理由见 `ChatCard`）：
 * 代码块本身占满整行、块内每处都是内容，常驻图标会一直压在代码上。 */
function ChatPre({ children, streaming }: { children?: ReactNode; streaming: boolean }) {
  const ref = useRef<HTMLPreElement>(null)
  const lang = (children as CodeChild | undefined)?.props?.className?.match(/language-([\w+#.-]+)/)?.[1]
  return (
    <div className="group/code relative">
      {lang && <span className="chat-code-lang">{lang}</span>}
      {!streaming && (
        <ChatCopyButton
          getText={() => ref.current?.textContent ?? ''}
          label="复制代码"
          className="absolute right-1.5 top-1.5 z-10 rounded-md border border-border-light bg-surface p-1 text-dimmed opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover/code:opacity-100"
        />
      )}
      <pre ref={ref} className={lang ? 'chat-has-lang' : undefined}>{children}</pre>
    </div>
  )
}

/** 一段普通正文。⚠️ 严格沿用改造前 `MessageItem` 里那段 `ReactMarkdown`（含打字机 key 语义） */
function ChatText({ content, streaming, statusText }: ChatMarkerContentProps) {
  // ⚠️ 依赖 `streaming`：流式结束的那一帧要重新渲染出复制按钮（同时上面的 `key`
  // 也从 `loading` 变 `done`、整棵子树重挂，故不必操心组件引用稳定）。
  const components = useMemo(
    () => ({ pre: ({ children }: { children?: ReactNode }) => <ChatPre streaming={streaming}>{children}</ChatPre> }),
    [streaming],
  )
  return (
    <ReactMarkdown key={streaming ? 'loading' : 'done'} remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={components}>
      {normalizeLatexDelimiters(content || (streaming ? statusText || '…' : ''))}
    </ReactMarkdown>
  )
}

/** `card-group` 里的**一张卡** —— 内容 + 一个「复制这张卡」按钮（2026-10-12）。
 *
 * ⚠️ 按钮**常驻**而不是 hover 浮现：卡里是一段完整、可整块搬走的内容，而 hover
 * 浮现意味着它在触摸屏上**永不出现**。代价是右上角多一个小图标，故用低对比色 +
 * 指针经过才转前景色（`chat-blocks.css`）压住存在感。 */
function ChatCard({ content, streaming }: ChatMarkerContentProps) {
  const ref = useRef<HTMLDivElement>(null)
  return (
    <div className="chat-card" ref={ref}>
      {!streaming && (
        <ChatCopyButton
          getText={() => ref.current?.textContent ?? ''}
          label="复制这张卡"
          className="chat-card-copy"
        />
      )}
      {/* ⚠️ 保留 `### ` 前缀原样交给渲染器 —— 卡内仍是那条 `<h3>`，
          紧凑靠 CSS 覆盖，**不新起第二条标题渲染路径**（与 `splitByH2` 同一纪律） */}
      <ChatText content={content} streaming={streaming} statusText="" />
    </div>
  )
}

/** `card-group`：块内每个 `###` 一张卡，并排可扫 */
function ChatCardGroup({ content, streaming }: ChatMarkerContentProps) {
  const { preamble, sections } = useMemo(() => splitByHeading(content, 3), [content])
  // ⚠️ **空壳防护**：没有 `###` 就不是卡片组（模型写残了 / 用了 `####`）。
  // 此时**不画空网格**，整块退化成普通正文 —— 内容一字不丢。
  if (sections.length === 0) return <ChatText content={content} streaming={streaming} statusText="" />

  return (
    <div className="chat-card-group" data-block-type="card-group">
      {/* 首个 `###` 之前的引言（通常没有，有就不能丢） */}
      {preamble.trim() !== '' && <ChatText content={preamble} streaming={streaming} statusText="" />}
      <div className="chat-cards">
        {sections.map((sec, i) => (
          <ChatCard key={i} content={`${sec.heading}\n${sec.body}`} streaming={streaming} statusText="" />
        ))}
      </div>
    </div>
  )
}

/** 追问 chips —— **两个数据来源共用的唯一渲染器**。
 *
 * ⚠️ **它是全仓唯一的 chip 发生地**（`chat-followup-chip` 只在本文件出现）：
 * 补调来的（`msg.followUps` 数组，见 `MessageItem`）与模型**自主写**在正文里的
 * （`follow-up` 块 → 下面的 `ChatFollowUp`）走的是同一个组件。各写一份就是第二份
 * 实现（模式 #54），而两份 chip 的观感分叉是**必然**的。
 *
 * ⚠️ **折叠状态是组件本地 state、不持久化**：它是瞬时的阅读偏好（「这一轮我不关心
 * 追问」）。分页窗口卸载重挂会复位，可接受 —— 重挂是切会话/翻页，用户此时看的是别处。
 * ⚠️ **两个来源各自独立折叠**：它们是两个组件实例，state 天然隔离、不会互相带动。
 *
 * ⚠️ **默认展开**：开关的承诺是「选了一定有」，默认收起会让这份承诺看不出来。
 *
 * ⚠️ **三个可选 prop（2026-10-11 / 2026-10-12，产物侧复用）**：`label` / `onSelect` /
 * `onRefresh` 缺省时行为**逐字不变** ⇒ 产物侧零回归。产物侧（预览区笔记末尾）带
 * `label="继续追问"` 与自己的 `onSelect`（要点：它必须**显式传 docRefs**，而对话侧
 * 那个刻意不传），**不传 `onRefresh`** —— 它没有可重跑的上游
 * （见 `chatStore.refreshFollowUps` 的准入判据）。
 */
export function ChatFollowUpList({ items, streaming, label = '继续追问', onSelect, onRefresh }: {
  items: string[]; streaming: boolean; label?: string; onSelect?: (q: string) => void
  /** 「换一批」——**缺省不渲染按钮**。传了就该由调用方负责重新生成并落库 */
  onRefresh?: () => Promise<void> | void
}) {
  const [collapsed, setCollapsed] = useState(false)
  // ⚠️ 生成中禁用按钮而不是隐藏：隐藏会让标签行宽度在每个来回跳动，
  //    而「正在换」这件事本身需要被看见（否则重试像没反应）。
  const [refreshing, setRefreshing] = useState(false)
  // ⚠️ **空数组不渲染空壳**：否则会留下一个「继续追问 · 0」的标签挂在回答末尾。
  // ⚠️ 这句必须在**全部** `useState` **之后**（hooks 顺序）—— 它挡的是渲染、不是状态。
  if (items.length === 0) return null

  const handleRefresh = async () => {
    if (!onRefresh || refreshing) return
    setRefreshing(true)
    try { await onRefresh() } finally { setRefreshing(false) }
  }

  return (
    <div className="chat-followup" data-block-type="follow-up">
      {/* ⚠️ 标签行是 flex 容器（2026-10-12）：`继续追问` 与 `换一批` 一左一右各占一端。
          两个按钮并列在同一个 `.chat-followup` 里，折叠开关仍是**整条标签**。 */}
      <div className="chat-followup-head">
        {/* ⚠️ `<button>` 而不是 `<div>`：它是折叠开关（2026-10-11）。外观全靠
            `chat-blocks.css` 里那串 UA 默认值覆盖 —— 少写 `font: inherit` 就会
            与正文的字体族/字重不一致（看起来像另一套 UI）。 */}
        <button
          type="button"
          className="chat-followup-label"
          data-followup-collapse
          aria-expanded={!collapsed}
          onClick={() => setCollapsed(v => !v)}
        >
          {collapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />}{label} · {items.length}
        </button>
        {onRefresh && (
          <button
            type="button"
            className="chat-followup-refresh"
            // ⚠️ 与 chip 同款：流式中禁用（新一批会挂到正在变的内容上）。
            disabled={streaming || refreshing}
            onClick={handleRefresh}
            title="换一批"
            aria-label="换一批"
          >
            <RefreshCw size={10} className={refreshing ? 'animate-spin' : undefined} />换一批
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="chat-followup-items">
          {items.map((q, i) => (
            <button
              key={i}
              type="button"
              disabled={streaming}
              // ⚠️ 不传 `docRefs`：这是**新问题**不是 @ 引用。
              // 「当前选中文档」由 `dispatchSend` 自动带上（`chatStore.ts` 的 docIds 组合），语义正确。
              // ⚠️ 流式中重复点击由 `dispatchSend` 首行 `if (s.streaming) return` 兜底，
              // `disabled` 只是让这一点**看得出来**。
              // ⚠️ 产物侧传 `onSelect`（要显式带 docRefs）—— 两边共用同一个 chip
              // 组件、不同的「发问入口」，不新起第二份实现（模式 #54）。
              onClick={() => (onSelect ? onSelect(q) : useChatStore.getState().sendMessage(q))}
              className="chat-followup-chip"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** `follow-up` 块：块内每行一个问题（模型**自主写**在正文里的那条路）。
 *
 * ⚠️ 解析不出列表项（空块 / 纯段落）→ **退化成普通正文**（内容一字不丢），
 * 不画空壳 —— 与 `ChatCardGroup` 同款纪律。 */
function ChatFollowUp({ content, streaming }: ChatMarkerContentProps) {
  const items = useMemo(() => parseFollowUpItems(content), [content])
  if (items.length === 0) return <ChatText content={content} streaming={streaming} statusText="" />
  return <ChatFollowUpList items={items} streaming={streaming} />
}

/** 「图标 + 正文」的**通用外壳** —— `info` / `digest` / `caution` / `how-to` / `quote` 共用。
 *
 * ⚠️ 抽壳的理由**不是省几行**，而是「复制按钮挂在哪几个块上」必须在一处决定：
 * 五块各写一遍 DOM 的话，「四个有按钮、一个漏了」是没人会发现的缺口 —— 五个组件
 * 看起来都正常，差别只在某一行没写。
 *
 * ⚠️ `copyLabel` 缺省 = **不渲染复制按钮**。`info` / `digest` 刻意不传：它们的内容短
 * （一句定性 / 几条要点），复制的价值低于它在气泡里占的那点注意力。这是**有意的取舍**，
 * 不是漏了 —— 另外三个（风险 / 步骤 / 引用）都是「要整段搬走」的东西。
 *
 * ⚠️ 五个块**一律不做子结构解析**（块内就是普通 Markdown，整块交给 `ChatText`）：
 * 于是不存在「解析不出 → 退化」的分支。没有可解析的东西，就没有解析失败。
 * 形态差异全在外壳（图标 + 语义色），与产物侧那三个是同一条思路。
 * ⚠️ `how-to` 的编号靠块内**有序列表**（CSS 给 `<ol>` 样式），那是 Markdown 的自然
 * 产物、不是本组件解析出来的 —— 模型写成无序列表时它退化成普通列表，**不是 bug**。 */
function ChatIconBlock({ cls, type, icon, copyLabel, content, streaming }: ChatMarkerContentProps & {
  cls: string
  type: string
  icon: ReactNode
  copyLabel?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  return (
    <div className={`chat-block ${cls}`} data-block-type={type} ref={ref}>
      <span className="chat-block-icon" aria-hidden="true">{icon}</span>
      <div className="chat-block-body">
        <ChatText content={content} streaming={streaming} statusText="" />
      </div>
      {copyLabel && !streaming && (
        <ChatCopyButton
          getText={() => ref.current?.textContent ?? ''}
          label={copyLabel}
          className="chat-block-copy"
        />
      )}
    </div>
  )
}

/** `info`：讲清一个对象（一个概念 / 一个工具 / 一份来源） */
function ChatInfoCard(props: ChatMarkerContentProps) {
  return <ChatIconBlock {...props} cls="chat-info-card" type="info" icon={<Info size={14} />} />
}

/** `digest`：长回答开头的整体印象（2～5 条要点） */
function ChatDigestCard(props: ChatMarkerContentProps) {
  return <ChatIconBlock {...props} cls="chat-digest-card" type="digest" icon={<ClipboardList size={14} />} />
}

/** `caution`：提醒一件**要读者采取行动**的事（风险 / 易错点 / 前提条件）。
 *
 * ⚠️ 图标用 `--tk-warning`（**不是** `--tk-error`）：严重度由**文字**承担，
 * 颜色不该替它分级 —— 与产物侧 `alert` 的判据同源（`blocks.css`）。
 * ⚠️ 图标是 `AlertTriangle` 而不是「产物侧那个 type 名 `alert`」：两侧类型集合刻意
 * 不相交，名字也不借用（`warning` 另被 fixture 当「未知 type」的示例占据）。 */
function ChatCautionCard(props: ChatMarkerContentProps) {
  return <ChatIconBlock {...props} cls="chat-caution" type="caution"
    icon={<AlertTriangle size={14} />} copyLabel="复制注意事项" />
}

/** `how-to`：一组**有先后顺序**的操作步骤（块内是有序列表，编号由 CSS 给） */
function ChatHowTo(props: ChatMarkerContentProps) {
  return <ChatIconBlock {...props} cls="chat-howto" type="how-to"
    icon={<ListOrdered size={14} />} copyLabel="复制步骤" />
}

/** `quote`：引用一段原文或他人的观点（块内是 `>` 引用 + 出处行） */
function ChatQuote(props: ChatMarkerContentProps) {
  return <ChatIconBlock {...props} cls="chat-quote" type="quote"
    icon={<Quote size={14} />} copyLabel="复制引用" />
}

export function ChatMarkerContent({ content, streaming, statusText }: ChatMarkerContentProps) {
  // ⚠️ 抑制只作用于**渲染用**的字符串 —— `content` 本身（消息本体 / 存储 / 复制 / 导出）
  // 一字不动。半截标记行只可能出现在末尾（chunk 只向后追加）。
  // ⚠️ 无需抑制时 `suppressTrailingPartialMarker` **原样返回入参引用**，于是下面的
  // `useMemo` 不重算、快路径与改造前逐字相同 —— 零回归是**可断言的**，不是「看起来一样」。
  const text = streaming ? suppressTrailingPartialMarker(content) : content
  const segments = useMemo(() => parseMarkers(text), [text])

  // ⚠️ **快路径**：无标记 = 单个 text 段 —— 绝大多数消息（尤其全部历史消息）走这里。
  // 产出与改造前**逐字相同**的树（同一个 key 语义、同一个 normalizeLatexDelimiters）。
  if (segments.length === 1 && segments[0].kind === 'text') {
    return <ChatText content={text} streaming={streaming} statusText={statusText} />
  }

  return (
    <>
      {segments.map((seg, i) => {
        // ⚠️ 空段丢掉：否则一个恰好被标记切出来的空壳会渲染出占位符（「…」冒在正文中间）。
        // 与 `MarkerContent` 同款处理。
        if (seg.content.trim() === '') return null
        // 每段内容都非空（上面已滤），故 `statusText` 占位不会在这里生效
        const props: ChatMarkerContentProps = { content: seg.content, streaming, statusText }
        if (seg.kind === 'text') return <ChatText key={i} {...props} />
        if (seg.type === 'card-group') return <ChatCardGroup key={i} {...props} />
        if (seg.type === 'follow-up') return <ChatFollowUp key={i} {...props} />
        if (seg.type === 'info') return <ChatInfoCard key={i} {...props} />
        if (seg.type === 'digest') return <ChatDigestCard key={i} {...props} />
        if (seg.type === 'caution') return <ChatCautionCard key={i} {...props} />
        if (seg.type === 'how-to') return <ChatHowTo key={i} {...props} />
        if (seg.type === 'quote') return <ChatQuote key={i} {...props} />
        // 其余（产物侧类型 / 未知 type）一律当普通正文 —— 零残留
        return <ChatText key={i} {...props} />
      })}
    </>
  )
}
