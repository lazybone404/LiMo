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
import { render, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'
import type { Capabilities } from '../api/core'
import type { SynthesisPreview } from '../api/synthesis'

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

/** 预览端点返回的 payload —— 各用例按需覆写（reject / chunked / notes 排除） */
let previewData: SynthesisPreview = {
  context_window: 131072, budget: 63536, total_tokens: 1200,
  mode: 'stuff', reason: '', stuff_window: 2400, estimated_calls: 0,
  docs: [
    { id: 'tr_a', title: '论文 A', tokens: 600, plan: 'full', hasNotes: true, notesTokens: 200 },
    { id: 'tr_b', title: '论文 B', tokens: 600, plan: 'full', hasNotes: true, notesTokens: 200 },
  ],
  excluded: [],
}

/** done 事件额外字段（如 excluded），由各用例覆写 */
let streamExtra: Record<string, unknown> = {}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

/** 一条 SSE：先回 done（合成成功），再正常结束流 */
function sseDone(id: string): Response {
  const chunk = new TextEncoder().encode(
    `data: ${JSON.stringify({ type: 'done', id, mode: 'stuff', degraded: [], ...streamExtra })}\n\n`,
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

/** 取出「发往合成端点的那个请求」——打开对话框会先打一次 /synthesis/preview，不能按序号取 */
const streamCall = () => captured.find(c => c.url.includes('/synthesis/stream'))

beforeEach(() => {
  captured = []
  streamExtra = {}
  previewData = {
    context_window: 131072, budget: 63536, total_tokens: 1200,
    mode: 'stuff', reason: '', stuff_window: 2400, estimated_calls: 0,
    docs: [
      { id: 'tr_a', title: '论文 A', tokens: 600, plan: 'full', hasNotes: true, notesTokens: 200 },
      { id: 'tr_b', title: '论文 B', tokens: 600, plan: 'full', hasNotes: true, notesTokens: 200 },
    ],
    excluded: [],
  }
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    captured.push({ url: String(url), init })
    // ⚠️ 预览是 JSON、合成是 SSE —— 混在一起会让「预览」拿到 SSE body，
    // 于是 canRun 恒为 false，测试变成在测一个永远点不动的按钮
    if (String(url).includes('/synthesis/preview')) return jsonResponse(previewData)
    return sseDone('tr_new')
  }))
  useStore.setState({
    toasts: [], selectedIds: new Set(), listLimit: 60, docsLoading: false,
    folders: [], currentDocId: null, searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
    capabilities: undefined,
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

/**
 * 合成对话框内的节点一律**从 `document.body` 查**，不从 render 的 `container` 查。
 *
 * ⚠️ 对话框是 `createPortal(..., document.body)` 的：它写在一个带
 * `backdrop-blur-[20px]` 的 `<aside>` 里，而 `backdrop-filter` 会创建**包含块**，
 * 把 `position: fixed` 的后代锁进面板大小的一小块（用户原话「这一个界面还是卡在
 * 那一个文档栏里面」）。Portal 之后 `container.querySelector('[data-synth-…]')`
 * 恒为 `null` —— 于是 `fireEvent.click(null)` 报的是「provide a DOM element」，
 * 看起来像测试写坏了，实际是组件改了挂载位置。
 */
const inDialog = <T extends Element = HTMLElement>(sel: string): T | null =>
  document.body.querySelector<T>(sel)
const allInDialog = (sel: string) => document.body.querySelectorAll(sel)

/** 合成按钮：title 两种文案都含「对比综述」，故按它取（同时避开行内其它按钮） */
const synthBtn = (container: HTMLElement) =>
  container.querySelector('button[title*="对比综述"]') as HTMLButtonElement

/**
 * 展开「高级选项」——对比维度与材料体裁都移进了这个折叠区（读者意图才是主控件）。
 *
 * ⚠️ 折叠用的是**条件渲染**（`{synthAdvanced && …}`）而不是 CSS 隐藏：收起时那两个
 * 控件**根本不在 DOM 里**，所以「维度默认不可见」这条判据才有硬证据（见 §18.1）。
 */
function openAdvanced() {
  fireEvent.click(inDialog('[data-synth-advanced-toggle]') as HTMLElement)
}

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
  it('body 里的 doc_ids 等于选中集，标题原样带出', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)

    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    // 对话框要先列出「将按以下顺序合成」，用户靠它核对选对没有
    expect(inDialog('[data-synth-sources]')!.textContent).toContain('论文 A')
    expect(inDialog('[data-synth-sources]')!.textContent).toContain('论文 B')

    fireEvent.change(inDialog('[data-synth-title]')!, { target: { value: '两篇对比' } })
    fireEvent.click(inDialog('[data-confirm-synth]')!)

    await vi.waitFor(() => expect(streamCall()).toBeTruthy())
    const call = streamCall()!
    const body = JSON.parse(String(call.init.body))
    // ⚠️ 对照 B：传 `[currentDocId]` → 此处变成 ['tr_a'] → 红。
    // 注意断言的是**逐项相等**（含顺序），不是 contains——顺序决定正文里 `【文档 1】` 是谁。
    expect(body.doc_ids).toEqual(['tr_a', 'tr_b'])
    expect(body.title).toBe('两篇对比')
    // ⚠️ 旧字段 `user_notes`（「对比指引」）**已删除**——它是死链：后端签名里有、
    // 函数体零引用，用户填的指引对产物从来没有影响。这条断言钉住它没被加回来。
    expect(body).not.toHaveProperty('user_notes')
    expect(call.init.method).toBe('POST')
    expect(call.url).toContain('/synthesis/stream')
  })
})

