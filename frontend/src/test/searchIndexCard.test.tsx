// SearchIndexCard — 「模型内存」三档释放策略的消费端
//
// 背景：本地 embedding 模型约占几十 MB 常驻内存。「建完索引放不放掉」这件事
// 交给用户选（`never` / `idle` / `immediate`），代价各不相同。
//
// ⚠️ 本文件钉住的**不是**「三个按钮能点」，而是两条纪律：
//   1. 档位清单**来自 capabilities**，前端不存第二份 —— 后端加一档而前端照旧，
//      用户就永远选不到它（而界面上完全看不出少了一档）。
//   2. 取不到 capabilities 时**整组不渲染**（问不到 ≠ 坏了），而不是糊一个本地默认表兜底
//      —— 后者恰好会把纪律 1 悄悄废掉，且测试照样全绿。
//
// ⚠️ 每条用例都配了对照实验（把实现改回错误版本，用例确实会判红），
//    否则「测试通过」与「测试根本没在检查」无法区分（key-patterns #38/#39）。

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { SearchIndexCard } from '../components/settings/SearchIndexCard'
import { getSearchStatus, retryFailedVectors, setVectorSearch, syncVectorIndex } from '../api/search'
import { useStore } from '../store'
import type { Capabilities } from '../api/core'
import type { VectorActionResult, VectorStatus } from '../api/search'

vi.mock('../api/search', () => ({
  getSearchStatus: vi.fn(),
  reindexSearch: vi.fn(),
  rebuildVectorIndex: vi.fn(),
  retryFailedVectors: vi.fn(),
  setVectorSearch: vi.fn(),
  syncVectorIndex: vi.fn(),
}))

/** 三档的下发形状。顺序刻意**不是**字典序（后端按声明顺序下发，前端照用）。 */
const RELEASE_CAPS: NonNullable<Capabilities['releasePolicies']> = [
  { key: 'never', label: '常驻内存', hint: '常驻的代价说明' },
  { key: 'idle', label: '闲置后释放', hint: '闲置的代价说明' },
  { key: 'immediate', label: '建完即释放', hint: '即放的代价说明' },
]

/** 两档的下发形状（顺序 = 后端 `TRIGGERS` 声明顺序）。 */
const TRIGGER_CAPS: NonNullable<Capabilities['triggerPolicies']> = [
  { key: 'auto', label: '攒够一批自动建', hint: '攒批的说明' },
  { key: 'manual', label: '只在手动时建', hint: '手动的说明' },
]

function vecStatus(over: Partial<VectorStatus> = {}): VectorStatus {
  return {
    enabled: true,
    reason: null,
    available: true,
    lastError: null,
    identity: { modelId: 'local-x', dimension: 512, chunkSize: 200, schemaVersion: 1, builtAt: '2026-10-26T09:00:00' },
    docs: { total: 3, ok: 3, pending: 0, failed: 0 },
    chunks: 12,
    sizeBytes: 2048,
    model: 'local-bge-small',
    models: [
      { name: 'local-bge-small', label: '本地（免费）', modelId: 'local-x', dimension: 512, local: true, ready: true, reason: null },
      { name: 'dashscope-embedding', label: '云端（按量计费）', modelId: 'text-embedding-v4', dimension: 1024, local: false, ready: true, reason: null },
    ],
    release: 'idle',
    trigger: 'auto',
    threshold: 100,
    ...over,
  } as VectorStatus
}

