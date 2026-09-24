/**
 * 文档库偏好 — 排序 / 密度 / 排列方式 / 筛选区布局（localStorage，同 genPrefs 的手写模式）。
 *
 * ⚠️ 只持久化「用户显式选过」的**呈现偏好**。筛选条件（搜索词/类型/标签/文件夹/标题/日期）
 * **不持久化**：下次打开应用时若还带着上次的筛选，用户会以为「文档库空了」——那是最难自查的一类故障。
 * 分界线是「这一项会不会让用户看到一份不完整的列表」：密度、排列方式、筛选区开合都不会。
 *
 * ⚠️ `expandedSections` 存的是**展开**的小节（而非收起的）：这样**新增的小节默认收起**。
 * 反过来存「收起的」时，以后每加一节都会默认撑开筛选区，而用户不知道是新功能干的。
 */
import type { SortKey, GroupBy } from './useDocQuery'

/** 行密度：detailed = 现有卡片；compact = 30px 单行 */
export type Density = 'detailed' | 'compact'

export interface DocLibraryPrefs {
  /**
   * 结构版本号 —— 目前**只写不判**：浅合并 `{...DEFAULTS, ...raw}` 已能向后兼容
   * （旧值缺字段 = 取默认），它只是将来「某个字段语义变了需要作废旧值时」的锚点。
   */
  version: number
  sort: SortKey
  density: Density
  groupBy: GroupBy
  /** 展开的筛选小节 id（见 SECTION_IDS）。存「展开的」→ 新增小节默认收起 */
  expandedSections: string[]
  /** 整个筛选区是否收起（一级折叠） */
  filterCollapsed: boolean
  /**
   * 被**收起**的列表分组 key（存「收起的」→ 新出现的组默认展开，符合预期）。
   *
   * ⚠️ 与 `expandedSections` 的存法**刻意相反**：筛选小节是新功能、默认该收起以免撑开面板；
   * 而分组头是新数据（新建的文件夹、刚出现的类型），用户会预期它就在那儿、看得见。
   * 两者各自的默认值，决定了各自该存哪一侧。
   */
  collapsedGroups: string[]
  /** 筛选区高度（px）—— 分隔条拖出来的配比，列表区吃掉剩余空间 */
  filterAreaHeight: number
}

export const PREFS_VERSION = 1

/**
 * 筛选小节的 id —— 顺序即渲染顺序（按使用频次排：文件夹范围 → 类型 → 标签 → 标题 → 日期）。
 * 放这里而不是 filters.tsx，是为了与下面的 `expandedSections` 默认值**同源**：
 * 「有哪些小节」和「默认展开哪个」分开写，早晚会有一边漏改。
 */
export const SECTION_IDS = ['folder', 'type', 'tag', 'title', 'date'] as const
export type SectionId = typeof SECTION_IDS[number]

/** 筛选区默认高度 —— 双击分隔条复位到它（拖到极端值后不必手动找回来） */
export const DEFAULT_FILTER_AREA_HEIGHT = 176

const DEFAULTS: DocLibraryPrefs = {
  version: PREFS_VERSION,
  sort: 'date',
  density: 'detailed',
  groupBy: 'folder',
  expandedSections: ['folder'],
  filterCollapsed: false,
  collapsedGroups: [],
  filterAreaHeight: DEFAULT_FILTER_AREA_HEIGHT,
}

/** 分隔条允许的高度区间 —— 与 store 无关，纯布局约束 */
export const FILTER_AREA_MIN = 0
export const FILTER_AREA_MAX = 420

const KEY = 'mbw_docLibPrefs'

export function loadDocLibraryPrefs(): DocLibraryPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}')
    return { ...DEFAULTS, ...raw }
  } catch {
    return { ...DEFAULTS }
  }
}

export function saveDocLibraryPrefs(prefs: Partial<DocLibraryPrefs>) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...loadDocLibraryPrefs(), ...prefs }))
  } catch {
    // 配额超限 / 隐私模式：静默失败（偏好丢失不影响功能）
  }
}
