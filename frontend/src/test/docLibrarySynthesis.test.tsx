/**
 * 合成入口 —— BatchBar 的「合成」按钮 + 确认对话框（M9）。
 *
 * 三条判据，每条都对应一个「看起来对、其实错」的实现：
 *
 * ① **单篇可点**：`disabled = busy || selectedCount < 2` 写成 `selectedCount > 0`
 *    照样「有按钮、能点」，只是点下去要么被后端拒（白跑一次网络），要么真做出一篇
 *    「1 合 1」的伪综述。单篇没有可对比的对象——**这不是参数错误，是语义不成立**。
 *
 * ② **带出去的 id 不是选中集**：最省事的写法是传 `[currentDocId]`（当前预览的那篇），
 *    而 currentDocId **就在选中集里**，所以用户看到的界面完全正常、请求也成功，
 *    只是综述少了几篇。故断言的是 **`fetch` 出去的 body 本体**，不是「调了 streamSynthesis」。
 *    本文件刻意**不 mock `api/synthesis`**，让真实实现跑——这样 body 字段名
 *    （`doc_ids` vs `docIds`）也在覆盖范围内。
 *
 * ③ **done 之后自动发起生成**：D7 已拍板「只创建、不自动生成」（用户要先核对
 *    「## 来源文档」清单再花 token）。若有人补上「顺手帮你生成」，界面上看不出区别，
 *    只有「本测试期间没有任何 `/generate/` 请求」能拦住。⚠️ 这条判据的**捕获网**是
 *    fetch——项目所有 HTTP 都经 `apiUrl()`/`apiFetch()` 落到 fetch，所以无论对方
 *    用 `streamGenerate` 还是裸 fetch，只要发了就一定会被记到。
 *
 * 对照实验（均已实测，证明本文件有区分力）：
 *   A. `disabled={busy}`（去掉 selectedCount < 2）→ 红（「1 篇时禁用」）
 *   B. `doc_ids: sourceDocIds` → `doc_ids: [currentDocId]` → 红（body 断言）
 *   C. 在 onDone 里补一行 `fetch('/api/generate/x/stream')` → 红（「无生成请求」）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'

// jsdom 没有 IntersectionObserver —— 空实现正好让渲染深度停在首页
class IO {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
;(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = IO

vi.mock('../api/taskManager', () => ({ cancelTask: vi.fn() }))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    listFolders: vi.fn().mockResolvedValue([]),
    listTranscripts: vi.fn().mockResolvedValue([]),
    getMarkdown: vi.fn(),
    createFolder: vi.fn(),
    renameFolder: vi.fn(),
    deleteFolder: vi.fn(),
    updateTranscript: vi.fn(),
    batchTranscripts: vi.fn(),
  }
})

const { DocLibrary } = await import('../components/doc-library/DocLibrary')

interface Captured { url: string; init: RequestInit }
let captured: Captured[] = []

/** 一条 SSE：先回 done（合成成功），再正常结束流 */
function sseDone(id: string): Response {
  const chunk = new TextEncoder().encode(
    `data: ${JSON.stringify({ type: 'done', id, mode: 'stuff', degraded: [] })}\n\n`,
  )
  let sent = false
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => (sent ? { done: true, value: undefined } : ((sent = true), { done: false, value: chunk })),
        cancel: async () => {},
      }),
    },
  } as unknown as Response
}

