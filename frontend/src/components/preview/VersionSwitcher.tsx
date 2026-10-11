/**
 * 版本切换器 — 产物版本**下拉**（由 TabDef.showVersionSwitcher 驱动）。
 *
 * 触发按钮显示**当前版**（`v3 · 2.4k字 ▾`）；展开后每行一个版本，两行式：
 *   第一行 `v{n}` + （编辑过的）「已编辑」徽标 + 当前项勾
 *   第二行 本地化时间 · 字数 · 来源（模型名）
 * 点击行 = 切换（`onSelect(version)`）；当前行高亮。
 *
 * ⚠️ **切换是纯视图操作、零写入**（产品模型：版本 = AI 生成出来的东西，用户在版本间
 * 只是「看哪一版」，没有「当前版指针」这个持久概念）。故本组件不含任何保存逻辑。
 *
 * ⚠️ 版本数 ≤1 时整块隐藏：只有一个版本时「切换」没有对象，留一个点不动的按钮
 * 只是噪音（此行为自 2026-08 起如此，本轮保留）。
 *
 * 过滤/高亮/隐藏逻辑内聚，props 驱动，无 store 运行时依赖（`MarkdownVersion`
 * 是 type-only import，编译期擦除）。
 */
import { useState } from 'react'
import { Check, ChevronDown, History, Loader2 } from 'lucide-react'
import type { MarkdownVersion } from '../../store/types'
// ⚠️ 时间 / 字数的格式化判据住在 `lib/artifactMeta.ts`（本组件与预览区自证条共用）。
// 原先它们在本文件里是模块私有的；抽出的理由是自证条要用同一套渲染，各写一份就会
// 慢慢漂移且无人报错（模式 #54）。函数体逐字搬移，行为不变。
import { fmtChars, fmtTime } from '../../lib/artifactMeta'

export interface VersionSwitcherProps {
  mode: 'notes' | 'polished'
  versions: MarkdownVersion[]
  activeMeta: { mode: string; version: number } | null
  loading: boolean
  onSelect: (version: number) => void
}

export function VersionSwitcher({ mode, versions, activeMeta, loading, onSelect }: VersionSwitcherProps) {
  const [open, setOpen] = useState(false)
  const modeVersions = versions.filter(v => v.mode === mode)
  if (modeVersions.length <= 1) return null
  // 高亮：优先匹配 markdownMeta（同 mode），否则匹配最新版本
  const activeVersion = (activeMeta?.mode === mode ? activeMeta?.version : undefined) ?? modeVersions[0]?.version
  const active = modeVersions.find(v => v.version === activeVersion)
  const activeChars = fmtChars(active?.chars)

  return (
    <div className="relative">
      <button
        data-version-switcher-toggle
        onClick={() => setOpen(v => !v)}
        disabled={loading}
        aria-expanded={open}
        title="切换产物版本"
        className="h-full flex items-center gap-1 px-2 rounded-lg text-[11px] text-muted hover:bg-surface-hover transition-colors disabled:opacity-40"
      >
        {loading
          ? <Loader2 size={11} className="animate-spin text-accent" />
          : <History size={11} />}
        <span className="font-mono">v{activeVersion}</span>
        {activeChars && <span className="text-disabled">{activeChars}</span>}
        <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <>
          {/* 点击浮层外任意处关闭 —— 与 FilterPopover 同款透明遮罩（不用 document
              上的 mousedown 监听：那要额外处理「点按钮本身也算外部」的边界） */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            data-version-switcher
            className="absolute right-0 top-full mt-1 z-50 w-[268px] max-h-[320px] overflow-y-auto rounded-xl border border-border-light bg-elevated shadow-2xl py-1"
          >
            <div className="px-3 py-1 text-[11px] font-medium text-dimmed">版本（共 {modeVersions.length} 版）</div>
            {modeVersions.map(v => {
              const isActive = v.version === activeVersion
              const chars = fmtChars(v.chars)
              const time = fmtTime(v.createdAt)
              const metaLine = [time, chars, v.source].filter(Boolean).join(' · ')
              return (
                <button
                  key={v.id}
                  data-version-row={v.version}
                  data-active={isActive || undefined}
                  onClick={() => { setOpen(false); onSelect(v.version) }}
                  className={`w-full text-left px-3 py-1.5 transition-colors ${
                    isActive ? 'bg-accent/10' : 'hover:bg-surface-hover'
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className={`font-mono text-[11px] ${isActive ? 'text-accent' : 'text-dimmed'}`}>
                      v{v.version}
                    </span>
                    {v.edited && (
                      <span data-version-edited className="px-1 rounded text-[10px] bg-accent/15 text-accent">已编辑</span>
                    )}
                    {isActive && <Check size={11} className="ml-auto text-accent" />}
                  </div>
                  {/* 第二行整行可能为空（历史产物无时间/无来源）——空时整行不渲染，
                      不留一条 4px 的空白带 */}
                  {metaLine && (
                    <div className="mt-0.5 text-[10px] text-disabled truncate">{metaLine}</div>
                  )}
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
