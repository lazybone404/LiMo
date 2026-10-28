"""`rerank_runtime` 的编排测试 + `/search/rerank` 端点 + `status.rerank` 段。

**为什么需要这个文件**：与 `vector_runtime` 同类的「开关 → 运行时对象」判据，
它的失效形态**全都不报错**：

- 打开开关但 `app.state.rerank_search` 是 `None` → 配置写着开着、对话检索一次
  都不重排，界面上看不出任何异常（只会觉得「开了没什么区别」）
- 关掉开关但对象还在 → 用户以为不发了，**候选文档的正文仍在往云端送**并计费
- 引擎找不到 / 自检不过时静默当「没开」→ 用户打开开关、付了钱（如果它真跑了）
  或什么也没发生，两种情况下他都没有可读的原因

⚠️ 本文件用 `SimpleNamespace` 造 app（同 `test_vector_runtime.py`），端点那几条
用 `TestClient` 但不进 startup（同 `test_search_api.py` —— 那是**当前仓库唯一**能
安全复用的手法，两处都在注释里写明了理由）。

对照实验（均实测，见文末「对照实验」段）：
  A. `sync_rerank_runtime` 的 `_off()` 里删掉 `app.state.rerank_search = None`
     → `test_关掉开关必须摘掉编排对象` 红
  B. 把 `_engine_precheck` 的结果丢掉（无条件建 `RerankSearcher`）
     → `test_自检不过时带原因停用` 红
  C. 端点里把 `max(1, int(body.candidates))` 改成 `max(0, ...)`
     → `test_candidates_入口夹到_1` 红
  D. `_configured_rerank_candidates` 改成 `max(1, ...)`（与入口同款）
     → `test_candidates_磁盘脏值回退默认` 红
"""
import os
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.config import DEFAULT_CONFIG
from app.core.registry import EngineRegistry
from app.services import rerank_runtime as rr
from app.services import rerank_search as rs


class _Engine:
    """重排引擎的形状：三个必填属性 + `rerank()` + 可选 `precheck()`。"""

    name = "fake-rerank"
    display_name = "fake（测试）"
    model_id = "fake-model"
    max_documents = 50
    is_local = False

    def __init__(self):
        self.precheck_result = None
        self.precheck_raises = False

    def precheck(self):
        if self.precheck_raises:
            raise RuntimeError("依赖没装")
        return self.precheck_result

    async def rerank(self, query, documents):
        return [(i, 0.0) for i in range(len(documents))]


def _app(cfg: dict) -> SimpleNamespace:
    app = SimpleNamespace(state=SimpleNamespace())
    app.state.config = {"search": dict(cfg)}
    # 预置一个「上一轮留下的」对象：成功分支要覆盖它，停用分支要摘掉它
    app.state.rerank_search = "stale"
    return app


@pytest.fixture()
def engine(monkeypatch):
    eng = _Engine()
    # 换掉整个注册表（进程内单例，别处的测试可能注册过真引擎 —— 不隔离的话
    # `EngineRegistry.get` 拿到的会是真 DashScope 引擎，`precheck` 会去查 Key）。
    monkeypatch.setattr(EngineRegistry, "_stores", {"rerank": {eng.name: eng}})
    return eng


# ═══════════════════════════════════════════════════════════════
# 运行时：三条「开」的路径 + 四条「关」的路径
# ═══════════════════════════════════════════════════════════════

def test_打开开关装上编排对象(engine):
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    r = rr.sync_rerank_runtime(app)
    assert r == {"enabled": True, "reason": None}, r
    assert app.state.rerank_search is not None, "成功分支必须装上对象"
    assert isinstance(app.state.rerank_search, rs.RerankSearcher)
    assert app.state.rerank_runtime == r


def test_关掉开关必须摘掉编排对象(engine):
    """① 号缺陷：关掉开关但对象还在 = 候选正文仍在往云端送，而界面写着「已关闭」。"""
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    rr.sync_rerank_runtime(app)
    assert app.state.rerank_search is not None

    app.state.config["search"]["rerank_enabled"] = False
    r = rr.sync_rerank_runtime(app)
    # 关掉是**用户的决定**，不是故障 —— `reason` 必须为空（否则设置页会挂一句红字）
    assert r == {"enabled": False, "reason": None}, r
    assert app.state.rerank_search is None, "关掉后对象必须摘掉"


def test_引擎找不到时带原因停用(engine):
    app = _app({"rerank_enabled": True, "rerank_model": "nope"})
    r = rr.sync_rerank_runtime(app)
    assert r["enabled"] is False and "nope" in r["reason"], r
    assert app.state.rerank_search is None


def test_自检不过时带原因停用(engine):
    """`precheck()` 返回原因 ⇒ 停用 + **把原话带给用户**（他照着那句话装依赖）。"""
    engine.precheck_result = "缺少 onnxruntime，请运行 pip install …"
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    r = rr.sync_rerank_runtime(app)
    assert r["enabled"] is False
    assert r["reason"] == engine.precheck_result, r
    assert app.state.rerank_search is None


