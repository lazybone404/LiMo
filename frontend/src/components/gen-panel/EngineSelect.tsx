export interface EngineOption {
  id: number; provider: string; name: string; desc: string
  capabilities?: string[]
  license?: string   // SPDX: "Apache-2.0" | "AGPL-3.0" | ... (插件引擎)
  plugin?: boolean    // 是否为用户安装的插件引擎
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

/** 许可证标签颜色 */
function licenseBadge(license: string): { bg: string; text: string } {
  const risky = ['AGPL', 'GPL', 'AGPL-3.0', 'GPL-3.0', 'GPL-2.0']
  const isRisky = risky.some(r => license.includes(r))
  return isRisky
    ? { bg: 'bg-warning/10', text: 'text-warning' }
    : { bg: 'bg-surface-hover', text: 'text-dimmed' }
}

/** 选项文本：引擎名 · 能力标签 — 描述 */
function optionLabel(opt: EngineOption) {
  const caps = opt.capabilities?.join(' ') || ''
  let text = opt.name
  if (opt.license) text += ` [${opt.license}]`
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

  const licBadge = selectedOpt?.license ? licenseBadge(selectedOpt.license) : null

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
      {selectedOpt && isSection && (
        <p className="text-[10px] text-accent/70 mt-0.5 ml-[5rem] flex items-center gap-2">
          当前引擎: {selectedOpt.name}
          {licBadge && (
            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium ${licBadge.bg} ${licBadge.text}`}>
              {selectedOpt.license}
            </span>
          )}
        </p>
      )}
      {extraHint && <p className="text-[10px] text-warning px-1">{extraHint}</p>}
    </div>
  )
}
