import { useState, useMemo, useCallback } from 'react'
import { useStore } from '../../store'
import { deleteTranscript, updateTranscript, appUrl } from '../../api/client'
import { ContextMenu } from '../ui/ContextMenu'
import { Search, X, CheckCircle2, Loader2, Circle, PanelLeftClose, Pencil, Trash2, Film, Globe, FileText, PenLine, BarChart3, TrendingUp, BookOpen, Paperclip, MessageSquare, Image as ImageIcon, Download, Music, Mic, Bot, Eye, Settings, RefreshCw, Tag, Clipboard, Archive, CheckSquare, Square } from 'lucide-react'
import { exportBatch } from '../../api/client'

const TAG_FILTERS = [
  { key: '', label: '全部', icon: '' },
  { key: 'video', label: '视频', icon: 'Film' },
  { key: 'webpage', label: '网页', icon: 'Globe' },
  { key: 'pdf', label: 'PDF', icon: 'FileText' },
  { key: 'docx', label: 'Word', icon: 'PenLine' },
  { key: 'pptx', label: 'PPT', icon: 'BarChart3' },
  { key: 'xlsx', label: 'Excel', icon: 'TrendingUp' },
  { key: 'epub', label: 'EPUB', icon: 'BookOpen' },
  { key: 'document', label: '文档', icon: 'Paperclip' },
  { key: 'chat', label: '聊天', icon: 'MessageSquare' },
  { key: 'image', label: '图片', icon: 'Image' },
  { key: 'text', label: '文本', icon: 'FileText' },
]

/** sourceType → 可读标签 */
const SRC_LABEL: Record<string, string> = Object.fromEntries(
  TAG_FILTERS.filter(t => t.key).map(t => [t.key, t.label])
)

/** sourceType → 主题色（缩略图背景 + 类型标签）。bg 使用 rgba 透明度，亮暗模式均适用 */
const TYPE_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  video:    { bg: 'rgba(255,59,48,0.12)',  text: '#FF3B30', dot: '#FF3B30' },
  webpage:  { bg: 'rgba(52,199,89,0.12)',  text: '#34C759', dot: '#34C759' },
  pdf:      { bg: 'rgba(255,149,0,0.12)',  text: '#FF9500', dot: '#FF9500' },
  docx:     { bg: 'rgba(0,122,255,0.12)',  text: '#007AFF', dot: '#007AFF' },
  pptx:     { bg: 'rgba(255,107,53,0.12)', text: '#FF6B35', dot: '#FF6B35' },
  xlsx:     { bg: 'rgba(48,179,92,0.12)',  text: '#30B35C', dot: '#30B35C' },
  epub:     { bg: 'rgba(175,82,222,0.12)', text: '#AF52DE', dot: '#AF52DE' },
  image:    { bg: 'rgba(255,45,85,0.12)',  text: '#FF2D55', dot: '#FF2D55' },
  text:     { bg: 'rgba(142,142,147,0.12)',text: '#8E8E93', dot: '#8E8E93' },
  document: { bg: 'rgba(0,122,255,0.12)',  text: '#007AFF', dot: '#007AFF' },
  chat:     { bg: 'rgba(90,200,250,0.12)', text: '#5AC8FA', dot: '#5AC8FA' },
}

const STAGE_ICONS: Record<string, string> = {
  fetching_subtitles: 'FileText', downloading: 'Download', extracting_audio: 'Music',
  transcribing: 'Mic', done: 'CheckCircle2', summarizing: 'Bot',
  ocr: 'Eye', preprocess: 'Settings', import: 'Download',
}

