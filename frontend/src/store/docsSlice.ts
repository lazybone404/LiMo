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
 * 一个文档标签。
 *
 * ⚠️ **不存 title**：渲染时从 `docs` 现取 —— 重命名自动生效，也不会有快照过期问题。
 * ⚠️ 这也是「文档之间的标签」而非预览区已有的「内容视图 tab」（笔记/整理版/转录…，
 * 由 `lib/tabRegistry.ts` + `activeView` 驱动）。两者正交，命名一律用 `docTab`。
 */
export interface DocTab {
  docId: string
  /** `false` = 预览标签（标题斜体、可被下一次单击替换）。**全组至多一个 false** */
  pinned: boolean
}

/** 待提交的关闭意图。`null` = 没有关闭在进行中 */
export type PendingClose =
  | { mode: 'one'; id: string }
  | { mode: 'others'; keepId: string }
  | { mode: 'all' }
  | null

/**
 * 关闭 `tabs[idx]` 之后，激活权归谁 —— **VS Code 规则：先取右邻，无则取左邻**。
 *
 * ⚠️ 传的是**关闭前**的数组（被关的那个还在），故右邻是 `idx + 1`、左邻是 `idx - 1`。
 * 两边都没有（只关最后一个）→ `null`，回欢迎页。
 */
function neighborAfterClose(tabs: DocTab[], idx: number): string | null {
  return tabs[idx + 1]?.docId ?? tabs[idx - 1]?.docId ?? null
}

/** 把 pendingClose 落到标签组上（纯函数，便于单测） */
function applyPendingClose(tabs: DocTab[], p: NonNullable<PendingClose>): DocTab[] {
  switch (p.mode) {
    case 'one': return tabs.filter(t => t.docId !== p.id)
    case 'others': return tabs.filter(t => t.docId === p.keepId)
    case 'all': return []
  }
}

/**
 * 「清空选择」的返回值 —— 各筛选 setter 与 clearSelection 共用。
 *
 * ⚠️ 做成**函数**而不是共享常量：共享的 Set 对象一旦被谁就地 `add` 一次就永久污染
 * 全局（`set` 只做浅合并，不会克隆）。
 *
 * ⚠️ **文档选择与文件夹选择必须在这里一起清**：两者都在列表中可见，任何一条筛选
 * 变更都可能让被选中的那一半从列表里消失 —— 只清文档会把「选中的文件夹」留在
 * 看不见的地方，批量条上还挂着一个对不可见对象生效的删除按钮。
 */
const noSelection = (): { selectedIds: Set<string>; selectedFolderIds: Set<string> } =>
  ({ selectedIds: new Set(), selectedFolderIds: new Set() })

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
  /**
   * 文件夹选择 —— **与文档选择刻意分成两个集合**。
   *
   * 合并成一个会让每一个消费方（导出、合成、智能分类、批量打标签、拖拽 payload）
   * 都必须自己过滤「这个 id 是文档还是文件夹」，漏一处就是把 `fd_xxx` 当文档 id
   * 发给后端（200 或 404，取决于端点，但界面上看不出异常）。分开之后，
   * 既有文档动作天然只作用于文档，文件夹动作单独一段。
   */
  selectedFolderIds: Set<string>
  setSelectedFolderIds: (ids: Set<string>) => void
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

  // ── 文档标签页（VS Code 双标签模型）──
  /**
   * 打开顺序 = 左→右。
   * ⚠️ **`currentDocId` 仍是激活的唯一真相源**，这里**不另存 activeTabId** ——
   * 两个真相源必然漂移。标签栏的高亮由 `tab.docId === currentDocId` 派生。
   */
  docTabs: DocTab[]
  /** 待提交的「打开意图」——见 `commitTabChange` 的说明 */
  pendingOpenMode: 'preview' | 'pinned' | null
  /** 待提交的「关闭意图」——⚠️ **只在 `commitTabChange` 里被消费**（见三个 close 动作的注释） */
  pendingClose: PendingClose

  openDocTab: (id: string, mode: 'preview' | 'pinned') => void
  pinDocTab: (id: string) => void
  closeDocTab: (id: string) => void
  closeOtherDocTabs: (keepId: string) => void
  closeAllDocTabs: () => void
  commitTabChange: (activeId: string | null) => void
  cancelTabChange: () => void

  setDocs: (docs: DocEntry[]) => void
  setFolders: (folders: FolderEntry[]) => void
  addDoc: (doc: DocEntry) => void
  /** 补记「这篇已有某产物」（生成/解读落盘后调用）——见实现处的两条守卫 */
  markArtifact: (docId: string, mode: string) => void
  /** ⚠️ 传 null 表示「没有选中任何文档」。空串 `''` 不是有效 id，不要再用它当哨兵。 */
  selectDoc: (id: string | null) => void
  setSearchQuery: (q: string) => void
  setActiveType: (t: string) => void
  setActiveTags: (tags: string[]) => void
  setActiveFolderId: (id: string | null) => void
  setTitleFilter: (v: string) => void
  setDateRange: (from: string | null, to: string | null) => void
}

