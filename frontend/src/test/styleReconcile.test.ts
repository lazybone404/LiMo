/**
 * P0-02 — 风格档位的**跨类型条件重置**。
 *
 * 缺陷形态：`style` 从 localStorage 恢复后**再不随文档类型变化**。于是
 * 「PDF 选 IMRaD → 切到视频」会带着一个视频风格表里不存在的 key 去生成，
 * 后端 `lookup.get(style, "")` 静默返回空串 → 产物无风格，**两侧都零报错**。
 *
 * 修法的关键字是「**条件**」：只在当前档对新类型不合法时才重置。写成无条件重置
 * 会引入一个更讨厌的症状 —— 冲掉用户在两类之间共享的合法档（`plain`/`detailed`/
 * `academic`/`business` 在视频与文档两套里都有），表现为「我只换了个文档，刚选的
 * 风格没了」，而那是用户自己的选择。
 *
 * 对照实验（证明本文件有区分力）：
 *   A. 把 `reconcileStyle` 改成无条件 `return DEFAULT_STYLE`
 *      → 「共享档不被重置」条红（plain/detailed/minimal/academic/business 全被改掉）
 *   B. 把 `reconcileStyle` 改成恒 `return style`（不重置）
 *      → 「档位不在新类型表里」条红
 */
import { describe, it, expect } from 'vitest'
import { reconcileStyle, DEFAULT_STYLE } from '../components/gen-panel/constants'

// 三套档位表取自 GenPanel 的 fallback 数组（caps 未就绪时的兜底）。
// ⚠️ 它们**不必等于**后端真实表 —— 本文件测的是纯函数的判定逻辑，不是表内容；
// 表内容与后端的一致性由 capabilities 下发保证，不在这个层测。
const VIDEO = ['plain', 'minimal', 'detailed', 'academic', 'life']
const DOC = ['plain', 'imrad', 'detailed', 'business', 'tutorial']
const EXCEL = ['plain', 'detailed', 'minimal', 'business']

describe('P0-02 — 风格档位跨类型条件重置', () => {
  it('本类型专有档保持不动（不误伤）', () => {
    expect(reconcileStyle('imrad', DOC)).toBe('imrad')
    expect(reconcileStyle('life', VIDEO)).toBe('life')
    expect(reconcileStyle('tutorial', DOC)).toBe('tutorial')
  })

  it('⚠️ 共享档不被重置 ——「条件」二字的全部意义', () => {
    for (const f of ['plain', 'detailed']) {
      expect(reconcileStyle(f, DOC), `${f} 在 doc 表里合法，不该被重置`).toBe(f)
      expect(reconcileStyle(f, VIDEO), `${f} 在 video 表里合法，不该被重置`).toBe(f)
      expect(reconcileStyle(f, EXCEL), `${f} 在 excel 表里合法，不该被重置`).toBe(f)
    }
    expect(reconcileStyle('minimal', VIDEO)).toBe('minimal')
    expect(reconcileStyle('academic', VIDEO)).toBe('academic')
    expect(reconcileStyle('business', DOC)).toBe('business')
  })

  it('档位不在新类型表里 → 退回默认档（不是原样带过去）', () => {
    // PDF 选 IMRaD → 切视频；视频选生活向 → 切 PDF/Excel
    expect(reconcileStyle('imrad', VIDEO)).toBe(DEFAULT_STYLE)
    expect(reconcileStyle('life', DOC)).toBe(DEFAULT_STYLE)
    expect(reconcileStyle('tutorial', EXCEL)).toBe(DEFAULT_STYLE)
    expect(reconcileStyle('academic', DOC)).toBe(DEFAULT_STYLE)
  })

  it('none（不套风格）在任何类型下都合法', () => {
    for (const keys of [VIDEO, DOC, EXCEL, []]) {
      expect(reconcileStyle('none', keys), `none 不该被重置（keys=${keys.length} 项）`).toBe('none')
    }
  })

  it('默认档在三套表里都存在（否则重置是「从非法换到另一个非法」）', () => {
    for (const keys of [VIDEO, DOC, EXCEL]) expect(keys).toContain(DEFAULT_STYLE)
  })

  it('未知值（表漂移 / 空串）也退回默认档', () => {
    // 旧别名 `meeting_minutes` 在 genPrefs 的迁移分支里已被归一为 `meeting`，
    // 正常路径不会到这里；但「表里没有的值一律不原样带过去」这条性质本身要成立。
    expect(reconcileStyle('meeting_minutes', DOC)).toBe(DEFAULT_STYLE)
    expect(reconcileStyle('', DOC)).toBe(DEFAULT_STYLE)
  })
})
