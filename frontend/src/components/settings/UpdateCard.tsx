/**
 * 设置 → 通用 → 应用更新。
 *
 * **这张卡只做两件事**：把「有没有新版本」这件事变得看得见，以及让用户点一下就能装。
 * 检查、下载、验签、杀 sidecar、启安装器全在 Rust 侧（`src-tauri/src/updater.rs`），
 * 这里连一条重试判断都不写 —— 前端每多一处对流程的假设，就多一处与真实流程分叉的机会。
 *
 * ## 状态**不在**这张卡里（2026-11-08 起）
 *
 * `update` 那份状态住在 `store/updateSlice.ts`，这里是它的一个视图。原因很具体：
 * 下载 150MB 要好几分钟，用户会离开设置页；卡片一卸载，局部状态连同进度回调一起没了，
 * 而下载与安装照常推进，最后应用无声退出并重启。任务面板与顶栏是另外两个视图。
 *
 * ## 三步，不是一步（照 P-ai 的拆法）
 *
 * `available`（有新版本）→ `downloading`（下载，**安全**）→ `ready`（已下好，等人点）
 * → `installing`（重启安装，不可撤销）。
 *
 * 拆开的理由：下载好几钟、安装一秒完成但会退出应用。合成一步时用户没法「先下好，
 * 忙完再装」，而且安装失败要重下 150MB。所以**「下载」是一键**（它不伤害任何东西），
 * **「重启安装」才要确认**（它会关掉正在跑的生成与转录，且没有断点续传）。
 *
 * ## 三条写在代码里的判据
 *
 * ① **有任务在跑时不装**。安装会让应用退出，正在跑的生成 / 转录 / 导入全部断在半路，
 *    而它们没有任何断点续传。所以这时按钮禁用并写明原因，不是默默点不动。
 *
 * ② **「重启并安装」要两步确认**。不用原生 `confirm`（它在 Tauri 里是阻塞式的，
 *    且样式不可控）。
 *
 * ③ **失败一律就地显示，不弹 toast**。这张卡的整块界面就是为「现在是什么状态」而存在的，
 *    把错误丢到屏幕角落的 toast 里，等于让用户盯着一个停在「可更新」状态的卡片猜。
 *
 * ⚠️ 装完之后**没有「成功」这一步**：Windows 上插件用 `std::process::exit(0)` 起安装器，
 * 进程直接没了。所以「正在安装，应用即将重启」是这条链路能给的最后一句话。
 */
import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowUpRight, CheckCircle, Download, Loader2, RefreshCw, RotateCcw,
} from 'lucide-react'
import { isDesktop, RELEASES_URL } from '../../api/update'
import { formatSize } from '../../lib/format'
import { useStore } from '../../store'

/** Release notes 只给一段摘要 —— 完整清单在 Releases 页面上，这里要的是「改了什么」。 */
const NOTES_LIMIT = 600

function notesSummary(notes: string | null): string {
  if (!notes) return ''
  const text = notes.trim()
  return text.length > NOTES_LIMIT ? text.slice(0, NOTES_LIMIT).trimEnd() + '…' : text
}

