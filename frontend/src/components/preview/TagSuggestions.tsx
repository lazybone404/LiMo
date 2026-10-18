import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, Tag } from 'lucide-react'
import { useStore } from '../../store'
import { batchTranscripts } from '../../api/transcripts'
import { getErrorMessage } from '../../api/core'
import { suggestTags } from '../../lib/tag-suggest'

/**
 * 标签建议条 —— 挂在产物自证条**同一行**后面，从库里**已有的**标签中挑出正文里
 * 真的出现过的那几个，点一下就加。
 *
 * ## 三条纪律（与 `ArtifactMetaBar` 同款）
 *
 * 1. **接入点与 `CardSections` / `MarkerContent` 同级**，绝不放 `MarkdownPreview` 内部
 *    —— 那会扩散到 Excel 数据摘要与原始转录。
 * 2. **不参与导出**：它是一次性的操作入口，不是内容。正文一个字都不碰。
 * 3. **不落盘**：建议由 `suggestTags` 从正文现算，见 [lib/tag-suggest.ts](../../lib/tag-suggest.ts)
 *    文件头「刻意不落盘」的理由。
 *
 * ## 为什么采纳走 `/transcripts/batch` 而不是 `/classify/commit`
 *
 * `commit` 走 `store.apply_classifications`，那里是
 * `e["folderId"] = a.get("folder_id") or ""` —— **无条件覆盖**。只带 tags 不带
 * folder_id 会把这篇文档从它所在的文件夹里踢回未分类，而且界面上只会看到
 * 「标签加上了」，看不出它还顺手改了归属。
 * `batch` 的 `tag` action 走 `update_transcripts_tags(..., 'add')`，只动标签。
 */
export function TagSuggestions({ content }: { content: string }) {
  const currentDocId = useStore((s) => s.currentDocId)
  const docs = useStore((s) => s.docs)
  const setDocs = useStore((s) => s.setDocs)
  const addToast = useStore((s) => s.addToast)

  /** 本次会话内已采纳的标签 —— 补上「后端已写、但 `docs` 还没重新拉」的那段空窗 */
  const [accepted, setAccepted] = useState<string[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  // 切文档就把本地记账清掉（同 `ArtifactMetaBar`：那些数都是这篇的）
  useEffect(() => { setAccepted([]) }, [currentDocId])

  const doc = useMemo(() => docs.find((d) => d.id === currentDocId), [docs, currentDocId])

  // 词表 = 全库标签去重。`docs` 是全量拉取的（含 tags），不需要另发请求。
  const vocab = useMemo(() => {
    const seen = new Set<string>()
    for (const d of docs) for (const t of d.tags || []) if (t.trim()) seen.add(t)
    return [...seen]
  }, [docs])

  const suggestions = useMemo(() => {
    if (!currentDocId) return []
    // 超长正文（原始转录可达数万字）截断：标签命中基本都在开头，尾部对判定的
    // 边际价值极低，而几百个标签 × 全文正则是同步阻塞主线程的。
    const body = content.length > 20000 ? content.slice(0, 20000) : content
    return suggestTags(
      doc?.title ?? '',
      body,
      vocab,
      [...(doc?.tags ?? []), ...accepted],
    )
  }, [content, doc?.title, doc?.tags, vocab, accepted, currentDocId])

  const accept = useCallback(async (tag: string) => {
    if (!currentDocId || busy) return
    setBusy(tag)
    try {
      const r = await batchTranscripts({ action: 'tag', ids: [currentDocId], tags: [tag], tagMode: 'add' })
      if ((r.failed || []).some((f) => f.id === currentDocId)) {
        throw new Error('标签未能写入')
      }
      // ⚠️ `await` 期间用户可能已经切到另一篇文档。`setAccepted` 是**本组件**的
      // 本地记账（切文档时会被 `useEffect` 清空），把上一篇的标签记进新文档，
      // 会让新文档里同一个词被 `suggestTags` 的 `existing` 过滤掉——界面上
      // 只是「少了一条建议」，没有任何报错。`setDocs` 不受影响：它用闭包里的
      // 旧 id 定点更新那一篇，本来就是对的。
      if (useStore.getState().currentDocId === currentDocId) {
        setAccepted((a) => [...a, tag])
      }
      // 乐观更新：文档库列表里的标签 chips 立刻跟上（与 DocLibrary 的批量打标签同款写法）
      setDocs(useStore.getState().docs.map((d) =>
        d.id === currentDocId ? { ...d, tags: [...new Set([...(d.tags || []), tag])] } : d))
      addToast(`已添加标签「${tag}」`)
    } catch (err) {
      addToast(getErrorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }, [currentDocId, busy, setDocs, addToast])

  if (!suggestions.length) return null

  return (
    <div data-tag-suggestions className="flex flex-wrap items-center gap-1.5 min-w-0">
      <span
        className="inline-flex items-center gap-1 text-[11px] leading-none text-disabled select-none"
        title="来自库里已有的标签，正文里出现过"
      >
        <Tag size={11} className="shrink-0" />
        建议标签
      </span>
      {suggestions.map(({ tag, inTitle }) => (
        <button
          key={tag}
          type="button"
          data-tag-suggestion={tag}
          data-in-title={inTitle ? '1' : undefined}
          disabled={busy !== null}
          onClick={() => void accept(tag)}
          title={inTitle ? '标题里出现过 · 点击添加' : '正文里出现过 · 点击添加'}
          className="inline-flex items-center gap-1 h-[21px] pl-1 pr-2 rounded-md
                     border border-border-light text-[11px] leading-none text-dimmed
                     transition-colors hover:border-accent/40 hover:text-accent
                     disabled:opacity-50 disabled:cursor-default"
        >
          <Plus size={10} className="shrink-0 text-disabled" />
          {tag}
        </button>
      ))}
    </div>
  )
}
