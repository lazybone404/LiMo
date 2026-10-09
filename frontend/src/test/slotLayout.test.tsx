/**
 * 布局层 —— 互斥过滤 + 组件来源。
 *
 * 这组断言全部落在**渲染结果**上，而不是 `activeLeftPanel` 的值上。理由是本轮最容易
 * 出现的缺陷不是「状态算错了」，而是「状态算对了、布局层没用它」—— 那种缺陷下
 * 只断 store 字段的测试**全绿**，而用户看到的是两个面板并排。
 *
 * 对照实验：
 *   A. 过滤写成「两个都可见就都渲染」→ 用例 1/2/3 红
 *   B. 互斥下仍然渲染 `PanelStrip` → 3 号用例（关掉的那个不该留窄条）红
 *   C. `SlotLayout` 保留硬编码的 `COMPONENTS` 映射 → 末尾「新注册的面板」红
 *   D. 并排分支被互斥逻辑污染（顺手把另一个也收起）→ 4 号用例红
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Sliders } from 'lucide-react'
import { useStore } from '../store'
import { SlotRegistry } from '../lib/slotRegistry'
import { PANEL_DEFAULT_WIDTH, PANEL_MIN_WIDTH, PANEL_MAX_WIDTH } from '../lib/slotMetrics'

vi.mock('../components/gen-panel/GenPanel', () => ({ GenPanel: () => <div data-stub="genPanel" /> }))
vi.mock('../components/doc-library/DocLibrary', () => ({ DocLibrary: () => <div data-stub="docLib" /> }))
vi.mock('../components/preview/PreviewArea', () => ({ PreviewArea: () => <div data-stub="preview" /> }))

const { SlotLayout } = await import('../components/ui/SlotLayout')

/** 面板是否进了布局 —— 只能这样问（`data-panel` 是 SlotLayout 上的测试钩子） */
const rendered = (c: HTMLElement, id: string) => c.querySelector(`[data-panel="${id}"]`) !== null

/** 折叠窄条 —— 互斥模式下刻意不渲染它，故用它验证「关掉的确实什么都不留」 */
const stripOf = (c: HTMLElement, label: string) => c.querySelector(`[title="展开${label}"]`)

beforeEach(() => {
  useStore.setState({
    layout: { leftPanelsExclusive: true },
    activeLeftPanel: 'docLib',
    slots: {
      genPanel: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true },
      docLib: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true },
      preview: { width: 0, collapsed: false, visible: true },
    },
  })
})

afterEach(cleanup)

// ── 互斥：同一时刻只有一个左侧面板在 DOM 里 ──────────────────

describe('互斥模式（默认）', () => {
  it('激活文档库时，生成的 DOM 里没有任何节点', () => {
    const { container } = render(<SlotLayout />)
    expect(rendered(container, 'docLib')).toBe(true)
    expect(rendered(container, 'genPanel')).toBe(false)
  })

  it('激活生成面板时反过来', () => {
    useStore.setState({ activeLeftPanel: 'genPanel' })
    const { container } = render(<SlotLayout />)
    expect(rendered(container, 'genPanel')).toBe(true)
    expect(rendered(container, 'docLib')).toBe(false)
  })

  it('⚠️ 未激活的那个**连折叠窄条都不留**（一次只该有一个面板，窄条没有意义）', () => {
    // 对照 B：仍然渲染 PanelStrip 的实现 → 活动栏旁边多出一条 34px 竖条 → 红
    const { container } = render(<SlotLayout />)
    expect(stripOf(container, '生成')).toBe(null)
  })

  it('全收起（active=null）时两个都不在，只剩预览区', () => {
    useStore.setState({ activeLeftPanel: null })
    const { container } = render(<SlotLayout />)
    expect(rendered(container, 'genPanel')).toBe(false)
    expect(rendered(container, 'docLib')).toBe(false)
    expect(rendered(container, 'preview')).toBe(true)
  })

  it('center 侧的预览区不受互斥影响', () => {
    for (const active of ['docLib', 'genPanel', null]) {
      useStore.setState({ activeLeftPanel: active })
      const { container } = render(<SlotLayout />)
      expect(rendered(container, 'preview')).toBe(true)
      cleanup()
    }
  })
})

// ── 并排：完整保留改造前的行为 ──────────────────────────────

describe('并排模式（可切回）', () => {
  it('两个左侧面板同时在 DOM 里', () => {
    // 对照 D：互斥逻辑污染并排分支 → 这里只剩一个 → 红
    useStore.setState({ layout: { leftPanelsExclusive: false }, activeLeftPanel: null })
    const { container } = render(<SlotLayout />)
    expect(rendered(container, 'genPanel')).toBe(true)
    expect(rendered(container, 'docLib')).toBe(true)
  })

  it('收起的面板渲染为折叠窄条（而不是消失）', () => {
    useStore.setState(state => ({
      layout: { leftPanelsExclusive: false },
      activeLeftPanel: null,
      slots: { ...state.slots, docLib: { ...state.slots.docLib, collapsed: true } },
    }))
    const { container } = render(<SlotLayout />)
    expect(rendered(container, 'docLib')).toBe(false)
    expect(stripOf(container, '文档库')).not.toBe(null)
  })
})

// ── 组件来源：注册表持有组件本体 ────────────────────────────

describe('hideSlots 仍然生效', () => {
  it('互斥模式下列入 hideSlots 的面板照旧不渲染', () => {
    useStore.setState({ activeLeftPanel: 'genPanel' })
    const { container } = render(<SlotLayout hideSlots={['genPanel']} />)
    expect(rendered(container, 'genPanel')).toBe(false)
  })
})

describe('★ 面板组件来自 SlotDef.component', () => {
  it('新注册的 left 面板能渲染出来（布局层不再持有组件映射表）', () => {
    // 对照 C：`SlotLayout` 保留硬编码 COMPONENTS 的实现 → 新面板渲染不出来 → 红。
    // 这条同时是「加一个面板只改注册表一处」的可执行版本。
    SlotRegistry.register({
      id: 'testPanel2',
      side: 'left',
      component: () => <div data-stub="testPanel2" />,
      label: '测试面板二',
      icon: Sliders,
      defaultWidth: PANEL_DEFAULT_WIDTH,
      minWidth: PANEL_MIN_WIDTH,
      maxWidth: PANEL_MAX_WIDTH,
    })
    useStore.setState(state => ({
      activeLeftPanel: 'testPanel2',
      slots: { ...state.slots, testPanel2: { width: PANEL_DEFAULT_WIDTH, collapsed: false, visible: true } },
    }))
    const { container } = render(<SlotLayout />)
    expect(rendered(container, 'testPanel2')).toBe(true)
    expect(container.querySelector('[data-stub="testPanel2"]')).not.toBe(null)
  })

  it('注册项里没有 component 的假设不成立 —— 字段是必填的（类型层挡住）', () => {
    // 运行期无法构造缺失字段的合法 SlotDef（TS 会拦），故这里只钉住「取到的确实是
    // 那个函数」：注册表读回来的引用与写进去的**是同一个**，没有被包装/替换。
    const C = () => <div data-stub="ref" />
    SlotRegistry.register({
      id: 'testPanel3',
      side: 'left',
      component: C,
      label: '测试面板三',
      icon: Sliders,
      defaultWidth: PANEL_DEFAULT_WIDTH,
      minWidth: PANEL_MIN_WIDTH,
      maxWidth: PANEL_MAX_WIDTH,
    })
    expect(SlotRegistry.get('testPanel3')?.component).toBe(C)
  })
})
