/**
 * 对话模块尺寸档位 — sm/md/lg 三档，供 ChatCore / MessageItem / ChatInput 共享。
 * 浮窗随宽度自适应切换，插槽面板固定 md。
 *
 * ⚠️ `msg` 是**唯一一个不写 Tailwind 字面量的字段** —— 它是「对话正文字号」，
 * 要能被用户 Ctrl+滚轮缩放，故走 `index.css` 的 `.chat-msg-*`
 * （`calc(<基准px> × var(--chat-font-scale, 1))`）。基准仍在这里决定（三档随浮窗宽度），
 * 倍率由 `lib/chat-font.ts` 写进 `<html>`。写回 `text-[13px]` 就等于「字号缩放失效但零报错」。
 * 其余字段（input / btn / icon / avatar…）与缩放无关，保持 Tailwind 字面量不动。
 */

export type ChatScale = 'sm' | 'md' | 'lg'

export const SCALES = {
  sm: { msg: 'chat-msg-sm', input: 'text-[12px]', btn: 11, icon: 12, avatar: 'w-6 h-6', gap: 'space-y-2', pad: 'px-3 py-2', listText: 'text-[11px]', listItem: 'px-2 py-1.5' },
  md: { msg: 'chat-msg-md', input: 'text-[13px]', btn: 13, icon: 14, avatar: 'w-7 h-7', gap: 'space-y-2.5', pad: 'px-3 py-2.5', listText: 'text-[12px]', listItem: 'px-2 py-2' },
  lg: { msg: 'chat-msg-lg', input: 'text-[14px]', btn: 14, icon: 15, avatar: 'w-8 h-8', gap: 'space-y-3', pad: 'px-4 py-3', listText: 'text-[13px]', listItem: 'px-2.5 py-2' },
} as const
