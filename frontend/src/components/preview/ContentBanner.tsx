import type { ReactNode } from 'react'

interface ContentBannerProps {
  title?: string
  sourceUrl?: string
  sourceType?: string
  /** 右侧动作槽（编辑按钮 / 已手动编辑徽章）。
   *  ⚠️ 与「原链接」**同一行、同一组右对齐**：两者都是「针对这个文档/这一版」的动作，
   *  分成两行只会把正文往下推。 */
  actions?: ReactNode
}

/** 内容区顶部窄条 — 标题 + 原链接 + 右侧动作，不抢占阅读焦点。
 *
 *  ⚠️ 原名 `VideoBanner`（只在 video 下渲染）。编辑按钮从正文上方那一行搬进来之后，
 *  非视频的 notes / polished 也必须能显示这一条 —— 否则「编辑」在非视频文档上没有
 *  落点。**什么时候渲染**由调用方（`PreviewArea` 的 `showContentBanner`）决定，
 *  本组件只负责这一行的形状。
 */
export function ContentBanner({ title, sourceUrl, sourceType, actions }: ContentBannerProps) {
  return (
    <div className="mb-4 overflow-hidden rounded-lg bg-surface-hover">
      <div className="flex items-center gap-2 px-3 py-1.5">
        {title ? (
          <span className="truncate text-xs font-medium text-foreground" title={title}>{title}</span>
        ) : (
          <span className="text-xs text-dimmed">{sourceType || '视频'}</span>
        )}
        {(sourceUrl || actions) && (
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {sourceUrl && (
              <a
                href={sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-muted hover:text-accent transition-colors"
              >
                原链接
              </a>
            )}
            {actions}
          </div>
        )}
      </div>
    </div>
  )
}
