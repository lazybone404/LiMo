/**
 * 智能分类 —— BatchBar 的「智能分类」按钮 + ClassifyDialog 的审定与写入。
 *
 * 四条判据，每条都对应一个「看起来对、其实错」的实现：
 *
 * ① **切档必须重调 preview**：取材深度决定模型看到什么，结果必然不同。最省事的
 *    写法是「切档只改本地 state、沿用旧的 suggestions」——界面完全正常（徽标照样
 *    是那些行），只是「仅标题」档下显示的仍是 600 字档的建议。判据因此落在
 *    **发出去的第二个请求体**上，而不是「界面变了没有」。
 *    本文件刻意**不 mock `api/classify`**，让真实实现跑——这样 body 字段名
 *    （`docIds` vs `doc_ids`）与 `body.code` 检查也在覆盖范围内。
 *
 * ② **先建夹、后 commit**：commit 只接受**已存在**的 folderId（它刻意不建夹，
 *    因为文件夹名是用户可改的自由文本，只有前端知道最终值）。若顺序反了，
 *    「有新建」的场景必然整批失败，而界面上看不出差别——对话框照样关闭。
 *
 * ③ **部分失败只回滚失败项**：整体回滚会把服务端明明已经改好的那些也退回原处，
 *    用户下次刷新会看到它们「跳回去又跳回来」。判据因此是「tr_a 保住了新夹、
 *    tr_b 退回原值」，而不是「toast 里有失败字样」。
 *
 * ④ **卸载即取消**：`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`，折叠
 *    左栏 = 卸载 `DocLibrary`。分类是**计费调用**，不 abort 就等于留下一个无人
 *    接收的 LLM 请求：token 照花、结果丢进虚空。判据是**请求对象上的 `AbortSignal`
 *    真的被 abort 了**，而不是「组件没了」。
 *
 * 对照实验（**均已实测**，证明本文件有区分力；每条的命中数如实记下）：
 *   A. `changeClassifyDepth` 里去掉 `loadClassifyPreview` → 红 1 条（「切「仅标题」…」）
 *   B. 把建夹挪到 `commitClassifications` 之后 → 红 3 条（顺序 / 同名复用 / 建夹失败中止
 *      —— 这三条是同一层判据的三个面，一次坏掉同时红是应该的，不是误伤）
 *   C. 部分失败改成整体回滚 → 红 1 条（「部分失败 → 只回滚失败项」）
 *   D. 删掉 cleanup 里的 `classifyAbortRef.current?.abort()` → 红 1 条（「卸载即取消」）
 */
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'
import type { ClassifyPreview, ClassifyCommitResult } from '../api/classify'

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
const { ClassifyDialog } = await import('../components/doc-library/ClassifyDialog')
const client = await import('../api/client')

const createFolderMock = client.createFolder as unknown as Mock

interface Captured { url: string; init: RequestInit }
let captured: Captured[] = []
/** 写入顺序的两个时间戳（createFolder 与 commit 各打一个） */
let order: string[] = []

/** preview 端点返回的 payload —— 各用例按需覆写（业务失败 / 三态 / 新建夹） */
let previewData: Record<string, unknown> = {}
/** commit 端点返回的 payload —— 各用例按需覆写（部分失败） */
let commitData: ClassifyCommitResult = { status: 'ok', affected: 0, failed: [] }

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response
}

const previewCalls = () => captured.filter(c => c.url.includes('/classify/preview'))
const commitCalls = () => captured.filter(c => c.url.includes('/classify/commit'))

function defaultPreview(): Record<string, unknown> {
  return {
    status: 'ok',
    model: '测试模型',
    limits: { maxNewFolders: 5, notesChars: 600, notesCharsMax: 2000, maxTagsPerDoc: 8 },
    folders: [{ id: 'fd_1', name: '研究', count: 3 }],
    tagVocab: ['深度学习', '综述'],
    suggestions: [
      {
        id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
        folder: { action: 'reuse', id: 'fd_1', name: '研究' },
        tags: ['深度学习'], warnings: [],
      },
      {
        id: 'tr_b', title: '论文 B', source: 'notes', chars: 600,
        folder: { action: 'none' }, tags: ['综述'], warnings: [],
      },
    ],
    warnings: [],
  }
}

