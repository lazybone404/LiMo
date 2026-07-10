"""
B站字幕直拉 — 通过 B站官方 API 获取字幕，无需下载视频。
参照 BiliNote pipe_subtitle 和 bilibili_downloader.py。
优先策略：人工中文 > AI中文 > 任意语言
"""

import json
import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _extract_bvid(url: str) -> Optional[str]:
    """从 B站 URL 提取 BV 号。支持 b23.tv 短链。"""
    if "b23.tv" in url or "b23.tv" in url.lower():
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10,
                                headers={"User-Agent": USER_AGENT})
            url = resp.url
        except requests.RequestException:
            return None
    m = re.search(r"BV([0-9A-Za-z]+)", url)
    return f"BV{m.group(1)}" if m else None


def fetch_subtitles(video_url: str, cookie: str = "") -> Optional[dict]:
    """
    B站 API 直拉字幕。返回 {"segments": [...], "language": "zh", "full_text": "..."}
    或 None（无可用字幕）。
    """
    bvid = _extract_bvid(video_url)
    if not bvid:
        logger.info("无法提取 BV 号: %s", video_url[:60])
        return None

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.bilibili.com",
    }
    if cookie:
        # 剥掉可能包裹的引号（yaml 可能序列化时加了引号）
        cookie = cookie.strip().strip("'\"").strip()
        if cookie:
            # HTTP header 值必须是 latin-1 编码，过滤非法字符
            headers["Cookie"] = cookie.encode("latin-1", errors="replace").decode("latin-1")

    # 1. view API → cid
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid}, headers=headers, timeout=10
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.info("view API 错误: bvid=%s code=%d msg=%s",
                       bvid, data.get("code"), data.get("message"))
            return None
        cid = data["data"].get("cid")
        if not cid:
            logger.info("未获取到 cid: %s", bvid)
            return None
    except requests.Timeout:
        logger.warning("view API 超时: %s", bvid)
        return None
    except requests.RequestException as e:
        logger.warning("view API 请求失败: %s", e)
        return None

    # 2. player API → 字幕列表
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/player/wbi/v2",
            params={"bvid": bvid, "cid": cid}, headers=headers, timeout=10
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.info("player API 错误: bvid=%s code=%d", bvid, data.get("code"))
            return None
        subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
    except requests.Timeout:
        logger.warning("player API 超时: %s", bvid)
        return None
    except requests.RequestException as e:
        logger.warning("player API 请求失败: %s", e)
        return None

    if not subtitles:
        logger.info("%s 无可用字幕轨", bvid)
        return None

    # 3. 优先级选轨：人工中文 > AI中文 > 任意
    def _is_zh(s):
        lan = (s.get("lan") or "").lower()
        return lan.startswith("zh") or lan == "ai-zh"

    chosen = None
    for s in subtitles:
        if _is_zh(s) and not s.get("ai_type"):
            chosen = s; break
    if not chosen:
        for s in subtitles:
            if _is_zh(s):
                chosen = s; break
    if not chosen:
        chosen = subtitles[0]

    sub_url = chosen.get("subtitle_url")
    if not sub_url:
        logger.info("%s 字幕轨无 subtitle_url（可能需 Cookie）", bvid)
        return None
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url

    # 4. 下载解析字幕 JSON
    try:
        body = requests.get(sub_url, headers=headers, timeout=15).json().get("body", [])
    except requests.Timeout:
        logger.warning("字幕JSON下载超时: %s", bvid)
        return None
    except requests.RequestException as e:
        logger.warning("字幕JSON下载失败: %s", e)
        return None

    segments = []
    for item in body:
        text = (item.get("content") or "").strip()
        if text:
            segments.append({
                "start": float(item.get("from", 0)),
                "end": float(item.get("to", 0)),
                "text": text,
            })

    if not segments:
        logger.info("%s 字幕内容为空", bvid)
        return None

    full_text = " ".join(s["text"] for s in segments)
    lan = chosen.get("lan") or "zh"

    # 提取视频元信息（标题、封面）— view API 返回的 data.data
    video_info = data.get("data", {})
    title = video_info.get("title", "")
    pic = video_info.get("pic", "")
    uploader = video_info.get("owner", {}).get("name", "")

    logger.info("✅ B站字幕直拉: %s lan=%s %d段 title=%s", bvid, lan, len(segments), title[:40] if title else "")
    return {
        "language": lan, "full_text": full_text,
        "segments": segments, "source": "bilibili_api",
        "title": title, "cover_url": pic, "uploader": uploader,
    }
