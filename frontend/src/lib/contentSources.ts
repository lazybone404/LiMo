/**
 * 内容源绑定 — TabDef.contentSource → 内容取值函数。
 *
 * CONTENT_GETTERS 用 Record<ContentSource, ...>：新增 contentSource 枚举值
 * → 编译器强制补 getter，缺一编译失败（数据驱动 + 类型约束）。
 * 三处消费统一走 getContentForView：PreviewArea displayContent / CopyContentBtn / ExportMenu。
 */
import { TabRegistry, type ContentSource } from './tabRegistry'
import type { ImageResult } from '../store/types'

export interface ContentGetterCtx {
  currentDocId: string | null
  activeView: string
  currentContent: string | null
  currentPolishedContent: string | null
  currentRawText: string | null
  currentFactCheckContent: string | null
  imageResults: Record<string, Record<string, ImageResult>>
  /**
   * 流式缓冲读取器（undefined = 不读缓冲，导出/复制场景读已保存内容）。
   * **必须返回 null（非 ''）表示无缓冲** — getter 用 `??` 回退到已保存内容，
   * 若返回 ''（流式刚开始无 token）则 '' 会显示空白而非已保存内容。
   */
  readStreamBuffer?: (mode: 'notes' | 'polished') => string | null
}

// Record<ContentSource, ...>：新增 contentSource 枚举值 → 编译器强制补 getter
export const CONTENT_GETTERS: Record<ContentSource, (ctx: ContentGetterCtx) => string | null> = {
  notes:    (ctx) => ctx.readStreamBuffer?.('notes') ?? ctx.currentContent,
  polished: (ctx) => ctx.readStreamBuffer?.('polished') ?? ctx.currentPolishedContent,
  rawText:  (ctx) => ctx.currentRawText,
  factCheck: (ctx) => ctx.currentFactCheckContent,
  imageResult: (ctx) => ctx.imageResults[ctx.currentDocId || '']?.[ctx.activeView]?.text || null,
  none:     () => null,
}

/** 统一入口：view → TabDef → contentSource → getter。Tab 未注册返回 null */
export function getContentForView(view: string, ctx: ContentGetterCtx): string | null {
  const def = TabRegistry.get(view)
  if (!def) return null
  return CONTENT_GETTERS[def.contentSource](ctx)
}
