/**
 * 智能分类 —— 配置阶段（先选策略）→ BatchBar 按钮 → 审定与写入。
 *
 * 六条判据，每条都对应一个「看起来对、其实错」的实现：
 *
 * ① **打开对话框不发请求**（2026-10-11 实机反馈的原始形态）。上一版打开即按默认档
 *    （600 字 + 优先复用）发起调用——调用本身零写入，但**结果先于选择出现**，用户
 *    没机会决定模型看多少、要不要建文件夹。判据落在**请求条数**上：打开后必须为 0。
 *    最省事的回归写法（把 `loadClassifyPreview` 挪回 `handleOpenClassify`）会让这条红。
 *
 * ② **配置阶段选的三项策略真的随请求发出**。少了它，界面上选「只打标签」而后端
 *    收到默认档：文件夹照动、标签照拿全库词表，而两端都不报错。
 *
 * ③ **切档必须重调 preview**：取材深度决定模型看到什么，结果必然不同。最省事的
 *    写法是「切档只改本地 state、沿用旧的 suggestions」——界面完全正常（徽标照样
 *    是那些行），只是「仅标题」档下显示的仍是 600 字档的建议。判据因此落在
 *    **发出去的第二个请求体**上，而不是「界面变了没有」。
 *    本文件刻意**不 mock `api/classify`**，让真实实现跑——这样 body 字段名
 *    （`docIds` vs `doc_ids`）与 `body.code` 检查也在覆盖范围内。
 *
 * ④ **先建夹、后 commit**：commit 只接受**已存在**的 folderId（它刻意不建夹，
 *    因为文件夹名是用户可改的自由文本，只有前端知道最终值）。若顺序反了，
 *    「有新建」的场景必然整批失败，而界面上看不出差别——对话框照样关闭。
 *
 * ⑤ **部分失败只回滚失败项**：整体回滚会把服务端明明已经改好的那些也退回原处，
 *    用户下次刷新会看到它们「跳回去又跳回来」。判据因此是「tr_a 保住了新夹、
 *    tr_b 退回原值」，而不是「toast 里有失败字样」。
 *
 * ⑥ **卸载即取消**：`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`，折叠
 *    左栏 = 卸载 `DocLibrary`。分类是**计费调用**，不 abort 就等于留下一个无人
 *    接收的 LLM 请求：token 照花、结果丢进虚空。判据是**请求对象上的 `AbortSignal`
 *    真的被 abort 了**，而不是「组件没了」。
 *
 * 对照实验（**均已实测**，证明本文件有区分力）：
 *   A. `changeClassifyDepth` 里去掉 `loadClassifyPreview` → 红 1 条（「切「仅标题」…」）
 *   B. 把建夹挪到 `commitClassifications` 之后 → 红 3 条（顺序 / 同名复用 / 建夹失败中止
 *      —— 这三条是同一层判据的三个面，一次坏掉同时红是应该的，不是误伤）
 *   C. 部分失败改成整体回滚 → 红 1 条（「部分失败 → 只回滚失败项」）
 *   D. 删掉 cleanup 里的 `classifyAbortRef.current?.abort()` → 红 1 条（「卸载即取消」）
 *   E. `handleOpenClassify` 里加回 `loadClassifyPreview(ids, opts)` → 红 1 条
 *      （「打开对话框不发请求」）
 *   F. `startClassify` 里丢掉 `opts`（改回只传 `docIds`）→ 红 1 条（「策略随请求发出」）
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
const { ClassifyDialog, DEPTH_OPTIONS } = await import('../components/doc-library/ClassifyDialog')
const client = await import('../api/client')

const createFolderMock = client.createFolder as unknown as Mock

/**
 * 对话框是 **Portal 到 `document.body`** 的（父级 `DocLibrary` 的根 `<aside>` 带
 * `backdrop-blur-[20px]`，而 `backdrop-filter` 会创建包含块，把 `position: fixed`
 * 的后代锁在面板内）。**因此一律用 `document.body` 查，不用 render 的 container**。
 */
