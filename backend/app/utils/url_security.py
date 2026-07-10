"""SSRF 防护：URL 安全校验。

阻止请求指向内网地址、loopback、link-local 等敏感目标。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 内网 / 保留地址段
_BLOCKED_NETWORKS = [
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


def is_private_ip(host: str) -> bool:
    """检查 IP 地址是否为内网/保留地址。

    返回 True 表示该地址不应被外部请求访问。
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return True  # 无法解析的地址，拒绝
    return any(addr in net for net in _BLOCKED_NETWORKS)


def resolve_host(host: str) -> str | None:
    """DNS 解析主机名，返回 IP 字符串。失败返回 None。"""
    try:
        info = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        if info:
            return info[0][4][0]
    except (socket.gaierror, OSError):
        pass
    return None


def validate_url(url: str) -> None:
    """校验 URL 安全性。

    检查项：
    1. 只允许 http/https 协议
    2. 主机名不能为空
    3. 主机名/IP 不能是内网/保留地址

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

    # 3. 检查主机是否为 IP 格式
    try:
        ipaddress.ip_address(host)
        # host 是 IP → 直接检查
        if is_private_ip(host):
            raise ValueError(f"禁止访问内网地址: {host}")
        return
    except ValueError:
        logger.warning("Host '%s' is not a valid IP address, will resolve via DNS", host)

    # 4. DNS 解析后检查 IP
    ip = resolve_host(host)
    if ip is None:
        raise ValueError(f"无法解析主机名: {host}")
    if is_private_ip(ip):
        raise ValueError(f"禁止访问内网地址: {host} → {ip}")
