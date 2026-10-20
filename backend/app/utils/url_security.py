"""SSRF 防护：URL 安全校验 + 重定向逐跳校验。

阻止请求指向内网地址、loopback、link-local 等敏感目标。

⚠️ **2026-10-20 修了两类真实绕过**（第二、三条是审查发现）：

1. **IPv6 完全没被拦**：旧实现的黑名单只有 IPv4 网段、DNS 也只查 `AF_INET`。
   实测 `http://[::1]` / `http://[::ffff:127.0.0.1]` / `http://[fc00::1]` 全部放行
   —— 打了本机就等于拿到了整个 `/api/*`。现在统一走 `ipaddress` 判定，并把
   `::ffff:a.b.c.d`（IPv4-mapped）归一成内嵌的 v4 再判，6to4 / NAT64 前缀直接拦。
2. **DNS 只查第一个地址**：多 A 记录（或 A + AAAA）时只要第一个是公网就放行，
   而实际连接可能落到另一个内网地址。现在**逐个校验所有解析结果**。
3. **重定向不校验**：`follow_redirects=True` 与 `requests` 默认跟随让公网 URL 用
   302 跳到 `127.0.0.1` 就绕开了上面两道。`safe_get` / `safe_get_httpx` 关闭自动
   跟随，逐跳重跑 `validate_url`。

⚠️ **判定「是不是 IP」不能用异常控制流**：旧代码 `try: ip_address(host) ... except
ValueError:` 把 `is_private_ip()` 主动抛的 ValueError 与「不是 IP」混在一起，于是
内网 IPv4 会继续走到 DNS 分支、报出「无法解析主机名」—— 拦是拦住了，但原因错位。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# 内网 / 保留地址段（IPv4）
_BLOCKED_V4_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),          # loopback
    ipaddress.ip_network("169.254.0.0/16"),        # link-local
    ipaddress.ip_network("0.0.0.0/8"),             # "this" network
    ipaddress.ip_network("224.0.0.0/4"),           # multicast
    ipaddress.ip_network("240.0.0.0/4"),           # reserved (class E)
    ipaddress.ip_network("100.64.0.0/10"),         # carrier-grade NAT
    ipaddress.ip_network("198.18.0.0/15"),         # benchmark testing
]

# 需要额外拦的 IPv6 网段。
# `is_private` 已覆盖 fc00::/7（ULA）与 fe80::/10（link-local），但这两个前缀能把
# 内嵌的 IPv4 再带回来（6to4 / NAT64），只判外层的话等于给了绕道。
_BLOCKED_V6_NETWORKS = [
    ipaddress.ip_network("2002::/16"),             # 6to4（内嵌 v4）
    ipaddress.ip_network("64:ff9b::/96"),          # NAT64（内嵌 v4）
    ipaddress.ip_network("fec0::/10"),             # site-local（已废弃仍保留）
]

# 重定向最多跟几跳（超过即判失败，与浏览器默认量级一致）
MAX_REDIRECTS = 5


def _is_blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """单个 IP 是否属于「不该被外部请求访问」的地址。"""
    # IPv6 里嵌着 IPv4（::ffff:a.b.c.d）时按内嵌的 v4 判 —— 否则
    # `::ffff:127.0.0.1` 会因为是 IPv6 而绕过整张 v4 黑名单。
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        return any(mapped in net for net in _BLOCKED_V4_NETWORKS)

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _BLOCKED_V4_NETWORKS)

    return bool(
        addr.is_loopback
        or addr.is_private        # 含 fc00::/7 ULA 与 fe80::/10
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or any(addr in net for net in _BLOCKED_V6_NETWORKS)
    )


def is_private_ip(host: str) -> bool:
    """检查 IP 地址是否为内网/保留地址。

    返回 True 表示该地址不应被外部请求访问。无法解析成 IP 的字符串一律视为
    **不安全**（这个函数只在「已知 host 是 IP」的语境下调用；判断 host 本身是不是
    IP 请用 `_parse_ip`）。
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True
    return _is_blocked_address(addr)


