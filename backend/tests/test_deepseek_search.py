"""DeepSeek Search / Fetch 接入 —— 归一、计费口径、以及**失败一律降级**。

DeepSeek 与既有 tavily / searxng 的差别有三处，每处都对应一条要钉的东西：

1. **计费口径是「按成功请求」**（$0.75 / 1000 次，失败 / 空结果 / 超时不收费）⇒
   「失败就降级」的代价只有延迟，不必在这里重试。测试钉的是「降级真的发生了」，
   而不是「重试了几次」—— 后者是把计费模型抄成代码，服务端一改就对不上。
2. **响应带 `summary`**（官方写明 *intended to be read by a model*），比一二百字的
   `snippet` 信息密度高得多。它必须原样穿过归一层交给 `compose_reference` 决定用不用
   —— 归一阶段就丢掉的话，下游再想用也没有了。
3. **Key 可回退 `providers`**：用户已经为对话配过 DeepSeek Key，不该再抄一遍。

⚠️ **负向对照不能省**（本文件第 5、7 节）：`httpx.AsyncClient` 的构造被计数，
断言「没 Key 时一次都没构造过」。只看「最终降级到了 DDG」是区分不出「先白请求一次
再降级」的 —— 而后者每问一句都在花钱，且现象与前者逐字节相同。
"""
import asyncio
import json

import httpx
import pytest

from app.agent import tools


# ═══════════════════════════════════════════════════════════
# 测试替身
# ═══════════════════════════════════════════════════════════

class _Resp:
    """最小 `httpx.Response` 子集：`raise_for_status` 与 `json`。"""

    def __init__(self, status: int, payload, url: str = "https://api.deepseek.com/x"):
        self.status_code = status
        self._payload = payload
        self._req = httpx.Request("POST", url)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self._req,
                response=httpx.Response(self.status_code, request=self._req),
            )

    def json(self):
        return self._payload


class _Client:
    """替身 `httpx.AsyncClient` —— 记下每次构造与 post 的入参。"""

    instances: list["_Client"] = []

    def __init__(self, resp: _Resp, record: dict, *a, **kw):
        self._resp = resp
        self._rec = record
        record.setdefault("constructed", 0)
        record["constructed"] += 1
        record["client_kwargs"] = kw
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        self._rec["url"] = url
        self._rec["json"] = kw.get("json")
        self._rec["headers"] = kw.get("headers") or {}
        return self._resp


@pytest.fixture(autouse=True)
def _reset_instances():
    _Client.instances = []
    yield
    _Client.instances = []


def _patch_http(monkeypatch, status: int, payload) -> dict:
    """把 `httpx.AsyncClient` 换成替身，返回记录字典。"""
    rec: dict = {}
    monkeypatch.setattr(
        tools.httpx, "AsyncClient", lambda *a, **kw: _Client(_Resp(status, payload), rec, *a, **kw))
    return rec


def _cfg(provider: str = "deepseek", key: str = "sk-x", providers: list | None = None) -> dict:
    return {
        "agent_tools": {
            "search": {"provider": provider, "deepseek_api_key": key},
            "fetch": {"provider": "trafilatura"},
        },
        "fetch": {"proxy": ""},
        "providers": providers or [],
    }


def _sentinel_ddg(monkeypatch) -> list[dict]:
    """把 DDG 换成哨兵 —— 断言「降级到它」比断言「结果里有东西」精确得多。"""
    calls: list[dict] = []

    async def _fake(query, max_results, proxy):
        calls.append({"query": query, "max_results": max_results})
        return '{"results": [], "source": "duckduckgo"}'

    monkeypatch.setattr(tools, "_ddg_search", _fake)
    return calls


def _no_ssrf_check(monkeypatch) -> None:
    """`fetch_url` 开头的 `validate_url` 会真做 DNS 解析 ⇒ 示例域名解析失败就被拦下。

    本节测的是「HTTP 失败怎么降级」，与 SSRF 无关，故把它短路掉。
    """
    monkeypatch.setattr("app.utils.url_security.validate_url", lambda url: None)


