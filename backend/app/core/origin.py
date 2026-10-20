"""来源可信判据 —— CORS 白名单与「有副作用的 GET」守卫共用一份。

**为什么需要这道守卫**：CORS 只阻止跨站**读响应**，不阻止请求发出。一个恶意网页写
`<img src="http://127.0.0.1:7413/api/generate/{id}/stream">` 就能让用户本机的后端
跑一次**计费生成**（产物还会落盘）——页面看不到任何结果，钱和等待时间却已经花掉。
`GET /api/chat/stream` 同理（计费 + 会话写入）。

判据两道，任一满足即放行：

1. **带 `X-LiMo-Client` 头**（主判据）—— 跨站 `<img>` / `<script>` / `<link>` 是
   no-cors 请求，**无法携带自定义头**；用 `fetch` 带自定义头则会先发 preflight，
   而 CORS 白名单（同一份正则）不会给未知 origin 回 CORS 头，预检失败 ⇒ 请求不发出。
2. **Origin（无 Origin 时看 Referer）命中白名单**（兼容不带自定义头的旧客户端）。

两者都缺失时**放行**：curl / CLI / 直接浏览器打开都不带这两个头，一律拒掉会伤及
调试入口。⚠️ 残余风险：恶意页面用 `Referrer-Policy: no-referrer` 可同时抹掉
Origin 与 Referer —— 这条路只能由第 1 道挡（所以前端必须带那个头）。

⚠️ 白名单正则**只此一份**：`main.py` 的 CORS 中间件也 import 它（模式 #54：两处各
写一份时，改一处漏一处的表现是「CORS 放行了、守卫拦了」或反过来）。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import Request

from app.core.errors import AppError

#: 前端每个请求都带的标记头（值不参与判定，「有没有」才是判据）
CLIENT_HEADER = "x-limo-client"

#: 允许的来源：Tauri 壳（`tauri.localhost` / `tauri://localhost`）+ 本机任意端口
#: - Tauri v2 生产 WebView2 实际 origin: `http://tauri.localhost`（Windows 实测）
#: - 开发 Vite devUrl: `http://localhost:<任意端口>`
ALLOWED_ORIGIN_RE = re.compile(
    r'^(https?://(localhost|127\.0\.0\.1)(:\d+)?|https?://tauri\.localhost|tauri://localhost)$'
)


def is_allowed_origin(value: str) -> bool:
    """`value` 是否命中来源白名单（空串视为不命中）。"""
    return bool(value and ALLOWED_ORIGIN_RE.match(value))


def _origin_of_referer(referer: str) -> str:
    """从完整 Referer URL 取出 origin（`scheme://netloc`）。"""
    try:
        parts = urlsplit(referer)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def is_trusted_request(request: Request) -> bool:
    """请求是否来自可信来源（见模块文档的三条判据）。"""
    if request.headers.get(CLIENT_HEADER):
        return True

    origin = request.headers.get("origin", "")
    if origin:
        return is_allowed_origin(origin)

    referer = request.headers.get("referer", "")
    if referer:
        return is_allowed_origin(_origin_of_referer(referer))

    # 浏览器发起的跨站请求必带 Origin 或 Referer；两者都没有通常是 curl / CLI /
    # 本机直接打开 —— 放行。
    return True


def ensure_trusted_request(request: Request) -> None:
    """不满足判据时抛 403（有副作用的 GET 端点入口调用）。"""
    if not is_trusted_request(request):
        raise AppError(
            "UNTRUSTED_ORIGIN",
            "拒绝来自未知来源的请求",
            detail=f"origin={request.headers.get('origin', '')[:120]}",
            status_code=403,
        )
