/**
 * 活动栏 —— 图标集来源、激活态、动作接线。
 *
 * 本文件的断言**刻意不硬编码图标个数或 id 列表**，一律与 `SlotRegistry.grouped().left`
 * 的派生结果比对。理由有两条：
 *   ① 注册表是可变单例且**没有 `unregister`** —— 本文件里注册的假面板会一直留着；
 *      硬编码 `['genPanel','docLib']` 的断言会在那条「加注册项」用例之后自己变红。
 *   ② 这条链的全部价值就是「加一个面板只改注册表一处」。判据写成派生 → 它顺带就是
 *      那句话的**可执行版本**；写成拷贝 → 它只证明今天手抄得对。
 *
 * 对照实验：
 *   A. 图标集写成 `SlotRegistry.all()` → 预览区（center）也冒出一个图标 → 红
 *   B. 图标集写成硬编码数组 → 下面「注册新面板」那条用例不再随注册表变化 → 红
 *   C. 激活判据写成「slot 可见就是激活」（不用 `isPanelOpen`）→ 互斥模式下未激活的
 *      面板也显示高亮 → 红
 *   D. 动作项（设置）也写 `aria-pressed` → 被读屏当作切换按钮 → 红
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, act } from '@testing-library/react'
import { Sliders } from 'lucide-react'
import { useStore } from '../store'
import { SlotRegistry } from '../lib/slotRegistry'
import { PANEL_DEFAULT_WIDTH, PANEL_MIN_WIDTH, PANEL_MAX_WIDTH } from '../lib/slotMetrics'

// 三个面板组件换成 stub —— 活动栏只关心「注册表里有什么」，不关心面板内部。
// 不 mock 的话 render 会连带挂载真实 GenPanel / DocLibrary 并触发它们的网络请求。
vi.mock('../components/gen-panel/GenPanel', () => ({ GenPanel: () => <div data-stub="genPanel" /> }))
vi.mock('../components/doc-library/DocLibrary', () => ({ DocLibrary: () => <div data-stub="docLib" /> }))
vi.mock('../components/preview/PreviewArea', () => ({ PreviewArea: () => <div data-stub="preview" /> }))

const { ActivityBar } = await import('../components/ui/ActivityBar')

const S = () => useStore.getState()
const iconsOf = (c: HTMLElement) =>
  [...c.querySelectorAll('[data-activity-item]')].map(e => e.getAttribute('data-activity-item'))
const itemOf = (c: HTMLElement, id: string) =>
  c.querySelector(`[data-activity-item="${id}"]`) as HTMLElement

/** 活动栏的完整图标名单：注册表 left 侧的派生 + 两个动作项（它们不是 slot） */
const expectedIcons = () => [...SlotRegistry.grouped().left.map(d => d.id), 'ai', 'settings']

beforeEach(() => {
  useStore.setState({
    layout: { leftPanelsExclusive: true },
    activeLeftPanel: 'docLib',
    aiVisible: false,
    slots: {
      genPanel: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true },
      docLib: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true },
      preview: { width: 0, collapsed: false, visible: true },
    },
  })
})

afterEach(cleanup)

// ── 图标集来源 ──────────────────────────────────────────────

describe('图标集由注册表派生', () => {
  it('等于 left 侧注册项 + 两个动作项，顺序一致', () => {
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(iconsOf(container)).toEqual(expectedIcons())
  })

  it('⚠️ 不含 center 侧的面板 —— 活动栏只管左侧', () => {
    // 对照 A：写成 SlotRegistry.all() 的实现 → preview 也出现 → 红
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(iconsOf(container)).not.toContain('preview')
  })
})

