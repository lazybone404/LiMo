/**
 * LiMo — 对话浮窗的**正文字号缩放**（与预览区的「阅读字号」**相互独立**的一份）。
 *
 * ⚠️ **为什么不与 `reading-prefs.fontScale` 共用一份**（2026-10-12 用户拍板）：
 * 两者是不同宿主上的不同排版 —— 预览区是宽屏长文（基准 16px、受版宽 / 行高 /
 * 段式一整套版式约束），对话气泡是**窄浮窗**里的短回复（基准 13–15px，还随浮窗
 * 宽度自动切 sm/md/lg 三档）。共用一个倍率意味着「调大阅读字号」会把窄浮窗里的
 * 气泡一起撑大，反之亦然 —— 而用户对这两处的期望本来就不一样（阅读要疏朗、
 * 对话要紧凑）。故各存各的档，互不干扰。
 *
 * ⚠️ **与 `chat-prefs.ts`（`mbw_chatPrefs`）也不是一回事**：那一个控制「下一次请求
 * 带不带 `follow_up` 参数」，是个**瞬时读取值**（进不了 DOM、也不该进 store）；
 * 本文件控制「正文怎么显示」，与 `marker-prefs` / `card-prefs` **同族** ——
 * 写一个挂在 `<html>` 上的 CSS 变量、由 CSS 选择器消费。故各自一个 localStorage
 * key，**刻意不合并**（合并会让「发送偏好」这个纯瞬时的概念被迫带上 DOM 副作用）。
 *
 * ⚠️ **档位换算与区间直接复用 `reading-prefs` 的 `nextFontScale` / `FONT_SCALE_RANGE`**
 * —— 两处的「字号倍率」是同一个概念（0.8–1.6、step 0.1、四舍五入回两位），
 * 各写一份就是同源漂移（模式 #54）。差别只在**消费的宿主**与**凭据的变量**。
 */

import { FONT_SCALE_RANGE, nextFontScale } from './reading-prefs'

export interface ChatFontPrefs {
  /** 对话正文字号倍率。**1.0 = 各档的基准字号**（sm 13px / md 14px / lg 15px） */
  fontScale: number
}

/** ⚠️ 必须等于「改造前的现状」—— 改这里就是改变所有用户打开对话时的默认观感 */
export const CHAT_FONT_DEFAULTS: ChatFontPrefs = { fontScale: 1.0 }

const CHAT_FONT_KEY = 'mbw_chatFont'

/** 复用的是 `reading-prefs` 的档位函数 —— 用途写在这里，避免读者去那边找。 */
export { FONT_SCALE_RANGE, nextFontScale }

/**
 * 读 localStorage + **逐字段校验**（对齐 `reading-prefs` / `marker-prefs` 的形态）。
 * 越界 / 类型错只退回该项默认值，不整包丢弃 —— 将来加第二个字段时不必改结构。
 */
export function getSavedChatFont(): ChatFontPrefs {
  try {
    const raw = localStorage.getItem(CHAT_FONT_KEY)
    if (raw) {
      const v = JSON.parse(raw) as Partial<ChatFontPrefs>
      return {
        fontScale: typeof v.fontScale === 'number' && Number.isFinite(v.fontScale)
          ? clamp(Math.round(v.fontScale * 100) / 100, FONT_SCALE_RANGE.min, FONT_SCALE_RANGE.max)
          : CHAT_FONT_DEFAULTS.fontScale,
      }
    }
  } catch { /* 损坏 JSON → 用默认 */ }
  return { ...CHAT_FONT_DEFAULTS }
}

/**
 * 写 DOM —— 一个无单位倍数变量，由 `index.css` 的 `.chat-msg-*` 三条规则消费
 * （`calc(<基准px> × var(--chat-font-scale))`）。
 *
 * ⚠️ **基准字号不在这里**：它是 sm/md/lg 三档（随浮窗宽度变），住在 `scale.ts`。
 * 本文件只负责**倍率**这一个维度；把基准也塞进来会变成「两个地方都在决定字号」。
 */
export function applyChatFont(p: ChatFontPrefs): void {
  document.documentElement.style.setProperty('--chat-font-scale', String(p.fontScale))
}

export function saveChatFont(p: ChatFontPrefs): void {
  try { localStorage.setItem(CHAT_FONT_KEY, JSON.stringify(p)) } catch { /* quota */ }
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v))
}
