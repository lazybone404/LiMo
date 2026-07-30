import { useState, useEffect, useMemo } from 'react'
import { useStore } from '../../store'
import { listEnabledModels, listProviders, getErrorMessage, apiUrl, type EnabledModel, type Provider } from '../../api/client'
import { createMarkdownComponents } from '../../lib/markdown-components'
import ReactMarkdown from 'react-markdown'
import { REMARK_PLUGINS, REHYPE_PLUGINS, normalizeLatexDelimiters } from '../../lib/markdown-plugins'

export type EpubChapter = {
  index: number; title: string; sections: EpubSection[]
}
export type EpubSection = {
  index: number; title: string; level: number
  text: string; skeleton: string
}

/** EPUB 骨架面板：章节树 + 快速/深度开关 + 生成按钮 */
export function EpubSkeletonPanel({ rawText, transcriptId }: { rawText: string; transcriptId: string }) {
  const [useSkeleton, setUseSkeleton] = useState(true)
  const [selectedSections, setSelectedSections] = useState<Set<string>>(new Set())
  const [generating, setGenerating] = useState(false)
  const [enabledModels, setEnabledModels] = useState<EnabledModel[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [fastModelId, setFastModelId] = useState<number | null>(null)
  const [deepModelId, setDeepModelId] = useState<number | null>(null)
  const addToast = useStore(s => s.addToast)
  const setActiveView = useStore(s => s.setActiveView)

  useEffect(() => {
    Promise.all([listEnabledModels(), listProviders()]).then(([models, provs]) => {
      setEnabledModels(models as EnabledModel[])
      setProviders(provs as Provider[])
      try {
        const saved = JSON.parse(localStorage.getItem('epubModelPrefs') || '{}')
        if (saved.fastModelId) setFastModelId(saved.fastModelId)
        if (saved.deepModelId) setDeepModelId(saved.deepModelId)
      } catch {}
    }).catch(err => console.error('加载处理结果失败:', err))
  }, [])

  useEffect(() => {
    try { localStorage.setItem('epubModelPrefs', JSON.stringify({ fastModelId, deepModelId })) } catch {}
  }, [fastModelId, deepModelId])

  const providerName = (pid: string) => providers.find(p => p.id === pid)?.name || pid
  const selectedModelId = useSkeleton ? fastModelId : deepModelId

  const parsedData = useMemo(() => {
    try { return JSON.parse(rawText) as { chapters?: EpubChapter[]; metadata?: Record<string, string> } } catch { return null }
  }, [rawText])
  const chapters = parsedData?.chapters || []
  const metadata = parsedData?.metadata || {}

  const allKeys: string[] = []
  chapters.forEach((ch, ci) => {
    (ch.sections || []).forEach((_sec, si) => {
      allKeys.push(`${ci}-${si}`)
    })
  })
  useEffect(() => {
    if (allKeys.length > 0 && selectedSections.size === 0) {
      setSelectedSections(new Set(allKeys))
    }
  }, [rawText])

  const toggleSelect = (key: string) => {
    setSelectedSections(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }
  const selectAll = () => setSelectedSections(new Set(allKeys))
  const deselectAll = () => setSelectedSections(new Set())

  const resolveDisplayRawText = () => {
    if (!useSkeleton) return rawText
    const chs = chapters.map(ch => ({
      ...ch,
      sections: (ch.sections || []).map(sec => ({
        ...sec,
        text: sec.skeleton || sec.text,
      })),
    }))
    return JSON.stringify({ chapters: chs, metadata })
  }

  const handleGenerate = async () => {
    if (generating) return
    setGenerating(true)
    try {
      const indices = allKeys
        .filter(k => selectedSections.has(k))
        .map(k => parseInt(k.split('-')[1]))
      const res = await fetch(apiUrl(`/generate/${transcriptId}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: 'notes,polished',
          use_skeleton: useSkeleton,
          chapter_indices: selectedSections.size === allKeys.length ? [] : indices,
          model_id: selectedModelId || undefined,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      if (data.content) {
        const store = useStore.getState()
        store.setCurrentContent(data.content)
        if (data.polished_content) store.setCurrentPolishedContent(data.polished_content)
        store.setMarkdownMeta({ id: data.id, style: data.style, mode: data.mode, version: data.version })
        store.setMarkdownVersions(data.versions || [])
        addToast('生成完成', 'success')
        setActiveView('summary')
      }
    } catch (e: unknown) {
      addToast(`生成失败: ${getErrorMessage(e)}`, 'error')
    } finally {
      setGenerating(false)
    }
  }

  const handleSkeletonGen = async () => {
    if (generating) return
    setGenerating(true)
    try {
      const res = await fetch(apiUrl(`/generate/${transcriptId}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: 'notes,polished',
          use_skeleton: true, chapter_indices: [],
          model_id: fastModelId || undefined,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      if (data.content) {
        const store = useStore.getState()
        store.setCurrentContent(data.content)
        if (data.polished_content) store.setCurrentPolishedContent(data.polished_content)
        store.setMarkdownMeta({ id: data.id, style: data.style, mode: data.mode, version: data.version })
        store.setMarkdownVersions(data.versions || [])
        addToast('骨架生成完成', 'success')
        setActiveView('summary')
      }
    } catch (e: unknown) {
      addToast(`生成失败: ${getErrorMessage(e)}`, 'error')
    } finally {
      setGenerating(false)
    }
  }

  const displayRawText = resolveDisplayRawText()

  if (!parsedData) return null

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border-light bg-surface shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 bg-surface-hover rounded-lg p-0.5">
            <button
              onClick={() => setUseSkeleton(true)}
              className={`px-3 py-1 text-[12px] rounded-md font-medium transition-colors ${
                useSkeleton ? 'bg-elevated text-accent shadow-sm' : 'text-muted hover:text-dimmed'
              }`}
            >快速</button>
            <button
              onClick={() => setUseSkeleton(false)}
              className={`px-3 py-1 text-[12px] rounded-md font-medium transition-colors ${
                !useSkeleton ? 'bg-elevated text-accent shadow-sm' : 'text-muted hover:text-dimmed'
              }`}
            >深度</button>
          </div>
          <span className="text-[11px] text-disabled">
            {useSkeleton ? '输入骨架，省 70% token' : '输入全文，深度理解'}
          </span>
          {enabledModels.length > 0 && (
            <select
              value={selectedModelId || ''}
              onChange={e => {
                const id = e.target.value ? Number(e.target.value) : null
                useSkeleton ? setFastModelId(id) : setDeepModelId(id)
              }}
              className="px-2 py-1 bg-surface border border-border-default rounded-lg text-[11px] outline-none max-w-[180px]"
            >
              <option value="">场景默认</option>
              {enabledModels.map(m => (
                <option key={m.id} value={m.id}>{providerName(m.provider_id)} · {m.model_name}</option>
              ))}
            </select>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => { selectAll() }} className="text-[11px] text-muted hover:text-accent transition-colors">全选</button>
          <span className="text-surface-hover">|</span>
          <button onClick={() => { deselectAll() }} className="text-[11px] text-muted hover:text-accent transition-colors">取消</button>
          <span className="text-[11px] text-disabled ml-1">已选 {selectedSections.size}/{allKeys.length} 节</span>
          <button onClick={handleSkeletonGen} disabled={generating} className="px-3 py-1.5 text-[12px] bg-accent text-white rounded-lg font-medium hover:bg-accent-hover disabled:opacity-50 transition-colors">
            {generating ? '生成中...' : '基于骨架生成全书'}
          </button>
          <button onClick={handleGenerate} disabled={generating || selectedSections.size === 0} className="px-3 py-1.5 text-[12px] bg-elevated text-accent border border-accent/30 rounded-lg font-medium hover:bg-accent/5 disabled:opacity-30 transition-colors">
            {generating ? '生成中...' : '生成选中章节'}
          </button>
        </div>
      </div>
      <div className="flex-1 flex overflow-hidden">
        <EpubChapterView
          rawText={displayRawText}
          showSkeleton={useSkeleton}
          selectedSections={selectedSections}
          onToggleSelect={toggleSelect}
        />
      </div>
    </div>
  )
}

/** EPUB 章节导航视图（骨架 / 原文共用） */
export function EpubChapterView({ rawText, showSkeleton, selectedSections, onToggleSelect }: {
  rawText: string; showSkeleton: boolean
  selectedSections?: Set<string>; onToggleSelect?: (key: string) => void
}) {
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [selectedSecIdx, setSelectedSecIdx] = useState(0)
  const components = useMemo(() => createMarkdownComponents(), [])
  let data: { chapters?: EpubChapter[]; metadata?: Record<string, string> } = {}
  try { data = JSON.parse(rawText) } catch { /* handled below */ }
  const chapters = data.chapters || []
  const metadata = data.metadata || {}
  const showCheckboxes = !!onToggleSelect

  const currentChapter = chapters[selectedIdx]
  const currentSection = currentChapter?.sections?.[selectedSecIdx]

  const navItems: { chIdx: number; secIdx: number; title: string; level: number }[] = []
  chapters.forEach((ch, ci) => {
    ch.sections?.forEach((sec, si) => {
      navItems.push({ chIdx: ci, secIdx: si, title: sec.title || ch.title, level: sec.level || 1 })
    })
  })

  const content = currentSection
    ? (showSkeleton ? currentSection.skeleton : currentSection.text) || currentSection.text
    : ''

  if (!data.chapters?.length) return <p className="text-[13px] text-disabled p-8">无章节数据</p>

  return (
    <div className="flex h-full">
      <div className="w-[220px] shrink-0 border-r border-border-light overflow-y-auto bg-surface">
        <div className="sticky top-0 bg-surface px-3 py-2 border-b border-border-light">
          <p className="text-[11px] text-disabled">
            {metadata.title || '章节目录'}
            {metadata.author && ` · ${metadata.author}`}
          </p>
          <p className="text-[10px] text-disabled mt-0.5">{chapters.length} 章 · {navItems.length} 节</p>
        </div>
        {chapters.map((ch, ci) => (
          <div key={ci}>
            <div
              onClick={() => { setSelectedIdx(ci); setSelectedSecIdx(0) }}
              className={`px-3 py-1.5 text-[12px] font-medium cursor-pointer hover:bg-surface-hover/50 transition-colors flex items-center gap-1.5 ${
                ci === selectedIdx ? 'bg-accent/10 text-accent' : 'text-foreground'
              }`}
            >
              {showCheckboxes && onToggleSelect && (
                <input type="checkbox" checked={
                  (ch.sections || []).every((_, si) => selectedSections?.has(`${ci}-${si}`))
                } onChange={() => {
                  (ch.sections || []).forEach((_, si) => onToggleSelect(`${ci}-${si}`))
                }} className="w-3 h-3 rounded border-border-light shrink-0" />
              )}
              <span className="truncate">{ch.title || `第${ci + 1}章`}</span>
            </div>
            {(ch.sections || []).map((sec, si) => (
              <div
                key={si}
                onClick={() => { setSelectedIdx(ci); setSelectedSecIdx(si) }}
                className={`pl-6 pr-3 py-1 text-[11px] cursor-pointer hover:bg-surface-hover/30 transition-colors flex items-center gap-1.5 ${
                  ci === selectedIdx && si === selectedSecIdx
                    ? 'bg-accent/5 text-accent font-medium'
                    : 'text-dimmed'
                }`}
              >
                {showCheckboxes && onToggleSelect && (
                  <input type="checkbox" checked={selectedSections?.has(`${ci}-${si}`) || false}
                    onChange={(e) => { e.stopPropagation(); onToggleSelect(`${ci}-${si}`) }}
                    className="w-3 h-3 rounded border-border-light shrink-0" />
                )}
                <span className="truncate">{sec.title || `§${si + 1}`}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="max-w-[720px] mx-auto markdown-body">
          <ReactMarkdown
            remarkPlugins={REMARK_PLUGINS}
            rehypePlugins={REHYPE_PLUGINS}
            components={components}
          >
            {normalizeLatexDelimiters(content || '*（无内容）*')}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}
