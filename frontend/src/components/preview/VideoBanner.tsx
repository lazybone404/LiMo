import { ExternalLink } from 'lucide-react'
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

/** 内容区顶部横幅 — 对标 BiliNote VideoBanner */
export function VideoBanner({ title, coverUrl, uploader, platform, sourceUrl, createdAt: _createdAt, sourceType }: VideoBannerProps) {
  const platformLabel = PLATFORM_LABELS[platform || ''] || platform || ''
  const hasCover = !!coverUrl
  const showMeta = title || uploader || platformLabel

  return (
    <div className="relative mb-6 overflow-hidden rounded-xl">
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

      {/* 内容层 */}
      <div className="relative flex items-center gap-4 px-5 py-4">
        {/* 封面缩略图 */}
        {hasCover && (
          <img
            src={appUrl(coverUrl)}
            alt={title || ''}
            referrerPolicy="no-referrer"
            className="h-16 w-28 shrink-0 rounded-md object-cover shadow-md"
          />
        )}

        {/* 文字信息 */}
        <div className="min-w-0 flex-1">
          {title && (
            <h2 className="truncate text-base font-bold text-white" title={title}>
              {title}
            </h2>
          )}
          {showMeta && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-white/70">
              {uploader && <span>{uploader}</span>}
              {uploader && platformLabel && <span className="text-white/40">·</span>}
              {platformLabel && <span>{platformLabel}</span>}
              {(uploader || platformLabel) && sourceType && <span className="text-white/40">·</span>}
              {sourceType && <span className="text-white/50">{sourceType}</span>}
            </div>
          )}
          {!showMeta && sourceType && (
            <div className="mt-1 text-sm text-white/50">{sourceType}</div>
          )}
        </div>

        {/* 跳转原链接 */}
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex shrink-0 items-center gap-1.5 rounded-full bg-white/15 px-3 py-1.5 text-xs font-medium text-white backdrop-blur-sm transition-colors hover:bg-white/25"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            <span>原链接</span>
          </a>
        )}
      </div>
    </div>
  )
}
