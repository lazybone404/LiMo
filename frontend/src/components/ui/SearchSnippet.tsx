/**
 * 搜索摘要渲染 — FTS5 片段含后端注入的 `<mark>` 高亮标记。
 *
 * ⚠️ **不能直接把 snippet 丢进 dangerouslySetInnerHTML**（SearchBar 与文档库两处都曾如此）：
 * snippet 取自**用户导入的原文**，会原样带回文中的 `<img onerror=…>` / `<script>`。
 * 这里只放行 `<mark>`/`</mark>` 两个标签，其余字符一律转义——高亮照常，
 * 内容里的 HTML 变成纯文本。
 *
 * 抽成组件而非在每处各写一遍：两处渲染的 XSS 姿态必须同源，复制第二份必然漂移。
 */

const ESCAPE_MAP: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;' }

/** 转义全部 HTML，再把被转义的 mark 标签还原（仅此两个标签享有特权） */
export function escapeSnippet(raw: string): string {
  return (raw || '')
    .replace(/[&<>]/g, c => ESCAPE_MAP[c])
    .replace(/&lt;mark&gt;/g, '<mark>')
    .replace(/&lt;\/mark&gt;/g, '</mark>')
}

export function SearchSnippet({ raw, className }: { raw: string; className?: string }) {
  return (
    <span
      className={className}
      dangerouslySetInnerHTML={{ __html: escapeSnippet(raw) }}
    />
  )
}
