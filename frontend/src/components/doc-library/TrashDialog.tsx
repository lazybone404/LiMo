/**
 * 回收站对话框 —— 删除的第二道门。
 *
 * 两条入口共用**同一组 API**（`api/trash.ts`）：删除后的「撤销」toast 走
 * `restoreTrash([id])`，这里的「恢复」也走它。前端不实现任何恢复逻辑 ——
 * 搬目录、写回索引条目、重建全文索引、给向量打 pending 全在后端。
 *
 * ⚠️ **必须 Portal 到 body**：`DocLibrary` 的根 `<aside>` 带 `backdrop-blur-[20px]`，
 * 它会创建包含块，把 `fixed` 后代锁进面板大小的一小块（模式 #67，本轮共 6 处同款）。
 *
 * ⚠️ 顶部那句「只保留文档本体」不是免责声明，是**必须说的事实**：上传的原始文件
 * 住在 `{root}/files/{hash}/`，删除文档今天就不删它。用户看到「清空回收站」时的
 * 合理预期是「磁盘腾出来了」，而它不是 —— 不说清楚，quietly 说谎。
 */
import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Trash2, RotateCcw, AlertTriangle, Loader2 } from 'lucide-react'
import { emptyTrash, getErrorMessage, listTrash, purgeTrash, restoreTrash, type TrashItem } from '../../api/client'
import { SRC_LABEL } from './docMeta'
import { fmtTime } from '../../lib/artifactMeta'

export interface TrashDialogProps {
  onClose: () => void
  /** 恢复成功后回调 —— 父组件重拉文档列表。不回调 = 「恢复成功但列表里没有」。 */
  onRestored: () => void
}

