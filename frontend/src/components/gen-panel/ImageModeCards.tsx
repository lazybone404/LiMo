import { ScanText, Table, Eye, ScanLine } from 'lucide-react'
import { IMAGE_MODE_LABELS, imageModeLabel } from '../../lib/imageModes'

interface Props {
  imageMode: string
  setImageMode: (m: 'extract' | 'table' | 'digitize' | 'understand') => void
  ocrSupportedModes: Set<string>
  ocrModesReady: boolean
  imageResults: Record<string, Record<string, { text?: string; engine?: string; id?: string }>>
  currentDocId: string | null
  setActiveView: (v: string) => void
}

// ⚠️ 标签来自 `lib/imageModes`（单一真相源）；`desc` / `icon` 是本卡专属，留在原地
const MODES = [
  { key: 'extract', icon: ScanText, label: IMAGE_MODE_LABELS.extract, desc: '提取全部文字，保留段落结构' },
  { key: 'table', icon: Table, label: IMAGE_MODE_LABELS.table, desc: '优先识别表格，输出 Markdown 表格' },
  { key: 'digitize', icon: ScanLine, label: IMAGE_MODE_LABELS.digitize, desc: '文档扫描件转可编辑文本' },
  { key: 'understand', icon: Eye, label: IMAGE_MODE_LABELS.understand, desc: '描述并解读图片中的信息' },
] as const

export function ImageModeCards({ imageMode, setImageMode, ocrSupportedModes, ocrModesReady, imageResults, currentDocId, setActiveView }: Props) {
  return (
    <div className="pt-3 border-t border-border-light">
      <span className="text-[12px] font-medium text-dimmed mb-2 block">处理模式</span>
      <div className="grid grid-cols-2 gap-2">
        {MODES.map(m => {
          const supported = !ocrModesReady || ocrSupportedModes.has(m.key)
          const isSelected = imageMode === m.key
          return (
            <button key={m.key}
              onClick={() => supported && setImageMode(m.key as any)}
              disabled={!supported}
              className={`flex flex-col items-center gap-1 p-2.5 rounded-[10px] border text-center transition-all
                ${!supported
                  ? 'border-border-light bg-surface opacity-50 cursor-not-allowed'
                  : isSelected
                    ? 'border-accent bg-accent/10 shadow-[0_0_0_1px_var(--color-accent)]'
                    : 'border-border-default bg-elevated hover:border-dimmed hover:bg-surface'}`}>
              <m.icon size={18} className={!supported ? 'text-disabled' : isSelected ? 'text-accent' : 'text-muted'} />
              <span className={`text-[11px] font-medium ${!supported ? 'text-disabled' : isSelected ? 'text-accent' : 'text-foreground'}`}>{m.label}</span>
              <span className="text-[9px] text-disabled leading-tight">{supported ? m.desc : '当前引擎不支持此模式'}</span>
            </button>
          )
        })}
      </div>

      {/* 已有的处理结果快速入口 */}
      {imageResults[currentDocId || ''] && Object.keys(imageResults[currentDocId || '']).length > 0 && (
        <div className="mt-2 flex items-center gap-1 flex-wrap">
          <span className="text-[10px] text-disabled">已处理:</span>
          {Object.keys(imageResults[currentDocId || '']).map(mode => (
            <button key={mode} onClick={() => setActiveView(mode)}
              className="px-2 py-0.5 rounded-full bg-success/10 text-[10px] text-success font-medium hover:bg-success/20 transition-colors">
              {/* 与上方 MODES 同一份标签（`lib/imageModes`）——此前这里另抄了一份短版，
                  于是同一个模式在卡片上叫「文档数字化」、在结果 chip 上叫「数字化」 */}
              {imageModeLabel(mode)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
