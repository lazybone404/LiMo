/**
 * 文档库偏好 — 排序方式等界面记忆（localStorage，同 genPrefs 的手写模式）。
 *
 * ⚠️ 只持久化「用户显式选过」的东西。筛选条件（搜索词/类型/标签/文件夹）**不持久化**：
 * 下次打开应用时若还带着上次的筛选，用户会以为「文档库空了」——那是最难自查的一类故障。
 */
import type { SortKey } from './useDocQuery'

export interface DocLibraryPrefs {
  sort: SortKey
}

const DEFAULTS: DocLibraryPrefs = {
  sort: 'date',
}

const KEY = 'mbw_docLibPrefs'

export function loadDocLibraryPrefs(): DocLibraryPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}')
    return { ...DEFAULTS, ...raw }
  } catch {
    return { ...DEFAULTS }
  }
}

export function saveDocLibraryPrefs(prefs: Partial<DocLibraryPrefs>) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...loadDocLibraryPrefs(), ...prefs }))
  } catch {
    // 配额超限 / 隐私模式：静默失败（偏好丢失不影响功能）
  }
}