/** 让 `getSearchStatus` 返回一份带指定向量状态的结果，并等卡渲染出来。 */
async function renderWith(
  vector: VectorStatus,
  caps: Capabilities['releasePolicies'] | undefined,
  trigCaps?: Capabilities['triggerPolicies'],
) {
  vi.mocked(getSearchStatus).mockResolvedValue({
    available: true, docCount: 3, sizeBytes: 2048, lastError: null, rebuiltAt: null, vector,
  })
  useStore.setState({
    capabilities: caps === undefined && trigCaps === undefined ? null : ({
      releasePolicies: caps, triggerPolicies: trigCaps,
    } as unknown as Capabilities),
  })
  vi.mocked(setVectorSearch).mockImplementation(async (enabled, _model, release, trigger, threshold) => ({
    enabled, reason: null, status: vecStatus({
      enabled,
      release: release ?? vector.release,
      trigger: trigger ?? vector.trigger,
      threshold: threshold ?? vector.threshold,
    }),
  }) as VectorActionResult)
  vi.mocked(syncVectorIndex).mockImplementation(async () => ({
    requested: true, enabled: true, reason: null, status: vecStatus(),
  }))
  // 重排后：失败项归零、回到待索引（真实后端就是这个结果）
  vi.mocked(retryFailedVectors).mockImplementation(async () => ({
    requeued: vector.docs.failed, enabled: true, reason: null,
    status: vecStatus({ docs: { ...vector.docs, pending: vector.docs.pending + vector.docs.failed, failed: 0 } }),
  }))

  const view = render(<SearchIndexCard />)
  // 等状态取回：`data-vector-index` 那段只在 `status.vector` 到位后渲染
  await vi.waitFor(() => expect(view.container.querySelector('[data-vector-index]')).toBeTruthy())
  return view
}

function releaseButtons(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-release-option]'))
}

function triggerButtons(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-trigger-option]'))
}

function thresholdInput(container: HTMLElement): HTMLInputElement | null {
  return container.querySelector<HTMLInputElement>('[data-vector-threshold]')
}

describe('SearchIndexCard — 模型内存三档', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useStore.setState({ capabilities: null })
  })

  it('本地引擎 + capabilities 有三档 → 按钮顺序与文案都照后端下发', async () => {
    const { container } = await renderWith(vecStatus(), RELEASE_CAPS)

    const btns = releaseButtons(container)
    expect(btns.map(b => b.textContent)).toEqual(['常驻内存', '闲置后释放', '建完即释放'])
    // 顺序 = 下发顺序（不是字典序，也不是前端自己排的）
    expect(btns.map(b => b.getAttribute('data-release-option'))).toEqual(['never', 'idle', 'immediate'])
  })

  it('capabilities 里没有三档 → 整组不渲染（不拿本地默认表兜底）', async () => {
    // 对照组：同一份状态，只把 `releasePolicies` 拿掉。
    // 若前端偷偷存了一份常量，这条会渲染出三个按钮 → 红。
    const { container } = await renderWith(vecStatus(), undefined)

    expect(container.querySelector('[data-vector-release]')).toBeNull()
    // 其余部分照常渲染 —— 「问不到」不该把整张卡吞掉
    expect(container.querySelector('[data-vector-toggle]')).toBeTruthy()
  })

  it('云端引擎不渲染三档（本机一个字节都不占，摆出来只会误导）', async () => {
    const { container } = await renderWith(
      vecStatus({ model: 'dashscope-embedding' }), RELEASE_CAPS,
    )

    expect(container.querySelector('[data-vector-release]')).toBeNull()
    expect(container.querySelector('[data-vector-index]')).toBeTruthy()
  })

  it('当前档高亮 + 下方说明跟随它；`release` 缺失（老后端）时按 idle 显示', async () => {
    const { container } = await renderWith(
      vecStatus({ release: undefined as unknown as VectorStatus['release'] }), RELEASE_CAPS,
    )

    const btns = releaseButtons(container)
    // 缺失时既不空白也不默认第一项 —— `idle` 是后端常量 DEFAULT_RELEASE
    expect(btns.find(b => b.getAttribute('aria-pressed') === 'true')?.getAttribute('data-release-option'))
      .toBe('idle')
    expect(container.querySelector('[data-vector-release]')?.textContent).toContain('闲置的代价说明')
  })

  it('点另一档 → 带 `release` 发请求（模型名不传，保持当前引擎）', async () => {
    const { container } = await renderWith(vecStatus(), RELEASE_CAPS)

    fireEvent.click(container.querySelector('[data-release-option="immediate"]')!)

    await vi.waitFor(() => expect(setVectorSearch).toHaveBeenCalledTimes(1))
    // ⚠️ 第二个参数必须是 `undefined`：传当前引擎名会走「切引擎」分支，
    // 而那条分支在口径不符时会把索引停掉（云端 1024 ↔ 本地 512）。
    expect(vi.mocked(setVectorSearch).mock.calls[0]).toEqual([true, undefined, 'immediate'])
    // 返回值就地换上：高亮与说明一起变
    await vi.waitFor(() => {
      expect(container.querySelector('[data-release-option="immediate"]')?.getAttribute('aria-pressed')).toBe('true')
      expect(container.querySelector('[data-vector-release]')?.textContent).toContain('即放的代价说明')
    })
  })

  it('点已选中的那一档 → 不发请求（没有要改的东西）', async () => {
    const { container } = await renderWith(vecStatus({ release: 'immediate' }), RELEASE_CAPS)

    fireEvent.click(container.querySelector('[data-release-option="immediate"]')!)

    expect(setVectorSearch).not.toHaveBeenCalled()
  })
})

