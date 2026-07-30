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
  // 导入：遍历查找所有匹配的导入 controller
  if (docId === '__import__' || docId.startsWith('__import__')) {
    let aborted = false
    _importControllers.forEach(ctrl => { ctrl.abort(); aborted = true })
    _importControllers.clear()
    return aborted
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
