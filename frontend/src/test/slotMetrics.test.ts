/**
 * 面板默认宽的单一真相源（P2-23）。
 *
 * **修复的是什么**：`260` 这个值曾在三处各写一遍 ——
 * `lib/slotRegistry.ts` 的 `genPanel.defaultWidth` / `docLib.defaultWidth`，
 * 与 `store/uiSlice.ts` 的 `slots` 初值。三处同值，靠一句注释「改一处必须改另一处」维系。
 *
 * ⚠️ **这类同值重写的失效方式是静默的**：改了注册表没改 store 初值，症状只是
 * 「首次渲染宽度 ≠ 拖拽复位后的默认宽度」，拖一下就对上了，看起来不像 bug。
 *
 * ⚠️ **所以本文件的主体是源码守卫，不是运行时断言**：三处**同值**时，
 * 运行时断言（`genPanel.width === PANEL_DEFAULT_WIDTH`）在「字面量写回去」的实现下
 * **依然全绿** —— 它对这类回归**零区分力**。只有「源码里不再有第二份字面量」
 * 才能在值还没分叉的那一刻拦住它（模式 #38：判据必须有区分力；对照实验见文末 ④）。
 */

import { describe, it, expect } from 'vitest'
import { useStore } from '../store'
import { SlotRegistry } from '../lib/slotRegistry'
import {
  PANEL_DEFAULT_WIDTH, PANEL_MIN_WIDTH, PANEL_MAX_WIDTH,
} from '../lib/slotMetrics'
import slotRegistrySrc from '../lib/slotRegistry.ts?raw'
import uiSliceSrc from '../store/uiSlice.ts?raw'
import slotMetricsSrc from '../lib/slotMetrics.ts?raw'

// ── ① 常量模块本身是叶子 ──

describe('slotMetrics — 零依赖叶子', () => {
  it('不自建 import（否则「双方都可安全引」的前提就不成立）', () => {
    const imports = slotMetricsSrc.match(/^\s*import\s.+$/gm) ?? []
    expect(imports).toEqual([])
  })

  it('三个常量都是正数且 min < default < max', () => {
    expect(PANEL_MIN_WIDTH).toBeGreaterThan(0)
    expect(PANEL_DEFAULT_WIDTH).toBeGreaterThan(PANEL_MIN_WIDTH)
    expect(PANEL_MAX_WIDTH).toBeGreaterThan(PANEL_DEFAULT_WIDTH)
  })
})

// ── ② 注册表与 store 初值都取自常量 ──

describe('三处引用同一常量', () => {
  it('SlotRegistry 的两个 left 面板 defaultWidth 都 == PANEL_DEFAULT_WIDTH', () => {
    expect(SlotRegistry.get('genPanel')?.defaultWidth).toBe(PANEL_DEFAULT_WIDTH)
    expect(SlotRegistry.get('docLib')?.defaultWidth).toBe(PANEL_DEFAULT_WIDTH)
  })

  it('SlotRegistry 的 min/max 也都取自常量', () => {
    for (const id of ['genPanel', 'docLib']) {
      expect(SlotRegistry.get(id)?.minWidth).toBe(PANEL_MIN_WIDTH)
      expect(SlotRegistry.get(id)?.maxWidth).toBe(PANEL_MAX_WIDTH)
    }
  })

  it('store 的 slots 初值宽度 == PANEL_DEFAULT_WIDTH（首渲染就走这条）', () => {
    const { slots } = useStore.getState()
    expect(slots.genPanel.width).toBe(PANEL_DEFAULT_WIDTH)
    expect(slots.docLib.width).toBe(PANEL_DEFAULT_WIDTH)
  })
})

// ── ③ 源码守卫：不允许再出现第二份字面量 ──

/** 剥掉注释再判——两份源码的注释都**刻意**引用了旧写法当反例（模式 #54 的配套细节） */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

describe('★ 源码里不再有第二份宽度字面量', () => {
  // ⚠️ 判据是**这三个被共享的值**，不是「任何数值得写成常量」：
  //    preview 区的 `defaultWidth: 0` / `minWidth: 200` / `maxWidth: Infinity`
  //    是它自己的语义（center 区占满剩余、默认不占位），与 left 面板那套无关，
  //    强行一并抽常量反而会诱导下一位读者以为它们共享同一个默认宽。
  const SHARED = /\b(260|50|600)\b/

  it('slotRegistry.ts 不含这三处字面量', () => {
    expect(stripComments(slotRegistrySrc)).not.toMatch(SHARED)
  })

  it('uiSlice.ts 不含这三处字面量', () => {
    expect(stripComments(uiSliceSrc)).not.toMatch(SHARED)
  })

  it('两处都确实 import 了 slotMetrics（不是各自又抄一份常量）', () => {
    expect(slotRegistrySrc).toContain("from './slotMetrics'")
    expect(uiSliceSrc).toContain("from '../lib/slotMetrics'")
  })
})

// ④ 对照实验（**实做，非本文件内的用例**，结论记录在此）：
//    把 `uiSlice.ts` 的 `width: PANEL_DEFAULT_WIDTH` 改回 `width: 260` ——
//    · ①② 的运行时断言**全绿**（值一模一样）
//    · ③ 的两个源码守卫**精确红**（`\b260\b` 命中 / `defaultWidth: 260` 命中）
//    这就是本文件以源码守卫为主体的理由（模式 #38 的「判据要有区分力」）。
