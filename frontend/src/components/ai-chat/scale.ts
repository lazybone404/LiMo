/**
 * 对话模块尺寸档位 — sm/md/lg 三档，供 ChatCore / MessageItem / ChatInput 共享。
 * 浮窗随宽度自适应切换，插槽面板固定 md。
 */

export type ChatScale = 'sm' | 'md' | 'lg'

export const SCALES = {
  sm: { msg: 'text-[13px]', input: 'text-[12px]', btn: 11, icon: 12, avatar: 'w-6 h-6', gap: 'space-y-2', pad: 'px-3 py-2', listText: 'text-[11px]', listItem: 'px-2 py-1.5' },
  md: { msg: 'text-[14px]', input: 'text-[13px]', btn: 13, icon: 14, avatar: 'w-7 h-7', gap: 'space-y-2.5', pad: 'px-3 py-2.5', listText: 'text-[12px]', listItem: 'px-2 py-2' },
  lg: { msg: 'text-[15px]', input: 'text-[14px]', btn: 14, icon: 15, avatar: 'w-8 h-8', gap: 'space-y-3', pad: 'px-4 py-3', listText: 'text-[13px]', listItem: 'px-2.5 py-2' },
} as const