describe('DocLibrary — 合成预览：每篇 token / 处理方式（§17.4）', () => {
  it('清单每行带出该篇 token 与预计处理方式', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    // token 只能来自预览端点——`docs` 是白名单元数据、不含正文，前端算不出来
    await vi.waitFor(() => expect(inDialog('[data-synth-plan]')).toBeTruthy())
    expect(inDialog('[data-synth-plan]')!.textContent).toContain('600')
    // 完整输入的篇不堆「完整输入」四个字（每行都有等于没有），只显 token
    expect(inDialog('[data-synth-plan]')!.textContent).not.toContain('完整输入')
  })

  it('over_window 篇触发窗口 banner，chunked 篇只做行内标注', async () => {
    previewData = {
      ...previewData,
      mode: 'map_reduce', total_tokens: 200000,
      docs: [
        { id: 'tr_a', title: '论文 A', tokens: 35000, plan: 'chunked', hasNotes: true, notesTokens: 200 },
        { id: 'tr_b', title: '论文 B', tokens: 165000, plan: 'over_window', hasNotes: true, notesTokens: 200 },
      ],
    }
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    await vi.waitFor(() => expect(inDialog('[data-synth-plan="chunked"]')).toBeTruthy())
    // chunked 是设计认可的终态（用户无需行动）→ **不得**触发「换个模型吧」的 banner
    expect(inDialog('[data-synth-window-banner]')).toBeTruthy()
    expect(inDialog('[data-synth-plan="chunked"]')!.textContent).toContain('分块抽取')
  })

  it('只有 chunked（无 over_window / 非 reject）→ 不出窗口 banner', async () => {
    previewData = {
      ...previewData,
      mode: 'map_reduce',
      docs: [
        { id: 'tr_a', title: '论文 A', tokens: 35000, plan: 'chunked', hasNotes: true, notesTokens: 200 },
        { id: 'tr_b', title: '论文 B', tokens: 36000, plan: 'chunked', hasNotes: true, notesTokens: 200 },
      ],
    }
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    await vi.waitFor(() => expect(inDialog('[data-synth-plan="chunked"]')).toBeTruthy())
    expect(inDialog('[data-synth-window-banner]')).toBeNull()
  })

  it('窗口不足（mode=reject）→ 出 banner、按钮禁用', async () => {
    previewData = {
      ...previewData, mode: 'reject', reason: '当前模型窗口不足以做多文档综述',
      stuff_window: 200000,
    }
    useStore.setState({
      docs: twoDocs(),
      capabilities: {
        llmModels: { deepseek: [{ id: 'x', name: '超大窗口模型', desc: '', context_window: 1000000 }] },
      } as unknown as Capabilities,
    })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    await vi.waitFor(() => expect(inDialog('[data-synth-window-banner]')).toBeTruthy())
    // 只提示、不给切换入口（§17.7）：推荐来自 capabilities.llmModels，不是前端写死的名单
    expect(inDialog('[data-synth-window-banner]')!.textContent).toContain('超大窗口模型')
    // ⚠️ 对照：去掉 canRun 里的 reject 分支 → 按钮可点 → 白等一次往返才被后端拒
    expect((inDialog('[data-confirm-synth]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('换模型推荐**排除已下线模型**（retired 非空不进推荐名单）', async () => {
    // 两个模型窗口都够大，差别只在 retired —— 窗口够大**不构成**推荐理由：
    // 推荐一个官方已下架的模型 = 让用户去设置页白配一趟（配完调不通，且提示里没线索）。
    //
    // ⚠️ 对照：去掉筛选里的 `!m.retired` → 「已下线大模型」出现在 banner 文本里 → 红。
    previewData = {
      ...previewData, mode: 'reject', reason: '当前模型窗口不足以做多文档综述',
      stuff_window: 200000,
    }
    useStore.setState({
      docs: twoDocs(),
      capabilities: {
        llmModels: {
          deepseek: [
            { id: 'y', name: '已下线大模型', desc: '', context_window: 1000000,
              retired: '2026-10-02', retired_note: '已下架，建议改用 在线大模型' },
            { id: 'x', name: '在线大模型', desc: '', context_window: 1000000 },
          ],
        },
      } as unknown as Capabilities,
    })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    await vi.waitFor(() => expect(inDialog('[data-synth-window-banner]')).toBeTruthy())
    const banner = inDialog('[data-synth-window-banner]')!.textContent!
    expect(banner).toContain('在线大模型')
    expect(banner).not.toContain('已下线大模型')
  })
})

describe('DocLibrary — 对比维度可控（§18）', () => {
  const withPresets = (): Capabilities => ({
    llmModels: {},
    dimensionPresets: [
      { key: 'paper', label: '论文综述', dimensions: ['研究问题', '方法'] },
      { key: 'tech', label: '技术资料', dimensions: ['背景', '方案'] },
    ],
  } as unknown as Capabilities)

  it('维度默认收在「高级」区里（它不再是主控件；读者意图才是）', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withPresets() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    // ⚠️ 对照：把维度留在外面（不折叠）→ 此处非 null → 红
    expect(inDialog('[data-synth-dims]')).toBeNull()
    expect(inDialog('[data-synth-advanced-toggle]')).toBeTruthy()
    openAdvanced()
    expect(inDialog('[data-synth-dims]')).toBeTruthy()
  })

  it('预设下拉消费 capabilities；默认预填首个预设、切换预设覆盖列表', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withPresets() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    openAdvanced()

    const select = inDialog('[data-synth-preset]') as HTMLSelectElement
    // ⚠️ 对照：前端硬编码预设名 → capabilities 换了文案它不变 → 红
    expect([...select.options].map(o => o.textContent)).toEqual(['论文综述', '技术资料'])

    const dims = () => (inDialog('[data-synth-dims]') as HTMLTextAreaElement).value
    expect(dims()).toBe('研究问题\n方法')
    fireEvent.change(select, { target: { value: 'tech' } })
    expect(dims()).toBe('背景\n方案')
  })

  it('用户编辑后的维度原样进 body（每行一个，去空行）', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withPresets() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    openAdvanced()

    fireEvent.change(inDialog('[data-synth-dims]')!,
      { target: { value: '样本量\n\n  结论  \n样本量' } })
    fireEvent.click(inDialog('[data-confirm-synth]')!)

    await vi.waitFor(() => expect(streamCall()).toBeTruthy())
    // 去空行 + 去重（权威的清洗规则在后端，这层只为让「共 N 个维度」显示得准）
    expect(JSON.parse(String(streamCall()!.init.body)).dimensions).toEqual(['样本量', '结论'])
  })

  it('老后端不返回 dimensionPresets → 不崩，只剩自由输入', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: undefined })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    openAdvanced()

    expect(inDialog('[data-synth-preset]')).toBeNull()
    expect(inDialog('[data-synth-dims]')).toBeTruthy()
  })
})

