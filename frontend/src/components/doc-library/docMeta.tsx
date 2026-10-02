/**
 * 文档库的展示元数据 — 来源类型筛选行 / 类型配色 / 图标映射。
 *
 * ⚠️ 这些是**类型标识色**（对标 GitHub 的 language color），不是主题色：
 * 它们刻意不随六轴主题变化，跨主题保持可辨识。全部经内联 style 使用，不参与 Tailwind token。
 */
import {
  Film, Globe, FileText, PenLine, BarChart3, TrendingUp, BookOpen,
  Paperclip, MessageSquare, Image as ImageIcon, Download, Music, Mic,
  CheckCircle2, Bot, Eye, Settings, Layers,
} from 'lucide-react'

/** 来源类型筛选行（原 TAG_FILTERS —— 它筛的一直是 sourceType，不是用户标签） */
export const SOURCE_FILTERS = [
  { key: '', label: '全部', icon: '' },
  { key: 'video', label: '视频', icon: 'Film' },
  { key: 'webpage', label: '网页', icon: 'Globe' },
  { key: 'pdf', label: 'PDF', icon: 'FileText' },
  { key: 'docx', label: 'Word', icon: 'PenLine' },
  { key: 'pptx', label: 'PPT', icon: 'BarChart3' },
  { key: 'xlsx', label: 'Excel', icon: 'TrendingUp' },
  { key: 'epub', label: 'EPUB', icon: 'BookOpen' },
  { key: 'document', label: '文档', icon: 'Paperclip' },
  { key: 'chat', label: '聊天', icon: 'MessageSquare' },
  { key: 'image', label: '图片', icon: 'Image' },
  { key: 'text', label: '文本', icon: 'FileText' },
  // 多篇合成的对比综述（sourceType === 'composite'）。排在最末：它是二次产物，
  // 与上面 11 个「原始输入类型」不是同一层的东西
  { key: 'composite', label: '合成', icon: 'Layers' },
]

/** sourceType → 可读标签 */
export const SRC_LABEL: Record<string, string> = Object.fromEntries(
  SOURCE_FILTERS.filter(t => t.key).map(t => [t.key, t.label])
)

/** sourceType → 主题色（缩略图背景 + 类型标签）。bg 使用 rgba 透明度，亮暗模式均适用 */
export const TYPE_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  video:    { bg: 'rgba(255,59,48,0.12)',  text: '#FF3B30', dot: '#FF3B30' },
  webpage:  { bg: 'rgba(52,199,89,0.12)',  text: '#34C759', dot: '#34C759' },
  pdf:      { bg: 'rgba(255,149,0,0.12)',  text: '#FF9500', dot: '#FF9500' },
  docx:     { bg: 'rgba(0,122,255,0.12)',  text: '#007AFF', dot: '#007AFF' },
  pptx:     { bg: 'rgba(255,107,53,0.12)', text: '#FF6B35', dot: '#FF6B35' },
  xlsx:     { bg: 'rgba(48,179,92,0.12)',  text: '#30B35C', dot: '#30B35C' },
  epub:     { bg: 'rgba(175,82,222,0.12)', text: '#AF52DE', dot: '#AF52DE' },
  image:    { bg: 'rgba(255,45,85,0.12)',  text: '#FF2D55', dot: '#FF2D55' },
  text:     { bg: 'rgba(142,142,147,0.12)',text: '#8E8E93', dot: '#8E8E93' },
  document: { bg: 'rgba(0,122,255,0.12)',  text: '#007AFF', dot: '#007AFF' },
  chat:     { bg: 'rgba(90,200,250,0.12)', text: '#5AC8FA', dot: '#5AC8FA' },
  // 靛蓝：未被上面任何原始类型占用，与「合成」这个二次产物身份相称
  composite:{ bg: 'rgba(88,86,214,0.12)',  text: '#5856D6', dot: '#5856D6' },
}

/** 根据名称渲染对应的 lucide 图标（替代 emoji） */
export function IconByName({ name, size = 14 }: { name: string; size?: number }) {
  switch (name) {
    case 'Film': return <Film size={size} />
    case 'Globe': return <Globe size={size} />
    case 'FileText': return <FileText size={size} />
    case 'PenLine': return <PenLine size={size} />
    case 'BarChart3': return <BarChart3 size={size} />
    case 'TrendingUp': return <TrendingUp size={size} />
    case 'BookOpen': return <BookOpen size={size} />
    case 'Paperclip': return <Paperclip size={size} />
    case 'MessageSquare': return <MessageSquare size={size} />
    case 'Image': return <ImageIcon size={size} />
    case 'Download': return <Download size={size} />
    case 'Music': return <Music size={size} />
    case 'Mic': return <Mic size={size} />
    case 'CheckCircle2': return <CheckCircle2 size={size} />
    case 'Bot': return <Bot size={size} />
    case 'Eye': return <Eye size={size} />
    case 'Settings': return <Settings size={size} />
    case 'Layers': return <Layers size={size} />
    default: return <FileText size={size} />
  }
}

/** 取类型对应的图标名（筛选项里查，缺失回退 FileText） */
export function iconForType(sourceType: string): string {
  return SOURCE_FILTERS.find(t => t.key === sourceType)?.icon || 'FileText'
}
