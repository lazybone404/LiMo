/**
 * 文档库面板 — 文件夹 / 筛选 / 排序 / 批量 / 全文搜索 / 分批渲染的**编排层**。
 *
 * 查询逻辑在 useDocQuery（纯函数可单测），卡片在 DocRow，文件夹树在 FolderTree，
 * 批量工具条在 BatchBar —— 本文件只负责把它们接起来并持有交互状态。
 */
import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useStore } from '../../store'
import {
  updateTranscript, exportBatch, getErrorMessage,
  listFolders, createFolder, renameFolder, deleteFolder, batchTranscripts,
} from '../../api/client'
import { cancelTask } from '../../api/taskManager'
import { ContextMenu } from '../ui/ContextMenu'
import { SearchSnippet } from '../ui/SearchSnippet'
import {
  Search, X, PanelLeftClose, Pencil, Trash2, BookOpen, MessageSquare,
  RefreshCw, Tag, Clipboard, CheckSquare, FolderInput, ArrowUpDown,
} from 'lucide-react'
import type { DocEntry, FolderEntry } from '../../store/types'
import { FolderTree } from './FolderTree'
import { BatchBar } from './BatchBar'
import { DocRow, type DocRowActions } from './DocRow'
import { SOURCE_FILTERS, IconByName } from './docMeta'
import { useDocQuery, collectTagFacets, SORT_OPTIONS, type SortKey } from './useDocQuery'
import { loadDocLibraryPrefs, saveDocLibraryPrefs } from './docLibraryPrefs'

/** 首次渲染的行数；到底部哨兵可见时每次追加这么多 */
const PAGE_SIZE = 60

/** 某文档是否有进行中的任务（键是 `${docId}__${kind}`，导入是裸 key） */
function isDocBusy(id: string): boolean {
  const tasks = useStore.getState().processingTasks
  return Object.keys(tasks).some(k => k === id || k.startsWith(id + '__'))
}

/**
 * 取消若干文档的进行中任务。
 *
 * ⚠️ 顺序不可颠倒：`cancelTask` 要任务句柄，而 `clearDocTasks` 会把句柄连同状态一起
 * 删掉——先清再取消会让 abort 找不到目标，任务继续在后台为已删除的 docId 落盘
 * （产生孤儿记录，文档「复活」）。
 * ⚠️ 必须**先整体扫一遍**要取消的 id：逐条「判断 → 取消 → 清」的话，第一轮 clear
 * 已经改掉了 processingTasks，后续判断就全落空了。
 */
function cancelDocTasks(ids: string[]) {
  const targets = ids.filter(isDocBusy)
  const s = useStore.getState()
  for (const id of targets) {
    cancelTask(id)
    s.clearDocTasks(id)
  }
}

/** 内联编辑态：id + 草稿值。标题/标签两处同构 */
interface EditState { id: string; value: string }

