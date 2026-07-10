/**
 * 全局任务管理器 — 让 TaskPanel 和外层组件可以取消任何正在进行的异步操作。
 *
 * GenPanel 创建 AbortController 时注册到这里，
 * TaskPanel 或其他地方可以通过 docId 取消。
 */

const controllers = new Map<string, AbortController>()

/**
 * 为某个 docId 注册 AbortController（自动 abort 旧的）
 */
export function registerAbort(docId: string, ctrl: AbortController): AbortController {
  controllers.get(docId)?.abort()
  controllers.set(docId, ctrl)
  return ctrl
}

/**
 * 取消指定 docId 的操作
 */
export function cancelTask(docId: string): boolean {
  const ctrl = controllers.get(docId)
  if (ctrl) {
    ctrl.abort()
    controllers.delete(docId)
    return true
  }
  return false
}

/**
 * 取消所有进行中的操作
 */
export function cancelAll(): void {
  controllers.forEach(c => c.abort())
  controllers.clear()
}
