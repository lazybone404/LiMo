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
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { getTypeMeta, isFormatVisible } from '../components/gen-panel/constants'
import { TabRegistry } from '../lib/tabRegistry'

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

// ─────────────────────────────────────────────────────────────────────────────
// P0-01 — 格式项的可见性由**后端能力表**派生，前后端同源
// ─────────────────────────────────────────────────────────────────────────────
//
// 修复前有两处各说各话：
//   ① `GenerateOptions` 的渲染条件按 `isPDF`/`isVideo`/`hasImages` 硬编码，
//      而 `GenPanel` 提交时会 `.filter(f => availableFormats.includes(f))` ——
//      UI 显示一个能力表不认的项 = 用户勾了、提交时被**静默抹掉**（本项要修的形状）；
//   ② `availableFormats` 前端是 `genOptions[st] || []`（无兜底），后端是
//      `AVAILABLE_OPTIONS.get(st, AVAILABLE_OPTIONS.get("text"))`（有兜底）——
//      同一件事两个表达，缺键的类型前端拿到空表、后端拿到 text 的 4 项。
//
// 对照实验（证明本组用例有区分力）：
//   A. 把 `constants.ts` 的 `availableFormats` 改回 `genOptions[st] || []`
//      → 「半 B」两条红（epub 的 availableFormats 变 `[]`）
//   B. 把 `formulas` 的门控改回 `isPDF` → 「半 A」条红（docx/pptx/text 三项）
//   C. 给后端 `AVAILABLE_OPTIONS` 加/删任一键值 → 「守卫自证」条红

/** 后端 `generation/prompt.py::AVAILABLE_OPTIONS` 的期望镜像。
 *
 *  ⚠️ 这**不是**真相源，只是「我以为的后端值」——下方第一条用例会打开后端源码
 *  逐键比对，两边一旦漂移就红（模式 #54：同源关系是搜索题，不是记忆题）。 */
const EXPECTED_BACKEND_FORMATS: Record<string, string[]> = {
  video: ['toc', 'link', 'screenshot', 'summary', 'chart'],
  audio: ['toc', 'summary', 'formulas'],
  webpage: ['toc', 'summary', 'formulas', 'chart'],
  pdf: ['toc', 'formulas', 'references', 'summary', 'images', 'chart'],
  docx: ['toc', 'summary', 'images', 'formulas', 'chart'],
  pptx: ['toc', 'summary', 'images', 'formulas', 'chart'],
  xlsx: [],
  text: ['toc', 'summary', 'formulas', 'chart'],
}