const dlg = <T extends Element = HTMLElement>(sel: string): T | null =>
  document.body.querySelector<T>(sel)
const dlgAll = (sel: string) => document.body.querySelectorAll(sel)

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
    limits: {
      maxNewFolders: 5, maxNewFoldersLimit: 10, notesChars: 600,
      notesCharsMax: 2000, maxTagsPerDoc: 8, maxSeedTags: 50,
    },
    folderMode: 'reuse_or_new',
    tagMode: 'vocab',
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

/**
 * 两篇待分类的文档。
 *
 * ⚠️ `tags` 默认**为空**是有意的（对应「文档可能没有标签」这个真实起点），而它同时
 * 决定对话框里「标签」一栏的默认档：空 → `seed`（让用户先给几个），非空 → `vocab`。
 * 因此凡是要断言 `tagMode === 'vocab'` 的用例，必须显式传入标签。
 */
function twoDocs(tags: string[] = []): DocEntry[] {
  return [
    { id: 'tr_a', title: '论文 A', sourceType: 'pdf', engine: 'e', contentHash: 'ha', tags: [...tags], createdAt: '2026-01-02T00:00:00' },
    { id: 'tr_b', title: '论文 B', sourceType: 'pdf', engine: 'e', contentHash: 'hb', tags: [...tags], createdAt: '2026-01-01T00:00:00' },
  ]
}

/** 勾中前 n 行的复选框（工具条随之出现） */
function selectRows(container: HTMLElement, n: number) {
  const toggles = container.querySelectorAll('[data-select-toggle]')
  for (let i = 0; i < n; i++) fireEvent.click(toggles[i] as HTMLElement)
}

/**
 * 打开对话框，停在**配置阶段**（此时一个请求都还没发）。
 *
 * ⚠️ 这里刻意不去等 preview——打开本就不该有 preview（见判据 ①）。
 */
async function openClassify(n = 2) {
  const r = render(<DocLibrary />)
  selectRows(r.container, n)
  fireEvent.click(r.container.querySelector('[data-batch-classify]') as HTMLElement)
  await vi.waitFor(() => expect(dlg('[data-classify-dialog]')).toBeTruthy())
  return r
}

/**
 * 等这一次 preview 的**结局**落地。
 *
 * ⚠️ 不能等「`data-classify-running` 消失」——那个元素只有审定阶段才渲染，配置阶段
 * 永远是 null，断言会立刻通过、用例在请求落地前就往下跑。这里等的是**两个结局之一的
 * 标志**：成功 → 头部的「尚未写入」声明；失败 → 配置阶段的错误行。
 */
async function waitPreviewSettled() {
  await vi.waitFor(() => {
    expect(dlg('[data-classify-notice]') || dlg('[data-classify-error]')).toBeTruthy()
  })
}

/** 打开对话框 + 按下「开始分析」+ 等到结果落地（`tune` 用于在按下之前改配置） */
async function runClassify(n = 2, tune?: () => void) {
  const r = await openClassify(n)
  tune?.()
  fireEvent.click(dlg('[data-start-classify]') as HTMLElement)
  await waitPreviewSettled()
  return r
}

