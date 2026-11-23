"""
Agent 工具 — 网络搜索 + 网页抓取

给 AgentRunner 注册用的 `search_web` / `fetch_url`。两家共用**一张方案表**：
配置里写的是哪一家、它此刻为什么不可用、失败之后退到哪儿 —— 三件事只回答一次
（`_search_plans` / `_fetch_plans` 加两个 runner）。

HTTP 请求统一走 config.fetch.proxy，**本地地址与直连域名除外** —— 判据收口在
app/core/net.py 的 `proxy_for()`，各调用点不再自己写 `if proxy:`。

⚠️ 降级对外的可见性刻意不对称：搜索结果带 `degraded` / `reason` 两个键交给模型转述，
抓取结果的降级只进日志。换一家搜索引擎，结果集完全不同，用户必须知道；而降级的抓取
仍是本地 trafilatura 提取的同一页正文，没有「另一份」可比。
"""

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.core.deepseek_api import TOOL_FETCH_URL, TOOL_SEARCH_URL, deepseek_api_key
from app.core.net import proxy_for

logger = logging.getLogger(__name__)

# ── SearXNG 自动启动 ──

_SEARXNG_PROC: subprocess.Popen | None = None
_SEARXNG_PORT = 18080
_SEARXNG_STARTUP_TIMEOUT = 15


def _get_searxng_launcher() -> list[str] | None:
    """返回 SearXNG 启动命令。查找顺序：
    1. SEARXNG_BIN_PATH 环境变量（Rust launcher 设置，安装目录）
    2. PyInstaller 打包：exe 同级 searxng/ 目录
    3. 开发环境：backend/bin/searxng/
    找不到返回 None。
    """
    # 1. 环境变量：Rust launcher 检测到安装目录下的 searxng
    env_path = os.environ.get("SEARXNG_BIN_PATH", "")
    if env_path and os.path.isdir(env_path):
        for candidate in [
            os.path.join(env_path, "searxng", "SearXNG for Windows.exe"),
            os.path.join(env_path, "SearXNG for Windows.exe"),
        ]:
            if os.path.exists(candidate):
                return [candidate]

    # 2. PyInstaller 打包：exe 同级 searxng/
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        exe = os.path.join(exe_dir, "searxng", "searxng", "SearXNG for Windows.exe")
        if os.path.exists(exe):
            return [exe]

    # 3. 开发环境：backend/bin/searxng/
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    searx_dir = os.path.join(base, "searxng")
    exe = os.path.join(searx_dir, "SearXNG for Windows.exe")
    if os.path.exists(exe):
        return [exe]
    # 回退：嵌入式 Python + webapp.py
    python_exe = os.path.join(searx_dir, "python", "python.exe")
    webapp_py = os.path.join(searx_dir, "python", "Lib", "site-packages", "searx", "webapp.py")
    if os.path.exists(python_exe) and os.path.exists(webapp_py):
        return [python_exe, webapp_py]
    return None


def _is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """检查端口是否已监听"""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


_SEARXNG_LOCK = asyncio.Lock()


async def _ensure_searxng() -> None:
    """确保 SearXNG 在运行。先检查端口，不可达则启动便携版并等待就绪。
    用 asyncio.Lock 防止并发启动。只允许一个协程执行启动，其余等待。
    """
    global _SEARXNG_PROC
    async with _SEARXNG_LOCK:
        if _is_port_open(_SEARXNG_PORT):
            return
        # 还没启动过或上次进程已死
        if _SEARXNG_PROC is not None and _SEARXNG_PROC.poll() is not None:
            _SEARXNG_PROC = None
        if _SEARXNG_PROC is not None:
            # 进程已在启动中，等待端口就绪
            deadline = time.time() + _SEARXNG_STARTUP_TIMEOUT
            while time.time() < deadline:
                if _is_port_open(_SEARXNG_PORT):
                    return
                await asyncio.sleep(0.3)
            logger.warning("SearXNG 等待就绪超时 (%ds)", _SEARXNG_STARTUP_TIMEOUT)
            return
        cmd = _get_searxng_launcher()
        if not cmd:
            logger.info("未找到便携版 SearXNG")
            return
        logger.info("正在启动便携版 SearXNG...")
        try:
            _SEARXNG_PROC = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=os.path.dirname(cmd[0]),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            deadline = time.time() + _SEARXNG_STARTUP_TIMEOUT
            while time.time() < deadline:
                if _is_port_open(_SEARXNG_PORT):
                    logger.info("SearXNG 已就绪")
                    return
                await asyncio.sleep(0.3)
            logger.warning("SearXNG 启动超时 (%ds)", _SEARXNG_STARTUP_TIMEOUT)
        except Exception as e:
            logger.warning("启动 SearXNG 失败: %s", str(e)[:80])

