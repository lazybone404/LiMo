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

export function ProcessingPlaceholder({ task, mode }: { task?: TaskInfo; mode?: string }) {
  let label = '处理中...'
  let reasoning = ''
  if (task) {
    if (task.kind === 'generate' && (task.mode || mode)) {
      // 产物级进度：mode 优先用调用方传入（与当前 tab 匹配）——并行双产物时
      // task.mode 是最后到达事件的产品，会在两路间来回跳，不能依赖它定位产物
      const m = mode || task.mode
      if (m === 'polished' || m === 'notes') {
        const n = task.progress?.[m] || 0
        label = m === 'polished'
          ? `正在生成整理版原文...（已生成 ${n} 字符）`
          : `正在生成笔记...（已生成 ${n} 字符）`
        reasoning = task.reasoning?.[m]?.trim() || ''
      }
    } else if (task.kind === 'process' && task.mode) {
      label = `处理中 — ${MODE_LABELS[task.mode] || task.mode}`
    } else {
      label = LABELS[task.kind] || '处理中...'
    }
  }
  // 思考链（deepseek-v4 reasoning_content）：临时展示打消等待焦虑——
  // 正式 content 一开始流式，displayContent 有值 → 本占位自动被正式内容替换
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-3 px-8">
      <Loader2 className="animate-spin text-accent" size={28} />
      <p className="text-[14px] font-medium text-foreground">{label}</p>
      {reasoning ? (
        <div className="w-full max-w-md max-h-36 overflow-y-auto text-[11px] text-dimmed leading-relaxed bg-surface/60 rounded-lg p-3">
          <span className="text-[10px] text-disabled block mb-1">模型思考中：</span>
          {reasoning}
        </div>
      ) : (
        <p className="text-[12px] text-disabled">请耐心等待，AI 处理中...</p>
      )}
    </div>
  )
}
