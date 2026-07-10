"""
抖音下载器 — 直接调用抖音 Web API 获取视频信息和音频，绕过 yt-dlp。

基于 BiliNote 实现，适配 LiMo 架构。
"""

import json
import logging
import re
import time
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

from app.downloaders.douyin_helper.abogus import ABogus

logger = logging.getLogger(__name__)

DOUYIN_DOMAIN = "https://www.douyin.com"

# ── 常量配置 ──

HEADERS_TEMPLATE = {
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # ⚠️ UA 必须与 ABogus 签名算法中的 ua_code 匹配，改 UA 会导致签名验证失败
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
}

MS_TOKEN_CONFIG = {
    "url": "https://mssdk.bytedance.com/web/report",
    "magic": 538969122,
    "version": 1,
    "dataType": 8,
    "strData": (
        "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"
        "R3d5j8lNhBPzA5e+mE=="
    ),
}


def _extract_video_id(url: str) -> str | None:
    """从抖音 URL 提取 aweme_id"""
    # 处理短链接重定向
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10,
                            headers={"User-Agent": HEADERS_TEMPLATE["User-Agent"]})
        url = resp.url
    except Exception:
        pass

    patterns = [r'video/(\d+)', r'aweme_id=(\d+)', r'modal_id=(\d+)']
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _gen_ms_token() -> str:
    """调用字节跳动 mssdk 生成 msToken"""
    import json as _json
    payload = _json.dumps({
        "magic": MS_TOKEN_CONFIG["magic"],
        "version": MS_TOKEN_CONFIG["version"],
        "dataType": MS_TOKEN_CONFIG["dataType"],
        "strData": MS_TOKEN_CONFIG["strData"],
        "tspFromClient": int(time.time() * 1000),
    })
    headers = {
        "User-Agent": HEADERS_TEMPLATE["User-Agent"],
        "Content-Type": "application/json",
    }
    resp = requests.post(MS_TOKEN_CONFIG["url"], data=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    ms_token = resp.cookies.get("msToken", "")
    if len(ms_token) not in (120, 128):
        raise ValueError(f"msToken 长度异常 ({len(ms_token)})，期望 120 或 128")
    return ms_token


def fetch_video_info(video_url: str, cookie: str = "") -> dict:
    """调用抖音 Web API 获取视频详情"""
    aweme_id = _extract_video_id(video_url)
    if not aweme_id:
        raise ValueError(f"无法从 URL 提取 aweme_id: {video_url}")

    # 构建请求参数（msToken 先设空，a_bogus 签完再补——官方签名不含 msToken）
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": 1,
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": 1920,
        "screen_height": 1080,
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": 12,
        "device_memory": 8,
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "aweme_id": aweme_id,
        "msToken": "",
    }

    # a_bogus 签名（msToken 为空，不含真实 token）
    ab = ABogus()
    a_bogus = quote(ab.get_value(params), safe='')

    # 签名后再尝试获取 msToken，成功则替换进 URL
    try:
        ms_token = _gen_ms_token()
        params["msToken"] = ms_token
        logger.info("抖音: aweme_id=%s msToken=%s...", aweme_id, ms_token[:20])
    except Exception as e:
        logger.warning("msToken 获取失败，使用空值: %s", e)

    query_str = urlencode(params)
    api_url = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/detail/?{query_str}&a_bogus={a_bogus}"

    headers = HEADERS_TEMPLATE.copy()
    if cookie:
        # 清理：去掉引号 + 过滤含非 ASCII 字符的条目（浏览器截断符 `…` 等）
        cookie = cookie.strip().strip("'\"").strip()
        clean_parts = []
        for pair in cookie.split(";"):
            pair = pair.strip()
            if "=" in pair:
                try:
                    pair.encode("latin-1")
                    clean_parts.append(pair)
                except UnicodeEncodeError:
                    continue
        cookie = "; ".join(clean_parts)
        headers["Cookie"] = cookie
        logger.info("抖音 Cookie 已注入 (%d 字符, %d 条)", len(cookie), len(clean_parts))

    logger.info("抖音 API 请求: %s...", api_url[:80])
    resp = requests.get(api_url, headers=headers, timeout=30)
    logger.info("抖音 API 响应: HTTP %s, Content-Type=%s, body_len=%d",
                resp.status_code, resp.headers.get("content-type", ""), len(resp.text))
    if resp.status_code != 200:
        raise ValueError(f"抖音 API 返回 {resp.status_code}: {resp.text[:300]}")
    if not resp.text.strip():
        raise ValueError("抖音 API 返回空响应体（可能被反爬拦截）")
    data = resp.json()

    aweme_detail = data.get("aweme_detail", {})
    if not aweme_detail:
        raise ValueError(f"抖音 API 返回无 aweme_detail: {str(data)[:300]}")

    return aweme_detail


def download_audio(video_url: str, cookie: str = "", output_dir: str | None = None) -> dict:
    """下载抖音视频的音频，返回 {file_path, title, duration, cover_url}"""
    aweme = fetch_video_info(video_url, cookie)

    title = aweme.get("item_title", "douyin_video")
    duration_ms = aweme.get("video", {}).get("duration", 0)
    duration = duration_ms / 1000.0 if duration_ms else 0

    # 封面：优先 cover_original_scale，fallback big_thumbs
    cover = ""
    video_info = aweme.get("video", {})
    if video_info.get("cover"):
        covers = video_info.get("cover_original_scale", {}).get("url_list", [])
        cover = covers[0] if covers else ""
    if not cover:
        cover = video_info.get("big_thumbs", {}).get("img_url", "")

    # 音频 URL
    music_url = aweme.get("music", {}).get("play_url", {}).get("uri", "")
    if not music_url:
        raise ValueError("抖音 API 返回无音频 URL")

    # 下载音频
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="dy_audio_")
    aweme_id = aweme.get("aweme_id", "unknown")
    output_path = Path(output_dir) / f"{aweme_id}.mp3"

    logger.info("抖音音频下载: %s → %s", music_url[:60], output_path)
    audio_resp = requests.get(music_url, timeout=60)
    audio_resp.raise_for_status()
    output_path.write_bytes(audio_resp.content)

    logger.info("抖音音频下载完成: %s (%.1fKB, %.0fs)",
                title, output_path.stat().st_size / 1024, duration)

    return {
        "file_path": str(output_path),
        "title": title,
        "duration": duration,
        "cover_url": cover,
        "aweme_id": aweme_id,
    }
