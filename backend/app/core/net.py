"""网络地址判据 — 代理豁免表的唯一入口。

**为什么要有这个模块**（2026-10-10）：`main.py` 启动时把 `fetch.proxy` 注入成**全局**
环境变量（`HTTP_PROXY` / `HTTPS_PROXY`），这条通道让 dashscope SDK / openai SDK /
requests / yt-dlp 全部走代理。但它**没有豁免表** —— 于是连 `http://127.0.0.1:18080`
的本地 SearXNG、以及用户自建在 `http://localhost:8000` 的 LLM 网关，也一并被塞进代理。
多数代理软件默认对回环直连，所以平时看不出问题 —— 那是**运气，不是设计**。

两条通道的覆盖面**刻意不同**（不是同源漂移，是被第三方库的表达能力限制）：

| 通道 | 谁在用 | 能表达什么 | 本模块的对应物 |
|---|---|---|---|
| `NO_PROXY` 环境变量 | 第三方 SDK：openai / dashscope / requests / yt-dlp | 只认主机名字面量或后缀，**各家匹配语义还不一致** | `NO_PROXY_VALUE`（回环字面量） |
| `proxy_for(url, proxy)` | 我们自己写的 httpx 调用点 | 任意判据 | `is_local_url()`（回环 + 私网 + link-local） |

⚠️ **私网地址只走 Python 那条**（`192.168.x` / `10.x` / `172.16-31.x`）：在
`NO_PROXY` 里写 `192.168.` 这种前缀，requests 会做后缀匹配、httpx 不一定 ——
把正确性押在这种不一致上，等于把 bug 埋进依赖库里。
"""

import ipaddress
import os
from urllib.parse import urlparse

# 回环 / 本机字面量。同时供环境变量通道（NO_PROXY_VALUE）与 Python 判据（is_local_url）使用，
# 保证「豁免表」只有一个真相源。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# 保留「仅供私有使用」的域名后缀：`.local`（RFC 6762 mDNS）/ `.home.arpa`（RFC 8375）/
# `.internal`（ICANN 2024 保留）。⚠️ **只收有标准依据的**：`.lan` / `.home` / `.corp`
# 虽然常见，但没有 RFC——绕过代理是个「猜错也静默」的判据，靠印象扩大豁免面会把真正
# 要走代理的网关也一并豁免掉（少了代理，症状是连不上而不是报错）。
# ⚠️ 与私网段同样**只走 Python 这一条**，不进 `NO_PROXY_VALUE`（各库后缀匹配语义不一）。
_LOCAL_DOMAIN_SUFFIXES = (".local", ".home.arpa", ".internal")

# 供 `NO_PROXY` / `no_proxy` 环境变量使用。
# ⚠️ 只列字面量，不含私网 —— 理由见模块文档。
NO_PROXY_VALUE = ",".join(_LOCAL_HOSTS)


def is_local_url(url: str) -> bool:
    """URL 的主机是否指向本机 / 内网（回环、私网、link-local、保留私有域名后缀）。

    现存唯一调用点是 `_searxng_search`（本地 SearXNG 不该走代理）；
    自建 LLM 网关（`http://localhost:8000`）走的是环境变量那条通道。
    """
    text = (url or "").strip()
    try:
        host = urlparse(text).hostname
        if host is None:
            # 兜底：传进来的可能是不带 scheme 的裸主机（`localhost:18080`），
            # 此时 urlparse 会把 `localhost` 当成 scheme，解不出 hostname
            host = urlparse(f"//{text}").hostname
    except ValueError:
        return False  # 畸形 URL（如未闭合的 IPv6 方括号）
    if not host:
        return False
    low = host.lower()
    if low in _LOCAL_HOSTS or low.endswith(_LOCAL_DOMAIN_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 普通域名 → 公网
    return ip.is_loopback or ip.is_private or ip.is_link_local


def proxy_for(url: str, proxy: str) -> str | None:
    """给一个 URL 决定用不用代理：本地地址、或压根没配代理 → None。

    **成对判据的收口点** —— 散在各调用点的裸写法 `if proxy: client_kwargs["proxy"] = proxy`
    正是这次的坑：它只问「配了没」，不问「目标是谁」。
    """
    # strip 在这里做：各调用点从 YAML 取到的值可能带空白，而 **httpx 收到 `"   "`
    # 会当成非法代理 URL** —— 收口一次，所有调用点自动免疫。
    p = (proxy or "").strip()
    if not p or is_local_url(url):
        return None
    return p


def merge_no_proxy(existing: str = "") -> str:
    """把豁免表并进**已有的** NO_PROXY 值，不覆盖用户 / 系统已配置的项。

    写环境变量时若直接赋值，会把用户预先设好的豁免项静默抹掉 —— 那正好是
    「配置看起来生效了，实际被覆盖」这类最难查的失效。
    """
    hosts = [h.strip() for h in (existing or "").split(",") if h.strip()]
    for h in _LOCAL_HOSTS:
        if h not in hosts:
            hosts.append(h)
    return ",".join(hosts)


def apply_proxy_env(proxy: str, environ=None) -> None:
    """把代理 + 豁免表写进环境变量（`main.py` 启动时调用一次）。

    **抽成函数是为了让测试直调真实实现** —— 否则测试只能复刻一遍那几行赋值语句，
    而复刻出来的副本跟真身漂移时不会有任何提示（本项目反复栽在这上面）。

    ⚠️ `NO_PROXY` 走 `merge_no_proxy` 而非直接赋值：直接写会把用户 / 系统预先配好的
    豁免项静默抹掉。两个大小写都设 —— requests 读小写优先、httpx 读大写优先。
    """
    proxy = (proxy or "").strip()
    if not proxy:
        return
    env = os.environ if environ is None else environ
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env[name] = proxy
    merged = merge_no_proxy(env.get("NO_PROXY") or env.get("no_proxy") or "")
    env["NO_PROXY"] = merged
    env["no_proxy"] = merged