beforeEach(() => {
  captured = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    captured.push({ url: String(url), init })
    return sseDone('tr_new')
  }))
  useStore.setState({
    toasts: [], selectedIds: new Set(), listLimit: 60, docsLoading: false,
    folders: [], currentDocId: null, searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function twoDocs(): DocEntry[] {
  return [
    { id: 'tr_a', title: '论文 A', sourceType: 'pdf', engine: 'e', contentHash: 'ha', tags: [], createdAt: '2026-01-02T00:00:00' },
    { id: 'tr_b', title: '论文 B', sourceType: 'pdf', engine: 'e', contentHash: 'hb', tags: [], createdAt: '2026-01-01T00:00:00' },
  ]
}

/** 勾中前 n 行的复选框（工具条随之出现） */
function selectRows(container: HTMLElement, n: number) {
  const toggles = container.querySelectorAll('[data-select-toggle]')
  for (let i = 0; i < n; i++) fireEvent.click(toggles[i] as HTMLElement)
}

/** 合成按钮：title 两种文案都含「对比综述」，故按它取（同时避开行内其它按钮） */
const synthBtn = (container: HTMLElement) =>
  container.querySelector('button[title*="对比综述"]') as HTMLButtonElement

describe('DocLibrary — 合成按钮门控', () => {
  it('1 篇时禁用，2 篇时可点', () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)

    selectRows(container, 1)
    // ⚠️ 对照 A：`disabled={busy}` → 此处为 false → 红
    expect(synthBtn(container).disabled).toBe(true)
    // 禁用时 title 必须解释「为什么不能点」，否则是个哑掉的按钮
    expect(synthBtn(container).title).toContain('至少选择 2 篇')

    // 勾第二篇累加到 2 —— ⚠️ 不能复用 `selectRows(container, 2)`：它从第 0 行起勾，
    // 会把刚勾上的第一篇**再点一次（= 取消）**，于是选中数停在 1、工具条还在但结果反了。
    fireEvent.click(container.querySelectorAll('[data-select-toggle]')[1] as HTMLElement)
    expect(synthBtn(container).disabled).toBe(false)
    expect(synthBtn(container).title).toContain('2 篇')
  })
})

describe('DocLibrary — 合成请求形状', () => {
  it('body 里的 doc_ids 等于选中集，标题/指引原样带出', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)

    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    // 对话框要先列出「将按以下顺序合成」，用户靠它核对选对没有
    expect(container.querySelector('[data-synth-sources]')!.textContent).toContain('论文 A')
    expect(container.querySelector('[data-synth-sources]')!.textContent).toContain('论文 B')

    fireEvent.change(container.querySelector('[data-synth-title]')!, { target: { value: '两篇对比' } })
    fireEvent.change(container.querySelector('[data-synth-notes]')!, { target: { value: '重点比方法' } })
    fireEvent.click(container.querySelector('[data-confirm-synth]')!)

    await vi.waitFor(() => expect(captured.length).toBe(1))
    const body = JSON.parse(String(captured[0].init.body))
    // ⚠️ 对照 B：传 `[currentDocId]` → 此处变成 ['tr_a'] → 红。
    // 注意断言的是**逐项相等**（含顺序），不是 contains——顺序决定正文里 `【文档 1】` 是谁。
    expect(body.doc_ids).toEqual(['tr_a', 'tr_b'])
    expect(body.title).toBe('两篇对比')
    expect(body.user_notes).toBe('重点比方法')
    expect(captured[0].init.method).toBe('POST')
    expect(captured[0].url).toContain('/synthesis/stream')
  })
})

describe('DocLibrary — done 之后不自动生成（D7）', () => {
  it('创建完成后新文档进库、被选中，但不发任何 generate 请求', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)

    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    fireEvent.click(container.querySelector('[data-confirm-synth]')!)

    // 先确认 done 链路真的走通了——否则「没有 generate 请求」可能只是「整个回调没跑」
    await vi.waitFor(() => {
      expect(useStore.getState().docs.some(d => d.id === 'tr_new')).toBe(true)
    })
    const created = useStore.getState().docs.find(d => d.id === 'tr_new')!
    expect(created.sourceType).toBe('composite')
    expect(created.sourceDocIds).toEqual(['tr_a', 'tr_b'])
    // 建完自动选中新文档，用户接着就能在预览区看「## 来源文档」清单
    expect(useStore.getState().currentDocId).toBe('tr_new')

    // ⚠️ 对照 C：在 onDone 里补一次 streamGenerate → 此处出现 /generate/ → 红。
    // 判据是「一次都不许有」而非「没有第二次」：D7 要的是**完全手工触发**。
    expect(captured.filter(c => /\/generate\//.test(c.url))).toEqual([])
  })
})
