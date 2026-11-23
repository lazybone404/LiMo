/**
 * 对话注入哪一份正文（`part`，2026-11-24）。
 *
 * 判据来自 2026-11-23 的讨论：「用户在哪，就看哪」—— 在笔记 tab 提问就该注入笔记。
 *
 * ⚠️ 要防的失效是**不同名**：笔记那个 tab 的 key 是 `summary`，而后端的产物名是
 * `notes`。直接透传 `activeView` 的形态是「后端认不出 → 静默退回逐字稿」，
 * 界面上完全看不出来（用户在看笔记，模型读的是转录）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { streamChat } from '../api/chat'
import { useStore } from '../store'
import { useChatStore } from '../store/chatStore'
import { VIEW_PART } from '../lib/tabRegistry'

let fetchUrls: string[] = []

beforeEach(() => {
  localStorage.clear()
  fetchUrls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    fetchUrls.push(String(input))
    return { ok: true, status: 200, body: new ReadableStream() } as unknown as Response
  })
  useStore.setState({ activeView: 'polished' })
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
    contextWindow: null, budget: null, lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 }, compressedHint: null,
    clearContextIndex: null, priceIn: null, priceOut: null,
  })
})

afterEach(() => { vi.restoreAllMocks() })

const call = (part?: string) => {
  streamChat('问题', [], { onMeta() {}, onText() {}, onToolStart() {}, onDone() {}, onError() {} },
    { part })
  return fetchUrls[fetchUrls.length - 1]
}

describe('视图 → 产物名的映射', () => {
  it('⚠️ 笔记 tab 的 key 是 summary，产物名是 notes', () => {
    expect(VIEW_PART.summary).toBe('notes')
    expect(VIEW_PART.polished).toBe('polished')
    expect(VIEW_PART.transcript).toBe('transcript')
  })

  it('不是「正文的某一份」的视图不映射（原图 / 数据预览 / 思维导图）', () => {
    for (const v of ['original', 'data_preview', 'mindmap', 'ocr']) {
      expect(VIEW_PART[v]).toBeUndefined()
    }
  })
})

describe('请求参数', () => {
  it('默认（逐字稿）不设参数 —— 与改造前逐字相同', () => {
    expect(call()).not.toContain('part=')
    expect(call('transcript')).not.toContain('part=')
  })

  it('笔记 / 整理版带上产物名', () => {
    expect(call('notes')).toContain('part=notes')
    expect(call('polished')).toContain('part=polished')
  })
})

describe('chatStore 按当前视图发 part', () => {
  const send = () => {
    useChatStore.getState().sendMessage('问题')
    return fetchUrls[fetchUrls.length - 1]
  }

  it('在笔记 tab 提问 → part=notes', () => {
    useStore.setState({ activeView: 'summary' })
    expect(send()).toContain('part=notes')
  })

  it('在数据预览 tab 提问 → 不传（那不是正文的某一份）', () => {
    useStore.setState({ activeView: 'data_preview' })
    expect(send()).not.toContain('part=')
  })
})
