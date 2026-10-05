/**
 * chatStore 单测 — 消息增删 / streaming 生命周期 / regenerate / editMessage / stop / docIds 组合。
 *
 * 通过 mock api/chat.ts 捕获 streamChat 参数与 callbacks，测试驱动流式状态机。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, type ChatMsg } from '../store/chatStore'
import { useStore } from '../store'
import * as chatApi from '../api/chat'

// ── Mock api/chat ──
let captured: {
  question: string
  docIds: string[]
  callbacks: {
    onMeta: (id: string) => void
    onText: (t: string) => void
    onToolStart: (tool: string, display: string) => void
    onToolResult: (tool: string, summary: string) => void
    onRef: (docId: string, title: string) => void
    onThinking: (t: string) => void
    onStatus: (m: string) => void
    onDone: () => void
    onError: (m: string) => void
  }
  signal: AbortSignal
  convId?: string
} | null = null

let listResult: { id: string; title: string }[] = []

vi.mock('../api/chat', () => ({
  streamChat: (question: string, docIds: string[], callbacks: unknown, signal: AbortSignal, convId?: string) => {
    captured = { question, docIds, callbacks: callbacks as NonNullable<typeof captured>['callbacks'], signal, convId }
  },
  listConversations: vi.fn(async () => listResult),
  getConversation: vi.fn(async () => null),
  rebuildConversationMessages: vi.fn(async () => ({ status: 'ok' })),
}))

beforeEach(() => {
  captured = null
  listResult = []
  useStore.setState({ currentDocId: null, docs: [] })
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
  })
})

// 触发 callbacks 驱动流式推进
function drive(cb: (c: NonNullable<typeof captured>['callbacks']) => void) {
  expect(captured).not.toBeNull()
  cb(captured!.callbacks)
}

describe('sendMessage — 流式状态机', () => {
  it('创建 user + assistant 占位消息，streaming=true', () => {
    useChatStore.getState().sendMessage('你好')
    const msgs = useChatStore.getState().messages
    expect(msgs).toHaveLength(2)
    expect(msgs[0]).toMatchObject({ role: 'user', content: '你好' })
    expect(msgs[1]).toMatchObject({ role: 'assistant', content: '', streaming: true })
    expect(useChatStore.getState().streaming).toBe(true)
    expect(useChatStore.getState().generating).toBe(true)
  })

  it('onText 打字机增量追加 content', () => {
    useChatStore.getState().sendMessage('问题')
    drive(cb => { cb.onText('你'); cb.onText('好') })
    const last = useChatStore.getState().messages[1]
    expect(last.content).toBe('你好')
  })

  it('onDone 复位 streaming 并保留内容', () => {
    useChatStore.getState().sendMessage('问题')
    drive(cb => { cb.onText('答案'); cb.onDone() })
    const s = useChatStore.getState()
    expect(s.streaming).toBe(false)
    expect(s.generating).toBe(false)
    expect(s.messages[1]).toMatchObject({ content: '答案', streaming: false })
  })

  it('onError 标 isError + 结束流式', () => {
    useChatStore.getState().sendMessage('问题')
    drive(cb => cb.onError('调用失败'))
    const s = useChatStore.getState()
    expect(s.streaming).toBe(false)
    expect(s.messages[1]).toMatchObject({ isError: true, streaming: false })
  })

  it('onMeta 新会话建立 currentId + 插入会话列表', () => {
    useChatStore.getState().sendMessage('新问题')
    drive(cb => cb.onMeta('conv_new'))
    const s = useChatStore.getState()
    expect(s.currentId).toBe('conv_new')
    expect(s.conversations).toHaveLength(1)
    expect(s.conversations[0].id).toBe('conv_new')
  })

  it('docIds = 当前选中文档 + 本条引用去重', () => {
    useStore.setState({ currentDocId: 'docA' })
    useChatStore.getState().sendMessage('问题', [
      { docId: 'docB', title: 'B' },
      { docId: 'docA', title: 'A' }, // 与 currentDocId 重复，去重
    ])
    expect(captured!.docIds).toEqual(['docA', 'docB'])
  })

  it('无引用时 docIds 只含当前选中文档', () => {
    useStore.setState({ currentDocId: 'docA' })
    useChatStore.getState().sendMessage('问题')
    expect(captured!.docIds).toEqual(['docA'])
  })
})

describe('toolCalls — 工具气泡三态', () => {
  it('onToolStart 创建 running，onToolResult 标记 done', () => {
    useChatStore.getState().sendMessage('问题')
    drive(cb => {
      cb.onToolStart('search_docs', '正在搜索知识库: x')
      cb.onToolResult('search_docs', '找到 2 个相关文档')
    })
    const last = useChatStore.getState().messages[1]
    expect(last.toolCalls).toHaveLength(1)
    expect(last.toolCalls![0]).toMatchObject({ tool: 'search_docs', status: 'done', summary: '找到 2 个相关文档' })
  })
})

describe('onRef / onThinking — 引用与思考段', () => {
  it('onRef 追加去重 docRefs', () => {
    useChatStore.getState().sendMessage('问题')
    drive(cb => { cb.onRef('docX', '文档X'); cb.onRef('docX', '文档X') })
    const last = useChatStore.getState().messages[1]
    expect(last.docRefs).toHaveLength(1)
    expect(last.docRefs![0]).toEqual({ docId: 'docX', title: '文档X' })
  })

  it('onThinking 累积思考段', () => {
    useChatStore.getState().sendMessage('问题')
    drive(cb => { cb.onThinking('思考'); cb.onThinking('过程') })
    expect(useChatStore.getState().messages[1].thinking).toBe('思考过程')
  })
})

describe('regenerate — 重新生成', () => {
  const seed = (): ChatMsg[] => [
    { id: 'm1', role: 'user', content: '问题1', date: 'd' },
    { id: 'm2', role: 'assistant', content: '回答1', date: 'd' },
    { id: 'm3', role: 'user', content: '问题2', date: 'd' },
    { id: 'm4', role: 'assistant', content: '回答2', date: 'd' },
  ]

  it('assistant 消息重新生成 → 删配对 user+回复，重发 user 内容', async () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    // ⚠️ 必须 await：rebuild 落定之后才发流式请求（P1-14），
    //    不 await 的话 `captured` 在本次同步执行里还是 null
    await useChatStore.getState().regenerate('m4')
    expect(captured!.question).toBe('问题2')
    const msgs = useChatStore.getState().messages
    expect(msgs.map(m => m.id).slice(0, 2)).toEqual(['m1', 'm2']) // m3/m4 已截断
    expect(msgs).toHaveLength(4)
    expect(msgs[2]).toMatchObject({ role: 'user', content: '问题2' }) // 重发的新 user
    expect(msgs[3]).toMatchObject({ role: 'assistant', streaming: true })
  })

  it('user 消息重新生成 → 删自身及回复', async () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    await useChatStore.getState().regenerate('m3')
    expect(captured!.question).toBe('问题2')
    const msgs = useChatStore.getState().messages
    expect(msgs.map(m => m.id).slice(0, 2)).toEqual(['m1', 'm2'])
    expect(msgs[2]).toMatchObject({ role: 'user', content: '问题2' })
  })

  it('流式中禁止 regenerate', () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: true })
    useChatStore.getState().regenerate('m4')
    expect(captured).toBeNull()
  })
})

describe('editMessage — 分支语义', () => {
  const seed = (): ChatMsg[] => [
    { id: 'm1', role: 'user', content: '问题1', date: 'd' },
    { id: 'm2', role: 'assistant', content: '回答1', date: 'd' },
  ]

  it('编辑 user 消息 → 截断该条及之后，以新内容立即重发', async () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    await useChatStore.getState().editMessage('m1', '修改后的问题')
    expect(captured!.question).toBe('修改后的问题')
    const msgs = useChatStore.getState().messages
    expect(msgs).toHaveLength(2) // m1/m2 已截断，重新 append 新 user+bot
    expect(msgs[0]).toMatchObject({ role: 'user', content: '修改后的问题' })
    expect(msgs[1]).toMatchObject({ role: 'assistant', streaming: true })
  })

  it('编辑 assistant 消息 → 仅本地覆盖文本，不重发', () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    useChatStore.getState().editMessage('m2', '手动修正的答案')
    expect(captured).toBeNull()
    expect(useChatStore.getState().messages[1].content).toBe('手动修正的答案')
  })
})

// ═══════════════════════════════════════════════════════════
// P1-14 — rebuild 必须**先落定**，流式 POST 才能发出去
//
// 后端 PUT 是整表覆盖，而 `/chat/stream` 在返回 StreamingResponse **之前**就
// append 了用户提问。两个请求若不按序到达，PUT 后到 = 刚发出的提问被整表抹掉
// （用户看到「问完立刻消失」）。
//
// ⚠️ 判据是**两个请求的先后**，不是「有没有报错」—— 所以修复前那两处 `.catch()`
//    对本条零作用，光测「失败时有 toast」也照样全绿。做法：把 rebuild 卡在一个
//    受控 promise 上，断言「它没 resolve 之前 captured（= streamChat 已发）仍为 null」。
// ⚠️ 对照：把实现改回不 await（`.catch()` 形式）→ 第一个断言红。
// ═══════════════════════════════════════════════════════════
describe('rebuild 与流式 POST 的先后（P1-14）', () => {
  const seed = (): ChatMsg[] => [
    { id: 'm1', role: 'user', content: '问题1', date: 'd' },
    { id: 'm2', role: 'assistant', content: '回答1', date: 'd' },
  ]

  /** 让下一次 rebuild 挂起，返回「放行」函数 */
  function gateNextRebuild(): () => void {
    let release!: () => void
    const gate = new Promise<void>(r => { release = () => r() })
    vi.mocked(chatApi.rebuildConversationMessages).mockImplementationOnce(async () => {
      await gate
      return { status: 'ok' }
    })
    return release
  }

  it('regenerate：rebuild 未落定 → 不发 /chat/stream', async () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    const release = gateNextRebuild()

    const p = useChatStore.getState().regenerate('m2')
    // macrotask 让所有 microtask 跑完：若实现没 await，这里 captured 早已非 null
    await new Promise(r => setTimeout(r, 0))
    expect(captured).toBeNull()

    release()
    await p
    expect(captured!.question).toBe('问题1')
    expect(vi.mocked(chatApi.rebuildConversationMessages)).toHaveBeenCalled()
  })

  it('editMessage（user 分支）：同上', async () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    const release = gateNextRebuild()

    const p = useChatStore.getState().editMessage('m1', '改过的问题')
    await new Promise(r => setTimeout(r, 0))
    expect(captured).toBeNull()

    release()
    await p
    expect(captured!.question).toBe('改过的问题')
  })

  it('对照：无 currentId 时（不留档的临时会话）仍然直接发，不被 await 卡住', async () => {
    // 守卫不能做成「一律等一个不存在的请求」—— 没有会话就没 rebuild 可等。
    useChatStore.setState({ messages: seed(), currentId: null, streaming: false })
    await useChatStore.getState().regenerate('m2')
    expect(captured!.question).toBe('问题1')
  })
})