# ═══════════════════════════════════════════════════════════
# 1. 请求体与鉴权
# ═══════════════════════════════════════════════════════════

def test_请求体只发三个字段(monkeypatch):
    """`need_content: False` 是**省钱**的直接开关（`content` 是整页正文）。

    `count` 不影响单价（按请求计费），所以取几条是纯质量决策 —— 这里只钉它传对了。
    """
    rec = _patch_http(monkeypatch, 200, {"results": []})

    asyncio.run(tools._deepseek_search("量子计算", 7, "sk-abc"))

    assert rec["url"] == tools._DEEPSEEK_SEARCH_URL
    assert rec["json"] == {"query": "量子计算", "count": 7, "need_content": False}
    assert rec["headers"]["Authorization"] == "Bearer sk-abc"


def test_端点常量不依赖provider_base_url():
    """⚠️ 两条端点**刻意写死**，不从 `providers[].base_url` 拼。

    后者是 `chat/completions` 的根；用户自建的 OpenAI 兼容代理通常只转发 chat，
    拿它的根拼 `/tools/search` 会 404 —— 而那看起来像「DeepSeek 挂了」。
    """
    assert tools._DEEPSEEK_SEARCH_URL == "https://api.deepseek.com/v1/tools/search"
    assert tools._DEEPSEEK_FETCH_URL == "https://api.deepseek.com/v1/tools/fetch"


# ═══════════════════════════════════════════════════════════
# 2. 响应归一
# ═══════════════════════════════════════════════════════════

def test_归一成契约形状_并保留summary(monkeypatch):
    """`summary` 必须穿过归一层 —— 归一阶段丢掉，下游再想用也没了。"""
    _patch_http(monkeypatch, 200, {"results": [
        {"title": "标题甲", "url": "https://a.example/1",
         "snippet": "片段甲", "summary": "摘要甲" * 10,
         "content": "整页正文" * 100, "authority": 3, "site_name": "某站"},
        {"title": "标题乙", "url": "https://b.example/2"},
    ]})

    data = json.loads(asyncio.run(tools._deepseek_search("q", 5, "k")))

    assert data["source"] == "deepseek"
    assert len(data["results"]) == 2
    # 只留契约里的四个键 —— 其余（authority / site_name / content）不在任何消费方
    assert set(data["results"][0]) == {"title", "url", "snippet", "summary"}
    assert data["results"][0]["summary"].startswith("摘要甲")
    # 缺字段的条目照样归一，不 KeyError
    assert data["results"][1]["snippet"] == ""


def test_超长字段截断(monkeypatch):
    """截断在**归一阶段**做，与 tavily / searxng 的 300 字同档。

    summary 给 800（它本来就是 600-800 字），下游 `compose_reference` 再按
    `SNIPPET_CHARS = 400` 收一次 —— 两级各管一件事，不是重复。
    """
    _patch_http(monkeypatch, 200, {"results": [{
        "title": "t", "url": "u", "snippet": "s" * 999, "summary": "m" * 9999}]})

    r = json.loads(asyncio.run(tools._deepseek_search("q", 5, "k")))["results"][0]

    assert len(r["snippet"]) == 300
    assert len(r["summary"]) == 800


def test_非dict条目被丢弃(monkeypatch):
    _patch_http(monkeypatch, 200, {"results": [{"title": "ok", "url": "u"}, "垃圾", None]})

    r = json.loads(asyncio.run(tools._deepseek_search("q", 5, "k")))["results"]

    assert len(r) == 1 and r[0]["title"] == "ok"


def test_结构异常显式抛而不是返回空列表(monkeypatch):
    """⚠️ 「结构异常」与「真的没搜到」的处置不同（前者要留日志证据）。

    返回空列表会把两件事抹平 —— `_search_all` 打印的 warning 就成了唯一线索，
    而它读到的形状已经是对的。
    """
    _patch_http(monkeypatch, 200, {"data": []})

    with pytest.raises(ValueError, match="结构异常"):
        asyncio.run(tools._deepseek_search("q", 5, "k"))