describe('智能分类 — 配置阶段（先选，后开始）', () => {
  /**
   * 用户原话：「我点智能分类只是点进那个界面还没开始选确定分类他就直接给我分类了」。
   *
   * 那条反馈的**事实层**是「一次零写入的 preview 被读成了分类结果」（见下方「建议态
   * 被显式声明」），**体验层**则是这句话本身：调用发生在用户做任何选择之前。
   */
  it('打开对话框**一个请求都不发**；计费调用只在按下「开始分析」时发生', async () => {
    useStore.setState({ docs: twoDocs() })
    const { container } = await openClassify(2)

    // 对话框必须**Portal 到 body**：`DocLibrary` 的根 `<aside>` 带 `backdrop-blur-[20px]`，
    // 它会创建包含块，把 `fixed` 后代裁成面板大小的一小块。这条断言是那件事的证据——
    // 去掉 portal 后其余用例也会红，但报错是「provide a DOM element」，看不出根因。
    expect(container.contains(dlg('[data-classify-dialog]'))).toBe(false)

    // ⚠️ 对照 E：`handleOpenClassify` 里加回 loadClassifyPreview → 此处为 1 → 红
    expect(previewCalls()).toEqual([])
    expect(dlg('[data-classify-config]')).toBeTruthy()
    expect(dlg('[data-classify-row]')).toBeNull()   // 还没有任何结果可审定

    fireEvent.click(dlg('[data-start-classify]') as HTMLElement)
    await waitPreviewSettled()
    expect(previewCalls().length).toBe(1)
  })

  it('配置阶段选的三项策略原样随请求发出（含手填基础标签的拆分）', async () => {
    useStore.setState({ docs: twoDocs() })
    await runClassify(2, () => {
      fireEvent.click(dlg('[data-classify-folder-opt="tags_only"]') as HTMLElement)
      fireEvent.click(dlg('[data-classify-tag-opt="seed"]') as HTMLElement)
      fireEvent.change(dlg('[data-classify-seed-tags]') as HTMLTextAreaElement,
        { target: { value: '论文\n综述, 工具链，论文' } })
    })

    const body = JSON.parse(String(previewCalls()[0].init.body))
    // ⚠️ 对照 F：`startClassify` 里丢掉 opts → 这几项回落到后端默认 → 红
    expect(body.folderMode).toBe('tags_only')
    expect(body.tagMode).toBe('seed')
    // 换行 / 中英文逗号都算分隔符，trim + 去重（`splitSeedTags`）
    expect(body.seedTags).toEqual(['论文', '综述', '工具链'])
    // 不动文件夹时不该传一个新建额度——后端会忽略，但传了响应里就回显一个无意义的数
    expect(body.maxNewFolders).toBe(0)
    expect(body.docIds).toEqual(['tr_a', 'tr_b'])
  })

  it('库里没有文件夹 → 「只用现有文件夹」禁用；没有标签 → 标签默认落到「我给几个」', async () => {
    useStore.setState({ docs: twoDocs(), folders: [] })
    await openClassify(2)

    // 一个夹都没有时选「只用现有的」= 全部落未分类，是个点不动的死选项
    expect((dlg('[data-classify-folder-opt="reuse_only"]') as HTMLButtonElement).disabled).toBe(true)
    expect(dlg('[data-classify-folder-opt="reuse_or_new"]')!.getAttribute('aria-checked')).toBe('true')
    // 词表为空时默认「复用现有标签」等于什么都没提供——落到让用户手填那一档
    expect(dlg('[data-classify-tag-opt="seed"]')!.getAttribute('aria-checked')).toBe('true')
  })

  it('「最多新建」滑条只在「优先复用，必要时新建」下出现', async () => {
    useStore.setState({ docs: twoDocs() })
    await openClassify(2)

    expect(dlg('[data-classify-max-new-wrap]')).toBeTruthy()
    fireEvent.change(dlg('[data-classify-max-new]') as HTMLInputElement, { target: { value: '2' } })
    expect(dlg('[data-classify-max-new-value]')!.textContent).toContain('2')

    fireEvent.click(dlg('[data-classify-folder-opt="tags_only"]') as HTMLElement)
    expect(dlg('[data-classify-max-new-wrap]')).toBeNull()
  })

  it('裁定阶段可「返回设置」改配置，再按下按钮时是**重新分析**', async () => {
    useStore.setState({ docs: twoDocs() })
    await runClassify(2)
    expect(dlg('[data-classify-notice]')).toBeTruthy()

    fireEvent.click(dlg('[data-back-classify]') as HTMLElement)
    expect(dlg('[data-classify-config]')).toBeTruthy()
    // 已有结果的前提下按钮文案变化——用户能看出这一次会再花一次额度
    expect(dlg('[data-start-classify]')!.textContent).toContain('重新分析')
  })
})