/** 根据名称渲染对应的 lucide 图标（替代 emoji） */
function IconByName({ name, size = 14 }: { name: string; size?: number }) {
  switch (name) {
    case 'Film': return <Film size={size} />
    case 'Globe': return <Globe size={size} />
    case 'FileText': return <FileText size={size} />
    case 'PenLine': return <PenLine size={size} />
    case 'BarChart3': return <BarChart3 size={size} />
    case 'TrendingUp': return <TrendingUp size={size} />
    case 'BookOpen': return <BookOpen size={size} />
    case 'Paperclip': return <Paperclip size={size} />
    case 'MessageSquare': return <MessageSquare size={size} />
    case 'Image': return <ImageIcon size={size} />
    case 'Download': return <Download size={size} />
    case 'Music': return <Music size={size} />
    case 'Mic': return <Mic size={size} />
    case 'CheckCircle2': return <CheckCircle2 size={size} />
    case 'Bot': return <Bot size={size} />
    case 'Eye': return <Eye size={size} />
    case 'Settings': return <Settings size={size} />
    default: return <FileText size={size} />
  }
}
const STAGE_LABELS: Record<string, string> = {
  fetching_subtitles: '拉取字幕', downloading: '下载音频',
  extracting_audio: '提取音频', transcribing: '语音转写',
  summarizing: 'AI生成', done: '完成',
}

