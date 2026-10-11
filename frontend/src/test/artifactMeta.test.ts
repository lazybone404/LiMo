/**
 * 产物元数据的**展示判据** —— `buildMetaChips` / `fmtTime` / `fmtChars`。
 *
 * 这组的价值全在「哪些字段该出现、按什么顺序、拿哪一行」这三件事上，而它们
 * **全都不会报错**：漏一个字段只是那个标签不显示，取错一行只是时间显示成另一个
 * mode 的——两边都是合法日期，屏幕上看起来毫无异常。
 *
 * 对照实验（每条对应一个「看起来对、其实错」的实现）：
 *   A. `chars` 缺失时补 `?? 0`（「反正都是 0」）→ 旧产物显示「0字」，**伪造数据** → 红。
 *   B. 版本行只按 `version` 匹配、不带 `mode` → 笔记 v2 显示成**整理版 v2** 的时间/字数
 *      （两者都从 v1 起编号，同号必然同时存在）→ 红。
 *   C. chips 顺序改成「先有的先放」→ 不同产物上标签位置乱跳，读的人每次都要重新认
 *      （`design/cards.md` B 比较卡：「多张卡片应尽量使用相同的维度和顺序」）→ 红。
 *   D. 给元数据条也加一个「已编辑」标签 → 与正上方工具条的「已手动编辑」相邻两行
 *      说同一件事（信息量没多，噪音多一倍）→ 红。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { buildMetaChips, fmtChars, fmtTime } from '../lib/artifactMeta'
import type { MarkdownMeta, MarkdownVersion } from '../store/types'

const meta = (p: Partial<MarkdownMeta> = {}): MarkdownMeta =>
  ({ id: 'mk_1', style: 'plain', mode: 'notes', version: 1, ...p })

const ver = (p: Partial<MarkdownVersion> = {}): MarkdownVersion =>
  ({ version: 1, id: 'mk_1', createdAt: '2026-10-11T06:30:00.000Z', mode: 'notes', ...p })

const keysOf = (m: MarkdownMeta | null, vs: MarkdownVersion[] = [ver()]) =>
  buildMetaChips(m, vs).map(c => c.key)

// ── 字段取舍：缺就不给，绝不补位 ─────────────────────────

describe('字段取舍', () => {
  it('meta 为 null → 没有任何标签（空态不渲染半条）', () => {
    expect(buildMetaChips(null)).toEqual([])
    expect(buildMetaChips(undefined)).toEqual([])
  })

  it('version 恒在场 —— 它是产物的身份，唯一无条件项', () => {
    expect(keysOf(meta())).toContain('version')
    // 连时间/字数/模型全缺时，version 仍在
    expect(keysOf(meta({ chars: undefined }), [ver({ createdAt: '', chars: undefined })])).toEqual(['version'])
  })

  it('⚠️ chars 缺失（undefined）→ 不给这个标签', () => {
    // 对照 A：写成 `meta.chars ?? row?.chars ?? 0` 会渲染出「0字」
    expect(keysOf(meta({ chars: undefined }), [ver({ chars: undefined })])).not.toContain('chars')
  })

  it('⚠️ chars === 0 → 给「0字」（与上一条成对：0 是真的 0）', () => {
    const chips = buildMetaChips(meta({ chars: 0 }), [ver({ chars: undefined })])
    expect(chips.find(c => c.key === 'chars')?.label).toBe('0字')
  })

  it('时间 / 模型缺失 → 不给对应标签（历史产物可能没有）', () => {
    const keys = keysOf(meta({ source: '' }), [ver({ createdAt: '', source: undefined })])
    expect(keys).not.toContain('time')
    expect(keys).not.toContain('model')
  })

  it('⚠️ 不产出「已编辑」标签 —— 同屏工具条已承担该信息', () => {
    // 对照 D：加回来就与「已手动编辑」相邻两行重复
    expect(keysOf(meta({ edited: true }))).not.toContain('edited')
  })
})

// ── 版本行匹配：必须双键 ─────────────────────────

describe('版本行匹配', () => {
  it('⚠️ 按 (version, mode) 双键匹配，不只看 version', () => {
    // notes / polished **各自从 v1 起**，同 version 必然同时存在
    const versions = [
      ver({ version: 2, mode: 'notes', createdAt: '2026-01-01T00:00:00Z', chars: 100 }),
      ver({ version: 2, mode: 'polished', createdAt: '2026-02-02T00:00:00Z', chars: 200 }),
    ]
    // 对照 B：只按 version 会命中 notes 那条（100字）
    const chips = buildMetaChips(meta({ mode: 'polished', version: 2 }), versions)
    expect(chips.find(c => c.key === 'chars')?.label).toBe('200字')
  })

  it('mode 字段缺失的历史条目 → 退回「同 version 的第一行」而不是不显示', () => {
    const chips = buildMetaChips(meta({ mode: 'notes', version: 1 }), [ver({ mode: undefined, chars: 42 })])
    expect(chips.find(c => c.key === 'chars')?.label).toBe('42字')
  })

  it('versions 里根本没有这一版 → 仍给 version 标签，其余静默缺席', () => {
    expect(keysOf(meta({ version: 9, chars: undefined }), [ver({ version: 1 })])).toEqual(['version'])
  })

  it('meta 自带 chars/source 时优先于版本行', () => {
    const chips = buildMetaChips(
      meta({ chars: 5, source: 'qwen3.8-max' }),
      [ver({ chars: 999, source: 'other-model' })],
    )
    expect(chips.find(c => c.key === 'chars')?.label).toBe('5字')
    expect(chips.find(c => c.key === 'model')?.label).toBe('qwen3.8-max')
  })
})

// ── 顺序 ─────────────────────────

describe('顺序', () => {
  it('恒为 version → time → chars → model', () => {
    // 对照 C：顺序随字段有无而变，读的人每次要重新认位置
    const keys = keysOf(meta({ chars: 2480, source: 'qwen3.8-max' }))
    expect(keys).toEqual(['version', 'time', 'chars', 'model'])
  })

  it('缺中间项时，其余项的相对顺序不变', () => {
    const keys = keysOf(meta({ chars: 2480, source: '' }), [ver({ createdAt: '' })])
    expect(keys).toEqual(['version', 'chars'])
  })
})

// ── 格式化函数（两个消费方共用） ─────────────────────────

describe('fmtChars', () => {
  it('undefined → 空串（不显示），0 → 「0字」（显示）', () => {
    expect(fmtChars(undefined)).toBe('')
    expect(fmtChars(0)).toBe('0字')
  })

  it('进位边界', () => {
    expect(fmtChars(999)).toBe('999字')
    expect(fmtChars(1000)).toBe('1.0k字')
    expect(fmtChars(2480)).toBe('2.5k字')
  })
})

describe('fmtTime', () => {
  it('空串 / 非法日期 → 空串（不渲染 Invalid Date）', () => {
    expect(fmtTime('')).toBe('')
    expect(fmtTime('not-a-date')).toBe('')
  })

  it('合法 ISO → 本地化到分钟（不断言具体文本，它随时区变）', () => {
    const out = fmtTime('2026-10-11T06:30:00.000Z')
    expect(out).not.toBe('')
    expect(out).toContain('2026')
    expect(out).toMatch(/\d{2}:\d{2}/)
  })
})

// ── 同源守卫：不含第二份实现 ─────────────────────────

describe('同源', () => {
  /** ⚠️ 用 process.cwd() 而非 import.meta.url：本文件跑在 jsdom 环境（同 markerPrefs.test.ts） */
  const switcher = readFileSync(resolve(process.cwd(), 'src/components/preview/VersionSwitcher.tsx'), 'utf-8')

  it('VersionSwitcher 从本模块取格式化函数（不各写一份）', () => {
    expect(switcher).toContain("from '../../lib/artifactMeta'")
    expect(switcher).toMatch(/import\s*\{[^}]*fmtChars[^}]*\}\s*from/)
  })

  it('⚠️ VersionSwitcher 内不得再出现私有的 fmtTime/fmtChars 实现', () => {
    // 有实现 = 两份判据并存，时间格式或进位会慢慢漂移，且**没有任何报错**
    expect(switcher).not.toMatch(/function\s+fmt(Time|Chars)\s*\(/)
  })
})