describe('智能分类 — 请求形状与失败', () => {
  it('默认档（600 字 / 优先复用 / 复用现有标签）原样发出', async () => {
    useStore.setState({
      docs: twoDocs(['深度学习']),   // 库里有标签 → 标签一栏默认「复用现有」
      folders: [{ id: 'fd_1', name: '研究', count: 1, createdAt: '2026-01-01T00:00:00' }],
    })
    await runClassify(2)

    const body = JSON.parse(String(previewCalls()[0].init.body))
    // ⚠️ 断言的是**逐项相等**（含顺序）：后端不变式要求 suggestions 顺序恒等于请求顺序
    expect(body.docIds).toEqual(['tr_a', 'tr_b'])
    expect(body.notesChars).toBe(600)
    expect(body.folderMode).toBe('reuse_or_new')
    expect(body.tagMode).toBe('vocab')
    expect(body.maxNewFolders).toBe(5)
    expect(previewCalls()[0].url).toContain('/classify/preview')
    expect(previewCalls()[0].init.method).toBe('POST')
  })

  it('审定阶段切「仅标题」→ **重调** preview，且沿用**上一次请求的**策略', async () => {
    // 库里得有夹，「只用现有文件夹」才可点（没有夹时它是禁用的死选项）
    useStore.setState({
      docs: twoDocs(['深度学习']),
      folders: [{ id: 'fd_1', name: '研究', count: 1, createdAt: '2026-01-01T00:00:00' }],
    })
    await runClassify(2, () => {
      // 刻意选一组**非默认**的策略：若「沿用」写成了「回落到默认」，断言才区分得出来
      fireEvent.click(dlg('[data-classify-folder-opt="reuse_only"]') as HTMLElement)
      fireEvent.click(dlg('[data-classify-tag-opt="free"]') as HTMLElement)
    })
    expect(JSON.parse(String(previewCalls()[0].init.body)).folderMode).toBe('reuse_only')

    fireEvent.click(dlg('[data-classify-depth="0"]') as HTMLElement)

    // ⚠️ 对照 A：`changeClassifyDepth` 里去掉 loadClassifyPreview → 只有 1 次请求 → 红
    await vi.waitFor(() => expect(previewCalls().length).toBe(2))
    const second = JSON.parse(String(previewCalls()[1].init.body))
    expect(second.notesChars).toBe(0)
    // 档位可以变，**选中集不能变**——预览与写入必须基于同一批 id
    expect(second.docIds).toEqual(['tr_a', 'tr_b'])
    // ⚠️ 其余三项必须沿用**上一次实际请求的**那套。策略选择器此刻不在场（在配置阶段），
    //    少了这个沿用，切档就把用户的策略悄悄打回默认而界面上看不见
    expect(second.folderMode).toBe('reuse_only')
    expect(second.tagMode).toBe('free')
  })

  it('preview 业务失败（HTTP 200 + code）→ 停在配置阶段显示错误，且不发 commit', async () => {
    // 项目模式 #36：AppError 转成 200 + {code}，只看 response.ok 会把失败当成功
    previewData = { code: 'CLASSIFY_NO_API_KEY', message: '未配置 API Key', recoverable: false }
    useStore.setState({ docs: twoDocs() })
    await openClassify(2)
    fireEvent.click(dlg('[data-start-classify]') as HTMLElement)
    await waitPreviewSettled()

    expect(dlg('[data-classify-error]')!.textContent).toContain('API Key')
    // 失败留在配置阶段：用户该做的是改配置/配 key 再试，不是对着一份不存在的建议点「应用」
    expect(dlg('[data-classify-config]')).toBeTruthy()
    expect(dlg('[data-confirm-classify]')).toBeNull()
    expect(commitCalls()).toEqual([])
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
    await runClassify(2)

    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_b"]')).toBeTruthy())
    expect(dlgAll('[data-classify-row]').length).toBe(2)
    // ⚠️ 对照（后端同款）：把无笔记篇也标成 notes（静默假装看过）→ 此选择器取不到 → 红
    expect(dlg('[data-classify-source="title_only"]')).toBeTruthy()
    expect(dlg('[data-classify-source="shortened"]')).toBeTruthy()
    // 降级说明是「模型看到了什么」的唯一线索，必须真的渲染出来
    expect(dlg('[data-classify-warning="tr_b"]')!.textContent).toContain('无笔记')
    expect(dlg('[data-classify-warning="tr_a"]')!.textContent).toContain('缩减')
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
    await runClassify(2)

    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_b"]')).toBeTruthy())
    expect(dlg('[data-classify-folder-mode="reuse"]')).toBeTruthy()
    expect(dlg('[data-classify-folder-mode="new"]')).toBeTruthy()
    // 新建名必须有输入框可改（模型起的名字是**建议**，不是定论）
    expect(dlg<HTMLInputElement>('[data-classify-new-name="tr_b"]')!.value).toBe('新主题')
    expect(dlg('[data-classify-warnings]')!.textContent).toContain('已丢弃')
  })

  /**
   * 2026-10-11 实机反馈：「我点智能分类只是点进那个界面还没开始选确定分类他就直接给我
   * 分类了」——**这不是写入 bug**（日志里只有 `preview`、没有 `commit`；`index.json`
   * 的 mtime 未变；全库 folderId / tags 仍为空），而是**建议被读成了结果**：preview
   * 返回后每一行都带着**已选好值**的下拉框与**已填满**的标签输入框，徽标又写完成时
   * （「归入现有」），整页看起来就是一份保存好的分类。
   *
   * 这条用例钉的是「建议态必须被显式声明」——三处缺任一，同样的误解就会复现。
   */
  it('建议态被显式声明：头部提示 + 徽标带「建议」+ 按钮写明篇数', async () => {
    useStore.setState({ docs: twoDocs() })
    await runClassify(2)

    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_a"]')).toBeTruthy())
    // ⚠️ 对照：删掉 `data-classify-notice` 那段 → 红。它是「还没写库」的唯一常驻声明
    expect(dlg('[data-classify-notice]')!.textContent).toContain('尚未写入')
    // ⚠️ 对照：徽标改回完成时（`reuse` → '归入现有'）→ 红
    expect(dlg('[data-classify-folder-mode="reuse"]')!.textContent).toContain('建议')
    // ⚠️ 对照：按钮改回光秃秃的「应用」→ 红。写明确认范围，顺带让「还没确认」更显眼
    expect(dlg('[data-confirm-classify]')!.textContent).toContain('2 篇')
  })
})

