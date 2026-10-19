/**
 * 对话侧「回答依据」三档（`mbw_chatPrefs.answerMode`）：只用知识库 / 允许发散 / 自由对话。
 *
 * 用户原话是「加个全局开关来确定它是否严格遵循还是发散」，随后补充「顺便问问的那些
 * 似乎需要极致的发散」——于是是**三档**而不是布尔。三档的文案与判据住在后端
 * （`scope_strict.md` / `scope_open.md` / `scope_free.md`，见 `test_chat_answer_mode.py`），
 * 本文件只管前端这一半：**偏好怎么存、参数怎么拼、界面上怎么选**。
 *
 * ⚠️ 要防的失效（全是静默的）：
 *
 *   A. 默认值不是 `strict` → 所有存量用户的行为被悄悄改掉（同 `webSearch` 默认开的反面）。
 *   B. 偏离默认时**没把参数发出去** → 后端永远收到默认值，用户切了档看不到任何变化。
 *   C. 保存时把别的字段重置回默认 → 「选了发散，联网自己关了」这种，零报错。
 *   D. 三档做成「点一下循环一档」→ 用户点两下不知道自己在哪档（故是下拉选择）。
 *
 * ⚠️ 与 `chatWebSearch.test.tsx` 分家：那个文件测联网与公网来源，本文件测回答依据。
 * 脚手架各留一份（理由同它文件头：抽第三个文件会让「这个测试 mock 了什么」多一层跳转）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, fireEvent, act } from '@testing-library/react'
import { useChatStore } from '../store/chatStore'
import { useStore } from '../store'
import { loadChatPrefs, saveChatPrefs, CHAT_DEFAULTS } from '../lib/chat-prefs'
import { streamChat } from '../api/chat'
import { ChatInput } from '../components/ai-chat/ChatInput'

const PREFS_KEY = 'mbw_chatPrefs'

/** 每次 `streamChat` 打出去的 URL —— 参数拼装断言用 */
let fetchUrls: string[] = []

beforeEach(() => {
  localStorage.clear()
  fetchUrls = []
  useChatStore.setState({
    conversations: [], currentId: null, topic: '新对话', messages: [],
    streaming: false, generating: false, statusText: '',
    contextWindow: null, budget: null, lastUsage: null,
    totalUsage: { prompt: 0, completion: 0 }, compressedHint: null,
    clearContextIndex: null, priceIn: null, priceOut: null,
  })
  useStore.setState({ currentDocId: null, docs: [] })
  // ⚠️ 不 mock `api/chat`：下面第 2 组直接调**真实的** `streamChat`，断言的就是它拼的 URL
  // （mock 掉等于把被测的那一层也换掉了，同 `chatWebSearch.test.tsx` 的理由）。
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    fetchUrls.push(String(input))
    return { ok: true, status: 200, body: new ReadableStream() } as unknown as Response
  })
})

afterEach(() => { vi.restoreAllMocks() })

const saved = () => JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}')

// ═══════════════════════════════════════════════════════════
// 1. 偏好层 —— 默认 = 改造前的行为
// ═══════════════════════════════════════════════════════════

