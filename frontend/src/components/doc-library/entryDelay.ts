/**
 * 文档卡片入场错位延迟（毫秒）。
 *
 * ⚠️ 必须与列表索引解耦。曾直接写 `animationDelay: ${i * 50}ms`：
 * 第 1000 个文档要等 50 秒才出现——第 1000 个文档不是「卡顿」，是**压根还没开始动画**，
 * 用户看到的表现是「列表里少了好多文档」。
 *
 * 这里按 20 取模循环，延迟上限固定为 475ms，与列表长度无关。
 */

export const ENTER_STAGGER = 20
export const ENTER_STEP_MS = 25

/** 第 i 个卡片的入场延迟（毫秒），有界且与列表长度无关 */
export function entryDelay(i: number): number {
  return (i % ENTER_STAGGER) * ENTER_STEP_MS
}
