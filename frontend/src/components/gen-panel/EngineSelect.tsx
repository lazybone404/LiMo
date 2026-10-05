import { useStore } from '../../store'

export interface EngineOption {
  id: number; provider: string; name: string; desc: string
  capabilities?: string[]
  license?: string   // SPDX: "Apache-2.0" | "AGPL-3.0" | ... (插件引擎，仅展示)
  plugin?: boolean    // 是否为用户安装的插件引擎
  displayName?: string  // 插件展示名（缺省回退 name）
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

/** 许可证徽章 — 仅展示（v2：主程序不判断插件协议，无警告语义） */
function licenseBadge(): { bg: string; text: string } {
  return { bg: 'bg-surface-hover', text: 'text-dimmed' }
}

/**
 * 选项文本：展示名 · 能力标签 — 描述
 *
 * ⚠️ 能力位**必须查表翻译**：此前是 `capabilities.join(' ')`，用户在下拉里看到的是
 * 裸英文（`pypdfium2` 的 `text image pdf`）。标签表由后端 capabilities 下发
 * （`core/registry.ENGINE_CAPABILITY_LABELS`），**前端不另存一份**——后端新增能力位时
 * 前端自动跟上，而不是显示英文。
 *
 * ⚠️ 与 `lib/modelCaps.ts` 的 `capLabel` **不是同一个表**：那是**模型**能力位
 * （这个模型收得下什么模态），这是**引擎**能力位（这个引擎能从文件里取出什么）。
 * 两边都有 `text`/`image`，但文案与含义不同，混用会互相污染。
 */
export function optionLabel(opt: EngineOption, capLabels?: Record<string, string>) {
  const caps = (opt.capabilities || []).map(c => capLabels?.[c] || c).join(' ')
  let text = opt.displayName || opt.name
  if (opt.license) text += ` [${opt.license}]`
  if (caps) text += ` · ${caps}`
  if (opt.desc) text += ` — ${opt.desc}`
  return text
}

export function EngineSelect({ category, label, options, binding, onChange, extraHint, variant = 'card' }: Props) {
  // ⚠️ hooks 必须在 `return null` 之前（早退分支会让 hook 数量在两次渲染间变化）
  const capLabels = useStore(s => s.capabilities?.engineLabels)
  if (options.length === 0) return null

  const selectedOpt = binding != null ? options.find(o => o.id === binding) : null
  const defaultOpt = options[0]
  const isSection = variant === 'section'
  const selectClass = isSection
    ? 'bg-surface border border-border-default rounded-[10px]'
    : 'bg-elevated border border-border-default rounded-lg'

  const licBadge = selectedOpt?.license ? licenseBadge() : null

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
            系统默认{defaultOpt ? `（${optionLabel(defaultOpt, capLabels)}）` : ''}
          </option>
          {options.map(o => (
            <option key={o.id} value={o.id}>{optionLabel(o, capLabels)}</option>
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
