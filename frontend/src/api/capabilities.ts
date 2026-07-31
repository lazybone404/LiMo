/**
 * capabilities 域 — 后端能力元数据。
 */
import { apiFetch, BASE, type Capabilities } from './core'

export async function fetchCapabilities(): Promise<Capabilities> {
  const r = await apiFetch(`${BASE}/capabilities`)
  return r.json()
}
