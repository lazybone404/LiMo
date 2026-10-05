/**
 * UnsavedChangesDialog — 编辑未保存时的三态离开确认弹窗。
 *
 * 用户编辑产物（notes/polished）后未保存直接切 tab / 切文档时弹出，
 * 提供三个选择：
 *   - 保存并离开：保存当前编辑内容，然后执行离开动作（切换目标）
 *   - 不保存：丢弃未保存修改，直接离开
 *   - 取消：留在当前编辑状态
 *
 * 替代原先的 window.confirm 两态确认（确认=丢弃离开，无法"保存后离开"）。
 * 样式走主题变量（--tk-* → Tailwind token），零硬编码色值。
 */
import { AlertTriangle, Loader2, Save } from 'lucide-react'

interface UnsavedChangesDialogProps {
  /** 弹窗标题（默认「有未保存的修改」） */
  title?: string
  /** 提示正文 */
  message?: string
  /** 保存请求中（禁用按钮防重复） */
  saving?: boolean
  /** 保存并离开 */
  onSave: () => void | Promise<void>
  /** 不保存，直接离开 */
  onDiscard: () => void
  /** 取消，留在当前 */
  onCancel: () => void
}

export function UnsavedChangesDialog({
  title = '有未保存的修改',
  message = '当前内容有未保存的修改，是否保存后再离开？',
  saving = false,
  onSave,
  onDiscard,
  onCancel,
}: UnsavedChangesDialogProps) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40"
      onClick={saving ? undefined : onCancel}
    >
      <div
        className="bg-surface border border-border-light rounded-2xl shadow-xl p-6 w-[400px] max-w-[90vw]"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-full bg-warning/10 text-warning shrink-0">
            <AlertTriangle size={18} />
          </div>
          <div className="min-w-0">
            <h3 className="text-[14px] font-semibold mb-1">{title}</h3>
            <p className="text-[12px] text-muted leading-relaxed">{message}</p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 mt-5">
          <button
            onClick={() => onCancel()}
            disabled={saving}
            className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface-hover rounded-lg transition-colors disabled:opacity-40"
          >取消</button>
          <button
            onClick={() => onDiscard()}
            disabled={saving}
            className="px-3 py-1.5 text-[12px] text-warning hover:bg-warning/10 rounded-lg transition-colors disabled:opacity-40"
          >不保存</button>
          <button
            onClick={() => onSave()}
            disabled={saving}
            className="px-3 py-1.5 text-[12px] bg-accent text-on-accent rounded-lg hover:bg-accent-hover disabled:opacity-40 transition-colors flex items-center gap-1"
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            保存并离开
          </button>
        </div>
      </div>
    </div>
  )
}
