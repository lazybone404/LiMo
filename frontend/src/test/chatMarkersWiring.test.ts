/**
 * `limo-block` 在**对话链路**上的接线守卫 —— 与 `exportMarkers.test.ts`（产物链路）成对。
 *
 * 这些链路的共同失败形态是「**协议加了、渲染器也写了，但某个消费点没接上**」：
 * 常量对、CSS 对、组件对，唯独导出时忘了剥、或者 prompt 写了却没拼进 `build()`。
 * 全程零报错，只有对照实机才看得出来。
 *
 * 另一半是**跨语言同源**（模式 #54）：类型集合写在 TS 与 Python 两个文件里，
 * 拼错任一个的症状都是「协议认识它、某一侧却不认」。
 *
 * ⚠️ 读 Python 源码前**剥掉整行 `#` 注释**：那边有好几处注释**故意**写着
 * `limo-block` 当说明，不剥的话断言在实现被删光后照样绿。
 */
/// <reference types="node" />
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const read = (rel: string) => readFileSync(resolve(process.cwd(), rel), 'utf-8')
/** 剥 Python 的整行 `#` 注释（行尾注释留着无害：真正的代码在同一行上） */
const py = (rel: string) => read(rel).replace(/^\s*#.*$/gm, '')

/**
 * 剥 TS/CSS 的块注释与行注释。
 *
 * ⚠️ 只在**判据是「这里不该出现某个标识符」**时用：注释里**故意**写着那个词
 * （提醒后来者别这么干）是这些文件的常态，不剥的话断言测的是注释的措辞。
 */
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

/**
 * 按 `def` 把 Python 源码切成 `{ 函数名: 函数体源码 }`（含缩进的类方法）。
 *
 * ⚠️ 为什么要切：`export.py` 里 `_strip_blocks(` 出现 6 次，全文件搜字符串的话
 * 「对话导出那两处漏接线、别处还在用」这一整类缺陷**照样绿**（`exportMarkers.test.ts`
 * 里对 `!= "0"` 有同样的说明）。
 *
 * ⚠️ **同名会互相覆盖**（后出现的胜）—— 只用于取「在这个文件里名字唯一」的函数，
 * 别拿它去查 `run` / `get` 这类通用名。
 */
function pyFuncs(src: string): Record<string, string> {
  const marks: { name: string; start: number }[] = []
  for (const m of src.matchAll(/^[ \t]*(?:async )?def (\w+)\(/gm)) marks.push({ name: m[1], start: m.index! })
  const out: Record<string, string> = {}
  marks.forEach((mk, i) => {
    out[mk.name] = src.slice(mk.start, i + 1 < marks.length ? marks[i + 1].start : src.length)
  })
  return out
}

const EXPORT_FNS = pyFuncs(py('../backend/app/routers/export.py'))
const SCAN_TS = read('src/lib/markdown-scan.ts')
const BLOCKS_PY = py('../backend/app/core/artifact_blocks.py')
const CHAT_TSX = read('src/components/ai-chat/ChatMarkerContent.tsx')
const MESSAGE_ITEM = read('src/components/ai-chat/MessageItem.tsx')
const CHAT_API = read('src/api/chat.ts')
const MAIN_TSX = read('src/main.tsx')
const CHAT_CSS = read('src/styles/chat-blocks.css')
const FORMATS_MD = read('../backend/app/agent/prompts/formats.md')
const PROMPTS_PY = py('../backend/app/agent/prompts/__init__.py')

describe('跨语言同源 — 类型集合', () => {
  it('两侧都拆成「产物侧 / 对话侧 / 全集」三个集合', () => {
    for (const src of [SCAN_TS, BLOCKS_PY]) {
      expect(src).toContain('ARTIFACT_BLOCK_TYPES')
      expect(src).toContain('CHAT_BLOCK_TYPES')
      expect(src).toContain('KNOWN_BLOCK_TYPES')
    }
  })

  it('两个新类型两侧都登记', () => {
    for (const src of [SCAN_TS, BLOCKS_PY]) {
      expect(src).toContain('card-group')
      expect(src).toContain('follow-up')
    }
  })
})

describe('对话侧渲染 — 类型分派必须真的分派', () => {
  it('`ChatMarkerContent` 对两个类型各有一支', () => {
    // ⚠️ 断言**整条判据表达式**：只搜 `'card-group'` 的话，常量表、注释、测试用例
    // 都含这个词，渲染分支被删掉也照样绿 —— 而症状正是「模型写了标记，
    // 用户看到一行被剥掉的注释、正文里什么都没有」。
    expect(CHAT_TSX).toContain("seg.type === 'card-group'")
    expect(CHAT_TSX).toContain("seg.type === 'follow-up'")
  })

  it('按 `###` 切卡，且**用的就是共用的层级化切分器**（不另写一份扫描）', () => {
    // 另写一份的失效方式：漏掉围栏感知 → 代码块里的 `###` 切出幽灵卡，
    // 而两张卡看起来都正常、零报错。
    expect(CHAT_TSX).toContain('splitByHeading(')
    expect(read('src/lib/markdown-sections.ts')).toContain('splitByHeading')
  })

  it('对话样式刻意与产物强调块分家（`.chat-*` 而非 `.md-block`）', () => {
    // 复用 `.md-block` 会让对话正文命中 `blocks.css` 的产物视觉（左边框 + 淡底）。
    // ⚠️ **必须先剥注释**：这两份文件的注释**故意**写着 `md-block` 提醒后来者别这么干，
    // 整文件搜子串会被自己的注释弄红 —— 而注释一改又变绿，那种断言测的是注释不是代码。
    // （`test_export_blocks.py` 里剥 CSS 注释是同款理由。）
    expect(stripComments(CHAT_TSX)).not.toContain('md-block')
    expect(stripComments(CHAT_CSS)).not.toContain('.md-block')
  })
})

describe('MessageItem — 两个消费点', () => {
  it('正文走 `ChatMarkerContent`（不接线 = 协议文本原样显示给用户）', () => {
    expect(MESSAGE_ITEM).toContain('ChatMarkerContent')
    expect(MESSAGE_ITEM).toContain('<ChatMarkerContent')
  })

  it('复制前剥标记（「复制」= 复制用户看到的内容）', () => {
    const copyBlock = MESSAGE_ITEM.slice(MESSAGE_ITEM.indexOf('const copy ='))
    expect(copyBlock).toContain('stripMarkers(')
  })

  it('⚠️ 段落路径传 `streaming`，否则流式半截抑制失效', () => {
    // 半截标记行既不被当标记剥、也不会被当正文渲染 —— 它是**两不管**的一行，
    // 只有 `suppressTrailingPartialMarker` 认它，而它只在 `streaming` 为真时调用。
    expect(MESSAGE_ITEM).toMatch(/streaming=\{[^}]*streaming[^}]*\}/)
  })
})

