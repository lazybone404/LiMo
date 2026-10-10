"""代理豁免表 —— app/core/net.py 的真值表 + 接线行为。

背景（2026-10-10）：启动时把 `fetch.proxy` 注入成全局环境变量，但没有豁免表 ——
本地 SearXNG（`http://127.0.0.1:18080`）和自建 LLM 网关（`http://localhost:8000`）
也一并被塞进代理。多数代理软件默认对回环直连，所以平时看不出问题。
"""

import asyncio
import pathlib

import pytest

from app.agent import tools
from app.core.net import (
    NO_PROXY_VALUE, apply_proxy_env, is_local_url, merge_no_proxy, proxy_for,
)

PROXY = "http://127.0.0.1:7890"


# ══════════════ ① is_local_url ══════════════

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18080",
    "http://127.0.0.2",          # 127/8 整段，不只是 .1
    "https://localhost:8000/v1",  # 自建 LLM 网关的典型形态
    "http://[::1]:8080",
    "http://0.0.0.0:8000",
    "http://192.168.1.5:8888",   # 私网（SearXNG 跑在局域网另一台机器）
    "http://10.0.0.1",
    "http://172.16.0.1",
    "http://169.254.1.1",        # link-local
    "localhost:18080",           # 裸主机（无 scheme）—— urlparse 会把它当 scheme
    # 保留私有域后缀：NAS / 路由器 / 打印机这类「局域网里有名字没 IP」的设备
    "http://nas.local",
    "http://printer.local:631",
    "http://gateway.home.arpa",
    "http://llm-gw.internal/v1",
])
def test_local_urls_are_recognized(url):
    assert is_local_url(url), url


@pytest.mark.parametrize("url", [
    "https://api.tavily.com",
    "https://www.youtube.com/watch?v=x",
    "http://8.8.8.8",            # 公网 IP
    "http://172.32.0.1",         # ⚠️ 只在 172.16-31 内才是私网，这是边界外侧
    "https://api.deepseek.com/v1",
    # ⚠️ 后缀判据的**反面对照**：含 "local"/"internal" 字样但**不是**该保留后缀。
    #    写成 `"local" in host` 或 `endswith("local")` 的实现会在这里红。
    "https://notlocal.com",
    "https://api.localstack.com",
    "https://internal.example.com",
    "",
])
def test_public_urls_are_not_local(url):
    """对照实验：判据必须有区分力 —— 一个「什么都判成本地」的实现会让代理全线失效。"""
    assert not is_local_url(url), url


def test_malformed_url_does_not_raise():
    """畸形 URL（未闭合的 IPv6 方括号）不能让整个调用链炸掉。"""
    assert is_local_url("http://[::1") is False


# ══════════════ ② proxy_for ══════════════

def test_proxy_for_public():
    assert proxy_for("https://api.tavily.com", PROXY) == PROXY


def test_proxy_for_local_is_none():
    assert proxy_for("http://127.0.0.1:18080", PROXY) is None


def test_proxy_for_no_proxy_configured():
    """没配代理 → None（不该凭空造一个空串代理塞给 httpx）。"""
    assert proxy_for("https://api.tavily.com", "") is None
    assert proxy_for("https://api.tavily.com", "   ") is None


# ══════════════ ③ merge_no_proxy：不覆盖已有项 ══════════════

def test_merge_into_empty():
    assert merge_no_proxy("") == NO_PROXY_VALUE


def test_merge_keeps_existing_entries():
    """⚠️ 核心性质：用户 / 系统预先配好的豁免项**不能**被静默抹掉。"""
    merged = merge_no_proxy("example.com,10.1.2.3")
    assert merged.startswith("example.com,10.1.2.3")
    assert "localhost" in merged and "127.0.0.1" in merged


def test_merge_is_idempotent():
    once = merge_no_proxy("")
    assert merge_no_proxy(once) == once


def test_merge_trims_whitespace():
    assert merge_no_proxy(" a.com , b.com ") == f"a.com,b.com,{NO_PROXY_VALUE}"


# ══════════════ ④ apply_proxy_env ══════════════

def test_apply_proxy_env_sets_all_six_vars():
    env: dict = {}
    apply_proxy_env(PROXY, env)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[name] == PROXY, name
    # 两个大小写都要设：requests 读小写优先、httpx 读大写优先
    assert env["NO_PROXY"] == NO_PROXY_VALUE
    assert env["no_proxy"] == NO_PROXY_VALUE


def test_apply_proxy_env_preserves_existing_no_proxy():
    env = {"NO_PROXY": "corp.internal"}
    apply_proxy_env(PROXY, env)
    assert env["NO_PROXY"].startswith("corp.internal")
    assert env["no_proxy"] == env["NO_PROXY"]


def test_apply_proxy_env_is_noop_without_proxy():
    """对照实验：没配代理时不该往环境里塞任何东西。"""
    env: dict = {}
    apply_proxy_env("", env)
    assert env == {}


# ══════════════ ⑤ 接线行为：SearXNG 实际传了什么 ══════════════

class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"results": []}


class _FakeAsyncClient:
    """记录构造入参，用来断言「这个请求到底走没走代理」。"""

    captured: dict = {}

    def __init__(self, **kwargs):
        _FakeAsyncClient.captured = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse()


def test_searxng_local_skips_proxy(monkeypatch):
    """本地 SearXNG 必须**不带** proxy 参数。"""
    monkeypatch.setattr(tools.httpx, "AsyncClient", _FakeAsyncClient)
    asyncio.run(tools._searxng_search("q", 5, "http://127.0.0.1:18080", PROXY))
    assert "proxy" not in _FakeAsyncClient.captured


def test_searxng_remote_still_uses_proxy(monkeypatch):
    """对照实验：远程 SearXNG 必须**照常**走代理 ——
    否则「豁免」会退化成「全线不代理」，那比不豁免更糟（国内根本连不上）。"""
    monkeypatch.setattr(tools.httpx, "AsyncClient", _FakeAsyncClient)
    asyncio.run(tools._searxng_search("q", 5, "https://searx.example.com", PROXY))
    assert _FakeAsyncClient.captured["proxy"] == PROXY


# ══════════════ ⑥ 接线守卫：豁免表真的被装上 ══════════════

def test_main_calls_apply_proxy_env():
    """把逻辑抽成函数后，**得有人调它** —— 少这一步等于豁免表根本没生效。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "apply_proxy_env(" in src, "main.py 必须调用 apply_proxy_env 装上豁免表"
