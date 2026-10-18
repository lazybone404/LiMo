/**
 * 文件夹选择器 —— 树形浮层，**三处共用**：
 * 右键「移动到文件夹…」/ 批量条「移动」/ 右键文件夹「移动到…」。
 *
 * ⚠️ **刻意不做成 `ContextMenu.submenu`**：子菜单是 hover 展开、鼠标一移就收起，而这里
 * 有折叠箭头、内联输入框、长列表滚动 —— 三种交互都会把鼠标带出子菜单，它必然半路消失。
 *
 * ⚠️ **必须 Portal 到 body**：文档库的 `<aside>` 带 `backdrop-blur-[20px]`，它会创建
 * 包含块，把 `fixed` 后代锁进面板大小的一小块（见 DocLibrary 里那段注释 / 模式 #67）。
 *
 * ⚠️ 禁用的判据（`excludeIds`）由**调用方**给：文件夹移动时传「自身 + 全部后代」。
 * 本组件不自己算 —— 它不知道调用方在移动什么，而「移动到自己肚子里」这条得由后端
 * 兜底（`FOLDER_CYCLE`），前端这里只是把不可选的项画灰。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, ChevronRight, Folder, FolderPlus } from 'lucide-react'
import type { FolderEntry } from '../../store/types'
import { buildFolderTree } from './folderTree'

/** 面板宽度（定位夹取用）。高度取 `max-h` 的近似值，只用于「别顶出视口下沿」 */
const PANEL_W = 240
const PANEL_H = 340

export interface FolderPickerProps {
  folders: FolderEntry[]
  /** 根级那一行的文案：文档移动 =「未分类（移出文件夹）」；文件夹移动 =「根级」 */
  rootLabel: string
  /** 禁用项（文件夹移动时 = 自身 + 全部后代）；鼠标悬停给出原因 */
  excludeIds?: Set<string>
  /** 浮层左上角（**视口坐标**：`e.clientX/Y` 或按钮 rect） */
  x: number
  y: number
  /** 批量条场景：根节点额外带 `data-batch-move-panel`（既有测试按它定位浮层） */
  batchPanel?: boolean
  onPick: (folderId: string) => void
  onClose: () => void
  /**
   * 底部「新建文件夹…」。**不传就没有这一行** —— 文件夹移动刻意不与「新建」耦合
   * （移动是把已有东西挪过去，新建是另一件事）。
   *
   * 返回新夹 id（失败 / 取消返回 `null`；toast 由调用方负责）—— 拿到 id 后本组件
   * 直接把它当作选中项交给 `onPick`，「新建并移过去」因此是一步。
   */
  onCreate?: (name: string) => Promise<string | null>
}

