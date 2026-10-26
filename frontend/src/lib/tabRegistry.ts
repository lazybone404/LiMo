/**
 * Tab 注册表 — 纯数据元注册，替代散落在 5 处的 switch/case / 数组 / 映射表。
 *
 * 新增 tab 只需在此注册一个 TabDef（1 处），零改动其他文件。
 *
 * 设计约束：
 * - 纯数据，不 import React 组件，避免循环依赖
 * - 渲染逻辑仍在 PreviewArea.tsx，但由 TabDef 属性驱动而非硬编码分支
 * - visible 回调处理 sourceTypes 表达不了的动态条件
 *
 * ⚠️ 唯一允许的 import 是 `lib/imageModes`（图片模式中文标签的唯一真相源）——
 * 它自身是零 import 的纯数据叶子，不破坏上面的无环约束。
 */

import { IMAGE_MODE_LABELS, type ImageMode } from './imageModes'

// ── Tab 数据定义 ──

/** 内容来源 — 决定内容从哪个字段读（getter 实现见 lib/contentSources.ts） */
export type ContentSource = 'notes' | 'polished' | 'rawText' | 'imageResult' | 'none'

export interface TabDef {
  /** 唯一标识，对应 store.activeView */
  key: string
  /** 显示在 tab 按钮上 */
  label: string
  /** 哪些 sourceType 下显示此 tab（主条件） */
  sourceTypes: string[]
  /**
   * 补充可见性条件（可选）。sourceTypes 表达不了的动态逻辑：
   *   - mindmap: 需要已有 notes 内容
   *   返回 false 时 tab 不出现在 tab 栏中。
   */
  visible?: (ctx: { currentContent: string | null; imageResults: Record<string, any> }) => boolean
  /** 哪个 sourceType 下本 tab 为默认选中 */
  defaultFor?: string[]
  /** tab 排序（越小越靠左） */
  priority: number

  // ── 内容源 ──

  /**
   * 内容来源 — 决定 displayContent 从哪个字段读。
   * - 'notes':     currentContent
   * - 'polished':  currentPolishedContent（同时流式缓冲用 generate_polished）
   * - 'rawText':   currentRawText
   * - 'imageResult': imageResults[docId][activeView].text
   * - 'none':      无内容（原图 tab）
   */
  contentSource: ContentSource

  // ── 导出 ──

  /** HTML 导出 mode 参数。'__mindmap__' 走专用端点。空字符串 = HTML 导出不可用 */
  htmlMode: string
  /** 导出文件名后缀 */
  exportSuffix: string
  /** 导出内容的人类标签（Toast 提示用） */
  exportLabel: string

  // ── 条件 UI ──

  /** 是否显示顶部窄条（`ContentBanner`：标题 + 原链接 + 右侧动作）。
   *  ⚠️ 它只是**必要条件** —— 真正的显示判据在 `PreviewArea.showContentBanner`
   *  （视频照旧，其余类型只在有编辑入口时出现）。 */
  showBanner?: boolean
  /** 是否显示 TOC 目录侧边栏 */
  showTOC?: boolean
  /** 是否显示版本切换器，以及对应哪个 mode 的版本列表 */
  showVersionSwitcher?: 'notes' | 'polished'

  // ── 处理状态门控 ──

  /** 哪些 task.kind 处于活跃状态时，此 tab 显示转圈 */
  processingKinds?: string[]
}

// ── 注册表 ──

class TabRegistryImpl {
  private _tabs: TabDef[] = []

  register(tab: TabDef): void {
    const idx = this._tabs.findIndex(t => t.key === tab.key)
    if (idx >= 0) {
      this._tabs[idx] = tab
    } else {
      this._tabs.push(tab)
    }
  }

  get(key: string): TabDef | undefined {
    return this._tabs.find(t => t.key === key)
  }

  /** 返回指定 sourceType 下所有可见的 tab（按 priority 排序） */
  forSourceType(
    sourceType: string,
    ctx?: { currentContent: string | null; imageResults: Record<string, any> },
  ): TabDef[] {
    return this._tabs
      .filter(t => t.sourceTypes.includes(sourceType))
      .filter(t => !t.visible || (ctx && t.visible(ctx)))
      .sort((a, b) => a.priority - b.priority)
  }

  /** 返回指定 sourceType + hasContent 下的默认 tab key */
  defaultFor(sourceType: string): string | undefined {
    const candidates = this._tabs
      .filter(t => t.defaultFor?.includes(sourceType))
      .sort((a, b) => a.priority - b.priority)
    return candidates[0]?.key
  }

  all(): readonly TabDef[] {
    return this._tabs
  }

  get count(): number {
    return this._tabs.length
  }
}

export const TabRegistry = new TabRegistryImpl()

// ═══════════════════════════════════════════
// 注册 14 个内置 tab
// ═══════════════════════════════════════════

// 优先级规划：
//   1-9:   （已移除 skeleton——epub 一键解读后无章节树面板）
//   10-19: 文档类（summary, polished, transcript, data_preview）
//   20-29: AI 工具（mindmap）
//   30-39: 图片类（original, extract, table, digitize, understand）
//   40+:   其他（ocr）