describe('激活态 — 判据与布局层同源', () => {
  it('互斥模式：只有当前激活的面板高亮', () => {
    useStore.setState({ activeLeftPanel: 'docLib' })
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(itemOf(container, 'docLib').getAttribute('aria-pressed')).toBe('true')
    // 对照 C：只判 `slot.visible` 的实现 → 这里也是 true → 红
    expect(itemOf(container, 'genPanel').getAttribute('aria-pressed')).toBe('false')
  })

  it('互斥模式：全收起（active=null）时无一高亮', () => {
    useStore.setState({ activeLeftPanel: null })
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(itemOf(container, 'docLib').getAttribute('aria-pressed')).toBe('false')
    expect(itemOf(container, 'genPanel').getAttribute('aria-pressed')).toBe('false')
  })

  it('并排模式：两个面板项同时高亮（active 字段被忽略）', () => {
    useStore.setState({ layout: { leftPanelsExclusive: false }, activeLeftPanel: null })
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(itemOf(container, 'docLib').getAttribute('aria-pressed')).toBe('true')
    expect(itemOf(container, 'genPanel').getAttribute('aria-pressed')).toBe('true')
  })

  it('visible=false 的面板不高亮（它就是被彻底关掉的那个）', () => {
    useStore.setState(state => ({
      slots: { ...state.slots, genPanel: { ...state.slots.genPanel, visible: false } },
    }))
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(itemOf(container, 'genPanel').getAttribute('aria-pressed')).toBe('false')
  })

  it('AI 项按 aiVisible 高亮', () => {
    useStore.setState({ aiVisible: true })
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(itemOf(container, 'ai').getAttribute('aria-pressed')).toBe('true')
  })
})

// ── 接线 ────────────────────────────────────────────────────

describe('点击接线', () => {
  it('点未激活的面板 → 它成为激活项并展开', () => {
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    fireEvent.click(itemOf(container, 'genPanel'))
    expect(S().activeLeftPanel).toBe('genPanel')
    expect(S().slots.genPanel.collapsed).toBe(false)
    expect(S().slots.genPanel.visible).toBe(true)
  })

  it('再点一次正激活的面板 → 谁都不展开（VS Code 语义，不露出另一个）', () => {
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    // 起点是「文档库激活」：点生成 → 生成接管；再点生成 → 全收起（而**不是**回到文档库）
    fireEvent.click(itemOf(container, 'genPanel'))
    expect(S().activeLeftPanel).toBe('genPanel')
    fireEvent.click(itemOf(container, 'genPanel'))
    expect(S().activeLeftPanel).toBe(null)
  })

  it('拖窄过的面板点击后宽度复位（不然只展开出一条缝）', () => {
    useStore.setState(state => ({
      slots: { ...state.slots, genPanel: { width: 40, collapsed: true, visible: true } },
    }))
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    fireEvent.click(itemOf(container, 'genPanel'))
    expect(S().slots.genPanel.width).toBe(PANEL_DEFAULT_WIDTH)
    expect(S().slots.genPanel.collapsed).toBe(false)
  })

  it('AI 项转调 toggleAI', () => {
    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    fireEvent.click(itemOf(container, 'ai'))
    expect(S().aiVisible).toBe(true)
  })

  it('设置项调 onOpenSettings，且**不声明 aria-pressed**', () => {
    // 对照 D：动作项也写 aria-pressed → 读屏把它念成「切换按钮（未按下）」→ 红
    const onOpenSettings = vi.fn()
    const { container } = render(<ActivityBar onOpenSettings={onOpenSettings} />)
    const settings = itemOf(container, 'settings')
    expect(settings.hasAttribute('aria-pressed')).toBe(false)
    fireEvent.click(settings)
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })
})

// ── 「加一个面板只改注册表一处」 ─────────────────────────────
//
// ⚠️ 这条放在最后：`SlotRegistry` 没有 `unregister`，注册会留在本文件内。
// 之所以不影响上面任何用例，是因为它们全部走 `expectedIcons()` 的派生比对 ——
// 而这正是本文件开头那两条理由的实证。
describe('★ 新增一个 left 面板 → 活动栏自动多一个图标', () => {
  it('注册即出现，无需改 ActivityBar', () => {
    const before = SlotRegistry.grouped().left.length

    SlotRegistry.register({
      id: 'testPanel',
      side: 'left',
      component: () => <div data-stub="testPanel" />,
      label: '测试面板',
      icon: Sliders,
      defaultWidth: PANEL_DEFAULT_WIDTH,
      minWidth: PANEL_MIN_WIDTH,
      maxWidth: PANEL_MAX_WIDTH,
    })
    // 未建 slot 的面板也要出现（图标是入口，先能点，点了才建状态）
    act(() => {
      useStore.setState(state => ({
        slots: { ...state.slots, testPanel: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true } },
      }))
    })

    const { container } = render(<ActivityBar onOpenSettings={() => {}} />)
    expect(SlotRegistry.grouped().left.length).toBe(before + 1)
    expect(iconsOf(container)).toEqual(expectedIcons())
    expect(iconsOf(container)).toContain('testPanel')
  })
})