describe('ClassifyDialog — 审定编辑（纯组件，直测）', () => {
  const base = (over: Partial<ClassifyPreview> = {}): ClassifyPreview => ({
    status: 'ok', model: 'm',
    limits: {
      maxNewFolders: 5, maxNewFoldersLimit: 10, notesChars: 600,
      notesCharsMax: 2000, maxTagsPerDoc: 8, maxSeedTags: 50,
    },
    folderMode: 'reuse_or_new', tagMode: 'vocab',
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

  /** `preview` 非空 → 挂载后自动进审定阶段（这是组件里唯一一处阶段切换） */
  const mount = (preview: ClassifyPreview | null, onConfirm = vi.fn(),
                 error: string | null = null) => {
    const r = render(
      <ClassifyDialog ids={['tr_a', 'tr_b']} folderCount={1} tagCount={2}
        running={false} committing={false}
        error={error} preview={preview}
        onStart={vi.fn()} onDepthChange={vi.fn()} onClose={vi.fn()} onConfirm={onConfirm} />,
    )
    return { ...r, onConfirm }
  }

  it('DEPTH_OPTIONS 首项即默认档 600（初值取 `[0].value`，故**顺序即默认值**）', () => {
    // ⚠️ 这条钉的是「调序 = 静默改默认档」这个隐式契约：把「仅标题」提到首位，
    //    所有用户的初始取材深度会悄悄变成 0，而界面上没有任何异常。
    expect(DEPTH_OPTIONS[0].value).toBe(600)
  })

  /**
   * **P1-2**：审定阶段重跑失败（`error` 非空）而手上的建议仍在 → 「应用」**必须仍可用**。
   *
   * 父组件 `loadClassifyPreview` 的 `.catch` 只设 error、不碰 preview（注释明写
   * 「旧的留在屏幕上 + 顶部一条错误行，用户可以先用手上的」）。`canConfirm` 若带
   * `!error`，就把这条设计意图当场推翻：建议明明摆在眼前，「应用」却是灰的。
   *
   * 对照：把 `!error` 加回 `canConfirm` → 本条红。
   */
  it('审定阶段重跑失败 → 手上的建议仍可用（「应用」不禁用）', () => {
    mount(base(), vi.fn(), '模型没有返回可用的分类结果，请重试')
    expect(dlg('[data-classify-error]')).toBeTruthy()          // 错误确实显示出来了
    expect((dlg('[data-confirm-classify]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('把「新建」改成归入现有 → plan 里带的是现有夹的 id，不再要求建夹；标签按逗号清洗', () => {
    const { onConfirm } = mount(base())

    fireEvent.change(dlg('[data-classify-folder-select="tr_b"]') as HTMLSelectElement,
      { target: { value: 'reuse:fd_1' } })
    expect(dlg('[data-classify-row="tr_b"]')!
      .getAttribute('data-classify-folder-mode')).toBe('reuse')
    fireEvent.change(dlg('[data-classify-tags="tr_b"]') as HTMLInputElement,
      { target: { value: ' 综述， 对比 ,, 综述 ' } })
    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)

    const plan = onConfirm.mock.calls[0][0]
    const row = plan.find((r: { id: string }) => r.id === 'tr_b')
    expect(row.folderId).toBe('fd_1')
    // ⚠️ 名字留空 = 不建夹（否则会凭空多出一个没人要的空文件夹，且界面上看不出来）
    expect(row.newFolderName).toBe('')
    // 前端只做「分割 + 去空白」，**不去重**——与 `BatchBar.commitTag` 逐字同款，
    // 去重归写入侧（`_merge_tags` 的 add 是并入去重）。断言按现状写，不按期望写：
    // 若在此处改成断言去重后的结果，测试就会「绿着」而代码里根本没有那段逻辑。
    expect(row.tags).toEqual(['综述', '对比', '综述'])
  })

  /**
   * **候补**（F7）：被「新建超限」拒掉的篇多出一个「新建：XX（模型原建议）」选项，
   * 采纳后把模型原本的夹名**预填**进新建名输入框（用户不必重打）。
   *
   * 对照：删掉 select 里 `d.mode !== 'new' && !!d.candidate` 那个 option → 本条红。
   */
  it('超限被拒的候补 → 多一个可采纳的「新建：XX（模型原建议）」选项', () => {
    const { onConfirm } = mount(base({
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'none', candidate: '乙类' }, tags: [],
          warnings: ['模型原本建议新建「乙类」，但新建数量已超过上限（1 个），已改为不归入'] },
      ],
    }))

    const sel = dlg('[data-classify-folder-select="tr_a"]') as HTMLSelectElement
    expect(Array.from(sel.options).find(o => o.value === 'new')?.textContent).toContain('乙类')

    fireEvent.change(sel, { target: { value: 'new' } })
    expect(dlg('[data-classify-row="tr_a"]')!
      .getAttribute('data-classify-folder-mode')).toBe('new')
    // ⚠️ 预填是关键：只给选项不给名字，用户还得照抄一遍模型的建议
    expect((dlg('[data-classify-new-name="tr_a"]') as HTMLInputElement).value).toBe('乙类')

    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)
    const row = onConfirm.mock.calls[0][0].find((r: { id: string }) => r.id === 'tr_a')
    expect(row.newFolderName).toBe('乙类')
  })

  it('本来就无归处的「不归入」**不给**候补选项（两者不得混同）', () => {
    mount(base({
      suggestions: [
        { id: 'tr_a', title: '论文 A', source: 'notes', chars: 600,
          folder: { action: 'none' }, tags: [], warnings: [] },
      ],
    }))
    const sel = dlg('[data-classify-folder-select="tr_a"]') as HTMLSelectElement
    expect(Array.from(sel.options).some(o => o.value === 'new')).toBe(false)
  })

  it('未分类 = 空 folderId（后端把空串当未分类，不会误建空名夹）', () => {
    const { onConfirm } = mount(base())
    fireEvent.change(dlg('[data-classify-folder-select="tr_a"]') as HTMLSelectElement,
      { target: { value: 'none' } })
    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)
    expect(onConfirm.mock.calls[0][0].find((r: { id: string }) => r.id === 'tr_a').folderId).toBe('')
  })

  it('改动后重跑 preview → 草稿整体重建（不把新旧两批建议混在一起）', () => {
    const { rerender } = mount(base())
    fireEvent.change(dlg('[data-classify-tags="tr_b"]') as HTMLInputElement,
      { target: { value: '手改的标签' } })

    rerender(
      <ClassifyDialog ids={['tr_a', 'tr_b']} folderCount={1} tagCount={2}
        running={false} committing={false}
        error={null} preview={base()} onStart={vi.fn()} onDepthChange={vi.fn()}
        onClose={vi.fn()} onConfirm={vi.fn()} />,
    )
    expect(dlg<HTMLInputElement>('[data-classify-tags="tr_b"]')!.value).toBe('')
  })

  /**
   * 审定阶段的段控档位读的是**后端回显**（`limits.notesChars`），不是本地配置草稿。
   *
   * ⚠️ 切档请求由父组件发出（`onDepthChange`），对话框本地的 `cfg` 并不会跟着改——
   * 若段控继续读 `cfg`，切完之后**高亮纹丝不动**，而屏幕上的结果已经换了。
   */
  it('切档后段控高亮跟随**后端回显**的档位', () => {
    mount(base({ limits: {
      maxNewFolders: 5, maxNewFoldersLimit: 10, notesChars: 0,
      notesCharsMax: 2000, maxTagsPerDoc: 8, maxSeedTags: 50,
    } }))
    // ⚠️ 对照：把 activeDepth 改回 `cfg.notesChars`（恒为 600）→ 高亮停在 600 → 红
    expect(dlg('[data-classify-depth="0"]')!.className).toContain('accent')
    expect(dlg('[data-classify-depth="600"]')!.className).not.toContain('accent')
  })

  it('行数为 0 或无预览时「应用」禁用（空方案不该发出一次写库请求）', () => {
    mount(base({ suggestions: [] }))
    expect((dlg('[data-confirm-classify]') as HTMLButtonElement).disabled).toBe(true)

    cleanup()
    mount(null)   // 还在配置阶段：压根没有「应用」这个按钮
    expect(dlg('[data-confirm-classify]')).toBeNull()
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
    await runClassify(2)
    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_b"]')).toBeTruthy())

    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)
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
    await runClassify(1)
    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_a"]')).toBeTruthy())

    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)
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
    await runClassify(2)
    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_b"]')).toBeTruthy())

    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)
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
    await runClassify(1)
    await vi.waitFor(() => expect(dlg('[data-classify-row="tr_a"]')).toBeTruthy())

    fireEvent.click(dlg('[data-confirm-classify]') as HTMLElement)
    await vi.waitFor(() => expect(useStore.getState().toasts.length).toBeGreaterThan(0))

    // 一篇都不许写：带着一半文件夹去 commit 会让分类结果与用户的审定不符
    expect(commitCalls()).toEqual([])
    expect(dlg('[data-classify-dialog]')).toBeTruthy()
  })
})

describe('智能分类 — 卸载即取消', () => {
  it('折叠面板（卸载组件）会 abort 那次计费请求', async () => {
    useStore.setState({ docs: twoDocs() })
    const { unmount } = await openClassify(2)
    fireEvent.click(dlg('[data-start-classify]') as HTMLElement)
    await vi.waitFor(() => expect(previewCalls().length).toBe(1))

    const sig = previewCalls()[0].init.signal as AbortSignal
    expect(sig).toBeTruthy()
    expect(sig.aborted).toBe(false)

    unmount()

    // ⚠️ 对照 D：删掉 cleanup 里的 abort → 此处停在 false → 红
    expect(sig.aborted).toBe(true)
  })
})
