import { TabRegistry } from '../../lib/tabRegistry'

/** 文件扩展名 → 子类型（纯静态，不会变） */
export const EXT_TO_SUBTYPE: Record<string, string> = {
  '.pdf': 'pdf', '.doc': 'word', '.docx': 'word',
  '.ppt': 'ppt', '.pptx': 'ppt',
  '.xls': 'excel', '.xlsx': 'excel', '.csv': 'excel',
  '.epub': 'epub', '.mobi': 'epub',
}

/** 拖拽验证：每种类型的有效扩展名（纯静态） */
export const DROP_ACCEPT: Record<string, string[]> = {
  video: ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'],
  image: ['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.gif'],
  file:  ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.epub'],
}

/** 输入类型标签（纯静态，在 ImportSection 内联 JSX 中引用） */
export const INPUT_TYPE_LABELS: Record<string, string> = {
  link: '网页', video: '视频', text: '文本', file: '文件', image: '图片',
}

/** 导入中状态标签 */
export const IMPORTING_LABELS: Record<string, string> = {
  video: '转写中...', image: '导入中...', file: '文档解析中...', link: '导入中...', text: '导入中...',
}

/** 文件子类型 → 引擎绑定子场景 key（与后端 _ext_to_sub_scenario 对齐） */
export function getSubScenario(subtype: string): string {
  return `doc:${subtype}`
}

// ── 类型元数据（GenPanel / GenerateOptions / PreviewArea 共用）──

export interface TypeMeta {
  sourceType: string
  isVideo: boolean
  isDoc: boolean
  isPDF: boolean
  isImage: boolean
  isExcel: boolean
  isMedia: boolean
  hasPolish: boolean
  hasVL: boolean
  hasImages: boolean
  availableFormats: string[]
  coreProducts: { name: string; label: string; description: string }[]
  generateLabel: string
  tabs: { key: string; label: string }[]
}

const _ALL_DOC_TYPES = ['pdf', 'docx', 'pptx', 'xlsx', 'epub', 'document', 'file']

export function getTypeMeta(sourceType: string, genOptions: Record<string, string[]> = {}): TypeMeta {
  const st = sourceType || 'webpage'
  const isVideo = st === 'video'
  const isImage = st === 'image'
  const isExcel = st === 'xlsx'
  const isPDF = st === 'pdf'
  const isDoc = _ALL_DOC_TYPES.includes(st)
  const isMedia = isVideo || st === 'webpage' || st === 'link'

  // tabs 从 TabRegistry 自动生成 — 零硬编码，新增 tab 只需注册 TabDef
  const tabs = TabRegistry.forSourceType(st).map(t => ({ key: t.key, label: t.label }))

  return {
    sourceType: st,
    isVideo, isDoc, isPDF, isImage, isExcel, isMedia,
    hasPolish: !isExcel && !isImage,
    hasVL: isVideo,
    hasImages: st === 'pdf' || st === 'docx' || st === 'pptx' || st === 'epub',
    coreProducts: isImage ? [] : isExcel ? [
      { name: 'both', label: '数据摘要', description: '概况·关键指标·字段解读' },
    ] : [
      { name: 'notes', label: '笔记', description: '结构化笔记：提取关键信息' },
      { name: 'polished', label: '整理版原文', description: '去噪音、修正错别字、合并段落' },
    ],
    generateLabel: isExcel ? '生成数据摘要' : '生成',
    availableFormats: genOptions[st] || [],
    tabs,
  }
}

const STYLE_ALIAS: Record<string, string> = {
  meeting_minutes: 'meeting',
}

export function normalizeStyle(s: string): string {
  return STYLE_ALIAS[s] || s
}
