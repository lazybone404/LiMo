/**
 * 对话侧卡片**形态**守卫（2026-10-17）。
 *
 * 这批改的全是样式层判据 —— TS 测不到布局，能钉住的只有两件事：
 *   1. 规则**在不在**：删掉一条 `:has()` 不会报错，只会静默退回旧形态；
 *   2. 规则**写成什么样**：数量分列靠特异性、语义块靠 `--block-edge`，
 *      写成别的样子同样零报错。
 *
 * 真正的观感只能实机验收（拖窄浮窗 / 切 apple·ink / 长列表 / 整组极短）。
 * 这里守的是「别在后续重构里把它删了或改回宽度驱动」。
 *
 * ⚠️ 读 CSS 前**剥注释**：本文件的注释**故意**写着 `border-left-width`、
 * `auto-fit` 这些「不要再这么写」的词（文件头与各段都在解释为什么改掉），
 * 不剥的话断言测的是注释措辞，而不是代码。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), 'utf-8')
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

const CSS = stripComments(read('src/styles/chat-blocks.css'))

describe('语义块 —— 四边框（2026-10-17 由左边框改）', () => {
  it('不再有左边框的加粗与着色（左边框是引用/强调的语言，不是带图标块的）', () => {
    // 失效形态：只删了 `border-left-width: 3px`，五个 `border-left-color` 还在 ——
    // 边框变回 `--card-border` 的 1px，颜色却仍是语义色，看起来像「1px 彩色边框」，
    // 与设计稿差一个档位且没人能一眼看出是哪一处没改。
    expect(CSS).not.toContain('border-left-width')
    expect(CSS).not.toContain('border-left-color')
  })

  it('`.chat-block` 自持 `1px solid` 四边框，不跟随 `--card-border`', () => {
    // 跟随 `--card-border` 的失效形态：apple / ink 两套方案把它设成 `none`，
    // 语义块只剩一个图标悬空 —— 而其余方案下一切正常，切方案才发现。
    expect(CSS).toContain('border: 1px solid var(--block-edge, var(--tk-border))')
  })

  it('五个变体各自定义 `--block-edge`', () => {
    for (const cls of ['.chat-info-card', '.chat-digest-card', '.chat-caution', '.chat-howto', '.chat-quote']) {
      const escaped = cls.replace('.', '\\.')
      expect(CSS).toMatch(new RegExp(`${escaped}\\s*\\{[^}]*--block-edge:`))
    }
  })

  it('`caution` 仍用 warning 而不是 error（严重度由文字承担）', () => {
    expect(CSS).toMatch(/\.chat-caution\s*\{[^}]*var\(--tk-warning\)/)
    expect(CSS).not.toContain('--tk-error')
  })
})

describe('卡片组 —— 列数按张数，不按宽度', () => {
  it('`auto-fit minmax` 已删除（宽度驱动 = 同一内容在两台机器上长得不一样）', () => {
    expect(CSS).not.toContain('auto-fit')
  })

  it('2 / 3 / 4 张的分列规则各就各位，且用 `:not()` 排除下一档', () => {
    // ⚠️ 两条同时成立时靠 `:not()` 分出胜负，不依赖书写顺序 —— 原示例页是
    // 「后写的胜出」，挪一下规则就静默改列数。
    expect(CSS).toContain('.chat-cards:has(> :nth-child(2)):not(:has(> :nth-child(3)))')
    expect(CSS).toContain('.chat-cards:has(> :nth-child(3)):not(:has(> :nth-child(4)))')
    expect(CSS).toContain('.chat-cards:has(> :nth-child(4)):not(:has(> :nth-child(5)))')
  })

  it('5 张以上落回单列（默认规则就是 `1fr`，没有 `nth-child(5)` 的三列规则）', () => {
    expect(CSS).toMatch(/\.chat-cards\s*\{[^}]*grid-template-columns: 1fr/)
    expect(CSS).not.toMatch(/\.chat-cards:has\(> :nth-child\(5\)\)/)
  })

  it('窄浮窗兜底：容器查询 + `!important`（压过 :has 规则的具体性）', () => {
    // 没有 `container-type` 的话 `@container` 整块被忽略 —— 兜底静默消失。
    expect(CSS).toMatch(/\.chat-card-group\s*\{[^}]*container-type: inline-size/)
    expect(CSS).toContain('@container (max-width: 320px)')
    expect(CSS).toMatch(/@container \(max-width: 320px\)\s*\{[\s\S]*?grid-template-columns: 1fr !important/)
  })
})

describe('篇幅变体 —— 结构判据，不退化', () => {
  it('整组极短的规则**带** `:has(> .chat-card)` 限定（省掉 = 误伤其他网格）', () => {
    // 省略后它会命中任何「子元素里没有 p/ul/ol」的网格，静默改掉 gap。
    expect(CSS).toContain('.chat-cards:has(> .chat-card):not(:has(> .chat-card > p))')
  })

  it('单张只有标题 → 垂直居中（不与邻居等高时标题挂顶上）', () => {
    expect(CSS).toMatch(/\.chat-card:not\(:has\(p\)\):not\(:has\(ul\)\):not\(:has\(ol\)\)\s*\{[^}]*justify-content: center/)
  })

  it('两段 → 段距收紧', () => {
    expect(CSS).toContain('.chat-card:has(> p + p) > p')
  })

  it('4 个内容块以上 → 长内容卡变种（有头面板，**不**去外壳）', () => {
    // ⚠️ `nth-child(5)` 不是 (4)：`.chat-card` 的第一个子元素恒为复制按钮
    // （流式期间 `display: none` 但仍占节点）。写成 (4) 时「标题 + 两段」这种
    // 规范**鼓励**的写法会提前一格触发变种并整组退列 —— 这条守卫钉住「数的是
    // DOM 子节点、含按钮」。
    expect(CSS).toMatch(/\.chat-card:has\(> :nth-child\(5\)\)\s*\{[^}]*padding: 0/)
    expect(CSS).toMatch(/\.chat-card:has\(> :nth-child\(5\)\) > h3\s*\{[^}]*border-bottom:/)
    // 内缩覆盖「非 h3 的一切内容元素」（代码块 / 表格同样要内缩），
    // 但必须排除复制按钮（absolute，margin 会改变定位）。
    expect(CSS).toMatch(
      /\.chat-card:has\(> :nth-child\(5\)\) > :not\(h3\):not\(\.chat-card-copy\)\s*\{[^}]*margin-left/,
    )
    // 反向断言：没有被拆回普通段落的规则（`display: contents` 会让外框消失，
    // 那正是用户明确否掉的「退化成分节正文」）。
    expect(CSS).not.toContain('display: contents')
  })

  it('整组退列：长内容卡 / 7 项以上列表', () => {
    expect(CSS).toContain('.chat-cards:has(> .chat-card > :nth-child(5))')
    expect(CSS).toContain('.chat-cards:has(> .chat-card > ul > li:nth-child(7))')
    expect(CSS).toContain('.chat-cards:has(> .chat-card > ol > li:nth-child(7))')
  })

  it('长串与标题：`overflow-wrap` / `text-wrap`', () => {
    // `.chat-card` 与 `.chat-block-body` 各一处 —— 前者管卡片组，后者管语义块；
    // 只写一处的话另一类块里的长 URL 照样撑破（零报错）。
    expect((CSS.match(/overflow-wrap: break-word/g) ?? []).length).toBeGreaterThanOrEqual(2)
    expect(CSS).toContain('text-wrap: balance')
  })
})

describe('结构判据与 DOM 同源（2026-10-18）', () => {
  // ⚠️ 读组件源码而不是渲染快照：要钉住的正是「按钮不被条件挂载」这个结构事实。
  // 若有人把卡片里的按钮改回 `{!streaming && <ChatCopyButton/>}`，CSS 的
  // `:nth-child(5)` 会在流式与结束时数出不同的子节点数 —— 卡片在流式结束那一帧
  // 跳变并让整组退列，且零报错、tsc 与其余测试全绿。
  const COMPONENT = read('src/components/ai-chat/ChatMarkerContent.tsx')
  const cardBody = (() => {
    const start = COMPONENT.indexOf('function ChatCard(')
    const end = COMPONENT.indexOf('\nfunction ', start + 1)
    return COMPONENT.slice(start, end === -1 ? undefined : end)
  })()

  it('`ChatCard` 的复制按钮在流式期间**隐藏不卸载**（判据靠它恒占一个子节点）', () => {
    expect(cardBody).toContain('chat-card-copy--streaming')
    // 反向：函数体内不得再出现 `!streaming` —— 那是「条件挂载」的信号。
    // ⚠️ 只查 ChatCard 的函数体：ChatPre 的代码块按钮**应该**是条件挂载的
    // （它不参与任何 nth-child 判据），全局断言会误伤。
    expect(cardBody).not.toContain('!streaming')
  })

  it('隐藏用自带类名（(0,2,0)）而不是 Tailwind `hidden`（(0,1,0) 胜负取决于加载顺序）', () => {
    expect(CSS).toMatch(/\.chat-card-copy\.chat-card-copy--streaming\s*\{[^}]*display: none/)
  })
})

describe('设置轴 —— 该跟随的跟随、该自持的自持', () => {
  it('尺寸仍是 em（跟随对话字号三档），不套用宽栏的 `--card-padding`', () => {
    // `--card-padding` 是 22px 26px 的宽栏值，浮窗里会把卡片撑爆；
    // 而 em 跟着 `--chat-font-scale` 一起缩 —— 这是「质感跟随、尺寸不跟随」的分界。
    expect(CSS).not.toContain('var(--card-padding)')
    expect(CSS).not.toContain('var(--card-gap)')
  })

  it('卡片组里的卡仍完整跟随方案（border 也跟随）', () => {
    expect(CSS).toMatch(/\.chat-card\s*\{[^}]*border: var\(--card-border\)/)
  })
})
