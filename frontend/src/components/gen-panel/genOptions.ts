/**
 * 生成参数的拼装 —— GenPanel 与批量生成共用这一份。
 *
 * 抽出来的理由是模式 #54：`handleGenerate` 里那张参数表有二十多个字段，每一项都带
 * 条件（视频专属项只在 `isVideo` 时传、`ocr_*` 在视频下不传、`doc_direct` 要同时
 * 满足三个条件……）。批量生成若抄一份，两边迟早分叉，而分叉的表现是「同一篇文档，
 * 单篇生成与批量生成产出的东西不一样」，界面上完全看不出来。
 *
 * ⚠️ **能力位判据也只此一份**。四个能力位（image / video / reasoning / document）
 * 的形状统一为「选中模型就查它，没选中就看任一启用模型」，此前在 GenPanel 里抄了
 * 四遍；批量生成需要第五遍时把它收口到这里。
 */
import type { EnabledModel } from '../../api/client'
import type { DocGenOptions, VideoGenOptions } from '../../api/generate'
import { normalizeStyle, type TypeMeta } from './constants'
import type { GenPrefs } from './genPrefs'

/** 格式勾选项的提交顺序。顺序本身有意义：后端按位解析，改序会让日志里的参数串对不上。 */
function formatEntries(prefs: GenPrefs): { fmt: string; on: boolean }[] {
  return [
    { fmt: 'toc', on: prefs.formatToc },
    { fmt: 'link', on: prefs.formatLink },
    { fmt: 'screenshot', on: prefs.formatScreenshot },
    { fmt: 'summary', on: prefs.formatSummary },
    { fmt: 'chart', on: prefs.formatChart },
    { fmt: 'followup', on: prefs.formatFollowUp },
    { fmt: 'formulas', on: prefs.formatFormulas },
    { fmt: 'references', on: prefs.formatReferences },
    { fmt: 'images', on: prefs.formatImages },
  ]
}

export interface FormatPick {
  /** 逗号分隔的格式串。空串表示这一篇一个都不传，调用方转成 `undefined`。 */
  formats: string
  /**
   * 用户勾了、但当前类型的能力表不认的项。
   *
   * `genPrefs` 是跨类型持久化的，所以「在 PDF 上勾过『保留文档图片』→ 切到文本」
   * 会把这一项带进来。它只在开发期有意义（该类型本就无处可勾，用户没有可采取的
   * 动作），GenPanel 拿它打一条 `console.warn`，不上 toast。
   */
  dropped: string[]
}

export function pickFormats(prefs: GenPrefs, typeMeta: TypeMeta): FormatPick {
  const kept: string[] = []
  const dropped: string[] = []
  for (const { fmt, on } of formatEntries(prefs)) {
    if (!on) continue
    if (typeMeta.availableFormats.includes(fmt)) kept.push(fmt)
    else dropped.push(fmt)
  }
  return { formats: kept.join(','), dropped }
}

/** 产物列表参数："notes,polished" / "notes" / "polished"。 */
export function buildStreamMode(prefs: GenPrefs): string {
  return prefs.selectedCore.join(',')
}

export type ModelCapability = 'image' | 'video' | 'reasoning' | 'document'

/**
 * 模型能力位判据（四个能力位共用）。
 *
 * 选中了具体模型 → 只看它；没选中（走「场景默认」）→ 看任一启用模型是否支持。
 * 后半句不是宽松处理：默认模型可能是 deepseek（支持 reasoning），此时若按「没选中
 * = 不支持」判，用户会看不到思考档位，而服务端照样会用它。
 */
export function supportsCapability(
  models: EnabledModel[],
  selectedModelId: number | null,
  cap: ModelCapability,
): boolean {
  if (selectedModelId) {
    return models.find(m => m.id === selectedModelId)?.capabilities?.includes(cap) ?? false
  }
  return models.some(m => m.capabilities?.includes(cap))
}