export const createDocsSlice: StateCreator<Store, [], [], DocsSlice> = (set, get) => ({
  docs: [], folders: [], currentDocId: null,
  searchQuery: '', activeType: '', activeTags: [], activeFolderId: null,
  titleFilter: '', dateFrom: null, dateTo: null,

  selectedIds: new Set(),
  setSelectedIds: (ids) => set({ selectedIds: ids }),
  selectedFolderIds: new Set(),
  setSelectedFolderIds: (ids) => set({ selectedFolderIds: ids }),
  clearSelection: () => set(noSelection()),

  listLimit: PAGE_SIZE,
  setListLimit: (n) => set({ listLimit: n }),
  folderBusy: new Set(),
  setFolderBusy: (ids) => set({ folderBusy: ids }),
  docsLoading: false,
  setDocsLoading: (v) => set({ docsLoading: v }),

  docTabs: [],
  pendingOpenMode: null,
  pendingClose: null,

  setDocs: (docs) => set({ docs }),
  setFolders: (folders) => set({ folders }),
  addDoc: (doc) => set(s => ({ docs: [doc, ...s.docs] })),
  /**
   * 就地补记「这篇已有某产物」——生成 / EPUB 解读刚落盘时调用（**服务端确认存在后**才调）。
   *
   * 存在的理由是新鲜度：`modes` 是列表拉取那一刻的快照，而生成发生在其后。不补记的话
   * 「生成完 → 切到别的文档 → 切回来」会因 `modes` 里没有它而不发请求，**笔记看起来
   * 凭空消失**（模式 #52 的典型：成对操作只做了一半，且零报错）。
   *
   * ⚠️ **`modes === undefined`（未知）时原样返回**，绝不补成 `[mode]`：那会把「不知道
   * 有没有」误变成「确定只有这一个」，另一种产物**从此永远不请求**（旧后端下切回来
   * 少一半内容）。`undefined` 只能由整份列表刷新收敛。
   * ⚠️ 已含该 mode 时也返回原对象——`set` 按引用比较，新数组会让整棵树白重渲染一次
   * （每篇生成完成都会调到这里）。
   */
  markArtifact: (docId, mode) => set(s => {
    const i = s.docs.findIndex(d => d.id === docId)
    if (i < 0) return s
    const d = s.docs[i]
    if (!d.modes || d.modes.includes(mode)) return s
    const docs = s.docs.slice()
    // 排序与后端同源（`get_artifact_modes` 用 `sorted`）——免得两处顺序不同，比对时误判
    docs[i] = { ...d, modes: [...d.modes, mode].sort() }
    return { docs }
  }),
  selectDoc: (id) => {
    try {
      // ⚠️ 只在真值时才写：空串不是有效 id，写进去会让下次启动拿它去 restore，
      // 而 `docs.some(d => d.id === '')` 恒为 false —— 白写一次还留个垃圾值。
      if (id) localStorage.setItem('mbw_lastDocId', id)
      else localStorage.removeItem('mbw_lastDocId')
    } catch { /* quota exceeded */ }
    set({ currentDocId: id })
  },

  // ── 文档标签页 ──────────────────────────────────────────────
  //
  // ## 为什么要「延后提交」（deferred commit）
  //
  // 未保存编辑的拦截是**事后**的：`selectDoc` 已改掉 `currentDocId` 之后，PreviewArea
  // 的守卫 effect 才发现有未保存内容并弹窗；用户点「取消」时回退 `selectDoc(旧 id)`。
  //
  // 若在点击的那一刻就改标签组，取消回退后就会出现「currentDocId 回到 A、标签却是 B」
  // 的漂移。所以要回滚的话还得快照被替换掉的预览槽 —— 那是 bug 温床。
  //
  // 改为：**导航意图先进 `pending*`，只有真正的 `loadDocContent` 才提交标签组**。
  // 于是「取消」天然等于「什么都没发生」，一行回滚代码都不需要。
  //
  // ⚠️ **`pendingClose` 只在 `commitTabChange` 里被消费，而后者只在 `loadDocContent`
  // 跑起来时才被调**。所以「不改 `currentDocId` 的操作」绝不能进 pending —— 典型是
  // **关闭一个非激活标签**：它不会触发任何 `loadDocContent`，进了 pending 就永远没人
  // 消费它，**那个标签永远关不掉，且零报错**。三个 close 动作因此各自分两支，
  // 判据统一是「**这个操作改不改 `currentDocId`**」。
  //
  // ## 为什么 close 立即执行那支安全
  //
  // 未保存守卫只在「离开当前正在编辑的文档」时才有意义（`currentDocId` 没变 = 没离开），
  // 所以不改 `currentDocId` 的关闭没有任何需要拦截的东西。

  openDocTab: (id, mode) => {
    const s = get()
    // ⚠️ 先记意图再 `selectDoc`：`selectDoc` 会触发 effect → `loadDocContent` →
    // `commitTabChange`，读到的必须是新意图（zustand 的 set 是同步的，这里顺序即语义）。
    const idx = s.docTabs.findIndex(t => t.docId === id)
    if (idx >= 0) {
      const tab = s.docTabs[idx]
      if (mode === 'pinned' && !tab.pinned) {
        // 就地翻成常驻，**保持原下标**（双击预览标签 = 钉住，不是移动它）。
        // ⚠️ 这一步**不触发未保存守卫**：钉住不算离开，何况它根本没改 `currentDocId`。
        const next = s.docTabs.slice()
        next[idx] = { ...tab, pinned: true }
        set({ docTabs: next, pendingOpenMode: null, pendingClose: null })
      } else {
        // 已在组内且无需改标签（单击已打开的 / 双击已常驻的）→ 只切过去
        set({ pendingOpenMode: null, pendingClose: null })
      }
      s.selectDoc(id)
      return
    }
    set({ pendingOpenMode: mode, pendingClose: null })
    s.selectDoc(id)
  },

  /** 把预览槽翻成常驻。**不做「取消固定」**——与 VS Code 一致：只有 Keep Open，没有 Unpin */
  pinDocTab: (id) => {
    const s = get()
    const idx = s.docTabs.findIndex(t => t.docId === id)
    if (idx < 0 || s.docTabs[idx].pinned) return
    const next = s.docTabs.slice()
    next[idx] = { ...s.docTabs[idx], pinned: true }
    set({ docTabs: next })
  },

  closeDocTab: (id) => {
    const s = get()
    const idx = s.docTabs.findIndex(t => t.docId === id)
    if (idx < 0) return
    if (id !== s.currentDocId) {
      // 不改 currentDocId → **立即执行**（进 pending 就永远没人消费，见上面的长注释）
      set({ docTabs: s.docTabs.filter(t => t.docId !== id) })
      return
    }
    // 关的是激活标签 → 会让 currentDocId 变化 → 走 pending 接受守卫检查
    set({ pendingClose: { mode: 'one', id }, pendingOpenMode: null })
    s.selectDoc(neighborAfterClose(s.docTabs, idx))
  },

  closeOtherDocTabs: (keepId) => {
    const s = get()
    if (keepId === s.currentDocId) {
      set({ docTabs: s.docTabs.filter(t => t.docId === keepId) })
      return
    }
    set({ pendingClose: { mode: 'others', keepId }, pendingOpenMode: null })
    s.selectDoc(keepId)
  },

  closeAllDocTabs: () => {
    const s = get()
    if (s.currentDocId === null) {
      // 已经是欢迎页 → **立即清空**。顺带避开另一个坑：`selectDoc(null)` 在
      // currentDocId 已是 null 时不触发 effect，那 pending 就没人消费了。
      set({ docTabs: [] })
      return
    }
    set({ pendingClose: { mode: 'all' }, pendingOpenMode: null })
    s.selectDoc(null)
  },

  /**
   * 由 `loadDocContent` 在**首行**调用（必须在 `if (!docId) return` 之前 ——
   * 「关闭最后一个标签」会走到 `docId = null`，那时也要提交 pendingClose）。
   */
  commitTabChange: (activeId) => {
    const s = get()
    let tabs = s.pendingClose ? applyPendingClose(s.docTabs, s.pendingClose) : s.docTabs

    // ⚠️ 空串不是有效 id（`GenPanel` 有一处 `selectDoc('')`）——不追加、也不兜底
    if (activeId && !tabs.some(t => t.docId === activeId)) {
      if (s.pendingOpenMode === 'preview') {
        // **替换已存在的预览槽并保持原位置**。写成 append 会让两个预览标签共存，
        // 而「全组至多一个预览槽」是不变量。
        const slot = tabs.findIndex(t => !t.pinned)
        tabs = slot >= 0
          ? tabs.map((t, i) => (i === slot ? { docId: activeId, pinned: false } : t))
          : [...tabs, { docId: activeId, pinned: false }]
      } else {
        // `pinned`，或**没有 pending 的兜底**（启动恢复、GenPanel 各处 selectDoc、
        // 合成完成、MessageItem 引用跳转…）→ 追加为常驻。
        // 这正是把提交挂在 `loadDocContent` 的价值：那些调用点一处都不用改。
        tabs = [...tabs, { docId: activeId, pinned: true }]
      }
    }
    set({ docTabs: tabs, pendingOpenMode: null, pendingClose: null })
  },

  cancelTabChange: () => set({ pendingOpenMode: null, pendingClose: null }),

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