def test_自检自己炸了也只是停用(engine):
    engine.precheck_raises = True
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    r = rr.sync_rerank_runtime(app)
    assert r["enabled"] is False and "RuntimeError" in r["reason"], r


def test_没有_precheck_钩子视为就绪(engine, monkeypatch):
    """云端引擎没有 `precheck` —— **缺失 ≠ 未就绪**（同 embedding 三个钩子的判据）。"""
    monkeypatch.delattr(_Engine, "precheck", raising=False)
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    assert rr.sync_rerank_runtime(app)["enabled"] is True


def test_幂等_连调两次结果一致(engine):
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    first = rr.sync_rerank_runtime(app)
    obj = app.state.rerank_search
    second = rr.sync_rerank_runtime(app)
    assert first == second == {"enabled": True, "reason": None}
    assert app.state.rerank_search is not obj, \
        "每次同步都重建 —— RerankSearcher 无状态，重建零成本（注释里写了理由）"


def test_未配置时默认引擎名进原因(engine, monkeypatch):
    """`rerank_model` 缺失时回退 `DEFAULT_ENGINE`，原因里报的是那个名字。"""
    monkeypatch.setattr(EngineRegistry, "_stores", {"rerank": {}})
    app = _app({"rerank_enabled": True})
    r = rr.sync_rerank_runtime(app)
    assert r["enabled"] is False
    assert rr.DEFAULT_ENGINE in r["reason"], r


def test_stop_只摘对象不改配置(engine):
    app = _app({"rerank_enabled": True, "rerank_model": engine.name})
    rr.sync_rerank_runtime(app)
    rr.stop_rerank_runtime(app)
    assert app.state.rerank_search is None
    # 配置是用户的决定，停机 / 测试收尾不许碰它
    assert app.state.config["search"]["rerank_enabled"] is True


# ═══════════════════════════════════════════════════════════════
# 候选窗口的两套夹紧判据（**刻意不同**，别合并）
# ═══════════════════════════════════════════════════════════════

def test_clamp_candidates_构建期回退默认值():
    """`RerankSearcher.__init__` 的夹紧：**脏值回退默认**。

    ⚠️ 落到 1 会让重排退化成「只能排一条」—— 功能在、效果没了，而用户只会觉得
    「开了重排毫无变化」。
    """
    assert rs._clamp_candidates(30) == 30
    assert rs._clamp_candidates(None) == rs.DEFAULT_CANDIDATES
    assert rs._clamp_candidates("手改出来的值") == rs.DEFAULT_CANDIDATES
    assert rs._clamp_candidates(0) == rs.DEFAULT_CANDIDATES
    assert rs._clamp_candidates(-5) == rs.DEFAULT_CANDIDATES


def test_默认配置与常量同源():
    """`DEFAULT_CONFIG` 里的字面量必须等于代码里的常量（改一处漏一处的守卫）。"""
    cfg = DEFAULT_CONFIG["search"]
    assert cfg["rerank_candidates"] == rs.DEFAULT_CANDIDATES
    assert cfg["rerank_model"] == rr.DEFAULT_ENGINE
    assert cfg["rerank_enabled"] is False, "默认关 = 零回归"


# ═══════════════════════════════════════════════════════════════
# 端点与状态段（TestClient，不进 startup）
# ═══════════════════════════════════════════════════════════════

from fastapi.testclient import TestClient          # noqa: E402

from app.core.search import SearchEngine           # noqa: E402
from app.routers import search as search_router    # noqa: E402
from main import app as _app_main                  # noqa: E402

#: 前端 `apiFetch` 每个请求都带它（`frontend/src/api/core.ts::CLIENT_HEADERS`）
_CLIENT = {"X-LiMo-Client": "1"}


@pytest.fixture()
def client(tmp_path):
    """临时 SearchEngine + 清空的运行时状态。

    ⚠️ 这六个 `app.state` 属性**必须显式赋值**：不赋的话会拿到先前某个测试留下的
    实例 —— 表现为顺序依赖的随机红（同 `test_search_api._setup`）。

    ⚠️ **向量那四个也要清**（`test_delete_index.py` 会把 `vector_store` 换成
    `_FakeVec`）：`/search/status` 里 `vector` 与 `rerank` 两段是**同一次请求**里
    取的，只清自己那半的话，本文件会因「先跑过哪个测试」而红 —— 而报错是
    `'_FakeVec' object has no attribute 'status'`，看起来像 rerank 的 bug。
    """
    _app_main.state.search_engine = SearchEngine(str(tmp_path))
    _app_main.state.rerank_search = None
    _app_main.state.rerank_runtime = None
    _app_main.state.vector_store = None
    _app_main.state.vector_runtime = None
    _app_main.state.vector_search = None
    _app_main.state.vector_worker = None
    _app_main.state.config = {"search": {}}
    _app_main.state.config_lock = threading.Lock()
    _CLEANUP = ("config", "config_lock", "rerank_search", "rerank_runtime",
                "vector_store", "vector_runtime", "vector_search", "vector_worker")
    try:
        yield TestClient(_app_main)
    finally:
        for name in _CLEANUP:
            if hasattr(_app_main.state, name):
                delattr(_app_main.state, name)