const DOC_TYPES = ['pdf', 'docx', 'pptx', 'document', 'epub']
const ALL_TEXT = ['video', 'webpage', 'text', 'link', ...DOC_TYPES]

// ── 笔记 ──
// ⚠️ `composite` 只加在这个 tab 与 transcript 上，**绝不能加进 DOC_TYPES / ALL_TEXT**——
// 那两处是 `polished` / `mindmap` 的 sourceTypes 展开源，加进去会让合成文档凭空多出
// 「整理版原文」与「思维导图」（它是 N 篇的综述，没有「原文」可言）。见 M8 测试。
TabRegistry.register({
  key: 'summary', label: '笔记',
  sourceTypes: ['video', 'webpage', 'text', 'link', 'pdf', 'docx', 'pptx', 'document', 'epub', 'xlsx', 'composite'],
  // ⚠️ `defaultFor` 与上面的 `sourceTypes` 是**两件事**：前者决定「生成完自动停在哪个 tab」。
  // 漏掉它 → 用户点完「生成对比综述」停在「转录」tab 上，得自己手动点「笔记」。
  defaultFor: ['video', 'webpage', 'text', 'link', 'epub', 'composite'],
  priority: 10,
  contentSource: 'notes', htmlMode: 'notes', exportSuffix: '', exportLabel: '笔记',
  showBanner: true, showTOC: true, showVersionSwitcher: 'notes',
  processingKinds: ['import', 'retranscribe', 'generate'],
})

// ── 整理版原文 ──
TabRegistry.register({
  key: 'polished', label: '整理版原文',
  sourceTypes: ALL_TEXT,
  defaultFor: ['pdf', 'docx', 'pptx', 'document'],
  priority: 11,
  contentSource: 'polished', htmlMode: 'polished', exportSuffix: '-整理版原文', exportLabel: '整理版原文',
  showBanner: true, showTOC: true, showVersionSwitcher: 'polished',
  processingKinds: ['import', 'retranscribe', 'generate'],
})

// ── 转录 ──
TabRegistry.register({
  key: 'transcript', label: '转录',
  // composite 的「转录」= 合成后的正文本身（含 `## 来源文档` 清单）——用户要能核对
  // 到底选了哪几篇、有没有被截断，所以这个 tab 必须有，且是只读的查看入口
  sourceTypes: ['video', 'webpage', 'text', 'link', 'pdf', 'docx', 'pptx', 'document', 'epub', 'composite'],
  priority: 12,
  contentSource: 'rawText', htmlMode: 'transcript', exportSuffix: '-转录', exportLabel: '转录',
  showBanner: true,
  processingKinds: ['import', 'retranscribe'],
})

// ── 数据预览（Excel） ──
TabRegistry.register({
  key: 'data_preview', label: '数据预览',
  sourceTypes: ['xlsx'],
  defaultFor: ['xlsx'],
  priority: 9,
  contentSource: 'rawText', htmlMode: 'transcript', exportSuffix: '', exportLabel: '数据预览',
  processingKinds: ['import', 'retranscribe', 'generate'],
})

// ── 思维导图 ──
TabRegistry.register({
  key: 'mindmap', label: '思维导图',
  sourceTypes: ALL_TEXT,
  priority: 21,
  contentSource: 'notes', htmlMode: '__mindmap__', exportSuffix: '', exportLabel: '思维导图',
  visible: (ctx) => !!ctx.currentContent,
  processingKinds: ['import', 'retranscribe', 'generate'],
})

// ── 图片类 ──
TabRegistry.register({
  key: 'original', label: '原图',
  sourceTypes: ['image'],
  defaultFor: ['image'],
  priority: 30,
  contentSource: 'none', htmlMode: '', exportSuffix: '', exportLabel: '原图',
  processingKinds: ['import'],
})

// 图片处理 tab（extract/table/digitize/understand）共享属性。
// ⚠️ label / suffix 都从 `lib/imageModes` 取（单一真相源）：suffix 是
// **`-` + label** 的派生式，手抄时改一处漏一处会让导出文件名与 tab 名对不上。
const IMG_TABS: Array<{ key: ImageMode; priority: number }> = [
  { key: 'extract', priority: 31 },
  { key: 'table', priority: 32 },
  { key: 'digitize', priority: 33 },
  { key: 'understand', priority: 34 },
]
for (const t of IMG_TABS) {
  const label = IMAGE_MODE_LABELS[t.key]
  TabRegistry.register({
    key: t.key, label,
    sourceTypes: ['image'],
    priority: t.priority,
    contentSource: 'imageResult', htmlMode: t.key, exportSuffix: `-${label}`, exportLabel: label,
    showBanner: true,
    processingKinds: ['import', 'process'],
  })
}

// ── OCR ──
TabRegistry.register({
  key: 'ocr', label: 'OCR',
  sourceTypes: ['image'],
  priority: 35,
  contentSource: 'none', htmlMode: '', exportSuffix: '', exportLabel: 'OCR',
  processingKinds: ['import'],
})