describe('deleteMessage / stopGenerating', () => {
  it('deleteMessage 移除单条', () => {
    useChatStore.setState({
      messages: [
        { id: 'm1', role: 'user', content: 'a', date: 'd' },
        { id: 'm2', role: 'assistant', content: 'b', date: 'd' },
      ],
    })
    useChatStore.getState().deleteMessage('m1')
    expect(useChatStore.getState().messages.map(m => m.id)).toEqual(['m2'])
  })

  it('流式中禁止删除（与 regenerate / editMessage 守卫一致）', () => {
    // 否则 rebuild 会把「含半成品回复的快照」整表回写，而流式那条还在 append
    useChatStore.setState({
      streaming: true,
      messages: [
        { id: 'm1', role: 'user', content: 'a', date: 'd' },
        { id: 'm2', role: 'assistant', content: 'b', date: 'd', streaming: true },
      ],
    })
    useChatStore.getState().deleteMessage('m1')
    expect(useChatStore.getState().messages.map(m => m.id)).toEqual(['m1', 'm2'])
  })

  it('stopGenerating abort 活跃请求的 signal', () => {
    useChatStore.getState().sendMessage('问题')
    expect(captured!.signal.aborted).toBe(false)
    useChatStore.getState().stopGenerating()
    expect(captured!.signal.aborted).toBe(true)
  })
})

describe('会话切换', () => {
  it('newChat 重置为空白会话', () => {
    useChatStore.setState({ messages: [{ id: 'm1', role: 'user', content: 'a', date: 'd' }], currentId: 'conv1' })
    useChatStore.getState().newChat()
    const s = useChatStore.getState()
    expect(s.currentId).toBeNull()
    expect(s.messages).toEqual([])
    expect(s.topic).toBe('新对话')
  })
})