export function DocLibrary() {
  const docs = useStore(s => s.docs)
  const currentId = useStore(s => s.currentDocId)
  const selectDoc = useStore(s => s.selectDoc)
  const setDocs = useStore(s => s.setDocs)
  const progress = useStore(s => s.importProgress)
  const importStages = useStore(s => s.importStages)
  const searchQuery = useStore(s => s.searchQuery)
  const setSearchQuery = useStore(s => s.setSearchQuery)
  const activeTag = useStore(s => s.activeTag)
  const setActiveTag = useStore(s => s.setActiveTag)
  // 右键菜单
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; docId: string } | null>(null)

  // 编辑状态
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editingTagsId, setEditingTagsId] = useState<string | null>(null)
  const [editTags, setEditTags] = useState('')

  // 删除确认
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)

  // 多选导出
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const toggleSelect = (id: string) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id); else next.add(id)
    setSelectedIds(next)
    if (next.size === 0) setSelectMode(false)
  }
  const handleBatchExport = async () => {
    if (selectedIds.size === 0) return
    const count = selectedIds.size
    setSelectedIds(new Set())
    setSelectMode(false)
    try {
      const result = await exportBatch([...selectedIds])
      if (result.ok) {
        addToast(`已导出 ${count} 个文档，请查看下载文件夹`)
      } else {
        addToast(`导出失败: ${result.error}`, 'error')
      }
    } catch (e) {
      addToast('导出失败: ' + (e as Error).message, 'error')
    }
  }
  // 需要从 store 拿 addToast
  const addToast = useStore(s => s.addToast)

  const filteredDocs = useMemo(() => {
    let result = docs
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      result = result.filter(d =>
        d.title.toLowerCase().includes(q) ||
        d.sourceType.toLowerCase().includes(q) ||
        (d.language && d.language.toLowerCase().includes(q))
      )
    }
    if (activeTag) {
      result = result.filter(d => d.sourceType === activeTag)
    }
    return result
  }, [docs, searchQuery, activeTag])

  // 右键菜单项
  const ctxDoc = docs.find(d => d.id === ctxMenu?.docId)
  const ctxItems = useMemo(() => {
    if (!ctxDoc) return []
    return [
      { label: '打开', icon: <BookOpen size={14} />, onClick: () => selectDoc(ctxDoc.id) },
      { label: '重新生成', icon: <RefreshCw size={14} />, onClick: () => { selectDoc(ctxDoc.id); useStore.getState().setGenPanelOpen(true) } },
      { label: 'AI 对话', icon: <MessageSquare size={14} />, onClick: () => { selectDoc(ctxDoc.id); useStore.getState().setAiVisible(true) } },
      { label: '', icon: undefined, onClick: () => {}, divider: true },
      { label: '编辑标题', icon: <Pencil size={14} />, onClick: () => { setEditTitle(ctxDoc.title); setEditingId(ctxDoc.id) } },
      { label: '编辑标签', icon: <Tag size={14} />, onClick: () => { setEditTags((ctxDoc.tags || []).join(', ')); setEditingTagsId(ctxDoc.id) } },
      { label: '复制 Markdown', icon: <Clipboard size={14} />, onClick: () => {
        const content = useStore.getState().currentContent
        if (content) {
          navigator.clipboard.writeText(content).then(
            () => addToast('已复制到剪贴板'),
            () => addToast('复制失败，请手动复制', 'error'),
          )
        }
      }},
      { label: '', icon: '', onClick: () => {}, divider: true },
      { label: '删除', icon: <Trash2 size={14} />, onClick: () => setDeleteConfirmId(ctxDoc.id), danger: true },
    ]
  }, [ctxDoc])

  const onContextMenu = useCallback((e: React.MouseEvent, docId: string) => {
    e.preventDefault()
    setCtxMenu({ x: e.clientX, y: e.clientY, docId })
  }, [])

  // 保存标题
  const saveTitle = async (id: string) => {
    if (!editTitle.trim()) {
      addToast('标题不能为空', 'error')
      return
    }
    await updateTranscript(id, { title: editTitle.trim() })
    setDocs(docs.map(d => d.id === id ? { ...d, title: editTitle.trim() } : d))
    setEditingId(null)
    addToast('标题已更新')
  }

  // 保存标签
  const saveTags = async (id: string) => {
    const tags = editTags.split(/[,，]/).map(t => t.trim()).filter(Boolean)
    await updateTranscript(id, { tags })
    setDocs(docs.map(d => d.id === id ? { ...d, tags } : d))
    setEditingTagsId(null)
  }

  // 删除
  const confirmDelete = async (id: string) => {
    await deleteTranscript(id)
    setDocs(docs.filter(d => d.id !== id))
    if (currentId === id) selectDoc('')
    setDeleteConfirmId(null)
  }

  return (
    <aside className="w-full h-full border-r border-border-light flex flex-col bg-elevated/60 backdrop-blur-[20px]">
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-light shrink-0">
        {selectedIds.size > 0 ? (
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-medium text-accent">已选 {selectedIds.size} 项</span>
            <button onClick={handleBatchExport}
              className="flex items-center gap-1 px-2.5 py-1 bg-accent text-white text-[11px] font-medium rounded-full hover:bg-accent-hover transition-colors">
              <Archive size={11} />导出 ZIP
            </button>
            <button onClick={() => { setSelectedIds(new Set()); setSelectMode(false) }}
              className="text-[11px] text-muted hover:text-dimmed">取消</button>
          </div>
        ) : (
          <>
            <span className="text-[12px] font-medium text-dimmed">文档库</span>
            <div className="flex items-center gap-1">
              <button onClick={() => setSelectMode(!selectMode)}
                className={`w-6 h-6 rounded-md flex items-center justify-center ${selectMode ? 'bg-accent/10 text-accent' : 'hover:bg-surface-hover text-muted'}`}
                title="批量导出">
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
          </>
        )}
      </div>

      {/* 搜索栏 */}
      <div className="px-3 py-2">
        <div className="flex items-center gap-2 bg-surface rounded-lg px-2.5 py-1.5">
          <Search size={14} className="text-disabled shrink-0" />
          <input placeholder="搜索..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
            className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-disabled" />
          {searchQuery && <button onClick={() => setSearchQuery('')} className="shrink-0"><X size={12} className="text-disabled hover:text-dimmed" /></button>}
        </div>
      </div>

      {/* 标签筛选 */}
      <div className="px-3 pb-2 flex gap-1 flex-wrap">
        {TAG_FILTERS.map(t => (
          <button key={t.key} onClick={() => setActiveTag(t.key)}
            className={`px-2 py-0.5 rounded-full text-[11px] transition-colors ${activeTag === t.key ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover cursor-pointer'}`}>
            {t.icon && <IconByName name={t.icon} size={13} />} {t.label}
          </button>
        ))}
      </div>

      {/* 文档列表 */}
      <div className="flex-1 overflow-y-auto px-2.5 space-y-1.5">
        {filteredDocs.length === 0 && !progress && (
          <p className="text-[12px] text-disabled text-center py-8">
            {searchQuery || activeTag ? '没有匹配的文档' : '还没有文档\n粘贴链接或文字开始'}
          </p>
        )}
        {filteredDocs.map((doc, i) => {
          const colors = TYPE_COLORS[doc.sourceType] || TYPE_COLORS.text
          const typeIcon = TAG_FILTERS.find(t => t.key === doc.sourceType)?.icon || 'FileText'
          const isSelected = doc.id === currentId
          return (
          <div key={doc.id}
            onClick={() => selectMode ? toggleSelect(doc.id) : selectDoc(doc.id)}
            onContextMenu={(e) => onContextMenu(e, doc.id)}
            className={`group relative cursor-pointer transition-all animate-[card-enter_0.3s_ease-out_backwards]
              rounded-xl border py-2.5 px-3
              ${selectedIds.has(doc.id)
                ? 'border-accent/30 bg-accent/5 shadow-sm'
                : isSelected
                  ? 'border-accent/30 bg-accent/5 shadow-sm'
                  : 'border-transparent hover:border-border-light hover:bg-surface-hover'
              }`}
            style={{ animationDelay: `${i * 50}ms` }}
          >
            {/* 编辑标题 — 内联 */}
            {editingId === doc.id ? (
              <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                <input
                  value={editTitle}
                  onChange={e => setEditTitle(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveTitle(doc.id); if (e.key === 'Escape') setEditingId(null) }}
                  onBlur={() => saveTitle(doc.id)}
                  autoFocus
                  className="flex-1 px-2 py-1 text-[13px] bg-elevated border border-accent rounded-md outline-none"
                />
              </div>
            ) : (
              <>
                {/* 主行：复选框 + 缩略图 + 标题 + 操作按钮 */}
                <div className="flex items-start gap-2.5">
                  {/* 复选框（选择模式下显示） */}
                  {selectMode && (
                    <button onClick={(e) => { e.stopPropagation(); toggleSelect(doc.id) }}
                      className="shrink-0 mt-0.5">
                      {selectedIds.has(doc.id)
                        ? <CheckSquare size={18} className="text-accent" />
                        : <Square size={18} className="text-disabled" />}
                    </button>
                  )}
                  {/* 左侧：封面图 或 类型图标（带颜色背景） */}
                  {doc.coverUrl ? (
                    <img src={appUrl(doc.coverUrl)} alt="" referrerPolicy="no-referrer"
                      className="w-10 h-10 rounded-lg object-cover shrink-0 mt-0.5" />
                  ) : (
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                      style={{ backgroundColor: colors.bg, color: colors.text }}>
                      <IconByName name={typeIcon} size={18} />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <span className="text-[13px] font-medium leading-snug line-clamp-2">{doc.title}</span>
                  </div>
                  {/* hover 显示编辑/删除快捷按钮 */}
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 shrink-0">
                    <button onClick={(e) => { e.stopPropagation(); setEditTitle(doc.title); setEditingId(doc.id) }}
                      className="w-5 h-5 rounded hover:bg-surface-hover flex items-center justify-center" title="编辑标题">
                      <Pencil size={11} className="text-disabled" />
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); setDeleteConfirmId(doc.id) }}
                      className="w-5 h-5 rounded hover:bg-error/10 flex items-center justify-center" title="删除">
                      <Trash2 size={11} className="text-disabled hover:text-error" />
                    </button>
                  </div>
                </div>

                {/* 底部元数据行：日期 · 类型标签 · 自定义标签 */}
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  {doc.createdAt && (
                    <span className="text-[11px] text-disabled">
                      {new Date(doc.createdAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
                    </span>
                  )}
                  {doc.language && (
                    <span className="text-[10px] text-disabled uppercase font-medium px-1.5 py-0.5 bg-surface-hover rounded-full">{doc.language}</span>
                  )}
                  {/* 类型彩色标签 */}
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full"
                    style={{ backgroundColor: colors.bg, color: colors.text }}>
                    {SRC_LABEL[doc.sourceType] || doc.sourceType}
                  </span>
                  {doc.tags && doc.tags.length > 0 && doc.tags.slice(0, 2).map(t => (
                    <span key={t} className="px-1.5 py-0.5 bg-surface-hover text-disabled rounded-full text-[10px]">{t}</span>
                  ))}
                </div>
              </>
            )}

            {/* 标签编辑 — 内联 */}
            {editingTagsId === doc.id && (
              <div className="mt-1 flex items-center gap-1" onClick={e => e.stopPropagation()}>
                <input
                  value={editTags}
                  onChange={e => setEditTags(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveTags(doc.id); if (e.key === 'Escape') setEditingTagsId(null) }}
                  onBlur={() => saveTags(doc.id)}
                  autoFocus
                  placeholder="标签, 逗号分隔"
                  className="flex-1 px-2 py-0.5 text-[11px] bg-elevated border border-accent rounded-md outline-none"
                />
              </div>
            )}
          </div>
        )})}
      </div>

      {/* 进度 — BiliNote风格步骤指示器 */}
      {progress && (
        <div className="px-4 py-3 border-t border-border-light bg-elevated/80">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
              <span className="text-[12px] font-medium text-dimmed">任务进度</span>
            </div>
            {progress.percent != null && (
              <span className="text-[11px] font-medium text-accent">{Math.round(progress.percent)}%</span>
            )}
          </div>
          <div className="h-1 rounded-full bg-surface-hover overflow-hidden mb-2.5">
            <div className="h-full rounded-full bg-accent transition-all duration-500"
              style={{ width: `${progress.percent ?? (importStages.length > 0 ? Math.min(95, importStages.filter(s => s.status === 'done').length / Math.max(1, importStages.length) * 100) : 50)}%` }} />
          </div>
          {importStages.length > 0 && (
            <div className="space-y-1.5">
              {importStages.map((st, i) => (
                <div key={st.phase} className="flex items-center gap-2 text-[11px]">
                  <span className="w-4 text-center text-[10px] text-disabled">{i + 1}</span>
                  {st.status === 'done' ? <CheckCircle2 size={13} className="text-success shrink-0" />
                    : st.status === 'running' ? <Loader2 size={13} className="text-accent animate-spin shrink-0" />
                    : st.status === 'error' ? <X size={13} className="text-error shrink-0" />
                    : <Circle size={13} className="text-disabled shrink-0" />}
                  <span className={
                    st.status === 'done' ? 'text-success font-medium' :
                    st.status === 'running' ? 'text-accent font-medium' :
                    st.status === 'error' ? 'text-error' : 'text-disabled'
                  }>
                    <IconByName name={STAGE_ICONS[st.phase] || 'FileText'} size={13} /> {STAGE_LABELS[st.phase] || st.message || st.phase}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 右键菜单 */}
      {ctxMenu && (
        <ContextMenu x={ctxMenu.x} y={ctxMenu.y} items={ctxItems} onClose={() => setCtxMenu(null)} />
      )}

      {/* 删除确认对话框 */}
      {deleteConfirmId && (
        <div className="fixed inset-0 z-[300] flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setDeleteConfirmId(null)}>
          <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light p-6 max-w-[320px] w-full mx-4 animate-[fadeIn_0.15s_ease-out]" onClick={e => e.stopPropagation()}>
            <h3 className="text-[17px] font-semibold mb-2">删除文档</h3>
            <p className="text-[13px] text-dimmed mb-6">
              确定要删除「{docs.find(d => d.id === deleteConfirmId)?.title}」吗？关联的 Markdown 也会被删除。此操作不可撤销。
            </p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setDeleteConfirmId(null)}
                className="px-4 py-2 text-[13px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
              <button onClick={() => confirmDelete(deleteConfirmId!)}
                className="px-4 py-2 bg-error text-white text-[13px] font-medium rounded-lg hover:bg-error/80 transition-colors">删除</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
