/**
 * 文档标签页（VS Code 双标签模型）—— store 层。
 *
 * 被测的是**延后提交**这套机制：导航意图先进 `pending*`，只有真正执行
 * `loadDocContent`（→ `commitTabChange`）时才落到 `docTabs`。它存在的唯一理由是
 * 「未保存编辑」的取消分支能天然回滚 —— 标签组**从未被动过**，不需要快照。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现 → 都会红）：
 *   A. 三个 close 动作一律进 pending → **关非激活标签永远关不掉**（pendingClose 只被
 *      commitTabChange 消费，而它只在 loadDocContent 里调；关非激活项不触发后者）→ 红
 *   B. 预览打开写成 append 而非「替换现有预览槽」→ 组里出现两个斜体标签 → 红
 *   C. openDocTab 直接改 docTabs（不加 pending）→ 取消后标签组回不去 → 红
 *   D. commitTabChange 漏掉「activeId 不在组内」的兜底 → 启动恢复 / GenPanel 的
 *      selectDoc 路径全都不产生标签 → 红
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useStore } from '../store'
import type { DocTab } from '../store/docsSlice'

const tabs = (): DocTab[] => useStore.getState().docTabs
const ids = (): string[] => tabs().map(t => t.docId)
const S = () => useStore.getState()

/** 走一遍真实链路：点击开标签 → 内容加载完成提交 */
function openAndCommit(id: string, mode: 'preview' | 'pinned' = 'preview') {
  S().openDocTab(id, mode)
  S().commitTabChange(id)
}

/** 直接把标签组铺成目标状态（省去重复的 open/commit 序列） */
function seed(entries: [string, boolean][], currentId: string | null) {
  useStore.setState({
    docTabs: entries.map(([docId, pinned]) => ({ docId, pinned })),
    currentDocId: currentId,
    pendingOpenMode: null,
    pendingClose: null,
  })
}

/** 关闭激活标签后，模拟 loadDocContent 走到提交 */
function closeAndCommit(id: string) {
  S().closeDocTab(id)
  S().commitTabChange(S().currentDocId)
}

beforeEach(() => {
  useStore.setState({
    docs: [],
    currentDocId: null,
    docTabs: [],
    pendingOpenMode: null,
    pendingClose: null,
    currentContent: null,
    currentPolishedContent: null,
    currentRawText: null,
    markdownMeta: null,
    markdownVersions: [],
    markdownEdited: { notes: false, polished: false },
    imageResults: {},
    toasts: [],
  })
})

// ── 延后提交 ────────────────────────────────────────────────

describe('延后提交 — 标签组只在 commitTabChange 时落地', () => {
  it('openDocTab 后标签组不含目标，但 currentDocId 已经切过去了', () => {
    S().openDocTab('B', 'preview')
    // 对照 C：直接改 docTabs 的实现 → 这里已经有 B 了 → 后面的取消就回不去
    expect(tabs()).toEqual([])
    expect(S().pendingOpenMode).toBe('preview')
    // ⚠️ 两个状态刻意不同步：currentDocId 是「内容层」的意图（守卫 effect 要看到它），
    // docTabs 是「导航层」的事实（要等守卫放行才承认）
    expect(S().currentDocId).toBe('B')
  })

  it('cancelTabChange 后标签组原样不变 —— 「取消」等于什么都没发生', () => {
    openAndCommit('A', 'pinned')
    S().openDocTab('B', 'preview')
    S().cancelTabChange()
    expect(tabs()).toEqual([{ docId: 'A', pinned: true }])
    expect(S().pendingOpenMode).toBe(null)
  })

  it('commitTabChange 之后才出现新标签', () => {
    S().openDocTab('B', 'preview')
    S().commitTabChange('B')
    expect(tabs()).toEqual([{ docId: 'B', pinned: false }])
    expect(S().pendingOpenMode).toBe(null)
  })

  it('commitTabChange 是幂等的：同一 id 提交两次不产生第二个标签', () => {
    openAndCommit('A', 'preview')
    S().commitTabChange('A')
    expect(tabs()).toHaveLength(1)
  })
})

// ── 预览槽（斜体项）────────────────────────────────────────

describe('预览标签——替换而非追加', () => {
  it('⚠️ 第二个预览替换第一个的**位置**，不是追加到末尾', () => {
    // 对照 B：append 实现 → 得到 [A(p), B(常驻), C(p)] 长度 3、有两个斜体 → 红
    openAndCommit('A', 'preview')   // [A(p)]
    openAndCommit('B', 'pinned')    // [A(p), B(常驻)]
    openAndCommit('C', 'preview')   // 应替换 A 的槽位 → [C(p), B(常驻)]
    expect(tabs()).toEqual([
      { docId: 'C', pinned: false },
      { docId: 'B', pinned: true },
    ])
  })

  it('没有预览槽时追加到末尾', () => {
    openAndCommit('A', 'pinned')
    openAndCommit('B', 'preview')
    expect(tabs()).toEqual([
      { docId: 'A', pinned: true },
      { docId: 'B', pinned: false },
    ])
  })

  it('连开 3 个预览后，全组至多一个斜体项（不变量）', () => {
    openAndCommit('A', 'preview')
    openAndCommit('B', 'preview')
    openAndCommit('C', 'preview')
    expect(tabs().filter(t => !t.pinned)).toHaveLength(1)
    expect(ids()).toEqual(['C'])
  })
})