def test_status_带_rerank_段(client):
    """`status.rerank` 的五个字段**在未启用时也必须齐**（前端类型里是必填）。"""
    st = client.get("/api/search/status").json()
    rk = st["rerank"]
    for key in ("enabled", "ready", "reason", "model", "candidates", "models"):
        assert key in rk, f"status.rerank 缺 {key}：{rk}"
    assert rk["enabled"] is False and rk["ready"] is False
    assert rk["model"] == rr.DEFAULT_ENGINE
    assert rk["candidates"] == rs.DEFAULT_CANDIDATES


def test_status_的_model_读配置而不是运行时(client):
    """未启用时下拉也要显示用户选中的那项（判据同 `_configured_model`）。"""
    _app_main.state.config = {"search": {"rerank_model": "某个本地引擎",
                                         "rerank_candidates": 20}}
    rk = client.get("/api/search/status").json()["rerank"]
    assert rk["model"] == "某个本地引擎"
    assert rk["candidates"] == 20
    assert rk["enabled"] is False, "读配置不等于启用"


def test_candidates_磁盘脏值回退默认(client):
    """**读回宽**：磁盘上可能是手改的字符串 / 旧版本的 `null`。

    ⚠️ 回退 `DEFAULT_CANDIDATES` 而**不是** 1 —— 与端点入口刻意不同（见下一条）。
    """
    for dirty in ("abc", None, 0, -3):
        _app_main.state.config = {"search": {"rerank_candidates": dirty}}
        got = client.get("/api/search/status").json()["rerank"]["candidates"]
        assert got == rs.DEFAULT_CANDIDATES, f"{dirty!r} 应回退默认值，实际 {got}"


def test_candidates_入口夹到_1(client, monkeypatch):
    """**入口严的量**：用户刚提交 `0` / 负数 ⇒ 夹到 **1**（最接近他意思的合法值）。

    与上一条成对：错的是「量」不是「性质」，故不 400；但也不该悄悄变成 50 ——
    那等于把他的输入换成了另一个数。
    """
    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config", lambda cfg: writes.append(dict(cfg["search"])))

    r = client.post("/api/search/rerank", headers=_CLIENT,
                    json={"enabled": True, "candidates": 0})
    assert r.status_code == 200, r.text
    assert writes[-1]["rerank_candidates"] == 1, writes

    r = client.post("/api/search/rerank", headers=_CLIENT,
                    json={"enabled": True, "candidates": -9})
    assert writes[-1]["rerank_candidates"] == 1


def test_非法_engine_400_且零写盘(client, monkeypatch):
    """入口严的**性质**：引擎名非法 → 400 `RERANK_NO_ENGINE`，一个字节都不写。"""
    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config", lambda cfg: writes.append(dict(cfg["search"])))

    r = client.post("/api/search/rerank", headers=_CLIENT,
                    json={"enabled": True, "model": "根本不存在"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "RERANK_NO_ENGINE"
    assert writes == [], f"非法 model 不该写盘，实际 {writes}"


def test_缺来源守卫时拒绝(client, monkeypatch):
    """有副作用的 POST 必须过 `ensure_trusted_request`（跨站页面能发 POST）。

    ⚠️ 判据是「**带了非法 Origin**」，不是「没带 Origin」—— 后者刻意放行（curl /
    CLI / 浏览器直接打开），拿它做断言会得到 200 而误以为是守卫坏了。
    """
    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config", lambda cfg: writes.append(dict(cfg["search"])))

    r = client.post("/api/search/rerank", json={"enabled": True},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "UNTRUSTED_ORIGIN"
    assert writes == [], f"被守卫拒掉的请求不该写盘，实际 {writes}"


def test_开关写入后回显状态(client, monkeypatch):
    """正常路径：落盘 + 同步运行时 + 返回动作后的完整状态。

    ⚠️ 刻意**不传 `model`**：传了就先撞入口那道的引擎存在性校验（400），
    这里要测的是「引擎不在场 ⇒ 写盘成功但运行时带原因停用」这条路径。
    """
    monkeypatch.setattr(search_router, "save_config", lambda cfg: None)
    monkeypatch.setattr(EngineRegistry, "_stores", {"rerank": {}})

    r = client.post("/api/search/rerank", headers=_CLIENT,
                    json={"enabled": True, "candidates": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    # 引擎不在场 ⇒ enabled=False 且 reason 有话（不是静默当「没开」）
    assert body["enabled"] is False and body["reason"], body
    assert body["status"]["candidates"] == 30
    assert body["status"]["models"] == []
    assert body["status"]["enabled"] is False
