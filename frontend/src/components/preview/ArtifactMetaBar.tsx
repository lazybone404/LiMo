/**
 * 产物自证条 —— 预览区正文上方的**一横排小标签**（图标 + 短文本）。
 *
 * 它回答「这段内容是哪一版 / 什么时候生成的 / 谁生成的 / 有没有被手动改过」——
 * 即 `<!-- limo-meta {…} -->` 自证头在**读者侧**的等价物。那行注释 2026-10-11 起
 * 渲染层一律不显示（`lib/markdown-plugins.ts::rehypeStripComments`），信息也随之
 * 从视野里消失；本组件把用户真正需要的那几项拎出来，以低干扰的徽章形态常驻。
 *
 * ⚠️ **不是把自证头 JSON 全量铺开**：`params` / `promptVersion` / `sourceRefs` 是给
 * agent 读的，铺给用户只是噪音。取哪几项、什么顺序，判据在 `lib/artifactMeta.ts`
 * 的 `buildMetaChips`（本组件只负责「数据 → 图标 + 外观」）。
 *
 * ⚠️ **接入点纪律**：与 `CardSections` / `MarkerContent` **同级**，只挂在 notes /
 * polished 的**只读态**。**绝不放 `MarkdownPreview` 内部**——那会静默扩散到 Excel
 * 数据摘要与原始转录两处（它们没有版本概念，挂上去就是「v1 · 0字」这种假信息）。
 *
 * ⚠️ **不参与导出**：这是视图装饰，与工具栏的版本切换器、编辑按钮同类，不属于
 * 「用户在预览栏看到的内容」。导出物里的元信息仍由 `.md` / `.html` 保留的自证头注释
 * 承载（`export.py::_pick_artifact` 用 `raw_content`）。
 */
import { AlignLeft, Clock, Cpu, History, type LucideIcon } from 'lucide-react'
import type { MarkdownMeta, MarkdownVersion } from '../../store/types'
import { buildMetaChips, type MetaChipKey } from '../../lib/artifactMeta'

/** 每个标签位配一个图标。⚠️ 与 `MetaChipKey` 是**穷尽映射**——新增一种 chip 而忘了
 *  配图标会在这里报类型错，不会静默渲染成一个没有图标、与其它标签不齐的方块。 */
const CHIP_ICONS: Record<MetaChipKey, LucideIcon> = {
  version: History,
  time: Clock,
  chars: AlignLeft,
  model: Cpu,
}

export interface ArtifactMetaBarProps {
  meta: MarkdownMeta | null
  versions?: MarkdownVersion[]
}

export function ArtifactMetaBar({ meta, versions = [] }: ArtifactMetaBarProps) {
  const chips = buildMetaChips(meta, versions)
  if (!chips.length) return null

  return (
    <div
      data-artifact-meta
      /* 纯布局，**不带外边距** —— 间距由调用方决定（它同时用在只读态正文上方，
         将来也可能挂在别处；自带 margin 的组件在同一处改版时会变成要覆盖的敌人）。 */
      className="flex flex-wrap items-center gap-1.5 min-w-0"
    >
      {chips.map((c) => {
        const Icon = CHIP_ICONS[c.key]
        return (
          <span
            key={c.key}
            data-meta-chip={c.key}
            title={c.key === 'model' ? '生成模型' : undefined}
            className="inline-flex items-center gap-1 h-[21px] pl-1.5 pr-2 rounded-md
                       border border-border-light text-[11px] leading-none text-dimmed
                       transition-colors hover:border-border-default hover:text-foreground"
          >
            <Icon size={11} className="shrink-0 text-disabled" />
            <span className={c.key === 'version' ? 'font-mono' : undefined}>{c.label}</span>
          </span>
        )
      })}
    </div>
  )
}
