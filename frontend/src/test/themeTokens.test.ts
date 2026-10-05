/**
 * P2-16 — 主题色值必须走 token。
 *
 * **修复的是什么**：`accent-[#007AFF]` 3 处（复选框永远是蓝色，换配色也不变）、
 * `.r-handle` 悬停底的 `rgba(0,122,255,.1)`（同一规则块里 `::after` 早在用 `--tk-accent`）、
 * `LogPanel` 的 `bg-[#1E1E1E]`。
 *
 * ⚠️ 最后一处最值得说：日志的**文字**用的是 `text-error/warning/success/disabled`
 * （全是主题 token，浅色主题下是深色系），而**底**是写死的深黑 —— 两者在浅色主题下
 * 必然打架。「终端就该是黑的」在这里不成立，因为字不是终端色。
 *
 * ⚠️ **刻意保留的一组**（本文件把它钉住，防下一个人「顺手清理」）：
 * `docMeta.tsx` 的 `TYPE_COLORS` —— 它是**类型标识色**（对标 GitHub language color），
 * 文件头 `:4-5` 的设计声明逐字写着「刻意不随六轴主题变化，跨主题保持可辨识」。
 * 判据是「它是不是主题色」，不是「它是不是 hex」。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import logPanelSrc from '../components/ui/LogPanel.tsx?raw'
import docMetaSrc from '../components/doc-library/docMeta.tsx?raw'
import pluginSettingsSrc from '../components/settings/PluginSettings.tsx?raw'
import settingsPageSrc from '../components/settings/SettingsPage.tsx?raw'
import exportPanelSrc from '../components/ui/ExportPanel.tsx?raw'

/**
 * `index.css` 走 fs 直读而不是 `?raw`：vitest 默认 `css: false`，
 * CSS 的 `?raw` 导入会得到**空串** —— 那样正则恒不匹配，而「不含违规色值」的断言
 * 反而会**恒真**（假绿灯）。这正是模式 #38 说的「判据必须自证有原料可查」。
 */
const indexCss = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf-8')

/** 剥 CSS 注释 —— 本文件与源码里都可能**刻意**引用旧写法当反例 */
function stripCssComments(s: string): string {
  return s.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** Tailwind 任意色值类：`bg-[#123456]` / `text-[#abc]` … */
const ARBITRARY_COLOR = /\b(?:bg|text|border|accent|ring|shadow|fill|stroke|from|to|via)-\[#/

/** 全仓源码（**排除 test/** —— 测试里刻意留着旧写法当反例） */
const ALL_SOURCES = import.meta.glob(
  ['../**/*.{ts,tsx}', '!../test/**'],
  { query: '?raw', import: 'default', eager: true },
) as Record<string, string>

describe('P2-16 — 零硬编码主题色', () => {
  it('全仓 .ts/.tsx 无 Tailwind 任意色值类', () => {
    const offenders = Object.entries(ALL_SOURCES)
      .filter(([, src]) => ARBITRARY_COLOR.test(src))
      .map(([p]) => p.replace(/^\.\.\//, ''))
    expect(offenders, `这些文件用了任意色值：${offenders.join(', ')}`).toEqual([])
    // 自证扫描面有效（不是空 glob 导致恒真 —— 模式 #38）
    expect(Object.keys(ALL_SOURCES).length).toBeGreaterThan(50)
  })

  it('对照：任意色值类确实会被抓到', () => {
    expect(ARBITRARY_COLOR.test('className="w-4 h-4 accent-[#007AFF]"')).toBe(true)
    expect(ARBITRARY_COLOR.test('className="w-4 h-4 accent-accent"')).toBe(false)
    // 不误伤：任意**尺寸**值仍在用（`text-[12px]` 满地都是，与颜色无关）
    expect(ARBITRARY_COLOR.test('className="text-[12px] px-[3px]"')).toBe(false)
  })

  it('三处复选框走 accent-accent（换了配色就该跟着变）', () => {
    for (const src of [pluginSettingsSrc, settingsPageSrc, exportPanelSrc]) {
      expect(src).toContain('accent-accent')
      expect(src).not.toContain('accent-[#')
    }
  })

  it('★ 日志底色走 token（文字是主题色，底不能自己定）', () => {
    expect(logPanelSrc).not.toContain('bg-[#')
    expect(logPanelSrc).toContain('bg-surface')
  })

  it('★ .r-handle 悬停底走 accent token', () => {
    // 剥注释再判：改动处的注释**刻意**写着旧写法当反例（模式 #54 的配套细节）
    expect(stripCssComments(indexCss)).not.toContain('rgba(0,122,255')
    expect(indexCss).toMatch(/\.r-handle:hover[^{]*\{[^}]*color-mix\(in srgb, var\(--tk-accent\)/)
  })

  it('index.css 的裸 hex 只出现在 token 定义处', () => {
    // 自证读到了文件（空串会让这条恒真）
    expect(indexCss.length).toBeGreaterThan(1000)
    // 判据：把每一处 `--xxx: #hex`（token 定义**必须**有字面值）抹掉后，
    // 再查是否还有 hex 残留。⚠️ 不能按「行首是不是 `--tk-`」判 ——
    // `[data-base="neutral"] { --tk-surface: #F5F5F5; --tk-elevated: #FCFCFC; ... }`
    // 是**一行里好几个 token 定义**，行首是选择器，那种写法会被整行误判成违规。
    const stripped = stripCssComments(indexCss)
      .replace(/--[\w-]+:\s*#[0-9A-Fa-f]{3,8}\b/g, '--token: (value)')
    const offenders = stripped
      .split('\n')
      .map((line, i) => [i + 1, line.trim()] as const)
      .filter(([, line]) => /#[0-9A-Fa-f]{3,8}\b/.test(line))
      .map(([n, line]) => `${n}: ${line}`)
    expect(offenders, `这些行的 hex 不在 token 定义位置：\n${offenders.join('\n')}`).toEqual([])
  })
})

describe('★ 反面对照：TYPE_COLORS 刻意保留', () => {
  it('类型标识色仍在（它是设计决定，不是漏网的硬编码）', () => {
    expect(docMetaSrc).toContain('TYPE_COLORS')
    expect(docMetaSrc).toMatch(/video:\s*\{[^}]*#[0-9A-Fa-f]{6}/)
  })

  it('它的设计声明必须与代码同处一个文件（否则下一个人会来「修」它）', () => {
    // 依据在文件头，不在 `:42` 定义旁 —— 报告自己承认过这里引用粒度选错
    const header = docMetaSrc.split('\n').slice(0, 10).join('\n')
    expect(header).toMatch(/刻意不随六轴主题变化|类型标识色/)
  })
})
