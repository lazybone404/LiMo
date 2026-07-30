import { useState } from 'react'
import { ChevronDown, ChevronUp, ExternalLink } from 'lucide-react'
import { appUrl } from '../../api/client'

interface VideoBannerProps {
  title?: string
  coverUrl?: string
  uploader?: string
  platform?: string
  sourceUrl?: string
  createdAt?: string
  sourceType?: string
}

const PLATFORM_LABELS: Record<string, string> = {
  bilibili: '哔哩哔哩', youtube: 'YouTube', douyin: '抖音',
}

/** 内容区顶部横幅 — 支持折叠，沉浸式阅读不遮挡 */
export function VideoBanner({ title, coverUrl, uploader, platform, sourceUrl, createdAt: _createdAt, sourceType }: VideoBannerProps) {
  const [collapsed, setCollapsed] = useState(false)
  const platformLabel = PLATFORM_LABELS[platform || ''] || platform || ''
  const hasCover = !!coverUrl
  const showMeta = title || uploader || platformLabel

  // 折叠态：窄条，仅标题 + 展开按钮
  if (collapsed) {
    return (
      <div className="relative mb-4 overflow-hidden rounded-lg bg-surface-hover">
        <div className="flex items-center gap-2 px-3 py-1.5">
          <button
            onClick={() => setCollapsed(false)}
            className="shrink-0 flex items-center justify-center w-5 h-5 rounded text-muted hover:text-foreground hover:bg-surface transition-colors"
            title="展开视频信息"
          >
            <ChevronDown size={14} />
          </button>
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

  return (
    <div className="relative mb-4 overflow-hidden rounded-lg">
      {/* 模糊背景封面 */}
      <div className="absolute inset-0">
        {hasCover ? (
          <img
            src={appUrl(coverUrl)}
            alt=""
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover blur-xl brightness-[0.35] scale-110"
          />
        ) : (
          <div className="h-full w-full bg-gradient-to-r from-blue-600 to-indigo-700" />
        )}
      </div>

      {/* 折叠按钮 — 右上角 */}
      <button
        onClick={() => setCollapsed(true)}
        className="absolute top-1.5 right-1.5 z-10 flex items-center justify-center w-5 h-5 rounded-full bg-white/10 text-white/60 hover:bg-white/20 hover:text-white/90 transition-colors"
        title="收起视频信息"
      >
        <ChevronUp size={13} />
      </button>

      {/* 内容层 */}
      <div className="relative flex items-center gap-3 px-4 py-3">
        {/* 封面缩略图 */}
        {hasCover && (
          <img
            src={appUrl(coverUrl)}
            alt={title || ''}
            referrerPolicy="no-referrer"
            className="h-12 w-20 shrink-0 rounded-md object-cover shadow-md"
          />
        )}

        {/* 文字信息 */}
        <div className="min-w-0 flex-1">
          {title && (
            <h2 className="truncate text-sm font-bold text-white pr-5" title={title}>
              {title}
            </h2>
          )}
          {showMeta && (
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-white/70">
              {uploader && <span>{uploader}</span>}
              {uploader && platformLabel && <span className="text-white/40">·</span>}
              {platformLabel && <span>{platformLabel}</span>}
              {(uploader || platformLabel) && sourceType && <span className="text-white/40">·</span>}
              {sourceType && <span className="text-white/50">{sourceType}</span>}
            </div>
          )}
          {!showMeta && sourceType && (
            <div className="mt-0.5 text-xs text-white/50">{sourceType}</div>
          )}
        </div>

        {/* 跳转原链接 */}
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex shrink-0 items-center gap-1 rounded-full bg-white/15 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur-sm transition-colors hover:bg-white/25"
          >
            <ExternalLink className="h-3 w-3" />
            <span>原链接</span>
          </a>
        )}
      </div>
    </div>
  )
}
