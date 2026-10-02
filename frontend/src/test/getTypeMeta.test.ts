/**
 * 类型元数据（`getTypeMeta`）—— GenPanel 的产物 / 按钮文案 / tab 全部由它驱动。
 *
 * 本文件只钉住一件事：**composite 与既有类型的产物形状不同，且既有类型一点没变**。
 *
 * 对照实验（均已实测，证明本文件有区分力）：
 *   A. 不给 composite 加分支（落到最末的默认支）
 *      → 红（`coreProducts` 多出 polished、`hasPolish` 变 true）
 *   B. 把判断写成 `st.includes('comp')` 这类宽条件（或顺手把 composite 放进
 *      `_ALL_DOC_TYPES`）→ 红（`isDoc` 变 true，GenPanel 的文档引擎块会被渲染出来）
 */
import { describe, it, expect } from 'vitest'
import { getTypeMeta } from '../components/gen-panel/constants'

describe('getTypeMeta — composite', () => {
  const m = getTypeMeta('composite')

  it('只有笔记这一个产物，且没有「整理版原文」', () => {
    expect(m.coreProducts.map(p => p.name)).toEqual(['notes'])
    // hasPolish 是**独立的**一个位（GenPanel 有多处单独读它）——只改 coreProducts
    // 不改它，仍会漏出「整理版原文」的入口
    expect(m.hasPolish).toBe(false)
  })

  it('按钮文案是「生成对比综述」', () => {
    expect(m.generateLabel).toBe('生成对比综述')
  })

  it('isComposite 为真、isDoc 为假', () => {
    expect(m.isComposite).toBe(true)
    // ⚠️ isDoc 为假是**有意的**：GenPanel 里文档引擎选择 / EPUB 解读 / 文档图片区块
    // 全部以 isDoc 门控，composite 不该看到任何一个（它没有「原始文件」可解析）
    expect(m.isDoc).toBe(false)
    expect(m.isPDF).toBe(false)
  })

  it('tab 与 TabRegistry 一致（笔记 + 转录）', () => {
    expect(m.tabs.map(t => t.key)).toEqual(['summary', 'transcript'])
  })
})

describe('getTypeMeta — 既有类型零回归', () => {
  it('pdf 仍是 笔记 + 整理版原文，按钮仍是「生成」', () => {
    const m = getTypeMeta('pdf')
    expect(m.isComposite).toBe(false)
    expect(m.hasPolish).toBe(true)
    expect(m.coreProducts.map(p => p.name)).toEqual(['notes', 'polished'])
    expect(m.generateLabel).toBe('生成')
    expect(m.isDoc).toBe(true)
  })

  it('xlsx 仍是单产物「数据摘要」', () => {
    const m = getTypeMeta('xlsx')
    expect(m.isComposite).toBe(false)
    expect(m.coreProducts.map(p => p.name)).toEqual(['both'])
    expect(m.hasPolish).toBe(false)
    expect(m.generateLabel).toBe('生成数据摘要')
  })
})