export function TrashDialog({ onClose, onRestored }: TrashDialogProps) {
  const [items, setItems] = useState<TrashItem[]>([])
  const [loading, setLoading] = useState(true)
  /** 正在处理的单条 id（逐条禁用，防连点重复请求） */
  const [busyId, setBusyId] = useState('')
  const [busyAll, setBusyAll] = useState(false)
  const [error, setError] = useState('')
  /** 二次确认：彻底删除单条 / 清空整个回收站。两者互斥，同时只可能有一个 */
  const [confirmPurge, setConfirmPurge] = useState<TrashItem | null>(null)
  const [confirmEmpty, setConfirmEmpty] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await listTrash()
      setItems(r.items)
      setError('')
    } catch (e) {
      // ⚠️ 读失败**不清空已有列表**（与文档库 `refreshDocs` 同款取舍）：
      // 一次瞬时失败不该让用户看到「回收站空了」这种比事实更糟的假象。
      setError(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  // Esc：有二次确认时先关确认（否则用户想「算了」却把整个回收站关了）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (confirmPurge) { setConfirmPurge(null); return }
      if (confirmEmpty) { setConfirmEmpty(false); return }
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [confirmPurge, confirmEmpty, onClose])

  const handleRestore = async (item: TrashItem) => {
    setBusyId(item.id)
    try {
      const r = await restoreTrash([item.id])
      if (r.failed?.length) setError(r.failed[0].error || '恢复失败')
      else { onRestored(); await load() }
    } catch (e) {
      setError(getErrorMessage(e))
    } finally { setBusyId('') }
  }

  const handlePurge = async (item: TrashItem) => {
    setConfirmPurge(null)
    setBusyId(item.id)
    try {
      await purgeTrash(item.id)
      await load()
    } catch (e) {
      setError(getErrorMessage(e))
    } finally { setBusyId('') }
  }

  const handleEmpty = async () => {
    setConfirmEmpty(false)
    setBusyAll(true)
    try {
      await emptyTrash()
      await load()
    } catch (e) {
      setError(getErrorMessage(e))
    } finally { setBusyAll(false) }
  }

  return createPortal(
    <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={onClose}>
      <div
        data-trash-dialog
        className="relative bg-elevated rounded-2xl shadow-2xl border border-border-light w-[460px] max-w-[92vw] max-h-[80vh] flex flex-col mx-4 animate-[fadeIn_0.15s_ease-out]"
        onClick={e => e.stopPropagation()}>

        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-light shrink-0">
          <h3 className="text-[15px] font-semibold">回收站</h3>
          <div className="flex items-center gap-2">
            <button
              data-trash-empty
              onClick={() => setConfirmEmpty(true)}
              disabled={busyAll || loading || items.length === 0}
              className="px-2 py-1 rounded-md text-[12px] text-error hover:bg-error/10 transition-colors disabled:opacity-40 disabled:hover:bg-transparent"
            >清空回收站</button>
            <button onClick={onClose} className="px-2 py-1 rounded-md text-[12px] text-dimmed hover:bg-surface-hover transition-colors">关闭</button>
          </div>
        </div>

        {/* 边界说明 —— 见文件头 */}
        <p className="px-5 pt-2.5 text-[11px] text-disabled shrink-0">
          回收站只保留文档本体（原文与笔记），上传的原始文件不在此范围。超过保留期的条目会被自动清理。
        </p>

        {error && (
          <p data-trash-error className="px-5 pt-2 text-[12px] text-error shrink-0">{error}</p>
        )}

        {/* 列表 */}
        <div className="flex-1 overflow-y-auto px-2 py-2 min-h-[120px]">
          {loading && items.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-8 text-[12px] text-disabled">
              <Loader2 size={13} className="animate-spin" /> 正在读取…
            </div>
          ) : items.length === 0 ? (
            <div data-trash-empty-state className="flex flex-col items-center gap-1 py-8 text-[12px] text-disabled">
              <Trash2 size={20} className="opacity-40" />
              <span>回收站是空的</span>
            </div>
          ) : items.map(item => (
            <div key={item.id} data-trash-row
              className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-surface-hover transition-colors group">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-foreground truncate" title={item.title}>{item.title || '（无标题）'}</div>
                <div className="text-[11px] text-disabled truncate">
                  {SRC_LABEL[item.sourceType] || item.sourceType || '文档'}
                  {' · '}{fmtTime(item.deletedAt) || '删除时间未知'}
                  {/* `daysLeft` 是后端算好的（0 = 今天到期）。前端不拿删除时间自己减 ——
                      那是第二份「保留几天」的判据 */}
                  {' · '}
                  <span className={item.daysLeft <= 1 ? 'text-warning' : ''}>
                    {item.daysLeft <= 0 ? '今天到期' : `${item.daysLeft} 天后清理`}
                  </span>
                </div>
              </div>
              <button
                data-trash-restore
                onClick={() => void handleRestore(item)}
                disabled={busyId === item.id || busyAll}
                className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-md text-[11px] text-accent hover:bg-accent/10 transition-colors disabled:opacity-40"
                title="恢复到文档库（保留原文件夹与标签）">
                <RotateCcw size={12} />恢复
              </button>
              <button
                data-trash-purge
                onClick={() => setConfirmPurge(item)}
                disabled={busyId === item.id || busyAll}
                className="shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-dimmed hover:text-error hover:bg-error/10 transition-colors disabled:opacity-40"
                title="彻底删除（不可恢复）">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>

        {/* 二次确认：彻底删除单条 */}
        {confirmPurge && (
          <div data-trash-confirm className="absolute inset-0 z-10 flex items-center justify-center bg-black/30 rounded-2xl">
            <div className="bg-elevated rounded-xl shadow-2xl border border-border-light p-5 max-w-[300px] w-full mx-4">
              <div className="flex items-start gap-2 mb-2">
                <AlertTriangle size={16} className="text-error shrink-0 mt-0.5" />
                <h4 className="text-[14px] font-semibold">彻底删除</h4>
              </div>
              <p className="text-[12px] text-dimmed mb-4">
                「{confirmPurge.title || '（无标题）'}」将被永久删除，<strong>无法恢复</strong>。
              </p>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setConfirmPurge(null)}
                  className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
                <button data-trash-confirm-purge onClick={() => void handlePurge(confirmPurge)}
                  className="px-3 py-1.5 bg-error text-white text-[12px] font-medium rounded-lg hover:bg-error/80 transition-colors">彻底删除</button>
              </div>
            </div>
          </div>
        )}

        {/* 二次确认：清空 */}
        {confirmEmpty && (
          <div data-trash-confirm className="absolute inset-0 z-10 flex items-center justify-center bg-black/30 rounded-2xl">
            <div className="bg-elevated rounded-xl shadow-2xl border border-border-light p-5 max-w-[300px] w-full mx-4">
              <div className="flex items-start gap-2 mb-2">
                <AlertTriangle size={16} className="text-error shrink-0 mt-0.5" />
                <h4 className="text-[14px] font-semibold">清空回收站</h4>
              </div>
              <p className="text-[12px] text-dimmed mb-4">
                {items.length} 个文档将被永久删除，<strong>无法恢复</strong>。
              </p>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setConfirmEmpty(false)}
                  className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
                <button data-trash-confirm-empty onClick={() => void handleEmpty()}
                  className="px-3 py-1.5 bg-error text-white text-[12px] font-medium rounded-lg hover:bg-error/80 transition-colors">清空</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