// ── 已打开的文档 ────────────────────────────────────────────

describe('已打开的文档——直接切过去', () => {
  it('单击一个常驻标签：长度不变、pinned 不变、只是激活', () => {
    openAndCommit('A', 'pinned')
    openAndCommit('B', 'pinned')
    S().openDocTab('A', 'preview')
    expect(tabs()).toEqual([
      { docId: 'A', pinned: true },
      { docId: 'B', pinned: true },
    ])
    expect(S().currentDocId).toBe('A')
    // 不进 pending —— 标签已在组内，没什么可提交的
    expect(S().pendingOpenMode).toBe(null)
  })

  it('对预览标签「双击」→ 就地翻成常驻，不新增标签', () => {
    openAndCommit('A', 'preview')
    S().openDocTab('A', 'pinned')
    expect(tabs()).toEqual([{ docId: 'A', pinned: true }])
    expect(S().pendingOpenMode).toBe(null)
  })

  it('双击天然幂等：click→click→dblclick 不产生重复标签', () => {
    // 浏览器双击会先派发两次 click。第二次 click 时文档已在组内 → no-op
    S().openDocTab('A', 'preview'); S().commitTabChange('A')   // 第一次 click 走完
    S().openDocTab('A', 'preview'); S().commitTabChange('A')   // 第二次 click
    S().openDocTab('A', 'pinned')                              // dblclick
    expect(tabs()).toEqual([{ docId: 'A', pinned: true }])
  })

  it('pinDocTab 对已是常驻的标签 / 不存在的标签都是 no-op', () => {
    openAndCommit('A', 'pinned')
    S().pinDocTab('A')
    S().pinDocTab('nope')
    expect(tabs()).toEqual([{ docId: 'A', pinned: true }])
  })
})

// ── 关闭：两支判据（最关键的一组）──────────────────────────

describe('关闭——「是否改变 currentDocId」决定立即执行还是延后', () => {
  it('⚠️ 关**非激活**标签：立即生效，且 pendingClose 保持 null', () => {
    // 对照 A：三个 close 一律进 pending → 这里 docTabs 纹丝不动 →
    // 而关非激活项**不会**触发 loadDocContent，那个 pendingClose 永远没人消费
    // → **标签永远关不掉，且零报错**。这条测试就是为它设的。
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().closeDocTab('A')
    expect(ids()).toEqual(['B', 'C'])
    expect(S().pendingClose).toBe(null)
    expect(S().currentDocId).toBe('B')   // 激活项不受影响
  })

  it('关**激活**标签：走 pendingClose，提交前标签组不变', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().closeDocTab('B')
    expect(S().pendingClose).toEqual({ mode: 'one', id: 'B' })
    expect(ids()).toEqual(['A', 'B', 'C'])   // 尚未提交
    expect(S().currentDocId).toBe('C')       // 但激活已经交给右邻了
  })

  it('关闭激活标签后取消 → 标签组原样、没关掉', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().closeDocTab('B')
    S().cancelTabChange()
    expect(ids()).toEqual(['A', 'B', 'C'])
    expect(S().pendingClose).toBe(null)
  })

  it('关闭激活标签后提交 → 真的少一个', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    closeAndCommit('B')
    expect(ids()).toEqual(['A', 'C'])
    expect(S().currentDocId).toBe('C')
  })

  it('关闭不存在的标签是 no-op（不改标签组、不进 pending）', () => {
    seed([['A', true]], 'A')
    S().closeDocTab('ghost')
    expect(ids()).toEqual(['A'])
    expect(S().pendingClose).toBe(null)
  })
})

describe('关闭后的激活归属（VS Code：先右邻，无则左邻）', () => {
  it('关中间的 → 激活右邻', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    closeAndCommit('B')
    expect(S().currentDocId).toBe('C')
  })

  it('关最右的 → 退到左邻', () => {
    seed([['A', true], ['B', true], ['C', true]], 'C')
    closeAndCommit('C')
    expect(S().currentDocId).toBe('B')
  })

  it('关最后一个 → currentDocId 变 null（回欢迎页）', () => {
    seed([['A', true]], 'A')
    closeAndCommit('A')
    expect(S().currentDocId).toBe(null)
    expect(tabs()).toEqual([])
  })
})

// ── 关闭其他 / 关闭全部 ────────────────────────────────────

