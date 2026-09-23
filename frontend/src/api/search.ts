/**
 * 全文搜索域 — FTS5 正文检索（GET /api/search）。
 *
 * ⚠️ 搜的是**原文（transcript）**，不含 notes/polished 产物。产物是 LLM 生成物，
 * 索引它们会让「搜什么」变得不可预测（同一句话可能同时命中原文与两版产物）。
 */
import { apiFetch, BASE } from './core'

export interface SearchHit {
  id: string
  title: string
  /** 含后端注入的 `<mark>` 高亮标记。**必须经 SearchSnippet 渲染**——
   *  片段是用户导入的原文，直接 dangerouslySetInnerHTML 等于把导入内容当 HTML 执行。 */
  snippet: string
}

/** 搜索原文正文。q 为空返回空数组（不发请求）。 */
export async function searchDocs(q: string, limit = 10, signal?: AbortSignal): Promise<SearchHit[]> {
  if (!q.trim()) return []
  const r = await apiFetch(`${BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`, { signal })
  const data = await r.json()
  return Array.isArray(data) ? data : []
}
