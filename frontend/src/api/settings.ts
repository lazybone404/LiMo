/**
 * settings 域 — 配置 / LLM 模型管理 / 引擎管理。
 */
import { apiFetch, BASE } from './core'

// -- config --

export async function getConfig() {
  const r = await apiFetch(`${BASE}/config`)
  return r.json()
}

export async function updateConfig(data: Record<string, any>) {
  const r = await apiFetch(`${BASE}/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function detectModels(provider: string, apiKey: string): Promise<{
  models?: { id: string; name: string; desc: string }[]
  error?: string
}> {
  const r = await apiFetch(`${BASE}/llm/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, api_key: apiKey }),
  })
  return r.json()
}

// -- model management (BiliNote style) --

export interface Provider {
  id: string; name: string; api_key: string; base_url: string
  type: string; enabled: number
}

export interface EnabledModel {
  id: number; provider_id: string; model_name: string
  capabilities?: string[]  // ["text", "image"]
  context_window?: number
}

export interface SceneModels {
  generate: number | null; chat: number | null; image_process: number | null
}

export async function listProviders(): Promise<Provider[]> {
  const r = await apiFetch(`${BASE}/providers`)
  return r.json()
}

export async function updateProvider(id: string, data: Partial<Provider>) {
  const r = await apiFetch(`${BASE}/providers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function addProvider(data: { name: string; api_key?: string; base_url?: string; type?: string }) {
  const r = await apiFetch(`${BASE}/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function deleteProvider(id: string) {
  const r = await apiFetch(`${BASE}/providers/${id}`, { method: 'DELETE' })
  return r.json()
}

export async function testProviderConnection(id: string) {
  const r = await apiFetch(`${BASE}/providers/${id}/test`, { method: 'POST' })
  return r.json()
}

export async function listEnabledModels(): Promise<EnabledModel[]> {
  const r = await apiFetch(`${BASE}/models`)
  return r.json()
}

export async function refreshModels(providerId: string): Promise<{
  models?: { id: string; owned_by: string; desc?: string }[]; error?: string
}> {
  const r = await apiFetch(`${BASE}/models/refresh/${providerId}`)
  return r.json()
}

export async function addModel(providerId: string, modelName: string, contextWindow?: number) {
  const r = await apiFetch(`${BASE}/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider_id: providerId, model_name: modelName,
      ...(contextWindow ? { context_window: contextWindow } : {}) }),
  })
  return r.json()
}

export async function deleteModel(modelId: number) {
  const r = await apiFetch(`${BASE}/models/${modelId}`, { method: 'DELETE' })
  return r.json()
}

export async function updateModelCapabilities(modelId: number, capabilities: string[]) {
  const r = await apiFetch(`${BASE}/models/${modelId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ capabilities }),
  })
  return r.json()
}

export async function getSceneModels(): Promise<SceneModels> {
  const r = await apiFetch(`${BASE}/scene-models`)
  return r.json()
}

export async function setSceneModels(data: SceneModels) {
  const r = await apiFetch(`${BASE}/scene-models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

// -- engine management --

/** 引擎厂商管理 API 的完整模型 */
export interface EngineProviderConfig {
  id: string; name: string; type: string; api_key: string
  base_url: string; capabilities: string[]
}
export interface EngineModel {
  id: number; provider_id: string; model_name: string; category: string
  capabilities?: string[]; desc?: string
}

export async function listEngineProviders(): Promise<EngineProviderConfig[]> {
  return (await apiFetch(`${BASE}/engine-providers`)).json()
}
export async function addEngineProvider(data: { name: string; api_key?: string; base_url?: string; type?: string }) {
  return (await apiFetch(`${BASE}/engine-providers`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })).json()
}
export async function deleteEngineProvider(id: string) {
  return (await apiFetch(`${BASE}/engine-providers/${id}`, { method: 'DELETE' })).json()
}
export async function listEngineModels(): Promise<EngineModel[]> {
  return (await apiFetch(`${BASE}/engine-models`)).json()
}
export async function deleteEngineModel(id: number) {
  return (await apiFetch(`${BASE}/engine-models/${id}`, { method: 'DELETE' })).json()
}

export async function getEngineBindings(): Promise<Record<string, number | null>> {
  return (await apiFetch(`${BASE}/engine-bindings`)).json()
}
export async function saveEngineBindings(data: Record<string, number | null | any[]>) {
  return (await apiFetch(`${BASE}/engine-bindings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bindings: data }) })).json()
}

/** 引擎注册表 — 所有已注册引擎按 category 分组 */
export async function getEngineRegistry(): Promise<Record<string, string[]>> {
  return (await apiFetch(`${BASE}/engine-registry`)).json()
}

/** 引擎自动降级开关 */
export async function getEngineAutoFallback(): Promise<{ enabled: boolean }> {
  return (await apiFetch(`${BASE}/engine-auto-fallback`)).json()
}
export async function setEngineAutoFallback(enabled: boolean): Promise<{ status: string; enabled: boolean }> {
  return (await apiFetch(`${BASE}/engine-auto-fallback`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) })).json()
}
