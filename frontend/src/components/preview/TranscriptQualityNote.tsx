/**
 * 转录质量指示 — 转录失败警告 / PDF 偏短提示 / 字符数。
 * props 驱动，无 store 依赖。
 */
import { AlertTriangle, Lightbulb } from 'lucide-react'

export function TranscriptQualityNote({ rawText, sourceType }: { rawText: string; sourceType?: string }) {
  const len = rawText.length
  const warnStart = rawText.startsWith('[转录失败') || rawText === '[转录结果为空]'
  const shortThreshold = 500
  const isShort = !warnStart && len < shortThreshold && sourceType === 'pdf'
  if (warnStart) {
    return <p className="mt-3 text-[11px] text-error bg-error/10 rounded-lg px-3 py-2 flex items-center gap-1"><AlertTriangle size={12} /> 转录失败 — 请尝试在左侧「生成选项」中切换引擎后点「重新转录」</p>
  }
  if (isShort) {
    return <p className="mt-3 text-[11px] text-warning bg-warning/10 rounded-lg px-3 py-2 flex items-center gap-1"><Lightbulb size={12} /> 转录仅 {len} 字符，对 PDF/Document 来说偏短。建议尝试切换为「扫描件（逐页 OCR）」引擎后点「重新转录」</p>
  }
  return <p className="mt-3 text-[11px] text-disabled">{len.toLocaleString()} 字符</p>
}