export interface GenOptionsContext {
  /** 这一篇的类型元数据（`getTypeMeta(sourceType, capabilities.genOptions)`） */
  typeMeta: TypeMeta
  /** 思考档位是否下发 —— 下调用的 `supportsCapability(models, id, 'reasoning')` */
  reasoningSupported: boolean
  /** PDF 直传的能力门 —— 下调用的 `supportsCapability(models, id, 'document')` */
  hasDocumentModel: boolean
  /** 参考文件暂存 id。仅会话内有效，故不进 `GenPrefs`。 */
  referenceIds?: string[]
  /** `'partial'` = 续写最新残缺产物（只有单篇的「继续生成」会传） */
  continueFrom?: '' | 'partial'
}

/**
 * 一次生成请求的全部参数。
 *
 * 类型写成两个具体形状的交集，而不是 `GenOptions`（那个是**联合**）：本函数的返回值
 * 同时可能带视频项与文档项（各自按条件取 `undefined`），联合类型上取不到对方的字段。
 * 两侧都是它的超集，故交给 `streamGenerate` 时仍然成立。
 */
export type BuiltGenOptions =
  & VideoGenOptions
  & DocGenOptions
  & { continue_from?: string }

/**
 * 拼装一次生成请求的全部参数。
 *
 * 每条 `xxx: cond ? value : undefined` 里的 `undefined` 是「不传」，不是「传空」——
 * `streamGenerate` 用 `!== undefined` 判，`undefined` 才会被整条跳过。
 */
export function buildGenOptions(
  prefs: GenPrefs,
  ctx: GenOptionsContext,
): BuiltGenOptions {
  const isVideo = ctx.typeMeta.isVideo
  // 视频不走 OCR（画面由视觉模型理解），故视频下这两个字段一律不传，而不是传真值。
  const ocrEnabled = !isVideo && prefs.ocrEnabled
  return {
    mode: buildStreamMode(prefs),
    style: normalizeStyle(prefs.style),
    formats: pickFormats(prefs, ctx.typeMeta).formats || undefined,
    model_id: prefs.selectedModelId || undefined,
    // 不支持思考档位还传，只会让后端每次生成记一条「档位未生效」warning
    reasoning_effort: ctx.reasoningSupported ? prefs.reasoningEffort : undefined,
    user_notes: prefs.userNotes || undefined,
    reference_ids: ctx.referenceIds?.length ? ctx.referenceIds : undefined,
    continue_from: ctx.continueFrom || undefined,

    video_understanding: isVideo ? prefs.videoUnderstanding : undefined,
    video_direct: isVideo && prefs.videoUnderstanding ? (prefs.videoDirect || undefined) : undefined,
    video_interval: isVideo ? prefs.videoInterval : undefined,
    grid_cols: isVideo ? prefs.gridCols : undefined,
    grid_rows: isVideo ? prefs.gridRows : undefined,
    dedupe_enabled: isVideo ? prefs.dedupeEnabled : undefined,
    unit_width: isVideo ? prefs.unitWidth : undefined,
    unit_height: isVideo ? prefs.unitHeight : undefined,
    save_quality: isVideo ? prefs.saveQuality : undefined,
    frame_quality_filter: isVideo ? prefs.frameQualityFilter : undefined,

    image_analyze: isVideo ? prefs.videoUnderstanding : (prefs.imageAnalyze || undefined),
    image_optimize: isVideo
      ? (prefs.videoUnderstanding ? prefs.imageOptimize : undefined)
      : (prefs.imageAnalyze ? prefs.imageOptimize : undefined),
    ocr_enabled: ocrEnabled || undefined,
    ocr_model_id: ocrEnabled ? prefs.ocrModelId ?? undefined : undefined,

    // 仅 PDF 且模型支持时传（false 一律转 undefined = 不传 = 后端默认文本模式）
    doc_direct: ctx.typeMeta.isPDF && ctx.hasDocumentModel && prefs.docDirect ? true : undefined,
    // 联网检索只注 notes，整理版单独生成时传了也没用 —— 由后端 `stream_mode != "polished"`
    // 拦住；这里不特判，用户切回 both/notes 时意图原样保留。
    web_search: prefs.webSearch ? true : undefined,
  }
}