beforeEach(() => {
  captured = []
  order = []
  previewData = defaultPreview()
  commitData = { status: 'ok', affected: 2, failed: [] }
  createFolderMock.mockReset()
  createFolderMock.mockImplementation(async (name: string) => {
    order.push(`create:${name}`)
    return { id: `fd_new_${name}`, name, count: 0 }
  })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const u = String(url)
    captured.push({ url: u, init })
    if (u.includes('/classify/preview')) return jsonResponse(previewData)
    if (u.includes('/classify/commit')) {
      order.push('commit')
      return jsonResponse(commitData)
    }
    return jsonResponse({})
  }))
  useStore.setState({
    toasts: [], selectedIds: new Set(), listLimit: 60, docsLoading: false,
    folders: [], currentDocId: null, searchQuery: '', activeType: '', activeTags: [],
    activeFolderId: null, capabilities: undefined,
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
 * 打开分类对话框，并等到**这一次 preview 走完**。
 *
 * ⚠️ 只等 `previewCalls().length > 0` 是不够的——那只证明 fetch 发出去了，而
 * `running` 要等 promise 落地才落下。此时顶部三个取材档按钮仍是 `disabled`
 * （`disabled={running || committing}`），点下去**静默无效**：用例会以「切档没
 * 重调 preview」的形态红，而真正的原因与切档无关。
 */
async function openClassify(n = 2) {
  const r = render(<DocLibrary />)
  selectRows(r.container, n)
  fireEvent.click(r.container.querySelector('[data-batch-classify]') as HTMLElement)
  await vi.waitFor(() => expect(previewCalls().length).toBeGreaterThan(0))
  await vi.waitFor(() => expect(r.container.querySelector('[data-classify-running]')).toBeNull())
  return r
}

describe('智能分类 — 取材深度与请求形状', () => {
  it('点按钮弹对话框，选中集与默认取材深度（600）原样发出', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)

    expect(container.querySelector('[data-classify-dialog]')).toBeTruthy()
    const body = JSON.parse(String(previewCalls()[0].init.body))
    // ⚠️ 断言的是**逐项相等**（含顺序）：后端不变式要求 suggestions 顺序恒等于请求顺序
    expect(body.docIds).toEqual(['tr_a', 'tr_b'])
    expect(body.notesChars).toBe(600)
    expect(previewCalls()[0].url).toContain('/classify/preview')
    expect(previewCalls()[0].init.method).toBe('POST')
  })

  it('切「仅标题」→ **重调** preview（前端不本地重算）', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)

    fireEvent.click(container.querySelector('[data-classify-depth="0"]') as HTMLElement)

    // ⚠️ 对照 A：`changeClassifyDepth` 里去掉 loadClassifyPreview → 只有 1 次请求 → 红
    await vi.waitFor(() => expect(previewCalls().length).toBe(2))
    const second = JSON.parse(String(previewCalls()[1].init.body))
    expect(second.notesChars).toBe(0)
    // 档位可以变，**选中集不能变**——预览与写入必须基于同一批 id
    expect(second.docIds).toEqual(['tr_a', 'tr_b'])
  })

  it('preview 业务失败（HTTP 200 + code）→ 显示错误且「应用」禁用', async () => {
    // 项目模式 #36：AppError 转成 200 + {code}，只看 response.ok 会把失败当成功
    previewData = { code: 'CLASSIFY_NO_API_KEY', message: '未配置 API Key', recoverable: false }
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)

    await vi.waitFor(() => expect(container.querySelector('[data-classify-error]')).toBeTruthy())
    expect((container.querySelector('[data-confirm-classify]') as HTMLButtonElement).disabled).toBe(true)
    // 失败时不该继续渲染一份不存在的建议
    expect(container.querySelector('[data-classify-row]')).toBeNull()
  })
})

