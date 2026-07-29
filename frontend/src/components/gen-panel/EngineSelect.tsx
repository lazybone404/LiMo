export interface EngineOption {
  id: number; provider: string; name: string; desc: string
  capabilities?: string[]
}

interface Props {
  category: string
  label: string
  options: EngineOption[]
  binding: number | null
  onChange: (category: string, modelId: number | null) => void
  extraHint?: string
  variant?: 'card' | 'section'
}

/** 选项文本：引擎名 · 能力标签 — 描述（描述由后端提供，不再硬编码） */
function optionLabel(opt: EngineOption) {
  const caps = opt.capabilities?.join(' ') || ''
  let text = opt.name
  if (caps) text += ` · ${caps}`
  if (opt.desc) text += ` — ${opt.desc}`
  return text
}

export function EngineSelect({ category, label, options, binding, onChange, extraHint, variant = 'card' }: Props) {
  if (options.length === 0) return null

  const selectedOpt = binding != null ? options.find(o => o.id === binding) : null
  const defaultOpt = options[0]
  const isSection = variant === 'section'
  const selectClass = isSection
    ? 'bg-surface border border-border-default rounded-[10px]'
    : 'bg-elevated border border-border-default rounded-lg'

  return (
    <div className="space-y-1">
      <div className={`flex items-center gap-3 min-w-0 ${isSection ? '' : ''}`}>
        <span className={`shrink-0 ${isSection ? 'text-[13px] font-medium w-20' : 'text-[11px] text-disabled'}`}>{label}</span>
        <select
          value={binding ?? ''}
          onChange={e => onChange(category, e.target.value ? Number(e.target.value) : null)}
          className={`w-full min-w-0 outline-none truncate ${isSection ? 'px-3 py-2 text-[13px] flex-1' : 'px-2 py-1.5 text-[12px]'} ${selectClass}`}
        >
          <option value="">
            系统默认{defaultOpt ? `（${optionLabel(defaultOpt)}）` : ''}
          </option>
          {options.map(o => (
            <option key={o.id} value={o.id}>{optionLabel(o)}</option>
          ))}
        </select>
      </div>
      {selectedOpt && selectedOpt.desc && isSection && (
        <p className="text-[10px] text-disabled px-1">{selectedOpt.desc}</p>
      )}
      {extraHint && <p className="text-[10px] text-warning px-1">{extraHint}</p>}
    </div>
  )
}
