"""网络地址判据 — 代理豁免表的唯一入口。

**为什么要有这个模块**（2026-10-10）：`main.py` 启动时把 `fetch.proxy` 注入成**全局**
环境变量（`HTTP_PROXY` / `HTTPS_PROXY`），这条通道让 dashscope SDK / openai SDK /
requests / yt-dlp 全部走代理。但它**没有豁免表** —— 于是连 `http://127.0.0.1:18080`
的本地 SearXNG、以及用户自建在 `http://localhost:8000` 的 LLM 网关，也一并被塞进代理。
多数代理软件默认对回环直连，所以平时看不出问题 —— 那是**运气，不是设计**。

两条通道的覆盖面**刻意不同**（不是同源漂移，是被第三方库的表达能力限制）：

| 通道 | 谁在用 | 能表达什么 | 本模块的对应物 |
|---|---|---|---|
| `NO_PROXY` 环境变量 | 第三方 SDK：openai / dashscope / requests / yt-dlp | 只认主机名字面量或后缀，**各家匹配语义还不一致** | `NO_PROXY_VALUE`（回环 + 域名后缀） |
| `proxy_for(url, proxy)` | 我们自己写的 httpx 调用点 | 任意判据 | `is_local_url()` + `is_bypass_url()` |

⚠️ **私网地址只走 Python 那条**（`192.168.x` / `10.x` / `172.16-31.x`）：在
`NO_PROXY` 里写 `192.168.` 这种前缀，requests 会做后缀匹配、httpx 不一定 ——
把正确性押在这种不一致上，等于把 bug 埋进依赖库里。域名后缀（`bilibili.com`）
两家语义一致，故**可以**进 `NO_PROXY`。

## 直连域名（2026-11-03）

启动时代理是**无差别**注入的，连 B 站这类国内站也一并塞进代理。代理软件没开
（`WinError 10061`）时，整条导入链路跟着失败 —— 而用户配代理的目的几乎总是
访问境外服务，国内站经代理反而更慢。

`DEFAULT_BYPASS_SUFFIXES` 是这份规则的**默认值**，`fetch.proxy_bypass` 是它的
可编辑形态（设置 → 代理 → 直连域名）。三条通道都要吃到它：

1. `proxy_for()` —— 我们自己的 httpx 调用点
2. `NO_PROXY` 环境变量 —— requests / SDK（B 站字幕 API 走这条）
3. **yt-dlp 的 `proxy=` 显式参数** —— ⚠️ 它**既不读 NO_PROXY 也不经 `proxy_for`**，
   源码里 `clean_proxies` 明写 *"takes preference over everything, including
   NO_PROXY"*。漏了这条等于没改（`video.py::_get_proxy(url)`）。
"""

import ipaddress
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 回环 / 本机字面量。同时供环境变量通道（NO_PROXY_VALUE）与 Python 判据（is_local_url）使用，
# 保证「豁免表」只有一个真相源。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# 保留「仅供私有使用」的域名后缀：`.local`（RFC 6762 mDNS）/ `.home.arpa`（RFC 8375）/
# `.internal`（ICANN 2024 保留）。⚠️ **只收有标准依据的**：`.lan` / `.home` / `.corp`
# 虽然常见，但没有 RFC——绕过代理是个「猜错也静默」的判据，靠印象扩大豁免面会把真正
# 要走代理的网关也一并豁免掉（少了代理，症状是连不上而不是报错）。
# ⚠️ 与私网段同样**只走 Python 这一条**，不进 `NO_PROXY_VALUE`（各库后缀匹配语义不一）。
_LOCAL_DOMAIN_SUFFIXES = (".local", ".home.arpa", ".internal")

# 默认「直连域名」（用户可在 设置 → 代理 → 直连域名 里编辑，落 `fetch.proxy_bypass`）。
#
# ⚠️ 这份表**故意**没有遵守 `_LOCAL_DOMAIN_SUFFIXES` 那条「只收有 RFC 依据的后缀」的纪律
# —— 两者回答的不是同一个问题。那里问「这台主机是不是本机/内网」，猜错的代价是把真正
# 要走代理的网关也豁免掉；这里问「用户配代理时想不想让这些站走代理」，而配代理的目的
# 几乎总是访问境外服务，国内站经代理反而更慢、代理一挂就全挂（2026-11-03 实测：代理
# 软件没开时 B 站导入整条链路 `WinError 10061`）。用户可随时删改，故不必替他猜死边界。
#
# 覆盖的是「导入来源 + 它们实际会请求的 CDN」，不是「所有国内站」：
# yt-dlp 下载 B 站视频走的是 `bilivideo.com`，只豁免 `bilibili.com` 等于没豁免。
DEFAULT_BYPASS_SUFFIXES: tuple[str, ...] = (
    "bilibili.com", "b23.tv", "bilivideo.com", "hdslb.com",   # B 站：主站/短链/视频 CDN/静态
    "douyin.com", "douyinvod.com", "ixigua.com",              # 抖音 / 西瓜
    "zhihu.com", "weixin.qq.com",                             # 知乎 / 公众号
    "aliyuncs.com", "deepseek.com",                           # 国内云 / LLM API
    "cn",                                                     # 国家级后缀（含 bigmodel.cn 等）
)

# 运行时规则 —— 由 `configure_proxy()` 写入。初值取默认表，于是「没人调 configure_proxy」
# 的路径（测试 / 一次性脚本）也得到与产品一致的判据，而不是一个沉默的空表。
_BYPASS_SUFFIXES: tuple[str, ...] = DEFAULT_BYPASS_SUFFIXES

