/**
 * 处理进度占位 — props 驱动，无 store 依赖。
 * 显示第一个活跃任务（多任务并发时展示最相关的那个，语义与原 PreviewArea 一致）。
 */
import { Loader2 } from 'lucide-react'
import type { TaskInfo } from '../../store/types'

const MODE_LABELS: Record<string, string> = {
  extract: '提取文字', table: '表格数据', digitize: '文档数字化', understand: '理解内容',
}

const LABELS: Record<string, string> = {
  'generate': '正在生成...',
  'process': '处理中...',
  'retranscribe': '重新转录中...',
  'import': '导入中...',
}

export function ProcessingPlaceholder({ task }: { task?: TaskInfo }) {
  let label = '处理中...'
  if (task) {
    if (task.kind === 'process' && task.mode) {
      label = `处理中 — ${MODE_LABELS[task.mode] || task.mode}`
    } else {
      label = LABELS[task.kind] || '处理中...'
    }
  }
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-3">
      <Loader2 className="animate-spin text-accent" size={28} />
      <p className="text-[14px] font-medium text-foreground">{label}</p>
      {task && <p className="text-[12px] text-disabled">请耐心等待，AI 处理中...</p>}
    </div>
  )
}
