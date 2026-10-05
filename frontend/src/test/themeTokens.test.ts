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

/**
 * 「不透明的主色实底」——`bg-accent`，但**排除** `bg-accent-hover`（那是 hover 态底色，
 * 与实底同时出现）与 `bg-accent/10`（透明变体，上面本来就不该用 on-accent 那种前景）。
 */
const ACCENT_SOLID = /\bbg-accent(?![\w/-])/

/** 写死的前景色 —— 主色实底上不允许出现（P1-16） */
const HARDCODED_FG = /\btext-(?:white|black)\b|\btext-\[#/

/**
 * 判据（**语义**，不是「必须出现某个类名」）：主色实底上的前景色必须**随主色变化**。
 * 修复前 33 处写死 `text-white`，而主色在 dark 下亮度 62%、白字对比度 ~2.9:1
 * （「暖阳」1.9:1）——低于 WCAG AA 4.5:1，也低于大字档 3:1。
 *
 * ⚠️ 刻意**不**禁 `text-white` 本身：`bg-black/20 text-white`（遮罩上的白图标）
 *    与 `bg-error text-white` 之类是**对的**，一刀切禁类名会产生大量假阳性。
 *    被禁的是「写死前景 × 不透明主色底」这个**组合**。
 */
export function isBadAccentLine(line: string): boolean {
  return ACCENT_SOLID.test(line) && HARDCODED_FG.test(line)
}

/**
 * 剥 TS/TSX 注释 —— **本判据必须剥**：`lib/onAccent.ts` 的文件头逐字写着「33 处
 * `bg-accent` 实底按钮，前景一律写死 `text-white`」，不剥的话「讲代码的文字」会被
 * 判成违规（模式 #54 的配套细节：守卫走 AST 而非 grep 的同一理由）。
 *
 * 块注释替换成等量空白而非删除 —— 保留换行，报错时给的行号才对得上。
 */
export function stripTsComments(s: string): string {
  return s
    .replace(/\/\*[\s\S]*?\*\//g, m => m.replace(/[^\n]/g, ' '))
    // `[^:]` 避开 `https://`（URL 里的 `//` 不是注释起点）
    .replace(/(^|[^:])\/\/[^\n]*/gm, '$1')
}

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

describe('P1-16 — 主色实底的前景色随主色变', () => {
  it('★ 全仓无「不透明主色底 + 写死黑白前景」的组合', () => {
    const offenders: string[] = []
    for (const [path, src] of Object.entries(ALL_SOURCES)) {
      stripTsComments(src).split('\n').forEach((line, i) => {
        if (isBadAccentLine(line)) offenders.push(`${path.replace(/^\.\.\//, '')}:${i + 1}`)
      })
    }
    expect(offenders, `这些行的主色实底配了写死前景（dark 下对比度不足）：\n${offenders.join('\n')}`).toEqual([])
    expect(Object.keys(ALL_SOURCES).length).toBeGreaterThan(50)
  })

  it('对照：该组合会被抓到，而合法的白字不受误伤', () => {
    expect(isBadAccentLine('className="px-4 bg-accent text-white"')).toBe(true)
    expect(isBadAccentLine('className="px-4 bg-accent text-on-accent"')).toBe(false)
    // 透明变体不是实底 → 放行（它上面本来就不该用 on-accent 那种前景）
    expect(isBadAccentLine('className="bg-accent/10 text-white"')).toBe(false)
    // 遮罩底不是主色底 → 白字是**对的**，正是「一刀切禁类名」会误伤的那批
    expect(isBadAccentLine('className="bg-black/20 text-white"')).toBe(false)
    // hover 态底色与实底同行时，被匹配到的仍只有实底那一个
    expect(isBadAccentLine('className="bg-accent text-on-accent hover:bg-accent-hover"')).toBe(false)
    // 注释里「讲旧写法」的文字不算违规 —— 剥注释这件事本身要有区分力（模式 #38）：
    // 不剥的话 `lib/onAccent.ts` 的文件头会被判违规，而那正是最该留档的地方。
    const docComment = ['/**', ' * 修复前 33 处 bg-accent 一律配 text-white。', ' */', 'const a = 1'].join('\n')
    expect(stripTsComments(docComment).split('\n').some(isBadAccentLine)).toBe(false)
    // 但**代码行**里的违规剥完仍在（别把剥注释做成「怎么都不报」）
    expect(stripTsComments('const a = 1 // 说明\nconst b = "bg-accent text-white"')
      .split('\n').some(isBadAccentLine)).toBe(true)
  })

  it('★ accent 三参数都住在 CSS 里（JS 侧不复制色表）', () => {
    const css = stripCssComments(indexCss)
    // 亮度参数化：:root 一档 + dark 覆盖一档
    expect((css.match(/--accent-lit\s*:/g) || []).length).toBe(2)
    expect(css).toMatch(/--tk-accent:\s*hsl\([^;]*var\(--accent-lit\)/)
    // 反过来：主色亮度**不得**再写死数字 —— 写死则 JS 读不到，而解决办法只剩
    // 「在前端复制一份色表」，那正是模式 #54 的副本（加第 27 套配色时静默用错字色）
    expect(css).not.toMatch(/--tk-accent:\s*hsl\([^;]*,\s*\d+%\)/)
  })

  it('★ on-accent 前景 token 有定义，且兜底值只有一处', () => {
    const css = stripCssComments(indexCss)
    expect(css).toMatch(/--tk-on-accent:\s*#[0-9A-Fa-f]{3,8}/)
    expect(css).toContain('--color-on-accent: var(--tk-on-accent)')
    // 26 套配色 × 明暗两档各有各的最优前景，CSS 里给不出 52 个值 ——
    // 只能有一个首帧兜底，真实取值由 lib/onAccent.ts 算完内联写入
    expect((css.match(/--tk-on-accent\s*:/g) || []).length).toBe(1)
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
