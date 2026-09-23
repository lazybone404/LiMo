/**
 * 全文搜索栏 — Header 区域的搜索输入框，下拉展示搜索结果。
 * 调用 GET /api/search?q=... 获取 FTS5 搜索结果。
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { Search, X } from 'lucide-react'
import { useStore } from '../../store'
import { searchDocs, type SearchHit } from '../../api/search'
import { SearchSnippet } from './SearchSnippet'

export function SearchBar() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchHit[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const selectDoc = useStore(s => s.selectDoc)

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  // 防抖搜索
  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        setResults(await searchDocs(query, 10))
        setSelectedIdx(0)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [query])

  // 键盘导航
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && results[selectedIdx]) {
      e.preventDefault()
      selectDoc(results[selectedIdx].id)
      setOpen(false)
      setQuery('')
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }, [open, results, selectedIdx, selectDoc])

  const openSearch = () => {
    setOpen(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  return (
    <div ref={containerRef} className="relative">
      {/* 搜索按钮 / 输入框 */}
      {!open ? (
        <button
          onClick={openSearch}
          className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[12px] text-muted hover:text-dimmed hover:bg-surface-hover transition-colors"
          title="搜索文档 (Ctrl+K)"
        >
          <Search size={13} />
          <span className="hidden lg:inline">搜索</span>
        </button>
      ) : (
        <div className="flex items-center bg-surface rounded-lg border border-border-light px-2.5 py-1 min-w-[200px]">
          <Search size={13} className="text-muted shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="搜索文档内容…"
            className="flex-1 bg-transparent border-none outline-none text-[12px] px-2 py-0.5 text-foreground placeholder:text-disabled min-w-0"
            autoFocus
          />
          {loading && (
            <span className="shrink-0 w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
          )}
          <button
            onClick={() => { setOpen(false); setQuery('') }}
            className="shrink-0 p-0.5 rounded text-muted hover:text-foreground"
          >
            <X size={13} />
          </button>
        </div>
      )}

      {/* 下拉结果 */}
      {open && query.trim() && (
        <div className="absolute top-full mt-1.5 left-0 w-[380px] max-h-[360px] overflow-y-auto rounded-xl border border-border-light bg-elevated shadow-2xl z-[9999]">
          {results.length === 0 ? (
            <div className="px-4 py-8 text-center text-[12px] text-disabled">
              {loading ? '搜索中…' : '无结果'}
            </div>
          ) : (
            results.map((r, i) => (
              <button
                key={r.id}
                onClick={() => {
                  selectDoc(r.id)
                  setOpen(false)
                  setQuery('')
                }}
                onMouseEnter={() => setSelectedIdx(i)}
                className={`w-full text-left px-4 py-2.5 border-b border-black/[0.04] last:border-b-0 transition-colors ${
                  i === selectedIdx ? 'bg-surface-hover' : 'hover:bg-surface-hover'
                }`}
              >
                <p className="text-[13px] font-medium text-foreground truncate">{r.title}</p>
                <SearchSnippet raw={r.snippet} className="block text-[11px] text-disabled mt-0.5 line-clamp-2" />
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
