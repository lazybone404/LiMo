/** 文件子类型 → 可用引擎名列表（GenPanel / ImportSection / ProviderSettings 共用） */
export const DOC_ENGINE_NAMES: Record<string, string[]> = {
  pdf: ['pymupdf4llm', 'pymupdf', 'pymupdf-text', 'pdf-ocr'],
  word: ['python-docx', 'python-docx-text', 'markitdown'],
  ppt: ['python-pptx', 'python-pptx-text', 'markitdown'],
  excel: ['openpyxl', 'markitdown'],
  epub: ['ebooklib', 'markitdown'],
  // PDF 子场景（Settings 页面独立行，引擎列表同 pdf）
  'text+image': ['pymupdf4llm', 'pymupdf'],
  'text-only': ['pymupdf-text'],
  'scan-ocr': ['pdf-ocr'],
  // Word 子场景
  'word-text': ['python-docx-text'],
  'word-convert': ['markitdown'],
  // PPT 子场景
  'ppt-text': ['python-pptx-text'],
  'ppt-convert': ['markitdown'],
}

/** 文件子类型 → 引擎绑定子场景 key */
export const SUB_SCENARIO: Record<string, string> = {
  pdf: 'doc:text+image',
  word: 'doc:word',
  ppt: 'doc:ppt',
  excel: 'doc:excel',
  epub: 'doc:epub',
}

/** 文件扩展名 → 子类型（用于从 sourceFileName 推断） */
export const EXT_TO_SUBTYPE: Record<string, string> = {
  '.pdf': 'pdf', '.doc': 'word', '.docx': 'word',
  '.ppt': 'ppt', '.pptx': 'ppt',
  '.xls': 'excel', '.xlsx': 'excel', '.csv': 'excel',
  '.epub': 'epub', '.mobi': 'epub',
}

/** 所有文档类 sourceType */
export const ALL_DOC_TYPES = ['pdf', 'docx', 'pptx', 'xlsx', 'epub', 'document', 'file']

// ── 类型元数据（GenPanel / GenerateOptions / PreviewArea 共用）──

export interface TypeMeta {
  sourceType: string
  isVideo: boolean
  isDoc: boolean
  isPDF: boolean
  isImage: boolean
  isExcel: boolean
  isMedia: boolean       // 视频/网页等媒体类型
  hasPolish: boolean      // 是否有整理版原文（Excel 没有）
  hasVL: boolean          // 是否支持 VL 视觉理解
  hasImages: boolean      // 是否支持保留文档图片（PDF/Word/PPT）
  hasFactCheck: boolean    // 是否支持事实验证（仅 PreviewArea tab 显示用）
  coreProducts: { name: string; label: string; description: string }[]  // 生成面板的产物复选框
  generateLabel: string   // 生成按钮文案
  /** PreviewArea tabs */
  tabs: { key: string; label: string }[]
}

/** 根据 sourceType 推导全部类型元数据，一处定义处处复用 */
export function getTypeMeta(sourceType: string, _genOptions: Record<string, string[]> = {}): TypeMeta {
  const st = sourceType || 'webpage'
  const isVideo = st === 'video'
  const isImage = st === 'image'
  const isExcel = st === 'xlsx'
  const isPDF = st === 'pdf'
  const isDoc = ALL_DOC_TYPES.includes(st)
  const isMedia = isVideo || st === 'webpage' || st === 'link'

  // tabs
  let tabs: { key: string; label: string }[]
  if (isImage) {
    tabs = [
      { key: 'original', label: '原图' },
      { key: 'extract', label: '提取文字' },
      { key: 'table', label: '表格数据' },
      { key: 'digitize', label: '文档数字化' },
      { key: 'understand', label: '理解内容' },
    ]
  } else if (isExcel) {
    tabs = [
      { key: 'data_preview', label: '数据预览' },
      { key: 'summary', label: '数据摘要' },
    ]
  } else if (isDoc) {
    if (st === 'epub') {
      tabs = [
        { key: 'skeleton', label: '骨架' },
        { key: 'summary', label: '笔记' },
        { key: 'polished', label: '整理版原文' },
        { key: 'transcript', label: '转录' },
        { key: 'fact_check', label: '事实核查' },
        { key: 'mindmap', label: '思维导图' },
      ]
    } else {
      tabs = [
        { key: 'summary', label: '笔记' },
        { key: 'polished', label: '整理版原文' },
        { key: 'transcript', label: '转录' },
        { key: 'fact_check', label: '事实核查' },
        { key: 'mindmap', label: '思维导图' },
      ]
    }
  } else {
    tabs = [
      { key: 'summary', label: '笔记' },
      { key: 'polished', label: '整理版原文' },
      { key: 'transcript', label: '原始转录' },
      { key: 'fact_check', label: '事实核查' },
      { key: 'mindmap', label: '思维导图' },
    ]
  }

  return {
    sourceType: st,
    isVideo, isDoc, isPDF, isImage, isExcel, isMedia,
    hasPolish: !isExcel && !isImage,
    hasVL: isVideo,
    hasImages: st === 'pdf' || st === 'docx' || st === 'pptx' || st === 'epub',
    hasFactCheck: !isImage && !isExcel,
    coreProducts: isImage ? [] : isExcel ? [
      { name: 'both', label: '数据摘要', description: '概况·关键指标·字段解读' },
    ] : [
      { name: 'notes', label: '笔记', description: '结构化笔记：提取关键信息' },
      { name: 'polished', label: '整理版原文', description: '去噪音、修正错别字、合并段落' },
    ],
    generateLabel: isExcel ? '生成数据摘要' : '生成',
    tabs,
  }
}

/** 风格标签 */
export const STYLE_LABELS: Record<string, string> = {
  none: '无',
  minimal: '精简', detailed: '全文精要', academic: '学术', life: '生活',
  tutorial: '教程', xiaohongshu: '小红书', business: '商务',
  meeting_minutes: '会议', task_oriented: '任务',
  imrad: 'IMRaD 精读', meeting: '会议纪要',
}
