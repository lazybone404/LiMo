/**
 * 智能分类确认对话框 —— 模型给建议，用户改完再写库。
 *
 * 与 `BatchBar` 同款：**纯展示组件**（props 注入 + 自持编辑草稿），不碰 store、
 * 不发请求。所有 I/O 都在 `DocLibrary` 的 `handleOpenClassify` / `runClassify` 里。
 *
 * 三块内容，各自对应一个必须被用户看见的事实：
 * 1. **取材深度**（顶部单选）——「模型看了多少」由用户决定。它同时是**触发重跑**的
 *    控件：切档必须重调 preview（判据与执行同源，前端不本地重算）。
 * 2. **逐篇行** —— 取材徽标（仅标题 / 已缩减 = 显式降级）+ 文件夹徽标（复用 / 新建 /
 *    未分类三色）+ 可编辑标签。
 * 3. **警告** —— 模型的幻觉 id、超上限、没覆盖到的篇，全部落在这里或行内。
 *
 * ⚠️ **切换取材深度会重置所有手改**：`preview` 是唯一真相源，而重跑后每一行的
 * 取材与建议都可能不同 —— 保留旧编辑等于把新旧两批建议混在一起。这与合成对话框
 * 「切原料模式重拉预览」是同一条规则。
 */
import { useEffect, useState } from 'react'
import { FolderPlus, FolderTree, FolderX, Loader2, Sparkles } from 'lucide-react'
import type { ClassifyPreview, ClassifySource, ClassifySuggestion } from '../../api/classify'

/** 取材深度档位（值 = 后端 `notesChars`；`0` = 仅标题）。 */
export const DEPTH_OPTIONS = [
  { value: 600, label: '600 字' },
  { value: 200, label: '200 字' },
  { value: 0, label: '仅标题' },
] as const

/** 一行审定后的方案。`newFolderName` 非空 = 需要调用方先建夹再写入。 */
export interface ClassifyPlanRow {
  id: string
  /** `''` = 未分类；`newFolderName` 非空时这里恒为 `''` */
  folderId: string
  newFolderName: string
  tags: string[]
}

interface Draft {
  id: string
  title: string
  source: ClassifySource
  chars: number
  warnings: string[]
  mode: 'reuse' | 'new' | 'none'
  folderId: string
  newName: string
  tagsText: string
}

export interface ClassifyDialogProps {
  /** 定格的选择集（打开那一刻）——用于显示篇数，也用于「空清单」判断 */
  ids: string[]
  notesChars: number
  running: boolean
  committing: boolean
  error: string | null
  preview: ClassifyPreview | null
  onDepthChange: (chars: number) => void
  onClose: () => void
  onConfirm: (plan: ClassifyPlanRow[]) => void
}

function toDraft(s: ClassifySuggestion): Draft {
  const a = s.folder.action
  const mode: Draft['mode'] =
    a === 'reuse' && s.folder.id ? 'reuse' : (a === 'new' && s.folder.name ? 'new' : 'none')
  return {
    id: s.id,
    title: s.title,
    source: s.source,
    chars: s.chars,
    warnings: s.warnings,
    mode,
    folderId: s.folder.id ?? '',
    newName: s.folder.name ?? '',
    tagsText: s.tags.join(', '),
  }
}

/** 取材徽标文案。`title_only` 与 `shortened` 都是**降级**，用 warning 色。 */
function sourceBadge(d: Draft): { text: string; degraded: boolean } {
  if (d.source === 'title_only') return { text: '仅标题', degraded: true }
  if (d.source === 'shortened') return { text: `已缩减至 ${d.chars} 字`, degraded: true }
  return { text: `${d.chars} 字`, degraded: false }
}

const FOLDER_BADGE: Record<Draft['mode'], { text: string; cls: string }> = {
  reuse: { text: '归入现有', cls: 'text-accent bg-accent/10' },
  new: { text: '新建', cls: 'text-warning bg-warning/10' },
  none: { text: '未分类', cls: 'text-muted bg-surface-hover' },
}