# ── 配置缓存（工具不在请求上下文中，直读 YAML。5s 缓存避免重复 I/O）──

_cfg_cache: dict | None = None
_cfg_ts = 0.0


def _get_config() -> dict:
    global _cfg_cache, _cfg_ts
    now = time.time()
    if _cfg_cache is not None and now - _cfg_ts < 5:
        return _cfg_cache
    from app.core.config import load_config
    _cfg_cache = load_config()
    _cfg_ts = now
    return _cfg_cache


# ── 公共工具 ──

#: DeepSeek 的 Search / Fetch 端点。
#:
#: ⚠️ **刻意不从 `providers[].base_url` 拼**：那是 `chat/completions` 的根，而这两条是
#: 独立的工具端点（同一把 Key、不同的服务）。用户自建的 OpenAI 兼容代理通常只转发
#: chat，拿它的根去拼 `/tools/search` 会 404；要过代理请配 `config.fetch.proxy`。
#: 端点常量与 Key 回退链住在 `core/deepseek_api.py`（OCR / ASR / Files 共用同一份）。
_TAVILY_URL = "https://api.tavily.com/search"


def _deepseek_key(sc: dict, cfg: dict) -> str:
    """DeepSeek Key —— 转发给 `core/deepseek_api.py::deepseek_api_key`。

    留这层薄壳是因为这里的两个调用点手上是 search 子配置；解析顺序本身
    （显式 → `agent_tools.search` → `providers` → `engine_providers`）只写一次。
    """
    return deepseek_api_key(cfg, (sc.get("deepseek_api_key") or "").strip())


