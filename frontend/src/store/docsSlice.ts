/**
 * 文档库 slice — docs / folders / 选中 / 分页 / 筛选条件。
 *
 * ⚠️ 类型筛选与标签筛选是**两组独立条件**（可同时生效，AND）：
 * - activeType：来源类型（video/pdf/docx…），对应顶部类型行
 * - activeTags：用户自定义标签（多选，AND），对应标签行
 * 曾把二者混在一个 `activeTag` 里（按钮写着「标签筛选」实际过滤的却是 sourceType），
 * 用户给自己打的标签根本无法用来筛选。
 *
 * ⚠️ `selectedIds` / `listLimit` / `folderBusy` 必须放在 store，不能留在组件本地：
 * 左侧面板折叠会**卸载组件**（SlotLayout 是 `collapsed ? <PanelStrip/> : <Comp/>`），
 * 留在本地等于「收起再展开，勾选、滚动深度、禁用态全部归零」。
 *
 * ⚠️ 这里**没有** `selectMode` 这类「批量模式」开关：复选框常驻，批量条由
 * `selectedIds.size > 0` 派生。载体不存在，就不可能与选择集漂移。
 */
import type { StateCreator } from 'zustand'
import type { Store, DocEntry, FolderEntry } from './types'

/** 首次渲染的行数；到底部哨兵可见时每次追加这么多（组件与 store 共用同一常量来源） */
export const PAGE_SIZE = 60

/**
 * 「清空选择」的返回值 —— 各筛选 setter 与 clearSelection 共用。
 *
 * ⚠️ 做成**函数**而不是共享常量：共享的 Set 对象一旦被谁就地 `add` 一次就永久污染
 * 全局（`set` 只做浅合并，不会克隆）。
 */
const noSelection = (): { selectedIds: Set<string> } => ({ selectedIds: new Set() })

export interface DocsSlice {
  docs: DocEntry[]
  folders: FolderEntry[]
  currentDocId: string | null
  searchQuery: string
  activeType: string
  activeTags: string[]
  /** 当前文件夹筛选：null = 全部；'' = 未分类；其他 = 文件夹 id。
   *  ⚠️ 三态必须区分开——空串是有效的「未分类」值，不能用 null 代替。 */
  activeFolderId: string | null
  /**
   * 标题关键词 —— **纯内存过滤，不触发 FTS**。这是它与搜索框的分工：
   * 搜索框「找内容」（打 `/api/search`），这一项「筛标题」（只在 `docs` 上跑）。
   */
  titleFilter: string
  /** 日期范围（`YYYY-MM-DD`，闭区间）。null = 不限；无 createdAt 的文档在设了范围时被排除 */
  dateFrom: string | null
  dateTo: string | null

  // ── 多选（常态，无模式开关）──
  selectedIds: Set<string>
  setSelectedIds: (ids: Set<string>) => void
  clearSelection: () => void

  // ── 面板状态 ──
  /** 分批渲染深度（已滚到多少条） */
  listLimit: number
  setListLimit: (n: number) => void
  /** 正在进行增删改的文件夹 id；`'create'` 表示正在新建 */
  folderBusy: Set<string>
  setFolderBusy: (ids: Set<string>) => void
  /** 文档列表首次加载中 —— 用于区分「还没加载」与「真的没有文档」 */
  docsLoading: boolean
  setDocsLoading: (v: boolean) => void

  setDocs: (docs: DocEntry[]) => void
  setFolders: (folders: FolderEntry[]) => void
  addDoc: (doc: DocEntry) => void
  /** ⚠️ 传 null 表示「没有选中任何文档」。空串 `''` 不是有效 id，不要再用它当哨兵。 */
  selectDoc: (id: string | null) => void
  setSearchQuery: (q: string) => void
  setActiveType: (t: string) => void
  setActiveTags: (tags: string[]) => void
  setActiveFolderId: (id: string | null) => void
  setTitleFilter: (v: string) => void
  setDateRange: (from: string | null, to: string | null) => void
}

export const createDocsSlice: StateCreator<Store, [], [], DocsSlice> = (set) => ({
  docs: [], folders: [], currentDocId: null,
  searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
  titleFilter: '', dateFrom: null, dateTo: null,

  selectedIds: new Set(),
  setSelectedIds: (ids) => set({ selectedIds: ids }),
  clearSelection: () => set(noSelection()),

  listLimit: PAGE_SIZE,
  setListLimit: (n) => set({ listLimit: n }),
  folderBusy: new Set(),
  setFolderBusy: (ids) => set({ folderBusy: ids }),
  docsLoading: false,
  setDocsLoading: (v) => set({ docsLoading: v }),

  setDocs: (docs) => set({ docs }),
  setFolders: (folders) => set({ folders }),
  addDoc: (doc) => set(s => ({ docs: [doc, ...s.docs] })),
  selectDoc: (id) => {
    try {
      // ⚠️ 只在真值时才写：空串不是有效 id，写进去会让下次启动拿它去 restore，
      // 而 `docs.some(d => d.id === '')` 恒为 false —— 白写一次还留个垃圾值。
      if (id) localStorage.setItem('mbw_lastDocId', id)
      else localStorage.removeItem('mbw_lastDocId')
    } catch { /* quota exceeded */ }
    set({ currentDocId: id })
  },

  // ⚠️ **每一个**筛选 setter 都和「清空选择」在同一次 `set` 里发生（而不是各自 set 之后
  // 再靠组件 effect 清）：effect 要等渲染提交后才跑，中间那一帧里用户看到的是
  // 「勾选还在、但被勾的文档已经不在列表里」——此刻任何批量操作都作用在看不见的对象上。
  // 新增筛选维度时，这条必须一起复制过去（漏一个 = 那一维度独有一个时序窗口）。
  setSearchQuery: (q) => set({ ...noSelection(), searchQuery: q }),
  setActiveType: (t) => set({ ...noSelection(), activeType: t }),
  setActiveTags: (tags) => set({ ...noSelection(), activeTags: tags }),
  setActiveFolderId: (id) => set({ ...noSelection(), activeFolderId: id }),
  setTitleFilter: (v) => set({ ...noSelection(), titleFilter: v }),
  // 起止一次设完：快捷项（本周/本月）必须同时落两值，分两次 set 会各清一次选择，
  // 且中间那一帧的范围是「新起点 + 旧终点」——一个用户从没选过的区间。
  setDateRange: (from, to) => set({ ...noSelection(), dateFrom: from, dateTo: to }),
})
