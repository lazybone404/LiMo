"""
Cookie 解析 — 用户粘贴原始字符串，系统自动提取关键字段。
不让用户手动找出 SESSDATA/bili_jct。
"""

import re
from typing import Optional


# 各平台需要的关键 cookie 字段
PLATFORM_KEYS = {
    "bilibili": ["SESSDATA", "bili_jct", "buvid3", "DedeUserID", "buvid4"],
    "youtube": ["SAPISID", "HSID", "SSID", "APISID", "SID", "LOGIN_INFO", "PREF"],
    # 抖音没有标准化的关键字段名，保留全部 cookie
}

REQUIRED_KEYS = {
    "bilibili": ["SESSDATA", "bili_jct"],
    "youtube": ["SAPISID"],
}


def parse_cookie_string(raw: str) -> dict[str, str]:
    """从原始 cookie 字符串解析为 dict"""
    cookies: dict[str, str] = {}
    if not raw or not raw.strip():
        return cookies

    # 支持三种格式:
    # 1. "key1=value1; key2=value2; ..."
    # 2. Netscape cookie 格式 (yt-dlp 输出)
    # 3. Chrome DevTools 表格格式（Application → Cookies → 全选复制）

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "\t" in line:
            parts = line.split("\t")
            # Chrome 表格格式: name \t value \t domain \t path \t expires...
            # 特征: 第一列不含 '='，且看上去像 cookie 名（非 IP/域名字符）
            if len(parts) >= 2 and "=" not in parts[0] and not parts[0].startswith("."):
                if parts[1].strip():
                    cookies[parts[0].strip()] = parts[1].strip()
                    continue
            # Netscape 格式: domain flag path secure expires name value
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
                continue
        # 标准格式: key=value 或 key=value;
        for chunk in line.split(";"):
            chunk = chunk.strip()
            if "=" in chunk:
                key, _, value = chunk.partition("=")
                key = key.strip()
                value = value.strip()
                if key and value:
                    cookies[key] = value

    return cookies


def extract_platform_cookie(raw: str, platform: str) -> dict[str, str]:
    """解析原始 cookie 并提取平台需要的字段"""
    cookies = parse_cookie_string(raw)
    keys = PLATFORM_KEYS.get(platform, [])

    if not keys:
        # 未定义关键字段的平台，保留所有解析出的 cookie
        return cookies

    return {k: v for k, v in cookies.items() if k in keys}


def to_netscape(cookies: dict[str, str], domain: str = ".bilibili.com") -> str:
    """将 cookie dict 转为 Netscape 格式（yt-dlp 需要）"""
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in cookies.items():
        # domain flag path secure expires name value
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def auto_parse(raw: str) -> dict:
    """
    用户粘贴任意格式 → 自动识别平台 + 提取关键字段

    Returns:
        {"platform": "bilibili", "cookies": {...}, "netscape": "..."}
    """
    if not raw or not raw.strip():
        return {"platform": None, "cookies": {}, "netscape": ""}

    cookies = parse_cookie_string(raw)

    # 自动检测平台
    platform = None
    if "bili_jct" in cookies or "SESSDATA" in cookies or "buvid3" in cookies:
        platform = "bilibili"
    elif "SAPISID" in cookies or "LOGIN_INFO" in cookies:
        platform = "youtube"
    elif "s_v_web_id" in cookies or "fpk1" in cookies:
        platform = "douyin"

    if platform:
        if platform == "douyin":
            # 抖音保留全部 cookie（无标准化关键字段）
            netscape = to_netscape(cookies, ".douyin.com")
            return {
                "platform": platform,
                "cookies": dict(cookies),
                "netscape": netscape,
            }
        extracted = extract_platform_cookie(raw, platform)
        domain = ".bilibili.com" if platform == "bilibili" else ".youtube.com"
        netscape = to_netscape(extracted, domain)
        return {
            "platform": platform,
            "cookies": extracted,
            "netscape": netscape,
        }

    # 无法识别平台，保留原始
    return {"platform": None, "cookies": cookies, "netscape": raw}