export function ClassifyDialog({
  ids, notesChars, running, committing, error, preview,
  onDepthChange, onClose, onConfirm,
}: ClassifyDialogProps) {
  const [drafts, setDrafts] = useState<Draft[]>([])

  // `preview` 是唯一真相源：每次重跑都重建草稿（含切档、含重试）
  useEffect(() => {
    setDrafts(preview ? preview.suggestions.map(toDraft) : [])
  }, [preview])

  const folders = preview?.folders ?? []
  const patch = (id: string, next: Partial<Draft>) =>
    setDrafts(prev => prev.map(d => (d.id === id ? { ...d, ...next } : d)))

  const canConfirm = !running && !committing && !error && !!preview && drafts.length > 0

  const confirm = () => {
    if (!canConfirm) return
    onConfirm(drafts.map(d => ({
      id: d.id,
      folderId: d.mode === 'reuse' ? d.folderId : '',
      // 名字留空的新建 = 实际不归入（后端把 folderId='' 当未分类，不会误建空名夹）
      newFolderName: d.mode === 'new' ? d.newName.trim() : '',
      tags: d.tagsText.split(/[,，]/).map(t => t.trim()).filter(Boolean),
    })))
  }

  return (
    <div data-classify-dialog
      className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={running || committing ? undefined : onClose}>
      <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light w-[640px] max-w-[92vw] max-h-[86vh] flex flex-col animate-[fadeIn_0.15s_ease-out]"
        onClick={e => e.stopPropagation()}>

        {/* 头部 */}
        <div className="px-5 pt-4 pb-3 border-b border-border-light shrink-0">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-accent" />
            <h3 className="text-[15px] font-semibold">智能分类 {ids.length} 篇</h3>
            {running && <Loader2 size={13} className="animate-spin text-muted" />}
          </div>

          {/* 取材深度 —— 用户决定「模型看多少」。切档**重调 preview**（见文件头） */}
          <div className="mt-2.5 flex items-center gap-2">
            <span className="text-[12px] text-dimmed">取材深度</span>
            <div className="flex rounded-md border border-border-light overflow-hidden">
              {DEPTH_OPTIONS.map(o => (
                <button key={o.value} data-classify-depth={o.value}
                  disabled={running || committing}
                  onClick={() => { if (o.value !== notesChars) onDepthChange(o.value) }}
                  className={`px-2.5 py-1 text-[11px] disabled:opacity-50 ${
                    notesChars === o.value
                      ? 'bg-accent/15 text-accent font-medium'
                      : 'text-muted hover:bg-surface-hover'}`}>
                  {o.label}
                </button>
              ))}
            </div>
            <span className="text-[11px] text-disabled">依据标题 + 笔记开头</span>
          </div>

          {preview && (
            <p className="mt-1.5 text-[11px] text-disabled">
              最多新建 {preview.limits.maxNewFolders} 个文件夹 · 每篇最多 {preview.limits.maxTagsPerDoc} 个标签
              {preview.tagVocab.length > 0 && ` · 已提供 ${preview.tagVocab.length} 个现有标签供复用`}
            </p>
          )}
        </div>

        {/* 主体 */}
        <div className="flex-1 overflow-y-auto px-5 py-3">
          {running && (
            <p data-classify-running className="text-[13px] text-dimmed py-6 text-center">
              正在分析这 {ids.length} 篇文档…
            </p>
          )}

          {!running && error && (
            <div data-classify-error className="text-[13px] text-error bg-error/10 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          {!running && !error && preview && (
            <>
              {preview.warnings.length > 0 && (
                <div data-classify-warnings
                  className="mb-2.5 text-[12px] text-warning bg-warning/10 rounded-lg px-3 py-2 space-y-0.5">
                  {preview.warnings.map((w, i) => <p key={i}>{w}</p>)}
                </div>
              )}

              <div className="space-y-1.5">
                {drafts.map(d => {
                  const badge = sourceBadge(d)
                  const fb = FOLDER_BADGE[d.mode]
                  return (
                    <div key={d.id} data-classify-row={d.id} data-classify-folder-mode={d.mode}
                      className="rounded-lg border border-border-light px-2.5 py-2">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[13px] truncate flex-1">{d.title}</span>
                        <span data-classify-source={d.source}
                          className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] ${
                            badge.degraded ? 'text-warning bg-warning/10' : 'text-disabled bg-surface-hover'}`}>
                          {badge.text}
                        </span>
                        <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] ${fb.cls}`}>
                          {fb.text}
                        </span>
                      </div>

                      {/* 文件夹选择：未分类 / 现有文件夹 / 保留新建 */}
                      <div className="mt-1.5 flex items-center gap-1.5">
                        {d.mode === 'reuse'
                          ? <FolderTree size={12} className="text-accent shrink-0" />
                          : d.mode === 'new'
                            ? <FolderPlus size={12} className="text-warning shrink-0" />
                            : <FolderX size={12} className="text-disabled shrink-0" />}
                        <select data-classify-folder-select={d.id}
                          value={d.mode === 'reuse' ? `reuse:${d.folderId}` : d.mode}
                          onChange={e => {
                            const v = e.target.value
                            if (v === 'none') patch(d.id, { mode: 'none', folderId: '' })
                            else if (v === 'new') patch(d.id, { mode: 'new' })
                            else patch(d.id, { mode: 'reuse', folderId: v.slice('reuse:'.length) })
                          }}
                          className="flex-1 text-[12px] bg-elevated border border-border-light rounded-md px-1.5 py-1 outline-none">
                          <option value="none">未分类</option>
                          {folders.map(f => (
                            <option key={f.id} value={`reuse:${f.id}`}>{f.name}</option>
                          ))}
                          {/* 「保留新建」只在模型确实建议了新建时出现 */}
                          {d.mode === 'new' && <option value="new">新建：{d.newName || '（未命名）'}</option>}
                        </select>
                        {d.mode === 'new' && (
                          <input data-classify-new-name={d.id} value={d.newName}
                            onChange={e => patch(d.id, { newName: e.target.value })}
                            placeholder="新文件夹名"
                            className="w-28 text-[12px] bg-elevated border border-warning/40 rounded-md px-1.5 py-1 outline-none" />
                        )}
                      </div>

                      <input data-classify-tags={d.id} value={d.tagsText}
                        onChange={e => patch(d.id, { tagsText: e.target.value })}
                        placeholder="标签，逗号分隔"
                        className="mt-1.5 w-full text-[12px] bg-elevated border border-border-light rounded-md px-1.5 py-1 outline-none" />

                      {d.warnings.length > 0 && (
                        <div data-classify-warning={d.id} className="mt-1 text-[11px] text-warning space-y-0.5">
                          {d.warnings.map((w, i) => <p key={i}>{w}</p>)}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>

        {/* 底部 */}
        <div className="px-5 py-3 border-t border-border-light flex justify-end gap-2 shrink-0">
          <button data-cancel-classify onClick={onClose} disabled={committing}
            className="px-4 py-1.5 text-[13px] text-dimmed hover:bg-surface rounded-lg disabled:opacity-50">
            取消
          </button>
          <button data-confirm-classify onClick={confirm} disabled={!canConfirm}
            className="px-4 py-1.5 bg-accent text-on-accent text-[13px] font-medium rounded-lg hover:bg-accent-hover disabled:opacity-50">
            {committing ? '应用中…' : '应用'}
          </button>
        </div>
      </div>
    </div>
  )
}
