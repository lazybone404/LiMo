/**
 * 智能分类确认对话框 —— 模型给建议，用户改完再写库。
 *
 * 与 `BatchBar` 同款：**纯展示组件**（props 注入 + 自持草稿），不碰 store、
 * 不发请求。所有 I/O 都在 `DocLibrary` 的 `handleOpenClassify` / `runClassify` 里。
 *
 * ## 两个阶段（2026-10-11 起）
 *
 * ```
 * ① 配置   取材深度 / 文件夹策略 / 标签策略   ← 打开就先看到这些
 * ② 分析   （一次计费调用，按钮转圈）
 * ③ 审定   逐篇建议，可改，确认后写库
 * ```
 *
 * ⚠️ **第一阶段是这一版的重点，不是装饰**。上一版打开对话框即按默认档
 * （600 字 + 优先复用）发起调用，用户原话：
 * 「我点智能分类只是点进那个界面还没开始选确定分类他就直接给我分类了」——
 * 调用本身零写入，但**结果先于选择出现**，用户既没机会决定「模型看多少」，
 * 也没机会决定「要不要建文件夹」。
 *
 * 因此：**`onStart` 是唯一触发计费调用的出口**，它在配置阶段底部按钮里被调用。
 * 组件自身绝不调 `classifyPreview`（那是 `DocLibrary` 的事），也就从结构上
 * 杜绝了「打开即分析」。
 *
 * ## 审定阶段的四块内容
 *
 * 1. **取材深度**（顶部段控）—— 保留在这一阶段，便于「600 字分得不好，试试仅标题」
 *    这种对比；切档**重调 preview**（判据与执行同源，前端不本地重算）。
 * 2. **逐篇行** —— 取材徽标（仅标题 / 已缩减 = 显式降级）+ 文件夹徽标（建议归入 /
 *    建议新建 / 不归入三色）+ 可编辑标签。
 * 3. **警告** —— 模型的幻觉 id、超上限、没覆盖到的篇，全部落在这里或行内。
 * 4. **「尚未写入」声明**（`data-classify-notice`，常驻头部不随主体滚动）—— 见下方
 *    `FOLDER_BADGE` 的注释：preview 返回后每行都带着**已选好值**的下拉框和**已填满**
 *    的标签框，整页看起来就是一份保存好的分类，实测会被读成「没确认就替我分好了」。
 *    它与徽标的「建议」前缀、按钮的「应用这 N 篇」是同一件事的三个落点，**缺一即复发**。
 *
 * ⚠️ **切换取材深度会重置所有手改**：`preview` 是唯一真相源，而重跑后每一行的
 * 取材与建议都可能不同 —— 保留旧编辑等于把新旧两批建议混在一起。这与合成对话框
 * 「切原料模式重拉预览」是同一条规则。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { FolderPlus, FolderTree, FolderX, Loader2, Sparkles } from 'lucide-react'
import type {
  ClassifyOptions, ClassifyPreview, ClassifySource, ClassifySuggestion,
  FolderMode, TagMode,
} from '../../api/classify'

/**
 * 取材深度档位（值 = 后端 `notesChars`；`0` = 仅标题）。
 *
 * ⚠️ **第一项即默认档**（初值取 `DEPTH_OPTIONS[0].value`，同合成域 `dimensionPresets[0]`
 * 预填的判据）。调序 = 改默认值，有单测钉住首项是 600。
 */
export const DEPTH_OPTIONS = [
  { value: 600, label: '600 字' },
  { value: 200, label: '200 字' },
  { value: 0, label: '仅标题' },
] as const

/**
 * 默认「最多新建」数与滑条上界。
 *
 * ⚠️ 与后端 `classifier.MAX_NEW_FOLDERS` / `MAX_NEW_FOLDERS_LIMIT` **同值但不同源**：
 * 配置阶段在调用之前，拿不到 preview 回显的 `limits`。这属于「界面默认值」不是
 * 「后端注册表」——两边不一致的后果是**默认档不同**（用户看得见、改得动），
 * 不是静默失效。后端 `clamp_max_new_folders` 仍会夹紧越界值。
 */
export const MAX_NEW_FOLDERS_DEFAULT = 5
export const MAX_NEW_FOLDERS_LIMIT = 10

