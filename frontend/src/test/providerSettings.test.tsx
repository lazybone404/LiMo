// ProviderSettings — 模型下线标记（retired 的消费端）
//
// 背景：后端 config.py 的 llm_models 预设里，官方已下架的模型带 `retired` +
// `retired_note`（`_SYNC_OPTIONAL_FLAGS` 保证成对下发）。此前**前端零消费**——
// 用户在一份官方已下架的模型上点「启用」，界面上没有任何异样。
//
// ⚠️ 每个用例都配了对照实验（把实现改回错误版本，用例确实会判红），
//    否则「测试通过」与「测试根本没在检查」无法区分（key-patterns #38/#39）。

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { ProviderSettings } from '../components/settings/ProviderSettings'
import { useStore } from '../store'
import type { Capabilities } from '../api/core'

const enabledRows: unknown[] = []

vi.mock('../api/client', () => ({
  listProviders: async () => [{ id: 'deepseek', name: 'DeepSeek', type: 'built-in', enabled: 1, api_key: '' }],
  listEnabledModels: async () => enabledRows,
  getSceneModels: async () => ({ generate: null, chat: null, image_process: null }),
  listEngineProviders: async () => [],
  listEngineModels: async () => [],
  getEngineBindings: async () => ({}),
  getEngineRegistry: async () => ({}),
  updateProvider: async () => ({}),
  addProvider: async () => ({}),
  deleteProvider: async () => ({}),
  testProviderConnection: async () => ({ ok: true }),
  refreshModels: async () => ({ models: [] }),
  addModel: async () => ({}),
  deleteModel: async () => ({}),
  updateModel: async () => ({}),
  setSceneModels: async () => ({}),
  getConfig: async () => ({}),
  updateConfig: async () => ({}),
  addEngineProvider: async () => ({}),
  deleteEngineProvider: async () => ({}),
  deleteEngineModel: async () => ({}),
  saveEngineBindings: async () => ({}),
  apiUrl: (p: string) => p,
  apiFetch: async () => ({}),
}))

describe('ProviderSettings — 模型下线标记（retired 消费端）', () => {
  beforeEach(() => {
    enabledRows.length = 0
    useStore.setState({ capabilities: null })
  })

  it('已启用但官方已下线的模型 → 行上标出「已下线 · 建议改用 X」', async () => {
    // 「已启用」是最该提醒的场景：用户正在用它。
    enabledRows.push({ id: 1, provider_id: 'deepseek', model_name: 'deepseek-v4-flash' })
    useStore.setState({
      capabilities: {
        llmModels: {
          deepseek: [{
            id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash', desc: '', context_window: 1000000,
            retired: '2026-10-02', retired_note: '已下架，建议改用 DeepSeek V5',
          }],
        },
      } as unknown as Capabilities,
    })

    const { container } = render(<ProviderSettings />)
    const badge = await vi.waitFor(() => {
      const el = container.querySelector('[data-model-retired]')
      expect(el).toBeTruthy()
      return el!
    })
    expect(badge.textContent).toBe('已下线 · 已下架，建议改用 DeepSeek V5')
    // 已启用状态同时可见 —— 标记是**叠加**在正常行上，不是替换
    expect(container.textContent).toContain('已启用')
  })

  it('正常模型不受影响（标记不出现）', async () => {
    enabledRows.push({ id: 1, provider_id: 'deepseek', model_name: 'deepseek-flash' })
    useStore.setState({
      capabilities: {
        llmModels: {
          deepseek: [{ id: 'deepseek-flash', name: 'DeepSeek Flash', desc: '', context_window: 1000000 }],
        },
      } as unknown as Capabilities,
    })

    const { container } = render(<ProviderSettings />)
    await vi.waitFor(() => expect(container.textContent).toContain('deepseek-flash'))
    expect(container.querySelector('[data-model-retired]')).toBeNull()
  })

  it('对照：数据源若用 refreshResult（厂商 API 裸条目）而非 caps.llmModels → 标记消失', async () => {
    // 这条钉住的是一个**设计选择**：refreshResult 走通厂商 API 时是 `{id, owned_by}`，
    // **不含** retired；只有 API 拉取失败回退预设时才带。若把 presetOf 改成查 refreshResult
    // （它也带 id/name 字段，看起来完全可行），则在「API 正常 + 模型恰好已下线」这个
    // 最常见组合下标记会**静默消失**——而这里 enabledRows 非空、refreshResult 为空，
    // 正是那个组合的等价形态。
    //
    // 对照实现：const presetOf = (name) => (refreshResult as any[]).find(p => p.id === name)
    // → retiredTip 恒为 '' → 本断言红。
    enabledRows.push({ id: 1, provider_id: 'deepseek', model_name: 'deepseek-v4-flash' })
    useStore.setState({
      capabilities: {
        llmModels: {
          deepseek: [{
            id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash', desc: '', context_window: 1000000,
            retired: '2026-10-02', retired_note: '已下架，建议改用 DeepSeek V5',
          }],
        },
      } as unknown as Capabilities,
    })

    const { container } = render(<ProviderSettings />)
    // 未点「刷新」→ refreshResult 为空 → 只有 caps 这一条路能拿到 retired
    const badge = await vi.waitFor(() => {
      const el = container.querySelector('[data-model-retired]')
      expect(el).toBeTruthy()
      return el!
    })
    expect(badge.textContent).toContain('建议改用')
  })
})
