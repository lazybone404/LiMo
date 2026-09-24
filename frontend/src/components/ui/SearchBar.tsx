/**
 * Header 搜索入口 —— 一个**按钮**，不是输入框。
 *
 * ⚠️ 这里曾经是一整套自己的下拉搜索（限 10 条、无 AbortController、自带一份
 * `SearchSnippet` 渲染分支、有自己的「打开/清空」状态机）。它与文档库的搜索是
 * **两份实现**：取回条数不同、命中结果集的组织方式不同、连片段高亮都是两条路。
 * 「搜文档」被做成了两遍，两边的行为只会越走越远（本轮改的就是文档库那一份）。
 *
 * 现在它退化为**入口**：点它 = 展开文档库 + 聚焦那里的搜索框（与 `Ctrl+K` 同一个
 * store action）。真正的搜索只有一处实现、一份结果集。
 */
import { Search } from 'lucide-react'
import { useStore } from '../../store'

export function SearchBar() {
  return (
    <button
      data-open-doc-search
      onClick={() => useStore.getState().openDocLibSearch()}
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[12px] text-muted hover:text-dimmed hover:bg-surface-hover transition-colors"
      title="搜索文档 (Ctrl+K)"
    >
      <Search size={13} />
      <span className="hidden lg:inline">搜索</span>
    </button>
  )
}
