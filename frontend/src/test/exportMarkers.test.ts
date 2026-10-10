/**
 * 语义标记在**导出链路**上的接线守卫（前端侧）。
 *
 * 这些链路的共同失败形态是「**加了开关但没接线**」——开关本身、CSS、属性名全都对，
 * 唯独某个调用点没把它发出去，于是「关掉装饰后导出的 HTML 仍然带装饰」、
 * 「.md 文件里留着一行标记」，而整条链上零报错、零异常，只有对照实机才看得出来。
 *
 * 另一半是**跨语言同源**：属性名（`data-marker-decor`）、取值（`off`）、
 * query 参数名与取值（`marker_decor` / `"0"`）分别写在 TS 与 Python 两个文件里，
 * 拼错任一个的症状都是「开关静默失效」。照 `appShortcutKeys.test.ts`（读 Rust 源码）
 * 与 `cardPrefs.test.ts`（读 export.py）的先例，由机器核对 —— 本项目 #54 号模式：
 * 写下「N 处同源」的那一刻就欠了一次全仓验证。
 *
 * ⚠️ 读 Python 源码前**必须剥掉 `#` 注释**：那边的好几处注释**故意**写着
 * `data-marker-decor="off"` 当说明（提醒后来者别再发明第四种写法），
 * 不剥的话断言在实现被删光后照样绿。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), 'utf-8')
/** 剥 Python 的整行 `#` 注释（行尾注释留着无害：真正的代码在同一行上） */
const py = (rel: string) => read(rel).replace(/^\s*#.*$/gm, '')

const EXPORT_PY = py('../backend/app/routers/export.py')
/** 块包装的 HTML 由这里产出（`render_blocks_html`）—— 分段与包裹的唯一实现 */
const BLOCKS_PY = py('../backend/app/core/artifact_blocks.py')
const MENU_TSX = read('src/components/ui/ExportMenu.tsx')
const PANEL_TSX = read('src/components/ui/ExportPanel.tsx')
const PREFS_TS = read('src/lib/marker-prefs.ts')

describe('跨语言同源 — 属性名与取值', () => {
  it('导出的 HTML 写的就是 `data-marker-decor="off"`', () => {
    // 与 `lib/marker-prefs.ts::applyMarkers` 逐字同源。拼错（比如改成
    // `data-marker-decor-off`）→ CSS 选择器永不命中 → 开关**什么都不发生**。
    expect(EXPORT_PY).toContain('data-marker-decor="off"')
    expect(PREFS_TS).toContain("data-marker-decor")
    expect(PREFS_TS).toContain("'off'")
  })

  it('块包装的类名与 `data-block-type` 两侧同源', () => {
    // `blocks.css` 的选择器是 `.md-block[data-block-type="…"]` —— 导出侧少写一个
    // 属性就是「块被包了但没有样式」。
    expect(BLOCKS_PY).toContain('class="md-block"')
    expect(BLOCKS_PY).toContain('data-block-type=')
    const css = read('src/styles/blocks.css')
    expect(css).toContain('.md-block[data-block-type=')
  })

  it('导出内联的 CSS 就是 `src/styles/blocks.css` 这一份', () => {
    // ⚠️ 后端从磁盘读它并内联 —— 如果哪天有人为了「后端方便」放进一份副本，
    // 预览与导出就开始各改各的（而两边看起来都对）。
    expect(EXPORT_PY).toContain('styles/blocks.css')
    // 自证路径真的存在：上面那条只是字符串匹配，文件改名了它照样绿
    expect(() => read('src/styles/blocks.css')).not.toThrow()
  })
})

describe('跨语言同源 — 导出开关的参数', () => {
  it('后端读的 query 参数名与前端发的一致', () => {
    // ⚠️ 断言**整条表达式**而不是分开两截搜：`!= "0"` 在 export.py 里不止一处，
    // 分开搜的话「参数名被改掉、判据还在别处」这一整类缺陷照样绿。
    expect(EXPORT_PY).toMatch(/query_params\.get\("marker_decor",\s*"1"\)\s*!=\s*"0"/)
    expect(MENU_TSX).toContain("if (!markerDecor) p.set('marker_decor', '0')")
  })

  it('导出侧写属性时带 `blocks_present` 闸门', () => {
    // 零回归的机制所在：无标记的产物导出**不写**这个属性（`data-marker-decor`
    // 缺失即默认），于是选择器不匹配、与改造前逐字节相同。闸门掉了不报错 ——
    // 只是每份无标记的导出 HTML 都悄悄多一个属性。
    expect(EXPORT_PY).toMatch(/if\s+blocks_present\s+and\s+not\s+marker_decor:/)
  })

  it('批量导出（ExportPanel）把开关带进请求体', () => {
    expect(PANEL_TSX).toContain('marker_decor: markerDecor')
  })
})

describe('导出页的令牌注入', () => {
  it('`_CARD_TOKENS_CSS` 的注入条件含 `blocks_present`', () => {
    // ⚠️ **真实缺陷的回归守卫**（本轮修掉的那个）：导出主题 `HTML_THEMES` 一个
    // `--tk-*` 都不提供，该常量是**唯一的提供者**。它原先只在 `skeleton == "card"`
    // 时注入，于是 `paper` + 有标记时 `blocks.css` 的
    // `border-left: 3px solid var(--tk-accent)` 整条声明作废 ——
    // **导出 HTML 里强调块一点样式都没有，而应用内预览完全正常、零报错**。
    expect(EXPORT_PY).toMatch(/if\s+skeleton\s*==\s*"card"\s+or\s+blocks_present:/)
  })
})

describe('客户端导出 — 标记行必须被剥掉', () => {
  it('Markdown 下载前剥标记', () => {
    // ⚠️ MD 与复制是**客户端直读 store** 的两条路，后端碰不到它们 ——
    // 后端那三条（html/word/pdf）测不到这里的漏接线。
    expect(MENU_TSX).toContain('stripMarkers(exportContent)')
  })

  it('复制到剪贴板前同样剥标记', () => {
    // 「复制」的语义是「复制用户看到的内容」，而标记行在预览里本就不显示
    const copyBlock = MENU_TSX.slice(MENU_TSX.indexOf('const handleCopy'))
    expect(copyBlock).toContain('stripMarkers(')
  })
})
