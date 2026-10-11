/**
 * 产物侧追问卡片 —— 生成面板勾「追问卡片」→ 笔记末尾出现可点的问题。
 *
 * 与 `chatFollowUp.test.tsx` 分家的理由：那个文件测的是**对话侧**（开关 → `follow_up=1`
 * → 后端补调 → `ChatMsg.followUps`）。本文件测**产物侧**：勾选项 → `formats=followup`
 * → 后端在 `done` **之后**推 `followup` 事件（带 `mode`/`version`）→ `markdownMeta.followUps`
 * → 预览区笔记末尾的 chip。
 *
 * 两侧共用同一个 chip 组件（`ChatFollowUpList`）—— 故本文件末尾有一组「对话侧零回归」
 * 的断言：新加的两个可选 prop 缺省时行为必须逐字不变。
 *
 * ⚠️ **本轮的失败模式全部是静默的**，所以每条守卫都配了对照实验（写在用例注释里）：
 *   - 事件被 `data.mode` 分支吞成正文 chunk → 笔记里冒出一个空 chunk，零报错
 *   - 追问被塞进 `content` → `.md` 导出把候选问句一起带走
 *   - 竞态覆盖 → 卡片闪过即消失
 */
/// <reference types="node" />
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { streamGenerate, type StreamGenerateCallbacks } from '../api/generate'
import { toMarkdownMeta } from '../lib/markdownMeta'
import { getTypeMeta, isFormatVisible } from '../components/gen-panel/constants'
import { GenerateOptions } from '../components/gen-panel/GenerateOptions'
import { ChatFollowUpList } from '../components/ai-chat/ChatMarkerContent'
import { useChatStore } from '../store/chatStore'

// ═══════════════════════════════════════════════════════════
// 1. SSE 分发 —— `followup` 必须走 `onFollowUp`，**不能**掉进 `data.mode` 那支
//
// ⚠️ 这是本设计最容易踩、且**零报错**的一格：本事件的 payload 恰好带 `mode`
// （产物侧要按版本落位），而 `api/generate.ts` 里 `else if (data.mode)` 那行的本意是
// 「带 mode 的正文 chunk」——顺序写反，追问就被当成正文塞进笔记（`text` 缺失 → 空 chunk）。
// 既有的 `stage` / `reasoning` 两支注释里已写明同款陷阱。
// ═══════════════════════════════════════════════════════════

/** 把事件数组编成一条完整的 SSE body（一次性入队 + 关闭） */
function sseBody(events: unknown[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  const text = events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('')
  return new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(enc.encode(text)); c.close() },
  })
}

/** 让 `parseSSEStream` 的 `while + await reader.read()` 跑完 */
const settle = async (rounds = 10) => {
  for (let i = 0; i < rounds; i++) await new Promise(r => setTimeout(r, 0))
}

interface Spy {
  chunks: [string, string?][]
  followUps: [string[], string | null | undefined][]
  errors: string[]
  done: number
  cb: StreamGenerateCallbacks
}

/** 用一条真实 SSE 流驱动**真实的** `streamGenerate`，把所有回调记下来 */
async function run(events: unknown[]): Promise<Spy> {
  const chunks: Spy['chunks'] = []
  const followUps: Spy['followUps'] = []
  const errors: string[] = []
  let done = 0
  const cb: StreamGenerateCallbacks = {
    onChunk: (t, m) => { chunks.push([t, m]) },
    onDone: () => { done += 1 },
    onError: m => { errors.push(m) },
    onCancelled: () => {},
    onFollowUp: (qs, err) => { followUps.push([qs, err]) },
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, body: sseBody(events) }) as unknown as Response))
  streamGenerate('doc1', { mode: 'notes', formats: 'followup' }, cb)
  await settle()
  return { chunks, followUps, errors, done, cb }
}

afterEach(() => { vi.unstubAllGlobals() })

