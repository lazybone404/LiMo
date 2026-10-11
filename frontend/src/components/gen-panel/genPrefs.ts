import { normalizeStyle } from './constants'

export interface GenPrefs {
  style: string
  selectedCore: string[]               // ["notes","polished"] — 生成产物的复选框
  formatToc: boolean; formatLink: boolean; formatSummary: boolean; formatScreenshot: boolean
  formatChart: boolean
  /**
   * 追问卡片：笔记生成完之后，**独立再调一次** LLM，产出 4~6 个「读完可能想问的问题」，
   * 以可点 chip 的形式补在笔记末尾（点了带这篇文档直接问 AI）。
   * ⚠️ 它是 format 项（住在「笔记格式」区）但**不进 prompt** —— 与 `formatChart` 那种
   * 「勾了就往 prompt 里加规范」的项不同，它只驱动「落盘后再调一次」+ 旁路文件。
   * 默认关 = 零回归。
   */
  formatFollowUp: boolean
  /** @deprecated replaced by target_language — kept for old data compat */
  formatTranslate: boolean; formatFormulas: boolean; formatReferences: boolean; formatImages: boolean
  imageAnalyze: boolean; imageOptimize: boolean
  ocrEnabled: boolean; ocrModelId: number | null
  selectedModelId: number | null
  videoUnderstanding: boolean; videoInterval: number
  videoDirect: boolean  // 视频直传模型处理（DashScope SDK，服务端抽帧；默认关 + 记忆）
  /**
   * PDF 直传（仅对标注了 document 能力的模型可见，且仅 PDF）。
   * 原件随生成请求发给模型，本地抽取的文本**不进 prompt** —— 这是「只发原文件」
   * 的二选一，不是叠加（叠加会让同一份内容 token 双花）。
   * ⚠️ 代价：转录/导出/搜索用的仍是本地 rawText，扫描件上两者会真分叉（见后端
   * services/doc_understand.py 的「双真相源」段）。默认关 + 记忆）。
   */
  docDirect: boolean
  /**
   * 思考强度（仅对标注了 reasoning 能力的模型可见）。
   * 实际下发值由后端 effort.py::effort_for 决定 —— **整理版恒为 low**（除非选 max），
   * 这不是隐规则，GenerateOptions 里有对应文案。
   * 默认 'high' = 服务端默认档，故对未升级的后端也是零行为变化。
   */
  reasoningEffort: 'low' | 'high' | 'max'
  gridCols: number; gridRows: number
  dedupeEnabled: boolean; unitWidth: number; unitHeight: number
  saveQuality: number; frameQualityFilter: boolean
  imageMode: 'extract' | 'table' | 'digitize' | 'understand'
  userNotes: string
  notesExpanded: boolean
  streamDisplay: boolean
}

const DEFAULTS: GenPrefs = {
  // ⚠️ `plain`（通俗）= 2026-10-05 起的全局默认风格档，与后端各处默认值同源。
  // ⚠️ 但它**只对没有本地存档的用户生效**——`loadGenPrefs()` 末尾是
  // `{ ...DEFAULTS, ...raw }`，已存过 localStorage 的用户，`raw.style` 会覆盖
  // 这个新默认。**刻意不加迁移**：用户当前的 detailed 可能是他显式选的，静默
  // 改掉是替他做决定。想要存量也变，得用户先清 `genPrefs` 或手动选一次。
  style: 'plain', selectedCore: ['notes', 'polished'],
  formatToc: true, formatLink: true, formatSummary: true, formatScreenshot: false, formatChart: false,
  formatFollowUp: false,
  formatTranslate: false, formatFormulas: false, formatReferences: false, formatImages: false,
  imageAnalyze: false, imageOptimize: true,
  ocrEnabled: false, ocrModelId: null,
  selectedModelId: null,
  videoUnderstanding: false, videoInterval: 6,
  videoDirect: false,
  docDirect: false,
  reasoningEffort: 'high',
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
    // 迁移旧风格 key（`meeting_minutes` → `meeting`）。
    // ⚠️ **别名表只有一份**（`constants.ts` 的 `STYLE_ALIAS`，与 `normalizeStyle` 同处）——
    // 此前这里手抄了一遍 `=== 'meeting_minutes'`，新增第二条别名时改一处漏一处，
    // 症状是「旧存档的档位静默不生效」且零报错（模式 #54：同源是搜索题不是记忆题）。
    // ⚠️ 必须带 `typeof` 守卫：老存档可能整个没有 `style` 字段，无条件写回会显式造出
    // `style: undefined`，而末尾 `{ ...DEFAULTS, ...raw }` 的展开**会覆盖**新默认值
    // （症状是「没选过风格的老用户拿不到 plain」）。
    if (typeof raw.style === 'string') {
      raw.style = normalizeStyle(raw.style)
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
