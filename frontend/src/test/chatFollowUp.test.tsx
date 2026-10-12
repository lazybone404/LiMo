/**
 * 追问卡片 —— **补调来的那一半**的端到端接线。
 *
 * 与 `chatBlocks.test.tsx` 分家的理由：那个文件测的是**模型自主写**在正文里的
 * `follow-up` 块（`parseFollowUpItems` → `ChatFollowUpList`）—— 那是**既有**能力。
 * 本文件测的是本轮新增的**另一条来源**：开关 → `follow_up=1` → 后端补调 →
 * `followup` 事件 → `ChatMsg.followUps` → 同一个渲染器。
 * 两条来源共用渲染器，故这里有一条**源码守卫**钉住「chip 只有一个发生地」。
 *
 * ⚠️ **刻意不 mock `../api/chat` 的 `streamChat`**（`chatStore.test.ts` 那样做是为了
 * 直接驱动 callbacks）。本文件要同时断言「URL 参数怎么拼」与「真实 callbacks 链路」，
 * 后者包含一个**隐含前提**：追问事件晚于 `done`，而 `receivedDoneOrError` 守卫在
 * `done` 到达时就已置真 ⇒ 不会误报断线。若将来有人把那个守卫改成「以流关闭为准」，
 * 本文件第 3 条会当场变成「每次追问都多弹一条连接错误」。
 * 做法：只 mock `globalThis.fetch`，返回一条**可手动推事件**的 SSE 流。
 */
/// <reference types="node" />
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import * as chatApi from '../api/chat'
import { useChatStore, type ChatMsg } from '../store/chatStore'
import { useStore } from '../store'
import { loadChatPrefs, saveChatPrefs } from '../lib/chat-prefs'
import { parseFollowUpItems } from '../lib/chat-block-content'
import { stripMarkers } from '../lib/markdown-markers'
import { ChatFollowUpList, ChatMarkerContent } from '../components/ai-chat/ChatMarkerContent'
import { MessageItem } from '../components/ai-chat/MessageItem'

// ── mock api/chat：只换掉走 `apiFetch` 的那三个，保留**真实的** `streamChat` ──
//
// ⚠️ 那三个**必须**换掉，否则 `endStreaming()` 每次收尾都会 fire-and-forget 一次
//    `listConversations()` —— 它走 `apiFetch` → 打到我们 mock 的 `fetch` 上，
//    把「第 N 次 fetch 对应第 N 轮对话」这个索引关系冲乱。
vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    listConversations: vi.fn(async () => []),
    getConversation: vi.fn(async () => null),
    rebuildConversationMessages: vi.fn(async () => ({ status: 'ok' })),
    // ⚠️ 「换一批」也必须换掉：它走 `apiFetch` → 本文件的 `globalThis.fetch` 被 mock 成
    // **SSE 流**（返回的是 `ReadableStream`，`.json()` 会炸）。给一个成功的**默认实现**
    // 而不是让每个用例自己 `mockResolvedValueOnce` —— 一次性的实现没被消费掉就会
    // 残留到下一个用例（`clearAllMocks` 只清调用记录，不清 once 队列）。
    refreshFollowUps: vi.fn(async () => ({ type: 'followup', questions: ['换一批得到的新问题'], error: null })),
  }
})

// ── 可控 SSE 流 ──

/** 一条可手动推事件的 SSE 流。格式与 `parseSSEStream` 期待的一致：`data: {json}\n` */
function makeSSE() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({ start(c) { ctrl = c } })
  const enc = new TextEncoder()
  return {
    body,
    push(type: string, extra: Record<string, unknown> = {}) {
      ctrl.enqueue(enc.encode(`data: ${JSON.stringify({ type, ...extra })}\n\n`))
    },
    close() { ctrl.close() },
  }
}

