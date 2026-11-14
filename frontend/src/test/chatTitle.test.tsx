/**
 * 深度联网的三个收尾 SSE 事件：`title` / `server_context` / `warning`。
 *
 * 三件事放一个文件，因为它们**同一轮流里相邻**、共用同一套 SSE 脚手架：
 *
 *  · `title` —— 服务端给的会话短标题，写回会话列表与 `topic`。
 *  · `server_context` —— `server_tool_calls` / `annotations` 原始字段，**落盘用**，
 *    下一轮原样带回（否则「第一轮有来源、第二轮模型失忆」）。
 *  · `warning` —— 用户开了深度联网但这一轮没走成（模型不支持 / 联网被关），
 *    必须让用户看见，否则「开关开着、行为却变了」无从察觉。
 *
 * ⚠️ 要防的失效：
 *
 *   A. `title` 无条件覆盖 → 用户手改过的标题每轮被打回去（判据在后端
 *      `SessionManager.set_title_if_default`，前端**不做第二次判断**）。
 *   B. `server_context` 只存不传 → 恢复/重建时丢，界面毫无变化。
 *   C. `warning` 走 `onError` → 一轮本来有答案的回答被显示成失败。
 *
 * ⚠️ 不 mock `api/chat`（同 `chatWebSearch.test.tsx`）：本文件要断言的是
 * `api/chat.ts` 解析这些事件 + `chatStore` 接线的真实链路。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import * as chatApi from '../api/chat'
import { useChatStore } from '../store/chatStore'
import { useStore } from '../store'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    listConversations: vi.fn(async () => []),
    getConversation: vi.fn(async () => null),
    rebuildConversationMessages: vi.fn(async () => ({ status: 'ok' })),
  }
})

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

let sse: ReturnType<typeof makeSSE>

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
    contextWindow: null, budget: null, lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 }, compressedHint: null,
    clearContextIndex: null, priceIn: null, priceOut: null,
  })
  useStore.setState({ currentDocId: null, docs: [], capabilities: null, toasts: [] })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    sse = makeSSE()
    return { ok: true, status: 200, body: sse.body } as unknown as Response
  })
})

afterEach(() => { vi.restoreAllMocks() })

const settle = async (rounds = 8) => {
  for (let i = 0; i < rounds; i++) await new Promise(r => setTimeout(r, 0))
}

type Push = (type: string, extra?: Record<string, unknown>) => void

/**
 * 跑一轮最小对话：`meta`（让会话建立）→ 若干事件 → `text` → `done`。
 * `meta` 必须最先推，否则 `onTitle` 里 `currentId` 还是 null、只更新了一半。
 */
async function round(events: (push: Push) => void) {
  useChatStore.getState().sendMessage('这个问题会变成默认标题')
  sse.push('meta', { conversation_id: 'conv1' })
  events((type, extra = {}) => sse.push(type, extra))
  sse.push('text', { text: '回答正文' })
  sse.push('done')
  sse.close()
  await settle()
}

// ═══════════════════════════════════════════════════════════
// 1. `title` → 会话列表条目 + 顶部标题
//
// ⚠️ 断言必须在 `done` **之前**做：`onDone` 会 `refreshConversations()`，
// 而测试里 `listConversations` 是 mock 的空列表 —— 它会把这一轮刚加进去的条目
// 整个换掉（真实环境里服务端会把新会话连同新标题一起返回）。所以要测的是
// `onTitle` 自己写进去的那一份。
// ═══════════════════════════════════════════════════════════

