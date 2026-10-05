/**
 * 文档标签栏组件 —— DOM 契约与交互接线。
 *
 * store 层的语义已由 docTabs.test.ts 钉住；这里只守**组件真的把它接上了**：
 * 三个测试钩子（`data-doc-tab` / `data-active` / `data-preview`）、关闭按钮的
 * `stopPropagation`、中键、右键菜单、标题现取。
 *
 * 对照实验：
 *   A. 关闭按钮漏 `stopPropagation` → 关非激活标签时冒泡到行 onClick →
 *      `openDocTab(已移除的 id)` → `currentDocId` 被切到刚关掉的那篇 → 红
 *   B. 标题存进 `DocTab` 快照而非现取 → 重命名后标签仍显示旧名 → 红
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useStore } from '../store'
import type { DocEntry } from '../store/types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    listTranscripts: vi.fn().mockResolvedValue([]),
    listFolders: vi.fn().mockResolvedValue([]),
    getMarkdown: vi.fn(),
  }
})

const { DocTabBar } = await import('../components/preview/DocTabBar')

function makeDocs(n: number): DocEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `d${i}`, title: `文档 ${i}`, sourceType: 'pdf', engine: 'pypdfium2',
    contentHash: `h${i}`, tags: [], createdAt: `2026-01-01T00:00:0${i}`,
  }))
}

const tabEls = (c: HTMLElement) => [...c.querySelectorAll('[data-doc-tab]')] as HTMLElement[]
const closeBtnOf = (el: HTMLElement) => el.querySelector('[data-tab-close]') as HTMLElement
const S = () => useStore.getState()

/**
 * ⚠️ 不能用 `fireEvent.auxClick` —— 这个版本的 testing-library 没有该事件映射。
 * 手写 MouseEvent 反而更贴近真实：中键点击就是带 `button: 1` 的 auxclick。
 */
const auxClick = (el: HTMLElement, button: number) =>
  fireEvent(el, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button }))

function seed(docs: DocEntry[], tabs: [string, boolean][], currentId: string | null) {
  useStore.setState({
    docs,
    docTabs: tabs.map(([docId, pinned]) => ({ docId, pinned })),
    currentDocId: currentId,
    pendingOpenMode: null,
    pendingClose: null,
    dirtyEditingDocId: null,
  })
}

beforeEach(() => {
  useStore.setState({
    docs: [], currentDocId: null, docTabs: [], toasts: [],
    dirtyEditingDocId: null, pendingOpenMode: null, pendingClose: null,
  })
})

afterEach(() => cleanup())

// ── 渲染契约 ────────────────────────────────────────────────

describe('DocTabBar — 渲染契约', () => {
  it('每个标签一个 [data-doc-tab]，数量与 docTabs 一致', () => {
    seed(makeDocs(3), [['d0', true], ['d1', true], ['d2', true]], 'd0')
    const { container } = render(<DocTabBar />)
    expect(tabEls(container).map(e => e.getAttribute('data-doc-tab'))).toEqual(['d0', 'd1', 'd2'])
  })

  it('激活项是唯一带 data-active 的那个', () => {
    seed(makeDocs(3), [['d0', true], ['d1', true], ['d2', true]], 'd1')
    const { container } = render(<DocTabBar />)
    const actives = tabEls(container).filter(e => e.hasAttribute('data-active'))
    expect(actives).toHaveLength(1)
    expect(actives[0].getAttribute('data-doc-tab')).toBe('d1')
  })

  it('预览槽带 data-preview（「斜体标记」这条语义落到 DOM）', () => {
    seed(makeDocs(2), [['d0', true], ['d1', false]], 'd0')
    const { container } = render(<DocTabBar />)
    const [a, b] = tabEls(container)
    expect(a.hasAttribute('data-preview')).toBe(false)
    expect(b.getAttribute('data-preview')).toBe('true')
  })

  it('docTabs 为空时整条不渲染（欢迎页不该出现一条空标签栏）', () => {
    seed(makeDocs(1), [], null)
    const { container } = render(<DocTabBar />)
    expect(container.querySelector('[data-doc-tab-bar]')).toBe(null)
  })

  it('标题**现取**自 docs：重命名后标签跟着变', () => {
    // 对照 B：把 title 存进 DocTab 快照的实现 → 这里仍是旧标题 → 红
    seed(makeDocs(1), [['d0', true]], 'd0')
    const { container } = render(<DocTabBar />)
    expect(tabEls(container)[0].textContent).toContain('文档 0')

    // setState 发生在 React 之外，必须 act 包住才会同步重渲染到 DOM
    act(() => {
      useStore.setState({ docs: [{ ...makeDocs(1)[0], title: '改过的标题' }] })
    })
    expect(tabEls(container)[0].textContent).toContain('改过的标题')
  })

  it('文档已不在 docs 里（被删）→ 兜底文案，不崩', () => {
    seed([], [['ghost', true]], 'ghost')
    const { container } = render(<DocTabBar />)
    expect(tabEls(container)[0].textContent).toContain('已删除的文档')
  })
})

// ── 交互 ────────────────────────────────────────────────────

