/**
 * 产物自证条 —— 一横排带图标的小标签（`components/preview/ArtifactMetaBar.tsx`）。
 *
 * 本文件管两件事：
 *   1. **渲染**（每个位一个图标 + 一段文本；无数据时整条不出现）；
 *   2. **接线**（只挂在预览区 notes/polished 的只读态、且流式生成中不显示）。
 *
 * 接线那组是**源码守卫**——判据是「这个组件的接入点必须唯一且受条件约束」，
 * 而它坏掉的方式是**静默的**：多接一处（Excel 数据摘要、原始转录）就是给没有版本
 * 概念的视图挂上「v1 · 0字」这种假信息；漏掉 `!isStreaming` 就是在流式生成时拿
 * **上一版**的元数据配新正文。两种都不会报错、也不会让任何渲染测试变红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render } from '@testing-library/react'
import { ArtifactMetaBar } from '../components/preview/ArtifactMetaBar'
import type { MarkdownMeta, MarkdownVersion } from '../store/types'

const meta = (p: Partial<MarkdownMeta> = {}): MarkdownMeta =>
  ({ id: 'mk_1', style: 'plain', mode: 'notes', version: 3, ...p })

const versions: MarkdownVersion[] = [
  { version: 3, id: 'mk_1', createdAt: '2026-10-11T06:30:00.000Z', mode: 'notes', chars: 2480, source: 'qwen3.8-max' },
]

// ── 渲染 ─────────────────────────

describe('ArtifactMetaBar', () => {
  it('完整数据 → 四个标签齐备，key 与顺序固定', () => {
    const { container } = render(
      <ArtifactMetaBar meta={meta({ chars: 2480, source: 'qwen3.8-max' })} versions={versions} />,
    )
    const keys = [...container.querySelectorAll('[data-meta-chip]')].map(el => el.getAttribute('data-meta-chip'))
    expect(keys).toEqual(['version', 'time', 'chars', 'model'])
    expect(container.querySelector('[data-artifact-meta]')).not.toBeNull()
  })

  it('⚠️ 每个标签各配一个图标（数量恒等 —— 多一个少一个都是错位）', () => {
    const { container } = render(
      <ArtifactMetaBar meta={meta({ chars: 2480, source: 'qwen3.8-max' })} versions={versions} />,
    )
    const chips = container.querySelectorAll('[data-meta-chip]')
    expect(chips).toHaveLength(4)
    for (const chip of chips) {
      expect(chip.querySelector('svg')).not.toBeNull()
    }
  })

  it('文本落在标签里（版本带 v 前缀）', () => {
    const { container } = render(<ArtifactMetaBar meta={meta()} versions={versions} />)
    expect(container.querySelector('[data-meta-chip="version"]')?.textContent).toBe('v3')
    expect(container.querySelector('[data-meta-chip="model"]')?.textContent).toBe('qwen3.8-max')
  })

  it('meta 为 null → 整条不渲染（不留空白容器）', () => {
    const { container } = render(<ArtifactMetaBar meta={null} versions={versions} />)
    expect(container.querySelector('[data-artifact-meta]')).toBeNull()
    expect(container.innerHTML).toBe('')
  })

  it('最小数据 → 只有版本标签，不补位出假信息', () => {
    const { container } = render(
      <ArtifactMetaBar meta={meta({ chars: undefined, source: '' })} versions={[]} />,
    )
    const keys = [...container.querySelectorAll('[data-meta-chip]')].map(el => el.getAttribute('data-meta-chip'))
    expect(keys).toEqual(['version'])
  })

  it('versions 省略时仍能渲染（prop 有默认值）', () => {
    const { container } = render(<ArtifactMetaBar meta={meta()} />)
    expect(container.querySelector('[data-artifact-meta]')).not.toBeNull()
  })
})

// ── 接线守卫 ─────────────────────────

describe('接线（源码守卫）', () => {
  const previewArea = readFileSync(
    resolve(process.cwd(), 'src/components/preview/PreviewArea.tsx'), 'utf-8',
  )
  const markdownPreview = readFileSync(
    resolve(process.cwd(), 'src/components/preview/MarkdownPreview.tsx'), 'utf-8',
  )

  it('⚠️ 接入点唯一 —— 只有一处挂载', () => {
    const hits = previewArea.match(/<ArtifactMetaBar/g) ?? []
    expect(hits).toHaveLength(1)
  })

  it('⚠️ 流式生成中不显示（此刻 markdownMeta 还是上一版的数）', () => {
    const idx = previewArea.indexOf('<ArtifactMetaBar')
    expect(idx).toBeGreaterThan(0)
    // 挂载点前 200 字符内必须有 !isStreaming 条件
    expect(previewArea.slice(Math.max(0, idx - 200), idx)).toContain('!isStreaming')
  })

  it('⚠️ 不放进 MarkdownPreview 内部（那会扩散到 Excel 数据摘要 / 原始转录）', () => {
    expect(markdownPreview).not.toContain('ArtifactMetaBar')
  })
})

// ── 颜色 token 真实存在 ─────────────────────────

/**
 * ⚠️ **Tailwind 对不存在的工具类静默不生成 CSS**——写 `border-border`（真名是
 * `border-border-default`）不会报错、不会让任何渲染测试变红，只是那条规则**根本
 * 不存在**：hover 时边框纹丝不动，而开发者在编辑器里看着类名「挺对的」。
 * 本组是本轮实际踩到后补的（第一版就写错了 `hover:border-border`）。
 */
describe('颜色 token 真实存在', () => {
  const barSrc = readFileSync(resolve(process.cwd(), 'src/components/preview/ArtifactMetaBar.tsx'), 'utf-8')
  const indexCss = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf-8')

  /** 非颜色 token 的标准工具类（对齐 / 换行 / 内建色），它们不查 @theme */
  const STD = new Set([
    'center', 'left', 'right', 'justify', 'wrap', 'nowrap', 'ellipsis',
    'balance', 'pretty', 'transparent', 'current', 'inherit',
  ])

  it('组件里每个 border-/text-/bg- 都指向 @theme 定义的颜色', () => {
    const used = new Set<string>()
    for (const m of barSrc.matchAll(/\b(?:border|text|bg)-([a-z][a-z-]*)\b/g)) used.add(m[1])
    // 前提断言：正则确实抽到了东西（抽不到就全绿了，是假绿）
    expect(used.size).toBeGreaterThan(2)
    const missing = [...used].filter(n => !STD.has(n) && !indexCss.includes(`--color-${n}:`))
    expect(missing).toEqual([])
  })

  it('对照：`border-border` 这种写法会被抓出来（token 名少了 `-default`）', () => {
    // 直接以「写错的那个字面量」跑一遍同一判据，证明它有区分力
    const bad = 'hover:border-border'
    const used = new Set<string>()
    for (const m of bad.matchAll(/\b(?:border|text|bg)-([a-z][a-z-]*)\b/g)) used.add(m[1])
    const missing = [...used].filter(n => !STD.has(n) && !indexCss.includes(`--color-${n}:`))
    expect(missing).toEqual(['border'])
  })
})
