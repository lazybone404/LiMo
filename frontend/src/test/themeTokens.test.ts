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

/**
 * 主题色值的真相源（2026-10-16 档 A/B 抽出）。
 *
 * ⚠️ **读错文件不会报错，只会静默变空**：主题相关的声明（26 套 accent 原语、
 * 浅色底盘、`[data-base]` 13 条、暗色、`--md-*`、`[data-font]`、`[data-texture]`）
 * 已全部搬进 `styles/theme-layer.css`（因为同一份数据还要给后端导出用）。
 * 断言若还指着 `index.css`，「恰好 N 次」那类计数断言会红（`0 !== N`，这次是好的），
 * 而「**不含**违规写法」那类会**恒真** —— 判据的原料空了，它就永远说没问题（模式 #38）。
 */
const themeLayerCss = readFileSync(resolve(process.cwd(), 'src/styles/theme-layer.css'), 'utf-8')
const textureCss = readFileSync(resolve(process.cwd(), 'src/lib/texture.css'), 'utf-8')

/** 剥 CSS 注释 —— 本文件与源码里都可能**刻意**引用旧写法当反例 */
function stripCssComments(s: string): string {
  return s.replace(/\/\*[\s\S]*?\*\//g, '')
}

/**
 * 把 `@media … { … }` 的**外层包装剥掉**，让里面的规则平铺。
 *
 * ⚠️ 为什么必须有这一步：下面 `surfaceRules` 用的 `([^{}]+)\{([^}]*)\}` 处理不了嵌套 ——
 * 遇到 `@media screen { [data-theme="dark"] { … } }` 时 `([^}]*)` 会一路吃到**内层的**
 * `}`，于是「选择器」被算成 `@media screen`、`[data-theme="dark"]` 掉进「声明体」里。
 * 症状是 `darkAt()` **恒返回 -1**：判据不报错、只是再也测不到东西（模式 #38 的形态）。
 * 2026-10-16 给两个暗色块包上 `@media screen`（「暗色是屏幕概念，纸永远是浅的」）时踩到。
 *
 * 只做括号配对删除、不解析媒体查询语义 —— 这里要的仅是**规则之间的源顺序**。
 * 剥注释**之后**再调（注释里逐字写着 `@media …` 当说明）。
 */
function unwrapMedia(s: string): string {
  for (;;) {
    const at = s.indexOf('@media')
    if (at === -1) return s
    const open = s.indexOf('{', at)
    if (open === -1) return s            // 病灶输入：有头无体，原样返回
    let depth = 1
    let i = open + 1
    for (; i < s.length && depth > 0; i++) {
      if (s[i] === '{') depth++
      else if (s[i] === '}') depth--
    }
    // `i - 1` 即收尾的 `}`：它使 depth 归零、循环再自增一次才退出。
    s = s.slice(0, at) + s.slice(open + 1, i - 1) + s.slice(i)
  }
}

/** Tailwind 任意色值类：`bg-[#123456]` / `text-[#abc]` … */
const ARBITRARY_COLOR = /\b(?:bg|text|border|accent|ring|shadow|fill|stroke|from|to|via)-\[#/

/**
 * 同上的 `rgb()/rgba()` 写法（2026-10-16 补的缺口）。
 *
 * ⚠️ 此前只拦 hex，于是一个**有色相**的 rgba 能整条溜过去 ——
 * `shadow-[0_1px_3px_rgba(0,122,255,.2)]` 与 `.r-handle` 那个「换了配色
 * 拖拽条却闪一下蓝」的 bug 是同一个形态（只是当时写在 CSS 里、不在工具类里）。
 *
 * ⚠️ **纯黑 / 纯白刻意放行**：`rgba(0,0,0,.08)` 那种投影**不是主题色** ——
 * 投影用纯黑、遮罩用纯黑，跨主题语义完全相同，硬塞进 token 是假抽象。
 * 被拦的是**有彩度**的那种：它换了主题不会跟着变。
 */
const ARBITRARY_RGBA = /\b(?:bg|text|border|accent|ring|shadow|fill|stroke|from|to|via)-\[[^\]]*rgba?\((?!\s*(?:0\s*,\s*0\s*,\s*0|255\s*,\s*255\s*,\s*255)\s*,)/

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
      .filter(([, src]) => ARBITRARY_COLOR.test(src) || ARBITRARY_RGBA.test(src))
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
    // rgba 缺口（2026-10-16 补）：有彩度的会被抓到，纯黑投影**刻意放行**
    expect(ARBITRARY_RGBA.test('className="shadow-[0_1px_3px_rgba(0,122,255,.2)]"')).toBe(true)
    expect(ARBITRARY_RGBA.test('className="shadow-[0_1px_3px_rgba(0,0,0,.08)]"')).toBe(false)
    expect(ARBITRARY_RGBA.test('className="bg-[rgba(255,255,255,.5)]"')).toBe(false)
    // 反向自证：这条守卫真在扫（不是常量拼错导致的恒假）
    expect(ARBITRARY_RGBA.test('className="bg-[rgba(235,108,54,.1)]"')).toBe(true)
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

  it('主题 CSS 的裸 hex 只出现在 token 定义处', () => {
    // ⚠️ **三份都要查**（2026-10-16 起主题声明住在 `styles/theme-layer.css`）。
    // 只查 `index.css` 会退化成恒真 —— 那份文件里已经一个色值都不剩了，
    // 「不含裸 hex」当然成立，而真正的漂移会发生在没人看的那一份（模式 #38）。
    const files: [string, string][] = [
      ['src/index.css', indexCss],
      ['src/styles/theme-layer.css', themeLayerCss],
      ['src/lib/texture.css', textureCss],
    ]
    for (const [name, css] of files) {
      // 自证读到了文件（空串会让这条恒真）
      expect(css.length, `${name} 读成了空文件？`).toBeGreaterThan(400)
      // 判据：把每一处 `--xxx: #hex`（token 定义**必须**有字面值）抹掉后，
      // 再查是否还有 hex 残留。⚠️ 不能按「行首是不是 `--tk-`」判 ——
      // `[data-base="neutral"] { --tk-surface: #F5F5F5; --tk-elevated: #FCFCFC; ... }`
      // 是**一行里好几个 token 定义**，行首是选择器，那种写法会被整行误判成违规。
      const stripped = stripCssComments(css)
        .replace(/--[\w-]+:\s*#[0-9A-Fa-f]{3,8}\b/g, '--token: (value)')
      const offenders = stripped
        .split('\n')
        .map((line, i) => [i + 1, line.trim()] as const)
        .filter(([, line]) => /#[0-9A-Fa-f]{3,8}\b/.test(line))
        .map(([n, line]) => `${name}:${n}: ${line}`)
      expect(offenders, `这些行的 hex 不在 token 定义位置：\n${offenders.join('\n')}`).toEqual([])
    }
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
    const css = stripCssComments(themeLayerCss)
    expect(css.length).toBeGreaterThan(1000)   // 自证读到的是主题层，不是空串
    // 亮度参数化：:root 一档 + dark 覆盖一档
    expect((css.match(/--accent-lit\s*:/g) || []).length).toBe(2)
    expect(css).toMatch(/--tk-accent:\s*hsl\([^;]*var\(--accent-lit\)/)
    // 反过来：主色亮度**不得**再写死数字 —— 写死则 JS 读不到，而解决办法只剩
    // 「在前端复制一份色表」，那正是模式 #54 的副本（加第 27 套配色时静默用错字色）
    expect(css).not.toMatch(/--tk-accent:\s*hsl\([^;]*,\s*\d+%\)/)
  })

  it('★ on-accent 前景 token 有定义，且兜底值只有一处', () => {
    const css = stripCssComments(themeLayerCss)
    expect(css).toMatch(/--tk-on-accent:\s*#[0-9A-Fa-f]{3,8}/)
    // 桥令牌留在 `@theme`（Tailwind 构建指令，刻意不搬进主题层）
    expect(stripCssComments(indexCss)).toContain('--color-on-accent: var(--tk-on-accent)')
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

/**
 * ★ 界面基调（`data-base`）三块的**相对顺序** —— 2026-10-16 修的失效 bug 的守卫。
 *
 * **修复的是什么**：`[data-base="…"]` 13 条规则写在 `:root` **之前**，而两者
 * 特异性**同为 (0,1,0)**、作用在**同一个元素**（`<html>`）上、写的是**同 5 个
 * 底色变量**（`--tk-surface` / `--tk-elevated` / `--tk-hover` / `--tk-border` /
 * `--tk-border-light`）⇒ 后声明的 `:root` 把基调值**整组盖回 warm**，
 * **界面基调整轴失效**（自 `e3a9027` 引入起，`THEME_PRESETS` 的 base 轴同样从未生效）。
 *
 * **为什么它活了很久**：所有**预览**都是对的 —— 设置页基调色块是内联
 * `style={{background: b.surface}}`（写死 hex）、向导预设卡片把 `data-base` 设在
 * **非根元素**上（`[data-base="…"]` 在那里**直接匹配、生效** —— 继承值永远输给
 * 直接声明），只有「应用到 `<html>`」这一条路被盖住。
 *
 * ⚠️ **判据是「顺序」而不是「某段文本在场」**：这三块全都在文件里、一个字都没少，
 * 单看任何一块都发现不了问题（模式 #54 的形态）。所以这里比的是**出现次序**。
 */
describe('★ data-base 必须住在 :root 之后、dark 之前（层叠顺序）', () => {
  /**
   * 判据实体：一份 CSS 里所有「定义了 --tk-surface 的规则」，按文档出现顺序。
   * ⚠️ `var(--tk-surface)` 不算定义（正则要求 `--tk-surface` 紧跟 `:`），
   *    故 `@theme` 里的 `--color-surface: var(--tk-surface)` 不会被误收。
   * **真实文件与对照实验共用这一份**（复制第二份表达式 = 模式 #54）。
   */
  const surfaceRules = (css: string) =>
    [...unwrapMedia(stripCssComments(css)).matchAll(/([^{}]+)\{([^}]*)\}/g)]
      .filter(m => /--tk-surface\s*:/.test(m[2]))
      .map(m => m[1].trim().replace(/\s+/g, ' '))

  /**
   * ⚠️ 2026-10-16 档 A 之后，**默认底盘与 `warm` 是同一条规则**
   * （`:root, [data-base="warm"]`）—— 因为 `data-base="warm"` 必须有真规则命中，
   * 否则设置页那张暖灰基调卡只能继承 `<html>` 的 `--tk-surface`（14 张里错 1 张）。
   * 于是判据里「底盘」与「第一条 base」的 index **相等**是**正常**的。
   */
  const isRootRule = (s: string) => s.split(',').some(sel => sel.trim() === ':root')
  const isBaseRule = (s: string) => s.includes('[data-base=')
  const firstRoot = (r: string[]) => r.findIndex(isRootRule)
  const firstBase = (r: string[]) => r.findIndex(isBaseRule)
  /** **最后一条** base —— 判 dark 的位置必须用它，见下面那条对照实验。 */
  const lastBase = (r: string[]) => r.reduce((acc, s, i) => (isBaseRule(s) ? i : acc), -1)
  const darkAt = (r: string[]) => r.findIndex(s => s.includes('[data-theme="dark"]'))

  const rules = surfaceRules(themeLayerCss)
  const iRoot = firstRoot(rules)
  const iBase = firstBase(rules)
  const iLastBase = lastBase(rules)
  const iDark = darkAt(rules)

  it('原料自证：三块都找得到（否则下面两条会恒真 —— 模式 #38）', () => {
    expect(iRoot, `:root 未找到，实际规则：${rules.join(' | ')}`).toBeGreaterThanOrEqual(0)
    expect(iBase, '[data-base=…] 未找到').toBeGreaterThanOrEqual(0)
    expect(iLastBase, '最后一条 [data-base=…] 未找到').toBeGreaterThanOrEqual(0)
    expect(iDark, '[data-theme="dark"] 未找到').toBeGreaterThanOrEqual(0)
  })

  it('★ 基调不早于默认底盘 —— 否则 5 个底色变量被 warm 整组盖回（整轴失效）', () => {
    // ⚠️ 从 `>` 放宽到 `>=`：底盘与 warm 现在是同一条规则（见上方注释）。
    // `firstBase` 是**第一条** base —— 只要没有任何 base 规则排在底盘之前，
    // 原来 `>` 守住的那件事就仍然成立。
    expect(iBase).toBeGreaterThanOrEqual(iRoot)
  })

  it('★ dark 在**全部**基调之后 —— 否则暗色模式下浅色基调会盖到深底上', () => {
    // 基调是**浅色模式**的底色偏色，刻意不给一套深色值：
    // 靠 dark 规则排在其后来「让位」，而不是靠两边取值恰好不同。
    // ⚠️ 比的是**最后一条** base 而非第一条 —— 比第一条时，
    // 把 dark 插进 14 条 base 中间照样绿（下面有对照实验钉住这点）。
    expect(iDark).toBeGreaterThan(iLastBase)
  })

  it('对照：调换成「base 在 :root 之前」（即修复前的真实顺序），上面的判据确实会红', () => {
    const swapped = surfaceRules(`
      [data-base="neutral"] { --tk-surface: #F5F5F5; }
      :root { --tk-surface: #F4F1EE; }
    `)
    expect(swapped).toHaveLength(2)
    expect(firstBase(swapped)).toBeLessThan(firstRoot(swapped))   // 坏顺序：判据为假
    expect(iBase).toBeGreaterThanOrEqual(iRoot)                   // 真实文件：判据为真
  })

  it('对照：dark 插在基调中间也会红（只比「第一条 base」是不够的）', () => {
    const midDark = surfaceRules(`
      :root, [data-base="warm"] { --tk-surface: #F4F1EE; }
      [data-theme="dark"] { --tk-surface: #161618; }
      [data-base="blush"] { --tk-surface: #F7F1F2; }
    `)
    expect(darkAt(midDark)).toBeGreaterThan(firstBase(midDark))        // 弱判据为真（被骗过）
    expect(darkAt(midDark)).toBeLessThanOrEqual(lastBase(midDark))     // 强判据为假（抓得住）
  })
})
