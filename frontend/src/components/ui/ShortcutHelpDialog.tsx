/**
 * 快捷键一览（`Ctrl+/`）。数据全部来自 `lib/shortcuts.ts`，本组件只管排版。
 *
 * ⚠️ **Portal 到 body**（关键模式 #67）：`DocLibrary` 的根 `<aside>` 带
 * `backdrop-blur-[20px]`，它会创建包含块把 `fixed` 后代锁进面板大小的一小块 ——
 * 而这个对话框可能正是从文档库那边打开的。
 *
 * ⚠️ 关闭用**裸 Esc**：`App.tsx::handleAppKeyDown` 开头有
 * `if (!(e.metaKey || e.ctrlKey)) return`，裸 Esc 进不去那里，故在这里自己挂
 * `document` 监听（与沉浸阅读的逃生口同款做法）。
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import {
  SHORTCUTS, GROUP_ORDER, GROUP_LABELS, formatShortcut, type ShortcutGroup,
} from '../../lib/shortcuts'

export function ShortcutHelpDialog({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // 与打开它的那一键成对：Ctrl+/ 也能关（用户最可能重复按的就是它）
      if (e.key === 'Escape' || (e.key === '/' && (e.ctrlKey || e.metaKey))) {
        e.preventDefault()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [onClose])

  const byGroup = (g: ShortcutGroup) => SHORTCUTS.filter(s => s.group === g)

  return createPortal(
    <div
      className="fixed inset-0 z-[10050] flex items-center justify-center bg-black/25 p-6"
      onClick={onClose}
      data-shortcut-help
    >
      <div
        className="w-full max-w-[560px] max-h-[80vh] overflow-y-auto rounded-2xl border border-border-light bg-elevated shadow-2xl shadow-black/10"
        // 点面板内部不该关掉它
        onClick={e => e.stopPropagation()}
      >
        <header className="sticky top-0 flex items-center justify-between px-5 py-3 border-b border-border-light bg-elevated/95 backdrop-blur-xl">
          <div>
            <h2 className="text-[15px] font-semibold text-foreground">键盘快捷键</h2>
            <p className="text-[11px] text-dimmed mt-0.5">按 Esc 关闭</p>
          </div>
          <button
            onClick={onClose}
            data-shortcut-help-close
            className="p-1.5 rounded-lg hover:bg-surface-hover text-muted hover:text-foreground transition-colors"
            title="关闭"
          >
            <X size={15} />
          </button>
        </header>

        <div className="px-5 py-3">
          {GROUP_ORDER.map(g => {
            const items = byGroup(g)
            if (items.length === 0) return null
            return (
              <section key={g} className="mb-4 last:mb-1">
                <h3 className="text-[11px] font-semibold text-dimmed uppercase tracking-wide mb-1.5">
                  {GROUP_LABELS[g]}
                </h3>
                <dl className="divide-y divide-border-light/60">
                  {items.map((s, i) => (
                    <div key={i} className="flex items-center justify-between gap-4 py-1.5">
                      <dt className="text-[13px] text-foreground leading-snug">{s.label}</dt>
                      <dd className="shrink-0">
                        <kbd className="inline-block px-2 py-0.5 rounded-md border border-border-light bg-surface text-[11px] font-medium text-dimmed tabular-nums">
                          {formatShortcut(s)}
                        </kbd>
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )
          })}
        </div>
      </div>
    </div>,
    document.body,
  )
}
