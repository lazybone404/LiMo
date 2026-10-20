"""
Agent 工具 — 网络搜索 + 网页抓取

给 AgentRunner 注册用的 search_web / fetch_url。
SearXNG 优先，不可达自动降级 DuckDuckGo。
HTTP 请求统一走 config.fetch.proxy（**本地地址除外** —— 判据收口在 app/core/net.py）。
"""

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.parse

import httpx

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

async def _tavily_search(query: str, max_results: int, api_key: str,
                        proxy: str = "") -> str:
    """Tavily Search API — AI 优化搜索，返回结构化结果"""
    async with httpx.AsyncClient(timeout=15, proxy=proxy or None) as client:
        r = await client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": max_results, "search_depth": "advanced"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        data = r.json()
        return json.dumps({
            "results": [
                {"title": x["title"], "url": x["url"],
                 "snippet": (x.get("content") or "")[:300]}
                for x in data.get("results", [])
            ],
            "source": "tavily",
        }, ensure_ascii=False)


async def search_web(query: str, max_results: int = 5) -> str:
    """搜索互联网，返回 JSON 结果列表。

    按 agent_tools.search.provider 选择方案：
      tavily → Tavily API（AI 优化，需 API Key）
      searxng → 本地 SearXNG（免费），不可达时降级 DuckDuckGo
      duckduckgo → 直接 DDG
    """
    cfg = _get_config()
    proxy = cfg.get("fetch", {}).get("proxy", "")
    sc = cfg.get("agent_tools", {}).get("search", {})
    # 兜底默认（load_config 深合并保证键恒存在，正常走不到这里）；
    # 取值须与 config.py::DEFAULT_CONFIG 的同名字段一致，否则就是第二个真相源
    provider = sc.get("provider", "duckduckgo")

    if provider == "tavily":
        api_key = sc.get("tavily_api_key", "")
        if not api_key:
            logger.warning("Tavily API key 未配置，降级 DuckDuckGo")
        else:
            try:
                return await _tavily_search(query, max_results, api_key, proxy)
            except Exception as e:
                logger.warning("Tavily 不可达，降级 DuckDuckGo: %s", str(e)[:80])
    elif provider == "searxng":
        url = sc.get("searxng_url", "http://127.0.0.1:18080")
        await _ensure_searxng()
        try:
            return await _searxng_search(query, max_results, url, proxy)
        except Exception as e:
            logger.warning("SearXNG 不可达，降级 DuckDuckGo: %s", str(e)[:80])
    return await _ddg_search(query, max_results, proxy)


async def fetch_url(url: str) -> str:
    """抓取网页正文，返回 Markdown（截断 4000 字）。

    按 agent_tools.fetch.provider 选择方案。
    同布 trafilatura 用 run_in_executor 防阻塞事件循环。
    """
    # SSRF 防护：校验 URL 安全性（协议 + 内网 IP）
    from app.utils.url_security import validate_url
    try:
        validate_url(url)
    except ValueError as e:
        return json.dumps({"error": f"URL 安全校验失败: {str(e)[:120]}"}, ensure_ascii=False)

    cfg = _get_config()
    proxy = cfg.get("fetch", {}).get("proxy", "")
    fc = cfg.get("agent_tools", {}).get("fetch", {})
    provider = fc.get("provider", "trafilatura")

    try:
        if provider == "jina":
            return await _jina_fetch(url, proxy)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _trafilatura_fetch, url, proxy)
    except Exception as e:
        return json.dumps({"error": f"抓取失败: {str(e)[:120]}"}, ensure_ascii=False)


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
        return json.dumps({"results": results, "source": "searxng"}, ensure_ascii=False)


# ── DuckDuckGo（真实网页搜索）──

async def _ddg_search(query: str, max_results: int, proxy: str) -> str:
    """使用 duckduckgo_search 库进行真实网页搜索（替代废弃的 Instant Answer API）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ddg_search_sync, query, max_results, proxy)


def _ddg_search_sync(query: str, max_results: int, proxy: str) -> str:
    """同步 DDG 搜索，在 executor 中运行"""
    try:
        from duckduckgo_search import DDGS
        with DDGS(proxy=proxy or None) as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        items = []
        for r in results:
            items.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": (r.get("body", "") or "")[:300],
            })
        logger.info("DDG: %d 条结果", len(items))
        return json.dumps({"results": items, "source": "duckduckgo"}, ensure_ascii=False)
    except Exception as e:
        logger.warning("DDG 搜索失败: %s", str(e)[:80])
        return json.dumps({"results": [], "source": "duckduckgo_error"}, ensure_ascii=False)


# ── 网页抓取 ──

def _trafilatura_fetch(url: str, proxy: str) -> str:
    """用 trafilatura 提取网页正文（同步，由 run_in_executor 调用）"""
    import trafilatura
    from app.utils.url_security import safe_get_httpx

    # 用 httpx 先下载（支持 proxy），再用 trafilatura 提取。
    # ⚠️ `follow_redirects` 已交给 safe_get_httpx 逐跳校验 —— 裸
    # `httpx.get(follow_redirects=True)` 会让公网 URL 302 到 127.0.0.1 绕过首跳校验。
    client_kwargs: dict = {"timeout": 15.0}
    if proxy:
        client_kwargs["proxy"] = proxy
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
    if proxy:
        client_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**client_kwargs) as c:
        r = await c.get(f"https://r.jina.ai/{url}")
        r.raise_for_status()
        result = r.text[:4000]
        logger.info("Jina: %d 字", len(result))
        return result