describe('对话样式 — 接线与铁律', () => {
  it('`chat-blocks.css` 被 `main.tsx` 引入（漏了 = 卡片无边界、chips 像裸文本）', () => {
    expect(MAIN_TSX).toContain('styles/chat-blocks.css')
    expect(() => read('src/styles/chat-blocks.css')).not.toThrow()
  })

  it('零硬编码色值（项目铁律：全部走 `--tk-*`）', () => {
    // 六轴主题下裸 hex 会「换个基调就露出原色」。尺寸也一律用 em（跟随对话字号档）。
    expect(CHAT_CSS).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(CHAT_CSS).not.toMatch(/\brgba?\(/)
    expect(CHAT_CSS).toContain('var(--tk-')
  })

  it('这份 CSS **不进导出**（对话不导出，后端只内联 `blocks.css`）', () => {
    // 真接进去的话，导出侧会多出一份只为对话服务的样式，两处各改各的。
    expect(read('../backend/app/routers/export.py')).not.toContain('chat-blocks.css')
  })
})

describe('对话 MD 导出 — 两个函数都要剥', () => {
  it('单条导出剥标记（否则 .md 里留着 `<!-- limo-block … -->`）', () => {
    expect(EXPORT_FNS['export_conversation']).toContain('_strip_blocks(')
  })

  it('批量导出同样剥（只改单条、漏批量是同类缺陷的默认形态）', () => {
    expect(EXPORT_FNS['export_conversations']).toContain('_strip_blocks(')
  })

  it('用户消息也走同一条（用户手工编辑/粘贴的消息同样可能带标记）', () => {
    // 单条导出按 role 分了两支，只要有一支漏掉 `_strip_blocks` 就是「某一侧带着标记出去」；
    // 批量导出走 `**{role}**` 一行。断言到**具体那条 append**，而不是「文件里出现过」。
    const single = EXPORT_FNS['export_conversation']
    expect(single).toMatch(/\*\*用户\*\*.*_strip_blocks\(/)
    expect(single).toMatch(/\*\*AI\*\*.*_strip_blocks\(/)
    expect(EXPORT_FNS['export_conversations']).toMatch(/\*\*\{role\}\*\*.*_strip_blocks\(/)
  })
})

describe('prompt — 写了就要拼进去', () => {
  it('`formats.md` 含两个类型的可复制示例', () => {
    expect(FORMATS_MD).toContain('<!-- limo-block:v1 type="card-group" -->')
    expect(FORMATS_MD).toContain('<!-- limo-block:v1 type="follow-up" -->')
    expect(FORMATS_MD).toContain('<!-- /limo-block -->')
  })

  it('`PromptLoader` 读它、且**拼进 `build()`**', () => {
    // ⚠️ 只读不拼是最静默的一种：文件在、内容对、`__init__` 里赋值了，
    // 而 `build()` 的 parts 里没有它 → 模型永远不知道有这套协议，零报错。
    expect(PROMPTS_PY).toContain('formats.md')
    const build = pyFuncs(PROMPTS_PY)['build']
    expect(build).toContain('self.formats')
  })

  it('规范只教语义、不教外观（外观归主题系统管）', () => {
    for (const appearance of ['圆角', 'font-size', 'border-radius', 'background-color']) {
      expect(FORMATS_MD).not.toContain(appearance)
    }
  })
})

describe('实时引用 chips —— 顺带修的那处缺口', () => {
  it('`tool_result` 分支读 `data.refs`（此前只取 summary、把 refs 丢了）', () => {
    const branch = CHAT_API.slice(CHAT_API.indexOf("data.type === 'tool_result'"))
    expect(branch).toContain('data.refs')
    expect(branch).toContain('onRef')
  })

  it('后端从不发的 `ref` 事件分支已删除（死代码）', () => {
    expect(CHAT_API).not.toContain("data.type === 'ref'")
  })
})