describe('偏好（lib/chat-prefs）', () => {
  it('默认 strict（改造前就是「只用知识库、无来源不说话」）', () => {
    // ⚠️ 与 `webSearch`（默认开）方向看起来相反，判据是同一条：
    // 「默认值必须等于改造前的行为」。改这里 = 替所有用户改了回答纪律。
    expect(loadChatPrefs().answerMode).toBe('strict')
    expect(CHAT_DEFAULTS.answerMode).toBe('strict')
  })

  it('三档都能存能读（localStorage 的字段名与取值都是磁盘契约）', () => {
    for (const m of ['strict', 'open', 'free'] as const) {
      saveChatPrefs({ answerMode: m })
      expect(loadChatPrefs().answerMode).toBe(m)
    }
  })

  it('⚠️ 合并式保存：改这一项不重置联网 / 追问', () => {
    // 对照实验：把 `saveChatPrefs` 改回整对象覆盖 → 这里红，而症状是
    // 「用户关掉联网，再切个回答依据，联网又自己打开了」。
    saveChatPrefs({ webSearch: false, followUp: true })
    saveChatPrefs({ answerMode: 'free' })
    const p = loadChatPrefs()
    expect(p.answerMode).toBe('free')
    expect(p.webSearch).toBe(false)
    expect(p.followUp).toBe(true)

    // 反向：改别的字段不该把档位打回默认
    saveChatPrefs({ webSearch: true })
    expect(loadChatPrefs().answerMode).toBe('free')
  })

  it('读回宽：未知档位 / 类型错 → 只退回该项默认，不崩也不整包丢弃', () => {
    // ⚠️ 与后端 `_answer_mode`（**入口严**，非法值显式报错）刻意不同：
    // 这边读的是**磁盘上可能过期的值**（旧版本残留 / 手改 localStorage），
    // 那时落默认档比抛错安全 —— 用户并没有「刚选了一个非法值」。
    for (const bad of ['loose', '', 123, null, true, 'Strict', 'OPEN']) {
      localStorage.setItem(PREFS_KEY, JSON.stringify({ answerMode: bad, webSearch: false }))
      expect(loadChatPrefs().answerMode).toBe('strict')
      expect(loadChatPrefs().webSearch).toBe(false)   // 另一项照常保留
    }

    localStorage.setItem(PREFS_KEY, '{这不是 JSON')
    expect(loadChatPrefs().answerMode).toBe('strict')
  })
})

// ═══════════════════════════════════════════════════════════
// 2. 参数层 —— 只在偏离默认时才发出去
// ═══════════════════════════════════════════════════════════

describe('answerMode → 请求参数', () => {
  const call = (answerMode?: 'strict' | 'open' | 'free') => {
    streamChat('问题', [], { onMeta() {}, onText() {}, onToolStart() {}, onDone() {}, onError() {} },
      { answerMode })
    return fetchUrls[fetchUrls.length - 1]
  }

  it('默认档（strict）→ URL 完全不含 answer_mode', () => {
    // ⚠️ 写成 `answer_mode=strict` 与不写是同一件事，而**少一个参数**是
    // 「与改造前逐字相同」里可被断言的那种形式（同 `webSearch` 的纪律）。
    expect(call()).not.toContain('answer_mode')
    expect(call('strict')).not.toContain('answer_mode')
    expect(call(undefined)).not.toContain('answer_mode')
  })

  it('open / free → 带参数', () => {
    expect(call('open')).toContain('answer_mode=open')
    expect(call('free')).toContain('answer_mode=free')
  })

  it('三项偏好互不干扰（三个参数各管各的）', () => {
    streamChat('问题', [], { onMeta() {}, onText() {}, onToolStart() {}, onDone() {}, onError() {} },
      { answerMode: 'free', webSearch: false, followUp: true })
    const url = fetchUrls[fetchUrls.length - 1]
    expect(url).toContain('answer_mode=free')
    expect(url).toContain('web_search=0')
    expect(url).toContain('follow_up=1')
  })
})

// ═══════════════════════════════════════════════════════════
// 3. store 接线 —— 发送那一刻现读偏好
// ═══════════════════════════════════════════════════════════

describe('chatStore 发送时带上当前档', () => {
  it('存的是 free → 这一轮的 URL 就带 free', () => {
    saveChatPrefs({ answerMode: 'free' })
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).toContain('answer_mode=free')
  })

  it('存的是 strict（默认）→ 不带参数', () => {
    useChatStore.getState().sendMessage('问题')
    expect(fetchUrls[0]).not.toContain('answer_mode')
  })
})

// ═══════════════════════════════════════════════════════════
// 4. 界面 —— 下拉选择，不是「点一下循环一档」
// ═══════════════════════════════════════════════════════════

