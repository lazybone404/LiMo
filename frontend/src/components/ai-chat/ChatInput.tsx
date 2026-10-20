/**
 * ChatInput — 对话输入区。
 *
 * textarea 多行自动增高 + IME 处理（修复中文输入法 Enter 确认拼音误发送）
 * + Enter 发送 / Shift+Enter 换行 + 流式中显示停止按钮。
 *
 * @ 文档引用：AtSign 按钮弹出文档选择浮层，选中文档 → docRef chips（本条消息引用）。
 * 发送时 docRefs 随 sendMessage 传入，docIds = 当前选中文档 + 本条引用（chatStore 组合去重）。
 */
import { useEffect, useRef, useState } from 'react'
import {
  Send, Square, AtSign, FileText, X, MessageCircleQuestion, Globe,
  BookOpen, Sparkles, Wand2, Check,
} from 'lucide-react'
import { useChatStore, type DocRef } from '../../store/chatStore'
import { useStore, type DocEntry } from '../../store'
import { useChatPrefs, saveChatPrefs, ANSWER_MODE_OPTIONS, type AnswerMode } from '../../lib/chat-prefs'
import { SCALES, type ChatScale } from './scale'
import { estimateTokenLength, formatTokens } from '../../utils/token'

interface ChatInputProps {
  scale: ChatScale
}

/**
 * 「回答依据」三档 —— 用户看到的名字刻意**不是**内部 key，而是这一档到底怎么答。
 *
 * ⚠️ 三档做成**下拉选择**而不是像联网那样的两态切换按钮：三态按钮点一下循环一档，
 * 用户点两下就不知道自己在哪一档了（且误点一次要多点两次才回来）。
 * 下拉直接把三句话摆出来，选哪档一目了然。
 *
 * ⚠️ 描述文字写「库里没有时怎么办」—— 那是三档**唯一**的区别，也是用户真正在意的
 * 那个差别（「严格 vs 发散」这个说法本身太抽象）。
 */
/** 三档的图标 — **只属于这一处**的视觉细节（文案在 `lib/chat-prefs.ts`，两处共用） */
const MODE_ICONS: Record<AnswerMode, typeof BookOpen> = {
  strict: BookOpen, open: Sparkles, free: Wand2,
}
const ANSWER_MODES = ANSWER_MODE_OPTIONS.map(o => ({ ...o, Icon: MODE_ICONS[o.key] }))

