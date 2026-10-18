/**
 * 文档库面板 — **编排层**：只负责把各部件接起来并持有交互状态。
 *
 * 职责划分（改动时先看这里，别把逻辑写回本文件）：
 * - 查询/纯函数  → `useDocQuery`（filterDocs / sortDocs / buildResultSet / groupDocs）
 * - 行渲染       → `DocRow`（两种密度）
 * - 列表与分组   → `DocList` + `GroupHeader`
 * - 筛选区       → `FilterArea` / `filters.tsx` / `FilterPopover`
 * - 多选         → `useDocSelection`（状态在 store）
 * - 偏好         → `docLibraryPrefs`（localStorage）
 *
 * ⚠️ 本文件**不持有任何「重开后不该重置」的状态**：面板折叠会卸载组件
 * （SlotLayout 是 `collapsed ? <PanelStrip/> : <Comp/>`），选择集、分批深度、
 * 禁用态全在 store；偏好全在 localStorage。详见 plan「三、状态归属」。
 */
import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../../store'
import {
  updateTranscript, exportBatch, getErrorMessage,
  listFolders, listTranscripts, createFolder, renameFolder, moveFolder, deleteFolder, batchTranscripts,
  ensureFolderPath, getMarkdown,
} from '../../api/client'
import { cancelTask } from '../../api/taskManager'
import { linearizeEvidence, stripMarkers } from '../../lib/markdown-markers'
import { ContextMenu } from '../ui/ContextMenu'
import {
  Pencil, Trash2, BookOpen, MessageSquare, RefreshCw, Tag, Clipboard, FolderInput, Layers,
  AlertTriangle, ChevronRight,
} from 'lucide-react'
import { streamSynthesis, previewSynthesis, type SynthesisPreview, type SourceMode } from '../../api/synthesis'
import { classifyPreview, commitClassifications, type ClassifyOptions, type ClassifyPreview } from '../../api/classify'
import {
  ClassifyDialog, MAX_DEPTH_DEFAULT, MAX_NEW_FOLDERS_DEFAULT, type ClassifyPlanRow,
} from './ClassifyDialog'
import type { DocEntry } from '../../store/types'
import { BatchBar } from './BatchBar'
import { FolderPicker } from './FolderPicker'
import { buildFolderTree } from './folderTree'
import type { HeaderAction } from './GroupHeader'
import { DocRow, type DocRowActions } from './DocRow'
import {
  useDocQuery, countByFolder, collectTagFacets, groupDocs, type DocGroup, type SortKey,
} from './useDocQuery'
import {
  loadDocLibraryPrefs, saveDocLibraryPrefs, DEFAULT_FILTER_AREA_HEIGHT,
  FILTER_AREA_MAX, FILTER_AREA_MIN,
  type Density, type DocLibraryPrefs, type SectionId,
} from './docLibraryPrefs'
import { useDocSelection } from './useDocSelection'
import { DocList } from './DocList'
import { ListToolbar } from './ListToolbar'
import { FilterArea } from './FilterArea'
import { FilterPopover } from './FilterPopover'
import { SearchField } from './SearchField'
import { DocLibraryToolbar } from './DocLibraryToolbar'
import { PAGE_SIZE } from '../../store/docsSlice'

/** 某文档是否有进行中的任务（键是 `${docId}__${kind}`，导入是裸 key） */
function isDocBusy(id: string): boolean {
  const tasks = useStore.getState().processingTasks
  return Object.keys(tasks).some(k => k === id || k.startsWith(id + '__'))
}

/**
 * 取消若干文档的进行中任务。
 *
 * ⚠️ 顺序不可颠倒：`cancelTask` 要任务句柄，而 `clearDocTasks` 会把句柄连同状态一起
 * 删掉——先清再取消会让 abort 找不到目标，任务继续在后台为已删除的 docId 落盘
 * （产生孤儿记录，文档「复活」）。
 * ⚠️ 必须**先整体扫一遍**要取消的 id：逐条「判断 → 取消 → 清」的话，第一轮 clear
 * 已经改掉了 processingTasks，后续判断就全落空了。
 */
function cancelDocTasks(ids: string[]) {
  const targets = ids.filter(isDocBusy)
  const s = useStore.getState()
  for (const id of targets) {
    cancelTask(id)
    s.clearDocTasks(id)
  }
}

/**
 * 维度 textarea（每行一个）→ 清洗后的维度数组。
 *
 * ⚠️ 这里只做**显然的**清洗（去空行、去重），上限与长度控制交给后端
 * `clean_dimensions()`——**权威在后端**，前端这层只是让「已选 N 个维度」显示得
 * 准一些。两边各写一套完整规则会漂移（且前端拦住的量后端拦不住就白搭）。
 */
function parseDimensions(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const line of text.split('\n')) {
    const d = line.trim()
    if (d && !seen.has(d)) { seen.add(d); out.push(d) }
  }
  return out
}

/** 预览里每篇的处理方式 → 面向用户的一句话（与后端 `_STATUS_LABEL` 同族语义）。
 *  ⚠️ `full` 用「未删节」而非「完整输入」（2026-10-05 与后端同步改）：用户在两处
 *  读的是同一件事——这篇材料有没有被动手脚。后端 `_STATUS_LABEL` 那个「输入」是
 *  相对模型说的词，而它的文案会落进产物正文、被模型学去（见该表注释）。 */
const SYNTH_PLAN_LABEL: Record<string, string> = {
  full: '未删节',
  summarized: '压缩为摘要',
  chunked: '分块抽取',
  over_window: '超出窗口',
  unavailable: '无可用原料',
}

/** 内联编辑态：id + 草稿值。标题/标签两处同构 */
interface EditState { id: string; value: string }

/**
 * 筛选区 / 列表区之间的可拖拽分隔条。
 *
 * ⚠️ 用 **mouse 事件 + document 级监听**而不是 pointer capture：
 * 拖动时鼠标必然会移出这条 6px 高的细线，只挂在元素上的 `onMouseMove` 会在那一刻断掉。
 * ⚠️ 起止高度在 `mousedown` 时**快照**进闭包（`startRef`）：拖动过程中 store 会不断
 * 更新 `height`，若每帧都读最新值，delta 会被自己累积成平方级增长。
 */
function FilterSplitter({ height, onResize }: { height: number; onResize: (h: number) => void }) {
  const startRef = useRef<{ y: number; h: number } | null>(null)

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()          // 不 preventDefault 的话拖动会顺带选中页面文本
    startRef.current = { y: e.clientY, h: height }
    const move = (ev: MouseEvent) => {
      const s = startRef.current
      if (s) onResize(s.h + (ev.clientY - s.y))
    }
    const up = () => {
      startRef.current = null
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
  }

  return (
    <div
      data-filter-splitter
      role="separator"
      aria-orientation="horizontal"
      onMouseDown={onMouseDown}
      onDoubleClick={() => onResize(DEFAULT_FILTER_AREA_HEIGHT)}
      title="拖动调整筛选区高度（双击复位）"
      className="group/splitter shrink-0 h-2 cursor-row-resize flex items-center justify-center hover:bg-accent/5"
    >
      <span className="w-8 h-0.5 rounded-full bg-border-default group-hover/splitter:bg-accent/40" />
    </div>
  )
}

