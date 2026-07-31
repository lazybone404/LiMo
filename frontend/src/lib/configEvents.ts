/**
 * 配置变更事件 — 设置页保存成功后广播，其他组件重新拉取最新配置
 *
 * 背景：GenPanel 的模型/引擎下拉、store 的 capabilities 只在挂载/初始化时拉取一次。
 * 设置页保存（启用模型、绑定引擎、改 Key 等）后若不刷新，导入面板永远是旧列表。
 * 用 CustomEvent 解耦：设置页只负责广播，消费方各自重新拉取。
 */

export const CONFIG_CHANGED_EVENT = 'limo:config-changed'

/** 广播配置已变更（设置页所有保存/增删改 handler 调用） */
export function notifyConfigChanged(): void {
  window.dispatchEvent(new CustomEvent(CONFIG_CHANGED_EVENT))
}

/** 订阅配置变更，返回取消订阅函数 */
export function onConfigChanged(cb: () => void): () => void {
  window.addEventListener(CONFIG_CHANGED_EVENT, cb)
  return () => window.removeEventListener(CONFIG_CHANGED_EVENT, cb)
}
