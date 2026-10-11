/**
 * 对话正文字号 —— 偏好读写 + **与阅读字号互不干扰**。
 *
 * 本文件最要紧的一组是「**两份字号各存各的**」（2026-10-12 用户拍板）：它们是两个
 * 宿主上的两个排版（宽屏长文 vs 窄浮窗气泡），共用一个倍率会让调大阅读字号把气泡
 * 一起撑大。这条**只在两个方向都断言时才守得住** —— 只断「改对话不动阅读」的实现，
 * 反过来把 `applyChatFont` 写成 `applyReading` 的别名照样过。
 *
 * 对照实验（每条都对应一个「看起来对、其实错」的实现）：
 *   A. `CHAT_FONT_DEFAULTS.fontScale` 写成 1.1（「气泡里 14px 有点小」）→
 *      所有既有用户的对话观感被静默改动 → 红
 *   B. 复用 `mbw_reading` 这个 key → 两份字号从此同生同灭 → 红
 *   C. `applyChatFont` 顺手也写 `--read-font-size`（「都是字号嘛」）→ 改对话把预览区
 *      一起缩放 → 红
 *   D. `nextFontScale` 在本模块重新实现一份 → 与 `reading-prefs` 的那份漂移 →
 *      `toBe` 身份断言 → 红
 *   E. 坏字段整包丢弃 → 与 `reading-prefs` 的同族问题（将来加第二个字段就会被咬）→ 红
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  CHAT_FONT_DEFAULTS,
  getSavedChatFont,
  applyChatFont,
  saveChatFont,
} from '../lib/chat-font'
import * as chatFont from '../lib/chat-font'
import * as readingPrefs from '../lib/reading-prefs'

const KEY = 'mbw_chatFont'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.style.removeProperty('--chat-font-scale')
  document.documentElement.style.removeProperty('--read-font-size')
})

// ── 零回归 ──────────────────────────────────────────────────

describe('零回归 — 默认 = 改造前的现状', () => {
  it('默认倍率 1.0（= 各档基准字号 13/14/15px）', () => {
    // 对照 A：默认改成别的值 → 所有既有用户的对话观感静默变化 → 红
    expect(CHAT_FONT_DEFAULTS.fontScale).toBe(1.0)
    expect(getSavedChatFont()).toEqual(CHAT_FONT_DEFAULTS)
  })

  it('applyChatFont(默认) 写出的变量就是 1', () => {
    applyChatFont(CHAT_FONT_DEFAULTS)
    expect(document.documentElement.style.getPropertyValue('--chat-font-scale')).toBe('1')
  })
})

// ── 两份字号互不干扰（本文件的核心）────────────────────────

describe('与阅读字号是两份独立的偏好', () => {
  it('存的是两个不同的 key', () => {
    // 对照 B：共用 `mbw_reading` → 这里读到对方的值 → 红
    saveChatFont({ fontScale: 1.3 })
    expect(localStorage.getItem(KEY)).toBe(JSON.stringify({ fontScale: 1.3 }))
    expect(localStorage.getItem('mbw_reading')).toBe(null)
  })

  it('applyChatFont **只**写 --chat-font-scale，不碰 --read-font-size', () => {
    // 对照 C：把两个 apply 合并 / 互相调用 → 预览区被一起缩放 → 红
    applyChatFont({ fontScale: 1.4 })
    expect(document.documentElement.style.getPropertyValue('--chat-font-scale')).toBe('1.4')
    expect(document.documentElement.style.getPropertyValue('--read-font-size')).toBe('')
  })

  it('反过来：applyReading 不碰 --chat-font-scale', () => {
    // 上一条的反方向 —— 只断一个方向会放过「read 覆盖 chat」的实现
    readingPrefs.applyReading({ ...readingPrefs.READING_DEFAULTS, fontScale: 1.2 })
    expect(document.documentElement.style.getPropertyValue('--read-font-size')).toBe('19.2px')
    expect(document.documentElement.style.getPropertyValue('--chat-font-scale')).toBe('')
  })
})

// ── 档位换算同源 ────────────────────────────────────────────

describe('档位换算复用 reading-prefs（不写第二份）', () => {
  it('导出的是同一份函数与区间（身份断言）', () => {
    // 对照 D：在本模块重写一份 nextFontScale → 两份会漂移（一个 round 一个不 round）→ 红
    expect(chatFont.nextFontScale).toBe(readingPrefs.nextFontScale)
    expect(chatFont.FONT_SCALE_RANGE).toBe(readingPrefs.FONT_SCALE_RANGE)
  })

  it('连推 8 档恰好到上限，且无浮点漂移', () => {
    let v: number = readingPrefs.FONT_SCALE_RANGE.min
    for (let i = 0; i < 8; i++) v = chatFont.nextFontScale(v, 1)
    expect(v).toBe(readingPrefs.FONT_SCALE_RANGE.max)
  })
})

// ── 读写校验 ────────────────────────────────────────────────

describe('getSavedChatFont — 校验与 clamp', () => {
  it('损坏 JSON → 用默认（不抛）', () => {
    localStorage.setItem(KEY, '{not json')
    expect(getSavedChatFont()).toEqual(CHAT_FONT_DEFAULTS)
  })

  it('越界被 clamp 进 [0.8, 1.6]', () => {
    for (const [bad, want] of [[0.3, 0.8], [9, 1.6]] as const) {
      localStorage.setItem(KEY, JSON.stringify({ fontScale: bad }))
      expect(getSavedChatFont().fontScale).toBe(want)
    }
  })

  it('非法类型一律回落 1.0', () => {
    for (const bad of ['1.5', NaN, null, {}, true]) {
      localStorage.setItem(KEY, JSON.stringify({ fontScale: bad }))
      expect(getSavedChatFont().fontScale).toBe(1.0)
    }
  })

  it('合法值归一到两位小数（防滚轮浮点尾巴）', () => {
    localStorage.setItem(KEY, JSON.stringify({ fontScale: 1.23456 }))
    expect(getSavedChatFont().fontScale).toBe(1.23)
  })

  it('缺 fontScale 字段（空对象）→ 用默认，不抛', () => {
    localStorage.setItem(KEY, '{}')
    expect(getSavedChatFont()).toEqual(CHAT_FONT_DEFAULTS)
  })
})

describe('saveChatFont', () => {
  it('save → get 往返一致', () => {
    saveChatFont({ fontScale: 1.5 })
    expect(getSavedChatFont()).toEqual({ fontScale: 1.5 })
  })
})
