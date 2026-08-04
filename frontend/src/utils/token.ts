/**
 * token 估算 — NextChat 同款启发式（前端无 tiktoken 依赖，纯字符级）。
 *
 * ⚠️ 仅用于输入框 / 上下文占用进度的「参考显示」——实际裁剪以后端 tiktoken
 * 精确估算为准（meta 事件带后端算好的 budget）。
 *
 * 规则：ASCII 字母 0.25 / 其他 ASCII 0.5 / 非 ASCII（中文等）1.5。
 */

export function estimateTokenLength(input: string): number {
  let tokenLength = 0
  for (let i = 0; i < input.length; i++) {
    const charCode = input.charCodeAt(i)
    if (charCode < 128) {
      // A-Z a-z
      if (charCode <= 122 && charCode >= 65) tokenLength += 0.25
      else tokenLength += 0.5
    } else {
      tokenLength += 1.5
    }
  }
  return tokenLength
}

/** 数字 → 可读 token 数（1.2K / 34K / 1.1M） */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`
  return String(Math.round(n))
}
