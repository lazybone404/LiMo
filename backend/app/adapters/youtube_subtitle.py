"""
YouTube 字幕直拉 — youtube-transcript-api，无需下载视频。
对齐 BiliNote downloaders/youtube_subtitle.py + demo/pipeline.py
策略：人工字幕优先 → 自动字幕 → 任意可用语言
"""

import logging
import os
import re
import json
import urllib.request
import urllib.error

from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)

# 语言优先级：中文 → 英文 → 日文
_PRIORITY_LANGS = ["zh-Hans", "zh", "zh-CN", "zh-TW", "en", "en-US", "ja"]


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


def fetch_subtitles(video_url: str, cookie_str: str = "", proxy: str = "") -> dict | None:
    """
    YouTube 字幕直拉。返回 {"segments": [...], "language": "zh", "full_text": "..."}
    或 None（无可用字幕）。cookie_str 用于需要登录的视频。
    """
    vid = _extract_video_id(video_url)
    if not vid:
        logger.info("无法提取 YouTube video ID: %s", video_url[:60])
        return None

    # 将 cookie 字符串转为 dict（youtube-transcript-api 需要）
    cookies = None
    if cookie_str:
        cookies = {}
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                cookies[k.strip()] = v.strip()
        if not cookies:
            cookies = None

    # 代理：youtube-transcript-api 底层用 requests，需设环境变量
    old_proxy = os.environ.get("HTTP_PROXY", "") if proxy else ""
    try:
        if proxy:
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
        transcript_list = YouTubeTranscriptApi.list(vid, cookies=cookies)
    except Exception as e:
        logger.warning("YouTube 字幕列表获取失败 (%s): %s", vid, str(e)[:120])
        return None
    finally:
        if proxy:
            if old_proxy:
                os.environ["HTTP_PROXY"] = old_proxy
                os.environ["HTTPS_PROXY"] = old_proxy
            else:
                os.environ.pop("HTTP_PROXY", None)
                os.environ.pop("HTTPS_PROXY", None)

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
        logger.warning("YouTube 字幕内容获取失败 (%s): %s", vid, str(e)[:120])
        return None

    segments = []
    for snippet in fetched:
        text = (snippet.get("text", "") if isinstance(snippet, dict) else str(snippet)).strip()
        if not text:
            continue
        start = float(snippet.get("start", 0)) if isinstance(snippet, dict) else 0.0
        duration = float(snippet.get("duration", 0)) if isinstance(snippet, dict) else 0.0
        segments.append({
            "start": round(start, 2),
            "end": round(start + duration, 2),
            "text": text,
        })

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
