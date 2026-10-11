/**
 * `MarkdownView`（接口返回形状）→ `MarkdownMeta`（预览区状态）的**唯一映射落点**。
 *
 * 为什么要有这个文件：这段映射此前在 5 处各写了一份内联对象字面量
 * （App.tsx 恢复态 / GenPanel 重新转录后 / GenPanel 生成完成后 / EpubInterpretPanel
 * 解读完成后 / PreviewArea 保存编辑后）。它们**长得几乎一样但各写各的**——于是
 * 2026-10-10 给版本列表加 `chars`/`source` 时，5 处里只有一处会记得改，
 * 其余 4 处静默少字段：版本切换器的触发按钮显示不出字数，且**零报错**。
 * 这就是模式 #54 说的「成对逻辑必须同源」——形状的家在这里，构造的家也在这里。
 */
import type { MarkdownView } from '../api/core'
import type { MarkdownMeta } from '../store/types'

/**
 * ⚠️ `edited` 用 `!!` 归一：后端从文件头派生后返回的是真 boolean，但历史/降级路径
 * 可能是 `undefined`——`MarkdownMeta.edited` 的消费方（重新生成前的「有未保存编辑」
 * 提示）把它当布尔用，留着 `undefined` 会让判据在两条路径上不同。
 *
 * ⚠️ `chars` / `source` **原样透传（可能是 `undefined`）**：`undefined` 的语义是
 * 「后端没给」，不是「0 字 / 未知模型」——补成默认值等于伪造数据。
 *
 * ⚠️ `followUps` 同理原样透传，且**不做「空数组当缺省」的归一**：后端是「有才给键、
 * 空不给」（`store.get_markdowns_by_transcript`），两者在这里必须保持可区分 ——
 * `[]` 与 `undefined` 的消费方（PreviewArea 的渲染条件）只判后者。
 */
export function toMarkdownMeta(md: MarkdownView): MarkdownMeta {
  return {
    id: md.id,
    style: md.style,
    mode: md.mode,
    version: md.version,
    edited: !!md.edited,
    chars: md.chars,
    source: md.source,
    followUps: md.followUps,
  }
}
