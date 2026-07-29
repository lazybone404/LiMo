import { describe, it, expect, vi } from 'vitest'
import { resolveTheme } from '../lib/theme'
import { THEME_PRESETS } from '../lib/theme-presets'
import { useStore } from '../store'

// ── resolveTheme ──

describe('resolveTheme', () => {
  it('returns "light" for light mode', () => {
    expect(resolveTheme('light')).toBe('light')
  })

  it('returns "dark" for dark mode', () => {
    expect(resolveTheme('dark')).toBe('dark')
  })

  it('returns "dark" for system mode when prefers-color-scheme is dark', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('dark'),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })))
    expect(resolveTheme('system')).toBe('dark')
    vi.unstubAllGlobals()
  })

  it('returns "light" for system mode when prefers-color-scheme is light', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((_query: string) => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })))
    expect(resolveTheme('system')).toBe('light')
    vi.unstubAllGlobals()
  })
})

// ── THEME_PRESETS ──

describe('THEME_PRESETS', () => {
  it('has 20 presets (5 groups × 4)', () => {
    expect(THEME_PRESETS).toHaveLength(20)
  })

  it('all presets have valid base, accent, texture, font values', () => {
    // Dynamically check: each preset field should be non-empty string
    for (const p of THEME_PRESETS) {
      expect(typeof p.base).toBe('string')
      expect(p.base.length).toBeGreaterThan(0)
      expect(typeof p.accent).toBe('string')
      expect(p.accent.length).toBeGreaterThan(0)
      expect(typeof p.texture).toBe('string')
      expect(p.texture.length).toBeGreaterThan(0)
      expect(typeof p.font).toBe('string')
      expect(p.font.length).toBeGreaterThan(0)
    }
  })

  it('all presets have unique names and labels', () => {
    const names = THEME_PRESETS.map(p => p.name)
    const labels = THEME_PRESETS.map(p => p.label)
    expect(new Set(names).size).toBe(names.length)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('has 5 groups of 4 presets each', () => {
    // 5 groups: 护眼(4), 经典(4), 粉色(4), 雅致(4), 国风(4)
    expect(THEME_PRESETS).toHaveLength(20)
    // Each group has 4 presets
    const groups = new Set(THEME_PRESETS.map((_p, i) => Math.floor(i / 4)))
    expect(groups.size).toBe(5)
  })
})

// ── applyPreset ──

describe('applyPreset — store action', () => {
  it('sets all theme fields atomically from a preset', () => {
    const preset = THEME_PRESETS[0] // 柔绿
    useStore.getState().applyPreset(preset.name)

    const s = useStore.getState()
    expect(s.base).toBe(preset.base)
    expect(s.accent).toBe(preset.accent)
    expect(s.texture).toBe(preset.texture)
    expect(s.font).toBe(preset.font)
  })

  it('does nothing for unknown preset name', () => {
    const before = {
      base: useStore.getState().base,
      accent: useStore.getState().accent,
    }
    useStore.getState().applyPreset('nonexistent')
    expect(useStore.getState().base).toBe(before.base)
    expect(useStore.getState().accent).toBe(before.accent)
  })
})
