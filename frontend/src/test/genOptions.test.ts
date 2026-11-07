/**
 * `genOptions.ts` —— 单篇生成与批量生成共用的参数拼装。
 *
 * 抽这个模块的理由是模式 #54：`handleGenerate` 那张参数表二十多个字段，每项都带
 * 条件。两份实现的分叉不会报错，只会让「同一篇文档，单篇生成与批量生成产出的东西
 * 不一样」——而这恰恰是这个模块唯一的价值，所以本文件全部钉在**取值形状**上。
 *
 * 对照实验（证明有区分力）：
 *   A. 删掉 `ocr_enabled` 的 `!isVideo` 条件（视频下也传）→ 第 2 条红
 *   B. 把 `pickFormats` 的 `typeMeta.availableFormats.includes` 去掉（全收）
 *      → 第 4 条红（dropped 为空、dropped 的项又出现在 formats 里）
 *   C. 把 `supportsCapability` 的「未选中看任一模型」那支删掉、恒返回 false
 *      → 第 5 条红（默认模型支持 reasoning 时档位被吞）
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  buildGenOptions, buildStreamMode, pickFormats, supportsCapability,
} from '../components/gen-panel/genOptions'
import { getTypeMeta } from '../components/gen-panel/constants'
import { loadGenPrefs, saveGenPrefs } from '../components/gen-panel/genPrefs'
import type { EnabledModel } from '../api/client'

/** 后端 `AVAILABLE_OPTIONS` 的一个小切片 —— 只给本文件用到的两个类型。 */
const GEN_OPTIONS: Record<string, string[]> = {
  video: ['toc', 'link', 'screenshot', 'summary', 'chart', 'followup'],
  pdf: ['toc', 'formulas', 'references', 'summary', 'images', 'chart', 'followup'],
  text: ['toc', 'summary', 'formulas', 'chart', 'followup'],
}

const ctxOf = (st: string, extra: Partial<Parameters<typeof buildGenOptions>[1]> = {}) => ({
  typeMeta: getTypeMeta(st, GEN_OPTIONS),
  reasoningSupported: true,
  hasDocumentModel: true,
  ...extra,
})

beforeEach(() => {
  localStorage.clear()
})

describe('buildGenOptions — 视频专属项', () => {
  it('非视频：视频专属字段一个都不出现，image_analyze 回落普通开关', () => {
    const prefs = { ...loadGenPrefs(), imageAnalyze: true, videoUnderstanding: true }
    const o = buildGenOptions(prefs, ctxOf('pdf'))
    for (const k of ['video_understanding', 'video_direct', 'video_interval', 'grid_cols',
      'grid_rows', 'dedupe_enabled', 'unit_width', 'unit_height', 'save_quality',
      'frame_quality_filter'] as const) {
      expect(o[k], `非视频不该带 ${k}`).toBeUndefined()
    }
    // 非视频下 `image_analyze` 只看普通开关，不受 `videoUnderstanding` 影响
    expect(o.image_analyze).toBe(true)
  })

  it('视频：ocr_* 一律不传（画面交视觉模型），视频项按开关下发', () => {
    const prefs = {
      ...loadGenPrefs(), ocrEnabled: true, ocrModelId: 7,
      videoUnderstanding: true, videoDirect: true, videoInterval: 10,
    }
    const o = buildGenOptions(prefs, ctxOf('video'))
    // ⚠️ 是「不传」而不是「传真值」：传了后端会按 OCR 预处理走一遍，白花一次调用
    expect(o.ocr_enabled).toBeUndefined()
    expect(o.ocr_model_id).toBeUndefined()
    expect(o.video_understanding).toBe(true)
    expect(o.video_direct).toBe(true)
    expect(o.video_interval).toBe(10)
    // 视频下 `image_analyze` 整体跟随「画面理解」，不再看普通图片开关
    expect(o.image_analyze).toBe(true)
  })

  it('视频未开画面理解时，直传与图片优化都不传（避免传一个孤立的 true）', () => {
    const prefs = {
      ...loadGenPrefs(), videoUnderstanding: false, videoDirect: true, imageOptimize: true,
    }
    const o = buildGenOptions(prefs, ctxOf('video'))
    expect(o.video_direct).toBeUndefined()
    expect(o.image_optimize).toBeUndefined()
  })
})

describe('buildGenOptions — 能力位', () => {
  it('模型不支持思考档位时不下发（否则每次生成一条「档位未生效」warning）', () => {
    const prefs = loadGenPrefs()
    expect(buildGenOptions(prefs, ctxOf('pdf', { reasoningSupported: false })).reasoning_effort)
      .toBeUndefined()
    expect(buildGenOptions(prefs, ctxOf('pdf', { reasoningSupported: true })).reasoning_effort)
      .toBe('high')
  })

  it('doc_direct 要同时满足 是 PDF + 模型支持 + 用户勾选', () => {
    const on = { ...loadGenPrefs(), docDirect: true }
    expect(buildGenOptions(on, ctxOf('pdf')).doc_direct).toBe(true)
    expect(buildGenOptions(on, ctxOf('pdf', { hasDocumentModel: false })).doc_direct).toBeUndefined()
    expect(buildGenOptions(on, ctxOf('docx')).doc_direct).toBeUndefined()
    expect(buildGenOptions({ ...loadGenPrefs(), docDirect: false }, ctxOf('pdf')).doc_direct)
      .toBeUndefined()
  })

  it('supportsCapability：选中模型看它自己，未选中看任一启用模型', () => {
    const models = [
      { id: 1, capabilities: ['image'] },
      { id: 2, capabilities: ['reasoning'] },
    ] as EnabledModel[]
    expect(supportsCapability(models, 1, 'image')).toBe(true)
    expect(supportsCapability(models, 1, 'reasoning')).toBe(false)
    expect(supportsCapability(models, null, 'reasoning')).toBe(true)
    expect(supportsCapability(models, null, 'document')).toBe(false)
    // 选了一个不存在的 id：按「它不支持」处理，不回退到「任一模型」
    expect(supportsCapability(models, 99, 'image')).toBe(false)
  })
})

