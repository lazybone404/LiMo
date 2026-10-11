/**
 * 对话侧联网 —— 开关（`mbw_chatPrefs.webSearch`）+ 公网来源（`ToolCall.sources`）。
 *
 * 两块放同一个文件，因为它们**服务同一件事**：让「模型有没有联网、搜到了什么」在
 * 两侧都成为**显式**的。此前联网能力常开、且 `search_web` 的结果 URL **一条通路都
 * 没有**（`refs` 只覆盖库内文档）——用户只看得见「正在搜索网络: xxx」。
 *
 * ⚠️ 与 `chatFollowUp.test.tsx` 分家：那个文件测补调追问，本文件测联网开关与来源。
 * 两者共用同一套 SSE 驱动脚手架（有意各留一份：把脚手架抽成第三个文件会让
 * 「这个测试到底 mock 了什么」多一层跳转，而两处的 mock 集**确实不同**）。
 *
 * ⚠️ **刻意不 mock `../api/chat` 的 `streamChat`**：本文件要同时断言「URL 参数怎么拼」
 * 与「真实 callbacks 链路」（`toChatSources` 在 `api/chat.ts` 内生效 —— 用 mock 替掉
 * `streamChat` 就等于把被测的那一层也换掉了）。做法同 `chatFollowUp.test.tsx`：
 * 只 mock `globalThis.fetch`，返回一条可手动推事件的 SSE 流。
 */
/// <reference types="node" />
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import * as chatApi from '../api/chat'
import { useChatStore, type ChatMsg } from '../store/chatStore'
import { useStore } from '../store'
import { loadChatPrefs, saveChatPrefs } from '../lib/chat-prefs'
import { toChatSources, isSafeSourceUrl, sourceHost } from '../lib/chat-sources'
import { MessageItem } from '../components/ai-chat/MessageItem'

// ── mock api/chat：只换掉走 `apiFetch` 的那三个，保留**真实的** `streamChat` ──
vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    listConversations: vi.fn(async () => []),
    getConversation: vi.fn(async () => null),
    rebuildConversationMessages: vi.fn(async () => ({ status: 'ok' })),
  }
})

// ── 可控 SSE 流（同 `chatFollowUp.test.tsx`）──

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

const settle = async (rounds = 8) => {
  for (let i = 0; i < rounds; i++) await new Promise(r => setTimeout(r, 0))
}

/**
 * 走完一轮带联网工具调用的回答：
 * `tool_start` → `tool_result(sources)` → `text` → `done` → 关流。
 *
 * ⚠️ 顺序照后端真实行为：一轮内**先**全部 `tool_start`、**再**逐个 `tool_result`。
 */
async function runWebRound(
  tool = 'search_web',
  sources: unknown = [{ title: '标题一', url: 'https://a.com/1' }],
  summary = '找到 3 条相关结果',
  refs: { docId: string; title: string }[] = [],
) {
  useChatStore.getState().sendMessage('问题')
  // ⚠️ 取**最后一次** fetch 造出来的那条流：一个用例里可以跑两轮（见「两条来源通路」
  // 那条），写死 `sseList[0]` 会在第二轮往已关闭的 controller 上 enqueue。
  const sse = sseList[sseList.length - 1]
  sse.push('tool_start', { tool, display: '正在搜索网络: 问题' })
  sse.push('tool_result', { tool, summary, sources, refs })
  sse.push('text', { text: '回答正文' })
  sse.push('done')
  sse.close()
  await settle()
  // ⚠️ 取**这一轮**的 bot（最后一条），不是写死 `messages[1]` —— 一个用例里跑两轮时
  // 后者会指回上一轮的消息，断言看着全绿其实测的是别人。
  const msgs = useChatStore.getState().messages
  return msgs[msgs.length - 1]
}

// ═══════════════════════════════════════════════════════════
// 1. 开关偏好 —— 默认开 = 零回归
//
// ⚠️ 与产物侧 `GenerateRequest.web_search: bool = False` **方向相反且都对**：
//    那边是新增能力（默认关 = 零回归），这边是给已有能力加开关（默认开 = 零回归）。
//    判据同一条 —— 「默认值必须等于改造前的行为」。改这里的默认值 = 改所有用户的
//    既有行为（把他们的联网悄悄关掉）。
// ═══════════════════════════════════════════════════════════

