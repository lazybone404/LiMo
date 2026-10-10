"""
YouTube 字幕直拉 — youtube-transcript-api，无需下载视频。
对齐 BiliNote downloaders/youtube_subtitle.py + demo/pipeline.py
策略：人工字幕优先 → 自动字幕 → 任意可用语言

⚠️ **API 版本契约：youtube-transcript-api 1.x**（pyproject 钉 `>=1.0`，实测 1.2.4）。
1.x 相对 0.6.x 是破坏性重写，三处形状全变：
  ① `list()` / `fetch()` 从**类方法**变成**实例方法**（经类调用必抛 TypeError）
  ② 构造改吃 `proxy_config` / `http_client` —— 代理与 cookie 都从这里进，
     **不再需要改 os.environ**（那种写法在并发下会互相踩）
  ③ 片段对象是 `FetchedTranscriptSnippet` **dataclass**（`.text` / `.start` /
     `.duration`），不是 dict、也不支持 `[]`

`tests/test_youtube_subtitle.py::test_api_shape_contract` 钉住这三条，依赖漂移即红。

2026-10-10 修复：此前代码按 0.6.x 写，`YouTubeTranscriptApi.list(vid, cookies=...)`
每次必抛 TypeError、又被 `except Exception` 吞掉 → 字幕路径**从未成功过**，静默
回退到 yt-dlp 下载（用户感受为「油管导入特别慢／像是不通」）。实测代理本身是通的。
"""

import json
import logging
import re
import urllib.request

import requests
import youtube_transcript_api as _yta
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

logger = logging.getLogger(__name__)

# 语言优先级：中文 → 英文 → 日文
_PRIORITY_LANGS = ["zh-Hans", "zh", "zh-CN", "zh-TW", "en", "en-US", "ja"]

# ── 失败分型：按「用户能采取的行动」划分，不按「哪行代码失败」（模式 #53）──
# ① 视频确实没有字幕 / 视频本身不可用 → **正常降级**：静默 return None，上层改走 ASR。
_NO_SUBTITLE_ERRORS = tuple(
    c for c in (
        getattr(_yta, "TranscriptsDisabled", None),
        getattr(_yta, "NoTranscriptFound", None),
        getattr(_yta, "VideoUnavailable", None),
        getattr(_yta, "VideoUnplayable", None),
        getattr(_yta, "InvalidVideoId", None),
        getattr(_yta, "AgeRestricted", None),
    ) if isinstance(c, type)
)
# ② 被反爬 / 网络拦下 → 「检查代理」是用户唯一能做的动作，**必须显式冒泡**：
#    伪装成「没字幕」会让代理失效表现为「导入变慢」，用户永远查不到原因。
_BLOCKED_ERRORS = tuple(
    c for c in (
        getattr(_yta, "IpBlocked", None),
        getattr(_yta, "RequestBlocked", None),
        getattr(_yta, "PoTokenRequired", None),
        getattr(_yta, "YouTubeRequestFailed", None),
        getattr(_yta, "YouTubeDataUnparsable", None),
    ) if isinstance(c, type)
)

# 用 getattr 收集而非直接 import：依赖版本漂移时，缺一个异常类不应让整个模块 import 失败
# （那会连「视频下载」这条不依赖字幕的路径一起打挂）。