describe('DocTabBar — 交互', () => {
  it('点非激活标签 → 切过去', () => {
    seed(makeDocs(2), [['d0', true], ['d1', true]], 'd0')
    const { container } = render(<DocTabBar />)
    fireEvent.click(tabEls(container)[1])
    expect(S().currentDocId).toBe('d1')
  })

  it('⚠️ 点关闭按钮**不**激活该标签（stopPropagation）', () => {
    // 对照 A：漏 stopPropagation → closeDocTab 移除 d1 后，click 继续冒泡到行 onClick
    // → openDocTab('d1') 发现它不在组内 → selectDoc('d1') → currentDocId 变成刚关掉的那篇
    seed(makeDocs(2), [['d0', true], ['d1', true]], 'd0')
    const { container } = render(<DocTabBar />)

    fireEvent.click(closeBtnOf(tabEls(container)[1]))

    expect(S().currentDocId).toBe('d0')
    expect(useStore.getState().docTabs.map(t => t.docId)).toEqual(['d0'])
  })

  it('中键关闭标签（判断的是 button === 1）', () => {
    seed(makeDocs(2), [['d0', true], ['d1', true]], 'd0')
    const { container } = render(<DocTabBar />)
    auxClick(tabEls(container)[1], 1)
    expect(useStore.getState().docTabs.map(t => t.docId)).toEqual(['d0'])
    expect(S().currentDocId).toBe('d0')
  })

  it('左键的 auxClick（button 0）不关闭', () => {
    seed(makeDocs(2), [['d0', true], ['d1', true]], 'd0')
    const { container } = render(<DocTabBar />)
    auxClick(tabEls(container)[1], 0)
    expect(useStore.getState().docTabs).toHaveLength(2)
  })

  it('右键出菜单：关闭 / 关闭其他 / 关闭全部', () => {
    seed(makeDocs(2), [['d0', true], ['d1', true]], 'd0')
    const { container } = render(<DocTabBar />)
    fireEvent.contextMenu(tabEls(container)[0], { clientX: 10, clientY: 10 })

    // ContextMenu 走 Portal 到 body，故查 screen 而不是 container
    expect(screen.getByText('关闭')).toBeTruthy()
    expect(screen.getByText('关闭其他')).toBeTruthy()
    expect(screen.getByText('关闭全部')).toBeTruthy()
  })

  it('菜单里的「固定为常驻标签」只对预览槽出现', () => {
    seed(makeDocs(2), [['d0', true], ['d1', false]], 'd0')
    const { container } = render(<DocTabBar />)

    fireEvent.contextMenu(tabEls(container)[0], { clientX: 10, clientY: 10 })   // 常驻项
    expect(screen.queryByText('固定为常驻标签')).toBe(null)
    cleanup()

    const c2 = render(<DocTabBar />).container
    fireEvent.contextMenu(tabEls(c2)[1], { clientX: 10, clientY: 10 })         // 预览槽
    expect(screen.getByText('固定为常驻标签')).toBeTruthy()
  })

  it('菜单点「关闭其他」→ 只留当前标签', () => {
    seed(makeDocs(3), [['d0', true], ['d1', true], ['d2', true]], 'd1')
    const { container } = render(<DocTabBar />)
    fireEvent.contextMenu(tabEls(container)[1], { clientX: 10, clientY: 10 })
    fireEvent.click(screen.getByText('关闭其他'))
    expect(useStore.getState().docTabs.map(t => t.docId)).toEqual(['d1'])
  })

  it('键盘可达：Enter 激活标签', () => {
    seed(makeDocs(2), [['d0', true], ['d1', true]], 'd0')
    const { container } = render(<DocTabBar />)
    fireEvent.keyDown(tabEls(container)[1], { key: 'Enter' })
    expect(S().currentDocId).toBe('d1')
  })
})

// ── 未保存编辑的视觉 ────────────────────────────────────────

describe('DocTabBar — 未保存编辑', () => {
  it('dirty 标签的关闭按钮：圆点常驻可见，X 退到 hover 才出现', () => {
    // ⚠️ 判据必须是 **class**，不是「元素在不在 DOM 里」——X 一直都在，靠 `hidden` 换掉的。
    // 用 `querySelector('svg') === null` 会恒假（切到 dirty 分支也照样红/绿错位）。
    seed(makeDocs(1), [['d0', true]], 'd0')
    useStore.setState({ dirtyEditingDocId: 'd0' })
    const { container } = render(<DocTabBar />)

    const btn = closeBtnOf(tabEls(container)[0])
    const dot = btn.querySelector('span')!
    const x = btn.querySelector('svg')!
    // ⚠️ 用 classList.contains 而非 `className.includes('hidden')` —— 后者会被
    // `group-hover/tab:hidden` 这个**子串**误判成「被隐藏了」（圆点正是靠它换 X 的）。
    // 判据必须是「类清单里有没有 hidden 这一项」，不是「字符串里有没有这几个字母」。
    expect(dot.classList.contains('hidden')).toBe(false)
    expect(x.classList.contains('hidden')).toBe(true)
    // 圆点用主题强调色，不是硬编码色值
    expect(dot.classList.contains('bg-accent')).toBe(true)
  })

  it('非 dirty 标签：X 直接可见，没有圆点', () => {
    seed(makeDocs(1), [['d0', true]], 'd0')
    const { container } = render(<DocTabBar />)
    const btn = closeBtnOf(tabEls(container)[0])
    expect(btn.querySelector('span')).toBe(null)
    expect(btn.querySelector('svg')).not.toBe(null)
  })
})
