/**
 * 文档库工具栏 —— 标题 + 新建文件夹 + 密度切换 + 收起面板。
 *
 * ⚠️ 这一整条会被 `BatchBar` **替换**（勾选任意一行时）—— 「新建文件夹」在批量态下
 * 不可见是刻意的：那时用户的目标是「对这批文档做点什么」，而新建的文件夹是空的、
 * 拖不进这批（拖拽目标在批量态下也被 BatchBar 顶掉了）。要建文件夹先取消选择即可。
 * （收起面板照旧只在非批量态可见。）
 *
 * ⚠️ 「新建文件夹」的输入行放在工具栏**下方**而不是弹窗：这是个高频轻动作，
 * 弹窗会打断视线流；行内输入 + Enter 提交 + Esc 取消与列表内的重命名行为一致。
 */
import { useState } from 'react'
import { FolderPlus, PanelLeftClose } from 'lucide-react'
import { useStore } from '../../store'
import type { Density } from './docLibraryPrefs'

export interface DocLibraryToolbarProps {
  density: Density
  onDensity: (d: Density) => void
  /** 返回是否成功 —— **成功才**清空草稿并收起输入行（失败保留，用户重试一次即可） */
  onCreateFolder: (name: string) => Promise<boolean>
  /** 新建文件夹请求进行中 —— 禁用入口，防重复发起 */
  busy: boolean
}

const DENSITY_OPTIONS: { key: Density; label: string; hint: string }[] = [
  { key: 'detailed', label: '详细', hint: '卡片视图，显示封面与元数据' },
  { key: 'compact', label: '简略', hint: '单行视图，一屏看更多' },
]

export function DocLibraryToolbar({ density, onDensity, onCreateFolder, busy }: DocLibraryToolbarProps) {
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState('')
  /** 提交中 —— 防 Enter 提交与紧随的 blur 各发一次请求（与 GroupHeader 同理） */
  const [committing, setCommitting] = useState(false)

  const commitCreate = async () => {
    if (committing) return
    const name = draft.trim()
    if (!name) { setCreating(false); setDraft(''); return }
    setCommitting(true)
    try {
      if (await onCreateFolder(name)) { setCreating(false); setDraft('') }
    } finally { setCommitting(false) }
  }

  const chip = (on: boolean) =>
    `px-1.5 py-0.5 rounded text-[11px] transition-colors ${
      on ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover'
    }`

  return (
    <div className="shrink-0 border-b border-border-light">
      <div className="flex items-center justify-between px-4 py-2.5">
        <span className="text-[12px] font-medium text-dimmed">文档库</span>
        <div className="flex items-center gap-1">
          <button
            data-new-folder
            onClick={() => setCreating(true)}
            disabled={busy}
            className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center disabled:opacity-40 disabled:hover:bg-transparent"
            title="新建文件夹"
          >
            <FolderPlus size={14} className="text-muted" />
          </button>
          {/* 密度切换 —— 两个 chip 而不是图标：文字比图标更能说明「详细/简略」的差别 */}
          <span className="flex items-center gap-0.5">
            {DENSITY_OPTIONS.map(o => (
              <button
                key={o.key}
                data-density={o.key}
                onClick={() => onDensity(o.key)}
                title={o.hint}
                className={chip(density === o.key)}
              >
                {o.label}
              </button>
            ))}
          </span>
          {/* 走统一入口：互斥下「收起」= 谁都不展开，并排下才收起为窄条（见 `closePanel`） */}
          <button
            data-collapse-panel
            onClick={() => useStore.getState().closePanel('docLib')}
            className="w-6 h-6 rounded-md hover:bg-surface-hover flex items-center justify-center"
            title="收起面板"
          >
            <PanelLeftClose size={14} className="text-muted" />
          </button>
        </div>
      </div>

      {creating && (
        <div className="px-3 pb-2">
          <input
            data-new-folder-input
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') void commitCreate()
              if (e.key === 'Escape') { setCreating(false); setDraft('') }
            }}
            onBlur={commitCreate}
            autoFocus
            placeholder="文件夹名"
            className="w-full px-2 py-1 text-[12px] bg-elevated border border-accent rounded-md outline-none"
          />
        </div>
      )}
    </div>
  )
}