describe('开关偏好（lib/chat-prefs）', () => {
  it('默认开启（改造前联网一直可用）', () => {
    expect(loadChatPrefs().webSearch).toBe(true)
  })

  it('保存后能读回（localStorage key 与字段名都是磁盘契约）', () => {
    saveChatPrefs({ webSearch: false })
    expect(loadChatPrefs().webSearch).toBe(false)
  })

  it('⚠️ 合并式保存：改一项不重置另一项', () => {
    // 对照实验：把 `saveChatPrefs` 改回整对象覆盖（`setItem(JSON.stringify(p))`）→
    // 下面两条**都红** —— 而症状是「用户关掉联网，顺手开了追问，联网又自己开了」，
    // 既没报错也没提示。`ChatInput.tsx` 的旧注释恰好预言过这一幕。
    saveChatPrefs({ webSearch: false })
    saveChatPrefs({ followUp: true })
    expect(loadChatPrefs().webSearch).toBe(false)   // 没被 followUp 那次重置
    expect(loadChatPrefs().followUp).toBe(true)

    saveChatPrefs({ webSearch: true })
    expect(loadChatPrefs().followUp).toBe(true)     // 反向亦然
  })

  it('损坏 JSON → 回默认、不抛', () => {
    localStorage.setItem('mbw_chatPrefs', '{这不是 JSON')
    expect(loadChatPrefs().webSearch).toBe(true)
  })

  it('字段类型错 → 只退回该项默认（另一项照常保留）', () => {
    localStorage.setItem('mbw_chatPrefs', JSON.stringify({ webSearch: 'no', followUp: true }))
    expect(loadChatPrefs().webSearch).toBe(true)
    expect(loadChatPrefs().followUp).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 开关 → 请求参数
//
// ⚠️ 方向与 `follow_up` **相反**：那个是「开着才带 `=1`」，这个是「**关着**才带 `=0`」。
//    纪律同一条 —— 参数只在偏离默认时才发出去（少一个参数是可被断言的形式）。
// ═══════════════════════════════════════════════════════════

describe('开关 → 请求参数', () => {
  it('默认（开）→ URL 完全不含 web_search', () => {
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).not.toContain('web_search')
  })

  it('显式开着 → 同样不含（与 `web_search=1` 是同一件事，不发冗余参数）', () => {
    saveChatPrefs({ webSearch: true })
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).not.toContain('web_search')
  })

  it('关 → URL 带 web_search=0', () => {
    saveChatPrefs({ webSearch: false })
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).toContain('web_search=0')
  })

  it('两个开关互不影响（关联网不带上 follow_up，开追问不带上 web_search）', async () => {
    saveChatPrefs({ webSearch: false, followUp: true })
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).toContain('web_search=0')
    expect(fetchUrls[0]).toContain('follow_up=1')

    // ⚠️ 必须先把这一轮**收尾**（`done` + 关流）—— `dispatchSend` 首行是
    // `if (s.streaming) return`，流没收完时第二次 `sendMessage` 是**静默 no-op**
    // （不报错、不发请求），`fetchUrls[1]` 会 undefined。
    sseList[0].push('done')
    sseList[0].close()
    await settle()

    saveChatPrefs({ webSearch: true, followUp: false })
    useChatStore.getState().sendMessage('问题二')
    expect(fetchUrls).toHaveLength(2)
    expect(fetchUrls[1]).not.toContain('web_search')
    expect(fetchUrls[1]).not.toContain('follow_up')
  })
})

// ═══════════════════════════════════════════════════════════
// 3. `tool_result.sources` → `ToolCall.sources` → 渲染
//
// 这是本轮新增的第三块可见性：此前 `search_web` 搜到的 URL 前端**一条通道都没有**。
// ═══════════════════════════════════════════════════════════

