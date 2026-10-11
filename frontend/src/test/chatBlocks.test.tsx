/**
 * 对话侧卡片块的渲染 —— 「卡片真的出来了、且里面真有字」+ 追问 chip 真的可点。
 *
 * ⚠️ 与 `markerContent.test.tsx` 同款：重点是**空壳检测**（项目病史：「渲染成功、
 * 断言全过，用户拿到一个空壳」）。这里断卡片的**数量**与**块内文字真的在**。
 *
 * ⚠️ 与产物侧的 `blocks.css` **刻意不共用类名**（`.chat-*` vs `.md-block`）——
 * 下面有一组断言专门钉住这一点：对话渲染路径不能命中产物的强调块样式。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. 复用 `preview/MarkerContent`（内部硬编码 `MarkdownPreview`）→ 整个对话正文换成
 *      产物渲染路径，`.markdown-chat` 内部凭空多出 `.markdown-body` →
 *      波及**全部历史消息**的静默回归 → 红。
 *   B. 产物侧类型（`key-takeaway`）在对话里被当卡片渲染 → 对话冒出带左边框的强调块 → 红。
 *   C. 流式时不做半截标记抑制 → 页面上出现 `<!-- limo-block:v1 type="card-` 的可见文字 → 红。
 *   D. `###` 少于 2 个时仍画网格 → 一张孤零零的卡（或空网格）→ 红。
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { ChatMarkerContent } from '../components/ai-chat/ChatMarkerContent'
import { useChatStore } from '../store/chatStore'

afterEach(cleanup)

const renderChat = (content: string, streaming = false) =>
  render(<ChatMarkerContent content={content} streaming={streaming} statusText="" />)

const CARD_GROUP = [
  '<!-- limo-block:v1 type="card-group" -->',
  '### 方案 A',
  '直接整篇提交。',
  '',
  '### 方案 B',
  '先分块再处理。',
  '<!-- /limo-block -->',
].join('\n')

describe('ChatMarkerContent — card-group', () => {
  it('两块卡片，卡内文字真的在（空壳检测）', () => {
    const { container } = renderChat(CARD_GROUP)
    const cards = container.querySelectorAll('.chat-card')
    expect(cards.length).toBe(2)
    expect(cards[0].textContent).toContain('方案 A')
    expect(cards[0].textContent).toContain('直接整篇提交。')
    expect(cards[1].textContent).toContain('先分块再处理。')
  })

  it('首个 `###` 之前的引言不丢', () => {
    const md = '<!-- limo-block:v1 type="card-group" -->\n铺垫一句。\n\n### 甲\n一\n\n### 乙\n二\n<!-- /limo-block -->'
    const { container } = renderChat(md)
    expect(container.textContent).toContain('铺垫一句。')
    expect(container.querySelectorAll('.chat-card').length).toBe(2)
  })

  it('对照 D：没有 `###` → 退化成普通正文，不画空网格', () => {
    const md = '<!-- limo-block:v1 type="card-group" -->\n只是一段话。\n<!-- /limo-block -->'
    const { container } = renderChat(md)
    expect(container.querySelectorAll('.chat-card').length).toBe(0)
    expect(container.querySelector('.chat-card-group')).toBeNull()
    expect(container.textContent).toContain('只是一段话。')
  })

  it('对照 A/围栏：`###` 出现在代码围栏里 → 卡数**不增**', () => {
    const md = '<!-- limo-block:v1 type="card-group" -->\n```\n### 代码里的井号\n```\n\n### 真卡\n内容\n<!-- /limo-block -->'
    const { container } = renderChat(md)
    expect(container.querySelectorAll('.chat-card').length).toBe(1)
    expect(container.querySelector('.chat-card')!.textContent).toContain('真卡')
  })
})

describe('ChatMarkerContent — follow-up', () => {
  const FOLLOW_UP = '<!-- limo-block:v1 type="follow-up" -->\n- 第一个问题？\n- 第二个问题？\n<!-- /limo-block -->'

  it('每项一颗按钮', () => {
    const { container } = renderChat(FOLLOW_UP)
    const chips = container.querySelectorAll('.chat-followup-chip')
    expect(chips.length).toBe(2)
    expect(chips[0].textContent).toBe('第一个问题？')
  })

  it('点击第 i 颗 → 以该项文本发送（不带 docRefs）', () => {
    const spy = vi.spyOn(useChatStore.getState(), 'sendMessage').mockImplementation(() => {})
    try {
      const { container } = renderChat(FOLLOW_UP)
      fireEvent.click(container.querySelectorAll('.chat-followup-chip')[1])
      expect(spy).toHaveBeenCalledWith('第二个问题？')
    } finally {
      spy.mockRestore()
    }
  })

  it('流式中按钮禁用（`dispatchSend` 首行也会兜底，这里只是让这一点看得出来）', () => {
    const { container } = renderChat(FOLLOW_UP, true)
    const chips = container.querySelectorAll('.chat-followup-chip')
    expect(chips.length).toBe(2)
    for (const c of chips) expect((c as HTMLButtonElement).disabled).toBe(true)
  })

  it('解析不出列表项 → 退化成普通正文', () => {
    const md = '<!-- limo-block:v1 type="follow-up" -->\n只是一句话，不是列表。\n<!-- /limo-block -->'
    const { container } = renderChat(md)
    expect(container.querySelectorAll('.chat-followup-chip').length).toBe(0)
    expect(container.querySelector('.chat-followup')).toBeNull()
    expect(container.textContent).toContain('只是一句话，不是列表。')
  })
})

describe('ChatMarkerContent — info / digest', () => {
  const INFO = '回答正文。\n\n<!-- limo-block:v1 type="info" -->\n**bcut-asr** 是 B 站必剪的免费云端转写接口。\n<!-- /limo-block -->'
  const DIGEST = '<!-- limo-block:v1 type="digest" -->\n- 要点一\n- 要点二\n<!-- /limo-block -->\n\n详细展开……'

  it('info → 卡片容器 + data-block-type，块内文字在（空壳检测）', () => {
    const { container } = renderChat(INFO)
    const card = container.querySelector('.chat-info-card')
    expect(card).not.toBeNull()
    expect(card!.getAttribute('data-block-type')).toBe('info')
    expect(card!.textContent).toContain('bcut-asr')
    expect(container.textContent).toContain('回答正文。')   // 块外正文没丢
  })

  it('digest → 卡片容器 + data-block-type，块内列表项在（空壳检测）', () => {
    const { container } = renderChat(DIGEST)
    const card = container.querySelector('.chat-digest-card')
    expect(card).not.toBeNull()
    expect(card!.getAttribute('data-block-type')).toBe('digest')
    expect(card!.textContent).toContain('要点一')
    expect(container.textContent).toContain('详细展开……')
  })

  it('两者都**不**带 md-block（对话路径不命中产物侧的 blocks.css）', () => {
    // 与「对照 B」同一条纪律：对话渲染路径与产物渲染路径刻意不共用类名。
    for (const md of [INFO, DIGEST]) {
      expect(renderChat(md).container.querySelector('.md-block')).toBeNull()
    }
  })

  it('标记行不出现在页面上', () => {
    for (const md of [INFO, DIGEST]) {
      const { container } = renderChat(md)
      expect(container.textContent).not.toContain('limo-block')
      expect(container.textContent).not.toContain('<!--')
    }
  })
})

describe('ChatMarkerContent — 快路径与跨侧兜底', () => {
  it('无标记 → 快路径：没有卡片组 / 追问容器，正文正常渲染', () => {
    const { container } = renderChat('# 标题\n\n正文一段。')
    expect(container.querySelector('.chat-card-group')).toBeNull()
    expect(container.querySelector('.chat-followup')).toBeNull()
    expect(container.textContent).toContain('正文一段。')
  })

  it('对照 B：产物侧类型在对话里**不**变成卡片、也不带 md-block', () => {
    // 逐一遍历产物侧全部类型 —— 「两侧集合不相交」的**渲染层**证据（集合本身由
    // markdownMarkers.test.ts 的守卫钉住，那条只证明常量对，证明不了这里真的没分支）。
    for (const t of ['key-takeaway', 'alert', 'recommend', 'metric']) {
      const md = `<!-- limo-block:v1 type="${t}" -->\n**重点**\n<!-- /limo-block -->`
      const { container } = renderChat(md)
      expect(container.querySelectorAll('.chat-card').length, t).toBe(0)
      expect(container.querySelector('[data-block-type="card-group"]'), t).toBeNull()
      expect(container.querySelector('.chat-info-card'), t).toBeNull()
      expect(container.querySelector('.chat-digest-card'), t).toBeNull()
      // ⚠️ `md-block` 会命中产物侧的 blocks.css（左边框 + 淡底），对话不该有
      expect(container.querySelector('.md-block'), t).toBeNull()
      expect(container.textContent).toContain('重点')
    }
  })

  it('未知 type 内容原样出现（不认识 ≠ 内容消失）', () => {
    const md = '<!-- limo-block:v1 type="warning" -->\n警告内容\n<!-- /limo-block -->'
    const { container } = renderChat(md)
    expect(container.textContent).toContain('警告内容')
    expect(container.querySelector('.md-block')).toBeNull()
  })

  it('标记行不出现在页面上', () => {
    const { container } = renderChat(CARD_GROUP)
    expect(container.textContent).not.toContain('limo-block')
    expect(container.textContent).not.toContain('<!--')
  })
})

describe('ChatMarkerContent — 流式半截标记', () => {
  it('对照 C：流式中写到一半的标记行**不显示**；同一文本在非流式下也不显示协议残缺部分', () => {
    const partial = '正文\n<!-- limo-block:v1 type="card-'
    const { container } = renderChat(partial, true)
    expect(container.textContent).not.toContain('limo-block')
    expect(container.textContent).not.toContain('<!--')
    expect(container.textContent).toContain('正文')
  })

  it('半截标记补全后卡片正常出现', () => {
    const { container } = renderChat(CARD_GROUP, true)
    expect(container.querySelectorAll('.chat-card').length).toBe(2)
  })
})