export function DocLibrary() {
  const docs = useStore(s => s.docs)
  const folders = useStore(s => s.folders)
  const currentId = useStore(s => s.currentDocId)
  const selectDoc = useStore(s => s.selectDoc)
  const setDocs = useStore(s => s.setDocs)
  const setFolders = useStore(s => s.setFolders)
  const searchQuery = useStore(s => s.searchQuery)
  const setSearchQuery = useStore(s => s.setSearchQuery)
  const activeType = useStore(s => s.activeType)
  const setActiveType = useStore(s => s.setActiveType)
  const activeTags = useStore(s => s.activeTags)
  const setActiveTags = useStore(s => s.setActiveTags)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolderId = useStore(s => s.setActiveFolderId)
  const addToast = useStore(s => s.addToast)

  const [prefs, setPrefs] = useState(loadDocLibraryPrefs)
  const sort = prefs.sort

  // 右键菜单
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; docId: string } | null>(null)
  // 内联编辑 —— state 供渲染，ref 供回调读取。
  // ⚠️ 回调**不能**直接闭包 state：那样每次按键都会重建 rowActions 引用，
  // DocRow 的 memo 就白加了（编辑一个标题会让 60 行全部重渲染）。
  const [titleEdit, setTitleEdit] = useState<EditState | null>(null)
  const titleEditRef = useRef<EditState | null>(null)
  const [tagsEdit, setTagsEdit] = useState<EditState | null>(null)
  const tagsEditRef = useRef<EditState | null>(null)
  // 多选
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // 删除确认（单条 = 长度为 1）
  const [deleteTargets, setDeleteTargets] = useState<string[] | null>(null)
  const [batchBusy, setBatchBusy] = useState(false)
  // 拖拽 —— onDragOver 阶段读不到 payload，悬停高亮只能靠这个 state
  const [draggingIds, setDraggingIds] = useState<Set<string>>(new Set())
  const dragging = draggingIds.size > 0

  const { visible, matchedIds, contentOnly, searching } = useDocQuery({
    docs, query: searchQuery, type: activeType, tags: activeTags, folderId: activeFolderId, sort,
  })

  const tagFacets = useMemo(() => collectTagFacets(docs), [docs])
  const uncategorizedCount = useMemo(() => docs.filter(d => !d.folderId).length, [docs])

  // ── 分批渲染 ──
  const [limit, setLimit] = useState(PAGE_SIZE)
  // 筛选/排序变化时重置——否则用户在新条件下看到的是「上次的加载深度」，像随机截断
  useEffect(() => { setLimit(PAGE_SIZE) }, [searchQuery, activeType, activeTags, activeFolderId, sort])
  const rendered = visible.slice(0, limit)
  const hasMore = limit < visible.length
  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || !hasMore) return
    const io = new IntersectionObserver(
      entries => { if (entries[0].isIntersecting) setLimit(l => l + PAGE_SIZE) },
      { rootMargin: '240px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasMore, limit])

  // 筛选条件变化时清空选择——否则会选中用户看不见的文档
  useEffect(() => { setSelectedIds(new Set()); setSelectMode(false) },
    [searchQuery, activeType, activeTags, activeFolderId])

  const refreshFolders = useCallback(async () => {
    try { setFolders(await listFolders()) } catch { /* 计数刷新失败无碍主流程 */ }
  }, [setFolders])

  const setSort = (key: SortKey) => {
    setPrefs({ sort: key })
    saveDocLibraryPrefs({ sort: key })
  }

  // ── 单条编辑 ──

  const applyTitleEdit = useCallback((v: EditState | null) => {
    titleEditRef.current = v
    setTitleEdit(v)
  }, [])
  const applyTagsEdit = useCallback((v: EditState | null) => {
    tagsEditRef.current = v
    setTagsEdit(v)
  }, [])

  const saveTitle = useCallback(async (id: string, title: string) => {
    const next = title.trim()
    applyTitleEdit(null)
    if (!next) { addToast('标题不能为空', 'error'); return }
    const prev = useStore.getState().docs
    setDocs(prev.map(d => d.id === id ? { ...d, title: next } : d))
    try {
      await updateTranscript(id, { title: next })
    } catch (err) {
      setDocs(prev)          // 回滚乐观更新，不制造「假成功」
      addToast(getErrorMessage(err), 'error')
    }
  }, [addToast, setDocs, applyTitleEdit])

  const saveTags = useCallback(async (id: string, raw: string) => {
    const tags = raw.split(/[,，]/).map(t => t.trim()).filter(Boolean)
    applyTagsEdit(null)
    const prev = useStore.getState().docs
    setDocs(prev.map(d => d.id === id ? { ...d, tags } : d))
    try {
      await updateTranscript(id, { tags })
    } catch (err) {
      setDocs(prev)
      addToast(getErrorMessage(err), 'error')
    }
  }, [addToast, setDocs, applyTagsEdit])

  // ── 移动（拖拽 / 批量条 / 右键菜单共用同一条路径）──

  const moveDocs = useCallback(async (ids: string[], folderId: string) => {
    if (!ids.length) return
    const prev = useStore.getState().docs
    setDocs(prev.map(d => ids.includes(d.id) ? { ...d, folderId } : d))
    try {
      const r = await batchTranscripts({ action: 'move', ids, folderId })
      if (r.failed?.length) addToast(`已移动 ${r.affected} 个，${r.failed.length} 个失败`, 'error')
    } catch (err) {
      setDocs(prev)
      addToast(getErrorMessage(err), 'error')
      return
    }
    refreshFolders()
  }, [addToast, setDocs, refreshFolders])

  const handleDragStart = useCallback((e: React.DragEvent, doc: DocEntry) => {
    // 拖已选中的项 = 整批拖；拖未选中的项 = 只拖它自己
    const ids = selectedIds.has(doc.id) ? [...selectedIds] : [doc.id]
    setDraggingIds(new Set(ids))
    // ⚠️ 只设自定义 MIME：设 text/plain 会让拖拽内容被粘进对话输入框
    e.dataTransfer.setData('application/x-limo-doc', ids.join(','))
    e.dataTransfer.effectAllowed = 'move'
  }, [selectedIds])

  const handleDragEnd = useCallback(() => setDraggingIds(new Set()), [])

  const handleFolderDrop = useCallback((folderId: string, e: React.DragEvent) => {
    const raw = e.dataTransfer.getData('application/x-limo-doc')
    setDraggingIds(new Set())
    // drop 阶段才读得到 payload（dragOver 阶段浏览器一律返回空串）
    const ids = raw ? raw.split(',').filter(Boolean) : []
    moveDocs(ids, folderId)
  }, [moveDocs])

  // ── 删除 ──

  const runDelete = useCallback(async (ids: string[]) => {
    setDeleteTargets(null)
    cancelDocTasks(ids)              // 顺序约束见 cancelDocTasks 注释
    setBatchBusy(true)
    try {
      const r = await batchTranscripts({ action: 'delete', ids })
      const failed = r.failed || []
      const okIds = ids.filter(id => !failed.some(f => f.id === id))
      const s = useStore.getState()
      setDocs(s.docs.filter(d => !okIds.includes(d.id)))
      if (s.currentDocId && okIds.includes(s.currentDocId)) selectDoc('')
      if (failed.length) addToast(`已删除 ${r.affected} 个，${failed.length} 个失败`, 'error')
      else addToast(`已删除 ${r.affected} 个文档`)
    } catch (err) {
      addToast(getErrorMessage(err), 'error')
    } finally {
      setBatchBusy(false)
      setSelectedIds(new Set())
      setSelectMode(false)
      refreshFolders()
    }
  }, [addToast, setDocs, selectDoc, refreshFolders])

  // ── 批量 ──

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      if (next.size === 0) setSelectMode(false)
      return next
    })
  }, [])

  const allSelected = visible.length > 0 && visible.every(d => selectedIds.has(d.id))
  const toggleSelectAll = useCallback(() => {
    setSelectedIds(prev => {
      if (visible.length > 0 && visible.every(d => prev.has(d.id))) return new Set()
      // 全选 = 当前筛选结果的**全部匹配项**，跨分批（不只是已渲染的 60 条）
      return new Set(visible.map(d => d.id))
    })
  }, [visible])

  const runBatch = useCallback(async (fn: () => Promise<void>) => {
    setBatchBusy(true)
    try { await fn() } finally { setBatchBusy(false) }
  }, [])

  const handleBatchExport = useCallback(() => runBatch(async () => {
    const count = selectedIds.size
    if (!count) return
    const ids = [...selectedIds]
    setSelectedIds(new Set()); setSelectMode(false)
    try {
      const result = await exportBatch(ids)
      if (result.ok) addToast(`已导出 ${count} 个文档，请查看下载文件夹`)
      else addToast(`导出失败: ${result.error}`, 'error')
    } catch (e) {
      addToast('导出失败: ' + (e as Error).message, 'error')
    }
  }), [selectedIds, runBatch, addToast])

  const handleBatchTag = useCallback((tags: string[], mode: 'add' | 'replace') =>
    runBatch(async () => {
      const ids = [...selectedIds]
      try {
        const r = await batchTranscripts({ action: 'tag', ids, tags, tagMode: mode })
        const failed = new Set((r.failed || []).map(f => f.id))
        setDocs(useStore.getState().docs.map(d => {
          if (!ids.includes(d.id) || failed.has(d.id)) return d
          const merged = mode === 'replace' ? tags : [...new Set([...(d.tags || []), ...tags])]
          return { ...d, tags: merged }
        }))
        if (r.failed?.length) addToast(`已打标签 ${r.affected} 个，${r.failed.length} 个失败`, 'error')
        else addToast(`已为 ${r.affected} 个文档打标签`)
      } catch (err) {
        addToast(getErrorMessage(err), 'error')
      }
    }), [selectedIds, runBatch, addToast, setDocs])

  // ── 右键菜单 ──

  const ctxDoc = docs.find(d => d.id === ctxMenu?.docId)
  const ctxItems = useMemo(() => {
    if (!ctxDoc) return []
    return [
      { label: '打开', icon: <BookOpen size={14} />, onClick: () => selectDoc(ctxDoc.id) },
      { label: '重新生成', icon: <RefreshCw size={14} />, onClick: () => { selectDoc(ctxDoc.id); useStore.getState().setGenPanelOpen(true) } },
      { label: 'AI 对话', icon: <MessageSquare size={14} />, onClick: () => { selectDoc(ctxDoc.id); useStore.getState().setAiVisible(true) } },
      { label: '', onClick: () => {}, divider: true },
      {
        // ⚠️ 非拖拽的移动路径：只做 DnD 会让键盘/触屏用户完全无法移动文档
        label: '移动到文件夹', icon: <FolderInput size={14} />,
        submenu: [
          { label: '未分类（移出文件夹）', onClick: () => moveDocs([ctxDoc.id], '') },
          ...folders.map(f => ({ label: f.name, onClick: () => moveDocs([ctxDoc.id], f.id) })),
        ],
      },
      { label: '编辑标题', icon: <Pencil size={14} />, onClick: () => applyTitleEdit({ id: ctxDoc.id, value: ctxDoc.title }) },
      { label: '编辑标签', icon: <Tag size={14} />, onClick: () => applyTagsEdit({ id: ctxDoc.id, value: (ctxDoc.tags || []).join(', ') }) },
      { label: '复制 Markdown', icon: <Clipboard size={14} />, onClick: () => {
        const content = useStore.getState().currentContent
        if (content) {
          navigator.clipboard.writeText(content).then(
            () => addToast('已复制到剪贴板'),
            () => addToast('复制失败，请手动复制', 'error'),
          )
        }
      }},
      { label: '', onClick: () => {}, divider: true },
      { label: '删除', icon: <Trash2 size={14} />, onClick: () => setDeleteTargets([ctxDoc.id]), danger: true },
    ]
  }, [ctxDoc, folders, moveDocs, selectDoc, addToast, applyTitleEdit, applyTagsEdit])

  const onContextMenu = useCallback((e: React.MouseEvent, docId: string) => {
    e.preventDefault()
    setCtxMenu({ x: e.clientX, y: e.clientY, docId })
  }, [])

  const handleRowDelete = useCallback((id: string) => setDeleteTargets([id]), [])

  // ── DocRow 回调（引用稳定，让 React.memo 真的生效）──
  const rowActions: DocRowActions = useMemo(() => ({
    onSelect: selectDoc,
    onToggleSelect: toggleSelect,
    onContextMenu,
    onEditTitle: d => applyTitleEdit({ id: d.id, value: d.title }),
    onEditTags: d => applyTagsEdit({ id: d.id, value: (d.tags || []).join(', ') }),
    onDelete: handleRowDelete,
    onDragStart: handleDragStart,
    onDragEnd: handleDragEnd,
    onTitleDraftChange: v => {
      const cur = titleEditRef.current
      if (cur) applyTitleEdit({ ...cur, value: v })
    },
    onTitleCommit: () => {
      const cur = titleEditRef.current
      if (cur) saveTitle(cur.id, cur.value)
    },
    onTitleCancel: () => applyTitleEdit(null),
    onTagsDraftChange: v => {
      const cur = tagsEditRef.current
      if (cur) applyTagsEdit({ ...cur, value: v })
    },
    onTagsCommit: () => {
      const cur = tagsEditRef.current
      if (cur) saveTags(cur.id, cur.value)
    },
    onTagsCancel: () => applyTagsEdit(null),
  }), [selectDoc, toggleSelect, onContextMenu, applyTitleEdit, applyTagsEdit, handleRowDelete,
       handleDragStart, handleDragEnd, saveTitle, saveTags])

  const hasFilter = !!(searchQuery.trim() || activeType || activeTags.length || activeFolderId !== null)

  return (
    <aside className="w-full h-full border-r border-border-light flex flex-col bg-elevated/60 backdrop-blur-[20px]">
      {/* 标题栏 / 批量工具条
          ⚠️ 显示条件是 `selectMode || 有选中`，不能只判选中数：否则刚点开
          「批量操作」时一条都还没勾，工具条不出现 —— 用户看得见「全选」按钮
          之前必须先手动勾一条，而「全选」正是他本来想按的那个键。 */}
      {selectMode || selectedIds.size > 0 ? (
        <BatchBar
          selectedCount={selectedIds.size}
          renderedCount={rendered.length}
          totalMatches={visible.length}
          folders={folders}
          busy={batchBusy}
          allSelected={allSelected}
          onToggleSelectAll={toggleSelectAll}
          onExport={handleBatchExport}
          onMove={fid => { setSelectMode(false); runBatch(() => moveDocs([...selectedIds], fid)) }}
          onTag={handleBatchTag}
          onDelete={() => setDeleteTargets([...selectedIds])}
          onClear={() => { setSelectedIds(new Set()); setSelectMode(false) }}
        />
      ) : (
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-light shrink-0">
          <span className="text-[12px] font-medium text-dimmed">文档库</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setSelectMode(!selectMode)}
              className={`w-6 h-6 rounded-md flex items-center justify-center ${selectMode ? 'bg-accent/10 text-accent' : 'hover:bg-surface-hover text-muted'}`}
              title="批量操作">
              <CheckSquare size={14} />
            </button>
            <button
              onClick={() => useStore.getState().setSlot('docLib', { collapsed: true })}
              className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center"
              title="收起面板"
            >
              <PanelLeftClose size={14} className="text-muted" />
            </button>
          </div>
        </div>
      )}

      {/* 搜索栏 */}
      <div className="px-3 pt-2">
        <div className="flex items-center gap-2 bg-surface rounded-lg px-2.5 py-1.5">
          <Search size={14} className="text-disabled shrink-0" />
          <input placeholder="搜索标题或正文…" value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
            className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-disabled" />
          {searching && <span className="shrink-0 w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />}
          {searchQuery && !searching && (
            <button onClick={() => setSearchQuery('')} className="shrink-0"><X size={12} className="text-disabled hover:text-dimmed" /></button>
          )}
        </div>
      </div>

      {/* 文件夹 */}
      <div className="pt-2">
        <FolderTree
          folders={folders}
          activeFolderId={activeFolderId}
          totalCount={docs.length}
          uncategorizedCount={uncategorizedCount}
          dragging={dragging}
          onSelect={setActiveFolderId}
          onCreate={async (name) => {
            try { await createFolder(name); await refreshFolders() }
            catch (err) { addToast(getErrorMessage(err), 'error') }
          }}
          onRename={async (id, name) => {
            try { await renameFolder(id, name); await refreshFolders() }
            catch (err) { addToast(getErrorMessage(err), 'error') }
          }}
          onDelete={async (folder: FolderEntry) => {
            try {
              const r = await deleteFolder(folder.id)
              await refreshFolders()
              // 文档回落未分类——本地同步，否则当前文件夹视图里还挂着已删文件夹的文档
              setDocs(useStore.getState().docs.map(d =>
                d.folderId === folder.id ? { ...d, folderId: '' } : d))
              if (activeFolderId === folder.id) setActiveFolderId(null)
              addToast(`已删除「${folder.name}」，${r.moved} 个文档已移到未分类`)
            } catch (err) {
              addToast(getErrorMessage(err), 'error')
            }
          }}
          onDrop={handleFolderDrop}
        />
      </div>

      {/* 类型筛选 + 排序 */}
      <div className="px-3 pb-1 flex items-center gap-1 flex-wrap">
        {SOURCE_FILTERS.map(t => (
          <button key={t.key} onClick={() => setActiveType(t.key)}
            className={`px-2 py-0.5 rounded-full text-[11px] transition-colors ${activeType === t.key ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover cursor-pointer'}`}>
            {t.icon && <IconByName name={t.icon} size={13} />} {t.label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-0.5">
          <ArrowUpDown size={11} className="text-disabled" />
          {SORT_OPTIONS.map(o => (
            <button key={o.key} onClick={() => setSort(o.key)}
              className={`px-1.5 py-0.5 rounded text-[11px] ${sort === o.key ? 'text-accent font-medium' : 'text-disabled hover:text-dimmed'}`}>
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* 用户标签筛选（facet，多选 AND） */}
      {tagFacets.length > 0 && (
        <div className="px-3 pb-2 flex items-center gap-1 flex-wrap">
          <Tag size={11} className="text-disabled shrink-0" />
          {tagFacets.slice(0, 12).map(({ tag, count }) => {
            const on = activeTags.includes(tag)
            return (
              <button key={tag}
                onClick={() => setActiveTags(on ? activeTags.filter(t => t !== tag) : [...activeTags, tag])}
                className={`px-2 py-0.5 rounded-full text-[11px] transition-colors ${on ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover'}`}>
                {tag} <span className="text-disabled">{count}</span>
              </button>
            )
          })}
          {activeTags.length > 0 && (
            <button onClick={() => setActiveTags([])} className="text-[11px] text-muted hover:text-dimmed">清除</button>
          )}
        </div>
      )}

      {/* 文档列表 */}
      <div className="flex-1 overflow-y-auto px-2.5 space-y-1.5">
        {visible.length === 0 && contentOnly.length === 0 && (
          <p className="text-[12px] text-disabled text-center py-8 whitespace-pre-line">
            {hasFilter ? '没有匹配的文档' : '还没有文档\n粘贴链接或文字开始'}
          </p>
        )}
        {rendered.map((doc, i) => (
          <DocRow
            key={doc.id}
            doc={doc}
            index={i}
            active={doc.id === currentId}
            selected={selectedIds.has(doc.id)}
            selectMode={selectMode}
            matched={matchedIds.has(doc.id)}
            titleDraft={titleEdit?.id === doc.id ? titleEdit.value : null}
            tagsDraft={tagsEdit?.id === doc.id ? tagsEdit.value : null}
            actions={rowActions}
          />
        ))}
        {hasMore && <div ref={sentinelRef} className="h-6" />}

        {/* 原文匹配分组 —— 只列内存过滤覆盖不到的命中，固定追加在下方（不参与用户排序） */}
        {contentOnly.length > 0 && (
          <div className="pt-2">
            <p className="text-[11px] text-disabled px-1 pb-1">原文匹配（{contentOnly.length}）</p>
            {contentOnly.map(h => (
              <button key={h.id} onClick={() => selectDoc(h.id)}
                className="w-full text-left rounded-xl border border-transparent hover:border-border-light hover:bg-surface-hover py-2 px-3 mb-1">
                <p className="text-[13px] font-medium leading-snug line-clamp-1">{h.title}</p>
                <SearchSnippet raw={h.snippet} className="block text-[11px] text-disabled mt-0.5 line-clamp-2" />
              </button>
            ))}
          </div>
        )}
      </div>

      {ctxMenu && <ContextMenu x={ctxMenu.x} y={ctxMenu.y} items={ctxItems} onClose={() => setCtxMenu(null)} />}

      {/* 删除确认 */}
      {deleteTargets && (
        <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setDeleteTargets(null)}>
          <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light p-6 max-w-[320px] w-full mx-4 animate-[fadeIn_0.15s_ease-out]" onClick={e => e.stopPropagation()}>
            <h3 className="text-[17px] font-semibold mb-2">
              {deleteTargets.length > 1 ? `删除 ${deleteTargets.length} 个文档` : '删除文档'}
            </h3>
            <p className="text-[13px] text-dimmed mb-6">
              {deleteTargets.length > 1
                ? <>确定要删除选中的 {deleteTargets.length} 个文档吗？关联的 Markdown 也会被删除。</>
                : <>确定要删除「{docs.find(d => d.id === deleteTargets[0])?.title}」吗？关联的 Markdown 也会被删除。</>}
              {deleteTargets.some(isDocBusy)
                ? <span className="text-warning mt-1 block">其中部分文档有进行中的任务，删除将同时取消。</span>
                : '此操作不可撤销。'}
            </p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setDeleteTargets(null)}
                className="px-4 py-2 text-[13px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
              <button data-confirm-delete onClick={() => runDelete(deleteTargets)}
                className="px-4 py-2 bg-error text-white text-[13px] font-medium rounded-lg hover:bg-error/80 transition-colors">删除</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
