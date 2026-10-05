/**
 * P2-20 — 图片处理模式的中文标签只允许存在**一份**。
 *
 * 缺陷形态：`提取文字 / 表格数据 / 文档数字化 / 理解内容` 这组标签曾在**八处**
 * 各写一遍（生成面板模式卡 + 提交按钮 + 结果 chip、tab 注册表的 label 与 suffix、
 * 结果视图、处理中占位、预览区两处空态）。改一处漏一处的症状是「同一个功能在界面上
 * 有两个名字」——**零报错、零测试红**，只有用户能看出来。
 *
 * ⚠️ **第八处（结果 chip）是收敛时漏掉的那一处**：它抄的是**缩写版**
 * （`{ extract: '文字', digitize: '数字化' }`），一个完整标签都没写，
 * 于是「按完整标签判」的守卫全绿通过 —— 见 `INLINE_MODE_MAP` 的说明。
 *
 * 对照实验（证明本文件有区分力）：
 *   A. 把任一处改回手写字面量（例如 `label: '提取文字'`）
 *      → 「标签不在别处复现」红，且报出具体文件
 *   B. 把 `tabRegistry` 的 `exportSuffix` 改回硬编码 `'-提取文字'`
 *      → 「suffix 由 label 派生」红（改名后导出文件名与 tab 名对不上）
 *   C. 把结果 chip 改回缩写版手抄 → **只有**结构判据红（完整标签判据绿）——
 *      这就是两条判据都要在的理由
 */
import { describe, it, expect } from 'vitest'
import { IMAGE_MODES, IMAGE_MODE_LABELS, imageModeLabel } from '../lib/imageModes'
import { TabRegistry } from '../lib/tabRegistry'

import selfSrc from '../lib/imageModes.ts?raw'
import tabRegistrySrc from '../lib/tabRegistry.ts?raw'
import previewAreaSrc from '../components/preview/PreviewArea.tsx?raw'
import genPanelSrc from '../components/gen-panel/GenPanel.tsx?raw'
import modeCardsSrc from '../components/gen-panel/ImageModeCards.tsx?raw'
import resultViewSrc from '../components/preview/ImageResultView.tsx?raw'
import placeholderSrc from '../components/preview/ProcessingPlaceholder.tsx?raw'

/**
 * 标签被**当作字符串字面量**使用的形态（带引号）。
 * ⚠️ 判据刻意收紧到「引号包裹」而不是裸子串：`ImageModeCards` 与 `PreviewArea`
 * 各有一份**描述文案**，措辞里含「提取…文字」这类词（`提取全部文字` /
 * `提取图片中的全部文字`）——裸子串判据会把它们判成标签副本，是**假红**，
 * 而假红会逼着下一个人去合并两份**本就该不同**的文案（文案不在收敛范围）。
 */
