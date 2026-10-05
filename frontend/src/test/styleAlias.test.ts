/**
 * P2-19 — 风格键的历史别名只允许存在**一份**（前端）。
 *
 * 缺陷形态：`meeting_minutes → meeting` 这条迁移被写在两处 ——
 * `constants.ts` 的 `STYLE_ALIAS` 表（`normalizeStyle` 读它）与 `genPrefs.ts`
 * 里手抄的 `if (raw.style === 'meeting_minutes')`。两处都能独立工作，所以
 * **改一处漏一处不会报错**：新增第二条别名时，只改表 → 老存档读出来仍是旧 key
 * → `reconcileStyle` 判定它不在新类型表里 → 悄悄退回默认档。用户看到的是
 * 「我的档位自己变了」，没有任何日志。
 *
 * 对照实验（证明本文件有区分力）：
 *   A. 把 `genPrefs.ts` 的迁移改回手抄 `=== 'meeting_minutes'`
 *      → 「别名表只有一份」红（源码守卫抓到第二处字面量）
 *   B. 去掉 `genPrefs.ts` 的 `typeof` 守卫，改成无条件
 *      `raw.style = normalizeStyle(raw.style)`
 *      → 「老存档无 style 字段」红（显式造出的 `undefined` 覆盖了新默认值）
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { normalizeStyle } from '../components/gen-panel/constants'
import { loadGenPrefs } from '../components/gen-panel/genPrefs'
import genPrefsSrc from '../components/gen-panel/genPrefs.ts?raw'

const KEY = 'genPrefs'

beforeEach(() => localStorage.clear())

describe('P2-19 — 风格别名单一真相源', () => {
  it('normalizeStyle 归一到表里的目标值', () => {
    expect(normalizeStyle('meeting_minutes')).toBe('meeting')
    expect(normalizeStyle('meeting')).toBe('meeting')   // 幂等
    expect(normalizeStyle('plain')).toBe('plain')       // 非别名原样
    expect(normalizeStyle('')).toBe('')
  })

  it('老存档里的旧 key 经 loadGenPrefs 归一', () => {
    localStorage.setItem(KEY, JSON.stringify({ style: 'meeting_minutes' }))
    expect(loadGenPrefs().style).toBe('meeting')
  })

  it('⚠️ 老存档整个没有 style 字段时，必须拿到新默认值（不是 undefined）', () => {
    // 这是「无条件 `raw.style = normalizeStyle(raw.style)`」会踩的坑：
    // 读出来的 undefined 被显式写回，末尾 `{ ...DEFAULTS, ...raw }` 的展开
    // **会覆盖** DEFAULTS —— 症状是「没选过风格的老用户拿不到 plain」。
    // 判据取「与无存档时的结果一致」而非硬编码 'plain'：默认值改了这条也不必跟着改。
    const baseline = loadGenPrefs().style          // localStorage 已清空
    expect(baseline).toBeTypeOf('string')
    expect(baseline).not.toBe('')
    localStorage.setItem(KEY, JSON.stringify({ selectedCore: ['notes'] }))
    expect(loadGenPrefs().style).toBe(baseline)
  })

  it('★ 迁移必须走共享函数，而不是自己再抄一份映射', () => {
    // ⚠️ 判据刻意**不查「有没有旧 key 字面量」**：注释里就该写清楚在迁移哪个 key，
    // 按字面量查会把「讲代码的文字」判成违规，得靠剥注释来绕——而剥注释本身
    // 又引入一个正则判据（`//` 出现在字符串里怎么办）。改成查「**调用了共享函数**」：
    // 这是真正防回归的性质——表里新增第二条别名时，调用方自动跟随（模式 #54）。
    expect(genPrefsSrc).toContain('normalizeStyle(raw.style)')
    expect(genPrefsSrc).toContain("from './constants'")
  })

  it('对照：上面的判据对手抄形态确实会红（否则它是恒真）', () => {
    const handCopy = "if (raw.style === 'meeting_minutes') {\n  raw.style = 'meeting'\n}"
    expect(handCopy).not.toContain('normalizeStyle(raw.style)')   // 判据会红
    expect(handCopy).not.toContain("from './constants'")
  })
})