export function FolderPicker({
  folders, rootLabel, excludeIds, x, y, batchPanel, onPick, onClose, onCreate,
}: FolderPickerProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  /** 收起的夹（默认全展开：移动时最怕「目标藏在收起的父级里」） */
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  const tree = useMemo(() => buildFolderTree(folders), [folders])

  /**
   * 点击外部 / Esc 关闭。
   *
   * ⚠️ **`setTimeout(0)` 不是防御性代码，是必需的**：浮层由触发按钮的**同一个 click**
   * 打开，同步挂上的 document 监听会在该事件冒泡到 document 时立刻把浮层关掉
   * （跨组件的事件顺序由 React 的委托层决定，不该依赖）。照抄 `ContextMenu` 的做法。
   */
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    const t = setTimeout(() => {
      document.addEventListener('click', handler)
      document.addEventListener('contextmenu', handler)
    }, 0)
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('click', handler)
      document.removeEventListener('contextmenu', handler)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  // 夹到视口内：右键菜单的坐标靠边时，浮层会有一半在屏幕外
  const left = Math.max(8, Math.min(x, (window.innerWidth || 800) - PANEL_W - 8))
  const top = Math.max(8, Math.min(y, (window.innerHeight || 800) - PANEL_H - 8))

  const commitCreate = async () => {
    if (busy || !onCreate) return
    const name = draft.trim()
    if (!name) { setCreating(false); setDraft(''); return }
    setBusy(true)
    try {
      const id = await onCreate(name)
      // 失败时**保留草稿**（与工具栏 / 分组头同一条契约）：用户重试一次即可
      if (id) { setDraft(''); setCreating(false); onPick(id) }
    } finally { setBusy(false) }
  }

  /**
   * 前序遍历 + 折叠传播。被收起的父级、以及被父级的收起**传染**到的后代都不进 DOM
   * ——与 `DocList` 同一条判据（那里连渲染配额也不给它们）。
   */
  const rows: React.ReactNode[] = []
  const hidden = new Set<string>()
  for (const id of tree.preorder) {
    const parent = tree.parentOf.get(id) || ''
    if (parent && (collapsed.has(parent) || hidden.has(parent))) { hidden.add(id); continue }

    const node = tree.byId.get(id)!
    const depth = tree.depthOf.get(id) ?? 0
    const kids = tree.childrenOf.get(id) || []
    const open = !collapsed.has(id)
    const disabled = !!excludeIds?.has(id)
    // 后端现算的子树总数；老后端 / 测试 mock 没有时退回直接数
    const total = node.totalCount ?? node.count ?? 0

    rows.push(
      <div
        key={id}
        data-picker-row={id}
        className="flex items-center pr-1"
        style={{ paddingLeft: 6 + depth * 12 }}
      >
        {kids.length > 0 ? (
          <button
            data-picker-toggle={id}
            onClick={() => setCollapsed(prev => {
              const next = new Set(prev)
              if (next.has(id)) next.delete(id); else next.add(id)
              return next
            })}
            className="w-4 h-5 flex items-center justify-center shrink-0 rounded hover:bg-surface-hover"
            title={open ? '收起' : '展开'}
          >
            {open ? <ChevronDown size={11} className="text-disabled" /> : <ChevronRight size={11} className="text-disabled" />}
          </button>
        ) : <span className="w-4 shrink-0" aria-hidden />}
        <button
          data-move-target={id}
          disabled={disabled}
          onClick={() => onPick(id)}
          title={disabled ? '不能移动到它自己或它的子文件夹' : (tree.pathOf.get(id) || node.name)}
          className="flex-1 min-w-0 flex items-center gap-1.5 px-1 py-1 rounded-md text-[12px] text-left text-dimmed hover:bg-surface-hover disabled:opacity-40 disabled:hover:bg-transparent"
        >
          <Folder size={12} className="shrink-0 text-disabled" />
          <span className="truncate">{node.name}</span>
          <span className="ml-auto text-[10px] text-disabled shrink-0">{total}</span>
        </button>
      </div>,
    )
  }

  return createPortal(
    <div
      ref={rootRef}
      data-folder-picker
      {...(batchPanel ? { 'data-batch-move-panel': '' } : {})}
      style={{ left, top, width: PANEL_W }}
      className="fixed z-[220] py-1 rounded-xl bg-elevated/95 backdrop-blur-[20px] shadow-lg border border-border-light animate-[fadeIn_0.12s_ease-out]"
    >
      <div className="max-h-72 overflow-y-auto">
        <div className="flex items-center pr-1" style={{ paddingLeft: 6 }}>
          <span className="w-4 shrink-0" aria-hidden />
          <button
            data-move-target=""
            onClick={() => onPick('')}
            className="flex-1 min-w-0 px-1 py-1 rounded-md text-[12px] text-left text-dimmed hover:bg-surface-hover truncate"
          >
            {rootLabel}
          </button>
        </div>
        {rows}
        {!folders.length && (
          <p className="px-2 py-1 text-[11px] text-disabled">尚无文件夹</p>
        )}
      </div>

      {onCreate && (creating ? (
        <div className="mt-1 px-1.5 pb-1">
          <input
            data-picker-new-input
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') void commitCreate()
              if (e.key === 'Escape') { setCreating(false); setDraft('') }
            }}
            disabled={busy}
            autoFocus
            placeholder="新文件夹名"
            className="w-full px-2 py-1 text-[12px] bg-elevated border border-accent rounded-md outline-none disabled:opacity-50"
          />
        </div>
      ) : (
        <div className="mt-1 border-t border-border-light pt-1">
          <button
            data-picker-new
            onClick={() => setCreating(true)}
            className="w-full flex items-center gap-1.5 px-2 py-1 text-[12px] text-muted hover:bg-surface-hover"
          >
            <FolderPlus size={12} className="text-disabled" />新建文件夹…
          </button>
        </div>
      ))}
    </div>,
    document.body,
  )
}
