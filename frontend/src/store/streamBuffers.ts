/**
 * 流式内容高速缓冲区 — 模块级 Map，无 React 依赖
 *
 * 设计意图：
 * - LLM token 到达频率高（20-50/s），直接写 Zustand store 会产生大量快照
 * - 模块级 Map 写入极快，不触发任何 re-render
 * - 通过 scheduleFlush 每 ~120ms 将 tick 写入 store，驱动 PreviewArea 重读
 *
 * 用法：
 *   import { appendStreamBuffer, getStreamBuffer, deleteStreamBuffer, scheduleFlush } from './streamBuffers'
 *
 *   // 写入（高频）
 *   appendStreamBuffer(key, text)
 *   scheduleFlush(() => useStore.getState().bumpStreamTick())
 *
 *   // 读取（PreviewArea 订阅 streamTick 后调用）
 *   const buf = getStreamBuffer(key)
 */

const _buffers = new Map<string, string>()

/** 获取指定 session 的累积内容 */
export function getStreamBuffer(key: string): string {
  return _buffers.get(key) || ''
}

/** 追加文本到指定 session */
export function appendStreamBuffer(key: string, text: string): void {
  _buffers.set(key, (_buffers.get(key) || '') + text)
}

/** 设置指定 session 的内容（覆盖） */
export function setStreamBuffer(key: string, text: string): void {
  _buffers.set(key, text)
}

/** 删除指定 session */
export function deleteStreamBuffer(key: string): void {
  _buffers.delete(key)
}

/**
 * 删除某文档的全部流式缓冲（key 形如 `${docId}__${kind}_${mode}`）。
 *
 * 缓冲的 key 由调用方自由拼接，这里只能按 `docId + '__'` **前缀**匹配——
 * 前提是 docId 本身不含 `__`（文档 id 由后端生成，满足）。
 * 用「取第一段」而不是前缀匹配会更精确，但两者都依赖同一前提，取前缀更简单。
 */
export function deleteStreamBuffersForDoc(docId: string): number {
  const prefix = docId + '__'
  let n = 0
  for (const k of [..._buffers.keys()]) {
    if (k.startsWith(prefix)) { _buffers.delete(k); n++ }
  }
  return n
}

/** 检查指定 session 是否存在 */
export function hasStreamBuffer(key: string): boolean {
  return _buffers.has(key)
}

let _flushTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 调度 flush（去抖 120ms）
 * 多次连续调用只会触发一次 flush，避免高频写 store
 */
export function scheduleFlush(flushFn: () => void): void {
  if (_flushTimer) return
  _flushTimer = setTimeout(() => {
    _flushTimer = null
    flushFn()
  }, 120)
}

/** 立即 flush（取消去抖），用于 onDone/onCancelled 等最终化场景 */
export function flushImmediate(flushFn: () => void): void {
  if (_flushTimer) {
    clearTimeout(_flushTimer)
    _flushTimer = null
  }
  flushFn()
}