describe('智能分类 — 逐篇呈现（取材徽标 / 三类文件夹建议）', () => {
  it('行数恒等于选中数；「仅标题」与「已缩减」**显式**标出', async () => {
    previewData = {
      ...defaultPreview(),
      suggestions: [
        {
          id: 'tr_a', title: '论文 A', source: 'shortened', chars: 200,
          folder: { action: 'reuse', id: 'fd_1', name: '研究' },
          tags: ['深度学习'], warnings: ['已从 600 字缩减至 200 字'],
        },
        {
          id: 'tr_b', title: '论文 B', source: 'title_only', chars: 0,
          folder: { action: 'none' }, tags: [], warnings: ['本篇无笔记，仅依据标题判断'],
        },
      ],
    }
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)

    await vi.waitFor(() => expect(container.querySelector('[data-classify-row="tr_b"]')).toBeTruthy())
    expect(container.querySelectorAll('[data-classify-row]').length).toBe(2)
    // ⚠️ 对照（后端同款）：把无笔记篇也标成 notes（静默假装看过）→ 此选择器取不到 → 红
    expect(container.querySelector('[data-classify-source="title_only"]')).toBeTruthy()
    expect(container.querySelector('[data-classify-source="shortened"]')).toBeTruthy()
    // 降级说明是「模型看到了什么」的唯一线索，必须真的渲染出来
    expect(container.querySelector('[data-classify-warning="tr_b"]')!.textContent).toContain('无笔记')
    expect(container.querySelector('[data-classify-warning="tr_a"]')!.textContent).toContain('缩减')
  })

  it('复用 / 新建 / 未分类三类建议都能呈现，且全局 warning 有落点', async () => {
    previewData = {
      ...defaultPreview(),
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'reuse', id: 'fd_1', name: '研究' }, tags: [], warnings: [] },
        { id: 'tr_b', title: '论文 B', source: 'notes', chars: 600,
          folder: { action: 'new', name: '新主题' }, tags: [], warnings: [] },
      ],
      warnings: ['模型给出了 1 个不在选中集合中的 id，已丢弃'],
    }
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)

    await vi.waitFor(() => expect(container.querySelector('[data-classify-row="tr_b"]')).toBeTruthy())
    expect(container.querySelector('[data-classify-folder-mode="reuse"]')).toBeTruthy()
    expect(container.querySelector('[data-classify-folder-mode="new"]')).toBeTruthy()
    // 新建名必须有输入框可改（模型起的名字是**建议**，不是定论）
    expect((container.querySelector('[data-classify-new-name="tr_b"]') as HTMLInputElement).value).toBe('新主题')
    expect(container.querySelector('[data-classify-warnings]')!.textContent).toContain('已丢弃')
  })
})