describe('closeOtherDocTabs / closeAllDocTabs', () => {
  it('保留的正是激活项 → 立即 filter，不进 pending', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().closeOtherDocTabs('B')
    expect(ids()).toEqual(['B'])
    expect(S().pendingClose).toBe(null)
  })

  it('保留的不是激活项 → 走 pending + 切过去', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().closeOtherDocTabs('A')
    expect(S().pendingClose).toEqual({ mode: 'others', keepId: 'A' })
    expect(S().currentDocId).toBe('A')
    S().commitTabChange('A')
    expect(ids()).toEqual(['A'])
  })

  it('关闭其他后取消 → 一个都没关', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().closeOtherDocTabs('A')
    S().cancelTabChange()
    expect(ids()).toEqual(['A', 'B', 'C'])
  })

  it('closeAll：currentDocId 为 null 时直接清空（不触发无效的 selectDoc）', () => {
    seed([['A', true], ['B', true]], null)
    S().closeAllDocTabs()
    expect(tabs()).toEqual([])
    expect(S().pendingClose).toBe(null)
  })

  it('closeAll：有激活项时走 pending，提交后清空并回欢迎页', () => {
    seed([['A', true], ['B', true]], 'B')
    S().closeAllDocTabs()
    expect(S().pendingClose).toEqual({ mode: 'all' })
    expect(S().currentDocId).toBe(null)
    expect(tabs()).toHaveLength(2)   // 尚未提交
    S().commitTabChange(null)
    expect(tabs()).toEqual([])
  })
})

// ── 兜底分支 ────────────────────────────────────────────────

describe('commitTabChange 的兜底追加', () => {
  it('⚠️ 无 pending 且 activeId 不在组内 → 追加为常驻', () => {
    // 对照 D：漏掉这条兜底 → 启动恢复、GenPanel 的 8 处 selectDoc、
    // MessageItem 的引用跳转全都**没有任何标签**（内容是加载了，标签栏是空的）→ 红
    S().commitTabChange('X')
    expect(tabs()).toEqual([{ docId: 'X', pinned: true }])
  })

  it('启动恢复（只有 currentDocId）→ 恰好一个常驻标签', () => {
    useStore.setState({ currentDocId: 'A' })
    S().commitTabChange('A')
    expect(tabs()).toEqual([{ docId: 'A', pinned: true }])
  })

  it('null / 空串不产生标签（欢迎页、以及 selectDoc(\'\') 那条路径）', () => {
    S().commitTabChange(null)
    S().commitTabChange('')
    expect(tabs()).toEqual([])
  })

  it('兜底追加不与 pendingOpenMode 打架：有 pending 时按 pending 的语义走', () => {
    S().openDocTab('A', 'preview')
    S().commitTabChange('A')
    expect(tabs()).toEqual([{ docId: 'A', pinned: false }])   // 不是常驻
  })
})

// ── 删除清理 ────────────────────────────────────────────────

describe('purgeDocArtifacts 与标签组', () => {
  it('删掉激活的文档 → 滤掉它 + 激活**原下标**处的邻居', () => {
    seed([['A', true], ['B', true], ['C', true]], 'B')
    S().purgeDocArtifacts(['B'])
    expect(ids()).toEqual(['A', 'C'])
    expect(S().currentDocId).toBe('C')
  })

  it('删掉最后一个且它是激活项 → currentDocId 变 null', () => {
    seed([['A', true]], 'A')
    S().purgeDocArtifacts(['A'])
    expect(tabs()).toEqual([])
    expect(S().currentDocId).toBe(null)
  })

  it('删非激活文档 → 激活项不动，预览内容也不清', () => {
    seed([['A', true], ['B', true]], 'B')
    useStore.setState({ currentContent: '还开着的正文' })
    S().purgeDocArtifacts(['A'])
    expect(ids()).toEqual(['B'])
    expect(S().currentDocId).toBe('B')
    // 清掉的必须是「被删的那篇」的内容，删别人时清空 = 无端丢掉阅读位置
    expect(S().currentContent).toBe('还开着的正文')
  })

  it('一次删多篇：组内一次滤净', () => {
    seed([['A', true], ['B', true], ['C', true], ['D', true]], 'A')
    S().purgeDocArtifacts(['B', 'D'])
    expect(ids()).toEqual(['A', 'C'])
    expect(S().currentDocId).toBe('A')   // 激活项没被删 → 不动
  })

  it('删除会清掉在途的导航意图（目标可能正是被删的那篇）', () => {
    seed([['A', true]], 'A')
    S().openDocTab('B', 'preview')
    S().purgeDocArtifacts(['B'])
    expect(S().pendingOpenMode).toBe(null)
    expect(S().pendingClose).toBe(null)
  })

  it('空数组是 no-op', () => {
    seed([['A', true]], 'A')
    S().purgeDocArtifacts([])
    expect(ids()).toEqual(['A'])
  })
})
