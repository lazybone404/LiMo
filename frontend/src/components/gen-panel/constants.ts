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
  /** 多篇合成的对比综述（sourceType === 'composite'）—— 唯一带「来源文档」的类型 */
  isComposite: boolean
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
  // ⚠️ composite **不在** `_ALL_DOC_TYPES` 里（它是二次产物，不是文档解析类型）——
  // 故必须显式给分支。不给的话会落到最末的默认支：显示「整理版原文」选项，
  // 而合成文档根本没有「原文」可整理（它的 rawText 本就是综述素材）。
  // ⚠️ 这也意味着 `isDoc === false`：GenPanel 里所有以 `isDoc` 为门控的块
  // （文档引擎选择、EPUB 解读等）对 composite 自动不渲染，不需要额外排除。
  const isComposite = st === 'composite'

  // tabs 从 TabRegistry 自动生成 — 零硬编码，新增 tab 只需注册 TabDef
  const tabs = TabRegistry.forSourceType(st).map(t => ({ key: t.key, label: t.label }))

  return {
    sourceType: st,
    isVideo, isDoc, isPDF, isImage, isExcel, isMedia, isComposite,
    hasPolish: !isExcel && !isImage && !isComposite,
    hasVL: isVideo,
    hasImages: st === 'pdf' || st === 'docx' || st === 'pptx' || st === 'epub',
    coreProducts: isImage ? [] : isExcel ? [
      { name: 'both', label: '数据摘要', description: '概况·关键指标·字段解读' },
    ] : isComposite ? [
      // 只有笔记（= 对比综述）。⚠️ 后端 `build_both()` 对 composite 返回 (sp, sp)，
      // 前端不给 polished 入口，两层一致
      { name: 'notes', label: '对比综述', description: '总表·分维度对比·共识与分歧·综合评价' },
    ] : [
      { name: 'notes', label: '笔记', description: '结构化笔记：提取关键信息' },
      { name: 'polished', label: '整理版原文', description: '去噪音、修正错别字、合并段落' },
    ],
    generateLabel: isExcel ? '生成数据摘要' : isComposite ? '生成对比综述' : '生成',
    // ⚠️ 与后端**同源兜底**（`generation/prompt.py`：`AVAILABLE_OPTIONS.get(st,
    // AVAILABLE_OPTIONS.get("text", []))`）。此前前端只 `genOptions[st] || []`，缺这一层
    // 兜底 —— 空表本身无害，但它是**门控判据**（见 isFormatVisible）：为空 = 该类型下
    // 所有格式项连渲染都不渲染，而后端其实支持。
    availableFormats: genOptions[st] ?? genOptions['text'] ?? [],
    tabs,
  }
}

/**
 * 笔记格式项在当前类型下是否可见 —— **单一真相源**。
 *
 * `GenerateOptions` 的渲染条件与 `GenPanel` 提交前的格式校验此前各写一份判据
 * （前者按 `isPDF`/`isVideo`/`hasImages` 硬编码，后者注释自称「镜像 GenerateOptions
 * 显示条件」）。两处漂移的失效方式很隐蔽：UI 显示一个后端能力表不认的项 = 用户勾了、
 * 提交时被 `GenPanel` 的 `.filter(f => availableFormats.includes(f))` 静默抹掉。
 *
 * 判据 = 后端下发的能力表（`AVAILABLE_OPTIONS`），前端不另存一份；
 * 仅 `images` 额外受「这类文档有图片」约束 —— 能力表说这类**能**提取图片，
 * 但这篇文档未必有（`hasImages` 按 sourceType 判，与后端键集刻意分开）。
 */
export function isFormatVisible(fmt: string, m: TypeMeta): boolean {
  if (!m.availableFormats.includes(fmt)) return false
  if (fmt === 'images') return m.hasImages
  return true
}

/** 风格档位的全局默认值（2026-10-05 起 = 通俗）。改它要同步后端 8 处，见 CLAUDE.md。 */
export const DEFAULT_STYLE = 'plain'

/**
 * 切换文档类型后应选中的风格档 —— **条件重置**：只在本档对新类型不合法时才动它。
 *
 * 两种写错的方式各有症状，且都不报错：
 *   - **无条件重置**（`return DEFAULT_STYLE`）→ 冲掉用户在两类之间**共享的合法档**
 *     （`plain`/`detailed`/`academic`/`business` 在视频与文档两套里都有），
 *     表现为「我只是换了个文档，刚选的风格没了」；
 *   - **完全不重置** → 带着一个本类型表里没有的 key 去生成，后端
 *     `lookup.get(style, "")` 静默返回空串，产物无风格且两侧零报错（P0-02）。
 *
 * `none`（不套风格）在任何类型下都合法，故永远保持。
 */
export function reconcileStyle(style: string, styleKeys: string[]): string {
  return new Set(['none', ...styleKeys]).has(style) ? style : DEFAULT_STYLE
}

/** `reconcileStyle` 的可用集从哪来 —— 决定 caps 里读哪一张表 */
export type StyleScope = 'excel' | 'doc' | 'media'

export interface CapsStyleTables {
  styles?: string[]
  docStyles?: string[]
  excelStyles?: string[]
}

/**
 * 取当前类型的风格表。**返回 `undefined` 表示 caps 未到位**（后端未起 / 旧后端）——
 * 这个 `undefined` 是有意义的信号，**不要在这里兜一张固定的表**。
 *
 * ⚠️ 此处曾有三组各 5 项的硬编码 fallback，与后端真表**漂移**（后端 video 10 档 /
 * doc 9 档，前端各写 5 档且顺序不同）。兜底表最坏的地方不是「少」，而是它
 * **看起来像完整表**——用户会以为「就这几种风格」，而后端支持更多，且往后越差越远。
 * 调用方拿到 `undefined` 后应当：只渲染默认档（`styleOptions`），
 * 且**不拿它做合法性判定**（`GenPanel` 的 effect 会早退）。
 */
export function pickStyleKeys(
  caps: CapsStyleTables | null | undefined,
  scope: StyleScope,
): string[] | undefined {
  if (!caps) return undefined
  const keys = scope === 'excel' ? caps.excelStyles
    : scope === 'doc' ? caps.docStyles
    : caps.styles
  return keys?.length ? keys : undefined
}

/**
 * 按钮组的选项列表 = 「不套风格」+ 当前可用档。
 * caps 未到位时退化为 `['none', DEFAULT_STYLE]`：**渲染得出选中态**即可，
 * 不假装有一张完整表（见 `pickStyleKeys` 的说明）。
 */
export function styleOptions(styleKeys: string[] | undefined): string[] {
  return ['none', ...(styleKeys?.length ? styleKeys : [DEFAULT_STYLE])]
}

/**
 * 风格键的历史别名 —— **前端唯一一份**，`normalizeStyle` 的所有调用方都走这里
 * （生成提交 `GenPanel.tsx` / 选项渲染 `GenerateOptions.tsx` / 存档迁移 `genPrefs.ts`）。
 *
 * ⚠️ 与后端 `generation/prompts/notes_styles.py::STYLE_ALIAS` 是**成对**的同一张表
 * （模式 #52）：前端归一漏一条 = 该档位静默不生效；后端漏一条 = 生成产物无风格。
 * 两侧都必须「写入前归一 + 读取时归一」，只做一半与完全没做一样。
 */
const STYLE_ALIAS: Record<string, string> = {
  meeting_minutes: 'meeting',
}

export function normalizeStyle(s: string): string {
  return STYLE_ALIAS[s] || s
}