export function ChatInput({ scale }: ChatInputProps) {
  const s = SCALES[scale]
  const streaming = useChatStore(st => st.streaming)
  const sendMessage = useChatStore(st => st.sendMessage)
  const stopGenerating = useChatStore(st => st.stopGenerating)
  const docs = useStore(st => st.docs)

  const [value, setValue] = useState('')
  const [composing, setComposing] = useState(false)
  const [docRefs, setDocRefs] = useState<DocRef[]>([])
  // ⚠️ **两个浮层共用一个「当前开着哪个」的选择器字段**（`'ref' | 'mode' | null`），
  // 不是两个独立 boolean：它们都 `absolute bottom-full` 锚在同一块输入区上，独立 boolean
  // 允许同时为真 ⇒ 两张浮层**精确重叠**，后渲染的那张盖住前一张，看上去像「点 @ 没反应、
  // 点开的是回答依据」（两个按钮紧挨着，用户根本分不清是谁开的）。
  // 改成单选后「同一时刻至多一张」由类型本身保证，不需要在每次 toggle 里手写互斥。
  const [openMenu, setOpenMenu] = useState<'ref' | 'mode' | null>(null)
  const refOpen = openMenu === 'ref'
  const modeOpen = openMenu === 'mode'
  // 派生的打开/关闭：JSX 与 effect 继续读这两个名字，语义不变。
  const setRefOpen = (v: boolean) => setOpenMenu(v ? 'ref' : null)
  const setModeOpen = (v: boolean) => setOpenMenu(v ? 'mode' : null)
  const [refSearch, setRefSearch] = useState('')
  // 三项发送偏好（`lib/chat-prefs.ts`）——⚠️ **订阅**而不是各自 `useState(初值)`：
  // 设置页是第二个入口，改完必须让这里跟着变（见 `useChatPrefs` 的注释）。
  // 真正的生效点仍是发送那一刻 `dispatchSend` 现读（那个位置要的是「此刻」）。
  const { followUp, webSearch, answerMode } = useChatPrefs()
  const taRef = useRef<HTMLTextAreaElement>(null)
  // 输入区 + @ 浮层的共同祖先：判断「点在内部还是外部」的界
  const rootRef = useRef<HTMLDivElement>(null)

  // ⚠️ 点浮层外关闭走 **document 监听**，不再是透明遮罩（2026-10-11 改）：
  // `FloatingAI` 的根 `<aside>` 带 `backdrop-blur`，它会**创建包含块**，把 `fixed`
  // 后代锁进浮窗自身的大小——`fixed inset-0` 遮罩实际只盖住对话窗那一块，点窗口外
  // 关不掉 @ 浮层（模式 #67）。浮层本身 `absolute bottom-full` 定位在这个输入区上，
  // Portal 到 body 会失去定位基准，故改为监听 document、用 `contains` 划内外界。
  // ⚠️ 两个浮层（@ 引用 / 回答依据）共用这一个监听：它们都锚在同一个 `rootRef` 上、
  // 都不该 Portal，各挂一个 document 监听只是让「什么时候该关」有两份实现。
  // ⚠️ **Esc 也挂在这里**：回答依据浮层里没有任何可聚焦控件（三档是 `<button>`，
  // 但按钮不消费 Esc），没有这条监听时它只能靠再点一次按钮或点外面关闭 —— 用户对
  // 弹出层的直觉是「Esc 关掉」，而这个直觉在隔壁 @ 浮层上恰好成立（那里有个 input
  // 自带 onKeyDown），于是两隔壁行为不一致。收口到同一处即可，@ 浮层那条 input 上的
  // 监听保留无害（Esc 在两个层面都会关掉同一个东西）。
  useEffect(() => {
    if (!openMenu) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return
      setOpenMenu(null)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenMenu(null) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [openMenu])

  // 上下文占用进度（C4）— 前端字符估算仅供参考，实际裁剪以后端 tiktoken 为准
  const messages = useChatStore(st => st.messages)
  const budget = useChatStore(st => st.budget)
  const contextTokens = messages.reduce((acc, m) => acc + estimateTokenLength(m.content), 0)
  const inputTokens = estimateTokenLength(value)
  const ctxRatio = budget && budget > 0 ? Math.min(1, contextTokens / budget) : 0

  // 每轮 token 用量 + 费用估算（C4；未配价格则不显示费用）
  const lastUsage = useChatStore(st => st.lastUsage)
  const priceIn = useChatStore(st => st.priceIn)
  const priceOut = useChatStore(st => st.priceOut)
  const cost = lastUsage && (priceIn != null || priceOut != null)
    ? ((lastUsage.prompt * (priceIn ?? 0)) + (lastUsage.completion * (priceOut ?? 0))) / 1e6
    : null

  const autoGrow = () => {
    const el = taRef.current
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 160) + 'px' }
  }

  const submit = () => {
    const text = value.trim()
    if (!text || streaming) return
    sendMessage(text, docRefs.length ? docRefs : undefined)
    setValue('')
    setDocRefs([])
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const filteredDocs = refSearch ? docs.filter(d => d.title.toLowerCase().includes(refSearch.toLowerCase())) : docs

  // 当前档（找不到时退回第一项 —— 枚举只有三个值，`??` 只是为了让类型收窄）
  const modeMeta = ANSWER_MODES.find(m => m.key === answerMode) ?? ANSWER_MODES[0]
  const ModeIcon = modeMeta.Icon

  // ⚠️ `saveChatPrefs` 是**合并式**保存（参数 `Partial`），所以这里只写自己这一项 ——
  // 早先它是整对象覆盖，加第二个开关的那一刻就会「切 A 开关时把 B 开关重置回默认」，
  // 而那件事既没报错也没提示（用户的联网开关会自己打开）。合并搬进了函数内部。
  const toggleFollowUp = () => saveChatPrefs({ followUp: !followUp })

  const toggleWebSearch = () => saveChatPrefs({ webSearch: !webSearch })

  // 选档即落盘并收起浮层（与 @ 浮层选完文档就关一致）。
  // ⚠️ 不用 toggle —— 连点同一档不该变成「无档」，三档必须恒有一档。
  const chooseMode = (m: AnswerMode) => {
    saveChatPrefs({ answerMode: m })
    setModeOpen(false)
    taRef.current?.focus()
  }

  const addRef = (doc: DocEntry) => {
    if (docRefs.some(r => r.docId === doc.id)) return
    setDocRefs([...docRefs, { docId: doc.id, title: doc.title }])
    setRefOpen(false)
    setRefSearch('')
    taRef.current?.focus()
  }

  return (
    <div ref={rootRef} className="relative shrink-0 border-t border-border-light p-3">

      {/* 本条消息引用的文档 chips */}
      {docRefs.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {docRefs.map(r => (
            <span key={r.docId} className="inline-flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
              <FileText size={10} /><span className="max-w-[140px] truncate">{r.title}</span>
              <button onClick={() => setDocRefs(docRefs.filter(x => x.docId !== r.docId))} className="text-accent/60 hover:text-accent" title="移除引用"><X size={10} /></button>
            </span>
          ))}
        </div>
      )}

      {/* @ 文档选择浮层 */}
      {refOpen && (
        <div className="absolute bottom-full left-3 right-3 z-10 mb-2 overflow-hidden rounded-xl border border-border-light bg-elevated shadow-xl">
          <div className="px-2 py-1.5">
            <input autoFocus value={refSearch} onChange={e => setRefSearch(e.target.value)} placeholder="搜索文档…"
              onKeyDown={e => { if (e.key === 'Escape') setRefOpen(false) }}
              className="w-full bg-transparent text-[12px] outline-none placeholder:text-disabled" />
          </div>
          <div className="max-h-48 overflow-y-auto border-t border-border-light">
            {filteredDocs.slice(0, 8).map(doc => (
              <button key={doc.id} onClick={() => addRef(doc)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-dimmed transition-colors hover:bg-surface">
                <FileText size={11} className="shrink-0 text-muted" />
                <span className="truncate">{doc.title}</span>
              </button>
            ))}
            {filteredDocs.length === 0 && <div className="px-3 py-3 text-center text-[11px] text-disabled">暂无文档</div>}
          </div>
        </div>
      )}

      {/* 回答依据浮层 —— 三档各占一行，直接把「库里没有时怎么办」写出来。
          ⚠️ `absolute` 锚在输入区上、不 Portal（同 @ 浮层，见上面 effect 的注释）。 */}
      {modeOpen && (
        <div className="absolute bottom-full left-3 z-10 mb-2 w-64 overflow-hidden rounded-xl border border-border-light bg-elevated p-1 shadow-xl">
          {ANSWER_MODES.map(({ key, label, desc, Icon }) => (
            <button key={key} onClick={() => chooseMode(key)} data-answer-mode-option={key}
              className={`flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-surface-hover ${key === answerMode ? 'bg-accent/5' : ''}`}>
              <Icon size={14} className={`mt-0.5 shrink-0 ${key === answerMode ? 'text-accent' : 'text-muted'}`} />
              <span className="min-w-0 flex-1">
                <span className={`block text-[12px] ${key === answerMode ? 'text-accent' : 'text-dimmed'}`}>{label}</span>
                <span className="mt-0.5 block text-[10px] leading-snug text-disabled">{desc}</span>
              </span>
              {key === answerMode && <Check size={12} className="mt-0.5 shrink-0 text-accent" />}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        <button onClick={() => { setOpenMenu(m => (m === 'ref' ? null : 'ref')); setRefSearch('') }} title="引用文档"
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors ${docRefs.length ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-surface-hover'}`}>
          <AtSign size={s.btn} />
        </button>
        {/* 追问建议开关：开启后每轮回答末尾给出可点击直发的问题。
            ⚠️ `title` 里必须写「点击即发送」——那是一颗 chip 的真实行为，
            不写清楚用户不敢点（看起来像普通文字）。 */}
        <button onClick={toggleFollowUp} data-followup-toggle={followUp ? 'on' : 'off'}
          title={followUp
            ? '追问建议已开启：每轮回答后给出 4～6 个追问，点击即发送'
            : '追问建议已关闭：开启后每轮回答都会给出追问（点击即发送）'}
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors ${followUp ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-surface-hover'}`}>
          <MessageCircleQuestion size={s.btn} />
        </button>
        {/* 联网开关：关掉后整组摘掉 search_web / fetch_url，模型只能看已导入的文档。
            ⚠️ 默认**开**（= 改造前的现状，见 `lib/chat-prefs.ts` 的默认值表）。 */}
        <button onClick={toggleWebSearch} data-websearch-toggle={webSearch ? 'on' : 'off'}
          title={webSearch
            ? '联网已开启：本地文档不够时模型可搜索互联网（点击关闭）'
            : '联网已关闭：模型只能使用已导入的文档（点击开启）'}
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors ${webSearch ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-surface-hover'}`}>
          <Globe size={s.btn} />
        </button>
        {/* 回答依据（三档）。⚠️ 图标随档变化 —— 光靠高亮分不出「当前是哪一档」，
            而这三档对用户的意义差别很大（尤其 free：它压根不查库）。 */}
        <button onClick={() => setOpenMenu(m => (m === 'mode' ? null : 'mode'))} data-answer-mode={answerMode}
          title={`回答依据：${modeMeta.label}（${modeMeta.desc}）—— 点击切换`}
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors ${answerMode === 'strict' ? 'text-muted hover:bg-surface-hover' : 'bg-accent/10 text-accent'}`}>
          <ModeIcon size={s.btn} />
        </button>
        <textarea
          ref={taRef}
          value={value}
          rows={1}
          onChange={e => { setValue(e.target.value); autoGrow() }}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey && !composing && !e.nativeEvent.isComposing) {
              e.preventDefault(); submit()
            }
          }}
          placeholder="输入问题… (Enter 发送，Shift+Enter 换行)"
          disabled={streaming}
          className={`flex-1 resize-none rounded-xl border border-border-default bg-surface px-3 py-2 font-mono ${s.input} outline-none placeholder:text-disabled disabled:opacity-50`}
          style={{ maxHeight: 160 }}
        />
        {streaming ? (
          <button onClick={stopGenerating} title="停止生成"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-error/10 text-error transition-all hover:bg-error/15">
            <Square size={s.btn} />
          </button>
        ) : (
          <button onClick={submit} disabled={!value.trim()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent transition-all hover:bg-accent-hover disabled:opacity-40">
            <Send size={s.btn} />
          </button>
        )}
      </div>

      {/* 上下文占用进度 + 输入 token（C4：估算仅供参考，实际裁剪以后端 tiktoken 为准） */}
      {(budget != null || value.trim() || messages.length > 0) && (
        <div className="mt-1 flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-surface-hover">
            <div className="h-full rounded-full bg-accent/50 transition-all" style={{ width: `${ctxRatio * 100}%` }} />
          </div>
          <span className="shrink-0 text-[10px] text-disabled">
            {formatTokens(contextTokens)}{budget ? `/${formatTokens(budget)}` : ''}
            {inputTokens > 0 && ` · 本条 ${formatTokens(inputTokens)}`}
            {lastUsage && ` · 本轮 ${formatTokens(lastUsage.prompt)}→${formatTokens(lastUsage.completion)}`}
            {cost != null && ` ≈¥${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}`}
          </span>
        </div>
      )}
    </div>
  )
}