describe('followup 事件的 SSE 分发', () => {
  const QS = ['第一个追问是什么', '第二个追问是什么']
  const followupEvt = { type: 'followup', mode: 'notes', version: 1, questions: QS, error: null }

  it('走 onFollowUp，且**一个 chunk 都没产生**', async () => {
    // 对照实验（已验证有区分力）：把 `api/generate.ts` 的 followup 分支挪到
    // `else if (data.mode)` **之后** → chunks 里会多出一条 `['', 'notes']`，本条红。
    const s = await run([{ text: '正文' }, { type: 'done' }, followupEvt])

    expect(s.followUps).toEqual([[QS, null]])
    expect(s.chunks.map(c => c[0])).toEqual(['正文'])   // 只有真正文那一条
    expect(s.done).toBe(1)
    expect(s.errors).toEqual([])
  })

  it('带 mode 的正文 chunk 仍走 onChunk（对照：证明 onChunk 通路是活的）', async () => {
    const s = await run([{ text: '笔记正文', mode: 'notes' }, { type: 'done' }])
    expect(s.chunks).toEqual([['笔记正文', 'notes']])
    expect(s.followUps).toEqual([])
  })

  it('补调失败：questions 为空数组、error 原样透传', async () => {
    const s = await run([
      { type: 'done' },
      { type: 'followup', mode: 'notes', version: 1, questions: [], error: '追问建议生成失败' },
    ])
    expect(s.followUps).toEqual([[[], '追问建议生成失败']])
  })

  it('追问推在 done 之后**不**触发「连接意外断开」（done 已置终态标记）', async () => {
    // ⚠️ `streamGenerate` 用 `receivedDoneOrError` + 流结束兜底报错。追问是**后补**的，
    //    若有人把那个守卫改成「以流关闭为准」（看起来更严谨），本用例会当场变红。
    const s = await run([{ text: '正文' }, { type: 'done' }, followupEvt])
    expect(s.errors).toEqual([])
  })

  it('没有 done 的流仍照常报「连接意外断开」（上一条不是恒真的）', async () => {
    const s = await run([{ text: '正文' }, followupEvt])
    expect(s.errors).toEqual(['连接意外断开，请重试'])
  })
})

