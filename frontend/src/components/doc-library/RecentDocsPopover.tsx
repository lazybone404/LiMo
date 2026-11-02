/**
 * 「最近打开」浮层 —— 图标按钮 + 下拉列表。
 *
 * 浮层写法**照抄 `FilterPopover`**（模式 #67 的例外形态）：`absolute` 锚在按钮上，
 * 点外部靠 `document` 的 `mousedown` + `contains` 划界。**不 Portal 到 body** ——
 * 它是一个下拉，脱离 `relative` 父就失去定位基准；也**不抽公共 hook** ——
 * 动一个已经在工作的组件，风险大于照抄二十行的成本。
 *
 * ⚠️ 列表**每次打开重读** localStorage，不在组件里缓存：这份数据会在别处
 * （`selectDoc`）被写，常驻组件持有快照就是「刚打开的文档不在最近里」。
 *
 * ⚠️ 「已删除」判据是 `docs.length > 0 && 查不到`：`docs` 还没拉回来时（冷启动
 * 头几百毫秒）每条都查不到，直接判已删除 = 满屏假话。
 */
import { useEffect, useRef, useState } from 'react'
import { History, FileX2 } from 'lucide-react'
import { useStore } from '../../store'
import { loadRecentDocs } from '../../lib/recent-docs'

export function RecentDocsPopover() {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const docs = useStore(s => s.docs)
  const selectDoc = useStore(s => s.selectDoc)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // 打开时才读：见文件头第二条
  const items = open ? loadRecentDocs() : []

  return (
    <div ref={rootRef} className="relative">
      <button
        data-open-recent
        onClick={() => setOpen(v => !v)}
        className={`w-6 h-6 rounded-md flex items-center justify-center transition-colors ${
          open ? 'bg-accent/10' : 'hover:bg-surface-hover'
        }`}
        title="最近打开"
      >
        <History size={14} className={open ? 'text-accent' : 'text-muted'} />
      </button>

      {open && (
        <div
          data-recent-popover
          className="absolute right-0 top-full mt-1 z-20 w-[260px] max-h-[320px] overflow-y-auto bg-elevated border border-border-light rounded-lg shadow-lg py-1"
        >
          {items.length === 0 ? (
            <p className="px-3 py-6 text-center text-[12px] text-disabled">还没有打开过文档</p>
          ) : items.map(r => {
            const live = docs.find(d => d.id === r.id)
            const gone = docs.length > 0 && !live
            const title = live?.title || r.title || '（无标题）'
            return (
              <button
                key={r.id}
                data-recent-item
                disabled={gone}
                onClick={() => { selectDoc(r.id); setOpen(false) }}
                title={gone ? '这篇文档已不在库中（已删除或未恢复）' : title}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors
                           enabled:hover:bg-surface-hover disabled:opacity-45 disabled:cursor-default"
              >
                {gone && <FileX2 size={12} className="shrink-0 text-disabled" />}
                <span className="flex-1 min-w-0 truncate text-[12px] text-foreground">{title}</span>
                {gone && <span className="shrink-0 text-[11px] text-disabled">已删除</span>}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