describe('pickFormats — 能力表是唯一判据', () => {
  it('只留下本类型能力表认的项，被丢的逐项点名', () => {
    const prefs = {
      ...loadGenPrefs(),
      formatImages: true, formatSummary: true, formatToc: true,
    }
    // ⚠️ `loadGenPrefs()` 的默认值里 `formatLink` 是开的（时间戳链接只对视频有意义），
    // 而 pdf / text 的能力表都没有 `link` —— 于是它默认就会被丢掉。这是既有行为，
    // 不是本模块新引入的：`dropped` 只用于开发期一条 `console.warn`。
    const pdf = pickFormats(prefs, getTypeMeta('pdf', GEN_OPTIONS))
    expect(pdf.formats).toBe('toc,summary,images')
    expect(pdf.dropped).toEqual(['link'])

    // 切到文本：`images` 也不在能力表里 → 一并丢掉，两项都点名
    const text = pickFormats(prefs, getTypeMeta('text', GEN_OPTIONS))
    expect(text.formats).toBe('toc,summary')
    expect(text.dropped).toEqual(['link', 'images'])

    // 对照：在视频上 `link` 是能力表认的，dropped 里就不该出现它
    const video = pickFormats(prefs, getTypeMeta('video', GEN_OPTIONS))
    expect(video.dropped).toEqual(['images'])
    expect(video.formats).toBe('toc,link,summary')
  })

  it('一个都不勾时 formats 是空串（调用方据此转成 undefined = 不传）', () => {
    const prefs = {
      ...loadGenPrefs(),
      formatToc: false, formatLink: false, formatScreenshot: false, formatSummary: false,
      formatChart: false, formatFollowUp: false, formatFormulas: false,
      formatReferences: false, formatImages: false,
    }
    expect(pickFormats(prefs, getTypeMeta('pdf', GEN_OPTIONS)).formats).toBe('')
    expect(buildGenOptions(prefs, ctxOf('pdf')).formats).toBeUndefined()
  })

  it('提交顺序稳定（后端按位解析，改序会让日志里的参数串对不上）', () => {
    // 故意按「与规范顺序相反」的顺序打开，断言输出仍按 formatEntries 的顺序
    const prefs = {
      ...loadGenPrefs(),
      formatFollowUp: true, formatChart: true, formatSummary: true, formatToc: true,
    }
    expect(pickFormats(prefs, getTypeMeta('text', GEN_OPTIONS)).formats)
      .toBe('toc,summary,chart,followup')
  })
})

describe('buildStreamMode 与空值纪律', () => {
  it('产物列表按勾选顺序拼串', () => {
    expect(buildStreamMode({ ...loadGenPrefs(), selectedCore: ['notes', 'polished'] }))
      .toBe('notes,polished')
    expect(buildStreamMode({ ...loadGenPrefs(), selectedCore: ['notes'] })).toBe('notes')
  })

  it('空 userNotes / 空 referenceIds / 无 continueFrom 一律不传', () => {
    const o = buildGenOptions(
      { ...loadGenPrefs(), userNotes: '', selectedModelId: null, webSearch: false },
      ctxOf('pdf'))
    expect(o.user_notes).toBeUndefined()
    expect(o.reference_ids).toBeUndefined()
    expect(o.continue_from).toBeUndefined()
    expect(o.model_id).toBeUndefined()
    expect(o.web_search).toBeUndefined()
  })

  it('续写只有单篇「继续生成」会带，且只认 partial', () => {
    const o = buildGenOptions(loadGenPrefs(), ctxOf('pdf', { continueFrom: 'partial' }))
    expect(o.continue_from).toBe('partial')
  })
})

describe('与「记住风格」的关系（genPrefs 是入参，本模块不读盘）', () => {
  it('同一个 prefs 交给两条调用链，产出逐字段相同', () => {
    // 单篇与批量拿到的都是 `loadGenPrefs()` 那一份；这里模拟两处各自读一次盘，
    // 断言拼装结果一致 —— 分叉只可能来自拼装层，不可能来自偏好本身。
    saveGenPrefs({ style: 'detailed', formatChart: true, webSearch: true })
    const a = loadGenPrefs()
    const b = loadGenPrefs()
    expect(buildGenOptions(a, ctxOf('text')))
      .toEqual(buildGenOptions(b, ctxOf('text')))
  })
})