/**
 * 伪造 `Response` —— `streamChat` 只用到 `ok` / `status` / `json()` / `body.getReader()`。
 * 自造对象可绕开 Node 与 jsdom 对 `Response` + `ReadableStream` 组合的额外校验
 * （`new Response(stream)` 在不同运行时下对 body 的检查不太一致）。
 */
const sseResponse = (body: ReadableStream<Uint8Array>) =>
  ({ ok: true, status: 200, body }) as unknown as Response

let sseList: ReturnType<typeof makeSSE>[] = []
/** 每次 `fetch` 的 URL —— 按调用顺序。断言参数拼装用 */
let fetchUrls: string[] = []

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sseList = []
  fetchUrls = []
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
    contextWindow: null, budget: null, lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 }, compressedHint: null,
    clearContextIndex: null, priceIn: null, priceOut: null,
  })
  useStore.setState({ currentDocId: null, docs: [] })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    fetchUrls.push(String(input))
    const sse = makeSSE()
    sseList.push(sse)
    return sseResponse(sse.body)
  })
})

afterEach(() => { vi.restoreAllMocks() })

/** 让 SSE 解析循环（`while + await reader.read()`）跑完已入队的事件 */
const settle = async (rounds = 8) => {
  for (let i = 0; i < rounds; i++) await new Promise(r => setTimeout(r, 0))
}

/** 发一条消息并把这一轮的 SSE 流交出来（`fetch` 是同步调用的，无需 await） */
function sendAndGetSSE(text = '问题') {
  useChatStore.getState().sendMessage(text)
  expect(sseList).toHaveLength(1)
  return sseList[0]
}

const botOf = (i = 1) => useChatStore.getState().messages[i]

// ═══════════════════════════════════════════════════════════
// 1. 开关偏好 —— 默认关 = 零回归
// ═══════════════════════════════════════════════════════════