describe('DocLibrary — 读者意图与材料体裁（§7.8）', () => {
  /** 五项意图 + 四项体裁，形状与后端 `READER_INTENTS` / `CONTENT_TYPES` 一致 */
  const withIntents = (): Capabilities => ({
    llmModels: {},
    readerIntents: [
      { key: 'understand', label: '理解主题', purpose: '读完能准确复述核心议题。', defaultContentType: null },
      { key: 'controversy', label: '找争议', purpose: '读完能指出真正冲突的地方。', defaultContentType: 'media' },
      { key: 'custom', label: '自定义', purpose: '', defaultContentType: null },
    ],
    contentTypes: [
      { key: 'paper', label: '论文' },
      { key: 'media', label: '观点内容' },
    ],
  } as unknown as Capabilities)

  it('意图单选组消费 capabilities，默认选中第一项并显示它的说明', () => {
    useStore.setState({ docs: twoDocs(), capabilities: withIntents() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    const radios = [...allInDialog('[data-synth-intent]')] as HTMLInputElement[]
    // ⚠️ 对照：前端硬编码五档意图 → capabilities 换了标签它不变 → 红
    expect(radios.map(r => r.parentElement!.textContent)).toEqual(['理解主题', '找争议', '自定义'])
    expect(radios[0].checked).toBe(true)
    expect(inDialog('[data-synth-intent-purpose]')!.textContent)
      .toContain('准确复述')
  })

  it('切意图 → body 带出该 key；选「自定义」才出现文本框并带出文本', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withIntents() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    // 非「自定义」意图没有文本框（后端按意图取预设文案，用户没东西可填）
    expect(inDialog('[data-synth-intent-text]')).toBeNull()

    fireEvent.click(inDialog('[data-synth-intent="custom"]')!)
    fireEvent.change(inDialog('[data-synth-intent-text]')!,
      { target: { value: '给团队做选型决策，重点看成本' } })
    fireEvent.click(inDialog('[data-confirm-synth]')!)

    await vi.waitFor(() => expect(streamCall()).toBeTruthy())
    const body = JSON.parse(String(streamCall()!.init.body))
    expect(body.reader_intent).toBe('custom')
    expect(body.reader_intent_text).toBe('给团队做选型决策，重点看成本')
  })

  it('切走「自定义」后不再发那段文本（否则它会污染另一条意图的读者目的）', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withIntents() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    fireEvent.click(inDialog('[data-synth-intent="custom"]')!)
    fireEvent.change(inDialog('[data-synth-intent-text]')!,
      { target: { value: '写给谁看的说明' } })
    fireEvent.click(inDialog('[data-synth-intent="controversy"]')!)
    fireEvent.click(inDialog('[data-confirm-synth]')!)

    await vi.waitFor(() => expect(streamCall()).toBeTruthy())
    const body = JSON.parse(String(streamCall()!.init.body))
    expect(body.reader_intent).toBe('controversy')
    expect(body.reader_intent_text).toBe('')
  })

  it('体裁由意图预填（找争议 → 观点内容），留空的意图不预填任何体裁', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withIntents() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    openAdvanced()

    const checked = () => [...allInDialog('[data-synth-genre]')]
      .filter(n => (n as HTMLInputElement).checked)
      .map(n => (n as HTMLElement).getAttribute('data-synth-genre'))

    // 「理解主题」的 defaultContentType 是 null → **刻意留空**，不替用户猜一个
    // （⚠️ 对照：无脑预填 paper → 此处非空 → 红）
    expect(checked()).toEqual([])

    fireEvent.click(inDialog('[data-synth-intent="controversy"]')!)
    expect(checked()).toEqual(['media'])
  })

  it('体裁可多选修正；不选则 body 里是空数组（不加任何体裁专属条款）', async () => {
    useStore.setState({ docs: twoDocs(), capabilities: withIntents() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    openAdvanced()

    // 手动加一个 → 两个都在（多选是「修正」不是「替换」）
    fireEvent.click(inDialog('[data-synth-intent="controversy"]')!)
    fireEvent.click(inDialog('[data-synth-genre="paper"]')!)
    fireEvent.click(inDialog('[data-confirm-synth]')!)
    await vi.waitFor(() => expect(streamCall()).toBeTruthy())
    expect(JSON.parse(String(streamCall()!.init.body)).content_type).toEqual(['media', 'paper'])
  })

  it('切意图重置体裁（与「切换预设覆盖维度」同款语义，小字已注明）', () => {
    useStore.setState({ docs: twoDocs(), capabilities: withIntents() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    openAdvanced()

    fireEvent.click(inDialog('[data-synth-intent="controversy"]')!)
    fireEvent.click(inDialog('[data-synth-genre="paper"]')!)
    // 切回不预填体裁的意图 → 之前手选的也一并清掉（覆盖语义，不留混血状态）
    fireEvent.click(inDialog('[data-synth-intent="understand"]')!)
    const checked = [...allInDialog('[data-synth-genre]')]
      .filter(n => (n as HTMLInputElement).checked)
    expect(checked).toEqual([])
  })
})

describe('DocLibrary — 对比原料可选（§19）', () => {
  it('切「笔记」重拉预览（带 source_mode）→ 无笔记的篇标灰 + 说明；done 里引号外的篇不进来源清单', async () => {
    // 默认打开时是 raw；用户切到 notes 后预览变了：tr_b 无笔记
    previewData = {
      ...previewData,
      docs: [
        { id: 'tr_a', title: '论文 A', tokens: 200, plan: 'full', hasNotes: true, notesTokens: 200 },
        { id: 'tr_b', title: '论文 B', tokens: 0, plan: 'unavailable', hasNotes: false, notesTokens: 0 },
      ],
      excluded: ['tr_b'],
    }
    // ⚠️ `excluded` 是 **id**（后端 `Result.excluded` 的语义）：前端拿它从选中集里
    // 做差集。曾经这里是 `['论文 B']`（标题）——那样的实现下差集永远为空，于是
    // 「2 合 1」徽章指向一份产物根本没读过的文档，且不报任何错。
    streamExtra = { excluded: ['tr_b'] }
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)
    selectRows(container, 2)
    fireEvent.click(synthBtn(container))

    const radioNotes = inDialog('[data-synth-source="notes"]') as HTMLInputElement
    fireEvent.click(radioNotes)

    // ⚠️ 必须**重拉**而不是前端本地重算：notes 的 token 数与 rawText 不同，`plan` 必须重算；
    // 本地重算 = 把 `plan_synthesis` 复刻一遍（项目铁律「成对逻辑必须同源」明令禁止）
    await vi.waitFor(() => {
      expect(captured.some(c =>
        c.url.includes('/synthesis/preview') && String(c.init.body).includes('"source_mode":"notes"'))).toBe(true)
    })
    await vi.waitFor(() => expect(inDialog('[data-synth-excluded]')).toBeTruthy())
    expect(inDialog('[data-synth-excluded]')!.textContent).toContain('1 篇无笔记')

    fireEvent.click(inDialog('[data-confirm-synth]')!)
    await vi.waitFor(() => expect(streamCall()).toBeTruthy())
    expect(JSON.parse(String(streamCall()!.init.body)).source_mode).toBe('notes')

    // ⚠️ 对照：把 excluded 也写进 sourceDocIds → 「2 合 1」徽章指向一份产物没读过的文档 → 红
    await vi.waitFor(() => expect(useStore.getState().docs.some(d => d.id === 'tr_new')).toBe(true))
    expect(useStore.getState().docs.find(d => d.id === 'tr_new')!.sourceDocIds).toEqual(['tr_a'])
  })
})

describe('DocLibrary — done 之后不自动生成（D7）', () => {
  it('创建完成后新文档进库、被选中，但不发任何 generate 请求', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = render(<DocLibrary />)

    selectRows(container, 2)
    fireEvent.click(synthBtn(container))
    fireEvent.click(inDialog('[data-confirm-synth]')!)

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