async def _deepseek_search(query: str, max_results: int, api_key: str,
                           proxy: str = "") -> str:
    """DeepSeek Search API — 原生 JSON，**按成功请求计费**（$0.75 / 1000 次）。

    ⚠️ `need_content: False`：`summary`（600-800 字，官方写明 *intended to be read
    by a model*）已经够用；`content` 是整页正文，多取等于多花 token 却换不来信息密度
    （`compose_reference` 无论如何只取 400 字）。

    ⚠️ 计费口径决定了两件事：① **失败 / 空结果 / 超时都不收费**，所以「失败就降级」
       的代价只有延迟，不必为省钱在这里重试；② `count` **不影响单价**，取几条是纯质量
       决策。官方重试策略同样照此：408/429/5xx 才值得等，402 是充值，其余 4xx 不重试。
    """
    async with httpx.AsyncClient(timeout=15,
                                 proxy=proxy_for(TOOL_SEARCH_URL, proxy)) as client:
        r = await client.post(
            TOOL_SEARCH_URL,
            json={"query": query, "count": max_results, "need_content": False},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        data = r.json()
    items = data.get("results") if isinstance(data, dict) else None
    if not isinstance(items, list):
        # 显式抛而不是返回空列表：`_search_all` 对「结构异常」与「真的没搜到」
        # 的处置不同（前者要留日志证据），而空列表会把两件事抹平。
        raise ValueError(f"DeepSeek 返回结构异常: {str(data)[:120]}")
    return [
        {
            "title": x.get("title", ""),
            "url": x.get("url", ""),
            "snippet": (x.get("snippet") or "")[:300],
            # summary 是 DeepSeek 独有的「给模型读的摘要」；留在这里由
            # `compose_reference` 决定用不用（它对别家逐字零回归）。
            "summary": (x.get("summary") or "")[:800],
        }
        for x in items if isinstance(x, dict)
    ]


async def _deepseek_fetch(url: str, api_key: str, proxy: str = "") -> str:
    """DeepSeek Fetch API — 返回该页正文的 Markdown（截断 4000 字，与 trafilatura 同档）。

    ⚠️ 计费与 Search 同价；失败不收费。超时给 30s（与 Jina 同档）—— 抓取比搜索慢。
    """
    async with httpx.AsyncClient(timeout=30,
                                 proxy=proxy_for(TOOL_FETCH_URL, proxy)) as client:
        r = await client.post(
            TOOL_FETCH_URL,
            json={"url": url},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        raise ValueError(f"DeepSeek 返回结构异常: {str(data)[:120]}")
    content = (data.get("content") or "").strip()
    if not content:
        raise ValueError("DeepSeek 未返回正文")
    title = (data.get("title") or "").strip()
    result = (f"# {title}\n\n{content}" if title else content)[:4000]
    logger.info("DeepSeek Fetch: %d 字", len(result))
    return result


async def _tavily_search(query: str, max_results: int, api_key: str,
                        proxy: str = "") -> str:
    """Tavily Search API — AI 优化搜索，返回结构化结果"""
    async with httpx.AsyncClient(timeout=15,
                                 proxy=proxy_for(_TAVILY_URL, proxy)) as client:
        r = await client.post(
            _TAVILY_URL,
            json={"query": query, "max_results": max_results, "search_depth": "advanced"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        data = r.json()
        return [
            {"title": x["title"], "url": x["url"],
             "snippet": (x.get("content") or "")[:300]}
            for x in data.get("results", [])
        ]


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _default(kind: str, key: str) -> str:
    """`DEFAULT_CONFIG` 里的同名字段 —— 默认值只许住在那里。

    `load_config` 的深合并保证这些键恒存在，正常走不到这里；留它是为了手改 YAML
    删掉整段时不该抛 KeyError，而默认值本身不能有第二份。
    """
    from app.core.config import DEFAULT_CONFIG
    return str((DEFAULT_CONFIG["agent_tools"][kind] or {}).get(key) or "")


def _configured(cfg: dict, kind: str) -> str:
    """配置里的方案名；空值回退 `DEFAULT_CONFIG`。"""
    name = str(((cfg.get("agent_tools") or {}).get(kind) or {}).get("provider") or "").strip()
    return name or _default(kind, "provider")


async def _in_thread(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


@dataclass(frozen=True)
class _SearchPlan:
    """一个可用的搜索方案。`missing` 非空 = 缺前置条件（如 Key），调用前就跳过。"""

    missing: str
    call: Callable[[str, int], Awaitable[list[dict]]]


@dataclass(frozen=True)
class _FetchPlan:
    missing: str
    call: Callable[[str], Awaitable[str]]


#: 搜索的兜底方案。**它必须零配置可用** —— 兜底再要求 Key 就兜不住了。
_SEARCH_FALLBACK = "duckduckgo"
#: 抓取的兜底方案。trafilatura 是本地提取，不依赖第三方服务。
_FETCH_FALLBACK = "trafilatura"


async def _searxng_auto(query: str, n: int, base_url: str, proxy: str) -> list[dict]:
    """先确保本地实例在跑，再搜 —— 「可用」这一步是**动作**不是判断。

    `_ensure_searxng` 找不到可执行文件时静默返回，于是 `_searxng_search` 随后
    连接失败、由 runner 记进 `reason`。这一串不报错是刻意的：它是运行期失败，
    不是配置错误，runner 会把它降级并写明原因。
    """
    await _ensure_searxng()
    return await _searxng_search(query, n, base_url, proxy)


def _search_plans(cfg: dict, proxy: str) -> dict[str, _SearchPlan]:
    """把配置烘成可调用方案。加一家 = 这里加一行 + 一个 provider 函数。"""
    sc = (cfg.get("agent_tools") or {}).get("search") or {}
    ds_key = _deepseek_key(sc, cfg)
    tavily_key = str(sc.get("tavily_api_key") or "").strip()
    searx_url = str(sc.get("searxng_url") or "").strip() or _default("search", "searxng_url")
    return {
        "searxng": _SearchPlan(
            "", lambda q, n: _searxng_auto(q, n, searx_url, proxy)),
        "tavily": _SearchPlan(
            "" if tavily_key else "未填 API Key",
            lambda q, n: _tavily_search(q, n, tavily_key, proxy)),
        "deepseek": _SearchPlan(
            "" if ds_key else "未填 API Key",
            lambda q, n: _deepseek_search(q, n, ds_key, proxy)),
        "duckduckgo": _SearchPlan("", lambda q, n: _ddg_search(q, n, proxy)),
    }


def _fetch_plans(cfg: dict, proxy: str) -> dict[str, _FetchPlan]:
    sc = (cfg.get("agent_tools") or {}).get("search") or {}
    ds_key = _deepseek_key(sc, cfg)
    return {
        "trafilatura": _FetchPlan(
            "", lambda url: _in_thread(_trafilatura_fetch, url, proxy)),
        "jina": _FetchPlan("", lambda url: _jina_fetch(url, proxy)),
        "deepseek": _FetchPlan(
            "" if ds_key else "未填 API Key",
            lambda url: _deepseek_fetch(url, ds_key, proxy)),
    }


async def search_web(query: str, max_results: int = 5) -> str:
    """搜索互联网，返回 JSON：`{results, source, degraded, reason?}`。

    按 `agent_tools.search.provider` 选方案（searxng / tavily / deepseek / duckduckgo）。
    主方案失败或缺 Key 时降级 `_SEARCH_FALLBACK`，并在 `degraded` / `reason` 里写明
    原因 —— 此前这一步只记日志，于是「配了 Tavily、实际走了 DDG」在界面与回答里
    都看不出来，用户以为在用他配的那一家。

    ⚠️ 未知方案名**直接报错，不落兜底**：那是配置写错了（拼错、旧值），静默用兜底
    会把这个错误一直藏到有人去翻日志为止。
    """
    cfg = _get_config()
    proxy = (cfg.get("fetch") or {}).get("proxy", "")
    primary = _configured(cfg, "search")
    plans = _search_plans(cfg, proxy)

    plan = plans.get(primary)
    if plan is None:
        logger.error("未知的搜索方案: %r（可选：%s）", primary, "、".join(plans))
        return _json({"results": [], "error": f"未知的搜索方案：{primary}"})

    failures: list[str] = []
    if plan.missing:
        failures.append(f"{primary}：{plan.missing}")
    else:
        try:
            items = await plan.call(query, max_results)
            return _json({"results": items, "source": primary, "degraded": False})
        except Exception as e:
            failures.append(f"{primary}：{str(e)[:80]}")
            logger.warning("%s 搜索失败，降级 %s: %s", primary, _SEARCH_FALLBACK, str(e)[:80])

    if primary != _SEARCH_FALLBACK:
        try:
            items = await plans[_SEARCH_FALLBACK].call(query, max_results)
            return _json({
                "results": items,
                "source": _SEARCH_FALLBACK,
                "degraded": True,
                "reason": "；".join(failures),
            })
        except Exception as e:
            failures.append(f"{_SEARCH_FALLBACK}：{str(e)[:80]}")

    return _json({
        "results": [],
        "source": "none",
        "degraded": True,
        "reason": "；".join(failures),
    })


async def fetch_url(url: str) -> str:
    """抓取网页正文，返回 Markdown（截断 4000 字）。

    按 `agent_tools.fetch.provider` 选方案（trafilatura / jina / deepseek），
    失败或缺 Key 时降级 `_FETCH_FALLBACK`。

    ⚠️ 降级**不往正文里加提示**：兜底的 trafilatura 是本地提取，拿到的仍是这一页
    本身，加一句「本页由 xx 提取」只会污染模型要读的正文。搜索侧之所以要标记，
    是因为换一家搜索引擎的结果集完全不同（见模块文档）。
    """
    # SSRF 防护：校验 URL 安全性（协议 + 内网 IP）
    from app.utils.url_security import validate_url
    try:
        validate_url(url)
    except ValueError as e:
        return _json({"error": f"URL 安全校验失败: {str(e)[:120]}"})

    cfg = _get_config()
    proxy = (cfg.get("fetch") or {}).get("proxy", "")
    primary = _configured(cfg, "fetch")
    plans = _fetch_plans(cfg, proxy)

    plan = plans.get(primary)
    if plan is None:
        logger.error("未知的抓取方案: %r（可选：%s）", primary, "、".join(plans))
        return _json({"error": f"未知的抓取方案：{primary}"})

    failures: list[str] = []
    if plan.missing:
        failures.append(f"{primary}：{plan.missing}")
    else:
        try:
            return await plan.call(url)
        except Exception as e:
            failures.append(f"{primary}：{str(e)[:120]}")
            logger.warning("%s 抓取失败，降级 %s: %s", primary, _FETCH_FALLBACK, str(e)[:120])

    if primary != _FETCH_FALLBACK:
        try:
            return await plans[_FETCH_FALLBACK].call(url)
        except Exception as e:
            failures.append(f"{_FETCH_FALLBACK}：{str(e)[:120]}")

    logger.warning("网页抓取失败: %s", "；".join(failures))
    return _json({"error": f"抓取失败: {'；'.join(failures)}"})


# ── SearXNG ──

async def _searxng_search(query: str, max_results: int, base_url: str, proxy: str) -> str:
    client_kwargs: dict = {"timeout": 10.0}
    # ⚠️ 本地 SearXNG（默认 http://127.0.0.1:18080）**不该走代理**：以前这里只问
    # 「配了代理没」，不问「目标是谁」，于是本机地址也被塞进代理 —— 多数代理软件默认
    # 对回环直连，看不出问题，但那是运气不是设计。其余调用点（tavily / jina / ddg /
    # 用户给的 URL）目标恒为公网，不需要过这道判据。
    p = proxy_for(base_url, proxy)
    if p:
        client_kwargs["proxy"] = p
    async with httpx.AsyncClient(**client_kwargs) as c:
        r = await c.get(f"{base_url}/search", params={"q": query, "format": "json"})
        r.raise_for_status()
        data = r.json()
        results = [{
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:300],
        } for item in data.get("results", [])[:max_results]]
        logger.info("SearXNG: %d 条结果", len(results))
        return results


# ── DuckDuckGo（真实网页搜索）──

async def _ddg_search(query: str, max_results: int, proxy: str) -> list[dict]:
    """使用 duckduckgo_search 库进行真实网页搜索（替代废弃的 Instant Answer API）"""
    return await _in_thread(_ddg_search_sync, query, max_results, proxy)


def _ddg_search_sync(query: str, max_results: int, proxy: str) -> list[dict]:
    """同步 DDG 搜索，在 executor 中运行。

    ⚠️ **不在这里吞异常**：失败要由 runner 记进 `reason`，否则「DDG 也挂了」与
    「DDG 搜到了 0 条」在返回值上完全相同 —— 而前者需要用户去查网络。
    """
    from duckduckgo_search import DDGS
    with DDGS(proxy=proxy or None) as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    items = [{
        "title": r.get("title", ""),
        "url": r.get("href", ""),
        "snippet": (r.get("body", "") or "")[:300],
    } for r in results]
    logger.info("DDG: %d 条结果", len(items))
    return items


# ── 网页抓取 ──

def _trafilatura_fetch(url: str, proxy: str) -> str:
    """用 trafilatura 提取网页正文（同步，由 run_in_executor 调用）"""
    import trafilatura
    from app.utils.url_security import safe_get_httpx

    # 用 httpx 先下载（支持 proxy），再用 trafilatura 提取。
    # ⚠️ `follow_redirects` 已交给 safe_get_httpx 逐跳校验 —— 裸
    # `httpx.get(follow_redirects=True)` 会让公网 URL 302 到 127.0.0.1 绕过首跳校验。
    client_kwargs: dict = {"timeout": 15.0}
    # 按**用户要抓的那个 URL** 判 —— 这条路径请求的就是它本身（不像 `_jina_fetch`
    # 请求的是 jina 的转发地址）。直连域名配上之后，抓国内网页不再经代理。
    p = proxy_for(url, proxy)
    if p:
        client_kwargs["proxy"] = p
    try:
        resp = safe_get_httpx(url, **client_kwargs)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, output_format="markdown")
    except ValueError:
        # 安全校验失败必须冒出去 —— 回退路径同样是网络请求，不能拿它兜底
        raise
    except Exception:
        # 下载失败时回退（无 proxy）。⚠️ 不用 `trafilatura.fetch_url`：它自己
        # 发请求且跟随重定向，等于把刚校验过的那一跳换成一条未校验的路。
        resp = safe_get_httpx(url, timeout=15.0)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, output_format="markdown")
    result = (text or "无内容")[:4000]
    logger.info("trafilatura: %d 字", len(result))
    return result


async def _jina_fetch(url: str, proxy: str) -> str:
    """用 Jina Reader 抓取（免费在线，URL 加前缀即可）"""
    client_kwargs: dict = {"timeout": 30.0}
    # 判的是 **jina 的地址**不是用户 URL：这条路径真正发出请求的对象是 r.jina.ai，
    # 「目标站直连」在 Jina 路由下本来就实现不了（正文由 Jina 抓）。
    p = proxy_for("https://r.jina.ai/", proxy)
    if p:
        client_kwargs["proxy"] = p
    async with httpx.AsyncClient(**client_kwargs) as c:
        r = await c.get(f"https://r.jina.ai/{url}")
        r.raise_for_status()
        result = r.text[:4000]
        logger.info("Jina: %d 字", len(result))
        return result
