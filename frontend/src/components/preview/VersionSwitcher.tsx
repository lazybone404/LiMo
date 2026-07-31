/**
 * 版本切换器 — v1/v2 切换（由 TabDef.showVersionSwitcher 驱动）。
 * 过滤/高亮/≤1 隐藏逻辑内聚，props 驱动，无 store 依赖。
 */
import { Loader2 } from 'lucide-react'

export interface VersionSwitcherProps {
  mode: 'notes' | 'polished'
  versions: Array<{ version: number; id: string; createdAt: string; status?: string; mode?: string }>
  activeMeta: { mode: string; version: number } | null
  loading: boolean
  onSelect: (version: number) => void
}

export function VersionSwitcher({ mode, versions, activeMeta, loading, onSelect }: VersionSwitcherProps) {
  const modeVersions = versions.filter(v => v.mode === mode)
  if (modeVersions.length <= 1) return null
  // 高亮：优先匹配 markdownMeta（同 mode），否则匹配最新版本
  const activeVersion = (activeMeta?.mode === mode ? activeMeta?.version : undefined) ?? modeVersions[0]?.version
  return (
    <div className="flex items-center gap-1">
      <span className="text-[10px] text-disabled">版本</span>
      {loading && <Loader2 size={10} className="animate-spin text-accent" />}
      {modeVersions.map(v => (
        <button key={v.id}
          onClick={() => onSelect(v.version)}
          disabled={loading}
          className={`px-2 py-0.5 rounded text-[11px] font-mono transition-all disabled:opacity-40 ${
            activeVersion === v.version
              ? 'bg-accent text-white'
              : 'text-muted hover:bg-surface-hover'
          }`}
        >v{v.version}</button>
      ))}
    </div>
  )
}
