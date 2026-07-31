interface VideoBannerProps {
  title?: string
  sourceUrl?: string
  sourceType?: string
}

/** 内容区顶部窄条 — 仅显示标题 + 原链接，不抢占阅读焦点 */
export function VideoBanner({ title, sourceUrl, sourceType }: VideoBannerProps) {
  return (
    <div className="mb-4 overflow-hidden rounded-lg bg-surface-hover">
      <div className="flex items-center gap-2 px-3 py-1.5">
        {title ? (
          <span className="truncate text-xs font-medium text-foreground" title={title}>{title}</span>
        ) : (
          <span className="text-xs text-dimmed">{sourceType || '视频'}</span>
        )}
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 ml-auto text-[10px] text-muted hover:text-accent transition-colors"
          >
            原链接
          </a>
        )}
      </div>
    </div>
  )
}