describe('ClassifyDialog — 审定编辑（纯组件，直测）', () => {
  const base = (over: Partial<ClassifyPreview> = {}): ClassifyPreview => ({
    status: 'ok', model: 'm',
    limits: { maxNewFolders: 5, notesChars: 600, notesCharsMax: 2000, maxTagsPerDoc: 8 },
    folders: [{ id: 'fd_1', name: '研究', count: 3 }],
    tagVocab: [],
    suggestions: [
      { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
        folder: { action: 'reuse', id: 'fd_1', name: '研究' }, tags: ['深度学习'], warnings: [] },
      { id: 'tr_b', title: '论文 B', source: 'notes', chars: 600,
        folder: { action: 'new', name: '新主题' }, tags: [], warnings: [] },
    ],
    warnings: [],
    ...over,
  })

  const mount = (preview: ClassifyPreview | null, onConfirm = vi.fn()) => {
    const r = render(
      <ClassifyDialog ids={['tr_a', 'tr_b']} notesChars={600} running={false} committing={false}
        error={null} preview={preview} onDepthChange={vi.fn()} onClose={vi.fn()} onConfirm={onConfirm} />,
    )
    return { ...r, onConfirm }
  }

  it('把「新建」改成归入现有 → plan 里带的是现有夹的 id，不再要求建夹', () => {
    const { container, onConfirm } = mount(base())

    fireEvent.change(container.querySelector('[data-classify-folder-select="tr_b"]') as HTMLSelectElement,
      { target: { value: 'reuse:fd_1' } })
    expect(container.querySelector('[data-classify-row="tr_b"]')!
      .getAttribute('data-classify-folder-mode')).toBe('reuse')

    fireEvent.click(container.querySelector('[data-confirm-classify]') as HTMLElement)
    const plan = onConfirm.mock.calls[0][0]
    const row = plan.find((r: { id: string }) => r.id === 'tr_b')
    expect(row.folderId).toBe('fd_1')
    // ⚠️ 名字留空 = 不建夹（否则会凭空多出一个没人要的空文件夹，且界面上看不出来）
    expect(row.newFolderName).toBe('')
  })

  it('标签按逗号清洗（中英文逗号都认）、去空白；未分类 = 空 folderId', () => {
    const { container, onConfirm } = mount(base())

    fireEvent.change(container.querySelector('[data-classify-tags="tr_b"]') as HTMLInputElement,
      { target: { value: ' 综述， 对比 ,, 综述 ' } })
    fireEvent.change(container.querySelector('[data-classify-folder-select="tr_a"]') as HTMLSelectElement,
      { target: { value: 'none' } })
    fireEvent.click(container.querySelector('[data-confirm-classify]') as HTMLElement)

    const plan = onConfirm.mock.calls[0][0]
    // 前端只做「分割 + 去空白」，**不去重**——与 `BatchBar.commitTag` 逐字同款，
    // 去重归写入侧（`_merge_tags` 的 add 是并入去重）。断言按现状写，不按期望写：
    // 若在此处改成断言去重后的结果，测试就会「绿着」而代码里根本没有那段逻辑。
    expect(plan.find((r: { id: string }) => r.id === 'tr_b').tags).toEqual(['综述', '对比', '综述'])
    // 未分类 = `''`（后端把空串当未分类，不会误建空名夹）
    expect(plan.find((r: { id: string }) => r.id === 'tr_a').folderId).toBe('')
  })

  it('改动后重跑 preview → 草稿整体重建（不把新旧两批建议混在一起）', () => {
    const { container, rerender } = mount(base())
    fireEvent.change(container.querySelector('[data-classify-tags="tr_b"]') as HTMLInputElement,
      { target: { value: '手改的标签' } })

    rerender(
      <ClassifyDialog ids={['tr_a', 'tr_b']} notesChars={0} running={false} committing={false}
        error={null} preview={base()} onDepthChange={vi.fn()} onClose={vi.fn()} onConfirm={vi.fn()} />,
    )
    expect((container.querySelector('[data-classify-tags="tr_b"]') as HTMLInputElement).value).toBe('')
  })

  it('行数为 0 或无预览时「应用」禁用（空方案不该发出一次写库请求）', () => {
    const { container } = mount(base({ suggestions: [] }))
    expect((container.querySelector('[data-confirm-classify]') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('智能分类 — 写入（建夹 → commit）', () => {
  it('先建夹、后 commit，且 assignments 用的是新建返回的 id', async () => {
    previewData = {
      ...defaultPreview(),
      folders: [],           // 库里一个夹都没有 → 只能新建
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'new', name: '新主题' }, tags: ['x'], warnings: [] },
        { id: 'tr_b', title: '论文 B', source: 'notes', chars: 600,
          folder: { action: 'new', name: '新主题' }, tags: ['y'], warnings: [] },
      ],
    }
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)
    await vi.waitFor(() => expect(container.querySelector('[data-classify-row="tr_b"]')).toBeTruthy())

    fireEvent.click(container.querySelector('[data-confirm-classify]') as HTMLElement)
    await vi.waitFor(() => expect(commitCalls().length).toBe(1))

    // ⚠️ 对照 B：把建夹挪到 commit 之后 → order 变成 ['commit', ...] → 红。
    // 顺序反了的失效是「第一次新建夹必然整批失败」，而界面照样关闭对话框。
    expect(order).toEqual(['create:新主题', 'commit'])
    // 同名只建一次（两篇都要「新主题」→ 一个夹）
    expect(createFolderMock).toHaveBeenCalledTimes(1)

    const body = JSON.parse(String(commitCalls()[0].init.body))
    const a = body.assignments.find((x: { id: string }) => x.id === 'tr_a')
    expect(a.folderId).toBe('fd_new_新主题')
    expect(body.assignments.find((x: { id: string }) => x.id === 'tr_b').folderId).toBe('fd_new_新主题')
    expect(a.tags).toEqual(['x'])
  })

  it('库里已有同名夹 → 直接复用，不重复建', async () => {
    previewData = {
      ...defaultPreview(),
      folders: [{ id: 'fd_same', name: ' 新主题 ', count: 1 }],
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'new', name: '新主题' }, tags: [], warnings: [] },
      ],
    }
    useStore.setState({
      docs: twoDocs(),
      folders: [{ id: 'fd_same', name: ' 新主题 ', count: 1, createdAt: '2026-01-01T00:00:00' }],
    })
    const { container } = await openClassify(1)
    await vi.waitFor(() => expect(container.querySelector('[data-classify-row="tr_a"]')).toBeTruthy())

    fireEvent.click(container.querySelector('[data-confirm-classify]') as HTMLElement)
    await vi.waitFor(() => expect(commitCalls().length).toBe(1))

    // 判据是 trim + 大小写不敏感（用户建过的夹不该因为多个空格就再建一个）
    expect(createFolderMock).not.toHaveBeenCalled()
    const body = JSON.parse(String(commitCalls()[0].init.body))
    expect(body.assignments[0].folderId).toBe('fd_same')
  })

  it('部分失败 → **只回滚失败项**，成功项留在新夹里', async () => {
    previewData = {
      ...defaultPreview(),
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'reuse', id: 'fd_1', name: '研究' }, tags: ['深度学习'], warnings: [] },
        { id: 'tr_b', title: '论文 B', source: 'notes', chars: 600,
          folder: { action: 'reuse', id: 'fd_1', name: '研究' }, tags: ['综述'], warnings: [] },
      ],
    }
    commitData = { status: 'ok', affected: 1, failed: [{ id: 'tr_b', error: '文档不存在' }] }
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)
    await vi.waitFor(() => expect(container.querySelector('[data-classify-row="tr_b"]')).toBeTruthy())

    fireEvent.click(container.querySelector('[data-confirm-classify]') as HTMLElement)
    await vi.waitFor(() => expect(useStore.getState().toasts.length).toBeGreaterThan(0))

    const docs = useStore.getState().docs
    // ⚠️ 对照 C：改成整体回滚 → tr_a 的 folderId 退回 '' → 红
    expect(docs.find(d => d.id === 'tr_a')!.folderId).toBe('fd_1')
    expect(docs.find(d => d.id === 'tr_a')!.tags).toContain('深度学习')
    expect(docs.find(d => d.id === 'tr_b')!.folderId).toBe('')
    expect(docs.find(d => d.id === 'tr_b')!.tags).toEqual([])

    const msg = useStore.getState().toasts.map(t => t.message).join(' / ')
    expect(msg).toContain('1 篇失败')
  })

  it('建夹失败 → 中止整次（不写一半），对话框保持打开、草稿不丢', async () => {
    previewData = {
      ...defaultPreview(),
      folders: [],
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'new', name: '新主题' }, tags: [], warnings: [] },
      ],
    }
    createFolderMock.mockRejectedValue(new Error('磁盘满了'))
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(1)
    await vi.waitFor(() => expect(container.querySelector('[data-classify-row="tr_a"]')).toBeTruthy())

    fireEvent.click(container.querySelector('[data-confirm-classify]') as HTMLElement)
    await vi.waitFor(() => expect(useStore.getState().toasts.length).toBeGreaterThan(0))

    // 一篇都不许写：带着一半文件夹去 commit 会让分类结果与用户的审定不符
    expect(commitCalls()).toEqual([])
    expect(container.querySelector('[data-classify-dialog]')).toBeTruthy()
  })
})

describe('智能分类 — 卸载即取消', () => {
  it('折叠面板（卸载组件）会 abort 那次计费请求', async () => {
    useStore.setState({ docs: twoDocs() })
    const { unmount } = await openClassify(2)

    const sig = previewCalls()[0].init.signal as AbortSignal
    expect(sig).toBeTruthy()
    expect(sig.aborted).toBe(false)

    unmount()

    // ⚠️ 对照 D：删掉 cleanup 里的 abort → 此处停在 false → 红
    expect(sig.aborted).toBe(true)
  })
})
