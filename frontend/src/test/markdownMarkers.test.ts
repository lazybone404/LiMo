/**
 * `limo-block` 标记解析器 —— 表驱动读**跨语言共享 fixture**。
 *
 * 解析规则有两份实现（本仓 JS 一份、后端 Python 一份，因为导出要跟随），它们必然分开
 * 演化。`src/test/fixtures/limo-blocks.json` 是两侧**唯一的一致性证据** —— 后端
 * `tests/test_artifact_blocks.py` 读同一份文件。「两边写得像」不是证据。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 逐行扫但不跳围栏 → 用户示例代码里的标记文本被当成真标记 → **静默改写用户内容**。
 *   B. 未闭合时把内容也吞掉 → 「模型少写一行闭标记」= 读者**永久看不到那段结论**，
 *      且产物上看不出少了什么（这正是本协议最重要的一条不变量要挡的）。
 *   C. 未知协议版本当成正常开标记处理 → v2 的块被 v1 渲染器用**错的语义**包起来。
 *   D. 块内围栏里的闭标记被当成闭合 → 块提前结束，后半段变成块外正文（形态错、零报错）。
 *   E. 无标记时也走「规范化 + 重新拼接」→ CRLF 产物被静默改成 LF（输出与输入不再逐字相同，
 *      而这条路径是所有旧产物的必经之路）。
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { hasBlocks, parseMarkers, stripMarkers } from '../lib/markdown-markers'
import { ARTIFACT_BLOCK_TYPES, CHAT_BLOCK_TYPES, KNOWN_BLOCK_TYPES, PROTOCOL_VERSION } from '../lib/markdown-scan'
import { splitByH2 } from '../lib/markdown-sections'

/**
 * ⚠️ 用 `process.cwd()` 而非 `import.meta.url`：本文件跑在 jsdom 环境。
 * 与 `markdownSections.test.ts` 读同一目录的 fixture 同款。
 */
const FIXTURE_PATH = resolve(process.cwd(), 'src/test/fixtures/limo-blocks.json')
const FIXTURE = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8')) as {
  meta: { protocolVersion: string; knownTypes: string[]; artifactTypes: string[]; chatTypes: string[] }
  cases: {
    name: string
    md: string
    segments: { kind: string; type?: string; content: string }[]
    stripped: string
  }[]
  h2Cases: { name: string; md: string; preamble: string; sections: { heading: string; body: string }[] }[]
}

describe('limo-block — 白名单与协议版本的跨语言守卫', () => {
  it('KNOWN_BLOCK_TYPES 与 fixture 的 knownTypes 一致（加类型时漏改一侧即红）', () => {
    expect([...KNOWN_BLOCK_TYPES].sort()).toEqual([...FIXTURE.meta.knownTypes].sort())
  })

  it('KNOWN = ARTIFACT ∪ CHAT，且两两不相交 —— 「协议知道」≠「某侧实现了」', () => {
    // ⚠️ 挡的是**静默失效**：把一个 type 加进 KNOWN_BLOCK_TYPES 等于宣称「有人处理它」，
    // 而某个渲染方其实没有分支 → 该 type 被原样当正文吐出来、零报错。
    // 这里核对三者关系；**各侧是否真有渲染分支**由 chatBlocks.test.tsx /
    // markerContent.test.tsx 的渲染断言承担（两条一起才闭环）。
    expect([...ARTIFACT_BLOCK_TYPES].sort()).toEqual([...FIXTURE.meta.artifactTypes].sort())
    expect([...CHAT_BLOCK_TYPES].sort()).toEqual([...FIXTURE.meta.chatTypes].sort())
    expect([...KNOWN_BLOCK_TYPES].sort()).toEqual(
      [...new Set([...ARTIFACT_BLOCK_TYPES, ...CHAT_BLOCK_TYPES])].sort(),
    )
    for (const t of ARTIFACT_BLOCK_TYPES) {
      expect(CHAT_BLOCK_TYPES.has(t), `两侧类型重叠：${t}`).toBe(false)
    }
  })

  it('PROTOCOL_VERSION 与 fixture 一致', () => {
    expect(PROTOCOL_VERSION).toBe(FIXTURE.meta.protocolVersion)
  })
})

