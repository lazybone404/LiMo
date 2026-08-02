/**
 * 插件管理域 — 列表 / 启停 / 卸载 / 配置（EnginePlugin v1）。
 */
import { apiFetch, BASE, type ConfigField } from './core'

export interface PluginInfo {
  name: string
  namespace: string
  category: string
  displayName: string
  userDesc: string
  license: string
  fileTypes: string[]
  configSchema: ConfigField[]
  path: string
  enabled: boolean
  /** 运行时是否已注册进引擎表（仅启动时决定）；enabled !== loaded → 需重启生效 */
  loaded: boolean
  /** enabled !== loaded：开关已改但引擎未注册/未注销，需重启后端 */
  pendingRestart: boolean
  config: Record<string, unknown>
  reason?: string
  execMode: string | null
}

export interface PluginListResponse {
  plugins: PluginInfo[]
}

/** 插件列表（含禁用/门控拒绝的，按 category+name 排序） */
export async function listPlugins(): Promise<PluginInfo[]> {
  const r = await apiFetch(`${BASE}/plugins`)
  const data = (await r.json()) as PluginListResponse
  return data.plugins
}

/** 启用/禁用插件 — 写 plugin_states，重启后生效 */
export async function setPluginEnabled(name: string, enabled: boolean): Promise<{ restart_required: boolean }> {
  const r = await apiFetch(`${BASE}/plugins/${encodeURIComponent(name)}/${enabled ? 'enable' : 'disable'}`, {
    method: 'POST',
  })
  return r.json()
}

/** 卸载插件 — 删 ~/.limo/plugins/ 下声明文件 + 同名目录（含 venv/模型） */
export async function deletePlugin(name: string): Promise<{ removed: string[] }> {
  const r = await apiFetch(`${BASE}/plugins/${encodeURIComponent(name)}`, { method: 'DELETE' })
  return r.json()
}

/** 保存插件配置 — 落盘 + 内存即时生效（下次执行 stdin 即含新 config） */
export async function savePluginConfig(name: string, config: Record<string, unknown>): Promise<{ status: string }> {
  const r = await apiFetch(`${BASE}/plugins/${encodeURIComponent(name)}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  return r.json()
}

/** 重启后端 — 生产 Tauri 调 Rust `restart_backend_sidecar`；浏览器 dev 模式不可用（抛错） */
export async function restartBackend(): Promise<void> {
  if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('restart_backend_sidecar')
  } else {
    throw new Error('开发模式无法重启后端，请手动重启 uvicorn 进程')
  }
}