// ── 建索引时机（2026-10-27）──
//
// 「模型只在重建时加载」是这一组的全部理由：本地 bge-small 加载 3–5 秒、常驻约 90MB，
// 为一两篇反复「载入 → 编码 → 释放」是纯浪费。所以自动档要**攒够阈值**才动手。
describe('SearchIndexCard — 建索引时机两档 + 攒批阈值', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useStore.setState({ capabilities: null })
  })

  it('capabilities 有两档 → 顺序/文案照下发，当前档高亮且说明跟随', async () => {
    const { container } = await renderWith(
      vecStatus({ trigger: 'manual' }), RELEASE_CAPS, TRIGGER_CAPS,
    )

    const btns = triggerButtons(container)
    expect(btns.map(b => b.textContent)).toEqual(['攒够一批自动建', '只在手动时建'])
    expect(btns.map(b => b.getAttribute('data-trigger-option'))).toEqual(['auto', 'manual'])
    expect(container.querySelector('[data-trigger-option="manual"]')?.getAttribute('aria-pressed'))
      .toBe('true')
    expect(container.querySelector('[data-vector-trigger]')?.textContent).toContain('手动的说明')
  })

  it('capabilities 里没有两档 → 整组不渲染（不拿本地默认表兜底）', async () => {
    // 对照组：同一份状态，只把 `triggerPolicies` 拿掉 —— 前端若偷偷存一份常量，这里会红。
    const { container } = await renderWith(vecStatus(), RELEASE_CAPS)

    expect(container.querySelector('[data-vector-trigger]')).toBeNull()
    // 「问不到」不该把整张卡吞掉
    expect(container.querySelector('[data-vector-toggle]')).toBeTruthy()
  })

  it('阈值输入框只在 auto 档渲染', async () => {
    // `manual` 承诺「永不自动建」，所以阈值对它没有任何作用 —— 摆着只会让人以为
    // 「攒够 N 篇还是会自动跑」，而那正是这一档保证不会发生的事。
    const auto = await renderWith(vecStatus({ trigger: 'auto' }), RELEASE_CAPS, TRIGGER_CAPS)
    expect(thresholdInput(auto.container)).toBeTruthy()
    expect(thresholdInput(auto.container)?.value).toBe('100')

    const manual = await renderWith(vecStatus({ trigger: 'manual' }), RELEASE_CAPS, TRIGGER_CAPS)
    expect(thresholdInput(manual.container)).toBeNull()
  })

  it('点另一档 → 只带 `trigger` 发请求（引擎与释放策略都不传，保持当前值）', async () => {
    const { container } = await renderWith(vecStatus({ trigger: 'auto' }), RELEASE_CAPS, TRIGGER_CAPS)

    fireEvent.click(container.querySelector('[data-trigger-option="manual"]')!)

    await vi.waitFor(() => expect(setVectorSearch).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setVectorSearch).mock.calls[0]).toEqual([true, undefined, undefined, 'manual'])
    await vi.waitFor(() => {
      expect(container.querySelector('[data-trigger-option="manual"]')?.getAttribute('aria-pressed'))
        .toBe('true')
    })
  })

  it('改阈值 → 失焦时带 `threshold` 提交一次', async () => {
    const { container } = await renderWith(
      vecStatus({ trigger: 'auto', threshold: 100 }), RELEASE_CAPS, TRIGGER_CAPS,
    )
    const input = thresholdInput(container)!

    fireEvent.change(input, { target: { value: '20' } })
    // ⚠️ **输入过程中一个请求都不发**：受控到后端值会让每敲一位就发一次
    // （后端每次都要跑一遍 `sync_vector_runtime`），而用户还来不及敲第二位。
    expect(setVectorSearch).not.toHaveBeenCalled()

    fireEvent.blur(input)
    await vi.waitFor(() => expect(setVectorSearch).toHaveBeenCalledTimes(1))
    expect(vi.mocked(setVectorSearch).mock.calls[0]).toEqual([true, undefined, undefined, undefined, 20])
  })

  it('阈值填非法值或与当前相同 → 不发请求，回弹到后端值', async () => {
    const { container } = await renderWith(
      vecStatus({ trigger: 'auto', threshold: 100 }), RELEASE_CAPS, TRIGGER_CAPS,
    )
    const input = thresholdInput(container)!

    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.blur(input)
    expect(setVectorSearch).not.toHaveBeenCalled()
    // 回弹（不报错）：用户想改的是「几篇」，敲进一个 0 不是要表达的意思；
    // 而把它当 1 提交会悄悄改掉一个他没打算改的值。
    expect(thresholdInput(container)?.value).toBe('100')

    fireEvent.change(input, { target: { value: '100' } })
    fireEvent.blur(input)
    expect(setVectorSearch).not.toHaveBeenCalled()
  })

  it('「立即建立索引」→ 调 syncVectorIndex（关闭时禁用）', async () => {
    const { container } = await renderWith(vecStatus(), RELEASE_CAPS, TRIGGER_CAPS)
    fireEvent.click(container.querySelector('[data-vector-sync] button')!)
    await vi.waitFor(() => expect(vi.mocked(syncVectorIndex).mock.calls.length).toBe(1))

    // 未开启时这条路后端会 400 `VECTOR_DISABLED` —— 按钮直接禁用，别让用户撞墙。
    const off = await renderWith(vecStatus({ enabled: false }), RELEASE_CAPS, TRIGGER_CAPS)
    const btn = off.container.querySelector<HTMLButtonElement>('[data-vector-sync] button')!
    expect(btn.disabled).toBe(true)
  })

  it('失败项：「重试失败项」只在有失败时出现，点了调 retryFailedVectors', async () => {
    // 无失败项 → 不渲染。这条按钮是「有需要才出现」的出口，常驻只是噪音
    // （且它旁边的「待索引 N 篇」已经承担了常态进度提示）。
    const clean = await renderWith(vecStatus(), RELEASE_CAPS, TRIGGER_CAPS)
    expect(clean.container.querySelector('[data-vector-retry]')).toBeNull()

    const { container } = await renderWith(
      vecStatus({ docs: { total: 5, ok: 3, pending: 0, failed: 2 } }),
      RELEASE_CAPS, TRIGGER_CAPS,
    )
    const btn = container.querySelector<HTMLButtonElement>('[data-vector-retry]')!
    // 数量就在按钮上：用户不必再回上面那行状态里数
    expect(btn.textContent).toContain('2')
    fireEvent.click(btn)
    await vi.waitFor(() => expect(retryFailedVectors).toHaveBeenCalledTimes(1))
    // 动作返回的新状态就地生效 → 没有失败项了，按钮消失
    await vi.waitFor(() => expect(container.querySelector('[data-vector-retry]')).toBeNull())

    // 语义检索没开启时后端会 400 `VECTOR_DISABLED` —— 直接禁用，别让用户撞墙。
    const off = await renderWith(
      vecStatus({ enabled: false, docs: { total: 5, ok: 3, pending: 0, failed: 2 } }),
      RELEASE_CAPS, TRIGGER_CAPS,
    )
    expect(off.container.querySelector<HTMLButtonElement>('[data-vector-retry]')!.disabled).toBe(true)
  })
})
