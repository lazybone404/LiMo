/**
 * 对话卡片块的内容解析 —— `follow-up` 的列表项抽取。**纯函数，无 React**。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 不跳围栏 → 块内代码示例里的 `- x` 变成一颗可点击的追问按钮
 *      （模型经常在解释「怎么写追问」时贴一段示例），且零报错。
 *   B. 解析失败时抛错 / 返回占位 → 「模型写残了」会变成一条报错或一句假问题；
 *      正确行为是返回 `[]`，由调用方**退化为普通正文**（内容一字不丢）。
 *   C. 不截断上限 → 模型失控列 20 条时，输入区被 20 颗按钮挤爆。
 */

import { describe, it, expect } from 'vitest'
import { MAX_FOLLOW_UPS, parseFollowUpItems } from '../lib/chat-block-content'

describe('parseFollowUpItems — 各种列表标记', () => {
  it('无序列表三种标记都认', () => {
    expect(parseFollowUpItems('- 甲\n* 乙\n+ 丙')).toEqual(['甲', '乙', '丙'])
  })

  it('有序列表两种写法都认', () => {
    expect(parseFollowUpItems('1. 甲\n2. 乙')).toEqual(['甲', '乙'])
    expect(parseFollowUpItems('1) 甲\n2) 乙')).toEqual(['甲', '乙'])
  })

  it('项文本保留行内 Markdown（渲染层负责显示，解析层不做美化）', () => {
    expect(parseFollowUpItems('- **为什么**要用它？')).toEqual(['**为什么**要用它？'])
    expect(parseFollowUpItems('- `code` 怎么写？')).toEqual(['`code` 怎么写？'])
  })

  it('缩进与尾随空白被归一（项文本两侧不留空白）', () => {
    expect(parseFollowUpItems('  -   甲   ')).toEqual(['甲'])
  })
})

describe('parseFollowUpItems — 该忽略的', () => {
  it('空行、非列表行、纯文本都被跳过', () => {
    expect(parseFollowUpItems('这是一段说明\n\n- 甲\n\n再一段说明')).toEqual(['甲'])
  })

  it('空列表项（只有标记没有内容）被丢弃', () => {
    expect(parseFollowUpItems('- \n- 甲\n-')).toEqual(['甲'])
  })

  it('⚠️ 对照 A：围栏内的 `- x` **不算**追问项', () => {
    const content = '示例：\n\n```\n- 这不是追问\n```\n\n- 这才是追问'
    expect(parseFollowUpItems(content)).toEqual(['这才是追问'])
  })

  it('无列表 → 空数组（调用方据此退化为普通正文）', () => {
    expect(parseFollowUpItems('只是普通的一段话')).toEqual([])
    expect(parseFollowUpItems('')).toEqual([])
  })

  it('CRLF 不产生带尾 `\\r` 的项', () => {
    expect(parseFollowUpItems('- 甲\r\n- 乙')).toEqual(['甲', '乙'])
  })
})

describe('parseFollowUpItems — 上限', () => {
  it('对照 C：超过上限被截断（模型失控时不当成输入区炸弹）', () => {
    const many = Array.from({ length: 20 }, (_, i) => `- 问题${i + 1}`).join('\n')
    const items = parseFollowUpItems(many)
    expect(items).toHaveLength(MAX_FOLLOW_UPS)
    expect(items[0]).toBe('问题1')
  })
})