/** 一行审定后的方案。`newFolderName` 非空 = 需要调用方先建夹再写入。 */
export interface ClassifyPlanRow {
  id: string
  /** `''` = 未分类；`newFolderName` 非空时这里恒为 `''` */
  folderId: string
  newFolderName: string
  tags: string[]
}

interface Draft {
  id: string
  title: string
  source: ClassifySource
  chars: number
  warnings: string[]
  mode: 'reuse' | 'new' | 'none'
  folderId: string
  newName: string
  /**
   * 模型原本建议的新建名，**仅当这一行是被「新建超限」拒掉时非空**（后端 `candidate`）。
   *
   * 用途只有一个：给被拒的行补一个「新建：XX」选项，让用户能一键采纳 —— 否则那篇
   * 只剩「不归入」，用户看不到模型本来想把它放进哪（91 篇实测：14 个候选只留 5 个）。
   */
  candidate: string
  tagsText: string
}

interface ConfigDraft {
  notesChars: number
  folderMode: FolderMode
  maxNewFolders: number
  tagMode: TagMode
  /** 文本框原文；**只在 `start()` 里拆一次**，拆法见 `splitSeedTags` */
  seedTagsText: string
}

/**
 * 手填标签的拆分口径：**换行、英文逗号、中文逗号**都算分隔符，然后 trim / 去空 / 去重。
 *
 * ⚠️ 刻意**不导出**：测试从 UI 走（改 textarea → 按「开始分析」→ 断言请求体里的
 * `seedTags`），那样连「这个函数有没有被调用到」都在覆盖范围内。直调它反倒会让
 * 「函数写对了、`start()` 里忘了用」这种漏接照样绿。
 *
 * 与 `BatchBar` 的标签清洗是**两码事**，别合并：那边是「用户已定标签的最后一次
 * 去空白」，这边是「用户手打的一坨文本怎么切成标签」，后者多认一个换行分隔符。
 */
function splitSeedTags(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/[\n,，]/)) {
    const t = raw.trim()
    if (t && !out.includes(t)) out.push(t)
  }
  return out
}

export interface ClassifyDialogProps {
  /** 定格的选择集（打开那一刻）——用于显示篇数，也用于「空清单」判断 */
  ids: string[]
  /** 库里现有文件夹数。`0` 时禁用「只用现有文件夹」，并提示可让模型建第一批 */
  folderCount: number
  /** 库里现有标签数（去重）。`0` 时默认落到「我给几个基础标签」 */
  tagCount: number
  running: boolean
  committing: boolean
  error: string | null
  preview: ClassifyPreview | null
  /** **计费调用的唯一出口**：用户在配置阶段按下「开始分析」时才被调用 */
  onStart: (opts: ClassifyOptions) => void
  /** 审定阶段切档 → 重调 preview（后端有夹紧，前端不本地重算） */
  onDepthChange: (chars: number) => void
  onClose: () => void
  onConfirm: (plan: ClassifyPlanRow[]) => void
}

function toDraft(s: ClassifySuggestion): Draft {
  const a = s.folder.action
  const mode: Draft['mode'] =
    a === 'reuse' && s.folder.id ? 'reuse' : (a === 'new' && s.folder.name ? 'new' : 'none')
  return {
    id: s.id,
    title: s.title,
    source: s.source,
    chars: s.chars,
    warnings: s.warnings,
    mode,
    folderId: s.folder.id ?? '',
    newName: s.folder.name ?? '',
    candidate: s.folder.candidate ?? '',
    tagsText: s.tags.join(', '),
  }
}

/** 取材徽标文案。`title_only` 与 `shortened` 都是**降级**，用 warning 色。 */
function sourceBadge(d: Draft): { text: string; degraded: boolean } {
  if (d.source === 'title_only') return { text: '仅标题', degraded: true }
  if (d.source === 'shortened') return { text: `已缩减至 ${d.chars} 字`, degraded: true }
  return { text: `${d.chars} 字`, degraded: false }
}