# ═══════════════════════════════════════════════════════════
# 3. Key 来源：显式优先 → 回退 providers
# ═══════════════════════════════════════════════════════════

def test_key_显式优先():
    cfg = {"providers": [{"id": "deepseek", "api_key": "sk-provider"}]}
    assert tools._deepseek_key({"deepseek_api_key": "sk-explicit"}, cfg) == "sk-explicit"


def test_key_为空回退providers():
    cfg = {"providers": [{"id": "deepseek", "api_key": "sk-provider"}]}
    assert tools._deepseek_key({}, cfg) == "sk-provider"
    assert tools._deepseek_key({"deepseek_api_key": "  "}, cfg) == "sk-provider"


def test_key_回退只认id不按base_url猜():
    """同一家用户完全可能配两条不同 base_url 的 provider；`id` 才是身份。"""
    cfg = {"providers": [
        {"id": "other", "api_key": "sk-wrong"},
        {"id": "deepseek", "api_key": "sk-right"},
    ]}
    assert tools._deepseek_key({}, cfg) == "sk-right"


def test_key_都没有返回空串():
    assert tools._deepseek_key({}, {}) == ""
    assert tools._deepseek_key({"deepseek_api_key": None}, {"providers": None}) == ""


# ═══════════════════════════════════════════════════════════
# 4. 失败一律降级 DuckDuckGo（含 4xx 与 5xx）
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("status", [401, 402, 403, 429, 500, 503])
def test_HTTP错误降级DDG(monkeypatch, status):
    """402 是「充值」、401/403 是 Key 问题、429/5xx 是服务端 —— **一律降级**。

    官方重试策略（408/429/5xx 可等）**刻意不实现**：在线检索里多等一轮就是多等一轮，
    而失败语义已经由降级表达清楚了；重试只会在「服务端真的挂了」时把延迟翻倍。
    """
    monkeypatch.setattr(tools, "_get_config", lambda: _cfg(key="sk-x"))
    _patch_http(monkeypatch, status, {"error": "nope"})
    ddg = _sentinel_ddg(monkeypatch)

    out = asyncio.run(tools.search_web("问题"))

    assert len(ddg) == 1, "必须降级到 DDG"
    assert json.loads(out)["source"] == "duckduckgo"


def test_响应不是JSON也降级(monkeypatch):
    """反序列化失败同样只是「这次搜索没成」，不是「对话该崩」。"""
    monkeypatch.setattr(tools, "_get_config", lambda: _cfg(key="sk-x"))

    class _BadJson(_Resp):
        def json(self):
            raise ValueError("Expecting value")

    monkeypatch.setattr(tools.httpx, "AsyncClient",
                        lambda *a, **kw: _Client(_BadJson(200, None), {}, *a, **kw))
    ddg = _sentinel_ddg(monkeypatch)

    asyncio.run(tools.search_web("q"))

    assert len(ddg) == 1


def test_成功时一次DDG都不调(monkeypatch):
    """⚠️ 对照：证明上一条的 `len(ddg) == 1` 不是「反正总会被调用」。"""
    monkeypatch.setattr(tools, "_get_config", lambda: _cfg(key="sk-x"))
    _patch_http(monkeypatch, 200, {"results": [{"title": "t", "url": "u", "summary": "s"}]})
    ddg = _sentinel_ddg(monkeypatch)

    out = asyncio.run(tools.search_web("q"))

    assert ddg == []
    assert json.loads(out)["source"] == "deepseek"


# ═══════════════════════════════════════════════════════════
# 5. 负向对照：没 Key 时**一次请求都不发**
# ═══════════════════════════════════════════════════════════

