/**
 * 产物元数据的**展示判据**（预览区「自证条」与版本下拉共用同一份）。
 *
 * 为什么要有这个文件：`fmtTime` / `fmtChars` 原先只住在 `VersionSwitcher.tsx` 里。
 * 2026-10-11 加自证条时若各写一份，就是同一份展示判据的第二份实现（模式 #54）——
 * 两处的时间格式或字数进位会慢慢漂移，而**没有任何报错**（两边都只是「显示得不太一样」，
 * 没有断言盯着）。同理，`buildMetaChips` 决定「哪些字段值得给用户看、按什么顺序」，
 * 这个判据也不该散在组件 JSX 里。
 *
 * ⚠️ 本模块**零 React**：纯函数，可被 `node` 环境直测（见 `test/artifactMeta.test.ts`）。
 */
import type { MarkdownMeta, MarkdownVersion } from '../store/types'

/**
 * 本地化到分钟。⚠️ 空串 / 解析失败一律返回空串——`new Date("").toLocaleString()`
 * 会渲染出 `Invalid Date`，那既不准确也不比留白更有用（空 = 这一版没有时间信息）。
 */
export function fmtTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

/** 字数标签。⚠️ `undefined` = 后端没给（旧产物 / 测试 mock）→ **不显示**；
 *  `0` = 确实 0 字 → 显示「0字」。把两者都渲染成 `0字` 是在伪造数据。 */
export function fmtChars(n?: number): string {
  if (n === undefined) return ''
  if (n < 1000) return `${n}字`
  return `${(n / 1000).toFixed(1)}k字`
}

/**
 * token 数的人读写法：`1234` → `1.2k`，`340` → `340`。
 *
 * ⚠️ 与 `fmtChars` **分开**：字数的单位是「字」，token 数的单位是「token」，
 * 混用一个函数会让「1.2k字」这种标签出现在用量位上。取整到 0.1k 是刻意的——
 * 用量是量级信息，不需要精确到个位，而 `12345` 五个数字挤在 11px 的标签里读不出来。
 */
export function fmtTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return ''
  if (n < 1000) return `${n}`
  return `${(n / 1000).toFixed(1)}k`
}

/**
 * 金额：`0.060482` → `¥0.06`；不足 0.01 时多给两位（`¥0.0005`）。
 *
 * ⚠️ **`undefined` 与 `0` 必须分开**：`undefined` = 单价没配全 → 返回空串，调用方
 * 只显示 token 数；`0` 理论上不会出现（后端不写 0），真出现了也照实显示「¥0.00」。
 * 若把 `undefined` 也渲染成 `¥0.00`，用户会以为这次生成免费——那是假话。
 */
export function fmtCost(cost?: number): string {
  if (cost === undefined || !Number.isFinite(cost)) return ''
  if (cost === 0) return '¥0.00'
  if (cost < 0.01) return `¥${cost.toFixed(4)}`
  return `¥${cost.toFixed(2)}`
}

export type MetaChipKey = 'version' | 'time' | 'chars' | 'model' | 'prompt' | 'usage'

export interface MetaChip {
  key: MetaChipKey
  label: string
}

/**
 * 取当前版在版本列表里的那一行（用来兜 `createdAt` / `chars` / `source`）。
 *
 * ⚠️ **必须按 `(version, mode)` 双键匹配**：同一份文档的 notes / polished **各自从 v1 起**，
 * 只按 `version` 找会拿到**另一个 mode** 的生成时间——「笔记 v2 显示的是整理版 v2 的时间」，
 * 而两个数都是合法的，没有任何东西会报错。
 * 兜底才退回「同 version 的第一行」（`mode` 字段缺失的历史条目）。
 */
function pickVersionRow(meta: MarkdownMeta, versions: MarkdownVersion[]): MarkdownVersion | undefined {
  return versions.find(v => v.version === meta.version && v.mode === meta.mode)
    ?? versions.find(v => v.version === meta.version)
}

