/**
 * VersionSwitcher — 产物版本下拉。
 *
 * ⚠️ 断言的是**信息是否真的到了用户眼前**，不是「组件渲染了」：版本列表的全部价值
 * 在「时间 / 字数 / 来源 / 编辑标记」四列（用户 2026-10-10 点名要看的），任何一列
 * 静默消失（字段名对不上、`undefined` 被吞掉）都不会报错，只是那一格空白。
 *
 * 查询走 `container.querySelector('[data-…]')`（与 `docLibraryFilters.test.tsx` 同款）
 * ——组件挂的是 `data-version-*` 而非 `data-testid`。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { VersionSwitcher } from '../components/preview/VersionSwitcher'
import type { MarkdownVersion } from '../store/types'

function v(n: number, extra: Partial<MarkdownVersion> = {}): MarkdownVersion {
  return {
    version: n, id: `md${n}`, createdAt: '2026-10-10T13:05:00',
    status: 'complete', mode: 'notes', chars: 2400, source: 'qwen3.8-max',
    ...extra,
  }
}

const toggle = (c: HTMLElement) =>
  c.querySelector('[data-version-switcher-toggle]') as HTMLElement | null
const menu = (c: HTMLElement) => c.querySelector('[data-version-switcher]')
const row = (c: HTMLElement, n: number) =>
  c.querySelector(`[data-version-row="${n}"]`) as HTMLElement | null

function renderSW(props: Partial<Parameters<typeof VersionSwitcher>[0]> = {}) {
  const r = render(
    <VersionSwitcher
      mode="notes"
      versions={[v(2), v(1)]}
      activeMeta={{ mode: 'notes', version: 2 }}
      loading={false}
      onSelect={() => {}}
      {...props}
    />,
  )
  return r
}

function openMenu(c: HTMLElement) {
  fireEvent.click(toggle(c)!)
  const m = menu(c)
  expect(m, '面板没展开').toBeTruthy()
  return m as HTMLElement
}

describe('VersionSwitcher', () => {
  it('只有一版时整块不渲染（切换没有对象）', () => {
    const { container } = renderSW({ versions: [v(1)] })
    expect(toggle(container)).toBeNull()
  })

  it('触发按钮显示当前版与字数', () => {
    const { container } = renderSW()
    const txt = toggle(container)!.textContent || ''
    expect(txt).toContain('v2')
    expect(txt).toContain('2.4k字')
  })

  it('展开后每行含版本号 / 时间 / 字数 / 来源', () => {
    const { container } = renderSW()
    openMenu(container)
    const txt = row(container, 1)!.textContent || ''
    expect(txt).toContain('v1')             // 版本号
    expect(txt).toContain('2.4k字')          // 字数
    expect(txt).toContain('qwen3.8-max')     // 来源（模型名）
    expect(txt).toContain('2026')            // 本地化时间（具体格式交给 Intl）
  })

  it('「已编辑」徽标只出现在被编辑过的那一版', () => {
    const { container } = renderSW({ versions: [v(2), v(1, { edited: true })] })
    openMenu(container)
    expect(row(container, 1)!.textContent).toContain('已编辑')
    expect(row(container, 2)!.textContent).not.toContain('已编辑')
  })

  it('后端没给字数时不显示「0字」（undefined ≠ 0）', () => {
    const { container } = renderSW({
      versions: [v(2), v(1, { chars: undefined })],
      activeMeta: { mode: 'notes', version: 1 },
    })
    const txt = toggle(container)!.textContent || ''
    expect(txt).toContain('v1')
    expect(txt).not.toContain('字')
  })

  it('0 字与「没给」不同 —— 真 0 字要显示出来', () => {
    const { container } = renderSW({
      versions: [v(2, { chars: 0 }), v(1)],
      activeMeta: { mode: 'notes', version: 2 },
    })
    expect(toggle(container)!.textContent).toContain('0字')
  })

  it('当前行高亮（data-active），其余行不高亮', () => {
    const { container } = renderSW()
    openMenu(container)
    expect(row(container, 2)!.getAttribute('data-active')).toBe('true')
    expect(row(container, 1)!.getAttribute('data-active')).toBeNull()
  })

  it('点击行 = 切换该版并收起面板', () => {
    const onSelect = vi.fn()
    const { container } = renderSW({ onSelect })
    openMenu(container)
    fireEvent.click(row(container, 1)!)
    expect(onSelect).toHaveBeenCalledWith(1)
    expect(menu(container)).toBeNull()
  })

  it('只看当前 mode 的版本（两个 mode 混在一个列表里）', () => {
    const { container } = renderSW({
      versions: [v(2), v(1), { ...v(3), mode: 'polished', id: 'p3' }],
    })
    openMenu(container)
    expect(row(container, 1)).toBeTruthy()
    expect(row(container, 2)).toBeTruthy()
    expect(row(container, 3)).toBeNull()
  })

  it('activeMeta 属于别的 mode 时，高亮兜底到最新版', () => {
    // ⚠️ 2026-10-10 改版后这**不该**再发生——`PreviewArea` 的同步 effect 会在切 tab 时
    // 把 `markdownMeta` 换成该 mode 自己的（见 `previewVersionSync.test.ts`）。
    // 但组件**必须容忍**它：入参不由它保证，高亮必须落在最新版上，否则会出现
    // 「一行都没高亮」而用户不知道自己看的是哪版。
    const { container } = renderSW({ activeMeta: { mode: 'polished', version: 7 } })
    openMenu(container)
    expect(row(container, 2)!.getAttribute('data-active')).toBe('true')
  })

  it('loading 时触发按钮禁用（防切换请求打架）', () => {
    const { container } = renderSW({ loading: true })
    expect(toggle(container)!.hasAttribute('disabled')).toBe(true)
  })
})
