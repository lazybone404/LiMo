/**
 * PendingTranscript — 「只收纳了、正文还没提取」的文档在预览区看到的那张卡片。
 *
 * ## 它取代的是「占位正文」的渲染
 *
 * 收纳模式下 `rawText` 是一段占位 Markdown（`<!-- limo-pending:… -->` + 一句说明），
 * 直接渲染出来是**完全正常的内容块** —— 用户会以为转录已经完成了，往下找生成按钮，
 * 然后再被生成前置拦截挡一次。所以 `PreviewArea` 的 `case 'transcript'` 在**所有
 * 分支之前**判 `transcribeState === 'pending'`，命中就交到这里。
 *
 * ## 转录动作走 `useStartTranscription`，不自己调接口
 *
 * 「转录完要把 `docs` 里这条改回 `done`」这件事有两个入口（这里是新的那个，
 * 另一个是生成面板的「⟳ 重新转录」）。两份实现迟早会分叉，而分叉的形态是
 * 「后端已写回真正文、预览区也显示了，列表徽章还挂着『待转录』」—— 没有地方报错。
 * 所以只留一份，见 `hooks/useStartTranscription.ts`。
 *
 * ⚠️ **转录进行中时这个组件不会渲染**：`PreviewArea::isViewProcessing` 排在前面，
 * transcript tab 的 `processingKinds` 含 `'retranscribe'` ⇒ 那时显示的是
 * `ProcessingPlaceholder`（带已用时）。这里只管**空闲态**，不必自己维护进度条。
 */
import { useState } from 'react'
import { FileSearch } from 'lucide-react'
import type { DocEntry } from '../../api/core'
import { EmptyState } from './EmptyState'
import { useStartTranscription } from '../../hooks/useStartTranscription'

export function PendingTranscript({ doc }: { doc: DocEntry }) {
  const { start } = useStartTranscription()
  // 失败只在卡片里说一句，不再往上抛 —— 面板那边的红框是生成面板的形态，
  // 这张卡片自己有地方放这句话。
  const [error, setError] = useState<string | null>(null)

  return (
    <EmptyState
      icon={<FileSearch size={50} className="text-disabled mx-auto opacity-20" />}
      title={<h1 className="text-[22px] font-semibold">这篇已经收进来了，正文还没提取</h1>}
      description={
        <>
          导入时只做了收纳（视频默认如此，可在设置里改成导入即转录）。
          <br />
          点下面的按钮会重新拉取原始内容并提取正文，之后就能生成笔记了。
        </>
      }
      hint={error ? <span className="text-warning">{error}</span> : undefined}
      action={
        <button
          onClick={() => {
            setError(null)
            void start(doc.id, { onError: setError, successToast: '转录完成' })
          }}
          className="px-5 py-2 text-[13px] font-medium text-accent bg-accent/10 rounded-lg hover:bg-accent/20 transition-colors"
        >
          ▶ 开始转录
        </button>
      }
    />
  )
}
