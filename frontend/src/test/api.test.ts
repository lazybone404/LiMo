import { describe, it, expect } from 'vitest'
import { apiUrl } from '../api/client'

describe('apiUrl', () => {
  it('returns relative path in browser mode', () => {
    // jsdom environment: __TAURI_INTERNALS__ not set → browser mode
    expect(apiUrl('/config')).toBe('/api/config')
  })

  it('prepends /api to paths starting with /', () => {
    expect(apiUrl('/chat')).toBe('/api/chat')
  })

  it('returns full URLs unchanged', () => {
    expect(apiUrl('http://example.com/test')).toBe('http://example.com/test')
    expect(apiUrl('https://api.openai.com/v1/models')).toBe('https://api.openai.com/v1/models')
  })

  it('handles paths without leading slash', () => {
    // This is a potential edge case — the function behavior depends on BASE
    const result = apiUrl('health')
    expect(result).toBe('/apihealth') // prepended to BASE without separator
  })
})