def _extract_video_id(url: str) -> str | None:
    """从 YouTube URL 提取 video ID"""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def get_video_info(video_id: str, proxy: str = "") -> dict:
    """
    通过 YouTube oEmbed API 获取视频元数据（无需 API Key，零成本）。
    返回 {"title": "", "thumbnail": "", "uploader": "", "uploader_url": ""}
    失败时返回空 dict。

    注：走 urllib + ProxyHandler，与上面的 youtube-transcript-api 无关。
    """
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "LiMo/1.0"})
        opener = urllib.request.build_opener()
        if proxy:
            opener.add_handler(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
        with opener.open(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return {
            "title": data.get("title", ""),
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            "uploader": data.get("author_name", ""),
            "uploader_url": data.get("author_url", ""),
        }
    except Exception as e:
        logger.warning("YouTube oEmbed 元数据获取失败 (%s): %s", video_id, str(e)[:100])
        # 回退：至少返回缩略图 URL（无需 API 也能访问）
        return {
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        }


def _segments_from_fetched(fetched) -> list[dict]:
    """
    把 1.x 的 `FetchedTranscript`（可迭代的 `FetchedTranscriptSnippet` dataclass）
    转成本项目的 segments 结构。

    ⚠️ 刻意**不做 dict/dataclass 双形态兼容** —— 那种 `isinstance(s, dict)` 分支会让
    两种形状都「看起来能跑」，而错误的那一支默默产出 `repr(snippet)` 当正文（静默失效）。
    形状由 test_api_shape_contract 钉死，这里只认属性访问。
    """
    segments = []
    for snippet in fetched:
        text = (snippet.text or "").strip()
        if not text:
            continue
        start = float(snippet.start or 0)
        duration = float(snippet.duration or 0)
        segments.append({
            "start": round(start, 2),
            "end": round(start + duration, 2),
            "text": text,
        })
    return segments


def _is_no_subtitle(e: Exception) -> bool:
    return bool(_NO_SUBTITLE_ERRORS) and isinstance(e, _NO_SUBTITLE_ERRORS)


def _is_blocked(e: Exception) -> bool:
    return bool(_BLOCKED_ERRORS) and isinstance(e, _BLOCKED_ERRORS)


def fetch_subtitles(video_url: str, cookie_str: str = "", proxy: str = "") -> dict | None:
    """
    YouTube 字幕直拉。返回 {"segments": [...], "language": "zh", "full_text": "..."}；
    或 None —— 表示**视频确实没有可用字幕**（正常降级，上层改走下载 + ASR）。

    被反爬 / 网络拦下时**抛出 RuntimeError**：上层 `video.py` 会把它收进 warnings
    展示给用户（此时唯一可采取的动作是检查代理，不该伪装成「没字幕」）。
    cookie_str 用于需要登录的视频。
    """
    vid = _extract_video_id(video_url)
    if not vid:
        logger.info("无法提取 YouTube video ID: %s", video_url[:60])
        return None

    # cookie：1.x 只能经 http_client（requests.Session）传
    http_client = requests.Session()
    if cookie_str:
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                if k.strip():
                    http_client.cookies.set(k.strip(), v.strip())

    # 代理：1.x 走 proxy_config 构造参数，不再改 os.environ
    proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None
    api = YouTubeTranscriptApi(proxy_config=proxy_config, http_client=http_client)

    try:
        transcript_list = api.list(vid)
    except Exception as e:
        if _is_no_subtitle(e):
            logger.info("YouTube 无可用字幕（正常降级，改走下载）: %s %s", vid, type(e).__name__)
            return None
        if _is_blocked(e):
            raise RuntimeError(
                f"YouTube 字幕列表被拦截（{type(e).__name__}）— 请检查代理是否生效"
            ) from e
        raise

    # 按优先级选轨：人工 > 自动 > 任意
    transcript = None
    source_label = ""

    try:
        transcript = transcript_list.find_manually_created_transcript(_PRIORITY_LANGS)
        source_label = "manual"
        logger.info("YouTube 选中人工字幕: %s (%s)", transcript.language_code, transcript.language)
    except Exception:
        try:
            transcript = transcript_list.find_generated_transcript(_PRIORITY_LANGS)
            source_label = "auto"
            logger.info("YouTube 选中自动字幕: %s (%s)", transcript.language_code, transcript.language)
        except Exception:
            for t in transcript_list:
                transcript = t
                source_label = "auto" if t.is_generated else "manual"
                logger.info("YouTube 使用首个可用字幕: %s (%s)", t.language_code, source_label)
                break

    if not transcript:
        logger.info("YouTube 视频 %s 没有任何可用字幕", vid)
        return None

    try:
        fetched = transcript.fetch()
    except Exception as e:
        if _is_no_subtitle(e):
            logger.info("YouTube 字幕内容不可用（正常降级，改走下载）: %s %s", vid, type(e).__name__)
            return None
        if _is_blocked(e):
            raise RuntimeError(
                f"YouTube 字幕内容被拦截（{type(e).__name__}）— 请检查代理是否生效"
            ) from e
        raise

    segments = _segments_from_fetched(fetched)

    if not segments:
        logger.info("YouTube 字幕内容为空: %s", vid)
        return None

    full_text = " ".join(s["text"] for s in segments)
    logger.info("YouTube 字幕直拉成功: %s lang=%s %s %d段",
                vid, transcript.language_code, source_label, len(segments))
    return {
        "language": transcript.language_code,
        "full_text": full_text,
        "segments": segments,
        "source": "youtube_transcript_api",
    }
