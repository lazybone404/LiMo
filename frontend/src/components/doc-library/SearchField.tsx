/**
 * 文档库搜索框 —— 绑定 store 的 `searchQuery`（**会触发后端 FTS**，与「标题关键词」筛选不同）。
 *
 * ⚠️ 与筛选区「标题关键词」的分工要写进 placeholder：那个只在本地内存里筛标题（0ms、零网络），
 * 这个搜正文（防抖 250ms 后打 `/api/search`）。两者可同时生效（AND）——用户在搜索框里找内容、
 * 再在筛选里把范围收窄到标题命中的那几篇，是合理动作。
 *
 * ⚠️ 外层布局（padding / 与筛选图标并排）由调用方给：详细模式下它是独立一行，
 * 简略模式下它与筛选入口同一行，组件自己带外边距就没法两用了。
 */
import { useEffect, useRef } from 'react'
import { Search, X } from 'lucide-react'
import { useStore } from '../../store'

export interface SearchFieldProps {
  /** FTS 请求进行中 */
  searching: boolean
}

export function SearchField({ searching }: SearchFieldProps) {
  const searchQuery = useStore(s => s.searchQuery)
  const setSearchQuery = useStore(s => s.setSearchQuery)
  const focusNonce = useStore(s => s.docLibSearchFocusNonce)
  const inputRef = useRef<HTMLInputElement>(null)

  // Ctrl+K / 顶栏搜索按钮 → 聚焦到这里。
  // ⚠️ `nonce > 0` 的守卫是必需的：初值 0 时不该抢走用户已有的焦点（比如他正停在
  // 生成面板的输入框上，而文档库恰好在这一帧挂载）。
  useEffect(() => {
    if (focusNonce > 0) inputRef.current?.focus()
  }, [focusNonce])

  return (
    <div className="flex items-center gap-2 bg-surface rounded-lg px-2.5 py-1.5">
      <Search size={14} className="text-disabled shrink-0" />
      <input
        ref={inputRef}
        data-search-input
        placeholder="搜索标题或正文…"
        value={searchQuery}
        onChange={e => setSearchQuery(e.target.value)}
        className="flex-1 min-w-0 bg-transparent text-[12px] outline-none placeholder:text-disabled"
      />
      {searching && <span className="shrink-0 w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />}
      {searchQuery && !searching && (
        <button
          data-search-clear
          onClick={() => setSearchQuery('')}
          className="shrink-0"
          title="清除搜索"
        >
          <X size={12} className="text-disabled hover:text-dimmed" />
        </button>
      )}
    </div>
  )
}