# 供 `NO_PROXY` / `no_proxy` 环境变量使用（默认规则下）。⚠️ 不含私网 —— 理由见模块文档。
NO_PROXY_VALUE = ",".join(_LOCAL_HOSTS + DEFAULT_BYPASS_SUFFIXES)


def _host_of(url: str) -> str:
    """URL 的主机名（小写）；解不出来返回空串。

    ⚠️ 传入的可能是**不带 scheme 的裸主机**（`localhost:18080`）—— 此时 `urlparse`
    会把 `localhost` 当成 scheme、解不出 hostname，故要有第二遍。
    """
    text = (url or "").strip()
    try:
        host = urlparse(text).hostname
        if host is None:
            host = urlparse(f"//{text}").hostname
    except ValueError:
        return ""  # 畸形 URL（如未闭合的 IPv6 方括号）
    return (host or "").lower()


def is_local_url(url: str) -> bool:
    """URL 的主机是否指向本机 / 内网（回环、私网、link-local、保留私有域名后缀）。

    ⚠️ **不包含「直连域名」规则** —— 那是 `is_bypass_url()`。两者刻意分开：前者是
    与位置有关的事实，后者是用户的偏好，混在一起会让「内网判据」被用户配置左右。
    `proxy_for()` 两个都查。
    """
    host = _host_of(url)
    if not host:
        return False
    if host in _LOCAL_HOSTS or host.endswith(_LOCAL_DOMAIN_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 普通域名 → 公网
    return ip.is_loopback or ip.is_private or ip.is_link_local


def normalize_bypass_rules(rules) -> tuple[str, ...]:
    """把用户填的直连域名清洗成可匹配的后缀表（小写、去前导点、去重、保序）。

    ⚠️ **含 `/` `*` 空格的条目会被丢掉**：它们永远匹配不上（`endswith` 恒 False），
    而输入框里看着又是「配置好了」—— 那正是「写了但没生效」的静默失效。丢的时候
    记一条 warning，至少日志里有痕迹。
    """
    out: list[str] = []
    for raw in rules or ():
        s = str(raw or "").strip().lower().lstrip(".")
        if not s:
            continue
        if any(ch in s for ch in "/* "):
            logger.warning("直连域名规则被忽略（不是主机名形态）: %r", raw)
            continue
        if s not in out:
            out.append(s)
    return tuple(out)


def is_bypass_url(url: str) -> bool:
    """URL 的主机是否命中「直连域名」规则（`fetch.proxy_bypass`）。"""
    host = _host_of(url)
    if not host:
        return False
    for suffix in _BYPASS_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def proxy_for(url: str, proxy: str) -> str | None:
    """给一个 URL 决定用不用代理：本地地址 / 直连域名 / 压根没配代理 → None。

    **成对判据的收口点** —— 散在各调用点的裸写法 `if proxy: client_kwargs["proxy"] = proxy`
    正是这次的坑：它只问「配了没」，不问「目标是谁」。
    """
    # strip 在这里做：各调用点从 YAML 取到的值可能带空白，而 **httpx 收到 `"   "`
    # 会当成非法代理 URL** —— 收口一次，所有调用点自动免疫。
    p = (proxy or "").strip()
    if not p or is_local_url(url) or is_bypass_url(url):
        return None
    return p


def merge_no_proxy(existing: str = "", bypass=None) -> str:
    """把豁免表并进**已有的** NO_PROXY 值，不覆盖用户 / 系统已配置的项。

    写环境变量时若直接赋值，会把用户预先设好的豁免项静默抹掉 —— 那正好是
    「配置看起来生效了，实际被覆盖」这类最难查的失效。

    `bypass` 缺省取模块当前的直连规则；显式传入是为了让测试不必动全局状态。
    """
    hosts = [h.strip() for h in (existing or "").split(",") if h.strip()]
    for h in _LOCAL_HOSTS:
        if h not in hosts:
            hosts.append(h)
    rules = _BYPASS_SUFFIXES if bypass is None else normalize_bypass_rules(bypass)
    for h in rules:
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


def configure_proxy(proxy: str, bypass=None, environ=None) -> None:
    """设置代理 + 直连域名规则 —— **启动时与保存配置后各调一次**。

    两件事必须一起做：模块级规则（`proxy_for` / `video.py` 用）与环境变量
    （第三方 SDK 用）。拆成两个调用点迟早漏一个，而漏了模块级那半的现象是
    「SDK 豁免了、我们自己的 httpx 还走代理」—— 界面上完全看不出来。

    `bypass=None` = 用 `DEFAULT_BYPASS_SUFFIXES`（老配置缺该键时）；传 `[]` 才是
    「一条都不豁免」（用户把输入框清空了）。

    ⚠️ `apply_proxy_env` 在 proxy 为空时直接 return（不清既有环境变量）—— 这是
    改造前就有的语义，本轮不动：环境里的 `HTTP_PROXY` 可能是**用户自己**在 shell
    里设的，清掉等于替他做决定。故「关掉代理」仍需重启后端才彻底生效，模块级规则
    则会立刻生效。
    """
    global _BYPASS_SUFFIXES
    _BYPASS_SUFFIXES = normalize_bypass_rules(
        DEFAULT_BYPASS_SUFFIXES if bypass is None else bypass)
    apply_proxy_env(proxy, environ)
