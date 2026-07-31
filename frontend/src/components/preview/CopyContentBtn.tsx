/**
 * 复制内容按钮 — 内容源由 CONTENT_GETTERS 推导（TabDef.contentSource 驱动）。
 * ctx 不带 readStreamBuffer（复制不复制流式缓冲，读已保存内容）。
 */
import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { useStore } from '../../store'
import { getContentForView, type ContentGetterCtx } from '../../lib/contentSources'

export function CopyContentBtn({ ctx }: { ctx: ContentGetterCtx }) {
  const [copied, setCopied] = useState(false)
  const addToast = useStore(s => s.addToast)

  const getContent = () => getContentForView(ctx.activeView, ctx)

  const handleCopy = () => {
    const text = getContent()
    if (!text) { addToast('没有可复制的内容', 'error'); return }
    navigator.clipboard.writeText(text).then(
      () => { setCopied(true); addToast('已复制到剪贴板'); setTimeout(() => setCopied(false), 1500) },
      () => addToast('复制失败', 'error'),
    )
  }

  if (!getContent()) return null

  return (
    <button onClick={handleCopy}
      className="px-3 py-1.5 text-[12px] rounded-full transition-colors text-dimmed hover:bg-surface-hover"
      title="复制内容"
    >{copied ? <Check size={14} className="text-success" /> : <Copy size={14} />}</button>
  )
}