describe('tool_result 的来源 → 工具气泡', () => {
  const SOURCES = [
    { title: '某篇文章', url: 'https://a.com/1' },
    { title: '', url: 'https://b.com/2' },          // 无标题 → 渲染层退回域名
  ]

  it('写进 ToolCall.sources，且随 summary 一起落地', async () => {
    const bot = await runWebRound('search_web', SOURCES)
    const tc = bot.toolCalls![0]
    expect(tc.tool).toBe('search_web')
    expect(tc.status).toBe('done')
    expect(tc.summary).toBe('找到 3 条相关结果')
    expect(tc.sources).toEqual(SOURCES)
  })

  it('默认收起：不渲染任何 `<a>`（避免每条工具气泡都铺开一列链接）', async () => {
    const bot = await runWebRound('search_web', SOURCES)
    const { container } = render(<MessageItem msg={bot} scale="md" />)

    const toggle = container.querySelector('[data-tool-sources-toggle]')!
    expect(toggle).not.toBeNull()
    expect(toggle.textContent).toContain('来源 2')
    expect(container.querySelectorAll('a')).toHaveLength(0)
  })

  it('展开后每条可点、`target=_blank`、无标题的退回域名', async () => {
    const bot = await runWebRound('search_web', SOURCES)
    const { container } = render(<MessageItem msg={bot} scale="md" />)
    fireEvent.click(container.querySelector('[data-tool-sources-toggle]')!)

    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(2)
    expect(links[0].getAttribute('href')).toBe('https://a.com/1')
    expect(links[0].textContent).toBe('某篇文章')
    expect(links[0].getAttribute('target')).toBe('_blank')
    expect(links[0].getAttribute('rel')).toContain('noopener')
    expect(links[1].textContent).toBe('b.com')   // 无标题 → `sourceHost`

    fireEvent.click(container.querySelector('[data-tool-sources-toggle]')!)
    expect(container.querySelectorAll('a')).toHaveLength(0)
  })

  it('fetch_url 也带来源（它抓的那一页）', async () => {
    const bot = await runWebRound('fetch_url', [{ title: 'a.com', url: 'https://a.com/post' }])
    expect(bot.toolCalls![0].sources).toEqual([{ title: 'a.com', url: 'https://a.com/post' }])
  })

  it('库内工具（search_docs）没有来源 → 不出现「来源」按钮', async () => {
    const bot = await runWebRound('search_docs', [])
    expect(bot.toolCalls![0].sources).toBeUndefined()
    const { container } = render(<MessageItem msg={bot} scale="md" />)
    expect(container.querySelector('[data-tool-sources-toggle]')).toBeNull()
    expect(container.querySelectorAll('a')).toHaveLength(0)
  })

  it('空数组与缺字段等价（不产出按钮、不留空壳）', async () => {
    const empty = await runWebRound('search_docs', [])
    expect(empty.toolCalls![0].sources).toBeUndefined()
    // 显式 `[]`（绕过归一直接塞进消息）也不渲染按钮 —— 渲染层 `sources ?? []`
    const forced: ChatMsg = {
      id: 'x', role: 'assistant', content: '答', date: 'd',
      toolCalls: [{ id: 't1', tool: 'search_web', display: '搜索中', status: 'done', sources: [] }],
    }
    const { container } = render(<MessageItem msg={forced} scale="md" />)
    expect(container.querySelector('[data-tool-sources-toggle]')).toBeNull()
  })
})

// ═══════════════════════════════════════════════════════════
// 4. ⚠️ URL 安全 —— 公网结果不可信
//
// 主防线在后端（不合规 URL 在抽取阶段就被丢掉）；前端这一道是纵深防御，
// 位置选在**不可信数据的两个入口**（SSE 流 / 磁盘恢复），渲染层直接渲染。
// ═══════════════════════════════════════════════════════════