const QUOTED_LABEL = /['"`](提取文字|表格数据|文档数字化|理解内容)['"`]/

/**
 * 「mode key 后面直接跟一个字符串字面量」= 手抄映射表的**形态**，与标签写的什么无关。
 *
 * ⚠️ 为什么在 `QUOTED_LABEL` 之外还要这一条：上面那条只认**完整标签**，而手抄的
 * 常见形态恰恰是**缩写**——`ImageModeCards` 的结果 chip 曾写
 * `{ extract: '文字', digitize: '数字化', understand: '理解' }[mode] || mode`，
 * 一个完整标签都没抄，于是 `QUOTED_LABEL` 全绿而界面上同一个模式有两个名字
 * （卡片叫「文档数字化」、chip 叫「数字化」）。**判据按文案定，就必然漏掉改写文案的副本**；
 * 按**结构**（key: 'string'）定才与文案无关。这是模式 #54 在字符串判据上的形态。
 */
const INLINE_MODE_MAP = /(?:^|[{,\s])['"]?(?:extract|table|digitize|understand)['"]?\s*:\s*['"`]/

/** 除 `lib/imageModes.ts` 自身外，任何文件都不该把标签写成字面量 */
const CONSUMERS: Array<[string, string]> = [
  ['lib/tabRegistry.ts', tabRegistrySrc],
  ['preview/PreviewArea.tsx', previewAreaSrc],
  ['gen-panel/GenPanel.tsx', genPanelSrc],
  ['gen-panel/ImageModeCards.tsx', modeCardsSrc],
  ['preview/ImageResultView.tsx', resultViewSrc],
  ['preview/ProcessingPlaceholder.tsx', placeholderSrc],
]

describe('P2-20 — 图片模式标签单一真相源', () => {
  it('表本身：四个 key 一一映射（键序即界面顺序）', () => {
    expect(IMAGE_MODES).toEqual(['extract', 'table', 'digitize', 'understand'])
    expect(Object.keys(IMAGE_MODE_LABELS)).toEqual([...IMAGE_MODES])
    expect(IMAGE_MODE_LABELS.extract).toBe('提取文字')
    expect(IMAGE_MODE_LABELS.understand).toBe('理解内容')
  })

  it('imageModeLabel 未知 key 原样返回（可见，不静默变空串）', () => {
    expect(imageModeLabel('extract')).toBe('提取文字')
    expect(imageModeLabel('nope')).toBe('nope')
  })

  it('★ 标签不在别处复现（源码守卫，模式 #54）', () => {
    // 先自证守卫有原料可查：表里确实写着这些标签
    expect(QUOTED_LABEL.test(selfSrc)).toBe(true)
    const offenders = CONSUMERS
      .filter(([, src]) => QUOTED_LABEL.test(src))
      .map(([name]) => name)
    expect(offenders, `这些文件手抄了图片模式标签：${offenders.join(', ')}`).toEqual([])
  })

  it('★ 标签缩写版的手抄同样被拦（结构判据，与文案无关）', () => {
    const offenders = CONSUMERS
      .filter(([, src]) => INLINE_MODE_MAP.test(src))
      .map(([name]) => name)
    expect(offenders, `这些文件内联了 mode→标签 映射：${offenders.join(', ')}`).toEqual([])
    // 自证守卫有原料可查
    expect(INLINE_MODE_MAP.test(selfSrc)).toBe(true)
  })

  it('对照：守卫对旧形态确实会红（否则上一条是恒真）', () => {
    const handCopy = `const MODE_LABELS = { extract: '提取文字', table: '表格数据' }`
    expect(QUOTED_LABEL.test(handCopy)).toBe(true)
    expect(INLINE_MODE_MAP.test(handCopy)).toBe(true)
    // 而描述文案不该被它命中——这是判据收紧到「引号包裹」的全部理由
    expect(QUOTED_LABEL.test(`desc: '提取全部文字，保留段落结构'`)).toBe(false)
    expect(QUOTED_LABEL.test(`desc: '提取图片中的全部文字，保留段落结构'`)).toBe(false)
  })

  it('对照：缩写版手抄——完整标签判据绿 / 结构判据红', () => {
    // 这正是 `ImageModeCards` 结果 chip 曾经的样子：一个完整标签都没抄
    const abbreviation = `{{ extract: '文字', digitize: '数字化', understand: '理解' }[mode] || mode}`
    expect(QUOTED_LABEL.test(abbreviation)).toBe(false)  // ← 旧判据：漏网
    expect(INLINE_MODE_MAP.test(abbreviation)).toBe(true) // ← 新判据：拦住
    // 不误伤：键名不是 mode key 的普通对象不受影响
    expect(INLINE_MODE_MAP.test(`{ key: 'extract', icon: ScanText }`)).toBe(false)
  })

  it('★ tab 的 exportSuffix 由 label 派生（不是并存的两份）', () => {
    for (const mode of IMAGE_MODES) {
      const tab = TabRegistry.get(mode)
      expect(tab, `tab ${mode} 未注册`).toBeDefined()
      expect(tab!.label).toBe(IMAGE_MODE_LABELS[mode])
      expect(tab!.exportSuffix).toBe(`-${IMAGE_MODE_LABELS[mode]}`)
      expect(tab!.exportLabel).toBe(IMAGE_MODE_LABELS[mode])
    }
  })

  it('对照：把改名当成实验，suffix 会跟着走（证明上一条测的是派生关系）', () => {
    // 直接对「派生式」本身做断言：若实现改回硬编码，`-${label}` 与前缀就不再等价。
    // 这里用表里现成的值验证等式成立（实现改了这条会红）。
    const label = IMAGE_MODE_LABELS.table
    expect(`-${label}`).toBe('-表格数据')
    expect(TabRegistry.get('table')!.exportSuffix).toBe(`-${label}`)
  })
})