describe('title 事件', () => {
  /** 走完 meta + 事件，**不推 done**（见上面那段注释） */
  async function tillTitle(events?: (push: Push) => void) {
    useChatStore.getState().sendMessage('这个问题会变成默认标题')
    sse.push('meta', { conversation_id: 'conv1' })
    events?.((type, extra = {}) => sse.push(type, extra))
    await settle()
    return useChatStore.getState()
  }

  it('写回会话列表条目与 topic（两处都要改，只改一处 = 侧栏与标题栏说法不同）', async () => {
    const s = await tillTitle(push => push('title', { title: '短标题' }))
    expect(s.topic).toBe('短标题')
    expect(s.conversations.find(c => c.id === 'conv1')!.title).toBe('短标题')
  })

  it('空标题 → 不动（后端偶尔会给空串，那是「没有标题」不是「标题叫空」）', async () => {
    const s = await tillTitle(push => push('title', { title: '' }))
    expect(s.topic).toContain('这个问题')
    expect(s.conversations.find(c => c.id === 'conv1')!.title).toContain('这个问题')
  })

  it('⚠️ 是否覆盖由后端裁决，前端收到就写（两份判据会分叉）', async () => {
    // 后端 `set_title_if_default` 在用户手改过标题时**不推**这个事件 ——
    // 前端这里看到的就是「没收到」，故保持原样。这条测的是「没有事件 = 不改」。
    const s = await tillTitle()
    expect(s.topic).toContain('这个问题')
    expect(s.conversations.find(c => c.id === 'conv1')!.title).toContain('这个问题')
  })
})

// ═══════════════════════════════════════════════════════════
// 2. `server_context` → 挂到本轮 assistant 消息上
// ═══════════════════════════════════════════════════════════

describe('server_context 事件', () => {
  const CALLS = [{ calls: [{ sources: [{ url: 'https://a.com/1' }] }] }]
  const ANNOTATIONS = [{ type: 'url_citation', url: 'https://a.com/1' }]

  it('落到本轮 assistant 消息的 serverToolCalls / annotations', async () => {
    await round(push => push('server_context', {
      server_tool_calls: CALLS, annotations: ANNOTATIONS,
    }))

    const msgs = useChatStore.getState().messages
    const bot = msgs[msgs.length - 1]
    expect(bot.role).toBe('assistant')
    expect(bot.serverToolCalls).toEqual(CALLS)
    expect(bot.annotations).toEqual(ANNOTATIONS)
  })

  it('⚠️ 重建会话时原样带回（`toBackendMsg` 与恢复侧成对，漏一处 = 编辑消息后失忆）', async () => {
    await round(push => push('server_context', {
      server_tool_calls: CALLS, annotations: ANNOTATIONS,
    }))
    const st = useChatStore.getState()
    vi.mocked(chatApi.rebuildConversationMessages).mockClear()
    await st.deleteMessage(st.messages[0].id)
    await settle(4)

    const calls = vi.mocked(chatApi.rebuildConversationMessages).mock.calls
    expect(calls.length).toBeGreaterThan(0)
    const payload = calls.at(-1)![1] as unknown as Record<string, unknown>[]
    const bot = payload.find(m => m.role === 'assistant')!
    expect(bot.server_tool_calls).toEqual(CALLS)
    expect(bot.annotations).toEqual(ANNOTATIONS)
  })

  it('没开启深度联网的轮次没有它 → 消息上也不该有这两个字段', async () => {
    await round(() => { /* 无 server_context */ })
    const msgs = useChatStore.getState().messages
    const bot = msgs[msgs.length - 1]
    expect(bot.serverToolCalls).toBeUndefined()
    expect(bot.annotations).toBeUndefined()
  })
})

// ═══════════════════════════════════════════════════════════
// 3. `warning` → info toast，**不是** error
// ═══════════════════════════════════════════════════════════

describe('warning 事件', () => {
  it('弹一条 info toast，这一轮照常有答案', async () => {
    await round(push => push('warning', { message: '当前对话模型不支持深度联网，已按普通联网模式回答' }))

    const toasts = useStore.getState().toasts
    const hit = toasts.find(t => t.message.includes('不支持深度联网'))
    expect(hit).toBeDefined()
    expect(hit!.type).toBe('info')

    const msgs = useChatStore.getState().messages
    expect(msgs[msgs.length - 1].content).toBe('回答正文')
    expect(useChatStore.getState().streaming).toBe(false)
  })
})
