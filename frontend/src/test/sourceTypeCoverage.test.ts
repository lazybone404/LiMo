/**
 * `sourceType` 的前后端覆盖守卫（P1-13）。
 *
 * 后端 `core/types.py::SourceType` 的取值集是**真相源**，前端的
 * `docMeta.SOURCE_FILTERS` 是它在文档库 UI 上的投影。两者此前**漏了同一个值**
 * （`audio`）：后端有这一类，前端没有筛选项、没有 `TYPE_COLORS` 项——于是音频文档
 * 既筛不出来、标签也取不到（`SRC_LABEL` 从 filter 表派生，跟着一起缺）。
 *
 * ⚠️ 这类「同一集合写在两种语言里」的漂移**不会报错**，只表现为某个类型的文档
 * 「看起来不存在」（模式 #54：同源是搜索题不是记忆题；#65：跨语言时只能靠机器核对）。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { SOURCE_FILTERS, SRC_LABEL, TYPE_COLORS } from '../components/doc-library/docMeta'

const TYPES_PY = readFileSync(
  resolve(process.cwd(), '../backend/app/core/types.py'),
  'utf-8',
)

/** 从 `class SourceType(StrEnum):` 块里解析 `NAME = "value"` 的 value 集。 */
function backendSourceTypes(): string[] {
  const block =
    TYPES_PY.split('class SourceType(StrEnum):')[1]?.split('\nclass ')[0] ?? ''
  return [...block.matchAll(/^\s+[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"/gm)].map((m) => m[1])
}

describe('sourceType 前后端覆盖', () => {
  it('守卫自证：真的解析到了后端的枚举值（含 audio / composite）', () => {
    const types = backendSourceTypes()
    // 解析失败的表现是「空数组 == 空数组」的假绿灯 —— 先自证能解析出东西（模式 #38）
    expect(types.length).toBeGreaterThanOrEqual(10)
    expect(types).toContain('audio')
    expect(types).toContain('composite')
  })

  it('前端 SOURCE_FILTERS 与后端 SourceType **集合相等**', () => {
    const frontend = SOURCE_FILTERS.map((t) => t.key).filter(Boolean).sort()
    const backend = backendSourceTypes().sort()
    expect(frontend, '前端筛选项与后端类型的差集').toEqual(backend)
  })

  it('每个筛选项都有标签与配色（缺项会静默 fallback 成灰色 text）', () => {
    for (const t of SOURCE_FILTERS.filter((t) => t.key)) {
      expect(SRC_LABEL[t.key], `${t.key} 缺 SRC_LABEL（从 filter 表派生，通常一起缺）`).toBeTruthy()
      expect(TYPE_COLORS[t.key], `${t.key} 缺 TYPE_COLORS 项 —— 会 fallback 到 text 的灰色`).toBeTruthy()
    }
  })
})
