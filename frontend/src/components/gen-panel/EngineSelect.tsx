// ── 策略标签：引擎名 → 用途说明，显示在选项名后面 ──
// 自动注册到 engine_options 后，前端下拉可选
const STRATEGY_LABEL: Record<string, Record<string, string>> = {
  doc: {
    pymupdf4llm: '推荐 · 全自动（文字+表格+嵌入图+矢量图）',
    pymupdf: '文字+表格+嵌入位图',
    'pymupdf-text': '仅文字+表格',
    'pdf-ocr': '扫描件OCR',
    markitdown: '微软官方 · DOCX/PPTX/XLSX/EPUB',
    'python-docx': '文字+表格',
    'python-docx-text': '仅文字+表格',
    'python-pptx': '文字+备注（图片提取有限）',
    'python-pptx-text': '仅文字+备注',
    openpyxl: 'Markdown表格',
    ebooklib: '章节+元数据',
  },
  asr: {
    'bcut': '免费云转写 · B站必剪', 'faster-whisper-small': '本地快速', 'faster-whisper-medium': '本地平衡',
    'dashscope-asr': '云端精转',
  },
  ocr: {
    'dashscope-ocr': '云端高精', tesseract: '本地离线',
  },
}

export interface EngineOption {
  id: number; provider: string; name: string; desc: string
  capabilities?: string[]
}

const CAP_LABELS: Record<string, string> = {
  text: '文字', table: '表格', image: '图片', ocr: 'OCR',
  pdf: 'PDF', office: '文档', asr: '语音',
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

/** 选项文本：引擎名 · 能力标签 — 策略标签 */
function optionLabel(opt: EngineOption, category: string) {
  const caps = opt.capabilities?.map(c => CAP_LABELS[c] || c).join(' ') || ''
  const tag = STRATEGY_LABEL.doc?.[opt.name] || STRATEGY_LABEL[category]?.[opt.name]
  let text = opt.name
  if (caps) text += ` · ${caps}`
  if (tag) text += ` — ${tag}`
  return text
}

export function EngineSelect({ category, label, options, binding, onChange, extraHint, variant = 'card' }: Props) {
  if (options.length === 0) return null

  const selectedOpt = binding != null ? options.find(o => o.id === binding) : null
  const defaultOpt = options.find(o => STRATEGY_LABEL.doc?.[o.name]) || options[0]
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
            系统默认{defaultOpt ? `（${optionLabel(defaultOpt, category)}）` : ''}
          </option>
          {options.map(o => (
            <option key={o.id} value={o.id}>{optionLabel(o, category)}</option>
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