describe('开关偏好（lib/chat-prefs）', () => {
  it('默认关闭（改这个默认值就是改所有用户的既有行为）', () => {
    expect(loadChatPrefs().followUp).toBe(false)
  })

  it('保存后能读回（localStorage key 是磁盘契约，这里写死它）', () => {
    saveChatPrefs({ followUp: true })
    expect(localStorage.getItem('mbw_chatPrefs')).toContain('true')
    expect(loadChatPrefs().followUp).toBe(true)
  })

  it('损坏 JSON → 回默认、不抛', () => {
    localStorage.setItem('mbw_chatPrefs', '{这不是 JSON')
    expect(loadChatPrefs().followUp).toBe(false)
  })

  it('字段类型错 → 只退回该项默认（不整包丢弃，为将来加第二项留路）', () => {
    localStorage.setItem('mbw_chatPrefs', JSON.stringify({ followUp: 'yes' }))
    expect(loadChatPrefs().followUp).toBe(false)
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 开关 → 请求参数
//
// ⚠️ 关着的时候是「**根本不设这个参数**」而不是 `follow_up=0` —— 前者可被断言，
//    后者与后端 `Query(0)` 的默认路径看起来完全一样、测不出区别。
//    对照实验：把参数写死（无视偏好）→ 第二条红（关着时也带上了）。
// ═══════════════════════════════════════════════════════════

describe('开关 → 请求参数', () => {
  it('开 → URL 带 follow_up=1', () => {
    saveChatPrefs({ followUp: true })
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).toContain('follow_up=1')
  })

  it('关 → URL 完全不含 follow_up（不是 =0）', () => {
    saveChatPrefs({ followUp: false })
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).not.toContain('follow_up')
  })
})

// ═══════════════════════════════════════════════════════════
// 3. `followup` 事件 → 消息字段 → chip
//
// 后端在 `done` **之后**补推这个事件（先推 done 让输入框即刻可用，再补调一次 LLM）。
// 这里完整走一遍真实 `streamChat` → callbacks 链路，包括 `receivedDoneOrError`。
// ═══════════════════════════════════════════════════════════

describe('followup 事件 → 消息字段', () => {
  const QUESTIONS = ['这个方案有哪些失败模式', '它和现有做法比如何']

  /** 走完一轮：text → done → followup → 关流 */
  async function runRound(questions: string[] | null, error: string | null = null) {
    const sse = sendAndGetSSE()
    sse.push('text', { text: '回答正文' })
    sse.push('done')
    sse.push('followup', { questions: questions ?? [], error })
    sse.close()
    await settle()
    return botOf()
  }

  it('done 之后推来的追问写进 followUps，并渲染出 chip', async () => {
    saveChatPrefs({ followUp: true })
    const bot = await runRound(QUESTIONS)

    expect(bot.followUps).toEqual(QUESTIONS)
    expect(bot.followUpError).toBeUndefined()
    expect(bot.streaming).toBe(false) // done 先到 → 流式已收敛

    const { container } = render(<MessageItem msg={bot} scale="md" />)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(2)
  })

  it('⚠️ 产物守卫：追问**不进 content** —— 导出/复制读 stripMarkers(content)，天然拿不到', async () => {
    saveChatPrefs({ followUp: true })
    const bot = await runRound(QUESTIONS)

    // 对照实验：改回「把追问追加进 content」→ 下面第一条就红
    expect(bot.content).toBe('回答正文')
    expect(stripMarkers(bot.content)).not.toContain('失败模式')
    expect(stripMarkers(bot.content)).not.toContain('limo-block')
  })

  it('补调失败 → followUpError 显式落点（不是静默当「这轮没有」）', async () => {
    saveChatPrefs({ followUp: true })
    const bot = await runRound(null, '追问建议生成失败')

    expect(bot.followUps).toBeUndefined()
    expect(bot.followUpError).toBe('追问建议生成失败')

    const { container } = render(<MessageItem msg={bot} scale="md" />)
    expect(container.textContent).toContain('追问建议生成失败')
    expect(container.querySelector('.chat-followup')).toBeNull() // 失败不留空壳
  })

  it('开关关着收到 followup 事件 → 不写进消息（本次请求的语义，不是第二份判据）', async () => {
    saveChatPrefs({ followUp: false })
    const bot = await runRound(QUESTIONS)

    expect(bot.followUps).toBeUndefined()
    expect(bot.followUpError).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════
// 4. ⚠️ 竞态守卫 —— 本轮新引入的唯一竞态
//
// 「追问晚于 done 到达，而用户此时已经发了新消息」是**必然会发生**的常态
// （done 一到输入框就可用，补调还要几秒）。按末尾定位会把上一轮的追问挂到
// 新一轮的 bot 上（`updateLastBot` 只认 assistant，于是干脆丢掉卡片）。
//   → 对照实验：把 `patchBot(botMsg.id, …)` 换回 `updateLastBot(…)` → 前两条断言红。
// ═══════════════════════════════════════════════════════════

describe('⚠️ 竞态：followup 晚于 done，用户此时已发新消息', () => {
  it('追问落在**原消息**上，新消息不被污染', async () => {
    saveChatPrefs({ followUp: true })
    useChatStore.getState().sendMessage('第一问')
    sseList[0].push('text', { text: '第一答' })
    sseList[0].push('done')
    await settle()

    // done 已处理 → 输入框可用（这正是用户能抢先发下一条的原因）
    expect(useChatStore.getState().streaming).toBe(false)
    const firstBotId = useChatStore.getState().messages[1].id

    // 用户在追问到达**之前**就发了下一条
    useChatStore.getState().sendMessage('第二问')
    expect(sseList).toHaveLength(2)

    // 现在第一轮的追问才到
    sseList[0].push('followup', { questions: ['第一轮的追问是什么'], error: null })
    sseList[0].close()
    await settle()

    const msgs = useChatStore.getState().messages
    expect(msgs[1].id).toBe(firstBotId)
    expect(msgs[1].followUps).toEqual(['第一轮的追问是什么'])
    // 新消息干净：既没被挂上追问，也没被标成失败
    expect(msgs[3].followUps).toBeUndefined()
    expect(msgs[3].followUpError).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════
// 5. 渲染器 —— 折叠 / 空态 / 两个来源共用一个
// ═══════════════════════════════════════════════════════════

describe('ChatFollowUpList — 折叠与空态', () => {
  const ITEMS = ['第一个追问是什么', '第二个追问是什么']

  it('默认展开（开关的承诺是「选了一定有」，默认收起就看不出这份承诺）', () => {
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} />)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(2)
    expect(container.querySelector('.chat-followup-label')!.getAttribute('aria-expanded')).toBe('true')
  })

  it('点折叠头 → chip 消失、标签保留且带数量；再点恢复', () => {
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} />)
    const head = container.querySelector('.chat-followup-label')!
    expect(head.textContent).toContain('2')

    fireEvent.click(head)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(0)
    expect(container.querySelector('.chat-followup-label')!.getAttribute('aria-expanded')).toBe('false')
    expect(container.textContent).toContain('继续追问')

    fireEvent.click(head)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(2)
  })

  it('空数组不渲染空壳（否则回答末尾挂着一个「继续追问 · 0」）', () => {
    const { container } = render(<ChatFollowUpList items={[]} streaming={false} />)
    expect(container.querySelector('.chat-followup')).toBeNull()
    expect(container.textContent).toBe('')
  })

  it('流式中 chip 可见但点不动（折叠头仍可点）', () => {
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming />)
    const chips = container.querySelectorAll('.chat-followup-chip')
    expect(chips).toHaveLength(2)
    chips.forEach(c => expect((c as HTMLButtonElement).disabled).toBe(true))
    expect((container.querySelector('.chat-followup-label') as HTMLButtonElement).disabled).toBe(false)
  })

  it('点 chip 直接发送，且新一轮照带开关（追问的追问天然成立）', () => {
    saveChatPrefs({ followUp: true })
    const { container } = render(<ChatFollowUpList items={['点我就发送的问题']} streaming={false} />)
    fireEvent.click(container.querySelector('.chat-followup-chip')!)

    const msgs = useChatStore.getState().messages
    expect(msgs[0]).toMatchObject({ role: 'user', content: '点我就发送的问题' })
    expect(msgs[1]).toMatchObject({ role: 'assistant', streaming: true })
    expect(fetchUrls[0]).toContain('follow_up=1')
  })

  it('模型自主写的 `follow-up` 块走**同一个**渲染器、同样可折叠', () => {
    const md = '<!-- limo-block:v1 type="follow-up" -->\n- 第一颗追问是什么\n- 第二颗追问是什么\n<!-- /limo-block -->'
    const { container } = render(<ChatMarkerContent content={md} streaming={false} statusText="" />)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(2)

    fireEvent.click(container.querySelector('.chat-followup-label')!)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(0)
    // 折叠后问题文本也一并收起（不是只藏按钮）
    expect(container.textContent).not.toContain('第一颗追问是什么')
  })
})

// ═══════════════════════════════════════════════════════════
// 6. 抽取层同源 —— 共享 fixture（与后端 test_followup.py 读同一份）
// ═══════════════════════════════════════════════════════════

const FIXTURE = JSON.parse(
  readFileSync(resolve(process.cwd(), 'src/test/fixtures/follow-up-parse.json'), 'utf-8'),
) as { cases: { name: string; content: string; items: string[] }[] }

describe('与后端同源 — 共享 fixture `follow-up-parse.json`', () => {
  it('fixture 可读且用例没被删空', () => {
    expect(FIXTURE.cases.length).toBeGreaterThanOrEqual(8)
  })

  // ⚠️ 逐条展开而不是断言「整体相等」：一条坏了要能直接读出是**哪一类行为**坏了
  for (const c of FIXTURE.cases) {
    it(`抽取一致 — ${c.name}`, () => {
      expect(parseFollowUpItems(c.content)).toEqual(c.items)
    })
  }
})

// ═══════════════════════════════════════════════════════════
// 7. 渲染器同源（源码守卫）
// ═══════════════════════════════════════════════════════════

describe('渲染器同源 — 源码守卫', () => {
  it('`chat-followup-chip` 在 src 的 TS/TSX 里**只出现一处**', () => {
    // 两个数据来源（补调数组 / 模型自主块）共用唯一渲染器；各写一份就是第二份实现，
    // 而两份 chip 的观感分叉是必然的。
    // ⚠️ 只扫 `.ts/.tsx`：`styles/chat-blocks.css` 里定义这个类名是**样式归属**、
    //    不是渲染发生地，把它算进来会让守卫永远红。
    // ⚠️ 断言**精确等于**那一个路径（不是「数量 ≤ 1」）：文件搬了家也要红。
    const root = resolve(process.cwd(), 'src')
    const hits = walkTs(root)
      .filter(f => !f.includes(`${sep}test${sep}`))
      .filter(f => readFileSync(f, 'utf-8').includes('chat-followup-chip'))
      .map(f => f.slice(root.length + 1).split(sep).join('/'))
    expect(hits).toEqual(['components/ai-chat/ChatMarkerContent.tsx'])
  })
})

/** 递归收集某个目录下的 `.ts` / `.tsx` */
function walkTs(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walkTs(p, out)
    else if (/\.tsx?$/.test(e.name)) out.push(p)
  }
  return out
}

// ═══════════════════════════════════════════════════════════
// 8. 持久化往返 —— 刷新 / 重开会话后卡片还在
//
// `openConversation`（捡回来）与 `toBackendMsg`（重建时别丢）是**成对**的，
// 缺任一都是静默丢卡，且症状完全一样（刷新后消失 / 编辑后消失）。
// ═══════════════════════════════════════════════════════════

describe('持久化往返', () => {
  it('openConversation 把落盘的 `follow_ups` 恢复成 `followUps`', async () => {
    vi.mocked(chatApi.getConversation).mockResolvedValueOnce({
      id: 'conv1', title: 'T', created_at: 'c', updated_at: 'u', doc_ids: [],
      messages: [{
        id: 'm1', role: 'assistant', content: '答', timestamp: 't',
        follow_ups: ['落盘恢复的追问是什么'],
      }],
    })
    await useChatStore.getState().openConversation('conv1')

    const msg = useChatStore.getState().messages[0]
    expect(msg.followUps).toEqual(['落盘恢复的追问是什么'])
    const { container } = render(<MessageItem msg={msg} scale="md" />)
    expect(container.querySelectorAll('.chat-followup-chip')).toHaveLength(1)
  })

  it('落盘是空数组时恢复成 undefined（`[]` 是渲染出空壳的前置条件）', async () => {
    vi.mocked(chatApi.getConversation).mockResolvedValueOnce({
      id: 'conv1', title: 'T', created_at: 'c', updated_at: 'u', doc_ids: [],
      messages: [{ id: 'm1', role: 'assistant', content: '答', timestamp: 't', follow_ups: [] }],
    })
    await useChatStore.getState().openConversation('conv1')
    expect(useChatStore.getState().messages[0].followUps).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════
// 9. ⚠️ 四种 rebuild 都不丢追问
//
// `toBackendMsg` 是**白名单**，且有**四处**调用点。四个动作共用它 ⇒
// 一处漏 = 四处丢，且症状一模一样（编辑任意一条消息后追问卡整批消失）。
// 所以这条不能只测一个动作。
//   → 对照实验：删掉 `toBackendMsg` 里那行 `follow_ups` 展开 → 四条全红。
// ═══════════════════════════════════════════════════════════

describe('⚠️ 四种 rebuild 都不丢追问', () => {
  const seed = (): ChatMsg[] => [
    { id: 'm1', role: 'user', content: '问1', date: 'd' },
    { id: 'm2', role: 'assistant', content: '答1', date: 'd', followUps: ['这条追问不能丢'] },
    { id: 'm3', role: 'user', content: '问2', date: 'd' },
    { id: 'm4', role: 'assistant', content: '答2', date: 'd' },
  ]

  /** 跑一个动作，取回最后一次 rebuild 的载荷 */
  async function payloadAfter(run: () => void | Promise<void>) {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    vi.mocked(chatApi.rebuildConversationMessages).mockClear()
    await run()
    await settle(4)
    const calls = vi.mocked(chatApi.rebuildConversationMessages).mock.calls
    expect(calls.length).toBeGreaterThan(0)
    return calls.at(-1)![1]
  }

  /** m2 若仍在载荷里，就必须带着 follow_ups（被截断掉的轮次不在此列） */
  function expectKept(payload: { id: string; follow_ups?: unknown }[]) {
    const m2 = payload.find(m => m.id === 'm2')
    expect(m2).toBeDefined()
    expect(m2!.follow_ups).toEqual(['这条追问不能丢'])
  }

  it('regenerate（从配对 user 起截断 + 重发）', async () => {
    expectKept(await payloadAfter(() => useChatStore.getState().regenerate('m4')))
  })

  it('deleteMessage（filter 单条）', async () => {
    expectKept(await payloadAfter(() => useChatStore.getState().deleteMessage('m3')))
  })

  it('editMessage(user)（截断 + 重发）', async () => {
    expectKept(await payloadAfter(() => useChatStore.getState().editMessage('m3', '改过的问题')))
  })

  it('editMessage(assistant)（就地覆盖）', async () => {
    expectKept(await payloadAfter(() => useChatStore.getState().editMessage('m4', '改过的回答')))
  })
})

// ═══════════════════════════════════════════════════════════
// 10. 「换一批」—— 只对有可重跑上游的那一份开放
//
// 两条来源里只有**后端补调**的那份能换：模型自主写在正文里的 `follow-up` 块住在
// `content` 里，没有「上游问题 + 回答」这对输入可重跑。故准入判据落在
// `msg.followUps` 上（那边为空即早退）。
// ═══════════════════════════════════════════════════════════

describe('ChatFollowUpList — 换一批按钮', () => {
  const ITEMS = ['第一个追问是什么']

  it('不传 `onRefresh` 时**不渲染**按钮（产物侧复用零回归）', () => {
    // ⚠️ 判据是「没传 ⇒ 没有」，不是「传了 ⇒ 有」：预览区笔记末尾那处复用传的是
    // `label` / `onSelect`、**不传** `onRefresh`（它没有可重跑的上游）。默认渲染的话
    // 产物侧会多出一个点了没反应的控件 —— 而那种缺口没有任何断言会红。
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} />)
    expect(container.querySelector('.chat-followup-refresh')).toBeNull()
    // 折叠开关照旧（按钮的有无不改标签行其余部分）
    expect(container.querySelector('.chat-followup-label')).not.toBeNull()
  })

  it('传了就有；点一次调用一次', async () => {
    const onRefresh = vi.fn(async () => {})
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} onRefresh={onRefresh} />)
    fireEvent.click(container.querySelector('.chat-followup-refresh')!)
    await settle(2)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('流式中禁用（新一批会挂到正在变的内容上）', () => {
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming onRefresh={async () => {}} />)
    expect((container.querySelector('.chat-followup-refresh') as HTMLButtonElement).disabled).toBe(true)
  })

  it('连点两次只发一次请求（`refreshing` 期间早退）', async () => {
    // 无这道闸门时每次点击都会独立地打完一轮 LLM 调用，而最后回来的那批胜出 ——
    // 用户看到的是一批随机结果，且账单是点击次数。
    let release!: () => void
    const onRefresh = vi.fn(() => new Promise<void>(r => { release = r }))
    const { container } = render(<ChatFollowUpList items={ITEMS} streaming={false} onRefresh={onRefresh} />)
    const btn = container.querySelector('.chat-followup-refresh')!
    fireEvent.click(btn)
    fireEvent.click(btn)
    release()
    await settle(2)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('`MessageItem` 上接了线（漏了 = 按钮看得见、点了一点反应没有）', () => {
    const msg: ChatMsg = { id: 'm1', role: 'assistant', content: '答', date: 'd', followUps: ['一条追问'] }
    const { container } = render(<MessageItem msg={msg} scale="md" />)
    expect(container.querySelector('.chat-followup-refresh')).not.toBeNull()
  })
})

describe('chatStore.refreshFollowUps', () => {
  const seed = (): ChatMsg[] => [
    { id: 'm1', role: 'user', content: '上游问题', date: 'd' },
    { id: 'm2', role: 'assistant', content: '上游回答', date: 'd', followUps: ['旧的一批'] },
  ]
  const put = (msgs: ChatMsg[], streaming = false) =>
    useChatStore.setState({ messages: msgs, currentId: 'conv1', streaming })

  it('上游取自**向前最近的一条 user**（与 `regenerate` 同一套配对规则）', async () => {
    put(seed())
    await useChatStore.getState().refreshFollowUps('m2')
    await settle(2)
    expect(vi.mocked(chatApi.refreshFollowUps)).toHaveBeenCalledWith('上游问题', '上游回答')
  })

  it('成功 → 替换该条的 `followUps` 并落库', async () => {
    put(seed())
    await useChatStore.getState().refreshFollowUps('m2')
    await settle(4)
    const msg = useChatStore.getState().messages[1]
    expect(msg.followUps).toEqual(['换一批得到的新问题'])
    expect(msg.followUpError).toBeUndefined()
    // 落库走整表覆盖（与 deleteMessage / editMessage 同款）
    const payload = vi.mocked(chatApi.rebuildConversationMessages).mock.calls.at(-1)![1]
    expect(payload[1].follow_ups).toEqual(['换一批得到的新问题'])
  })

  it('失败 → **保留原有那批** + 记下原因（替换语义只管成功那一半）', async () => {
    // ⚠️ 生成失败还把用户手上正能用的一批清掉，是纯粹的净损失 —— 这条挡的正是它。
    vi.mocked(chatApi.refreshFollowUps).mockResolvedValueOnce({
      type: 'followup', questions: [], error: '追问建议生成失败',
    })
    put(seed())
    await useChatStore.getState().refreshFollowUps('m2')
    await settle(4)
    const msg = useChatStore.getState().messages[1]
    expect(msg.followUps).toEqual(['旧的一批'])
    expect(msg.followUpError).toBe('追问建议生成失败')
  })

  it('流式中不动（与 `deleteMessage` 同款守卫）', async () => {
    // 调用方本身不写 content，但末尾那次**整表 rebuild** 会把「含半成品回复的快照」
    // 整表回写，与正在流的那条互相打架。
    put(seed(), true)
    await useChatStore.getState().refreshFollowUps('m2')
    await settle(2)
    expect(chatApi.refreshFollowUps).not.toHaveBeenCalled()
  })

  it('模型自主写在正文里的那份 → 早退（它没有可重跑的上游）', async () => {
    // 追问住在 `content` 里（`follow-up` 块），`followUps` 为空。放行的话会拿
    // 正文里的块当回答去重跑，生成一批与那条内容无关的问题。
    put([{ id: 'm1', role: 'assistant', content: '带 follow-up 块的正文', date: 'd' }])
    await useChatStore.getState().refreshFollowUps('m1')
    await settle(2)
    expect(chatApi.refreshFollowUps).not.toHaveBeenCalled()
  })

  it('目标消息已被删掉 → 什么都不做（陈旧响应不许落到别处）', async () => {
    put(seed())
    await useChatStore.getState().refreshFollowUps('不存在的 id')
    await settle(2)
    expect(chatApi.refreshFollowUps).not.toHaveBeenCalled()
    expect(useChatStore.getState().messages[1].followUps).toEqual(['旧的一批'])
  })
})