/**
 * 行首文件夹徽标。
 *
 * ⚠️ **文案必须是「建议 XX」而非完成时**：这一行同时渲染着一个**已选好值**的
 * `<select>`（和已填满的标签 `<input>`），徽标若写「归入现有」，整行读起来就是
 * 「已经保存好的分类结果」。2026-10-11 实测：用户点开对话框、preview 返回后看到
 * 每行都带文件夹与标签，据此认为「我还没确认它就把我分类了」——而库里其实一个字
 * 都没写（日志里只有 `preview`、没有 `commit`；`index.json` 的 mtime 未变）。
 * 加上「建议」二字把语义拉回**尚未生效**。改文案时保留这个前缀。
 *
 * `none` 例外：它是「这行不参与归类」的中性态，加「建议」反而拗口。
 */
const FOLDER_BADGE: Record<Draft['mode'], { text: string; cls: string }> = {
  reuse: { text: '建议归入', cls: 'text-accent bg-accent/10' },
  new: { text: '建议新建', cls: 'text-warning bg-warning/10' },
  none: { text: '不归入', cls: 'text-muted bg-surface-hover' },
}

/** 配置阶段的单选项。`hint` 是「选它会发生什么」——三个策略的差别全在这行字里。 */
function OptionRow({ active, disabled, onClick, title, hint, testid }: {
  active: boolean
  disabled?: boolean
  onClick: () => void
  title: string
  hint?: string
  testid: [string, string]
}) {
  return (
    <button type="button" role="radio" aria-checked={active} disabled={disabled}
      {...{ [testid[0]]: testid[1] }}
      onClick={onClick}
      className={`w-full text-left rounded-lg border px-2.5 py-1.5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
        active ? 'border-accent bg-accent/10' : 'border-border-light hover:bg-surface-hover'}`}>
      <div className="flex items-center gap-1.5">
        <span className={`w-2.5 h-2.5 rounded-full border shrink-0 ${
          active ? 'border-accent bg-accent' : 'border-border'}`} />
        <span className="text-[13px]">{title}</span>
      </div>
      {hint && <p className="mt-0.5 ml-4 text-[11px] text-dimmed">{hint}</p>}
    </button>
  )
}

