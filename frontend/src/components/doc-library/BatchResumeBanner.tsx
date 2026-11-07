/**
 * 文档库顶部的「上次批量队列没跑完」横幅（2026-11-07）。
 *
 * 批量转录与批量生成都是串行队列，刷新页面之后队列连同它的状态一起没了。
 * 快照由 `lib/batch-queue.ts` 落在 localStorage，这里只负责把「还有几篇」说清楚，
 * 并给出一个继续跑的入口。
 *
 * ## 点「继续运行」打开的是确认对话框，不是直接开跑
 *
 * 一次批量生成是若干次计费调用，一次批量转录要下载与跑 ASR。用户刷新页面之后
 * 第一下点击就重新花一遍钱，不合适。所以按钮只调 `open()`（零请求），
 * 清单原样回到确认对话框，按「开始」才发第一个请求。
 *
 * ## 两种队列各占一条横幅
 *
 * 不合并成「共 5 项未完成 · [继续运行]」：那样一个按钮要同时打开两个对话框，
 * 而两个队列是各自确认、各自取消的。两条横幅各带一个「忽略」，清掉一条不动另一条。
 */
import { History, X } from 'lucide-react'

interface RowProps {
  /** 未完成的篇数。0 时不渲染。 */
  count: number
  /** 动作名，用于文案：「还有 3 篇没转录完」。 */
  verb: string
  /** `data-*` 落点，测试与将来的埋点靠它区分两种队列。 */
  kind: 'transcribe' | 'generate'
  onResume: () => void
  onDismiss: () => void
}

function ResumeRow({ count, verb, kind, onResume, onDismiss }: RowProps) {
  if (count <= 0) return null
  return (
    <div data-batch-resume={kind}
      className="mx-3 mt-2 mb-1 rounded-lg bg-accent/10 px-3 py-2 text-[11px] text-accent leading-relaxed">
      <div className="flex items-start gap-1.5">
        <History size={12} className="mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <span className="font-medium">还有 {count} 篇没{verb}完</span>
          <span className="opacity-80"> · 刷新页面后队列已停下</span>
        </div>
        <button onClick={onDismiss} title="不再提示"
          className="shrink-0 opacity-60 hover:opacity-100">
          <X size={12} />
        </button>
      </div>

      <button onClick={onResume} data-batch-resume-go
        className="mt-1.5 rounded-md border border-accent/40 px-2 py-0.5 hover:bg-accent/10">
        继续运行
      </button>
    </div>
  )
}

interface Props {
  transcribeCount: number
  generateCount: number
  onResumeTranscribe: () => void
  onResumeGenerate: () => void
  onDismissTranscribe: () => void
  onDismissGenerate: () => void
}

export function BatchResumeBanner({
  transcribeCount, generateCount,
  onResumeTranscribe, onResumeGenerate,
  onDismissTranscribe, onDismissGenerate,
}: Props) {
  return (
    <>
      <ResumeRow count={transcribeCount} verb="转录" kind="transcribe"
        onResume={onResumeTranscribe} onDismiss={onDismissTranscribe} />
      <ResumeRow count={generateCount} verb="生成" kind="generate"
        onResume={onResumeGenerate} onDismiss={onDismissGenerate} />
    </>
  )
}