describe('URL 安全（不可信数据入口）', () => {
  it('`javascript:` / `data:` 的整条被丢掉（端到端：不进 ToolCall.sources）', async () => {
    // 对照实验：把 `api/chat.ts` 的 `toChatSources(...)` 换成原样透传 → 本组红，
    // 且下面渲染出来的会是一个可点的 `javascript:` 链接。
    const bot = await runWebRound('search_web', [
      { title: '恶意', url: 'javascript:alert(1)' },
      { title: '也恶意', url: 'data:text/html,<script>x</script>' },
      { title: '正常', url: 'https://ok.com/x' },
    ])
    expect(bot.toolCalls![0].sources).toEqual([{ title: '正常', url: 'https://ok.com/x' }])

    const { container } = render(<MessageItem msg={bot} scale="md" />)
    fireEvent.click(container.querySelector('[data-tool-sources-toggle]')!)
    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(1)
    expect(links[0].getAttribute('href')).toBe('https://ok.com/x')
    expect(container.innerHTML).not.toContain('javascript:')
  })

  it('`isSafeSourceUrl` 判据（与后端 `_safe_http_url` 同条件）', () => {
    for (const ok of ['http://a.com/x', 'https://a.com', 'HTTPS://A.com/x?y=1'])
      expect(isSafeSourceUrl(ok)).toBe(true)

    // ⚠️ 末尾锚 `$`：`"https://a b"` 这种夹带空白的畸形串必须拒（进 `href` 可能被截断解析）
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', '//a.com', '/rel',
                       'a.com', '', '   ', 'https://', 'https://a b'])
      expect(isSafeSourceUrl(bad)).toBe(false)

    for (const bad of [null, 0, [], {}, undefined])
      expect(isSafeSourceUrl(bad)).toBe(false)

    // ⚠️ 长度上限 2000（与后端 `_safe_http_url` 同值）—— 恰好 2000 放行、2001 拒。
    // 正常搜索返回里不会有几 KB 的 URL，超长一律当畸形数据。
    const p = 'https://a.com/'
    expect(isSafeSourceUrl(p + 'x'.repeat(2000 - p.length))).toBe(true)
    expect(isSafeSourceUrl(p + 'x'.repeat(2001 - p.length))).toBe(false)
  })

  it('`toChatSources` 保序去重（同一篇文章被两轮命中是常态）', () => {
    expect(toChatSources([
      { title: 'A', url: 'https://a.com/1' },
      { title: 'A 的另一份', url: 'https://a.com/1' },
      { title: 'B', url: 'https://b.com' },
    ])).toEqual([
      { title: 'A', url: 'https://a.com/1' },
      { title: 'B', url: 'https://b.com' },
    ])
    // 非数组 / 非对象项一律不崩
    for (const bad of [undefined, null, 'x', 42, {}, [1, null, 'x', {}]])
      expect(toChatSources(bad)).toEqual([])
  })

  it('`sourceHost` 去 `www.`，解析不了就原样返回', () => {
    expect(sourceHost('https://www.a.com/x')).toBe('a.com')
    expect(sourceHost('https://b.com')).toBe('b.com')
    expect(sourceHost('不是 URL')).toBe('不是 URL')
  })
})

// ═══════════════════════════════════════════════════════════
// 5. 持久化往返 —— 刷新 / 重开会话后来源还在
//
// `openConversation`（恢复时捡）与 `toBackendMsg`（重建时别丢）是**成对**的，
// 缺任一都是静默丢来源，症状完全一样。与追问那一对同款纪律。
// ═══════════════════════════════════════════════════════════

