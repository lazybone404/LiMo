/**
 * ChatInput — 对话输入区。
 *
 * textarea 多行自动增高 + IME 处理（修复中文输入法 Enter 确认拼音误发送）
 * + Enter 发送 / Shift+Enter 换行 + 流式中显示停止按钮。
 *
 * @ 文档引用：AtSign 按钮弹出文档选择浮层，选中文档 → docRef chips（本条消息引用）。
 * 发送时 docRefs 随 sendMessage 传入，docIds = 当前选中文档 + 本条引用（chatStore 组合去重）。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  Send, Square, AtSign, FileText, X, MessageCircleQuestion, Globe, ScanSearch,
  BookOpen, Sparkles, Wand2, Check, SlidersHorizontal,
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

/**
 * 回答设置浮层里的一行开关：图标 + 名字 + 一句话说明 + 复选框，**整行可点**。
 *
 * ⚠️ 用 `<label>` 包住 `<input type="checkbox">` 而不是「按钮里塞一个复选框」：
 * 复选框自己消费点击，套在 button 里会被内层吞掉一次、外层再响应一次（一次点击
 * 连切两下 = 看起来没反应）。label 的默认行为就是「点哪都切换同一个复选框」。
 * ⚠️ 关掉时**连说明文字一起变暗并写明原因**（比如「需先开启上面的联网」），
 * 而不是只把复选框置灰 —— 置灰但不说为什么，用户只能去猜。
 */