def test_无Key不发请求直接降级(monkeypatch):
    """只断言「最后降级了」区分不出「先白请求一次再降级」—— 后者每问一句都在花钱。"""
    rec = _patch_http(monkeypatch, 200, {"results": []})
    monkeypatch.setattr(tools, "_get_config", lambda: _cfg(key="", providers=[]))
    ddg = _sentinel_ddg(monkeypatch)

    asyncio.run(tools.search_web("q"))

    assert rec.get("constructed", 0) == 0, "没 Key 就不该构造 HTTP 客户端"
    assert len(ddg) == 1


def test_非deepseek方案完全不受影响(monkeypatch):
    """零回归：provider 不是 deepseek 时，这段新分支一个字节都不参与。"""
    rec = _patch_http(monkeypatch, 200, {"results": []})
    monkeypatch.setattr(tools, "_get_config", lambda: _cfg(provider="duckduckgo"))
    ddg = _sentinel_ddg(monkeypatch)

    asyncio.run(tools.search_web("q"))

    assert rec.get("constructed", 0) == 0
    assert len(ddg) == 1


# ═══════════════════════════════════════════════════════════
# 6. Fetch
# ═══════════════════════════════════════════════════════════

def test_fetch_拼标题并截断4000(monkeypatch):
    rec = _patch_http(monkeypatch, 200, {"title": "论文标题", "content": "正文" * 5000})

    out = asyncio.run(tools._deepseek_fetch("https://x.example/p", "sk-k"))

    assert rec["url"] == tools._DEEPSEEK_FETCH_URL
    assert rec["json"] == {"url": "https://x.example/p"}
    assert out.startswith("# 论文标题\n\n")
    assert len(out) == 4000


def test_fetch_无标题不拼空标题(monkeypatch):
    _patch_http(monkeypatch, 200, {"content": "正文"})

    assert asyncio.run(tools._deepseek_fetch("https://x.example/p", "sk-k")) == "正文"


def test_fetch_空正文抛错(monkeypatch):
    _patch_http(monkeypatch, 200, {"content": "   "})

    with pytest.raises(ValueError, match="未返回正文"):
        asyncio.run(tools._deepseek_fetch("https://x.example/p", "sk-k"))


def test_fetch_url走deepseek分支(monkeypatch):
    """Key 住在 `agent_tools.search` 段（与搜索共用一把），不是 `fetch` 段。"""
    _no_ssrf_check(monkeypatch)
    rec = _patch_http(monkeypatch, 200, {"title": "T", "content": "C"})
    cfg = _cfg(key="sk-shared")
    cfg["agent_tools"]["fetch"]["provider"] = "deepseek"
    monkeypatch.setattr(tools, "_get_config", lambda: cfg)

    out = asyncio.run(tools.fetch_url("https://x.example/p"))

    assert rec["url"] == tools._DEEPSEEK_FETCH_URL
    assert out == "# T\n\nC"


def test_fetch_url失败降级trafilatura(monkeypatch):
    _no_ssrf_check(monkeypatch)
    _patch_http(monkeypatch, 500, {"error": "boom"})
    cfg = _cfg(key="sk-shared")
    cfg["agent_tools"]["fetch"]["provider"] = "deepseek"
    monkeypatch.setattr(tools, "_get_config", lambda: cfg)
    monkeypatch.setattr(tools, "_trafilatura_fetch", lambda url, proxy: "本地提取的正文")

    assert asyncio.run(tools.fetch_url("https://x.example/p")) == "本地提取的正文"


def test_fetch_url无Key不发请求(monkeypatch):
    _no_ssrf_check(monkeypatch)
    rec = _patch_http(monkeypatch, 200, {"content": "C"})
    cfg = _cfg(key="", providers=[])
    cfg["agent_tools"]["fetch"]["provider"] = "deepseek"
    monkeypatch.setattr(tools, "_get_config", lambda: cfg)
    monkeypatch.setattr(tools, "_trafilatura_fetch", lambda url, proxy: "本地提取的正文")

    assert asyncio.run(tools.fetch_url("https://x.example/p")) == "本地提取的正文"
    assert rec.get("constructed", 0) == 0