export function ClassifyDialog({
  ids, folderCount, tagCount,
  running, committing, error, preview,
  onStart, onDepthChange, onClose, onConfirm,
}: ClassifyDialogProps) {
  // 'config' 是**初值**——绝不因为组件挂载而跳到 review（那正是要修的形态）
  const [phase, setPhase] = useState<'config' | 'review'>('config')
  const [cfg, setCfg] = useState<ConfigDraft>(() => ({
    notesChars: DEPTH_OPTIONS[0].value,
    folderMode: 'reuse_or_new',
    maxNewFolders: MAX_NEW_FOLDERS_DEFAULT,
    // 库里一个标签都没有时，默认落到「我给几个基础标签」——否则 `vocab` 拿到的是
    // 一张空词表，界面上写着「复用现有标签」而实际什么都没提供
    tagMode: tagCount > 0 ? 'vocab' : 'seed',
    seedTagsText: '',
  }))
  const [drafts, setDrafts] = useState<Draft[]>([])

  // 结果到了才切到审定阶段（重跑同样适用）；「返回设置」不会被它弹回去，
  // 因为 effect 的依赖是 `preview` 引用，返回设置不改变它
  useEffect(() => { if (preview) setPhase('review') }, [preview])
  // `preview` 是唯一真相源：每次重跑都重建草稿（含切档、含重试）
  useEffect(() => {
    setDrafts(preview ? preview.suggestions.map(toDraft) : [])
  }, [preview])

  const patch = (id: string, next: Partial<Draft>) =>
    setDrafts(prev => prev.map(d => (d.id === id ? { ...d, ...next } : d)))
  const set = (next: Partial<ConfigDraft>) => setCfg(prev => ({ ...prev, ...next }))

  const folders = preview?.folders ?? []
  // ⚠️ **刻意不含 `!error`**（P1-2）：`error` 只在「审定阶段重跑失败」时非空，而那时
  // 屏幕上的草稿是**可用的**——父组件 `loadClassifyPreview` 的 `.catch` 只设 error、
  // 不碰 preview（注释明写「旧的留在屏幕上 + 顶部一条错误行，用户可以先用手上的」）。
  // 带上 `!error` 就把这条设计意图当场推翻：建议明明摆在眼前，「应用」却是灰的。
  // 「从没成功过」的情况由后面的 `!!preview` 挡住，`!error` 是多余且有害的一道。
  const canConfirm = !running && !committing && !!preview && drafts.length > 0

  const start = () => {
    if (running || committing) return
    onStart({
      notesChars: cfg.notesChars,
      folderMode: cfg.folderMode,
      // 不适用时传中性值：后端会忽略，但传了可避免响应里回显一个无意义的数字
      maxNewFolders: cfg.folderMode === 'reuse_or_new' ? cfg.maxNewFolders : 0,
      tagMode: cfg.tagMode,
      seedTags: cfg.tagMode === 'seed' ? splitSeedTags(cfg.seedTagsText) : [],
    })
  }

  const confirm = () => {
    if (!canConfirm) return
    onConfirm(drafts.map(d => ({
      id: d.id,
      folderId: d.mode === 'reuse' ? d.folderId : '',
      // 名字留空的新建 = 实际不归入（后端把 folderId='' 当未分类，不会误建空名夹）
      newFolderName: d.mode === 'new' ? d.newName.trim() : '',
      tags: d.tagsText.split(/[,，]/).map(t => t.trim()).filter(Boolean),
    })))
  }

  const busy = running || committing

  /**
   * 取材深度段控的当前档位 —— **配置阶段与审定阶段共用这一份**。
   *
   * 分阶段的差异正是下面这个三元：配置阶段读本地 `cfg`（用户正在编辑的表单值），
   * 审定阶段读**后端回显**（`limits.notesChars`）。
   *
   * ⚠️ 审定阶段为什么不能读 `cfg`：切档请求由父组件发出（`onDepthChange`），那时对话框
   * 本地的 `cfg` 并不会跟着改——它只在配置阶段驱动表单。继续读 `cfg` 的话，审定阶段
   * 切换深度后**高亮纹丝不动**，而结果已经换了：界面显示「600 字」、屏幕上是仅标题的
   * 产物。读回显还顺带解决了另一件事——越界输入被后端夹紧后，显示的是**实际用的值**。
   *
   * ⚠️ 两处段控**必须共用这一份**：各算各的话，要么两处慢慢漂移，要么其中一处
   * （配置阶段）与这里的三元分支重合而让三元的一支永不被行使（模式 #54）。
   */
  const activeDepth = phase === 'review'
    ? (preview?.limits.notesChars ?? cfg.notesChars)
    : cfg.notesChars

  return createPortal(
    <div data-classify-dialog data-classify-phase={phase}
      className="fixed inset-0 z-300 flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={busy ? undefined : onClose}>
      <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light w-[640px] max-w-[92vw] max-h-[86vh] flex flex-col animate-[fadeIn_0.15s_ease-out]"
        onClick={e => e.stopPropagation()}>

        {/* 头部 */}
        <div className="px-5 pt-4 pb-3 border-b border-border-light shrink-0">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-accent" />
            <h3 className="text-[15px] font-semibold">智能分类 · {ids.length} 篇</h3>
            {running && <Loader2 size={13} className="animate-spin text-muted" />}
          </div>

          {/* 「这是建议、还没写库」——见 FOLDER_BADGE 上方注释：不给这句话，
              preview 返回后每行都像已保存的结果。常驻头部（不随主体滚动） */}
          {phase === 'review' && preview && (
            <p data-classify-notice
              className="mt-2 text-[11px] text-accent bg-accent/10 rounded-md px-2 py-1">
              以下为模型建议，<b>尚未写入文档库</b> —— 确认后点右下角「应用这 {ids.length} 篇」
            </p>
          )}

          {phase === 'review' && (
            <>
              {/* 取材深度 —— 保留在这一阶段便于对比（切档**重调 preview**，见文件头） */}
              <div className="mt-2.5 flex items-center gap-2">
                <span className="text-[12px] text-dimmed">取材深度</span>
                <div className="flex rounded-md border border-border-light overflow-hidden">
                  {DEPTH_OPTIONS.map(o => (
                    <button key={o.value} data-classify-depth={o.value}
                      disabled={busy}
                      onClick={() => { if (o.value !== activeDepth) onDepthChange(o.value) }}
                      className={`px-2.5 py-1 text-[11px] disabled:opacity-50 ${
                        activeDepth === o.value
                          ? 'bg-accent/15 text-accent font-medium'
                          : 'text-muted hover:bg-surface-hover'}`}>
                      {o.label}
                    </button>
                  ))}
                </div>
                <span className="text-[11px] text-disabled">切档会重新分析</span>
              </div>

              {preview && (
                <p className="mt-1.5 text-[11px] text-disabled">
                  {STRATEGY_SUMMARY(preview, ids.length)}
                  {preview.tagVocab.length > 0 && ` · 已提供 ${preview.tagVocab.length} 个标签供复用`}
                </p>
              )}
            </>
          )}
        </div>

        {/* 主体 */}
        <div className="flex-1 overflow-y-auto px-5 py-3">
          {phase === 'config' && (
            <div data-classify-config className="space-y-4">
              <p className="text-[12px] text-dimmed">
                先定好分类策略。<b>点「开始分析」才会调用模型</b>——在那之前不花任何额度。
              </p>

              {/* ① 取材深度 */}
              <section>
                <Label>取材深度</Label>
                <div className="flex items-center gap-2">
                  <div className="flex rounded-md border border-border-light overflow-hidden">
                    {DEPTH_OPTIONS.map(o => (
                      <button key={o.value} data-classify-depth={o.value}
                        disabled={busy}
                        onClick={() => set({ notesChars: o.value })}
                        className={`px-3 py-1 text-[12px] disabled:opacity-50 ${
                          activeDepth === o.value
                            ? 'bg-accent/15 text-accent font-medium'
                            : 'text-muted hover:bg-surface-hover'}`}>
                        {o.label}
                      </button>
                    ))}
                  </div>
                  <span className="text-[11px] text-disabled">依据标题 + 笔记开头</span>
                </div>
              </section>

              {/* ② 文件夹策略 */}
              <section>
                <Label>文件夹</Label>
                <div className="space-y-1.5">
                  <OptionRow testid={['data-classify-folder-opt', 'reuse_or_new']}
                    active={cfg.folderMode === 'reuse_or_new'} disabled={busy}
                    onClick={() => set({ folderMode: 'reuse_or_new' })}
                    title="优先复用现有文件夹，必要时新建"
                    hint={folderCount > 0
                      ? `库里已有 ${folderCount} 个文件夹，会尽量往里面归`
                      : '库里还没有文件夹——选这个可由模型帮你建第一批'} />
                  <OptionRow testid={['data-classify-folder-opt', 'reuse_only']}
                    active={cfg.folderMode === 'reuse_only'}
                    disabled={busy || folderCount === 0}
                    onClick={() => set({ folderMode: 'reuse_only' })}
                    title="只用现有文件夹"
                    hint={folderCount > 0
                      ? '一篇都不新建，归不进现有夹的落「未分类」'
                      : '库里还没有文件夹，选它等于全部不归入'} />
                  <OptionRow testid={['data-classify-folder-opt', 'tags_only']}
                    active={cfg.folderMode === 'tags_only'} disabled={busy}
                    onClick={() => set({ folderMode: 'tags_only' })}
                    title="本次不动文件夹，只打标签"
                    hint="现有分类保持原样" />
                </div>

                {/* 「要不要建夹、建几个」——用户明确提出的那一问 */}
                {cfg.folderMode === 'reuse_or_new' && (
                  <div data-classify-max-new-wrap className="mt-2 flex items-center gap-2.5 pl-4">
                    <span className="text-[12px] text-dimmed shrink-0">最多新建</span>
                    <input data-classify-max-new type="range" min={0}
                      max={MAX_NEW_FOLDERS_LIMIT} step={1} disabled={busy}
                      value={cfg.maxNewFolders}
                      onChange={e => set({ maxNewFolders: Number(e.target.value) })}
                      className="flex-1 accent-(--tk-accent)" />
                    <span data-classify-max-new-value
                      className="text-[12px] font-medium w-8 text-right">
                      {cfg.maxNewFolders === 0 ? '不建' : `${cfg.maxNewFolders} 个`}
                    </span>
                  </div>
                )}
              </section>

              {/* ③ 标签策略 */}
              <section>
                <Label>标签</Label>
                <div className="space-y-1.5">
                  <OptionRow testid={['data-classify-tag-opt', 'vocab']}
                    active={cfg.tagMode === 'vocab'} disabled={busy}
                    onClick={() => set({ tagMode: 'vocab' })}
                    title="复用库里现有标签"
                    hint={tagCount > 0
                      ? `把库里的 ${tagCount} 个标签喂给模型，要求优先复用（防止标签越打越散）`
                      : '库里还没有标签，词表是空的——想有可复用的就先选下面一项'} />
                  <OptionRow testid={['data-classify-tag-opt', 'seed']}
                    active={cfg.tagMode === 'seed'} disabled={busy}
                    onClick={() => set({ tagMode: 'seed' })}
                    title="我给几个基础标签"
                    hint={tagCount > 0
                      ? '用你给的这几个做起手，模型在此范围内归置'
                      : '库里还没标签时的起步方案：先定几个大类，之后逐步积累'} />
                  <OptionRow testid={['data-classify-tag-opt', 'free']}
                    active={cfg.tagMode === 'free'} disabled={busy}
                    onClick={() => set({ tagMode: 'free' })}
                    title="完全交给模型"
                    hint="不给词表，由模型自由命名（标签容易发散）" />
                </div>

                {cfg.tagMode === 'seed' && (
                  <textarea data-classify-seed-tags rows={3} disabled={busy}
                    value={cfg.seedTagsText}
                    onChange={e => set({ seedTagsText: e.target.value })}
                    placeholder={'每行一个，也可用逗号隔开。例如：\n深度学习\n综述\n工具链'}
                    className="mt-2 w-full text-[12px] leading-relaxed bg-elevated border border-border-light rounded-lg px-2.5 py-1.5 outline-none resize-none focus:border-accent" />
                )}
              </section>

              {error && (
                <div data-classify-error
                  className="text-[13px] text-error bg-error/10 rounded-lg px-3 py-2">
                  {error}
                </div>
              )}
            </div>
          )}

          {phase === 'review' && (
            <>
              {running && (
                <p data-classify-running className="text-[13px] text-dimmed py-6 text-center">
                  正在分析这 {ids.length} 篇文档…
                </p>
              )}

              {/* 切档重跑失败：**保留上一次的结果**，只在顶部提示——清空预览会让
                  用户连已经拿到的建议都失去，而失败本身与那些建议无关 */}
              {!running && error && (
                <div data-classify-error
                  className="mb-2.5 text-[13px] text-error bg-error/10 rounded-lg px-3 py-2">
                  {error}
                </div>
              )}

              {!running && preview && (
                <>
                  {preview.warnings.length > 0 && (
                    <div data-classify-warnings
                      className="mb-2.5 text-[12px] text-warning bg-warning/10 rounded-lg px-3 py-2 space-y-0.5">
                      {preview.warnings.map((w, i) => <p key={i}>{w}</p>)}
                    </div>
                  )}

                  <div className="space-y-1.5">
                    {drafts.map(d => {
                      const badge = sourceBadge(d)
                      const fb = FOLDER_BADGE[d.mode]
                      return (
                        <div key={d.id} data-classify-row={d.id} data-classify-folder-mode={d.mode}
                          className="rounded-lg border border-border-light px-2.5 py-2">
                          <div className="flex items-center gap-1.5">
                            <span className="text-[13px] truncate flex-1">{d.title}</span>
                            <span data-classify-source={d.source}
                              className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] ${
                                badge.degraded ? 'text-warning bg-warning/10' : 'text-disabled bg-surface-hover'}`}>
                              {badge.text}
                            </span>
                            <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] ${fb.cls}`}>
                              {fb.text}
                            </span>
                          </div>

                          {/* 文件夹选择：未分类 / 现有文件夹 / 保留新建 */}
                          <div className="mt-1.5 flex items-center gap-1.5">
                            {d.mode === 'reuse'
                              ? <FolderTree size={12} className="text-accent shrink-0" />
                              : d.mode === 'new'
                                ? <FolderPlus size={12} className="text-warning shrink-0" />
                                : <FolderX size={12} className="text-disabled shrink-0" />}
                            <select data-classify-folder-select={d.id}
                              value={d.mode === 'reuse' ? `reuse:${d.folderId}` : d.mode}
                              onChange={e => {
                                const v = e.target.value
                                if (v === 'none') patch(d.id, { mode: 'none', folderId: '' })
                                else if (v === 'new') {
                                  // 采纳候补时**把模型原本的夹名预填进去**，用户不必重打一遍
                                  patch(d.id, { mode: 'new', newName: d.newName || d.candidate })
                                } else patch(d.id, { mode: 'reuse', folderId: v.slice('reuse:'.length) })
                              }}
                              className="flex-1 text-[12px] bg-elevated border border-border-light rounded-md px-1.5 py-1 outline-none">
                              <option value="none">未分类</option>
                              {folders.map(f => (
                                <option key={f.id} value={`reuse:${f.id}`}>{f.name}</option>
                              ))}
                              {/* 「保留新建」只在模型确实建议了新建时出现 */}
                              {d.mode === 'new' && <option value="new">新建：{d.newName || '（未命名）'}</option>}
                              {/* 超限被拒的**候补**：模型原本想建的夹名，一键采纳（等于手动多开一格） */}
                              {d.mode !== 'new' && !!d.candidate && (
                                <option value="new">新建：{d.candidate}（模型原建议）</option>
                              )}
                            </select>
                            {d.mode === 'new' && (
                              <input data-classify-new-name={d.id} value={d.newName}
                                onChange={e => patch(d.id, { newName: e.target.value })}
                                placeholder="新文件夹名"
                                className="w-28 text-[12px] bg-elevated border border-warning/40 rounded-md px-1.5 py-1 outline-none" />
                            )}
                          </div>

                          <input data-classify-tags={d.id} value={d.tagsText}
                            onChange={e => patch(d.id, { tagsText: e.target.value })}
                            placeholder="标签，逗号分隔"
                            className="mt-1.5 w-full text-[12px] bg-elevated border border-border-light rounded-md px-1.5 py-1 outline-none" />

                          {d.warnings.length > 0 && (
                            <div data-classify-warning={d.id} className="mt-1 text-[11px] text-warning space-y-0.5">
                              {d.warnings.map((w, i) => <p key={i}>{w}</p>)}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {/* 底部 */}
        <div className="px-5 py-3 border-t border-border-light flex items-center gap-2 shrink-0">
          {phase === 'review'
            ? <button data-back-classify onClick={() => setPhase('config')} disabled={busy}
                className="text-[12px] text-dimmed hover:text-accent disabled:opacity-50">
                ← 返回设置
              </button>
            : <span className="text-[11px] text-disabled">
                {cfg.notesChars === 0 ? '只依据标题' : `每篇读约 ${cfg.notesChars} 字笔记`}
              </span>}
          <div className="flex-1" />
          <button data-cancel-classify onClick={onClose} disabled={committing}
            className="px-4 py-1.5 text-[13px] text-dimmed hover:bg-surface rounded-lg disabled:opacity-50">
            取消
          </button>
          {phase === 'config' ? (
            <button data-start-classify onClick={start} disabled={busy}
              className="px-4 py-1.5 bg-accent text-on-accent text-[13px] font-medium rounded-lg hover:bg-accent-hover disabled:opacity-50">
              {running ? '分析中…' : preview ? '重新分析' : '开始分析'}
            </button>
          ) : (
            <button data-confirm-classify onClick={confirm} disabled={!canConfirm}
              className="px-4 py-1.5 bg-accent text-on-accent text-[13px] font-medium rounded-lg hover:bg-accent-hover disabled:opacity-50">
              {committing ? '应用中…' : `应用这 ${ids.length} 篇`}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mb-1.5 text-[12px] font-medium text-dimmed">{children}</p>
}

/**
 * 审定阶段头部的一行策略摘要 —— 「这一次模型到底看到了什么」。
 *
 * 策略是**用户选的**（不再藏在默认档里），所以这一行是确认而非告知；
 * 措辞用中文，不暴露 `reuse_or_new` 这类内部枚举名。
 */
function STRATEGY_SUMMARY(p: ClassifyPreview, count: number): string {
  const f = p.folderMode === 'reuse_or_new'
    ? `文件夹：复用为主，最多新建 ${p.limits.maxNewFolders} 个`
    : p.folderMode === 'reuse_only' ? '文件夹：只用现有的'
      : '文件夹：本次不动'
  const t = p.tagMode === 'vocab' ? '标签：复用现有词表'
    : p.tagMode === 'seed' ? '标签：按你给的基础标签'
      : '标签：由模型自由命名'
  return `${count} 篇 · ${f} · ${t} · 每篇最多 ${p.limits.maxTagsPerDoc} 个标签`
}
