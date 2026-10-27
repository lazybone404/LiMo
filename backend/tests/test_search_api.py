"""检索端点的 HTTP 层测试（`/api/search/status` / `/api/search/reindex`）。

**为什么需要这个文件**：这两个端点此前**没有任何 HTTP 层测试**。
`test_search.py` 测的是 `SearchEngine` 类本身 —— 「引擎能重建」与「端点调通了重建」
是两件事（模式 #51：能力存在 ≠ 该路径在用它）。而 `POST /api/search/reindex`
是设置页那个「重建索引」按钮的全部实现。

第四轮审查把它的同步 `engine.rebuild(...)` 改成 `await asyncio.to_thread(...)`
（P2-03）：改动只有一行，但**传参对不对、端点还通不通没有任何判据** ——
`rebuild` 的 `vacuum` 是 keyword-only，一旦签名或参数顺序漂移，用户看到的是
「点了重建没反应」，而所有单测照样全绿。

⚠️ 本文件**不进 `TestClient` 上下文**（不跑 startup event），手动注入临时
Store + SearchEngine —— 与 `test_delete_index.py` 同一手法，理由见该文件注释。

对照实验（均实测）：
  A. 把 `await asyncio.to_thread(engine.rebuild, transcripts, _get_content, vacuum=True)`
     写成位置形式 `engine.rebuild(transcripts, _get_content, True)`（`vacuum` 是
     keyword-only）→ `TypeError` → 500 → `test_reindex_via_http_rebuilds_index` 红
  B. 删掉 `search_reindex` 里的 `ensure_trusted_request(request)`
     → `test_reindex_rejects_untrusted_origin` 红
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

from app.core.search import SearchEngine
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.core.vector_store import VectorStore
from app.storage.store import Store
from main import app

_NEEDLE = "needle"

#: 前端 `apiFetch` 每个请求都带它（`frontend/src/api/core.ts::CLIENT_HEADERS`）
_CLIENT = {"X-LiMo-Client": "1"}


def _setup() -> tuple[TestClient, str]:
    """临时 Store + SearchEngine + 两篇带同一关键词的文档。

    ⚠️ 三个 `app.state` 属性**必须显式赋值**：不赋的话会拿到先前某个跑了 startup
    的测试留下的实例（指向已删的临时目录）—— 表现为顺序依赖的随机红。
    """
    d = tempfile.mkdtemp(prefix="limo_search_api_")
    store = Store(d)
    app.state.store = store
    app.state.search_engine = SearchEngine(d)      # 建表，但不灌内容
    app.state.vector_store = None
    app.state.vector_runtime = None
    app.state.vector_search = None
    app.state.vector_worker = None

    for i in (1, 2):
        store.save_transcript(RawTranscript(
            id=f"tr_{i}", title=f"标题{i}", source_type=SourceType.WEBPAGE,
            engine="trafilatura", raw_text=f"第 {i} 篇的正文里都有 {_NEEDLE} 这个词。",
            content_hash=f"h{i}", metadata=RawMetadata(),
        ))
    return TestClient(app), d


def test_reindex_via_http_rebuilds_index():
    """点「重建索引」→ 索引真的被灌满（篇数 + 可搜性**两条都断**）。

    ⚠️ 只断 `docCount` 不够：`docCount` 来自 `SELECT count(*)`，一个「表建对了
    但灌空」的实现也会让篇数对不上 —— 所以再搜一次。反过来只断搜索也不够：
    搜索能命中而篇数报 0 说明状态端点读错了地方。
    """
    c, d = _setup()
    try:
        # 前提断言：重建前是空的（否则「重建成功」可能只是读了旧内容）
        before = c.get("/api/search/status").json()
        assert before["docCount"] == 0, f"前提不成立：{before}"

        r = c.post("/api/search/reindex", headers=_CLIENT)
        assert r.status_code == 200, r.text
        st = r.json()
        assert st["available"] is True and st["docCount"] == 2, st
        assert st["lastError"] is None, st
        assert st["rebuiltAt"], "重建成功要记下时间（设置页显示『上次重建于』）"
        # 状态里必须带向量段（`_vector_status` 走的是 `app.state`，未初始化时给默认值）
        assert st["vector"]["enabled"] is False, st["vector"]

        hits = c.get("/api/search", params={"q": _NEEDLE}).json()["hits"]
        assert {h["id"] for h in hits} == {"tr_1", "tr_2"}, \
            f"重建后必须能搜到两篇，实际 {hits}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reindex_rejects_untrusted_origin():
    """陌生 Origin → 403（有副作用的重建不能由任意网页触发）。

    ⚠️ 与上一条**成对**：只测成功路径的话，把 `ensure_trusted_request` 删掉
    也不会有任何用例红。
    """
    c, d = _setup()
    try:
        r = c.post("/api/search/reindex", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, r.text
        assert app.state.search_engine.status()["docCount"] == 0, \
            "被拒的请求不该先把索引重建了"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_status_reports_vector_block_when_uninitialized():
    """向量库未初始化 → `vector` 段给出明确的 `enabled: false` + 原因。

    ⚠️ 这一段**不能省成 `null`**：前端 `SearchIndexCard` 据此渲染开关与说明，
    `null` 会让「后端没接向量」与「向量关着」看起来一样。
    """
    c, d = _setup()
    try:
        st = c.get("/api/search/status").json()
        assert st["available"] is True
        vec = st["vector"]
        assert vec["enabled"] is False and vec["reason"], vec
        assert vec["docs"] == {} and vec["chunks"] == 0 and vec["sizeBytes"] == 0, vec
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 向量化引擎选择（2026-10-26 阶段 4：云端 / 本地二选一） ──

class _CloudEmb:
    """**没有** `precheck` 的引擎 —— 云端形状（Key 的问题在第一次编码时才该显式报）。"""

    is_local = False

    def __init__(self, name):
        self.name = name
        self.display_name = f"{name}（云端）"
        self.model_id = f"{name}-model"
        self.dimension = 1024


class _LocalEmb(_CloudEmb):
    """**有** `precheck` 的引擎 —— 本地形状（依赖 / 模型文件可能不在场）。"""

    # ⚠️ 类属性而非 `__init__` 里赋值：子类的实例属性会**遮蔽**父类同名的类属性，
    # 在上面那样写（父 `__init__` 设 `self.is_local = False`）时子类无论怎么声明都是 False。
    is_local = True

    def __init__(self, name, reason=None):
        super().__init__(name)
        self.display_name = f"{name}（本地）"
        self.dimension = 512
        self._reason = reason

    def precheck(self):
        return self._reason


@pytest.fixture()
def fake_embeddings(monkeypatch):
    """把注册表换成两个可控替身（`EngineRegistry` 是进程内单例，别的测试文件可能
    已经注册过真引擎 —— 不隔离的话这里断言的「有哪些」会随测试顺序变化）。"""
    from app.core.registry import EngineRegistry

    engines = {"cloud-x": _CloudEmb("cloud-x"),
               "local-x": _LocalEmb("local-x", reason="缺 onnxruntime")}
    monkeypatch.setattr(EngineRegistry, "_stores", {"embedding": dict(engines)})
    return engines


def test_status_lists_embedding_engines(fake_embeddings):
    """`status.vector` 给出 `model`（当前选中）+ `models`（下拉数据源）。

    ⚠️ 两个键**在 `vector_store is None` 的早退分支上也要有**：前端类型里它们是
    必填，缺键会让「后端没接向量库」与「字段忘了给」在界面上长得一模一样。
    """
    c, d = _setup()                     # 注意：`vector_store = None`，走早退分支
    try:
        vec = c.get("/api/search/status").json()["vector"]

        assert vec["model"] == "dashscope-embedding", "配置没设时给默认引擎"
        models = {m["name"]: m for m in vec["models"]}
        assert set(models) == {"cloud-x", "local-x"}

        assert models["local-x"]["local"] is True
        assert models["local-x"]["ready"] is False
        assert models["local-x"]["reason"] == "缺 onnxruntime"
        assert models["local-x"]["dimension"] == 512
        assert models["local-x"]["modelId"] == "local-x-model"
        assert models["local-x"]["label"] == "local-x（本地）"

        # ⚠️ 没有 `precheck` 的引擎 = 视为就绪（不是「未知」）—— 云端确实没有
        # 「依赖装没装」这回事，报不可用会让用户去找一个不存在的开关。
        assert models["cloud-x"]["ready"] is True
        assert models["cloud-x"]["reason"] is None
        assert models["cloud-x"]["local"] is False

        # 顺序 = 注册顺序（dict 插入序）：云端在前 ⇒ 默认项落在第一位，零迁移
        assert [m["name"] for m in vec["models"]] == ["cloud-x", "local-x"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_status_model_reflects_config(fake_embeddings):
    """`model` 读的是**配置**而不是 runtime —— 用户切到本地后即使还没启用，
    下拉也必须显示他选中的那项。"""
    c, d = _setup()
    app.state.config = {"search": {"vector_enabled": False, "vector_model": "local-x"}}
    try:
        vec = c.get("/api/search/status").json()["vector"]
        assert vec["model"] == "local-x"
        assert vec["enabled"] is False           # 选中 ≠ 启用，两件事
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config


def test_status_reports_release_policy():
    """`vector.release` 是**配置值**：默认 `idle`、合法值原样、脏值回退 `idle`。

    ⚠️ 脏值回退**不抛**（与请求入口的 400 刻意不同）：这里是「磁盘上可能过期的值」，
    为了一个读不懂的键让设置页整块报错，用户无从下手；而 `idle` 的语义是安全的
    （闲置后不占内存）。
    """
    c, d = _setup()
    app.state.config = {}
    try:
        assert c.get("/api/search/status").json()["vector"]["release"] == "idle"

        app.state.config = {"search": {"vector_release": "never"}}
        assert c.get("/api/search/status").json()["vector"]["release"] == "never"

        app.state.config = {"search": {"vector_release": "手改出来的值"}}
        assert c.get("/api/search/status").json()["vector"]["release"] == "idle"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config


def test_vector_toggle_sets_release(monkeypatch):
    """释放策略随开关一起落盘；非法值 400 `VECTOR_BAD_RELEASE` 且**零写盘**。

    静默退化的失效形态：用户以为关掉了释放、界面上「已保存」，而那几十 MB
    一直在内存里 —— 产物与日志上都看不出任何异常。
    """
    import threading

    import app.routers.search as search_router

    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config",
                        lambda cfg: writes.append(dict(cfg["search"])))

    c, d = _setup()
    app.state.config = {"search": {"vector_enabled": False}}
    app.state.config_lock = threading.Lock()
    try:
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": False, "release": "immediate"})
        assert r.status_code == 200, r.text
        assert writes == [{"vector_enabled": False, "vector_release": "immediate"}]
        assert r.json()["status"]["release"] == "immediate"

        writes.clear()
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": False, "release": "sometimes"})
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "VECTOR_BAD_RELEASE"
        assert writes == [], "非法策略不该产生任何写盘"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config
        del app.state.config_lock


def test_默认配置的释放策略与常量同源():
    """`DEFAULT_CONFIG` 里的字面量与 `vector_worker.DEFAULT_RELEASE` 必须相等。

    两处各写一份时漂移是零报错的：默认配置写着 `never` 而常量是 `idle` ⇒ 用户从没
    碰过这个设置，worker 按 `idle` 跑而设置页显示「常驻内存」。
    """
    from app.core.config import DEFAULT_CONFIG
    from app.services.vector_worker import DEFAULT_RELEASE

    assert DEFAULT_CONFIG["search"]["vector_release"] == DEFAULT_RELEASE


def test_status_reports_trigger_and_threshold():
    """`vector.trigger` / `vector.threshold` 读的是**配置**：未启用时也要显示用户选的那档。

    ⚠️ 与 `release` 同一组判据：`vector_store = None`（早退分支）时字段必须仍然在 ——
    前端类型里它们必填，缺键会让「后端没接向量库」与「字段忘了给」在界面上长得一模一样。

    ⚠️ 脏值一律回退 / 夹紧（**读回宽**），理由同 `test_status_reports_release_policy`：
    这是「磁盘上可能过期的值」，为一个读不懂的键让设置页整块报错，用户无从下手。
    """
    from app.services.vector_worker import DEFAULT_THRESHOLD, DEFAULT_TRIGGER

    c, d = _setup()
    app.state.config = {}
    try:
        v = c.get("/api/search/status").json()["vector"]
        assert v["trigger"] == DEFAULT_TRIGGER
        assert v["threshold"] == DEFAULT_THRESHOLD

        app.state.config = {"search": {"vector_trigger": "manual", "vector_threshold": 20}}
        v = c.get("/api/search/status").json()["vector"]
        assert v["trigger"] == "manual"
        assert v["threshold"] == 20

        # 非法档 → 回退默认；`0`（与负数同族）→ 夹紧到 1
        app.state.config = {"search": {"vector_trigger": "手改出来的值", "vector_threshold": 0}}
        v = c.get("/api/search/status").json()["vector"]
        assert v["trigger"] == DEFAULT_TRIGGER
        assert v["threshold"] == 1, "0 会让闸门恒真（等于悄悄关掉攒批），必须夹紧"

        app.state.config = {"search": {"vector_threshold": "abc"}}
        assert c.get("/api/search/status").json()["vector"]["threshold"] == DEFAULT_THRESHOLD
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config


def test_vector_toggle_sets_trigger_and_clamps_threshold(monkeypatch):
    """触发方式随开关落盘：非法值 400 `VECTOR_BAD_TRIGGER` 且**零写盘**；阈值静默夹紧。

    ⚠️ 两条判据**刻意不同**（与 `release` / `threshold` 的分野同源）：
    「错的是性质」（切到一个不存在的触发方式）→ 显式报错，否则用户以为改成了手动档，
    而 worker 还在自动跑、继续花钱；「错的是量」（阈值 0）→ 夹紧到 1，因为 `0` 会让
    闸门恒真，等于把攒批悄悄关掉 —— 报错反而让用户无从下手（他要的就是「多建点」）。
    """
    import threading

    import app.routers.search as search_router

    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config",
                        lambda cfg: writes.append(dict(cfg["search"])))

    c, d = _setup()
    app.state.config = {"search": {"vector_enabled": False}}
    app.state.config_lock = threading.Lock()
    try:
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": False, "trigger": "manual", "threshold": 20})
        assert r.status_code == 200, r.text
        assert writes == [{"vector_enabled": False, "vector_trigger": "manual",
                           "vector_threshold": 20}]
        v = r.json()["status"]
        assert v["trigger"] == "manual" and v["threshold"] == 20

        writes.clear()
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": False, "trigger": "每周一次"})
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "VECTOR_BAD_TRIGGER"
        assert writes == [], "非法触发方式不该产生任何写盘"

        writes.clear()
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": False, "threshold": 0})
        assert r.status_code == 200, r.text
        # ⚠️ 只带 `threshold` 时**不该抹掉**上一轮写的 `vector_trigger`（合并式写入，
        # 同 `saveChatPrefs` 的教训：整对象覆盖会让「改 A 把 B 重置」零报错）。
        assert writes == [{"vector_enabled": False, "vector_trigger": "manual",
                           "vector_threshold": 1}]
        assert r.json()["status"]["threshold"] == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config
        del app.state.config_lock


class _StubWorker:
    """只回答「被叫过几次」—— `/search/vector/sync` 的全部行为就是这一次调用。"""

    def __init__(self) -> None:
        self.calls = 0

    def request_sync(self) -> None:
        self.calls += 1


def test_vector_sync_rejects_when_disabled():
    """worker 没在跑 → 400 `VECTOR_DISABLED`（**不现场造一个**）。

    ⚠️ 临时造一个会连带引擎、口径、库一起要求就绪 —— 而「语义检索没启用」可能正是
    库不可用或口径不符，那时起 worker 只会在下一轮静默失败，用户看到的是
    「点了一下，什么也没发生」。
    """
    c, d = _setup()
    try:
        r = c.post("/api/search/vector/sync", headers=_CLIENT)
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "VECTOR_DISABLED"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_vector_sync_wakes_worker():
    """worker 在跑 → 叫它一次并回 `requested`。

    ⚠️ 这里断的是「**端点把话递过去了**」而不是「索引真的建完了」—— 后者由
    `test_vector_worker.py::test_request_sync_叫醒循环并跳过闸门` 端到端钉住
    （那条有真 worker + 真循环）。端点这层只负一次调用，不要在这里复刻调度判据。
    """
    c, d = _setup()
    stub = _StubWorker()
    app.state.vector_worker = stub
    try:
        r = c.post("/api/search/vector/sync", headers=_CLIENT)
        assert r.status_code == 200, r.text
        assert r.json()["requested"] is True
        assert stub.calls == 1
    finally:
        app.state.vector_worker = None
        shutil.rmtree(d, ignore_errors=True)


def test_vector_retry_failed_requeues_and_wakes():
    """失败项重排 → 回 pending、**并叫醒 worker**（不能只重排不跑）。

    ⚠️ 第二个断言不是「顺手加的」：`manual` 档下重排完它们只是 `pending`，没有
    `request_sync` 就永远等不到下一次心跳 —— 按钮点了没反应，而状态里 pending
    数是涨的（看起来像正在建）。反向：`request_sync` 而无重排 = 跑一轮空转。
    两者缺一，用户看到的都是「点了，什么也没发生」。
    """
    c, d = _setup()
    stub = _StubWorker()
    app.state.vector_worker = stub
    # `_setup()` 把 vector_store 显式置 None（防串到上个测试的临时目录），
    # 这里要一个真库来行使重排 —— 临时目录同一个，用完一起删。
    vec = VectorStore(d)
    app.state.vector_store = vec
    try:
        vec.mark_failed("tr_a", "boom")
        vec.mark_failed("tr_b", "boom")
        r = c.post("/api/search/vector/retry-failed", headers=_CLIENT)
        assert r.status_code == 200, r.text
        assert r.json()["requeued"] == 2
        assert stub.calls == 1
        assert r.json()["status"]["docs"]["failed"] == 0
        assert r.json()["status"]["docs"]["pending"] == 2
    finally:
        app.state.vector_worker = None
        app.state.vector_store = None
        shutil.rmtree(d, ignore_errors=True)


def test_vector_retry_failed_noop_does_not_wake():
    """没有失败项时返回 0 且**不叫醒**（叫醒一轮空转没有意义）。"""
    c, d = _setup()
    stub = _StubWorker()
    app.state.vector_worker = stub
    app.state.vector_store = VectorStore(d)
    try:
        r = c.post("/api/search/vector/retry-failed", headers=_CLIENT)
        assert r.status_code == 200, r.text
        assert r.json()["requeued"] == 0
        assert stub.calls == 0
    finally:
        app.state.vector_worker = None
        app.state.vector_store = None
        shutil.rmtree(d, ignore_errors=True)


def test_vector_retry_failed_rejects_when_disabled():
    """worker 没在跑 → 400 `VECTOR_DISABLED`（同 `/sync`，不现场造一个）。"""
    c, d = _setup()
    try:
        r = c.post("/api/search/vector/retry-failed", headers=_CLIENT)
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "VECTOR_DISABLED"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_默认配置的触发方式与常量同源():
    """`DEFAULT_CONFIG` 里的触发方式 / 阈值必须与 `vector_worker` 的常量相等。

    与 `test_默认配置的释放策略与常量同源` 成对：两处各写一份时漂移**零报错** ——
    默认配置写着 100 而常量是 20，用户从没碰过这个设置，worker 按 20 攒批，
    而设置页显示「攒够 100 篇」。
    """
    from app.core.config import DEFAULT_CONFIG
    from app.services.vector_worker import DEFAULT_THRESHOLD, DEFAULT_TRIGGER

    assert DEFAULT_CONFIG["search"]["vector_trigger"] == DEFAULT_TRIGGER
    assert DEFAULT_CONFIG["search"]["vector_threshold"] == DEFAULT_THRESHOLD


def test_vector_toggle_switches_engine(fake_embeddings, monkeypatch):
    """切引擎 = 写配置 + 就地重新同步运行时（下一个用例验非法值）。"""
    import threading

    import app.routers.search as search_router

    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config",
                        lambda cfg: writes.append(dict(cfg["search"])))

    c, d = _setup()
    app.state.config = {"search": {"vector_enabled": False,
                                   "vector_model": "dashscope-embedding"}}
    app.state.config_lock = threading.Lock()
    try:
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": True, "model": "local-x"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"]["model"] == "local-x"
        # ⚠️ 写盘的是 `app.state.config` 这个内存对象（`save_config` 的隐藏前提）
        assert writes == [{"vector_enabled": True, "vector_model": "local-x"}]

        # `vector_store = None` ⇒ 运行时给不出「已启用」，但**原因必须在场**
        assert body["enabled"] is False and body["reason"] == "向量索引库未初始化"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config
        del app.state.config_lock


def test_vector_toggle_rejects_unknown_engine(fake_embeddings, monkeypatch):
    """非法引擎名 → 400 `VECTOR_NO_ENGINE`，**一个字节都不写**。

    ⚠️ 显式报错而非静默夹紧：错的是「性质」（切到一个不存在的引擎）不是「量」。
    静默退回默认值会让用户以为换成功了，而界面上的下拉从此对他撒谎。
    """
    import threading

    import app.routers.search as search_router

    writes: list[dict] = []
    monkeypatch.setattr(search_router, "save_config", lambda cfg: writes.append(cfg))

    c, d = _setup()
    app.state.config = {"search": {"vector_enabled": False}}
    app.state.config_lock = threading.Lock()
    try:
        r = c.post("/api/search/vector", headers=_CLIENT,
                   json={"enabled": True, "model": "nope"})
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "VECTOR_NO_ENGINE"
        assert writes == [], "非法引擎名不该产生任何写盘"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        del app.state.config
        del app.state.config_lock