/** 真去读后端源码（跨语言守卫，同 `appShortcutKeys.test.ts` 的手法）。 */
function parseBackendAvailableOptions(): Record<string, string[]> {
  const src = readFileSync(
    resolve(process.cwd(), '../backend/app/generation/prompt.py'), 'utf-8')
  const block = src.match(/AVAILABLE_OPTIONS\s*=\s*\{([\s\S]*?)\n\}/)
  expect(block, 'prompt.py 里找不到 AVAILABLE_OPTIONS —— 守卫本身失效了').toBeTruthy()
  const out: Record<string, string[]> = {}
  for (const m of block![1].matchAll(/"(\w+)"\s*:\s*\[([^\]]*)\]/g)) {
    out[m[1]] = m[2].split(',').map(s => s.trim().replace(/['"]/g, '')).filter(Boolean)
  }
  return out
}

describe('P0-01 — 格式门控与后端能力表同源', () => {
  it('守卫自证：从 prompt.py 解析出的表与期望镜像逐键相等', () => {
    const parsed = parseBackendAvailableOptions()
    expect(Object.keys(parsed).sort()).toEqual(Object.keys(EXPECTED_BACKEND_FORMATS).sort())
    expect(parsed).toEqual(EXPECTED_BACKEND_FORMATS)
  })

  it('半 B：前端对缺键类型兜底到 text（与后端同一个兜底，不是空表）', () => {
    // 后端：AVAILABLE_OPTIONS.get(st, AVAILABLE_OPTIONS.get("text", []))
    const m = getTypeMeta('epub', EXPECTED_BACKEND_FORMATS)
    expect(m.availableFormats).toEqual(EXPECTED_BACKEND_FORMATS.text)
    expect(m.availableFormats.length).toBeGreaterThan(0)  // 改回 `|| []` 时此处必红
  })

  it('半 B：caps 里根本没下发该键时同样兜底（不是只有「下发了空数组」才救）', () => {
    // caps 下发的 genOptions 是**整表**（后端 AVAILABLE_OPTIONS 原样），
    // 用来模拟的真实形态是「表在、但没有这个键」——即 `document`/`epub` 的真实处境
    // （`_adapter_to_source_type` 对 document 适配器总是细分出具体类型或 pdf 兜底，
    // 故 `document` 作为 sourceType 在导入路径上其实不可达；epub 可达且真的无键）。
    const m = getTypeMeta('document', EXPECTED_BACKEND_FORMATS)
    expect(EXPECTED_BACKEND_FORMATS.document).toBeUndefined()  // 前提：该键本来就不存在
    expect(m.availableFormats).toEqual(EXPECTED_BACKEND_FORMATS.text)
  })

  it('caps 未就绪（genOptions 为空表）→ 格式项全部不可见，且不过滤掉任何勾选', () => {
    // ⚠️ 这是**有意**的语义，不是漏洞：`genOptions` 来自 `/api/capabilities`，
    // 与模型列表 / 风格档位 / 产物类型同一个来源；未就绪时不猜（猜 = 又回硬编码）。
    // 关键性质是「不可见」与「提交时不被抹掉」**同时**成立 —— 空表既拿不到 UI 入口，
    // `.filter` 也不会误伤（没有可勾的项）。
    const m = getTypeMeta('pdf', {})
    expect(m.availableFormats).toEqual([])
    for (const f of ['toc', 'formulas', 'chart', 'images']) {
      expect(isFormatVisible(f, m)).toBe(false)
    }
  })

  it('半 A：formulas 对 pdf/docx/pptx/text 都可见（此前只有 PDF 能勾）', () => {
    for (const st of ['pdf', 'docx', 'pptx', 'text']) {
      expect(isFormatVisible('formulas', getTypeMeta(st, EXPECTED_BACKEND_FORMATS)),
        `${st} 的「公式集」应可见（后端能力表含 formulas）`).toBe(true)
    }
  })

  it('半 A 反向：references 仍只对 pdf 可见（能力表里只有 pdf 有）', () => {
    expect(isFormatVisible('references', getTypeMeta('pdf', EXPECTED_BACKEND_FORMATS))).toBe(true)
    for (const st of ['docx', 'pptx', 'text', 'webpage']) {
      expect(isFormatVisible('references', getTypeMeta(st, EXPECTED_BACKEND_FORMATS)),
        `${st} 不该出现「参考文献」（能力表无此项）`).toBe(false)
    }
  })

  it('核心不变量：任何 UI 可见的格式项都必然在能力表里', () => {
    // 这条直接钉住 P0-01 的成因 —— GenPanel 提交时按 availableFormats 过滤，
    // 「可见但不在表里」的项会被无声丢弃。
    const ALL = ['toc', 'link', 'screenshot', 'summary', 'chart',
      'formulas', 'references', 'images']
    for (const st of Object.keys(EXPECTED_BACKEND_FORMATS)) {
      const m = getTypeMeta(st, EXPECTED_BACKEND_FORMATS)
      for (const f of ALL) {
        if (isFormatVisible(f, m)) {
          expect(m.availableFormats, `${st} 的 ${f} UI 可见却不在能力表里`).toContain(f)
        }
      }
    }
  })

  it('images 额外受 hasImages 约束（能力表说这类文档能提图，但这篇未必有）', () => {
    expect(isFormatVisible('images', getTypeMeta('pdf', EXPECTED_BACKEND_FORMATS))).toBe(true)
    // 造一个「能力表含 images、但 hasImages=false」的类型 —— 该附加约束必须生效。
    // ⚠️ 用 `text` 构造（它的 hasImages 为假）；`epub` 不行：`hasImages` 的定义
    // 含 epub（`pdf|docx|pptx|epub`），拿它构造等于前提不成立、用例恒真。
    const m = { ...getTypeMeta('text', EXPECTED_BACKEND_FORMATS), availableFormats: ['images'] }
    expect(m.hasImages).toBe(false)  // 前提
    expect(isFormatVisible('images', m)).toBe(false)
    expect(isFormatVisible('images', { ...m, hasImages: true })).toBe(true)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 2026-10-11 — 导出面板的 tab 清单滤掉 `contentSource === 'none'`
// ─────────────────────────────────────────────────────────────────────────────
//
// 「原图」/「OCR」两个 tab 的 `contentSource` 是 `'none'`（`CONTENT_GETTERS.none`
// 恒返回 null）—— 它们**自己声明了没有可导出的内容**。而 `TypeMeta.tabs` 唯一的
// 消费方就是导出面板，从前不过滤 ⇒ 选项在、勾得动，导出后后端在那个 tab 上兜底成
// `rawText`，于是 zip 里多出一个名叫「原图」、内容其实是原文的文件。
//
// 对照实验：去掉 `constants.ts` 的 `.filter(t => t.contentSource !== 'none')`
// → 第一、第三条红（第二条仍绿 —— 它守的正是「别把 tab 本身删了」）。

describe('2026-10-11 — 导出 tab 清单滤掉 contentSource=none', () => {
  it('图片类型不再出现「原图」/「OCR」，四个处理 tab 一个不少', () => {
    const keys = getTypeMeta('image').tabs.map(t => t.key)
    expect(keys).not.toContain('original')
    expect(keys).not.toContain('ocr')
    expect(keys).toEqual(['extract', 'table', 'digitize', 'understand'])
  })

  it('守卫自证：这两个 tab 在 TabRegistry 里**仍然注册着**', () => {
    // ⚠️ 滤的是**导出清单**，不是把 tab 删了 —— 真删掉的话预览区 tab 栏会静默少两项，
    // 而上面那条照样绿。两条必须同时在场。
    const all = TabRegistry.forSourceType('image').map(t => t.key)
    expect(all).toContain('original')
    expect(all).toContain('ocr')
  })

  it('过滤规则**只有** contentSource !== none（不是按类型硬编码的黑名单）', () => {
    // 判据从 TabRegistry 派生，不手写「图片类型去掉哪几个」——
    // 手写名单在下次加 tab 时会静默过期（模式 #54）。
    for (const st of ['image', 'webpage', 'video', 'pdf', 'xlsx', 'composite', 'text']) {
      const expected = TabRegistry.forSourceType(st)
        .filter(t => t.contentSource !== 'none')
        .map(t => t.key)
      expect(getTypeMeta(st).tabs.map(t => t.key), `${st} 的导出 tab 清单`).toEqual(expected)
    }
  })
})