describe('持久化往返', () => {
  it('openConversation 把落盘的 `tool_calls[].sources` 捡回来', async () => {
    vi.mocked(chatApi.getConversation).mockResolvedValueOnce({
      id: 'conv1', title: 'T', created_at: 'c', updated_at: 'u', doc_ids: [],
      messages: [{
        id: 'm1', role: 'assistant', content: '答', timestamp: 't',
        tool_calls: [{ tool: 'search_web', display: '正在搜索网络: x', summary: '找到 2 条相关结果',
                       sources: [{ title: '旧来源', url: 'https://old.com/x' }] }],
      }],
    })
    await useChatStore.getState().openConversation('conv1')

    const tc = useChatStore.getState().messages[0].toolCalls![0]
    expect(tc.summary).toBe('找到 2 条相关结果')
    expect(tc.sources).toEqual([{ title: '旧来源', url: 'https://old.com/x' }])

    const { container } = render(<MessageItem msg={useChatStore.getState().messages[0]} scale="md" />)
    expect(container.querySelector('[data-tool-sources-toggle]')).not.toBeNull()
  })

  it('⚠️ 旧版本落盘的数据没有 `sources` 字段 → 不崩、也只是没有按钮', async () => {
    // `toChatSources(undefined)` 返回 `[]` → 归一成 `undefined`。旧数据没有
    // `summary` 同理（本轮之前落盘的确只有 `tool` + `display`）。
    vi.mocked(chatApi.getConversation).mockResolvedValueOnce({
      id: 'conv1', title: 'T', created_at: 'c', updated_at: 'u', doc_ids: [],
      messages: [{
        id: 'm1', role: 'assistant', content: '答', timestamp: 't',
        tool_calls: [{ tool: 'search_web', display: '正在搜索网络: x' }],
      }],
    })
    await useChatStore.getState().openConversation('conv1')

    const tc = useChatStore.getState().messages[0].toolCalls![0]
    expect(tc.sources).toBeUndefined()
    expect(tc.summary).toBeUndefined()
    const { container } = render(<MessageItem msg={useChatStore.getState().messages[0]} scale="md" />)
    expect(container.querySelector('[data-tool-sources-toggle]')).toBeNull()
    expect(container.textContent).toContain('正在搜索网络: x')   // 气泡本身照常
  })

  it('恢复时 `sources: []` 归一成 undefined（空数组是「空壳按钮」的前置条件）', async () => {
    vi.mocked(chatApi.getConversation).mockResolvedValueOnce({
      id: 'conv1', title: 'T', created_at: 'c', updated_at: 'u', doc_ids: [],
      messages: [{
        id: 'm1', role: 'assistant', content: '答', timestamp: 't',
        tool_calls: [{ tool: 'search_web', display: 'x', summary: 'y', sources: [] }],
      }],
    })
    await useChatStore.getState().openConversation('conv1')
    expect(useChatStore.getState().messages[0].toolCalls![0].sources).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════
// 6. ⚠️ 四种 rebuild 都不丢来源
//
// `toBackendMsg` 是**白名单**且有**四处**调用点（regenerate / deleteMessage /
// editMessage × 2）。一处漏 = 四处丢，症状一模一样（编辑任意一条消息后，
// 全部历史气泡的来源集体消失）。⚠️ 与 `chatFollowUp.test.tsx` 的同名一组同因。
//   → 对照实验：删掉 `toBackendMsg` 里 map 中的 `sources` → 四条全红。
// ═══════════════════════════════════════════════════════════

describe('⚠️ 四种 rebuild 都不丢来源', () => {
  const seed = (): ChatMsg[] => [
    { id: 'm1', role: 'user', content: '问1', date: 'd' },
    { id: 'm2', role: 'assistant', content: '答1', date: 'd',
      toolCalls: [{ id: 't1', tool: 'search_web', display: '搜索中', summary: '找到 1 条相关结果',
                    status: 'done', sources: [{ title: '不能丢的来源', url: 'https://keep.com/x' }] }] },
    { id: 'm3', role: 'user', content: '问2', date: 'd' },
    { id: 'm4', role: 'assistant', content: '答2', date: 'd' },
  ]

  async function payloadAfter(run: () => void | Promise<void>) {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    vi.mocked(chatApi.rebuildConversationMessages).mockClear()
    await run()
    await settle(4)
    const calls = vi.mocked(chatApi.rebuildConversationMessages).mock.calls
    expect(calls.length).toBeGreaterThan(0)
    return calls.at(-1)![1]
  }

  function expectKept(payload: { id: string; tool_calls?: unknown }[]) {
    const m2 = payload.find(m => m.id === 'm2')
    expect(m2).toBeDefined()
    expect(m2!.tool_calls).toEqual([{
      tool: 'search_web', display: '搜索中', summary: '找到 1 条相关结果',
      sources: [{ title: '不能丢的来源', url: 'https://keep.com/x' }],
    }])
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
// 7. 对照组：两条来源通路**不合并**
//
// `refs`（库内文档，点了跳预览区）与 `sources`（公网来源，点了开浏览器）形状不同、
// 消费方不同。合并成一个列表 = 前端分不出该跳哪，且**两个功能同时坏**。
// ═══════════════════════════════════════════════════════════

describe('⚠️ refs 与 sources 是两条路', () => {
  it('`search_web` 有 sources、没有 refs；`search_docs` 反之', async () => {
    const web = await runWebRound('search_web',
      [{ title: '公网', url: 'https://a.com' }], '找到 1 条相关结果')
    expect(web.toolCalls![0].sources).toHaveLength(1)
    expect(web.docRefs ?? []).toHaveLength(0)
    const { container } = render(<MessageItem msg={web} scale="md" />)
    // 公网来源走**展开列表**（工具气泡内），不是引用 chips
    expect(container.querySelector('[data-tool-sources-toggle]')).not.toBeNull()
    expect(container.querySelector('button[title="跳转到该文档"]')).toBeNull()

    const docs = await runWebRound('search_docs', [], '找到 2 篇相关文档',
      [{ docId: 'tr_a', title: 'A' }, { docId: 'tr_b', title: 'B' }])
    expect(docs.toolCalls![0].sources).toBeUndefined()
    expect(docs.docRefs!.map(r => r.docId)).toEqual(['tr_a', 'tr_b'])
  })
})