export function UpdateCard() {
  const desktop = isDesktop()
  // 状态全部来自 slice；本组件只留一个纯界面态（重启前的两步确认）。
  // ⚠️ 连「跳过的版本号」也在 store 里：顶栏提示要跟着它变（见 updateSlice 的注释）。
  const update = useStore(s => s.update)
  const checkNow = useStore(s => s.checkNow)
  const startDownload = useStore(s => s.startDownload)
  const applyUpdate = useStore(s => s.applyUpdate)
  const skipVersion = useStore(s => s.skipVersion)
  const unskipVersion = useStore(s => s.unskipVersion)
  // 重启前的两步确认。
  const [confirming, setConfirming] = useState(false)

  const { phase } = update
  const locked = phase === 'downloading' || phase === 'ready' || phase === 'installing'
  const error = phase === 'failed' ? update.error : null
  const pct = phase === 'downloading' && update.total
    ? Math.min(100, Math.round((update.downloaded / update.total) * 100))
    : null

  const anyTaskRunning = useStore(s =>
    Object.values(s.processingTasks).some(t => t.status === 'running'))

  // 打开这张卡就查一次。手动点开的「通用」页面，用户想要的正是此刻的结论；
  // 而静默检查的 24 小时节流属于启动链（`App.tsx`），不是这里。
  // ⚠️ 用 ref 而非 state 当守卫：开发模式的 StrictMode 会把 effect 跑两遍。
  // ⚠️ 下载 / 就绪 / 安装中**不重查** —— 那会把正在推进的进度状态换成「检查中」。
  const autoRan = useRef(false)
  useEffect(() => {
    if (!desktop || autoRan.current || locked) return
    autoRan.current = true
    void checkNow()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop])

  function onSkip() {
    const v = update.version
    if (!v) return
    skipVersion(v)
    setConfirming(false)
  }

  const isSkipped = !!update.version && update.version === update.skippedVersion

  // 非桌面版（浏览器里 `npm run dev`）—— 不渲染任何按钮，只说明为什么没有。
  if (!desktop) {
    return (
      <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-3" data-update-card>
        <div className="flex items-center gap-2 text-[14px]">
          <Download size={16} className="text-muted shrink-0" />
          <span className="font-medium">应用更新</span>
        </div>
        <p className="text-[12px] text-muted leading-relaxed">
          在浏览器里打开时无法更新 —— 应用更新需要桌面版的安装环境（下载、验签、启动安装器）。
          用「检查更新」这类操作请打开桌面版 LiMo。
        </p>
      </div>
    )
  }

  return (
    <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4" data-update-card>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[14px]">
            <Download size={16} className="text-muted shrink-0" />
            <span className="font-medium">应用更新</span>
            {update.currentVersion && (
              <span className="text-[11px] text-muted">当前版本 v{update.currentVersion}</span>
            )}
          </div>
          <p className="text-[12px] text-muted leading-relaxed">
            从 GitHub 发布页检查并安装新版本。更新包会先验签再安装，应用会自动重启。
          </p>
        </div>
        <button
          onClick={() => { void checkNow() }}
          disabled={phase === 'checking' || locked}
          data-update-check
          className="shrink-0 px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5 disabled:opacity-50"
        >
          {phase === 'checking' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {phase === 'checking' ? '检查中' : '检查更新'}
        </button>
      </div>

      {/* 已是最新 */}
      {phase === 'up-to-date' && (
        <p className="text-[13px] flex items-center gap-2 text-muted" data-update-uptodate>
          <CheckCircle size={14} className="text-accent shrink-0" />
          已是最新版本
        </p>
      )}

      {/* 发现新版本 —— 只有一个动作：下载。下载不伤害任何东西，所以不用确认。 */}
      {phase === 'available' && update.version && (
        <div className="space-y-3" data-update-available>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-medium">发现新版本 v{update.version}</span>
            {update.date && <span className="text-[11px] text-muted">{update.date.slice(0, 10)}</span>}
            {isSkipped && (
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-elevated text-muted border border-border-light">
                已跳过
              </span>
            )}
          </div>

          {notesSummary(update.notes) && (
            <pre className="text-[12px] text-muted leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto rounded-lg border border-border-light p-3 font-sans">
              {notesSummary(update.notes)}
            </pre>
          )}

          {/* 已跳过：只给「重新看看」与「去发布页」。不摆「下载」——
              用户正是表达过「这次不要」，再把按钮摆在最显眼处等于没听。 */}
          {isSkipped ? (
            <div className="flex flex-wrap items-center gap-3">
              <button onClick={() => unskipVersion()} data-update-unskip
                className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default">
                取消跳过
              </button>
              <ReleasesLink />
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => { void startDownload() }} data-update-install
                className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover inline-flex items-center gap-1.5">
                <Download size={12} />
                下载更新
              </button>
              <button onClick={onSkip} data-update-skip
                className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default">
                跳过此版本
              </button>
            </div>
          )}
        </div>
      )}

      {/* 下载中 —— 进度条只在**下载**阶段有意义。 */}
      {phase === 'downloading' && (
        <div className="space-y-1.5" data-update-progress>
          <div className="flex items-center justify-between text-[12px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <Loader2 size={12} className="animate-spin" />
              正在下载更新包
            </span>
            <span>
              {update.total
                ? `${formatSize(update.downloaded)} / ${formatSize(update.total)}`
                : formatSize(update.downloaded)}
              {pct !== null && ` · ${pct}%`}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-surface-hover overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              // 总长度未知时给一条 30% 的固定宽度：`pct === null` 那条分支
              // 不是错误，是清单没给 `content-length`，进度条只能表达「在动」。
              style={{ width: pct === null ? '30%' : `${pct}%` }}
            />
          </div>
          {update.viaProxy && (
            <p className="text-[11px] text-muted">直连不通，正在通过中转线路下载。</p>
          )}
          <p className="text-[11px] text-muted">下载期间可以继续使用，安装时才会重启应用。</p>
        </div>
      )}

      {/* 已下载 —— 决策点在这里，不在下载前。 */}
      {phase === 'ready' && (
        <div className="space-y-2" data-update-ready>
          <p className="text-[13px] flex items-center gap-2">
            <CheckCircle size={14} className="text-accent shrink-0" />
            更新 v{update.version} 已下载完成，可以随时安装
          </p>
          {confirming ? (
            <div className="rounded-lg border border-border-default p-3 space-y-2" data-update-confirm>
              <p className="text-[12px] text-muted leading-relaxed">
                安装会<strong className="font-medium">关闭并重启 LiMo</strong>。
                {anyTaskRunning
                  ? '当前还有任务在运行，现在安装会让它们中断在半路 —— 按钮先禁用，等任务完成再点。'
                  : '如果此刻正在生成笔记或转录，它们会中断。'}
              </p>
              <div className="flex gap-2">
                <button onClick={() => { void applyUpdate() }} disabled={anyTaskRunning} data-update-install-start
                  className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50">
                  开始安装
                </button>
                <button onClick={() => setConfirming(false)}
                  className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default">
                  稍后
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => setConfirming(true)} disabled={anyTaskRunning}
                title={anyTaskRunning ? '有任务正在运行，完成后再更新' : undefined}
                data-update-install-start
                className="px-3 py-1.5 text-[12px] rounded-lg bg-accent text-on-accent hover:bg-accent-hover inline-flex items-center gap-1.5 disabled:opacity-50">
                <RotateCcw size={12} />
                重启并安装
              </button>
              {anyTaskRunning && (
                <span className="text-[11px] text-warning">有任务正在运行</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* 安装中 —— 进程马上会被安装器接管，这里给最后一句。 */}
      {phase === 'installing' && (
        <div className="space-y-1.5" data-update-progress>
          <span className="text-[12px] text-muted inline-flex items-center gap-1.5">
            <Loader2 size={12} className="animate-spin" />
            正在安装，应用即将重启
          </span>
        </div>
      )}

      {error && (
        <div className="space-y-1.5" data-update-error>
          <p className="text-[12px] text-warning flex items-start gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {error}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            {/* 失败后唯一的卡片内出路：重下一次（下载失败时包没进来；
                安装失败时 Rust 侧那份已被取走，同样要重下）。 */}
            {update.version && (
              <button onClick={() => { void startDownload() }} data-update-retry
                className="px-3 py-1.5 text-[12px] rounded-lg border border-border-default inline-flex items-center gap-1.5">
                <RotateCcw size={12} />
                重新下载
              </button>
            )}
            <ReleasesLink />
          </div>
        </div>
      )}
    </div>
  )
}

/** 「去发布页手动下载」—— 更新失败时唯一还有效的出路，所以每一处失败旁边都有它。
 *  ⚠️ 用**直连**地址（`RELEASES_URL`），不能带中转让前缀：中转只代理资源文件。 */
function ReleasesLink() {
  return (
    <a
      href={RELEASES_URL}
      target="_blank"
      rel="noreferrer"
      data-update-releases
      className="text-[12px] text-accent hover:underline inline-flex items-center gap-1"
    >
      在浏览器中打开 Releases
      <ArrowUpRight size={12} />
    </a>
  )
}
