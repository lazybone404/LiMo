/**
 * chatStore 单测 — 消息增删 / streaming 生命周期 / regenerate / editMessage / stop / docIds 组合。
 *
 * 通过 mock api/chat.ts 捕获 streamChat 参数与 callbacks，测试驱动流式状态机。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, type ChatMsg } from '../store/chatStore'
import { useStore } from '../store'

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

  it('assistant 消息重新生成 → 删配对 user+回复，重发 user 内容', () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    useChatStore.getState().regenerate('m4')
    expect(captured!.question).toBe('问题2')
    const msgs = useChatStore.getState().messages
    expect(msgs.map(m => m.id).slice(0, 2)).toEqual(['m1', 'm2']) // m3/m4 已截断
    expect(msgs).toHaveLength(4)
    expect(msgs[2]).toMatchObject({ role: 'user', content: '问题2' }) // 重发的新 user
    expect(msgs[3]).toMatchObject({ role: 'assistant', streaming: true })
  })

  it('user 消息重新生成 → 删自身及回复', () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    useChatStore.getState().regenerate('m3')
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

  it('编辑 user 消息 → 截断该条及之后，以新内容立即重发', () => {
    useChatStore.setState({ messages: seed(), currentId: 'conv1', streaming: false })
    useChatStore.getState().editMessage('m1', '修改后的问题')
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
