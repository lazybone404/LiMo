/**
 * 全局任务管理器 — 让 TaskPanel 和外层组件可以取消任何正在进行的异步操作。
 *
 * Key 规则：
 * - Doc 任务（generate/retranscribe/process）：归一化到 docId
 *   注册 "doc123__generate" → 存为 "doc123"
 *   取消 "doc123" → 命中 ✓
 *   同一文档重复注册 → 自动 abort 旧任务（互斥语义）
 *
 * - 导入任务：按 __import__ 前缀分组
 *   注册 "__import__0__import" → 仍用导入组前缀查找
 *   取消 "__import__" → 命中所有导入任务
 *   多导入并发不互相 abort（各自独立 controller）
 */

const controllers = new Map<string, AbortController>()
// 导入任务独立存储（支持多导入并发）
const _importControllers = new Map<string, AbortController>()

/**
 * 为某个 taskKey 注册 AbortController
 * - Doc 任务自动 abort 同文档的旧任务
 * - 导入任务各自独立（不互相 abort）
 */
export function registerAbort(taskKey: string, ctrl: AbortController): AbortController {
  if (taskKey.startsWith('__import__')) {
    // 导入：独立存储，不 abort 其他导入
    _importControllers.set(taskKey, ctrl)
  } else {
    // Doc 任务：归一化到 docId，互斥（新任务 abort 旧任务）
    const idx = taskKey.indexOf('__')
    const baseKey = idx > 0 ? taskKey.slice(0, idx) : taskKey
    controllers.get(baseKey)?.abort()
    controllers.set(baseKey, ctrl)
  }
  return ctrl
}

/**
 * 取消指定 docId / baseKey 的操作
 */
export function cancelTask(docId: string): boolean {
  // 取消所有导入（旧调用方语义）
  if (docId === '__import__') {
    let aborted = false
    _importControllers.forEach(ctrl => { ctrl.abort(); aborted = true })
    _importControllers.clear()
    return aborted
  }
  // P2-7：精确取消单条导入——支持 importKey（__import__N）与 taskKey（__import__N__import）两种形态，
  // 不误伤并发的其他导入任务
  if (docId.startsWith('__import__')) {
    const ctrl = _importControllers.get(docId) || _importControllers.get(docId + '__import')
    if (ctrl) {
      ctrl.abort()
      _importControllers.delete(docId)
      _importControllers.delete(docId + '__import')
      return true
    }
    return false
  }
  // Doc 任务：baseKey 直接匹配
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
  _importControllers.forEach(c => c.abort())
  _importControllers.clear()
}