describe('分发顺序 — 源码守卫', () => {
  /** 剥掉注释：源码里那两行本身就在注释中被反复讨论，不剥会被自己绊倒 */
  const src = readFileSync(resolve(process.cwd(), 'src/api/generate.ts'), 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')

  it('`data.type === \'followup\'` 分支出现在 `else if (data.mode)` **之前**', () => {
    const iFollowup = src.indexOf("data.type === 'followup'")
    const iMode = src.indexOf('} else if (data.mode) {')
    expect(iFollowup, '找不到 followup 分支 —— 守卫本身失效了').toBeGreaterThan(-1)
    expect(iMode, '找不到 data.mode 分支 —— 守卫本身失效了').toBeGreaterThan(-1)
    expect(iFollowup).toBeLessThan(iMode)
  })

  it('同款陷阱的两支（stage / reasoning）也在它之前 —— 三条是同一个约束', () => {
    const iMode = src.indexOf('} else if (data.mode) {')
    expect(src.indexOf("data.type === 'stage'")).toBeLessThan(iMode)
    expect(src.indexOf("data.type === 'reasoning'")).toBeLessThan(iMode)
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 数据链 —— `toMarkdownMeta` 是**唯一**映射落点（8 个调用点自动获得）
// ═══════════════════════════════════════════════════════════

describe('toMarkdownMeta — followUps 透传', () => {
  const base = {
    id: 'md_1', transcriptId: 'tx', mode: 'notes' as const, version: 1,
    createdAt: '2026-10-11T10:00:00', status: 'complete', content: '正文',
    chars: 2, edited: false, source: 'qwen-max', style: 'plain',
  }

  it('有追问 → 原样带出（预览区的 chip 全靠这一个字段）', () => {
    const meta = toMarkdownMeta({ ...base, followUps: ['问题一', '问题二'] })
    expect(meta.followUps).toEqual(['问题一', '问题二'])
  })

  it('没有追问 → `undefined`（**不是空数组** —— 空数组会让渲染器画出「继续追问 · 0」）', () => {
    const meta = toMarkdownMeta({ ...base })
    expect(meta.followUps).toBeUndefined()
  })

  it('空数组**原样透传**，不被合并成 undefined（两种「没有」是两件事）', () => {
    // ⚠️ 这条刻意与上一条相反，是 `markdownMeta.ts` 文件头明写的纪律：
    //    `undefined` = 后端没给这个键（这个版本没有追问）
    //    `[]`        = 后端明确说这批是空的（追问生成失败 / 结果为空）
    // 渲染条件统一走 `?.length`，两者都拦得住；但在这里合并等于**伪造数据** ——
    // 后端「有才给键」的语义会在前端被抹平，将来想区分「没跑过」与「跑了没结果」就没依据了。
    const meta = toMarkdownMeta({ ...base, followUps: [] })
    expect(meta.followUps).toEqual([])
  })
})

// ═══════════════════════════════════════════════════════════
// 3. 勾选项的可见性 —— 能力表给了就可见、不给就不显示
//
// `followup` 登记进后端 `AVAILABLE_OPTIONS` 才会经 capabilities 下发、被
// `availableFormats` 放行；不登记则「UI 不显示」+「提交时被 `.filter` 静默抹掉」
// 同时发生（P0-01 的成因）。**「后端表里有没有 followup」由 `getTypeMeta.test.ts`
// 打开 prompt.py 逐键比对**；这里钉的是前端这一侧：门控**不得有 per-format 分支**。
//
// ⚠️ 本组一律用**最小构造**（只给被测的那一项），不复制整张能力表 ——
//    复制一份镜像就等于多一个过期点（模式 #54）。
// ⚠️ 也**不能**写成 `getTypeMeta(st)` 不传表：caps 未就绪时 `availableFormats` 是
//    空表，两侧同为 false ⇒ 断言恒真（第一版正是这么写的，假绿）。
// ═══════════════════════════════════════════════════════════

describe('追问卡片的可见性', () => {
  it('能力表给了 → 可见', () => {
    const m = getTypeMeta('webpage', { webpage: ['toc', 'summary', 'chart', 'followup'] })
    expect(isFormatVisible('followup', m)).toBe(true)
  })

  it('能力表没给 → 不可见（上一条不是恒真的）', () => {
    const m = getTypeMeta('webpage', { webpage: ['toc', 'summary', 'chart'] })
    expect(isFormatVisible('followup', m)).toBe(false)
  })

  it('`images` 的 hasImages 特例**不牵连** followup', () => {
    // 这是 `isFormatVisible` 里唯一的 per-format 分支（`images` 额外要求 hasImages）。
    // 若有人把它写成 `f === 'images' || f === 'followup'`，这条会红 ——
    // 而「没有任何 per-format 分支」正是「登记即可见」这个结构性保证的全部依赖。
    // ⚠️ 用 `text` 构造：它的 `hasImages` 为假（拿 pdf/docx 构造则前提不成立、恒真）。
    const m = getTypeMeta('text', { text: ['followup'] })
    expect(m.hasImages).toBe(false)          // 前提自证
    expect(isFormatVisible('followup', m)).toBe(true)
    expect(isFormatVisible('images', m)).toBe(false)   // 对照：特例本身仍活着
  })
})

// ═══════════════════════════════════════════════════════════
// 4. 勾选项本身 —— 渲染 `GenerateOptions`
//
// 只断言三件事：标签在场、点了能把值交出去、**只生成整理版时它被禁用**。
// 最后一条是「缺失的 affordance」：后端 `stream_mode != "polished"` 会正确拦住，
// 但用户看不出来 —— 禁用 + 一句「追问仅笔记产出」才是显式的。
// ═══════════════════════════════════════════════════════════

/** `typeMeta` 必须由父组件（GenPanel）从下发的能力表构造 —— 组件**不自己查表**。
 *  表里只放被测项，其余项压根不渲染（本文件不管它们）。 */
const CAPS = { webpage: ['toc', 'summary', 'formulas', 'chart', 'followup'] }

/** 组件必填 prop 有 40+ 个；这里按「渲染时真会被读到」逐项给值。
 *  ⚠️ `userNotes` / `notesExpanded` 必须给 —— 备注区那块是无条件求值的
 *  （`p.userNotes.trim()` 在折叠按钮的第一行），少给就 `Cannot read properties of undefined`。 */
function optsProps(over: Record<string, unknown> = {}) {
  const noop = () => {}
  return {
    selectedModelId: 1, setSelectedModelId: noop,
    enabledModels: [{ id: 1, provider_id: 'p', model_name: 'm', capabilities: ['text'] }],
    providers: [], providerName: (s: string) => s,
    formatToc: false, setFormatToc: noop,
    formatLink: false, setFormatLink: noop,
    formatSummary: false, setFormatSummary: noop,
    formatScreenshot: false, setFormatScreenshot: noop,
    formatChart: false, setFormatChart: noop,
    formatFollowUp: false, setFormatFollowUp: noop,
    formatFormulas: false, setFormatFormulas: noop,
    formatReferences: false, setFormatReferences: noop,
    formatImages: false, setFormatImages: noop,
    imageAnalyze: false, setImageAnalyze: noop,
    imageOptimize: false, setImageOptimize: noop,
    ocrEnabled: false, setOcrEnabled: noop, ocrModelId: null, setOcrModelId: noop,
    hasImageModel: false, hasVideoModel: false, hasDocumentModel: false,
    docDirect: false, setDocDirect: noop,
    reasoningSupported: false, reasoningEffort: 'high' as const, setReasoningEffort: noop,
    style: 'plain', setStyle: noop, styleKeys: ['plain'],
    selectedCore: ['notes'], setSelectedCore: noop,
    videoUnderstanding: false, setVideoUnderstanding: noop,
    videoDirect: false, setVideoDirect: noop,
    videoInterval: 5, setVideoInterval: noop,
    gridCols: 3, setGridCols: noop, gridRows: 3, setGridRows: noop,
    dedupeEnabled: true, setDedupeEnabled: noop,
    unitWidth: 512, setUnitWidth: noop, unitHeight: 288, setUnitHeight: noop,
    saveQuality: 85, setSaveQuality: noop,
    frameQualityFilter: false, setFrameQualityFilter: noop,
    userNotes: '', setUserNotes: noop,
    notesExpanded: false, setNotesExpanded: noop,
    refFile: null, setRefFile: noop,
    typeMeta: getTypeMeta('webpage', CAPS),
    ...over,
  } as unknown as Parameters<typeof GenerateOptions>[0]
}

const followUpInput = (container: HTMLElement) =>
  [...container.querySelectorAll('input[type="checkbox"]')]
    .find(i => i.closest('label')?.textContent?.includes('追问卡片')) as HTMLInputElement | undefined

describe('GenerateOptions — 追问卡片勾选项', () => {
  it('勾选项在场，且带说明文案（用户要能看出它做什么）', () => {
    const { container } = render(<GenerateOptions {...optsProps()} />)
    expect(followUpInput(container)).toBeTruthy()
    expect(container.textContent).toContain('读完可点的问题，点了直接问 AI')
  })

  it('勾上把 true 交出去（是真的受控，不是画上去的）', () => {
    const setFormatFollowUp = vi.fn()
    const { container } = render(<GenerateOptions {...optsProps({ setFormatFollowUp })} />)
    fireEvent.click(followUpInput(container)!)
    expect(setFormatFollowUp).toHaveBeenCalledWith(true)
  })

  it('能力表里没有 followup 时整个不渲染（与后端表同源，不是硬编码）', () => {
    const { container } = render(
      <GenerateOptions {...optsProps({ typeMeta: getTypeMeta('webpage', { webpage: ['toc'] }) })} />)
    expect(followUpInput(container)).toBeUndefined()
  })

  it('只生成整理版时**禁用**并说明原因（后端会拦住，但不能只有后端知道）', () => {
    // ⚠️ `isPolishedOnly` **不是 prop** —— 组件自己从 `selectedCore` 算
    //    （`includes('polished') && !includes('notes')`）。造状态要动 selectedCore。
    const { container } = render(<GenerateOptions {...optsProps({ selectedCore: ['polished'] })} />)
    const box = followUpInput(container)!
    expect(box.disabled).toBe(true)
    expect(container.textContent).toContain('追问仅笔记产出')
  })

  it('笔记 + 整理版都选时**不**禁用，且点击真能把 true 抛出去（上一对的反面对照）', () => {
    // ⚠️ 「禁用的那个点不动」**不能**用 `fireEvent.click` 验证：jsdom 只在
    //    `HTMLElement.click()` 上查 disabled，`fireEvent` 走的是 `dispatchEvent`，
    //    对 disabled 元素照样派发 ⇒ 断言恒假（第一版这么写了，红在错误的理由上）。
    //    禁用的真实语义由 `box.disabled` 属性承担（上一条已断言），浏览器保证它。
    // ⚠️ 判据是「**只有**整理版」而不是「含整理版」—— 造这个状态来钉住它。
    const setFormatFollowUp = vi.fn()
    const { container } = render(
      <GenerateOptions {...optsProps({ selectedCore: ['notes', 'polished'], setFormatFollowUp })} />)
    const box = followUpInput(container)!
    expect(box.disabled).toBe(false)
    fireEvent.click(box)
    expect(setFormatFollowUp).toHaveBeenCalledWith(true)
  })
})

// ═══════════════════════════════════════════════════════════
// 5. 渲染器复用 —— `ChatFollowUpList` 的两个新 prop
//
// ⚠️ 缺省时行为必须**逐字不变**（对话侧零回归）：这组最后两条是那半边的守卫。
// ═══════════════════════════════════════════════════════════

describe('ChatFollowUpList — 产物侧复用', () => {
  const ITEMS = ['产物侧的追问一', '产物侧的追问二']

  beforeEach(() => {
    useChatStore.setState({ messages: [], streaming: false })
  })

  it('传 onSelect 时点击走它，**不发消息**（产物侧要显式带 docRefs，由调用方决定）', () => {
    // 对照实验（已验证）：把 chip 的 onClick 改回无条件 `sendMessage` → 本条红。
    const onSelect = vi.fn()
    const { container } = render(
      <ChatFollowUpList items={ITEMS} streaming={false} onSelect={onSelect} />)
    fireEvent.click(container.querySelectorAll('.chat-followup-chip')[1])

    expect(onSelect).toHaveBeenCalledWith('产物侧的追问二')
    expect(useChatStore.getState().messages).toEqual([])   // 一条消息都没发出去
  })

  it('自定义 label 出现在折叠头上（产物侧说的是「继续追问」而不是对话侧的默认词）', () => {
    const { container } = render(
      <ChatFollowUpList items={ITEMS} streaming={false} label="继续追问" />)
    expect(container.querySelector('.chat-followup-label')!.textContent).toContain('继续追问')
    expect(container.querySelector('.chat-followup-label')!.textContent).toContain('2')
  })

  it('⚠️ 零回归：不传 onSelect 时点击照旧直接发送', () => {
    useChatStore.setState({ streaming: false, messages: [] })
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} />)
    fireEvent.click(container.querySelectorAll('.chat-followup-chip')[0])

    const msgs = useChatStore.getState().messages
    expect(msgs[0]).toMatchObject({ role: 'user', content: '产物侧的追问一' })
    expect(msgs[1]).toMatchObject({ role: 'assistant', streaming: true })
  })

  it('⚠️ 零回归：不传 label 时文案仍是「继续追问」', () => {
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} />)
    expect(container.querySelector('.chat-followup-label')!.textContent).toContain('继续追问')
  })
})