describe('parseMarkers — 跨语言共享 fixture', () => {
  it('fixture 至少覆盖到那几类关键行为（防止用例被删空后整组测试静默变成空跑）', () => {
    expect(FIXTURE.cases.length).toBeGreaterThanOrEqual(11)
    const names = FIXTURE.cases.map(c => c.name).join('\n')
    expect(names).toContain('无标记')     // E
    expect(names).toContain('未闭合')     // B
    expect(names).toContain('未知协议版本') // C
    expect(names).toContain('围栏内')     // A
    expect(names).toContain('不算闭合')   // D
    expect(names).toContain('CRLF')
    expect(names).toContain('卡片组')     // 对话侧新类型
    expect(names).toContain('追问')
  })

  for (const c of FIXTURE.cases) {
    it(c.name, () => {
      expect(parseMarkers(c.md)).toEqual(c.segments)
    })

    it(`${c.name} —— stripMarkers 同源`, () => {
      expect(stripMarkers(c.md)).toBe(c.stripped)
    })
  }
})

describe('parseMarkers — 结构不变量', () => {
  it('无标记时只产出**一个** text 段，且除 CRLF 规范化外零改动（零回归）', () => {
    const segs = parseMarkers('# 标题\n\n正文\n第二行\n')
    expect(segs).toHaveLength(1)
    expect(segs[0].kind).toBe('text')
    expect(segs[0].content).toBe('# 标题\n\n正文\n第二行\n')
  })

  it('无标记时 CRLF 被规范化（与 splitByH2 同款既定行为），段数仍为 1', () => {
    const segs = parseMarkers('正文\r\n第二行')
    expect(segs).toHaveLength(1)
    expect(segs[0].content).toBe('正文\n第二行')
  })

  it('无标记时 stripMarkers 返回**同一个字符串**（连 CRLF 都不动 —— 它是旧产物的必经路径）', () => {
    const md = '普通文章\r\n\r\n没有任何标记'
    expect(stripMarkers(md)).toBe(md)
  })

  it('未闭合降级后与「从未写过标记」逐字相同（内容一字不丢）', () => {
    const withMarker = '正文一\n\n<!-- limo-block:v1 type="key-takeaway" -->\n块内文字\n块内第二行'
    const never = '正文一\n\n块内文字\n块内第二行'
    expect(stripMarkers(withMarker)).toBe(never)
  })

  it('块内容是原文，不 trim、不剥加粗（块内渲染必须与块外同一条路径）', () => {
    const segs = parseMarkers('<!-- limo-block:v1 type="key-takeaway" -->\n\n  **甲**  \n\n<!-- /limo-block -->')
    expect(segs).toHaveLength(1)
    expect(segs[0]).toMatchObject({ kind: 'block', content: '\n  **甲**  \n' })
  })

  it('stripMarkers 幂等', () => {
    for (const c of FIXTURE.cases) {
      const once = stripMarkers(c.md)
      expect(stripMarkers(once)).toBe(once)
    }
  })

  it('hasBlocks 是子串判定（误报只多走慢路径，漏报才是 bug）', () => {
    expect(hasBlocks('提到 limo-block 这个词')).toBe(true)
    expect(hasBlocks('普通正文')).toBe(false)
  })
})

describe('splitByH2 × 标记块 —— 块内的 `##` 不是文档结构', () => {
  for (const c of FIXTURE.h2Cases) {
    it(c.name, () => {
      expect(splitByH2(c.md)).toEqual({ preamble: c.preamble, sections: c.sections })
    })
  }
})
