/**
 * 导出文件名清洗的**跨语言同值守卫**（P1-12）。
 *
 * 同一个「标题 → 文件名」的动作被写在三处，此前**长度上限分叉**：
 *   - 后端 `app/routers/export.py::_safe_filename` —— 截 80
 *   - `components/ui/ExportMenu.tsx` —— 截 60（客户端 MD / 复制下载）
 *   - `components/ui/ExportPanel.tsx` —— 截 60（批量导出面板的初值）
 *
 * 后果不是报错，而是**同一个标题从不同入口导出得到两个不同的文件名**
 * （用户会以为那是两份东西）。收敛后前端两处走 `sanitizeExportName`，
 * 数值由 `EXPORT_NAME_MAX` 定，后端那一半跨语言只能靠**源码级守卫**核对
 * （模式 #65：同一条约束写在两种语言里时，必须由机器核对）。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { EXPORT_NAME_MAX, sanitizeExportName } from '../lib/download'

const BACKEND_EXPORT_PY = readFileSync(
  resolve(process.cwd(), '../backend/app/routers/export.py'),
  'utf-8',
)

describe('sanitizeExportName', () => {
  it('去掉文件系统非法字符', () => {
    expect(sanitizeExportName('a/b:c*d?e"f<g>h|i')).toBe('abcdefghi')
    // 全角标点不在字符集里（只针对半角路径/文件系统字符）——别顺手把它们也抹了
    expect(sanitizeExportName('标题：带冒号')).toBe('标题：带冒号')
  })

  it('空标题兜底 export', () => {
    expect(sanitizeExportName('')).toBe('export')
    expect(sanitizeExportName(undefined)).toBe('export')
    expect(sanitizeExportName(null)).toBe('export')
  })

  it('长度上限是 80 —— 60 字符的标题**不再**被截（修复前的分叉点）', () => {
    const t60 = 'a'.repeat(60)
    expect(sanitizeExportName(t60)).toBe(t60)
    expect(sanitizeExportName('a'.repeat(80))).toBe('a'.repeat(80))
    expect(sanitizeExportName('a'.repeat(200))).toHaveLength(EXPORT_NAME_MAX)
  })
})

describe('跨语言：与后端 _safe_filename 同值', () => {
  it('后端截断长度 == EXPORT_NAME_MAX', () => {
    const m = BACKEND_EXPORT_PY.match(
      /return name\[:(\d+)\]\s*if len\(name\)\s*>\s*(\d+)\s*else name/,
    )
    expect(
      m,
      '后端 `_safe_filename` 的写法变了（解析不到 name[:N]）——请同步更新本守卫，'
      + '否则这条跨语言核对会静默失效（模式 #38：判据必须自证有区分力）',
    ).toBeTruthy()
    // 两个数字本身也得同值（`name[:80] if len(name) > 60` 这种写错法照样能解析）
    expect(m![1]).toBe(m![2])
    expect(Number(m![1])).toBe(EXPORT_NAME_MAX)
  })

  it('两个导出组件都复用了 sanitizeExportName（防第四份副本长出来）', () => {
    for (const f of [
      'src/components/ui/ExportMenu.tsx',
      'src/components/ui/ExportPanel.tsx',
    ]) {
      const src = readFileSync(resolve(process.cwd(), f), 'utf-8')
      expect(src, `${f} 应复用 sanitizeExportName 而不是内联 replace+slice`).toContain(
        'sanitizeExportName',
      )
    }
  })
})