def _parse_ip(host: str):
    """把 host 解析成 IP 对象；不是 IP 字面量时返回 None。"""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def resolve_hosts(host: str) -> list[str]:
    """DNS 解析主机名，返回**全部**去重后的 IP 字符串。失败返回空列表。

    ⚠️ 必须用 `AF_UNSPEC` 同时查 A 与 AAAA 记录 —— 只查 `AF_INET` 时，一个只有
    AAAA 记录的域名会被当成「解析失败」拒绝，而只解析到一个地址时又漏掉了
    「多个 A 记录里混着内网」的情况（校验必须逐个过）。
    """
    try:
        info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for entry in info:
        ip = entry[4][0]
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def validate_url(url: str) -> None:
    """校验 URL 安全性。

    检查项：
    1. 只允许 http/https 协议
    2. 主机名不能为空
    3. 主机名/IP 不能是内网/保留地址（IP 字面量直接判；域名则**所有**解析结果逐个判）

    抛出 ValueError 如果不安全。
    """
    parsed = urlparse(url)

    # 1. 协议检查
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不支持的协议: {parsed.scheme}")

    # 2. 主机名不能为空
    host = parsed.hostname
    if not host:
        raise ValueError("URL 缺少主机名")

    # 3. 是 IP 字面量 → 直接判（urlparse 会把 `[::1]` 还原成 `::1`）
    addr = _parse_ip(host)
    if addr is not None:
        if _is_blocked_address(addr):
            raise ValueError(f"禁止访问内网地址: {host}")
        return

    # 4. DNS 解析后逐个检查
    ips = resolve_hosts(host)
    if not ips:
        raise ValueError(f"无法解析主机名: {host}")
    for ip in ips:
        if is_private_ip(ip):
            raise ValueError(f"禁止访问内网地址: {host} → {ip}")


def _validate_redirect(current: str, location: str) -> str:
    """把 Location 解析成绝对地址并校验，返回新 URL。"""
    nxt = urljoin(current, location)
    validate_url(nxt)
    return nxt


def safe_get(url: str, *, session: Any = None, max_redirects: int = MAX_REDIRECTS, **kwargs):
    """带**逐跳 SSRF 校验**的 `requests.get`。

    关闭 requests 的自动重定向，每一跳都重跑 `validate_url` —— 公网 URL 302 到
    `127.0.0.1` 是最省事的 SSRF 绕法，只在第一跳校验等于没校验。

    `kwargs` 原样透传（timeout / headers / stream …）。`allow_redirects` 会被忽略
    （本函数自己管理重定向）。
    """
    import requests

    getter = session.get if session is not None else requests.get
    kwargs.pop("allow_redirects", None)

    current = url
    for _ in range(max_redirects + 1):
        validate_url(current)
        resp = getter(current, allow_redirects=False, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            # stream=True 时未读的响应体必须显式关掉，否则连接泄漏
            resp.close()
            current = _validate_redirect(current, location)
            continue
        return resp
    raise ValueError(f"重定向次数超过 {max_redirects}: {url}")


def safe_get_httpx(url: str, *, client: Any = None, max_redirects: int = MAX_REDIRECTS, **kwargs):
    """带逐跳校验的 `httpx.get`（或 `client.get`）。语义同 `safe_get`。"""
    import httpx

    getter = client.get if client is not None else httpx.get
    kwargs.pop("follow_redirects", None)

    current = url
    for _ in range(max_redirects + 1):
        validate_url(current)
        resp = getter(current, follow_redirects=False, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                return resp
            resp.close()
            current = _validate_redirect(current, location)
            continue
        return resp
    raise ValueError(f"重定向次数超过 {max_redirects}: {url}")


__all__ = [
    "MAX_REDIRECTS",
    "is_private_ip",
    "resolve_hosts",
    "validate_url",
    "safe_get",
    "safe_get_httpx",
]