function PrefRow({ icon, label, desc, checked, disabled, attr, onChange }: {
  icon: ReactNode
  label: string
  desc: string
  checked: boolean
  disabled?: boolean
  /** 测试与无障碍用的 `data-*`（形如 `{ 'data-websearch-toggle': 'on' }`） */
  attr: Record<string, string>
  onChange: (v: boolean) => void
}) {
  return (
    <label className={`flex items-start gap-2 rounded-lg px-2 py-1.5 transition-colors ${
      disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-surface-hover'}`}>
      <span className="mt-0.5 shrink-0 text-muted">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-[12px] text-dimmed">{label}</span>
        <span className="mt-0.5 block text-[10px] leading-snug text-disabled">{desc}</span>
      </span>
      <input type="checkbox" checked={checked} disabled={disabled}
        onChange={e => onChange(e.target.checked)}
        className="mt-1 h-3.5 w-3.5 shrink-0 accent-accent" {...attr} />
    </label>
  )
}

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
  // 2026-11-17 起第二个浮层是「回答设置」（联网 / 深度联网 / 追问建议 / 回答依据四组），
  // 不再是单独一个「回答依据」下拉 —— 横排五颗图标在浮窗宽度下把输入框挤成一条缝，
  // 而图标本身说不出「追问」「回答依据」这些名字（用户反馈：认不出来是哪一颗）。
  const [openMenu, setOpenMenu] = useState<'ref' | 'settings' | null>(null)
  const refOpen = openMenu === 'ref'
  const settingsOpen = openMenu === 'settings'
  // 派生的打开/关闭：JSX 与 effect 继续读这个名字，语义不变。
  const setRefOpen = (v: boolean) => setOpenMenu(v ? 'ref' : null)
  const [refSearch, setRefSearch] = useState('')
  // 三项发送偏好（`lib/chat-prefs.ts`）——⚠️ **订阅**而不是各自 `useState(初值)`：
  // 设置页是第二个入口，改完必须让这里跟着变（见 `useChatPrefs` 的注释）。
  // 真正的生效点仍是发送那一刻 `dispatchSend` 现读（那个位置要的是「此刻」）。
  const { followUp, webSearch, answerMode, deepWeb } = useChatPrefs()
  // 深度联网按钮**是否渲染**由后端能力决定（当前对话模型是否支持内置服务端工具）。
  // ⚠️ 前端不自己看模型名猜 —— 猜错的表现是「按钮在那儿，点了后端却回退普通模式」。
  const deepWebAvailable = useStore(st => st.capabilities?.chatDeepWebAvailable ?? false)
  const taRef = useRef<HTMLTextAreaElement>(null)
  // 输入区 + @ 浮层的共同祖先：判断「点在内部还是外部」的界
  const rootRef = useRef<HTMLDivElement>(null)

  // ⚠️ 点浮层外关闭走 **document 监听**，不再是透明遮罩（2026-10-11 改）：
  // `FloatingAI` 的根 `<aside>` 带 `backdrop-blur`，它会**创建包含块**，把 `fixed`
  // 后代锁进浮窗自身的大小——`fixed inset-0` 遮罩实际只盖住对话窗那一块，点窗口外
  // 关不掉 @ 浮层（模式 #67）。浮层本身 `absolute bottom-full` 定位在这个输入区上，
  // Portal 到 body 会失去定位基准，故改为监听 document、用 `contains` 划内外界。
  // ⚠️ 两个浮层（@ 引用 / 回答设置）共用这一个监听：它们都锚在同一个 `rootRef` 上、
  // 都不该 Portal，各挂一个 document 监听只是让「什么时候该关」有两份实现。
  // ⚠️ **Esc 也挂在这里**：回答设置浮层里除了复选框没有需要 Esc 的控件，
  // 没有这条监听时它只能靠再点一次按钮或点外面关闭 —— 用户对弹出层的直觉是
  // 「Esc 关掉」，而这个直觉在隔壁 @ 浮层上恰好成立（那里有个 input 自带 onKeyDown），
  // 于是两隔壁行为不一致。收口到同一处即可，@ 浮层那条 input 上的监听保留无害。
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

  // ⚠️ `saveChatPrefs` 是**合并式**保存（参数 `Partial`），所以这里只写自己这一项 ——
  // 早先它是整对象覆盖，加第二个开关的那一刻就会「切 A 开关时把 B 开关重置回默认」，
  // 而那件事既没报错也没提示（用户的联网开关会自己打开）。合并搬进了函数内部。

  // 选档即落盘并收起浮层（与 @ 浮层选完文档就关一致）。
  // ⚠️ 不用 toggle —— 连点同一档不该变成「无档」，三档必须恒有一档。
  const chooseMode = (m: AnswerMode) => {
    saveChatPrefs({ answerMode: m })
    setOpenMenu(null)
    taRef.current?.focus()
  }

  // 「回答设置」图标是否点亮：四项里任何一项不是默认值就算（默认值是「改造前的现状」，
  // 见 lib/chat-prefs.ts 的默认值表）。点亮只说明「这里改过东西」，
  // 具体改了哪一项要打开浮层看 —— 图标替不了那四行文字。
  const settingsTouched = !webSearch || followUp || deepWeb || answerMode !== 'strict'

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

      {/* 回答设置浮层 —— 联网 / 深度联网 / 追问建议 / 回答依据四组。
          2026-11-17 之前是「四颗独立图标 + 一个回答依据下拉」并排，五颗横排会把
          浮窗变窄时的输入框挤成一条缝，而图标本身说不出每项叫什么（用户反馈：
          认不出是哪一颗）。收进一张浮层后，每项都有名字和一句话说明；
          开/关交给复选框，不再只靠图标底色的深浅 —— 那个差别用户看不出来。
          ⚠️ `absolute` 锚在输入区上、不 Portal（同 @ 浮层，见上面 effect 的注释）。 */}
      {settingsOpen && (
        <div className="absolute bottom-full left-3 z-10 mb-2 w-72 overflow-hidden rounded-xl border border-border-light bg-elevated p-1 shadow-xl" data-settings-panel>
          <div className="px-2 pb-0.5 pt-1 text-[11px] font-medium text-dimmed">回答设置</div>

          <PrefRow icon={<Globe size={14} />} label="联网" attr={{ 'data-websearch-toggle': webSearch ? 'on' : 'off' }}
            desc="本地文档不够时允许模型搜索互联网"
            checked={webSearch} onChange={v => saveChatPrefs({ webSearch: v })} />

          {deepWebAvailable && (
            <PrefRow icon={<ScanSearch size={14} />} label="深度联网"
              attr={{ 'data-deepweb-toggle': deepWeb && webSearch ? 'on' : 'off' }}
              desc={webSearch
                // ⚠️ 两个副作用都要写出来：服务端检索**不看**设置里的搜索方案
                // （用户配了 Tavily 也不会用），本轮也拿不到知识库三件套。
                // 只写「由服务端完成」时，前一件对用户是完全不可见的。
                ? '服务端检索，会忽略设置里的搜索方案；本轮不使用知识库工具'
                : '需先开启上面的「联网」'}
              checked={deepWeb && webSearch} disabled={!webSearch}
              onChange={v => saveChatPrefs({ deepWeb: v })} />
          )}

          <PrefRow icon={<MessageCircleQuestion size={14} />} label="追问建议"
            attr={{ 'data-followup-toggle': followUp ? 'on' : 'off' }}
            desc="每轮回答后给出可点击直发的追问"
            checked={followUp} onChange={v => saveChatPrefs({ followUp: v })} />

          <div className="my-1 border-t border-border-light" />
          <div className="px-2 pb-0.5 pt-1 text-[11px] text-dimmed">回答依据</div>
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
        {/* 回答设置：五项偏好（联网 / 深度联网 / 追问建议 / 回答依据）的唯一入口。
            ⚠️ `data-answer-mode` 挂在这里而不是某个选项上：它是「当前档位」的展示位，
            测试与无障碍读的都是它；具体三档在浮层里的 `data-answer-mode-option`。
            ⚠️ 点亮判据是「任一项偏离默认」，不是「某一项开着」—— 四项里任何一项改过都算，
            否则用户会以为「图标没亮 = 设置没变」。 */}
        <button onClick={() => setOpenMenu(m => (m === 'settings' ? null : 'settings'))}
          data-settings-toggle={settingsOpen ? 'open' : 'closed'}
          data-answer-mode={answerMode}
          title={`回答设置：${modeMeta.label}${webSearch ? '' : ' · 联网关'}`
            + `${deepWeb && webSearch ? ' · 深度联网开' : ''}${followUp ? ' · 追问开' : ''}`}
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors ${settingsTouched ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-surface-hover'}`}>
          <SlidersHorizontal size={s.btn} />
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