/**
 * 产物元数据 → 一横排标签的数据。
 *
 * 取哪几项、什么顺序（左→右 = **身份 → 何时 → 多少 → 谁做的**）：
 *
 * - `version` **恒在场**——它是这份产物的身份，不是可选信息（唯一的无条件项）
 * - `chars` 缺失（`undefined`）→ **不给这个标签**（`0` 才是「真的 0 字」，见 `fmtChars`）
 * - 时间 / 模型缺失 → 不给（历史产物可能没有）
 *
 * ⚠️ **刻意不含 `edited`**：同一屏的工具条已经用 `markdownEdited[mode]` 显示「已手动编辑」
 * （warning 色，紧邻编辑按钮，语义是「你再点编辑会覆盖」），元数据条就在它正上方，
 * 再加一个「已编辑」就是同一信息相邻两行重复——而它并不比工具条多说什么。
 *
 * 这条「缺就不给、绝不补位」的判据直接来自设计规范：`design/cards.md` 的 A 信息卡
 * 「不要为了填满卡片而添加无关字段」。自证头里的 `params` / `sourceRefs` 是给 agent
 * 读的，铺给用户只是噪音，刻意不进这张表。
 *
 * ⚠️ `promptVersion` 2026-11-08 **进了**这张表：它回答「这一版是第几版提示词生成的」，
 * 复盘时是有效信息（此前 `PROMPT_VERSION` 手抄漏 bump 过，界面上又一处都不显示，
 * 等于两个断点）。历史产物读不出时**不显示这个 chip**，不补默认值。
 *
 * ⚠️ `usage` 2026-11-13 **进了**这张表：它回答「这次生成花了多少」，是用户唯一能
 * 在界面上看到成本的地方（此前只能去平台报表里按时间对）。排在最后是因为它比上面
 * 各项都「细」，放在模型名后面读起来像附注。缺失（历史产物 / 网关不回 usage）时
 * **整条不显示**；只有 token 没有金额时显示成 `↑1.2k ↓340`，不补金额。
 */
export function buildMetaChips(
  meta: MarkdownMeta | null | undefined,
  versions: MarkdownVersion[] = [],
): MetaChip[] {
  if (!meta) return []
  const row = pickVersionRow(meta, versions)

  const chips: MetaChip[] = [{ key: 'version', label: `v${meta.version}` }]

  const time = fmtTime(row?.createdAt ?? '')
  if (time) chips.push({ key: 'time', label: time })

  const chars = fmtChars(meta.chars ?? row?.chars)
  if (chars) chips.push({ key: 'chars', label: chars })

  const model = (meta.source || row?.source || '').trim()
  if (model) chips.push({ key: 'model', label: model })

  // 提示词版本（`prompt 26`）。⚠️ 只从版本行取，不从 `meta` 取：`MarkdownMeta`
  // 是「当前选中版」的视图，而提示词版本按**产物版本**走 —— 两者混在同一字段上，
  // 切版本时会出现「v1 的内容配 v3 的提示词版本」。
  const prompt = (row?.promptVersion ?? '').trim()
  if (prompt) chips.push({ key: 'prompt', label: `prompt ${prompt}` })

  // 用量（`↑1.2k ↓340 · ¥0.06`）。⚠️ 只从 `meta` 取、不从版本行取：
  // `usage` 不进 `artifact_versions`（那是契约测试钉住的精确键集），版本切换时
  // 自证条跟随 `meta` 重建，两者天然同版。
  // ⚠️ 缺金额时**只显示 token**，不补 `¥0.00`（理由见 `fmtCost`）。
  const usage = meta.usage
  if (usage) {
    const tok = `↑${fmtTokens(usage.input)} ↓${fmtTokens(usage.output)}`
    const cost = fmtCost(usage.cost)
    chips.push({ key: 'usage', label: cost ? `${tok} · ${cost}` : tok })
  }

  return chips
}
