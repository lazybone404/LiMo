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
import { getSearchStatus, setVectorSearch } from '../api/search'
import { useStore } from '../store'
import type { Capabilities } from '../api/core'
import type { VectorActionResult, VectorStatus } from '../api/search'

vi.mock('../api/search', () => ({
  getSearchStatus: vi.fn(),
  reindexSearch: vi.fn(),
  rebuildVectorIndex: vi.fn(),
  setVectorSearch: vi.fn(),
}))

/** 三档的下发形状。顺序刻意**不是**字典序（后端按声明顺序下发，前端照用）。 */
const RELEASE_CAPS: NonNullable<Capabilities['releasePolicies']> = [
  { key: 'never', label: '常驻内存', hint: '常驻的代价说明' },
  { key: 'idle', label: '闲置后释放', hint: '闲置的代价说明' },
  { key: 'immediate', label: '建完即释放', hint: '即放的代价说明' },
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
    ...over,
  } as VectorStatus
}

/** 让 `getSearchStatus` 返回一份带指定向量状态的结果，并等卡渲染出来。 */
async function renderWith(vector: VectorStatus, caps: Capabilities['releasePolicies'] | undefined) {
  vi.mocked(getSearchStatus).mockResolvedValue({
    available: true, docCount: 3, sizeBytes: 2048, lastError: null, rebuiltAt: null, vector,
  })
  useStore.setState({
    capabilities: caps === undefined ? null : ({ releasePolicies: caps } as unknown as Capabilities),
  })
  vi.mocked(setVectorSearch).mockImplementation(async (enabled, _model, release) => ({
    enabled, reason: null, status: vecStatus({ enabled, release: release ?? vector.release }),
  }) as VectorActionResult)

  const view = render(<SearchIndexCard />)
  // 等状态取回：`data-vector-index` 那段只在 `status.vector` 到位后渲染
  await vi.waitFor(() => expect(view.container.querySelector('[data-vector-index]')).toBeTruthy())
  return view
}

function releaseButtons(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-release-option]'))
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