describe('ChatInput 的三档选择器', () => {
  const openMenu = (c: HTMLElement) => {
    fireEvent.click(c.querySelector('[data-answer-mode]')!)
    return c.querySelector('[data-answer-mode-option]') !== null
  }

  it('默认渲染 strict 档，三句话都能在浮层里看到', () => {
    const { container } = render(<ChatInput scale="md" />)
    expect(container.querySelector('[data-answer-mode]')!.getAttribute('data-answer-mode')).toBe('strict')

    expect(openMenu(container)).toBe(true)
    // ⚠️ 描述写「库里没有时怎么办」，而不是抽象的「严格 / 发散」——
    // 那是三档唯一真正的区别，也是用户在意的那一点。
    const text = container.textContent!
    expect(text).toContain('只用知识库')
    expect(text).toContain('允许发散')
    expect(text).toContain('自由对话')
    expect(text).toContain('库里没有就说没找到')
  })

  it('选中即落盘 + 按钮反映当前档 + 浮层收起', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)
    fireEvent.click(container.querySelector('[data-answer-mode-option="free"]')!)

    expect(saved().answerMode).toBe('free')
    expect(container.querySelector('[data-answer-mode]')!.getAttribute('data-answer-mode')).toBe('free')
    expect(container.querySelector('[data-answer-mode-option]')).toBeNull()   // 收起
  })

  it('⚠️ 再点一次当前档不会变成「无档」（是选择不是 toggle）', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)
    fireEvent.click(container.querySelector('[data-answer-mode-option="open"]')!)
    openMenu(container)
    fireEvent.click(container.querySelector('[data-answer-mode-option="open"]')!)

    expect(saved().answerMode).toBe('open')
    expect(container.querySelector('[data-answer-mode]')!.getAttribute('data-answer-mode')).toBe('open')
  })

  it('点外部关闭浮层（与 @ 浮层共用同一个 document 监听）', () => {
    // 对照实验：把那个 effect 写成「只处理 refOpen、只 setRefOpen(false)」→ 这里红
    // （浮层关不掉），而那正是「两个浮层各挂一个监听」的退化形态。
    const { container } = render(<ChatInput scale="md" />)
    expect(openMenu(container)).toBe(true)
    fireEvent.mouseDown(document.body)
    expect(container.querySelector('[data-answer-mode-option]')).toBeNull()
    // 关浮层 ≠ 改档；且**默认值不落盘**（打开又关掉不该在 localStorage 里留下痕迹）
    expect(container.querySelector('[data-answer-mode]')!.getAttribute('data-answer-mode')).toBe('strict')
    expect(localStorage.getItem(PREFS_KEY)).toBeNull()
  })

  it('点浮层内部不关闭（只做选择）', () => {
    const { container } = render(<ChatInput scale="md" />)
    openMenu(container)
    fireEvent.mouseDown(container.querySelector('[data-answer-mode-option="open"]')!)
    expect(container.querySelectorAll('[data-answer-mode-option]')).toHaveLength(3)
  })
})

// ═══════════════════════════════════════════════════════════
// 5. 两处入口共享一份状态（2026-10-20 用户拍板「不按入口区分」）
//
// 用户问的是「要不要按入口区分」，选的是「设置页与输入框显示同一个值，
// 改任一处另一处跟着变」。判据：**有第二个入口就必须有一份订阅** ——
// 两处各自 `useState(初始值)` 的失效形态是全静默的：设置页改完，正在挂着的
// 输入框仍显示旧档，而发送那一刻 `dispatchSend` 读的又是新值 ——
// 按钮说「只用知识库」、实际按「自由对话」跑，界面上完全看不出来。
// ═══════════════════════════════════════════════════════════