export function DocLibrary() {
  const docs = useStore(s => s.docs)
  // 订阅整个 capabilities 对象（而非 `s.capabilities?.dimensionPresets` 再 `?? []`）：
  // 后者每次返回**新数组**，React 的 getSnapshot 比较会永远不等，触发
  // 「getSnapshot should be cached」甚至无限重渲染。派生放在渲染体里做。
  const capabilities = useStore(s => s.capabilities)
  const folders = useStore(s => s.folders)
  const currentId = useStore(s => s.currentDocId)
  // ⚠️ 打开文档一律走 `openDocTab`（而非 `selectDoc`）——它额外记下「预览 / 常驻」意图，
  // 由 `PreviewArea` 的 `loadDocContent` 在真正加载时提交成标签。
  const openDocTab = useStore(s => s.openDocTab)
  const setDocs = useStore(s => s.setDocs)
  const setFolders = useStore(s => s.setFolders)
  const searchQuery = useStore(s => s.searchQuery)
  const activeType = useStore(s => s.activeType)
  const activeTags = useStore(s => s.activeTags)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolderId = useStore(s => s.setActiveFolderId)
  const titleFilter = useStore(s => s.titleFilter)
  const dateFrom = useStore(s => s.dateFrom)
  const dateTo = useStore(s => s.dateTo)
  const addToast = useStore(s => s.addToast)
  const docsLoading = useStore(s => s.docsLoading)
  const purgeDocArtifacts = useStore(s => s.purgeDocArtifacts)
  const folderBusy = useStore(s => s.folderBusy)
  const setFolderBusy = useStore(s => s.setFolderBusy)

  const [prefs, setPrefs] = useState(loadDocLibraryPrefs)
  const { sort, density, groupBy, collapsedGroups } = prefs

  /**
   * 改一项偏好并**落盘** —— 读写必须成对出现在这里。
   * 只 setState 不落盘 = 本次生效、下次归零；只落盘不 setState = 本次不生效、
   * 下次突然冒出来。两种都很难自查（关了面板再打开才发现），故统一走这一个出口。
   */
  const patchPrefs = useCallback((patch: Partial<DocLibraryPrefs>) => {
    setPrefs(p => ({ ...p, ...patch }))
    saveDocLibraryPrefs(patch)
  }, [])

  const toggleFilterArea = useCallback(() => {
    patchPrefs({ filterCollapsed: !prefs.filterCollapsed })
  }, [prefs.filterCollapsed, patchPrefs])

  const toggleSection = useCallback((id: SectionId) => {
    const cur = prefs.expandedSections
    patchPrefs({ expandedSections: cur.includes(id) ? cur.filter(s => s !== id) : [...cur, id] })
  }, [prefs.expandedSections, patchPrefs])

  const setFilterHeight = useCallback((h: number) => {
    patchPrefs({ filterAreaHeight: Math.min(FILTER_AREA_MAX, Math.max(FILTER_AREA_MIN, h)) })
  }, [patchPrefs])

  const setSort = useCallback((key: SortKey) => patchPrefs({ sort: key }), [patchPrefs])

  /**
   * 搜索词**从空变为非空**的那一刻自动切到「相关度」。
   *
   * 这是用户对搜索的默认预期（「最相关的在最前」），且与改动前的行为一致 —— 那时
   * FTS 命中被固定追加在最上方，用户从没选过排序。并入主列表后若不补这一下，
   * 同样的搜索会按「时间」排，最相关的那篇可能沉到第三行。
   *
   * ⚠️ 只在「空 → 非空」这一跳切：此后用户在本次搜索里手动改成「时间」就不再回切 ——
   * 每敲一个字都重置他的选择，比不自动切更烦人。
   */
  const wasSearching = useRef(false)
  useEffect(() => {
    const cur = searchQuery.trim() !== ''
    if (cur && !wasSearching.current) setSort('relevance')
    // 反向：搜索词清空后「相关度」不再有意义（rank 全空、排序退化成保持原序），
    // 而那个按钮也从选项里消失了 —— 不切回去会留下「四个键都没高亮」的空档。
    else if (!cur && sort === 'relevance') setSort('date')
    wasSearching.current = cur
  }, [searchQuery, sort, setSort])
  const setDensity = useCallback((d: Density) => patchPrefs({ density: d }), [patchPrefs])
  const setGroupBy = useCallback((g: DocLibraryPrefs['groupBy']) => patchPrefs({ groupBy: g }), [patchPrefs])

  /**
   * 分组头开合 —— 存的是**收起的**（见 docLibraryPrefs 的注释：新出现的组默认展开）。
   */
  const toggleGroup = useCallback((key: string) => {
    const cur = prefs.collapsedGroups
    patchPrefs({ collapsedGroups: cur.includes(key) ? cur.filter(k => k !== key) : [...cur, key] })
  }, [prefs.collapsedGroups, patchPrefs])

  // 右键菜单（文档 / 文件夹各一个，互斥：打开其一时另一个必然已被关闭）
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; docId: string } | null>(null)
  const [folderCtx, setFolderCtx] = useState<{ x: number; y: number; id: string } | null>(null)
  /** 文档移动浮层（树形）；`ids` 在打开那一刻定格，与选择集变化解耦 */
  const [docMove, setDocMove] = useState<{ ids: string[]; x: number; y: number } | null>(null)
  /** 文件夹移动浮层；`id` 是**被移动的那个夹**，禁用集由它派生 */
  const [folderMove, setFolderMove] = useState<{ id: string; x: number; y: number } | null>(null)
  /** 「请某个分组头进入就地状态」的信号，交给 `GroupHeader` 消费（见 `HeaderAction`） */
  const [headerAction, setHeaderAction] = useState<HeaderAction | null>(null)
  // 内联编辑 —— state 供渲染，ref 供回调读取。
  // ⚠️ 回调**不能**直接闭包 state：那样每次按键都会重建 rowActions 引用，
  // DocRow 的 memo 就白加了（编辑一个标题会让 60 行全部重渲染）。
  const [titleEdit, setTitleEdit] = useState<EditState | null>(null)
  const titleEditRef = useRef<EditState | null>(null)
  const [tagsEdit, setTagsEdit] = useState<EditState | null>(null)
  const tagsEditRef = useRef<EditState | null>(null)
  // 删除确认（单条 = 长度为 1）
  const [deleteTargets, setDeleteTargets] = useState<string[] | null>(null)
  /**
   * 文件夹批量删除的确认目标 —— 打开那一刻**定格**的 `selectedFolderIds`
   * （与 `synthIds` 同一条理由：确认框里报的数字与实际删的必须是同一批）。
   * 存的是用户选中的原始集合；「哪些是根、会连带多少」由 `folderDeletePlan` 现算。
   */
  const [folderDeleteTargets, setFolderDeleteTargets] = useState<string[] | null>(null)
  /**
   * 两个删除确认框都支持 Esc 关闭 —— 它们是**模态**遮罩，键盘用户没有别的方式退出，
   * 而鼠标用户早已习惯按 Esc 取消。`setState(null)` 这类 setter 引用稳定，可以进依赖。
   * ⚠️ 监听挂在 `document` 且不加 capture：内层的就地输入框（重命名 / 新建子夹）在
   * 自己的 onKeyDown 里处理 Esc，那条路径上不会同时开着确认框。
   */
  useEffect(() => {
    if (!deleteTargets) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDeleteTargets(null) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [deleteTargets])
  useEffect(() => {
    if (!folderDeleteTargets) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFolderDeleteTargets(null) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [folderDeleteTargets])
  const [batchBusy, setBatchBusy] = useState(false)
  // 合成对话框 —— null = 关闭；打开中由 synthStage 区分「待确认」与「进行中」
  // （非 null 即进行中，同时承载 stage 文案）
  const [synthOpen, setSynthOpen] = useState(false)
  // 打开那一刻定格的选择集：预览与执行必须基于**同一批** id，否则会出现
  // 「预览说会压缩这篇，实际压缩了另一篇」。不依赖「模态遮罩挡住了选择集变化」
  // 这个隐含假设——把它显式化。
  const [synthIds, setSynthIds] = useState<string[]>([])
  const [synthTitle, setSynthTitle] = useState('')
  // 读者意图（对话框主控件，设计 §7.8.3）+ 「自定义」意图的自由文本。
  // ⚠️ 后者**承接原 `synthNotes`（「对比指引」）的位置**——那个字段后端从不读取
  // （死链），用户填的指引对产物零影响；现在它经 meta 进 prompt 的 task 段。
  const [synthIntent, setSynthIntent] = useState('')
  const [synthIntentText, setSynthIntentText] = useState('')
  const [synthStage, setSynthStage] = useState<string | null>(null)
  // 原料模式（互斥，默认原文）与对比维度（每行一个）—— 都是**现场决定**，
  // 故放对话框本地 state，不做全局持久化（设计 §19.8）
  const [synthSourceMode, setSynthSourceMode] = useState<SourceMode>('raw')
  const [synthDimsText, setSynthDimsText] = useState('')
  const [synthPresetKey, setSynthPresetKey] = useState('')
  // 材料体裁多标签（决定加载哪几类文风增量条款）+「高级」区展开态。
  // 维度与体裁都归高级区：读者意图才是主控件，这两项是「想微调才展开」的修正项。
  const [synthGenres, setSynthGenres] = useState<string[]>([])
  const [synthAdvanced, setSynthAdvanced] = useState(false)
  // 合成前预览（每篇 token / 预计处理方式）：零 LLM 调用、零副作用，
  // 让用户在花 token 之前就知道该不该换模型（设计 §17.4）
  const [synthPreview, setSynthPreview] = useState<SynthesisPreview | null>(null)
  const [synthPreviewErr, setSynthPreviewErr] = useState<string | null>(null)
  // 智能分类对话框 —— 同样是**现场决定**的瞬时状态（折叠即丢弃，不持久化）。
  // `classifyIds` 在打开那一刻**定格**：预览与写入必须基于同一批 id。
  const [classifyOpen, setClassifyOpen] = useState(false)
  const [classifyIds, setClassifyIds] = useState<string[]>([])
  /**
   * **上一次实际发给后端的**那套策略。
   *
   * ⚠️ 它在审定阶段被两处消费：① 用户切取材深度时，其余三项要原样沿用（只换深度
   * 就得重跑）；② 界面上的当前档位。**它记录的是「请求了什么」而不是「结果是什么」**
   * ——结果那边的真相是 `preview.limits`（后端夹紧后的值），两者在越界输入下会不同。
   */
  const [classifyOpts, setClassifyOpts] = useState<ClassifyOptions | null>(null)
  const [classifyRunning, setClassifyRunning] = useState(false)
  const [classifyCommitting, setClassifyCommitting] = useState(false)
  const [classifyErr, setClassifyErr] = useState<string | null>(null)
  const [classifyPreviewData, setClassifyPreviewData] = useState<ClassifyPreview | null>(null)
  // 拖拽 —— onDragOver 阶段读不到 payload，放置区的显隐/高亮只能靠这个 state
  const [draggingIds, setDraggingIds] = useState<Set<string>>(new Set())

  /** 文件夹树 —— 路径标签 / 祖先展开 / 移动禁用集都从它派生，不各算一份 */
  const tree = useMemo(() => buildFolderTree(folders), [folders])

  // ⚠️ `folders` 必须传：不传时 `filterDocs` 退化成**改造前的严格等值**（只看 `folderId`
  // 恰好相等的），点「技术」就看不到「技术/前端」里的文档。它是本次改动的核心语义。
  // ⚠️ `tree` 也传：`filterDocs` / `countByFolder` / `groupDocs` 三处原本各自
  // `buildFolderTree(folders)`，输入是同一个 `folders` ⇒ 同一批数据建三遍树。
  // 建好的那一份就住在这里（它本来就有），转手给它们即可。
  const { visible, matchedIds, snippetById, rankOf, searching, hitsTruncated } = useDocQuery({
    docs, query: searchQuery, title: titleFilter, type: activeType, tags: activeTags,
    folderId: activeFolderId, dateFrom, dateTo, sort, folders, tree,
  })

  /**
   * 文件夹计数 —— **唯一来源**（筛选 chip、分组头、批量条全部读它）。
   * ⚠️ 不读后端的 `folder.count`：那是上次拉取的快照，而 `moveDocs` 是乐观更新
   * （改完立刻改本地 `docs`、不 refetch）。两边各读各的，移动那一瞬间计数就互相矛盾。
   */
  const counts = useMemo(() => countByFolder(docs, folders, tree), [docs, folders, tree])

  /**
   * 库里的**去重标签数** —— 分类对话框拿它决定「标签」一栏的默认档与措辞。
   *
   * ⚠️ 用的是**全库**而非当前筛选结果：词表是后端 `collect_tag_vocab(store)` 从整库
   * 归并出来的，按筛选后的小集合来提示「库里有 N 个标签供复用」，数字就与模型
   * 实际拿到的那份对不上——而这行字的全部职责就是让用户知道模型手里有什么。
   */
  const tagCount = useMemo(() => collectTagFacets(docs).length, [docs])

  /**
   * 简略模式**固定按文件夹分组**（不管「排列方式」偏好是什么）——
   * 它的形态是 **IDE 文件树**：文件夹是可折叠的父节点、文档缩进一层，
   * 两者同高同构，一屏看更多靠的是行紧凑而不是「去掉层级」。
   *
   * ⚠️ 曾经这里是 `density === 'compact' ? 'folder' : groupBy`（简略一律平铺）——
   * 那样确实更省纵向空间，但省掉的是**结构**：一个文件夹里有 30 篇时，平铺列表
   * 除了一列标题什么信息都不给，用户在文件堆里翻找的成本远高于几行分组头的高度。
   * 简略模式本来就不渲染「排列方式」旋钮，也就没有「跟随那个偏好」的必要。
   */
  const arrangeBy = density === 'compact' ? 'folder' : groupBy
  const grouped: DocGroup[] | null = useMemo(
    () => (arrangeBy === 'none' ? null : groupDocs(visible, arrangeBy, folders, undefined, tree)),
    [arrangeBy, visible, folders, tree],
  )

  /**
   * 分组头徽标数字 —— 两种维度**语义不同且都有意如此**：
   * - 文件夹组：该文件夹的**全量**文档数（跨筛选）。筛选到「PDF」时组里只有 2 行，
   *   但删除这个文件夹会搬走全部 12 篇；徽标若是筛选后的数，删除确认就成了骗人的。
   * - 类型/时间组：全量结果集里的条数（派生值，没有「删掉这个组」这回事）。
   *   `groups` 由**未分批**的 `visible` 切出来，故 `g.docs.length` 就是全量。
   */
  const countOf = useCallback((g: DocGroup) =>
    g.editable
      // ⚠️ 文件夹组读**含子树**的总数：点「技术」筛出的是整棵子树的文档，徽标若只报
      // 直接数，就会出现「技术（3）」点开列出 12 篇。直接数（`byFolder`）另有用途 ——
      // 对账求和只能用那一份，把 `totalByFolder` 加起来会把每篇文档数 N 遍。
      ? (g.ref ? counts.totalByFolder.get(g.ref) || 0 : counts.uncategorized)
      : g.docs.length,
  [counts])

  // 只有「拖着已归类的文档」时才需要「移出文件夹」这个落点，否则是无意义的目标
  const draggingCategorized = useMemo(
    () => docs.some(d => draggingIds.has(d.id) && d.folderId),
    [docs, draggingIds],
  )

  // 当前结果集的顺序（Shift 区间靠它算）。⚠️ 必须 memo：hook 内用 effect 同步它，
  // 每次渲染都给个新数组会让那个 effect 每帧都白跑一次。
  const visibleIds = useMemo(() => visible.map(d => d.id), [visible])

  /**
   * 文件夹的显示顺序（前序，只含**文件夹组**）—— 文件夹 Shift 区间靠它算。
   * ⚠️ 平铺（`grouped === null`）时为空数组：那时没有分组头，用户也选不到文件夹，
   * 区间自然退化（`useDocSelection` 里回退成单点 toggle）。
   */
  const folderOrder = useMemo(
    () => (grouped ?? []).filter(g => g.editable && g.ref).map(g => g.ref),
    [grouped],
  )

  const {
    selectedIds, selectedFolderIds, hasSelection, toggle: toggleSelect, setAll: setSelectionAll,
    clear: clearSelection, handleRowClick, handleRowMouseDown, handleRowDoubleClick,
    handleFolderSelect, onListKeyDown,
  } = useDocSelection(visibleIds, openDocTab, folderOrder)

  /**
   * 当前排列**不是「按文件夹」**时清掉文件夹选择。
   *
   * 只有按文件夹排列才渲染可选的文件夹分组头。切到平铺 / 按类型 / 按时间时，残留的
   * `selectedFolderIds` 会让批量条上挂着一个「删除 N 个文件夹」——作用在用户此刻完全
   * 看不见的对象上。这与「筛选变更在同一次 set 里清空选择」是同一条理由，只是那一批
   * 走的是 docsSlice 的 setter，而排列偏好住在本组件外侧（`docLibraryPrefs`）。
   *
   * ⚠️ 判据必须是 `arrangeBy === 'folder'`，**不是 `grouped === null`**：按类型 / 按时间
   * 排列时 `grouped` 同样非 null，但那些组的 `editable` 是 false、复选框根本不渲染
   * —— 用 `grouped` 判会让「切到按类型」这条路径静默漏掉，而界面上那些文件夹已经消失。
   * 简略模式强制按文件夹（见上方 `arrangeBy`），所以它也走保留分支。
   */
  const setSelectedFolderIds = useStore(s => s.setSelectedFolderIds)
  useEffect(() => {
    if (arrangeBy === 'folder') return
    if (useStore.getState().selectedFolderIds.size) setSelectedFolderIds(new Set())
  }, [arrangeBy, setSelectedFolderIds])

  // ── 分批渲染 ──
  // 深度活在 store 里：面板折叠会卸载组件，留在本地等于「收起再展开，滚到哪全忘了」。
  const limit = useStore(s => s.listLimit)
  const setLimit = useStore(s => s.setListLimit)
  // 筛选/排序/排列变化时重置——否则用户在新条件下看到的是「上次的加载深度」，像随机截断
  useEffect(() => {
    setLimit(PAGE_SIZE)
  }, [searchQuery, activeType, activeTags, activeFolderId, titleFilter, dateFrom, dateTo, sort, groupBy, setLimit])
  const rendered = visible.slice(0, limit)
  const hasMore = limit < visible.length
  /**
   * 哨兵 / 滚动容器 —— **必须用 callback ref + state 持有元素本身**，不能用 `useRef` 对象。
   *
   * ⚠️ 观察关系绑的是**某个具体 DOM 元素**，而 effect 的依赖只有 `[hasMore, limit]`。
   * 只要元素被替换而这两个值没变，effect 就不会重跑 —— 旧 observer 继续观察一个
   * 已经从文档里移除的元素，交叉事件**再也不会触发**。
   *
   * 而这个替换是真实存在的：简略 / 详细是两条不同的 JSX 分支，切换密度时
   * `DocList` 整个重挂载（换了一棵子树），哨兵与滚动容器都是新元素。
   * 症状：切过一次密度之后，列表永远停在第一屏的 60 行 —— 滚到底什么都不发生、
   * 零报错，看起来像「显示的文件数有限」。
   *
   * 把「元素是谁」放进依赖，换掉即重建 observer，是唯一不会漏的判据
   * （依赖里列 `density` 只是记住今天有这个分支，下次加分支照样会漏）。
   */
  const [sentinelEl, setSentinelEl] = useState<HTMLDivElement | null>(null)
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null)
  /** ⚠️ 引用必须稳定：内联箭头函数会让 React 每次渲染都 `old(null)` → `new(el)`，白刷两轮 */
  const sentinelRef = useCallback((el: HTMLDivElement | null) => setSentinelEl(el), [])
  const scrollRef = useCallback((el: HTMLDivElement | null) => setScrollEl(el), [])
  useEffect(() => {
    if (!sentinelEl || !scrollEl || !hasMore) return
    // ⚠️ 必须显式传 `root`：不传时以 **viewport** 为基准，而哨兵在一个 `overflow-y-auto`
    // 的容器里 —— 容器内的滚动不会触发 viewport 交叉，判定只在页面级滚动时才对。
    const io = new IntersectionObserver(
      entries => { if (entries[0].isIntersecting) setLimit(limit + PAGE_SIZE) },
      { root: scrollEl, rootMargin: '240px' },
    )
    io.observe(sentinelEl)
    return () => io.disconnect()
  }, [sentinelEl, scrollEl, hasMore, limit, setLimit])

  // 选择（常态多选，无模式开关）。⚠️ 「筛选变更清空选择」不在这里做 effect——那有一帧的
  // 时序窗口（勾选还在、被勾的文档已不可见），改由 docsSlice 的筛选 setter 内联完成。

  const refreshFolders = useCallback(async () => {
    try { setFolders(await listFolders()) } catch { /* 计数刷新失败无碍主流程 */ }
  }, [setFolders])

  /**
   * 给一次文件夹增删改标上「进行中」（key 为 `'create'` 或 folderId），
   * 结束时（含抛错）清除。用来 disabled 入口按钮，避免重复点击发两次请求。
   *
   * ⚠️ 从 `useStore.getState()` 读当前值而不是闭包里的 `folderBusy`：闭包是上一帧的，
   * 两个操作同时进（如新建 + 重命名）时后进的那个会把先进的 key 抹掉。
   */
  const withFolderBusy = useCallback(async (key: string, fn: () => Promise<void>) => {
    const add = (set: Set<string>, id: string) => new Set(set).add(id)
    const drop = (set: Set<string>, id: string) => { const n = new Set(set); n.delete(id); return n }
    setFolderBusy(add(useStore.getState().folderBusy, key))
    try { await fn() }
    finally { setFolderBusy(drop(useStore.getState().folderBusy, key)) }
  }, [setFolderBusy])

  /** 重新拉全量文档列表 —— 只在「服务端行为与预期不符」时兜底用，不是常规路径 */
  const refreshDocs = useCallback(async () => {
    try { setDocs(await listTranscripts()) } catch { /* 保持本地视图，比清空好 */ }
  }, [setDocs])

  /** 文件夹 id → 展示**路径**（toast 用；'' = 未分类）。
   *  ⚠️ 用路径不用裸名：文件夹允许重名，`技术 › 前端` 与 `生活 › 前端` 在 toast 里
   *  必须能区分，否则用户看到「已移动到『前端』」根本不知道是哪一个。 */
  const folderLabel = useCallback((id: string) =>
    id === '' ? '未分类' : (tree.pathOf.get(id) || '文件夹'), [tree])

  // ── 文件夹增删改 ──

  /**
   * 把若干文件夹**及其全部祖先**从「收起」集合里移除。
   *
   * 收起集合存的是分组 **key**（`f:${id}`，见 `groupDocs`），不是裸 id。
   *
   * 三处共用：新建子夹后（否则新夹藏在收起的父级里，看起来像没建成）、移动文件夹后
   * （同上）、筛选到某夹时（否则「筛到子级而父级收着」= 空白列表）。
   * ⚠️ 幂等：没有需要展开的就**不调 `patchPrefs`** —— 该方法会写 localStorage，
   * 每次筛选变更都白写一次盘没有意义，而挂在 effect 里的版本还会多绕一圈。
   */
  const revealFolders = useCallback((ids: string[]) => {
    const want = new Set<string>()
    for (const id of ids) {
      if (!id) continue
      want.add(`f:${id}`)
      for (const a of tree.ancestorIds(id)) want.add(`f:${a}`)
    }
    if (!want.size) return
    const cur = prefs.collapsedGroups
    const next = cur.filter(k => !want.has(k))
    if (next.length !== cur.length) patchPrefs({ collapsedGroups: next })
  }, [tree, prefs.collapsedGroups, patchPrefs])

  // 筛到子夹时自动展开祖先。⚠️ 不展开的话列表是空的，而用户看到的是「这个夹一篇都没有」，
  // 与「文档都在、只是父级收着」完全无法区分。
  useEffect(() => {
    if (activeFolderId) revealFolders([activeFolderId])
  }, [activeFolderId, revealFolders])

  /**
   * 新建文件夹（`parentId` 省略 = 根级）。返回**新夹 id**，失败返回 `null`。
   *
   * ⚠️ 返回 id 而不只是 boolean：`FolderPicker` 的「新建并移过去」要拿它立即选中
   * （否则用户还得再点一次刚建出来的那一行）。工具栏那条路只关心成功与否，包一层即可。
   */
  const createNewFolder = useCallback(async (name: string, parentId = '') => {
    let newId: string | null = null
    await withFolderBusy('create', async () => {
      try {
        const f = await createFolder(name, parentId)
        newId = f?.id ?? null
        await refreshFolders()
        revealFolders([parentId])
      } catch (err) { addToast(getErrorMessage(err), 'error') }
    })
    return newId
  }, [withFolderBusy, refreshFolders, addToast, revealFolders])

  const renameFolderById = useCallback(async (id: string, name: string) => {
    let ok = false
    await withFolderBusy(id, async () => {
      try { await renameFolder(id, name); await refreshFolders(); ok = true }
      catch (err) { addToast(getErrorMessage(err), 'error') }
    })
    return ok
  }, [withFolderBusy, refreshFolders, addToast])

  /** 移动文件夹到另一个父级（'' = 根级）。移到自己或后代由后端拒绝（400） */
  const moveFolderById = useCallback(async (id: string, parentId: string) => {
    // ⚠️ 目标就是当前父级时 PATCH 是静默 no-op：不发请求，并明确说一句——
    // 否则用户点了「移动」却什么都没发生，看起来像功能坏了
    if ((folders.find(f => f.id === id)?.parentId || '') === parentId) {
      const target = parentId ? `「${folderLabel(parentId)}」` : '根级'
      addToast(`「${folderLabel(id)}」已经在${target}里，无需移动`)
      return false
    }
    let ok = false
    await withFolderBusy(id, async () => {
      try {
        await moveFolder(id, parentId)
        await refreshFolders()
        ok = true
        // 移进一个收起的父级里 = 移完就找不着了
        revealFolders([parentId])
      } catch (err) { addToast(getErrorMessage(err), 'error') }
    })
    return ok
  }, [folders, withFolderBusy, refreshFolders, addToast, revealFolders, folderLabel])

  const deleteFolderById = useCallback((id: string) => {
    void withFolderBusy(id, async () => {
      const label = folderLabel(id)
      try {
        const r = await deleteFolder(id)
        await refreshFolders()
        // ⚠️ 回落范围**只认后端返回的 `deletedFolderIds`**（含自身与全部后代）。
        // 用本地 `descendantIds` 再算一遍 = 第二个真相来源：后端改删除语义时前端会
        // 静默停在旧规则上，「删了父夹、子夹里的文档还指着已不存在的 folderId」。
        const gone = new Set(r.deletedFolderIds || [])
        setDocs(useStore.getState().docs.map(d =>
          gone.has(d.folderId || '') ? { ...d, folderId: '' } : d))
        // 当前筛的夹被删掉（含被当成祖先连带删掉）→ 清筛选，否则列表永远空白
        if (activeFolderId && gone.has(activeFolderId)) setActiveFolderId(null)
        const kids = (r.deletedFolders || 1) - 1
        addToast(`已删除「${label}」${kids > 0 ? `及其 ${kids} 个子文件夹` : ''}，` +
          `${r.moved} 个文档已移到未分类`)
      } catch (err) {
        addToast(getErrorMessage(err), 'error')
      }
    })
  }, [folderLabel, withFolderBusy, refreshFolders, setDocs, activeFolderId, setActiveFolderId, addToast])

  // ── 单条编辑 ──

  const applyTitleEdit = useCallback((v: EditState | null) => {
    titleEditRef.current = v
    setTitleEdit(v)
  }, [])
  const applyTagsEdit = useCallback((v: EditState | null) => {
    tagsEditRef.current = v
    setTagsEdit(v)
  }, [])

  const saveTitle = useCallback(async (id: string, title: string) => {
    const next = title.trim()
    applyTitleEdit(null)
    if (!next) { addToast('标题不能为空', 'error'); return }
    const prev = useStore.getState().docs
    setDocs(prev.map(d => d.id === id ? { ...d, title: next } : d))
    try {
      await updateTranscript(id, { title: next })
    } catch (err) {
      setDocs(prev)          // 回滚乐观更新，不制造「假成功」
      addToast(getErrorMessage(err), 'error')
    }
  }, [addToast, setDocs, applyTitleEdit])

  const saveTags = useCallback(async (id: string, raw: string) => {
    const tags = raw.split(/[,，]/).map(t => t.trim()).filter(Boolean)
    applyTagsEdit(null)
    const prev = useStore.getState().docs
    setDocs(prev.map(d => d.id === id ? { ...d, tags } : d))
    try {
      await updateTranscript(id, { tags })
    } catch (err) {
      setDocs(prev)
      addToast(getErrorMessage(err), 'error')
    }
  }, [addToast, setDocs, applyTagsEdit])

  // ── 移动（拖拽 / 批量条 / 右键菜单共用同一条路径）──

  const moveDocs = useCallback(async (ids: string[], folderId: string) => {
    if (!ids.length) return
    const prev = useStore.getState().docs
    setDocs(prev.map(d => ids.includes(d.id) ? { ...d, folderId } : d))
    try {
      const r = await batchTranscripts({ action: 'move', ids, folderId })
      const failed = r.failed || []
      if (failed.length) {
        // ⚠️ **只回滚失败的**：整体 `setDocs(prev)` 会把已经成功移动的那些也退回原处，
        // 而服务端明明改了 —— 用户下次刷新会看到文档「跳回去又跳回来」。
        const failedIds = new Set(failed.map(f => f.id))
        const before = new Map(prev.map(d => [d.id, d.folderId]))
        setDocs(useStore.getState().docs.map(d =>
          failedIds.has(d.id) ? { ...d, folderId: before.get(d.id) ?? '' } : d))
        addToast(`已移动 ${r.affected} 个，${failed.length} 个失败`, 'error')
      } else {
        addToast(`已移动 ${r.affected} 个到「${folderLabel(folderId)}」`)
      }
      // 数量口径对不上 = 服务端行为与预期不符，本地乐观更新已不可信，拉真值
      if (r.affected + failed.length !== ids.length) refreshDocs()
    } catch (err) {
      setDocs(prev)          // 整个请求失败 = 一条都没动，整体回滚是对的
      addToast(getErrorMessage(err), 'error')
      return
    }
    refreshFolders()
  }, [addToast, setDocs, refreshFolders, refreshDocs, folderLabel])

  const handleDragStart = useCallback((e: React.DragEvent, doc: DocEntry) => {
    // 按住 Ctrl/Cmd 拖 = **划选**（useDocSelection），不是把文档拖去文件夹：
    // 同一个手势在两套语义下含义不同，不掐掉 DnD 会一边划选一边显示拖拽幽灵图。
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); return }
    // 从复选框热区起手同理 —— 那里按下是「选中这一行」，不该变成拖动它
    if (e.target instanceof Element && e.target.closest('[data-select-toggle]')) {
      e.preventDefault()
      return
    }
    // 拖已选中的项 = 整批拖；拖未选中的项 = 只拖它自己
    const ids = selectedIds.has(doc.id) ? [...selectedIds] : [doc.id]
    setDraggingIds(new Set(ids))
    // ⚠️ 只设自定义 MIME：设 text/plain 会让拖拽内容被粘进对话输入框
    e.dataTransfer.setData('application/x-limo-doc', ids.join(','))
    e.dataTransfer.effectAllowed = 'move'
  }, [selectedIds])

  const handleDragEnd = useCallback(() => setDraggingIds(new Set()), [])

  const handleFolderDrop = useCallback((folderId: string, e: React.DragEvent) => {
    const raw = e.dataTransfer.getData('application/x-limo-doc')
    setDraggingIds(new Set())
    // drop 阶段才读得到 payload（dragOver 阶段浏览器一律返回空串）
    const ids = raw ? raw.split(',').filter(Boolean) : []
    moveDocs(ids, folderId)
  }, [moveDocs])

  const handleDropToUncategorized = useCallback(
    (e: React.DragEvent) => handleFolderDrop('', e), [handleFolderDrop])

  // ── 删除 ──

  const runDelete = useCallback(async (ids: string[]) => {
    setDeleteTargets(null)
    cancelDocTasks(ids)              // 顺序约束见 cancelDocTasks 注释
    setBatchBusy(true)
    try {
      const r = await batchTranscripts({ action: 'delete', ids })
      const failed = r.failed || []
      const okIds = ids.filter(id => !failed.some(f => f.id === id))
      const s = useStore.getState()
      setDocs(s.docs.filter(d => !okIds.includes(d.id)))
      // ⚠️ **这里刻意不预先 `selectDoc(null)`**：那会让 `purgeDocArtifacts` 读到的
      // `currentDocId` 已经是 null，判不出「被删的正是当前打开的那篇」→ 标签组清了
      // 却不会激活邻居。「删除后的归属」（含邻居选择与 `mbw_lastDocId`）由 purge 独占。
      // 只清真正删掉的那些的产物残留（失败的还留着，清了等于把好数据丢了）
      purgeDocArtifacts(okIds)
      if (failed.length) addToast(`已删除 ${r.affected} 个，${failed.length} 个失败`, 'error')
      else addToast(`已删除 ${r.affected} 个文档`)
    } catch (err) {
      addToast(getErrorMessage(err), 'error')
    } finally {
      setBatchBusy(false)
      clearSelection()
      refreshFolders()
    }
  }, [addToast, setDocs, refreshFolders, purgeDocArtifacts, clearSelection])

  // ── 批量 ──

  const allSelected = visible.length > 0 && visible.every(d => selectedIds.has(d.id))
  const toggleSelectAll = useCallback(() => {
    // 全选 = 当前筛选结果的**全部匹配项**，跨分批（不只是已渲染的 60 条）
    setSelectionAll(visibleIds, !allSelected)
  }, [visibleIds, allSelected, setSelectionAll])

  const runBatch = useCallback(async (fn: () => Promise<void>) => {
    setBatchBusy(true)
    try { await fn() } finally { setBatchBusy(false) }
  }, [])

  const handleBatchExport = useCallback(() => runBatch(async () => {
    const count = selectedIds.size
    if (!count) return
    const ids = [...selectedIds]
    clearSelection()
    try {
      const result = await exportBatch(ids)
      if (result.ok) addToast(`已导出 ${count} 个文档，请查看下载文件夹`)
      else addToast(`导出失败: ${result.error}`, 'error')
    } catch (e) {
      addToast('导出失败: ' + (e as Error).message, 'error')
    }
  }), [selectedIds, runBatch, addToast, clearSelection])

  const handleBatchTag = useCallback((tags: string[], mode: 'add' | 'replace') =>
    runBatch(async () => {
      const ids = [...selectedIds]
      try {
        const r = await batchTranscripts({ action: 'tag', ids, tags, tagMode: mode })
        const failed = new Set((r.failed || []).map(f => f.id))
        setDocs(useStore.getState().docs.map(d => {
          if (!ids.includes(d.id) || failed.has(d.id)) return d
          const merged = mode === 'replace' ? tags : [...new Set([...(d.tags || []), ...tags])]
          return { ...d, tags: merged }
        }))
        if (r.failed?.length) addToast(`已打标签 ${r.affected} 个，${r.failed.length} 个失败`, 'error')
        else addToast(`已为 ${r.affected} 个文档打标签`)
      } catch (err) {
        addToast(getErrorMessage(err), 'error')
      }
    }), [selectedIds, runBatch, addToast, setDocs])

  // ── 文件夹批量动作 ──
  //
  // ⚠️ 与文档批量**刻意分成两条路**：作用对象不同（文件夹本身 vs 文档），payload 也不同
  // （文件夹是一个一个 PATCH/DELETE，没有批量端点）。合并成一个「批量」入口就得在每个
  // 消费方里按 id 前缀分流，漏一处就是把 `fd_xxx` 当文档 id 发出去。

  /**
   * 批量移动的禁用集 = 选中夹 ∪ 各自全部后代（移进自己的子树 → 后端 `FOLDER_CYCLE`）。
   * 与单夹的 `folderMoveExclude` 同一判据，只是起点是多个。
   */
  const folderBatchExclude = useMemo(() => {
    if (!selectedFolderIds.size) return undefined
    const s = new Set<string>()
    for (const id of selectedFolderIds) {
      s.add(id)
      for (const d of tree.descendantIds(id)) s.add(d)
    }
    return s
  }, [selectedFolderIds, tree])

  /**
   * 删除确认框里的数字 —— **先去后代化，再统计**。
   *
   * ⚠️ 选中 `{技术, 技术 › 前端}` 时只该删「技术」一次：两个都发 DELETE 的话第二次必然
   * 404（它已随父级消失）。故先滤掉「祖先也在选中集里」的那些，只留根。
   * ⚠️ 数字必须与真正执行的集合同源（都用这里算出的 `roots`），否则确认框说 5 个、
   * 实际删 3 个。
   */
  const folderDeletePlan = useMemo(() => {
    if (!folderDeleteTargets) return null
    const sel = new Set(folderDeleteTargets)
    const roots = folderDeleteTargets.filter(id => !tree.ancestorIds(id).some(a => sel.has(a)))
    const gone = new Set<string>()
    for (const id of roots) {
      gone.add(id)
      for (const d of tree.descendantIds(id)) gone.add(d)
    }
    // 只算「会被搬回未分类」的那些（未分类本身不在任何被删夹里）
    const docsInScope = docs.filter(d => d.folderId && gone.has(d.folderId)).length
    return { roots, folderCount: gone.size, docsInScope }
  }, [folderDeleteTargets, tree, docs])

  /**
   * 批量删除文件夹 —— 逐个调 DELETE（没有批量端点，且级联范围在服务端算）。
   *
   * ⚠️ 传进来的是**去后代化后的根**（见 `folderDeletePlan`）。
   * ⚠️ 文档回落范围**只认每次后端返回的 `deletedFolderIds`**（与单删同一条判据：
   * 自己用本地 `descendantIds` 再算一遍 = 第二个真相来源）。
   * ⚠️ 部分失败必须点名：静默丢弃会让用户以为那几个夹也删掉了。
   */
  const runDeleteFolders = useCallback(async (roots: string[]) => {
    setFolderDeleteTargets(null)
    if (!roots.length) return
    await runBatch(async () => {
      const gone = new Set<string>()
      /** 删除失败的根 —— 保留在选中集里，用户不必重新找一遍 */
      const failed = new Set<string>()
      let rows = 0
      let moved = 0
      const failures: string[] = []
      for (const id of roots) {
        try {
          const r = await deleteFolder(id)
          rows += r.deletedFolders || 1
          moved += r.moved || 0
          // 老后端不返回集合时至少把自身算进去，否则本地文档会指着已不存在的 folderId
          const ids = r.deletedFolderIds?.length ? r.deletedFolderIds : [id]
          for (const g of ids) gone.add(g)
        } catch (err) {
          failed.add(id)
          failures.push(`${folderLabel(id)}：${getErrorMessage(err)}`)
        }
      }
      await refreshFolders()
      setDocs(useStore.getState().docs.map(d =>
        d.folderId && gone.has(d.folderId) ? { ...d, folderId: '' } : d))
      // 当前筛的夹被删掉（含被祖先连带删掉）→ 清筛选，否则列表永远空白
      if (activeFolderId && gone.has(activeFolderId)) setActiveFolderId(null)
      if (failures.length) {
        addToast(`已删除 ${rows} 个文件夹，${failures.length} 个失败（${failures[0]}`
          + `${failures.length > 1 ? ' 等' : ''}），${moved} 个文档已移到未分类`, 'error')
      } else {
        addToast(`已删除 ${rows} 个文件夹，${moved} 个文档已移到未分类`)
      }
      clearSelection()
      // ⚠️ 部分失败时把失败项**放回**选中集：与「成功那批的文档选择一起清掉」不冲突，
      // 只是让用户能直接重试。全清会让失败的那几个夹在列表里重新变成「找不到了」。
      if (failed.size) setSelectedFolderIds(failed)
    })
  }, [runBatch, refreshFolders, setDocs, activeFolderId, setActiveFolderId,
      folderLabel, addToast, clearSelection, setSelectedFolderIds])

  /**
   * 批量移动文件夹到 `parentId`（'' = 根级）—— 逐个 PATCH。
   *
   * ⚠️ 目标不可能落在选中夹的子树内（`folderBatchExclude` 已把它们禁掉），但后端仍会
   * 再校验一次 —— 前端禁用是「让用户看得懂为什么不行」，不是安全边界。
   */
  const runMoveFolders = useCallback(async (parentId: string) => {
    const all = [...selectedFolderIds]
    if (!all.length) return
    /**
     * ⚠️ 已经在目标父级下的夹要**先摘掉**：PATCH 是一次静默 no-op，但 toast 会说
     * 「已移动 N 个」，而用户看到的树没有任何变化。全部都是 no-op 时连请求都不发。
     * 判据与后端同源（`parentId` 缺省视为根 `""`）。
     */
    const ids = all.filter(id => (folders.find(f => f.id === id)?.parentId || '') !== parentId)
    const unchanged = all.length - ids.length
    if (!ids.length) {
      const target = parentId ? `「${folderLabel(parentId)}」` : '根级'
      addToast(`选中的 ${all.length} 个文件夹已经在${target}里，无需移动`)
      return
    }
    await runBatch(async () => {
      let ok = 0
      /** 移动失败的夹 —— 保留在选中集里供重试（与删除同一取舍） */
      const failed = new Set<string>()
      const failures: string[] = []
      for (const id of ids) {
        try { await moveFolder(id, parentId); ok++ }
        catch (err) { failed.add(id); failures.push(`${folderLabel(id)}：${getErrorMessage(err)}`) }
      }
      await refreshFolders()
      // 移进一个收起的父级里 = 移完就找不着了
      revealFolders([parentId])
      const target = parentId ? folderLabel(parentId) : '根级'
      if (failures.length) addToast(`已移动 ${ok} 个文件夹，${failures.length} 个失败（${failures[0]}）`, 'error')
      else addToast(`已移动 ${ok} 个文件夹到「${target}」${unchanged ? `（${unchanged} 个已在该位置）` : ''}`)
      clearSelection()
      if (failed.size) setSelectedFolderIds(failed)
    })
  }, [selectedFolderIds, folders, runBatch, refreshFolders, revealFolders, folderLabel,
      addToast, clearSelection, setSelectedFolderIds])

  // ── 合成对比综述（多篇 → 一个新文档） ──

  /** 维度预设（后端下发；老后端无此字段时退化为空 → 只有自由输入） */
  const dimensionPresets = capabilities?.dimensionPresets ?? []
  /** 读者意图选项（主控件）与材料体裁选项（高级区）—— 同样后端下发、前端不硬编码 */
  const readerIntents = capabilities?.readerIntents ?? []
  const contentTypeOptions = capabilities?.contentTypes ?? []

  /**
   * 拉一次合成前预览（零 LLM 调用）。
   *
   * ⚠️ 切「原料模式」时必须**重调**而不是前端本地重算：notes 的 token 数与
   * rawText 不同，`plan` 必须重算；而本地重算 = 把 `plan_synthesis` 复刻一遍
   * （项目铁律「成对逻辑必须同源」明令禁止）。重调是一次无 LLM 调用的请求。
   */
  const loadSynthPreview = useCallback((ids: string[], mode: SourceMode) => {
    setSynthPreview(null); setSynthPreviewErr(null)
    previewSynthesis(ids, null, mode)
      .then(setSynthPreview)
      // 业务失败（篇数不足 / 源已删）走这里 —— 提示但不阻断对话框，
      // 用户仍可取消；「合成」按钮由 `canRun` 单独把关
      .catch(err => setSynthPreviewErr(getErrorMessage(err)))
  }, [])

  /**
   * 打开确认对话框。
   *
   * ⚠️ 合成**刻意不自动接一次生成**（设计 D7）。理由不是省事：用户需要先看到
   * 「选了哪几篇、哪几篇被压缩成摘要」再花 token。合成文档建好后默认停在「转录」
   * tab（= `## 来源文档` 清单），核对完再点「生成对比综述」。
   */
  const handleOpenSynthesize = useCallback(() => {
    const ids = [...selectedIds]
    if (ids.length < 2) return
    const first = docs.find(d => d.id === ids[0])?.title || ''
    setSynthIds(ids)
    setSynthTitle(first ? `${first} 等 ${ids.length} 篇对比` : `${ids.length} 篇对比`)
    setSynthIntentText('')
    setSynthStage(null)
    setSynthAdvanced(false)
    // 每次打开都回到「原文 + 首个预设 + 首个意图」：这些是**现场决定**，不该继承
    // 上次（否则用户上次改成「笔记」后这次静默沿用，会莫名其妙少几篇）
    setSynthSourceMode('raw')
    const firstPreset = dimensionPresets[0]
    setSynthPresetKey(firstPreset?.key ?? '')
    setSynthDimsText((firstPreset?.dimensions ?? []).join('\n'))
    // 意图取下发列表第一项（后端保证第一项即默认「理解主题」），体裁由该意图的
    // `defaultContentType` 推导——`null` 就留空，**不替用户猜一个**。
    const firstIntent = readerIntents[0]
    setSynthIntent(firstIntent?.key ?? '')
    setSynthGenres(firstIntent?.defaultContentType ? [firstIntent.defaultContentType] : [])
    setSynthOpen(true)
    loadSynthPreview(ids, 'raw')
  }, [selectedIds, docs, dimensionPresets, readerIntents, loadSynthPreview])

  /** 切换原料模式 → 重拉预览（不可用的篇会在清单里标灰） */
  const changeSynthSourceMode = useCallback((mode: SourceMode) => {
    setSynthSourceMode(mode)
    loadSynthPreview(synthIds, mode)
  }, [synthIds, loadSynthPreview])

  /** 切换维度预设 = **覆盖**当前维度列表（预设即「种子值」语义，UI 上有一行说明） */
  const changeSynthPreset = useCallback((key: string) => {
    setSynthPresetKey(key)
    const preset = dimensionPresets.find(p => p.key === key)
    if (preset) setSynthDimsText(preset.dimensions.join('\n'))
  }, [dimensionPresets])

  /**
   * 切换读者意图 → **体裁跟着重算**（与「切换预设覆盖维度列表」同款语义，
   * 同一对话框里两条「覆盖」规则，用户学一次就懂；小字里注明了这一点）。
   *
   * ⚠️ `defaultContentType` 为 `null` 的意图（理解主题 / 比较材料 / 自定义）
   * **刻意留空**——它们说的是读者的动作，不指示材料是什么体裁。这里若退化成
   * 「猜一个 paper」，就正是设计 §2.2 要根除的那种静默错配（对一叠视频转写稿
   * 毫无意义，而用户看不出为什么）。
   */
  const changeSynthIntent = useCallback((key: string) => {
    setSynthIntent(key)
    const dct = readerIntents.find(i => i.key === key)?.defaultContentType
    setSynthGenres(dct ? [dct] : [])
  }, [readerIntents])

  /** 体裁多选（高级区）。空数组合法 = 不加任何体裁增量条款。 */
  const toggleSynthGenre = useCallback((key: string) => {
    setSynthGenres(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  }, [])

  const runSynthesize = useCallback(() => {
    const ids = synthIds
    // 窗口不足时后端会拒绝，这里提前拦住，别让用户白等一次往返
    const blocked = synthPreview?.mode === 'reject' || synthPreviewErr !== null
    // `synthStage !== null` 防重复点击：这是计费调用，双击两次就是两份钱
    if (ids.length < 2 || synthStage !== null || blocked) return
    const title = synthTitle.trim()
    setBatchBusy(true)
    setSynthStage('准备中…')
    streamSynthesis(ids, {
      title,
      readerIntent: synthIntent,
      // ⚠️ 只在「自定义」时发文本：用户填了自定义文本后改选「找争议」的话，
      // 文本框隐藏但 state 还在——原样发出去会用它污染那条意图的读者目的。
      readerIntentText: synthIntent === 'custom' ? synthIntentText.trim() : '',
      dimensions: parseDimensions(synthDimsText),
      contentType: synthGenres,
      sourceMode: synthSourceMode,
    }, {
      onStage: setSynthStage,
      onDone: (id, _mode, degraded, excluded) => {
        setBatchBusy(false); setSynthOpen(false); setSynthStage(null)
        // ⚠️ 必须走 `useStore.getState()` 而不是组件作用域里的闭包：合成可能持续数分钟，
        // 期间用户完全可能折叠文档库 —— 而折叠 = **卸载组件**（SlotLayout）。
        // 卸载后组件的回调全部失效：文档其实已经落库，列表里却看不到，用户以为失败了。
        // store 层调用不受组件生命周期影响，这条是「产物已落库但 UI 没接上」的防线。
        const s = useStore.getState()
        const used = ids.filter(i => !excluded.includes(i))
        s.addDoc({
          id, title: title || '对比综述', sourceType: 'composite', engine: 'llm',
          contentHash: '', tags: [], createdAt: new Date().toISOString(),
          // 来源清单以**实际参与**的篇为准——被排除的篇没进产物，
          // 记上会让「N 合 1」徽章与来源芯片条指向一份它没读过的文档
          sourceDocIds: used,
          sourceTitles: used.map(i => docs.find(d => d.id === i)?.title || ''),
        })
        s.selectDoc(id)
        s.clearSelection()
        // 降级可见（设计 §4.4.3）：SSE 一闪就没了，这里再提一次；主渠道是产物里的标注
        const notes: string[] = []
        if (degraded.length) notes.push(`${degraded.length} 篇为摘要输入`)
        if (excluded.length) notes.push(`${excluded.length} 篇无笔记已跳过`)
        if (notes.length) addToast(`已合成 ${used.length} 篇，其中 ${notes.join('，')}`, 'info')
        else addToast(`已合成 ${used.length} 篇对比综述`)
      },
      onError: (_code, message, cancelled) => {
        setBatchBusy(false); setSynthOpen(false); setSynthStage(null)
        // 用户主动取消不该弹「错误」（与普通生成的取消语义一致）
        if (!cancelled) addToast(message, 'error')
      },
    })
  }, [synthIds, synthTitle, synthIntent, synthIntentText, synthGenres,
      synthDimsText, synthSourceMode, synthStage, synthPreview, synthPreviewErr,
      docs, addToast])

  // ── 智能分类（模型建议 → 用户审定 → 一次写入）──

  /**
   * 分类 preview 的 `AbortController`。
   *
   * ⚠️ **折叠面板 = 卸载组件**（`SlotLayout` 是 `collapsed ? <PanelStrip/> : <Comp/>`），
   * 而分类是**计费调用**。不中止的话，用户收起侧栏就留下一个无人接收的 LLM 请求：
   * token 照花、结果丢进虚空。cleanup 里 abort → 后端 `disconnect_watcher` 察觉断连
   * → `CLASSIFY_CANCELLED`。**刻意不留恢复路径**（草稿不持久化，重开就重跑）。
   */
  const classifyAbortRef = useRef<AbortController | null>(null)
  useEffect(() => () => { classifyAbortRef.current?.abort() }, [])

  /**
   * 拉一次分类建议（一次 LLM 调用，零写入）。
   *
   * ⚠️ 切「取材深度」/换策略时必须**重调**而不是前端本地重算——那决定了模型看到什么，
   * 结果必然不同；本地重算等于把分类逻辑复刻一遍（项目铁律：成对逻辑必须同源）。
   *
   * ⚠️ **不清空上一次的 `preview`**（清空只在打开对话框时做）：审定阶段切档重跑失败时，
   * 用户已经拿到的建议不该跟着一起消失——失败的是**新的那次**，与手上这份无关。
   * 旧的留在屏幕上 + 顶部一条错误行，用户可以先用手上的、也可以再试。
   */
  const loadClassifyPreview = useCallback((ids: string[], opts: ClassifyOptions) => {
    classifyAbortRef.current?.abort()      // 连点两档时作废前一次，只留最后一次
    const ctrl = new AbortController()
    classifyAbortRef.current = ctrl
    setClassifyRunning(true); setClassifyErr(null)
    classifyPreview(ids, opts, ctrl.signal)
      .then(p => { if (!ctrl.signal.aborted) setClassifyPreviewData(p) })
      .catch(err => {
        if (ctrl.signal.aborted) return    // 自己作废的请求不该弹错误
        setClassifyErr(getErrorMessage(err))
      })
      .finally(() => {
        // 只有仍是当前请求时才收尾：否则后发的那次会被先结束的那次把 loading 抹掉
        if (classifyAbortRef.current === ctrl) setClassifyRunning(false)
      })
  }, [])

  /**
   * 打开对话框 —— **只打开，不发请求**。
   *
   * ⚠️ 这一行曾经是 `loadClassifyPreview(ids, 600)`（打开即按默认档分析）。用户原话：
   * 「我点智能分类只是点进那个界面还没开始选确定分类他就直接给我分类了」——调用本身
   * 零写入，但**结果先于选择出现**，用户没机会决定模型看多少、要不要建文件夹。
   * 现在计费调用的唯一出口是 `startClassify`（对话框配置阶段底部那个按钮）。
   */
  const handleOpenClassify = useCallback(() => {
    const ids = [...selectedIds]
    if (!ids.length) return
    setClassifyIds(ids)
    setClassifyOpts(null)
    setClassifyPreviewData(null)
    setClassifyErr(null)
    setClassifyCommitting(false)
    setClassifyOpen(true)
  }, [selectedIds])

  /** 用户在配置阶段按下「开始分析」——唯一一次（首次）计费调用的入口 */
  const startClassify = useCallback((opts: ClassifyOptions) => {
    setClassifyOpts(opts)
    loadClassifyPreview(classifyIds, opts)
  }, [classifyIds, loadClassifyPreview])

  /**
   * 审定阶段切取材深度 → 重跑。
   *
   * ⚠️ 只换 `notesChars`，其余各项沿用**上一次实际请求的**那套（`classifyOpts`）。
   * 少了这个沿用，切档就会把用户的文件夹/标签策略悄悄打回默认——而界面上那三个
   * 选择器此时并不在场（在配置阶段），用户看不到它们变了。
   *
   * ⚠️ 兜底值本身**不可达**（本函数只在审定阶段被调用，那时 `handleOpenClassify` 必已
   * 设过 `classifyOpts`），保留只为满足类型；但也**不许写字面量**——写成 `?? 5` 的话，
   * `MAX_NEW_FOLDERS_DEFAULT` 一旦调整，这里会静默留在旧值上（用户设的滑条上限变了、
   * 切一次深度又悄悄退回 5），且零报错。
   */
  const changeClassifyDepth = useCallback((chars: number) => {
    const next: ClassifyOptions = {
      notesChars: chars,
      folderMode: classifyOpts?.folderMode ?? 'reuse_or_new',
      maxNewFolders: classifyOpts?.maxNewFolders ?? MAX_NEW_FOLDERS_DEFAULT,
      maxDepth: classifyOpts?.maxDepth ?? MAX_DEPTH_DEFAULT,
      tagMode: classifyOpts?.tagMode ?? 'vocab',
      seedTags: classifyOpts?.seedTags ?? [],
    }
    setClassifyOpts(next)
    loadClassifyPreview(classifyIds, next)
  }, [classifyIds, classifyOpts, loadClassifyPreview])

  const closeClassify = useCallback(() => {
    classifyAbortRef.current?.abort()
    setClassifyOpen(false)
    setClassifyOpts(null)
    setClassifyPreviewData(null)
    setClassifyErr(null)
    setClassifyRunning(false)
  }, [])

  /**
   * 应用分类方案：**先建夹、再写入**。
   *
   * 顺序不能反——`commit` 只接受已存在的 folderId（它刻意不建夹，因为文件夹名是
   * 用户可改的自由文本，只有前端知道最终值）。
   *
   * 任一建夹失败 → **中止整次**并保持对话框打开（草稿还在），绝不带着一半文件夹写入。
   */
  const runClassify = useCallback(async (plan: ClassifyPlanRow[]) => {
    if (!plan.length) return
    setClassifyCommitting(true)
    try {
      // ① 新建名 → id。**整条路径交给后端 `ensure-path`**（缺哪层建哪层，已有则复用）。
      //
      // ⚠️ 前端**刻意不解析路径**（不切 `/`、不比名字）：解析只活在
      // `folder_tree.parse_path` 一处。前端自己实现一份的话，它的失效形态是静默的
      // ——「历史里本来就叫『前端/后端』的夹」会被拆成两层，而分类结果看起来完全正常。
      //
      // ⚠️ 也**不再做本地同名预查**：ensure-path 本身幂等（同父同级同名复用），
      // 而本地查重需要一份与 `path_key` 同构的归一实现 —— 那正是第二份判据。
      const newNames = [...new Set(plan.map(r => r.newFolderName.trim()).filter(Boolean))]
      const nameToId = new Map<string, string>()
      let createdRows = 0
      for (const name of newNames) {
        const res = await ensureFolderPath(name)
        if (!res?.id) throw new Error(`新建文件夹「${name}」失败`)
        nameToId.set(name, res.id)
        createdRows += res.created?.length ?? 0
      }

      const assignments = plan.map(r => ({
        id: r.id,
        // 新建名留空 = 实际不归入（`folderId: ''` 就是未分类，不会误建空名夹）
        folderId: r.newFolderName.trim()
          ? (nameToId.get(r.newFolderName.trim()) ?? r.folderId)
          : r.folderId,
        tags: r.tags,
      }))

      // ② 乐观更新（写库前先改本地，交互不等往返）
      const before = useStore.getState().docs
      const beforeFolder = new Map(before.map(d => [d.id, d.folderId]))
      const beforeTags = new Map(before.map(d => [d.id, [...(d.tags || [])]]))
      const byId = new Map(assignments.map(a => [a.id, a]))
      setDocs(before.map(d => {
        const a = byId.get(d.id)
        if (!a) return d
        return { ...d, folderId: a.folderId, tags: [...new Set([...(d.tags || []), ...a.tags])] }
      }))

      const r = await commitClassifications(assignments)
      const failed = r.failed || []
      if (failed.length) {
        // ⚠️ **只回滚失败项**（照抄 `moveDocs` 的样板）：整体回滚会把服务端明明
        //    已经改好的那些也退回原处，用户下次刷新会看到它们「跳回去又跳回来」
        const failedIds = new Set(failed.map(f => f.id))
        setDocs(useStore.getState().docs.map(d =>
          failedIds.has(d.id)
            ? { ...d, folderId: beforeFolder.get(d.id) ?? '', tags: beforeTags.get(d.id) ?? [] }
            : d))
        // 建了夹却一篇都没写进去 → 会留下空文件夹，**说出来**而不是让用户自己发现
        // 计数用**实际新建的文件夹行数**（与后端额度口径一致：`技术/前端` 两层都缺时算 2）
        const orphan = createdRows ? `（新建的 ${createdRows} 个文件夹已保留）` : ''
        addToast(`已分类 ${r.affected} 篇，${failed.length} 篇失败${orphan}`, 'error')
      } else {
        addToast(`已分类 ${r.affected} 篇`)
      }
      setClassifyOpen(false)
      setClassifyOpts(null)
      setClassifyPreviewData(null)
      clearSelection()
      refreshFolders()
    } catch (err) {
      // 建夹失败 / 后端整体拒绝：**保持对话框打开**（草稿不丢），让用户改完再来
      addToast(getErrorMessage(err), 'error')
    } finally {
      setClassifyCommitting(false)
    }
  }, [addToast, setDocs, clearSelection, refreshFolders])

  // ── 右键菜单 ──

  /**
   * 复制某文档的笔记 Markdown。
   *
   * ⚠️ 必须按传入的 docId **现取**，不能读全局 `currentContent`——那是「当前预览的
   * 文档」的内容，右键 A 而预览停在 B 时会静默复制成 B 的正文，且成功的 toast
   * 让用户以为复制对了。这是「拿当前状态冒充上下文」的典型翻车形态。
   */
  const copyMarkdown = useCallback(async (docId: string) => {
    try {
      const md = await getMarkdown(docId, 'notes')
      const content = md?.content
      if (!content?.trim()) { addToast('该文档暂无笔记内容', 'info'); return }
      // ⚠️ **复制也是「导出」**（正文离开应用），与 md 下载同一条判据：
      // ① 证据块先线性化（原位 `[证据 N]` + 文末附录）—— 剪贴板里没有折叠；
      // ② 再剥掉其余标记行。**顺序不能反**（反过来围栏已剥、附录不生成）。
      // ⚠️ 2026-10-18 前这里**连剥标记都没做** —— 复制出去的是带
      // `<!-- limo-block:v1 … -->` 的磁盘原文（既有缺陷，本轮顺手修）。
      await navigator.clipboard.writeText(
        stripMarkers(linearizeEvidence(content)).trim(),
      )
      addToast('已复制到剪贴板')
    } catch (err) {
      addToast(getErrorMessage(err), 'error')
    }
  }, [addToast])

  const ctxDoc = docs.find(d => d.id === ctxMenu?.docId)
  /**
   * 文档右键菜单 —— **按用途分五段**（打开 / 生成 / 整理 / 输出 / 危险）。
   *
   * 九项连成一长条时，用户每次都得从头扫一遍找「移动到文件夹」；分段后眼睛有落点，
   * 而「删除」不再紧挨着「编辑标签」。分段的依据同批量条（见 `BatchBar` 文件头）：
   * 误点的代价。
   */
  const ctxItems = useMemo(() => {
    if (!ctxDoc) return []
    return [
      // ── 打开 ──
      // ⚠️ 右键是**显式动作**，一律钉成常驻标签（'pinned'）——不像单击那样会被
      // 下一次单击顶掉。用户从菜单里点「打开」，意图显然比随手点一行更明确。
      { label: '打开', icon: <BookOpen size={14} />, onClick: () => openDocTab(ctxDoc.id, 'pinned') },
      { label: '在 AI 中提问', icon: <MessageSquare size={14} />, onClick: () => { openDocTab(ctxDoc.id, 'pinned'); useStore.getState().setAiVisible(true) } },

      { label: '', onClick: () => {}, divider: true },

      // ── 生成 ──
      // ⚠️ 走统一入口 `openPanel`（不是旧别名）：互斥模式下它同时把左侧切到生成面板，
      // 而「重新生成」的下一步按钮就在那个面板里 —— 这正是用户此刻要去的地方。
      { label: '重新生成', icon: <RefreshCw size={14} />, onClick: () => { openDocTab(ctxDoc.id, 'pinned'); useStore.getState().openPanel('genPanel') } },

      { label: '', onClick: () => {}, divider: true },

      // ── 整理 ──
      {
        // ⚠️ 非拖拽的移动路径：只做 DnD 会让键盘/触屏用户完全无法移动文档。
        // 打开**树形浮层**而不是子菜单：文件夹可嵌套后，扁平子菜单里 `前端`/`后端`/`React`
        // 全在同一层，选中哪个全凭记忆（`FolderPicker` 文件头有完整理由）。
        label: '移动到文件夹…', icon: <FolderInput size={14} />,
        onClick: () => {
          setDocMove({ ids: [ctxDoc.id], x: ctxMenu?.x ?? 0, y: ctxMenu?.y ?? 0 })
          setCtxMenu(null)
        },
      },
      { label: '编辑标题', icon: <Pencil size={14} />, onClick: () => applyTitleEdit({ id: ctxDoc.id, value: ctxDoc.title }) },
      { label: '编辑标签', icon: <Tag size={14} />, onClick: () => applyTagsEdit({ id: ctxDoc.id, value: (ctxDoc.tags || []).join(', ') }) },

      { label: '', onClick: () => {}, divider: true },

      // ── 输出 ──
      { label: '复制 Markdown', icon: <Clipboard size={14} />, onClick: () => { void copyMarkdown(ctxDoc.id) } },

      { label: '', onClick: () => {}, divider: true },

      // ── 危险 ──
      { label: '删除', icon: <Trash2 size={14} />, onClick: () => setDeleteTargets([ctxDoc.id]), danger: true },
    ]
  }, [ctxDoc, ctxMenu, openDocTab, copyMarkdown, applyTitleEdit, applyTagsEdit])

  const onContextMenu = useCallback((e: React.MouseEvent, docId: string) => {
    e.preventDefault()
    setFolderCtx(null)         // 两个菜单互斥：右键文档时把文件夹菜单收掉
    setCtxMenu({ x: e.clientX, y: e.clientY, docId })
  }, [])

  const onFolderContextMenu = useCallback((e: React.MouseEvent, id: string) => {
    e.preventDefault()
    setCtxMenu(null)
    setFolderCtx({ x: e.clientX, y: e.clientY, id })
  }, [])

  /**
   * 文件夹右键菜单 —— 四项：新建子文件夹 / 重命名 / 移动到… / 删除。
   *
   * ⚠️ 三个就地项（新建子夹 / 重命名 / 删除确认）的状态都住在 `GroupHeader` 内部，
   * 这里只负责**递信号**（`HeaderAction`），先展开该夹保证分组头已渲染出来。
   */
  const folderCtxItems = useMemo(() => {
    if (!folderCtx) return []
    const id = folderCtx.id
    const name = tree.pathOf.get(id) || '文件夹'
    const ask = (kind: HeaderAction['kind']) => {
      revealFolders([id])
      setHeaderAction({ kind, id })
      setFolderCtx(null)
    }
    return [
      { label: '新建子文件夹', icon: <FolderInput size={14} />, onClick: () => ask('newChild') },
      { label: '重命名', icon: <Pencil size={14} />, onClick: () => ask('rename') },
      {
        label: '移动到…', icon: <FolderInput size={14} />,
        onClick: () => { setFolderMove({ id, x: folderCtx.x, y: folderCtx.y }); setFolderCtx(null) },
      },
      { label: '', onClick: () => {}, divider: true },
      {
        // ⚠️ 删除走分组头的行内确认（那里已写好「及其 N 个子文件夹 / M 个文档移到未分类」
        // 的文案，再写一份就是第二个真相源）
        label: `删除「${name}」`, icon: <Trash2 size={14} />, danger: true,
        onClick: () => ask('delete'),
      },
    ]
  }, [folderCtx, tree, revealFolders])

  /** 就地新建子夹（分组头输入框提交） */
  const createChildFolder = useCallback(async (parentId: string, name: string) => {
    const id = await createNewFolder(name, parentId)
    if (id) {
      // 新夹默认展开（否则它自己收着，看着像没建成）
      revealFolders([id])
      return true
    }
    return false
  }, [createNewFolder, revealFolders])

  /** 移动浮层的禁用集：自身 + 全部后代（移进自己的肚子里 → 后端 `FOLDER_CYCLE`） */
  const folderMoveExclude = useMemo(() => {
    if (!folderMove) return undefined
    const s = tree.descendantIds(folderMove.id)
    s.add(folderMove.id)
    return s
  }, [folderMove, tree])

  const handleRowDelete = useCallback((id: string) => setDeleteTargets([id]), [])

  // ── DocRow 回调（引用稳定，让 React.memo 真的生效）──
  const rowActions: DocRowActions = useMemo(() => ({
    onRowClick: handleRowClick,
    onRowDoubleClick: handleRowDoubleClick,
    onToggleSelect: toggleSelect,
    onRowMouseDown: handleRowMouseDown,
    onContextMenu,
    onEditTitle: d => applyTitleEdit({ id: d.id, value: d.title }),
    onEditTags: d => applyTagsEdit({ id: d.id, value: (d.tags || []).join(', ') }),
    onDelete: handleRowDelete,
    onDragStart: handleDragStart,
    onDragEnd: handleDragEnd,
    onTitleDraftChange: v => {
      const cur = titleEditRef.current
      if (cur) applyTitleEdit({ ...cur, value: v })
    },
    onTitleCommit: () => {
      const cur = titleEditRef.current
      if (cur) saveTitle(cur.id, cur.value)
    },
    onTitleCancel: () => applyTitleEdit(null),
    onTagsDraftChange: v => {
      const cur = tagsEditRef.current
      if (cur) applyTagsEdit({ ...cur, value: v })
    },
    onTagsCommit: () => {
      const cur = tagsEditRef.current
      if (cur) saveTags(cur.id, cur.value)
    },
    onTagsCancel: () => applyTagsEdit(null),
  }), [handleRowClick, handleRowMouseDown, handleRowDoubleClick, toggleSelect, onContextMenu, applyTitleEdit, applyTagsEdit, handleRowDelete,
       handleDragStart, handleDragEnd, saveTitle, saveTags])

  /**
   * 行渲染工厂 —— 交给 `DocList` 调。⚠️ 这里拼的是**每个值**（不是整个 DocRow props
   * 对象），故 `DocRow` 的 `memo` 浅比较照常生效；本函数的引用变化不会让行重渲染。
   */
  const renderRow = useCallback((doc: DocEntry, index: number) => (
    <DocRow
      key={doc.id}
      doc={doc}
      index={index}
      active={doc.id === currentId}
      selected={selectedIds.has(doc.id)}
      density={density}
      matched={matchedIds.has(doc.id)}
      snippet={snippetById.get(doc.id)}
      titleDraft={titleEdit?.id === doc.id ? titleEdit.value : null}
      tagsDraft={tagsEdit?.id === doc.id ? tagsEdit.value : null}
      actions={rowActions}
    />
  ), [currentId, selectedIds, density, matchedIds, snippetById, titleEdit, tagsEdit, rowActions])

  const hasFilter = !!(
    searchQuery.trim() || activeType || activeTags.length || activeFolderId !== null ||
    titleFilter.trim() || dateFrom !== null || dateTo !== null
  )

  const emptyText = visible.length === 0
    ? (hasFilter ? '没有匹配的文档' : '还没有文档\n粘贴链接或文字开始')
    : null

  const listProps = {
    collapsedGroups, countOf, folderBusy, docsLoading, hasMore,
    // 文件夹选择集与文档选择集分开（见 docsSlice），分组头复选框读的是这一个
    selectedFolderIds, onFolderSelect: handleFolderSelect,
    emptyText, truncated: hitsTruncated,
    draggingCategorized,
    // 简略模式的形态是 IDE 文件树：分组子行缩进 + 缩进线，分组头当父节点（见 grouped）
    treeIndent: density === 'compact',
    // 完整路径：缩进夹紧到 6 层后，深层的同名夹只能靠 title 读全
    folderPath: (id: string) => tree.pathOf.get(id) || '',
    onFolderNewChild: createChildFolder,
    onFolderContextMenu,
    headerAction,
    onHeaderActionConsumed: () => setHeaderAction(null),
    onDropToUncategorized: handleDropToUncategorized,
    onToggleGroup: toggleGroup,
    onFolderRename: renameFolderById,
    onFolderDelete: deleteFolderById,
    onFolderDrop: handleFolderDrop,
    renderRow, scrollRef, sentinelRef, onListKeyDown,
  }

  return (
    /**
     * ⚠️ **本面板内的任何浮层都必须 `createPortal(..., document.body)`**。
     *
     * `backdrop-blur-[20px]` 会让这个 `<aside>` 成为后代 `position: fixed` 元素的
     * **包含块**（`backdrop-filter` 非 none 即创建，同 `transform` / `filter`）。
     * 于是写在里面的「全屏遮罩」会被裁剪成面板大小的一小块 —— 用户原话：
     * 「这一个界面还是卡在那一个文档栏里面，我认为这个应该像 ai 对话界面一样可以
     * 悬浮在外面」。现有三处（智能分类 / 合成 / 删除确认）都已 Portal；
     * **新增待办：加浮层时先看这里**。
     *
     * ⚠️ **例外：`absolute` 锚在按钮上的「下拉」不能 Portal**（2026-10-11 代码审查补）
     * ——`FilterPopover` 的筛选浮层是 `absolute right-0 top-full` 挂在这个面板的按钮
     * 上的**下拉**，Portal 到 body 就失去定位基准（会落到视口左上角）。它的修法是另一个
     * 更小的动作：**删掉透明遮罩、改监听 `document` 的 `mousedown`**，用 `rootRef.contains`
     * 划内外界。判据：先看这个浮层是「对话框」（整块 `fixed` → Portal）还是「下拉」
     * （锚在元素上 → 监听 document）。
     *
     * 同样的坑也在 `FloatingAI` 的根 `<aside>`（它自带 `backdrop-blur-[20px]`），
     * 对话侧的三个小对话框同因同修。
     */
    <aside className="w-full h-full border-r border-border-light flex flex-col bg-elevated/60 backdrop-blur-[20px]">
      {/* 标题栏 / 批量工具条
          ⚠️ 显隐**派生自 `hasSelection`**，没有「批量模式」开关：勾中任意一行，工具条
          自己出现；取消到 0 条，自己消失。若要「一条没勾时也能点全选」，那条路径是
          列表内的 Ctrl+A（见 useDocSelection），不是常驻一个模式按钮。 */}
      {hasSelection ? (
        <BatchBar
          selectedCount={selectedIds.size}
          renderedCount={rendered.length}
          totalMatches={visible.length}
          folders={folders}
          busy={batchBusy}
          allSelected={allSelected}
          onToggleSelectAll={toggleSelectAll}
          onExport={handleBatchExport}
          onSynthesize={handleOpenSynthesize}
          onMove={fid => runBatch(() => moveDocs([...selectedIds], fid))}
          onTag={handleBatchTag}
          onClassify={handleOpenClassify}
          onDelete={() => setDeleteTargets([...selectedIds])}
          onClear={clearSelection}
          onCreateFolder={name => createNewFolder(name)}
          // ── 文件夹（第二个集合）──
          folderCount={selectedFolderIds.size}
          onMoveFolders={parentId => void runMoveFolders(parentId)}
          onDeleteFolders={() => setFolderDeleteTargets([...selectedFolderIds])}
          folderExcludeIds={folderBatchExclude}
        />
      ) : (
        <DocLibraryToolbar
          density={density}
          onDensity={setDensity}
          busy={folderBusy.has('create')}
          // 工具栏只关心「成没成」（成功才清草稿）；`FolderPicker` 那边才需要新夹 id
          onCreateFolder={async name => (await createNewFolder(name)) !== null}
        />
      )}

      {density === 'compact' ? (
        /* 简略模式：搜索 + 筛选入口收成一行，列表是**固定按文件夹分组的 IDE 文件树**
           （见 `grouped`：不管「排列方式」偏好是什么，简略一律按文件夹）。
           排列旋钮与分隔条**不渲染**——排列已被固定、筛选收进了浮层，
           在「一屏看更多」的模式里这两样既没空间也没意义。 */
        <>
          <div className="px-3 pt-2 shrink-0 flex items-stretch gap-1.5">
            <div className="flex-1 min-w-0"><SearchField searching={searching} /></div>
            <FilterPopover />
          </div>
          <div className="h-2 shrink-0" />
          <DocList {...listProps} groups={grouped} rendered={rendered} limit={limit} />
        </>
      ) : (
        <>
          <div className="px-3 pt-2 shrink-0"><SearchField searching={searching} /></div>
          <div className="pt-2 shrink-0">
            <FilterArea
              collapsed={prefs.filterCollapsed}
              height={prefs.filterAreaHeight}
              expandedSections={prefs.expandedSections}
              onToggleArea={toggleFilterArea}
              onToggleSection={toggleSection}
            />
          </div>
          {/* 分隔条只在筛选区展开时才有意义（收起后没有配比可调） */}
          {!prefs.filterCollapsed && (
            <FilterSplitter height={prefs.filterAreaHeight} onResize={setFilterHeight} />
          )}
          <div className="pt-1 shrink-0">
            <ListToolbar groupBy={groupBy} sort={sort} onGroupBy={setGroupBy} onSort={setSort}
              showRelevance={rankOf.size > 0} />
          </div>
          <DocList {...listProps} groups={grouped} rendered={rendered} limit={limit} />
        </>
      )}

      {ctxMenu && <ContextMenu x={ctxMenu.x} y={ctxMenu.y} items={ctxItems} onClose={() => setCtxMenu(null)} />}
      {folderCtx && <ContextMenu x={folderCtx.x} y={folderCtx.y} items={folderCtxItems} onClose={() => setFolderCtx(null)} />}

      {/* 文档移动浮层（右键「移动到文件夹…」）。批量条那个是它自己的实例（锚在按钮上），
          两者共用同一个组件、同一个 `data-move-target` 契约。 */}
      {docMove && (
        <FolderPicker
          folders={folders}
          rootLabel="未分类（移出文件夹）"
          x={docMove.x}
          y={docMove.y}
          onPick={id => { const ids = docMove.ids; setDocMove(null); void runBatch(() => moveDocs(ids, id)) }}
          onClose={() => setDocMove(null)}
          onCreate={name => createNewFolder(name)}
        />
      )}

      {/* 文件夹移动浮层 —— **不提供「新建」**：移动是把已有东西挪过去，新建是另一件事 */}
      {folderMove && (
        <FolderPicker
          folders={folders}
          rootLabel="根级（移出当前层级）"
          excludeIds={folderMoveExclude}
          x={folderMove.x}
          y={folderMove.y}
          onPick={parentId => { const id = folderMove.id; setFolderMove(null); void moveFolderById(id, parentId) }}
          onClose={() => setFolderMove(null)}
        />
      )}

      {/* 删除确认 —— **必须 Portal 到 body**，理由见 `<aside>` 的注释（它带
          `backdrop-blur-[20px]`，会把 `fixed` 后代锁在面板内） */}
      {deleteTargets && createPortal(
        <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setDeleteTargets(null)}>
          <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light p-6 max-w-[320px] w-full mx-4 animate-[fadeIn_0.15s_ease-out]" onClick={e => e.stopPropagation()}>
            <h3 className="text-[17px] font-semibold mb-2">
              {deleteTargets.length > 1 ? `删除 ${deleteTargets.length} 个文档` : '删除文档'}
            </h3>
            <p className="text-[13px] text-dimmed mb-6">
              {deleteTargets.length > 1
                ? <>确定要删除选中的 {deleteTargets.length} 个文档吗？关联的 Markdown 也会被删除。</>
                : <>确定要删除「{docs.find(d => d.id === deleteTargets[0])?.title}」吗？关联的 Markdown 也会被删除。</>}
              {deleteTargets.some(isDocBusy)
                ? <span className="text-warning mt-1 block">其中部分文档有进行中的任务，删除将同时取消。</span>
                : '此操作不可撤销。'}
            </p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setDeleteTargets(null)}
                className="px-4 py-2 text-[13px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
              <button data-confirm-delete onClick={() => runDelete(deleteTargets)}
                className="px-4 py-2 bg-error text-white text-[13px] font-medium rounded-lg hover:bg-error/80 transition-colors">删除</button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* 文件夹批量删除确认 —— 同样 Portal 到 body（理由见 `<aside>` 的注释）。
          ⚠️ 文案必须报**子树**口径：删一个夹会连带它全部子夹，只说「N 个文件夹」
          会让用户以为是点选的那几个；而「文档不会被删除」这句是这个动作唯一能给的
          保证，必须写在确认框里而不是只写在 toast 里。 */}
      {folderDeleteTargets && folderDeletePlan && createPortal(
        <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setFolderDeleteTargets(null)}>
          <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light p-6 max-w-85 w-full mx-4 animate-[fadeIn_0.15s_ease-out]" onClick={e => e.stopPropagation()}>
            <h3 className="text-[17px] font-semibold mb-2">
              {folderDeletePlan.roots.length > 1 ? `删除 ${folderDeletePlan.roots.length} 个文件夹` : '删除文件夹'}
            </h3>
            <p className="text-[13px] text-dimmed mb-6">
              确定要删除
              {folderDeletePlan.roots.length === 1
                ? `「${folderLabel(folderDeletePlan.roots[0])}」`
                : `选中的 ${folderDeletePlan.roots.length} 个文件夹`}
              {folderDeletePlan.folderCount > folderDeletePlan.roots.length
                ? `及其 ${folderDeletePlan.folderCount - folderDeletePlan.roots.length} 个子文件夹`
                : ''}
              吗？其中 {folderDeletePlan.docsInScope} 个文档将移到未分类
              （<span className="font-medium">文档不会被删除</span>）。
              {folderDeleteTargets.length > folderDeletePlan.roots.length && (
                <span className="block mt-1 text-[12px]">
                  另有 {folderDeleteTargets.length - folderDeletePlan.roots.length} 个选中的子文件夹
                  已包含在上面，将一并删除。
                </span>
              )}
            </p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setFolderDeleteTargets(null)}
                className="px-4 py-2 text-[13px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
              <button data-confirm-delete-folders onClick={() => void runDeleteFolders(folderDeletePlan.roots)}
                className="px-4 py-2 bg-error text-white text-[13px] font-medium rounded-lg hover:bg-error/80 transition-colors">删除</button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* 智能分类确认 —— 建议由模型给，写不写、怎么写由用户定 */}
      {classifyOpen && (
        <ClassifyDialog
          ids={classifyIds}
          folderCount={folders.length}
          tagCount={tagCount}
          running={classifyRunning}
          committing={classifyCommitting}
          error={classifyErr}
          preview={classifyPreviewData}
          onStart={startClassify}
          onDepthChange={changeClassifyDepth}
          onClose={closeClassify}
          onConfirm={runClassify}
        />
      )}

      {/* 合成确认 —— 进行中（synthStage 非 null）时同一层显示阶段文案，
          不关窗：合成长则几分钟，用户需要看到它还在动 */}
      {synthOpen && (() => {
        // ⚠️ 用**打开那一刻定格**的 `synthIds`，而不是实时的 `selectedIds`：
        // 预览与执行必须基于同一批 id，否则会出现「预览说会压缩这篇、实际压缩了另一篇」。
        const ids = synthIds
        const running = synthStage !== null
        // 预览按 id 索引：后端 docs 顺序与入参一致，但按 id 查不依赖那个顺序契约
        const prevById = new Map((synthPreview?.docs ?? []).map(d => [d.id, d]))
        const excludedSet = new Set(synthPreview?.excluded ?? [])
        const unusableCount = excludedSet.size
        // 窗口类 banner 的触发集**刻意收窄**（设计 §17.4）：banner 的语义是「你该做个动作」，
        // 所以只有用户真该动的情形才配出现。`chunked`（总量超窗、单篇都不超）是**设计认可
        // 的终态**、用户无需行动 —— 它也触发的话，一条「不用换模型」的路径会不断劝人换。
        const overWindow = (synthPreview?.docs ?? []).some(d => d.plan === 'over_window')
        const reject = synthPreview?.mode === 'reject'
        const showWindowBanner = reject || overWindow
        // 换模型推荐：**只提示，不提供切换**（设计 §17.7 明确不做对话框内换模型）
        //
        // ⚠️ 必须排除 `retired`（官方已下线）——这是本文件唯一一处「把预设模型名念给用户
        //    听、让他去设置页配」的地方，推荐一个已下架的模型 = 让用户白跑一趟：
        //    照着配完发现调不通，而提示里没有任何线索说明原因。窗口够大**不构成**
        //    推荐理由（`glm-4v-plus` 的 131072 就够某些场景，但它已下架）。
        const stuffWindow = synthPreview?.stuff_window ?? 0
        const bigModels: string[] = []
        if (showWindowBanner && stuffWindow > 0) {
          const seen = new Set<string>()
          for (const list of Object.values(capabilities?.llmModels ?? {})) {
            for (const m of list) {
              if ((m.context_window ?? 0) >= stuffWindow && !m.retired && !seen.has(m.name)) {
                seen.add(m.name); bigModels.push(m.name)
              }
            }
          }
        }
        const canRun = !running && !reject && synthPreviewErr === null
        const activeIntent = readerIntents.find(i => i.key === synthIntent)
        // 当前生效的材料体裁（高级区折叠时也要看得见——它是**实际生效值**，
        // 比「意图建议值」更有用：用户手改过体裁后，这里显示的是改后的样子）
        const genreSummary = synthGenres.length
          ? synthGenres.map(k => contentTypeOptions.find(c => c.key === k)?.label ?? k).join('、')
          : '未指定'
        // 只做「去空行 + 去重」的显然清洗，权威规则在后端 `clean_dimensions()`——
        // 这里算出的数字仅用于给用户一个「共几个维度」的即时反馈
        const dimsCount = parseDimensions(synthDimsText).length
        const fmtTok = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`
        // ⚠️ Portal 到 body：本对话框虽然写在 `<aside>`（带 `backdrop-blur-[20px]`）里，
        //    但 `backdrop-filter` 会为该元素创建**包含块**，`fixed` 后代将相对它定位，
        //    于是「全屏遮罩」被裁剪成面板大小的一小块。见 `<aside>` 的注释。
        return createPortal(
          <div className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
            onClick={() => { if (!running) setSynthOpen(false) }}>
            {/* ⚠️ 宽度 720px 而非 420px（max-w-105）：本对话框是 fixed 居中，
                **本来就不受文档库栏位宽度约束**，420px 是自己设窄了——而内容有
                已选清单 / 标题 / 对比指引 / 逐篇 token 与处理方式 / 原料单选 /
                维度预设下拉 + 维度列表（§17–§19 三补后更密）。
                `w-full mx-4` 保证窄屏自动收缩，不需要额外的响应式分支。 */}
            <div data-synth-dialog className="bg-elevated rounded-2xl shadow-2xl border border-border-light p-6 max-w-180 w-full mx-4 max-h-[85vh] overflow-y-auto animate-[fadeIn_0.15s_ease-out]"
              onClick={e => e.stopPropagation()}>
              <h3 className="text-[17px] font-semibold mb-2 flex items-center gap-2">
                <Layers size={16} className="text-accent" />合成 {ids.length} 篇对比综述
              </h3>

              {running ? (
                <div className="py-4">
                  <p className="text-[13px] text-dimmed flex items-center gap-2">
                    <RefreshCw size={13} className="animate-spin text-accent" />{synthStage}
                  </p>
                  <p className="text-[11px] text-disabled mt-3">
                    合成期间可以离开本面板，完成后文档会自动进库。
                  </p>
                </div>
              ) : (
                <>
                  {/* 已选清单 —— 用户在这里核对「选对了吗、会怎么处理」，这是本对话框存在的主要理由。
                      token 与处理方式来自预览端点（零 LLM 调用）——前端没有正文，算不出来 */}
                  <p className="text-[11px] text-disabled mb-1">将按以下顺序合成：</p>
                  <ul data-synth-sources className="mb-3 max-h-32 overflow-y-auto rounded-lg border border-border-light bg-surface px-3 py-2 space-y-0.5">
                    {ids.map((id, i) => {
                      const p = prevById.get(id)
                      const unavailable = excludedSet.has(id) || p?.plan === 'unavailable'
                      return (
                        <li key={id} data-synth-row={id}
                          className={`text-[12px] truncate flex items-baseline gap-1 ${unavailable ? 'text-disabled' : 'text-dimmed'}`}>
                          <span className="text-disabled mr-1">{i + 1}.</span>
                          <span className="truncate flex-1">{docs.find(d => d.id === id)?.title || id}</span>
                          {p && !unavailable && (
                            <span data-synth-plan={p.plan} className={`shrink-0 text-[11px] ${p.plan === 'over_window' ? 'text-warning' : 'text-disabled'}`}>
                              {p.plan === 'full' ? '' : `${SYNTH_PLAN_LABEL[p.plan] ?? p.plan} · `}{fmtTok(p.tokens)} tokens
                            </span>
                          )}
                          {unavailable && <span className="shrink-0 text-[11px] text-disabled">无可用原料</span>}
                        </li>
                      )
                    })}
                  </ul>

                  {/* 窗口类 banner —— 只有 `reject` / `over_window` 触发（见上）；`chunked` 不出 */}
                  {showWindowBanner && (
                    <div data-synth-window-banner
                      className="mb-3 px-3 py-2 rounded-lg bg-warning/10 text-warning text-[11px] leading-relaxed flex gap-1.5">
                      <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                      <div>
                        <p>
                          {synthPreview?.reason || '部分文档超出当前模型窗口，将被压缩或截断后参与对比。'}
                        </p>
                        <p className="mt-1">
                          若要全部原样输入，需要约 {fmtTok(stuffWindow)} 窗口的模型。
                          {bigModels.length > 0
                            ? ` 可用：${bigModels.slice(0, 3).join('、')}${bigModels.length > 3 ? ' 等' : ''}（请到设置中切换）`
                            : ' 当前已配置的模型均不满足。'}
                        </p>
                      </div>
                    </div>
                  )}

                  {/* 预览失败（篇数不足 / 源已删）—— 不阻断对话框，但拦下「合成」按钮 */}
                  {synthPreviewErr && (
                    <p data-synth-preview-err className="mb-3 text-[11px] text-warning">{synthPreviewErr}</p>
                  )}

                  <label className="text-[12px] font-medium text-dimmed block mb-1">标题</label>
                  <input data-synth-title value={synthTitle} onChange={e => setSynthTitle(e.target.value)}
                    className="w-full px-2 py-1.5 mb-3 text-[13px] bg-elevated border border-border-light rounded-md outline-none focus:border-accent" />

                  {/* 原料模式 —— 比维度更基础（先决定「用什么材料」，再决定「按什么比」），故在它之上。
                      刻意**互斥不混合**：混合会让等比配额的前提失效、产物标注不可解释（设计 §19.2） */}
                  <label className="text-[12px] font-medium text-dimmed block mb-1">对比原料</label>
                  <div className="flex gap-4 mb-3">
                    {([['raw', '原文（默认）'], ['notes', '已生成的笔记']] as const).map(([v, text]) => (
                      <label key={v} className="flex items-center gap-1.5 text-[12px] text-dimmed cursor-pointer">
                        <input type="radio" name="synth-source-mode" value={v}
                          data-synth-source={v}
                          checked={synthSourceMode === v}
                          onChange={() => changeSynthSourceMode(v)}
                          className="accent-accent" />
                        {text}
                      </label>
                    ))}
                  </div>
                  {unusableCount > 0 && (
                    <p data-synth-excluded className="mb-3 -mt-1.5 text-[11px] text-warning">
                      已选 {ids.length} 篇，其中 {unusableCount} 篇无笔记，合成时将跳过。
                    </p>
                  )}

                  {/* 读者意图 —— 本对话框的**主控件**：它决定 task 段的「读者目的」，
                      即「这份综述写给谁读、读来完成什么」（设计 §7.8.3）。维度与体裁都是
                      **修正项**，退到高级区——三个控件同权重时用户不知道该动哪个。 */}
                  {readerIntents.length > 0 && (
                    <>
                      <label className="text-[12px] font-medium text-dimmed block mb-1">读者意图</label>
                      <div data-synth-intents className="flex flex-wrap gap-x-4 gap-y-1.5">
                        {readerIntents.map(it => (
                          <label key={it.key} className="flex items-center gap-1.5 text-[12px] text-dimmed cursor-pointer">
                            <input type="radio" name="synth-intent" value={it.key}
                              data-synth-intent={it.key}
                              checked={synthIntent === it.key}
                              onChange={() => changeSynthIntent(it.key)}
                              className="accent-accent" />
                            {it.label}
                          </label>
                        ))}
                      </div>
                      {activeIntent?.purpose && (
                        <p data-synth-intent-purpose className="text-[10px] text-disabled mt-1 mb-3 leading-relaxed">
                          {activeIntent.purpose}
                        </p>
                      )}
                      {/* 产物形态提示（2026-10-18）—— **只读**，不给用户选。
                          形态由意图派生（`synthesis.OUTPUT_SHAPES`），两个旋钮必然
                          出现「意图说理解、形态选比较」这种自相矛盾的组合。
                          文案来自 capabilities 的 `shapeLabel` / `shapeDescription`，
                          前端不硬编码（后端加了形态这里自动跟上）。 */}
                      {activeIntent?.shapeLabel && (
                        <p data-synth-shape className="text-[10px] text-disabled -mt-2 mb-3 leading-relaxed">
                          产物形态：<span className="text-dimmed">{activeIntent.shapeLabel}</span>
                          {activeIntent.shapeDescription ? ` · ${activeIntent.shapeDescription}` : ''}
                        </p>
                      )}
                      {synthIntent === 'custom' && (
                        <textarea data-synth-intent-text value={synthIntentText}
                          onChange={e => setSynthIntentText(e.target.value)}
                          rows={2} placeholder="说明这份综述给谁读、读来完成什么，例如：给团队做选型决策，重点看可落地性与成本"
                          className="w-full px-2 py-1.5 mb-3 text-[13px] bg-elevated border border-border-light rounded-md outline-none focus:border-accent resize-none" />
                      )}
                    </>
                  )}

                  {/* 「高级」区 —— 维度与材料体裁。收起态在标题行里带出**当前生效**的体裁
                      （不是「意图的建议值」）：用户手改过体裁之后，这里显示的就是改后的样子。 */}
                  <button type="button" data-synth-advanced-toggle
                    onClick={() => setSynthAdvanced(v => !v)}
                    className="flex items-center gap-1 text-[12px] text-dimmed hover:text-text mb-2 transition-colors">
                    <ChevronRight size={12}
                      className={`transition-transform ${synthAdvanced ? 'rotate-90' : ''}`} />
                    高级选项（对比维度 / 材料体裁：{genreSummary}）
                  </button>

                  {synthAdvanced && (
                    <div data-synth-advanced className="mb-3 pl-3 border-l border-border-light">
                      {/* 对比维度 —— **两个落点**，软硬不同（2026-10-05 一天内变了两次，
                          以当前为准）：
                          ① 「## 读者问题」那几节的**候选来源**（软：可合并、重排、增补）
                          ② 文末「## 各篇速查」的**小节标题**（硬：逐条成节）
                          ⚠️ ① 单独存在时维度**在产物上没有硬落点**，「维度可控」有被架空的
                          风险；② 把它接住了。但速查区是**竖排清单**，不是旧版那种
                          「列 = 各篇文档」的大表——那张表 N 篇就是 N+1 列、中文单元格被
                          压到十几个字符宽（用户原话：「表格上的内容多会影响显示」）。
                          ⚠️ ① 的意义是**告诉模型什么更重要**——工程实践的警告正是
                          「模型倾向保留出现次数最多的内容，而不是最重要的内容」，删不得。 */}
                      <label className="text-[12px] font-medium text-dimmed block mb-1">对比维度</label>
                      {dimensionPresets.length > 0 && (
                        <select data-synth-preset value={synthPresetKey} onChange={e => changeSynthPreset(e.target.value)}
                          className="w-full px-2 py-1.5 mb-2 text-[13px] bg-elevated border border-border-light rounded-md outline-none focus:border-accent">
                          {dimensionPresets.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
                        </select>
                      )}
                      <textarea data-synth-dims value={synthDimsText} onChange={e => setSynthDimsText(e.target.value)}
                        rows={4} placeholder="每行一个维度，例如：研究问题"
                        className="w-full px-2 py-1.5 text-[13px] bg-elevated border border-border-light rounded-md outline-none focus:border-accent resize-none" />
                      <p className="text-[10px] text-disabled mt-0.5 mb-3">
                        每行一个，共 {dimsCount} 个维度 —— 模型围绕它推导「读者问题」小节
                        （可合并、重排或增补）；文末「各篇速查」则按这些维度逐条列出每篇的取值，
                        供横向比对。切换预设会覆盖当前列表。
                      </p>

                      {/* 材料体裁（多选）—— 决定加载哪几类「类型增量」文风条款（设计 §7.8.2） */}
                      <label className="text-[12px] font-medium text-dimmed block mb-1">材料体裁（可多选）</label>
                      <div data-synth-genres className="flex flex-wrap gap-x-4 gap-y-1.5">
                        {contentTypeOptions.map(c => (
                          <label key={c.key} className="flex items-center gap-1.5 text-[12px] text-dimmed cursor-pointer">
                            <input type="checkbox" data-synth-genre={c.key}
                              checked={synthGenres.includes(c.key)}
                              onChange={() => toggleSynthGenre(c.key)}
                              className="accent-accent" />
                            {c.label}
                          </label>
                        ))}
                      </div>
                      <p className="text-[10px] text-disabled mt-0.5">
                        切换读者意图会重置本项；留空则不加任何体裁专属的文风条款。
                      </p>
                    </div>
                  )}

                  <div className="flex gap-2 justify-end">
                    <button onClick={() => setSynthOpen(false)}
                      className="px-4 py-2 text-[13px] text-dimmed hover:bg-surface rounded-lg transition-colors">取消</button>
                    <button data-confirm-synth onClick={runSynthesize} disabled={!canRun}
                      className="px-4 py-2 bg-accent text-on-accent text-[13px] font-medium rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed">合成</button>
                  </div>
                </>
              )}
            </div>
          </div>,
          document.body,
        )
      })()}
    </aside>
  )
}
