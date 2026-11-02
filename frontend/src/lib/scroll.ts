/**
 * 滚动容器的「是否贴底」判据 —— 对话消息区与日志面板共用。
 *
 * ⚠️ **必须共用一份**：两处各写一遍 `scrollHeight - scrollTop - clientHeight < 40` 时，
 * 容差是同一个概念却有两个值 —— 将来只改一处就会得到「聊天区跟随、日志面板不跟随」
 * 这种只在一边复现的行为，而两边都读起来完全正常（模式 #54：同源漂移）。
 */

/** 「认为在底部」的容差（px）—— 贴底判定与「回到最新」按钮显隐共用这一个值 */
export const BOTTOM_EPSILON = 40

/** 容器当前是否已贴底（`< BOTTOM_EPSILON` 即算贴底）。 */
export function isNearBottom(el: {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_EPSILON
}