describe('两处入口共享一份状态', () => {
  const modeOf = (c: HTMLElement) =>
    c.querySelector('[data-answer-mode]')!.getAttribute('data-answer-mode')

  it('⚠️ 设置页改档 → 已挂载的输入框立刻跟着变', () => {
    // 对照实验：把 `ChatInput` 改回 `useState(loadChatPrefs())` → 这里红
    // （按钮停在 strict），而发送出去的是 free。
    const { container } = render(<ChatInput scale="md" />)
    expect(modeOf(container)).toBe('strict')

    // 设置页点单选组时调的就是这一个函数（源码守卫钉住它是唯一写入口）
    act(() => { saveChatPrefs({ answerMode: 'free' }) })
    expect(modeOf(container)).toBe('free')

    act(() => { saveChatPrefs({ answerMode: 'open' }) })
    expect(modeOf(container)).toBe('open')
  })

  it('反过来：在输入框里选档 → 设置页读到的值同步变', () => {
    const { container } = render(<ChatInput scale="md" />)
    fireEvent.click(container.querySelector('[data-answer-mode]')!)
    fireEvent.click(container.querySelector('[data-answer-mode-option="free"]')!)

    // 设置页的 `useChatPrefs()` 与 `loadChatPrefs()` 同源（同一个缓存）
    expect(loadChatPrefs().answerMode).toBe('free')
  })

  it('联网 / 追问两个开关同样两处同源（不只是回答依据）', () => {
    const { container } = render(<ChatInput scale="md" />)
    const web = () => container.querySelector('[data-websearch-toggle]')!.getAttribute('data-websearch-toggle')
    const fu = () => container.querySelector('[data-followup-toggle]')!.getAttribute('data-followup-toggle')
    expect(web()).toBe('on')     // 默认开 = 改造前现状
    expect(fu()).toBe('off')

    act(() => { saveChatPrefs({ webSearch: false, followUp: true }) })
    expect(web()).toBe('off')
    expect(fu()).toBe('on')
  })

  it('快照引用必须稳定（否则 useSyncExternalStore 会无限重渲染）', () => {
    // `getSnapshot` 每次返回新对象 = React 认为「变了」→ 重渲染 → 再取 → 再变。
    expect(loadChatPrefs()).toBe(loadChatPrefs())
    saveChatPrefs({ answerMode: 'free' })
    expect(loadChatPrefs()).toBe(loadChatPrefs())

    // 但缓存是**以磁盘原始串为键**，不是一次性缓存：
    // 手改 / 清空 localStorage 之后必须立刻读到新值（设置页与测试都依赖它）。
    localStorage.clear()
    expect(loadChatPrefs().answerMode).toBe('strict')
  })
})

describe('源码守卫：没有第二份状态、也没有第二份文案', () => {
  const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), 'utf-8')
  /** 剥块注释与整行 `//`：这些文件里**故意**写着「别用 X」的提醒是常态 */
  const stripComments = (s: string) =>
    s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

  it('设置页用订阅而不是挂载时读一次（否则「改完不刷新」会复发）', () => {
    // 对照实验：把 `useChatPrefs()` 换成 `useState(loadChatPrefs())` → 这里红。
    // ⚠️ 这条守卫的价值在于**第二种入口**：`ChatInput` 那边改对了、设置页漏了，
    //    现象只出现在「先开设置页改、再回对话」这条路上。
    const src = stripComments(read('src/components/settings/SettingsPage.tsx'))
    expect(src).toContain('useChatPrefs()')
    expect(src).not.toContain('useState(loadChatPrefs')

    // 三项都要有写入口（漏一项 = 设置页少了那个开关，用户找不到）
    expect(src).toContain('saveChatPrefs({ webSearch')
    expect(src).toContain('saveChatPrefs({ followUp')
    expect(src).toContain('saveChatPrefs({ answerMode')
  })

  it('文案只有一份（输入框不内联三档字面量，两处说法不会打架）', () => {
    const src = stripComments(read('src/components/ai-chat/ChatInput.tsx'))
    expect(src).toContain('ANSWER_MODE_OPTIONS')
    for (const s of ['只用知识库', '允许发散', '自由对话']) {
      expect(src).not.toContain(s)
    }
  })
})
