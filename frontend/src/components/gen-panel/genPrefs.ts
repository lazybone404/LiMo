export interface GenPrefs {
  style: string
  selectedCore: string[]               // ["notes","polished"] — 生成产物的复选框
  formatToc: boolean; formatLink: boolean; formatSummary: boolean; formatScreenshot: boolean
  formatChart: boolean
  /** @deprecated replaced by target_language — kept for old data compat */
  formatTranslate: boolean; formatFormulas: boolean; formatReferences: boolean; formatImages: boolean
  imageAnalyze: boolean; imageOptimize: boolean
  ocrEnabled: boolean; ocrModelId: number | null
  selectedModelId: number | null
  videoUnderstanding: boolean; videoInterval: number
  videoDirect: boolean  // 视频直传模型处理（DashScope SDK，服务端抽帧；默认关 + 记忆）
  gridCols: number; gridRows: number
  dedupeEnabled: boolean; unitWidth: number; unitHeight: number
  saveQuality: number; frameQualityFilter: boolean
  imageMode: 'extract' | 'table' | 'digitize' | 'understand'
  userNotes: string
  notesExpanded: boolean
  streamDisplay: boolean
}

const DEFAULTS: GenPrefs = {
  style: 'detailed', selectedCore: ['notes', 'polished'],
  formatToc: true, formatLink: true, formatSummary: true, formatScreenshot: false, formatChart: false,
  formatTranslate: false, formatFormulas: false, formatReferences: false, formatImages: false,
  imageAnalyze: false, imageOptimize: true,
  ocrEnabled: false, ocrModelId: null,
  selectedModelId: null,
  videoUnderstanding: false, videoInterval: 6,
  videoDirect: false,
  gridCols: 2, gridRows: 2,
  dedupeEnabled: true, unitWidth: 960, unitHeight: 540,
  saveQuality: 90, frameQualityFilter: true,
  imageMode: 'extract',
  userNotes: '',
  notesExpanded: false,
  streamDisplay: true,
}

export function loadGenPrefs(): GenPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem('genPrefs') || '{}')
    // 迁移旧格式 → selectedCore
    if (!raw.selectedCore || raw.selectedCore.length === 0) {
      const products: string[] = []
      // 旧 selectedProducts → 去掉 fact_check
      if (raw.selectedProducts) {
        for (const p of raw.selectedProducts) {
          if (p !== 'fact_check') products.push(p)
        }
        delete raw.selectedProducts
      }
      // 旧 genMode
      if (raw.genMode) {
        if (raw.genMode === 'both' || raw.genMode === 'notes') products.push('notes')
        if (raw.genMode === 'both' || raw.genMode === 'polished') products.push('polished')
        delete raw.genMode
      }
      // 旧 genNotes / genPolish
      if (raw.genNotes) products.push('notes')
      if (raw.genPolish) products.push('polished')
      delete raw.genNotes; delete raw.genPolish; delete raw.factCheck
      if (products.length > 0) {
        raw.selectedCore = [...new Set(products)]
      } else {
        delete raw.selectedCore
      }
    }
    // 迁移旧风格 key：meeting_minutes → meeting
    if (raw.style === 'meeting_minutes') {
      raw.style = 'meeting'
    }
    return { ...DEFAULTS, ...raw }
  } catch { return { ...DEFAULTS } }
}

export function saveGenPrefs(prefs: Partial<GenPrefs>) {
  const current = loadGenPrefs()
  try {
    localStorage.setItem('genPrefs', JSON.stringify({ ...current, ...prefs }))
  } catch {
    // 存储配额超限或隐私模式下静默失败
  }
}
