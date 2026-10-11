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
 * ⚠️ **刻意不用 `md-block` 类名** —— 那会命中 `blocks.css` 的产物强调块样式
 * （左边框 + 淡底 + 16px 外边距 + 圆角）。对话要紧凑，且「对话里的块」与
 * 「产物里的块」本就该是两种观感（产品场景不同）。对话样式全在 `styles/chat-blocks.css`。
 */

import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { ChevronDown, ChevronRight, ClipboardList, Info } from 'lucide-react'
import { parseMarkers } from '../../lib/markdown-markers'
import { splitByHeading } from '../../lib/markdown-sections'
import { suppressTrailingPartialMarker } from '../../lib/markdown-scan'
import { parseFollowUpItems } from '../../lib/chat-block-content'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'
import { useChatStore } from '../../store/chatStore'

interface ChatMarkerContentProps {
  /** `msg.content` 原文 */
  content: string
  /** 该条消息是否仍在流式接收 */
  streaming: boolean
  /** 正文为空时的占位文案（沿用 `MessageItem` 现状：状态条或 `…`） */
  statusText: string
}

/** 一段普通正文。⚠️ 严格沿用改造前 `MessageItem` 里那段 `ReactMarkdown`（含打字机 key 语义） */
function ChatText({ content, streaming, statusText }: ChatMarkerContentProps) {
  return (
    <ReactMarkdown key={streaming ? 'loading' : 'done'} remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS}>
      {normalizeLatexDelimiters(content || (streaming ? statusText || '…' : ''))}
    </ReactMarkdown>
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
          <div className="chat-card" key={i}>
            {/* ⚠️ 保留 `### ` 前缀原样交给渲染器 —— 卡内仍是那条 `<h3>`，
                紧凑靠 CSS 覆盖，**不新起第二条标题渲染路径**（与 `splitByH2` 同一纪律） */}
            <ChatText content={`${sec.heading}\n${sec.body}`} streaming={streaming} statusText="" />
          </div>
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
 * ⚠️ **两个可选 prop（2026-10-11，产物侧复用）**：`label` / `onSelect` 缺省时行为
 * **逐字不变** ⇒ 对话侧零回归。产物侧（预览区笔记末尾）带 `label="继续追问"` 与自己的
 * `onSelect`（要点：它必须**显式传 docRefs**，而对话侧那个刻意不传）。
 */
export function ChatFollowUpList({ items, streaming, label = '继续追问', onSelect }: {
  items: string[]; streaming: boolean; label?: string; onSelect?: (q: string) => void
}) {
  const [collapsed, setCollapsed] = useState(false)
  // ⚠️ **空数组不渲染空壳**：否则会留下一个「继续追问 · 0」的标签挂在回答末尾。
  // ⚠️ 这句必须在 `useState` **之后**（hooks 顺序）—— 它挡的是渲染、不是状态。
  if (items.length === 0) return null

  return (
    <div className="chat-followup" data-block-type="follow-up">
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

/** `info`：讲清一个对象（一个概念 / 一个工具 / 一份来源）。
 *
 * ⚠️ 与上面两个不同，它**不做子结构解析** —— 块内就是普通 Markdown，整块交给 `ChatText`。
 * 于是也不存在「解析不出 → 退化」的分支：没有可解析的东西，就没有解析失败。
 * 形态差异全在外壳（图标 + 语义色），与产物侧的三种是同一个思路。 */
function ChatInfoCard({ content, streaming }: ChatMarkerContentProps) {
  return (
    <div className="chat-info-card" data-block-type="info">
      <Info className="chat-block-icon" size={14} aria-hidden="true" />
      <div className="chat-block-body">
        <ChatText content={content} streaming={streaming} statusText="" />
      </div>
    </div>
  )
}

/** `digest`：长回答开头的整体印象（2～5 条要点）。
 *
 * ⚠️ 同 `info`：不解析子结构，块内是普通 Markdown（通常是有序 / 无序列表）。 */
function ChatDigestCard({ content, streaming }: ChatMarkerContentProps) {
  return (
    <div className="chat-digest-card" data-block-type="digest">
      <ClipboardList className="chat-block-icon" size={14} aria-hidden="true" />
      <div className="chat-block-body">
        <ChatText content={content} streaming={streaming} statusText="" />
      </div>
    </div>
  )
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
        // 其余（产物侧类型 / 未知 type）一律当普通正文 —— 零残留
        return <ChatText key={i} {...props} />
      })}
    </>
  )
}
