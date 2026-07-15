import { useState, useMemo, useEffect } from 'react'
import { useStore } from '../../store'
import { X, Download, FileText, Globe, File, Archive, Search, ChevronUp, ChevronDown, Film, PenLine, Image, BarChart3, BookOpen } from 'lucide-react'
import { getTypeMeta } from '../gen-panel/constants'
import { downloadFromUrl, downloadBlob } from '../../lib/download'
import { apiUrl } from '../../api/client'

type Format = 'md' | 'html' | 'plain'

const FORMAT_META: Record<Format, { icon: typeof FileText; label: string }> = {
  md: { icon: FileText, label: 'Markdown' },
  html: { icon: Globe, label: 'HTML' },
  plain: { icon: File, label: '纯文本' },
}

const TYPE_LABELS: Record<string, string> = {
  video: '视频', webpage: '网页', text: '文本', image: '图片',
  pdf: 'PDF', docx: 'Word', pptx: 'PPT', xlsx: 'Excel', epub: '电子书', document: '文档',
}

const TYPE_ICON: Record<string, typeof FileText> = {
  video: Film, webpage: Globe, text: PenLine, image: Image,
  pdf: FileText, docx: FileText, pptx: FileText, xlsx: BarChart3, epub: BookOpen,
}

export function ExportPanel({ onClose }: { onClose: () => void }) {
  const currentDocId = useStore(s => s.currentDocId)
  const docs = useStore(s => s.docs)
  const addToast = useStore(s => s.addToast)

  // ── 文档选择 ──
  const [selectedIds, setSelectedIds] = useState<string[]>(currentDocId ? [currentDocId] : [])
  const [docSearch, setDocSearch] = useState('')
  const [docListExpanded, setDocListExpanded] = useState(true)

  const filteredDocs = useMemo(() => {
    if (!docSearch) return docs
    const q = docSearch.toLowerCase()
    return docs.filter(d => d.title.toLowerCase().includes(q))
  }, [docs, docSearch])

  const selectedDocs = docs.filter(d => selectedIds.includes(d.id))

  const toggleDoc = (id: string) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id])
  }

  // ── 按类型分组 + 各自独立 tab 选择 ──
  const typeGroups = useMemo(() => {
    const groups = new Map<string, { type: string; docs: typeof selectedDocs; tabs: { key: string; label: string }[] }>()
    selectedDocs.forEach(d => {
      const st = d.sourceType || 'other'
      if (!groups.has(st)) {
        const meta = getTypeMeta(st)
        groups.set(st, { type: st, docs: [], tabs: meta.tabs })
      }
      groups.get(st)!.docs.push(d)
    })
    return Array.from(groups.values())
  }, [selectedDocs])

  const [groupTabs, setGroupTabs] = useState<Record<string, string[]>>({})
  // 仅在新类型出现时补充默认 tab，已有类型保留用户选择
  useEffect(() => {
    setGroupTabs(prev => {
      const next = { ...prev }
      let changed = false
      typeGroups.forEach(g => {
        if (!next[g.type]) {
          next[g.type] = g.tabs.map(t => t.key)
          changed = true
        }
      })
      // 清理已移除的类型
      const activeTypes = new Set(typeGroups.map(g => g.type))
      Object.keys(next).forEach(t => { if (!activeTypes.has(t)) { delete next[t]; changed = true } })
      return changed ? next : prev
    })
  }, [typeGroups])

  const toggleGroupTab = (type: string, key: string) => {
    setGroupTabs(prev => {
      const cur = prev[type] || []
      return { ...prev, [type]: cur.includes(key) ? cur.filter(t => t !== key) : [...cur, key] }
    })
  }

  // ── 格式 ──
  const [formats, setFormats] = useState<Format[]>(['md'])
  const toggleFormat = (f: Format) => {
    setFormats(prev => prev.includes(f) ? prev.filter(x => x !== f) : [...prev, f])
  }

  // ── 设置 ──
  const [formulaMd, setFormulaMd] = useState<'keep' | 'render' | 'remove'>('keep')
  const [imageMd, setImageMd] = useState<'separate' | 'inline' | 'none'>('separate')
  const [formulaPlain, setFormulaPlain] = useState<'remove' | 'unicode'>('remove')
  const [htmlTheme, setHtmlTheme] = useState<'paper' | 'github' | 'minimal' | 'limo'>('limo')

  // ── 文件名 ──
  const date = new Date().toISOString().slice(0, 10)
  const [filename, setFilename] = useState('')
  const [filenameEdited, setFilenameEdited] = useState(false)
  useEffect(() => {
    if (filenameEdited) return  // 手动改过就不再覆盖
    if (selectedIds.length === 1) {
      const t = (docs.find(d => d.id === selectedIds[0])?.title || 'export').replace(/[\\/:*?"<>|]/g, '').slice(0, 60)
      setFilename(`LiMo_${t}_${date}`)
    } else if (selectedIds.length > 1) {
      setFilename(`LiMo_多篇导出_${date}`)
    }
  }, [selectedIds.join(','), date])

  // ── 预览 ──
  const [exporting, setExporting] = useState(false)
  // ── 导出摘要 ──
  const previewLines = useMemo(() => {
    const lines: string[] = []
    let totalFiles = 0
    typeGroups.forEach(g => {
      const tabs = groupTabs[g.type] || []
      if (tabs.length === 0) return
      const label = TYPE_LABELS[g.type] || g.type || '文件'
      lines.push(`${label} ${g.type} (${g.docs.length}篇) → ${tabs.length} 产物`)
      totalFiles += tabs.length * g.docs.length * formats.length
    })
    if (totalFiles === 0) { lines.push('未选择导出内容'); return lines }

    if (totalFiles > 1) {
      lines.push(`共 ${totalFiles} 个文件`)
      if (formats.length > 1) lines.push(`(${formats.map(f => FORMAT_META[f].label).join(' + ')})`)
    }
    if (formats.includes('md')) lines.push(`MD: 公式${formulaMd === 'keep' ? '保留' : formulaMd === 'render' ? '渲染图片' : '移除'} · 图片${imageMd === 'separate' ? '独立文件夹' : imageMd === 'inline' ? 'base64内嵌' : '不包含'}`)
    if (formats.includes('html')) lines.push(`HTML: ${htmlTheme === 'limo' ? 'LiMo 预览栏' : htmlTheme === 'paper' ? '学术论文' : htmlTheme === 'github' ? 'GitHub' : '极简'}样式`)
    if (formats.includes('plain')) lines.push(`纯文本: 公式${formulaPlain === 'remove' ? '移除' : 'Unicode'}`)
    return lines
  }, [typeGroups, groupTabs, formats, formulaMd, imageMd, formulaPlain, htmlTheme])

  const totalFiles = typeGroups.reduce((sum, g) => {
    const tabs = groupTabs[g.type] || []
    return sum + tabs.length * g.docs.length * formats.length
  }, 0)
  const canExport = totalFiles > 0

  return (
    <>
      <div className="fixed inset-0 z-[9998] bg-black/10" onClick={onClose} />
      <aside className="fixed right-0 bottom-0 top-0 z-[9999] flex w-[480px] max-w-[92vw] flex-col border-l bg-elevated shadow-2xl">
        <header className="flex items-center justify-between px-5 py-3.5 border-b shrink-0">
          <div className="flex items-center gap-2">
            <Download size={16} className="text-dimmed" />
            <h2 className="text-[15px] font-semibold">导出</h2>
            {selectedIds.length > 0 && <span className="text-[12px] text-disabled">已选 {selectedIds.length} 篇</span>}
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-surface-hover text-muted"><X size={15} /></button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* ── 文档选择 ── */}
          <section>
            <div className="flex items-center justify-between mb-2 cursor-pointer" onClick={() => setDocListExpanded(!docListExpanded)}>
              <h3 className="text-[13px] font-semibold text-foreground">选择文档</h3>
              {docListExpanded ? <ChevronUp size={14} className="text-disabled" /> : <ChevronDown size={14} className="text-disabled" />}
            </div>
            {docListExpanded && (
              <div className="bg-surface rounded-xl overflow-hidden">
                <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border-light">
                  <Search size={12} className="text-disabled" />
                  <input value={docSearch} onChange={e => setDocSearch(e.target.value)} placeholder="搜索文档..." className="flex-1 bg-transparent text-[12px] outline-none" />
                </div>
                <div className="max-h-[160px] overflow-y-auto">
                  {filteredDocs.map(d => (
                    <label key={d.id}
                      className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer hover:bg-elevated/50 transition-colors ${selectedIds.includes(d.id) ? 'bg-accent/5' : ''}`}>
                      <input type="checkbox" checked={selectedIds.includes(d.id)} onChange={() => toggleDoc(d.id)} className="w-4 h-4 accent-[#007AFF] shrink-0" />
                      <span className="text-[12px] truncate flex-1">{d.title}</span>
                      <span className="text-[10px] text-disabled shrink-0">{TYPE_LABELS[d.sourceType] || d.sourceType}</span>
                      <span className="text-[9px] text-muted shrink-0">{d.createdAt?.slice(0, 10)}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* ── 导出内容（按类型分组）── */}
          {typeGroups.length > 0 && (
            <section>
              <h3 className="text-[13px] font-semibold text-foreground mb-2">导出内容 · 按类型分组</h3>
              <p className="text-[10px] text-disabled -mt-1 mb-2">仅导出已生成的产物，未生成的内容会被自动跳过</p>
              <div className="space-y-2">
                {typeGroups.map(g => {
                  const IconComp = TYPE_ICON[g.type] || FileText
                  const tabs = groupTabs[g.type] || []
                  return (
                    <div key={g.type} className="bg-surface rounded-xl p-2.5">
                      <div className="text-[11px] font-medium text-dimmed mb-1.5 px-1 flex items-center gap-1">
                        <IconComp size={13} /> ({g.docs.length}篇)
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {g.tabs.map(t => {
                          const checked = tabs.includes(t.key)
                          return (
                            <label key={t.key}
                              onClick={() => toggleGroupTab(g.type, t.key)}
                              className={`px-2.5 py-1 rounded-lg text-[11px] cursor-pointer transition-colors ${checked ? 'bg-accent text-white' : 'bg-elevated text-dimmed hover:bg-surface-hover'}`}>
                              {t.label}
                            </label>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          {/* ── 导出格式 ── */}
          <section>
            <h3 className="text-[13px] font-semibold text-foreground mb-2">导出格式</h3>
            <div className="flex gap-2">
              {(['md', 'html', 'plain'] as Format[]).map(f => {
                const active = formats.includes(f)
                const meta = FORMAT_META[f]
                return (
                  <button key={f} onClick={() => toggleFormat(f)}
                    className={`flex flex-col items-center gap-1 px-4 py-2.5 rounded-xl border transition-all flex-1 ${active ? 'border-accent bg-accent/5' : 'border-border-light hover:bg-surface-hover'}`}>
                    <meta.icon size={18} className={active ? 'text-accent' : 'text-muted'} />
                    <span className={`text-[11px] font-medium ${active ? 'text-accent' : 'text-dimmed'}`}>{meta.label}</span>
                  </button>
                )
              })}
            </div>
            {/* 思维导图固定 HTML，与格式选择无关 */}
            {Object.values(groupTabs).some(tabs => tabs.includes('mindmap')) && (
              <p className="text-[10px] text-disabled mt-1.5">思维导图始终导出为交互式 HTML，不受上方格式选择影响</p>
            )}
          </section>

          {/* ── 导出设置 ── */}
          {formats.length > 0 && (
            <section>
              <h3 className="text-[13px] font-semibold text-foreground mb-2">导出设置</h3>
              <div className="bg-surface rounded-xl p-3 space-y-3">
                {formats.includes('md') && (
                  <div>
                    <div className="flex items-center gap-1.5 mb-2">
                      <FileText size={13} className="text-accent" />
                      <span className="text-[12px] font-medium">Markdown</span>
                    </div>
                    <div className="flex gap-2">
                      <select value={formulaMd} onChange={e => setFormulaMd(e.target.value as typeof formulaMd)}
                        className="flex-1 px-2.5 py-1.5 bg-elevated border border-border-default rounded-lg text-[11px] outline-none">
                        <option value="keep">公式：保留 LaTeX</option>
                        <option value="render">公式：渲染为图片</option>
                        <option value="remove">公式：移除</option>
                      </select>
                      <select value={imageMd} onChange={e => setImageMd(e.target.value as typeof imageMd)}
                        className="flex-1 px-2.5 py-1.5 bg-elevated border border-border-default rounded-lg text-[11px] outline-none">
                        <option value="separate">图片：独立文件夹</option>
                        <option value="inline">图片：base64 内嵌</option>
                        <option value="none">图片：不包含</option>
                      </select>
                    </div>
                  </div>
                )}
                {formats.includes('html') && (
                  <div>
                    <div className="flex items-center gap-1.5 mb-2">
                      <Globe size={13} className="text-accent" />
                      <span className="text-[12px] font-medium">HTML</span>
                    </div>
                    <select value={htmlTheme} onChange={e => setHtmlTheme(e.target.value as typeof htmlTheme)}
                      className="w-full px-2.5 py-1.5 bg-elevated border border-border-default rounded-lg text-[11px] outline-none">
                      <option value="limo">样式：LiMo 预览栏</option>
                      <option value="paper">样式：学术论文</option>
                      <option value="github">样式：GitHub 风格</option>
                      <option value="minimal">样式：极简白底</option>
                    </select>
                  </div>
                )}
                {formats.includes('plain') && (
                  <div>
                    <div className="flex items-center gap-1.5 mb-2">
                      <File size={13} className="text-accent" />
                      <span className="text-[12px] font-medium">纯文本</span>
                    </div>
                    <select value={formulaPlain} onChange={e => setFormulaPlain(e.target.value as typeof formulaPlain)}
                      className="w-full px-2.5 py-1.5 bg-elevated border border-border-default rounded-lg text-[11px] outline-none">
                      <option value="remove">公式：移除替换 [公式]</option>
                      <option value="unicode">公式：Unicode 近似</option>
                    </select>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* ── 文件名 ── */}
          <section>
            <h3 className="text-[13px] font-semibold text-foreground mb-2">文件名</h3>
            <input value={filename} onChange={e => { setFilename(e.target.value); setFilenameEdited(true) }}
              className="w-full px-3 py-2 bg-surface border border-border-default rounded-lg text-[13px] font-mono outline-none" />
          </section>

          {/* ── 导出摘要 ── */}
          <section>
            <h3 className="text-[13px] font-semibold text-foreground mb-2">导出摘要</h3>
            {previewLines.length === 0 || (previewLines.length === 1 && previewLines[0] === '未选择导出内容') ? (
              <p className="text-[12px] text-disabled bg-surface rounded-xl p-4">
                {selectedIds.length === 0 ? '请在「选择文档」中选中文档' : '请在「导出内容」中选择要导出的产物'}
              </p>
            ) : (
              <div className="bg-surface rounded-xl p-3 space-y-0.5">
                {previewLines.map((line, i) => (
                  <p key={i} className={`text-[11px] text-dimmed font-mono ${Object.values(TYPE_LABELS).some(label => line.startsWith(label)) ? 'mt-1.5' : ''}`}>
                    {line || ' '}
                  </p>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* ── 底部 ── */}
        <footer className="border-t px-5 py-4 shrink-0">
          <div className="flex gap-2">
            <button disabled={selectedIds.length === 0 || exporting} onClick={async () => {
              if (selectedIds.length === 0) return
              setExporting(true)
              try {
                if (selectedIds.length === 1) {
                  // 单文档：GET /export/{id}/all
                  const result = await downloadFromUrl(`/export/${selectedIds[0]}/all`, 'export.zip')
                  if (result.ok) addToast('已导出全部产物 (ZIP)，请查看下载文件夹')
                  else addToast(result.error || '导出失败', 'error')
                } else {
                  // 多文档：POST /export/batch
                  const r = await fetch(apiUrl('/export/batch'), {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ids: selectedIds }),
                  })
                  if (!r.ok) { const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` })); addToast(err.detail || '导出失败', 'error'); return }
                  const blob = await r.blob()
                  const result = downloadBlob(blob, `${filename || 'export'}.zip`)
                  if (result.ok) addToast(`已导出 ${selectedIds.length} 篇文档的全部产物 (ZIP)，请查看下载文件夹`)
                  else addToast(result.error || '导出失败', 'error')
                }
              } catch (e) { addToast('导出失败: ' + (e as Error).message, 'error') }
              finally { setExporting(false) }
            }}
              className={`flex-1 flex items-center justify-center gap-1.5 px-4 py-2.5 bg-surface text-dimmed text-[13px] font-medium rounded-xl hover:bg-surface-hover transition-colors ${selectedIds.length === 0 ? 'opacity-30' : ''}`}>
              {exporting ? '打包中...' : <><Archive size={14} /> 一键导出所有产物 (ZIP)</>}
            </button>
            <button disabled={!canExport || exporting} onClick={async () => {
              setExporting(true)
              try {
                const tabs = Array.from(new Set(typeGroups.flatMap(g => groupTabs[g.type] || [])))
                const r = await fetch(apiUrl('/export/custom'), {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ doc_ids: selectedIds, formats, content_tabs: tabs, formula_md: formulaMd, image_md: imageMd, formula_plain: formulaPlain, html_theme: htmlTheme, filename }),
                })
                if (!r.ok) { const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` })); addToast(err.detail || '导出失败', 'error'); return }
                const blob = await r.blob()
                const result = downloadBlob(blob, `${filename}.zip`)
                if (result.ok) {
                  addToast(`已导出 ${totalFiles} 个文件，请查看下载文件夹`)
                } else {
                  addToast(result.error || '导出失败', 'error')
                }
              } catch (e) { addToast('导出失败: ' + (e as Error).message, 'error') }
              finally { setExporting(false) }
            }}
              className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2.5 bg-accent text-white text-[13px] font-medium rounded-xl hover:bg-accent-hover disabled:opacity-30 transition-colors">
              <Download size={14} />
              {exporting ? '导出中...' : `按上方设置导出 ${totalFiles} 个文件 ${formats.length > 1 || totalFiles > 1 ? '(ZIP)' : ''}`}
            </button>
          </div>
        </footer>
      </aside>
    </>
  )
}
