"""性能打点 —— 每条纪律至少一条守卫。

被钉住的三件事（都是「静默失效」的形状）：

1. **记录不判断。** 改造前 `_log_request` 的判据是 `duration_ms >= 2000`，
   于是 1900ms 的退化和 12ms 的正常请求在数据上长得一样。守卫：
   `test_1900ms_也要进统计`（把阈值写回来 → 红）。
2. **写失败不影响业务。** `setup` 遇到不可写的目录只 warning，`record` 照常
   做进程内统计。
3. **独立文件。** `limo.perf` 的 `propagate` 必须是 False —— 否则性能行会同时
   进 app.log（挤占 5×2MB 的业务日志）和 `GET /api/logs` 的 1000 条环形缓冲
   （几秒就能把日志面板刷空，而界面上看不出任何异常）。

外加三条容易漏的：
- 未 `setup` 时 `record` 不能炸（启动早期就有打点：`startup.store` 排在 setup 之后，
  但 `Store.__init__` 内部也可能在别的入口被调用）
- `setup` 幂等（测试与真实启动都会调；重复挂 handler 会让每行日志写两遍）
- **HTTP 打点用路由模板**而不是原始 path —— 否则几千篇文档就是几千个统计组，
  `snapshot()` 返回一张没人看得懂的表，且内存无上界。

⚠️ 有一条刻意没测：「落盘写入时抛异常」的兜底分支。`logging.Handler.handle`
会先吞掉 handler 的 `emit` 异常（走 `handleError`），`_write` 里的 try/except
抓不到它。那段代码留着是防「logger 被整层替换」，不是防磁盘满（磁盘满在
`setup` 阶段就 OSError 了，有测试）。
"""
from types import SimpleNamespace

import pytest

from app.core import perf


@pytest.fixture(autouse=True)
def _clean_perf():
    perf.reset()
    yield
    perf.reset()


# ── 纪律 1：记录不判断 ──

def test_1900ms_也要进统计(tmp_path):
    """改造前的判据是 `>= 2000` 才算慢。打点不做这个判断：两个样本都在。"""
    perf.setup(tmp_path)
    perf.record("GET /api/x", 12.0, status=200)
    perf.record("GET /api/x", 1900.0, status=200)
    g = {x["name"]: x for x in perf.snapshot()["groups"]}
    assert g["GET /api/x"]["count"] == 2       # 一条不少
    assert g["GET /api/x"]["max"] == 1900.0


def test_快慢样本算出的分位数如实反映分布(tmp_path):
    perf.setup(tmp_path)
    for ms in (10, 10, 10, 10, 10, 10, 10, 10, 10, 5000):
        perf.record("g", float(ms))
    g = perf.snapshot()["groups"][0]
    assert g["count"] == 10
    assert g["p50"] == 10.0
    assert g["p95"] == 5000.0      # nearest-rank：10 个样本的 P95 = 第 10 个
    assert g["max"] == 5000.0


def test_snapshot_按p95降序(tmp_path):
    perf.setup(tmp_path)
    perf.record("fast", 5.0)
    perf.record("slow", 900.0)
    perf.record("mid", 300.0)
    assert [g["name"] for g in perf.snapshot()["groups"]] == ["slow", "mid", "fast"]


def test_空快照不炸():
    snap = perf.snapshot()
    assert snap["groups"] == []
    assert snap["totalSamples"] == 0


def test_分组数有上限不会无限增长(tmp_path, monkeypatch):
    """动态路径没归一化好时（防御性）——组数封顶，只丢新的统计，不抛、不涨内存。"""
    perf.setup(tmp_path)
    monkeypatch.setattr(perf, "_MAX_GROUPS", 3)
    for i in range(10):
        perf.record(f"g{i}", 1.0)
    assert len(perf.snapshot()["groups"]) == 3


# ── 纪律 2：写失败不影响业务 ──

def test_setup_遇到不可写目录不抛异常_且内存统计照常(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")      # 拿一个文件当目录 → mkdir 抛 OSError

    perf.setup(blocker)                             # 必须不抛
    perf.record("g", 42.0)                          # 必须不抛
    assert perf.snapshot()["groups"][0]["count"] == 1
    assert perf.snapshot()["logFile"] is None       # 落盘不可用 ⇒ 如实为 None


def test_未setup时record也能用():
    """启动早期/纯单测环境：内存统计照常，落盘静默跳过。"""
    perf.record("g", 7.0)
    assert perf.snapshot()["groups"][0]["max"] == 7.0


def test_timer_异常路径也记录(tmp_path):
    perf.setup(tmp_path)
    with pytest.raises(RuntimeError):
        with perf.timer("boom", phase="x"):
            raise RuntimeError("坏了")
    g = perf.snapshot()["groups"][0]
    assert g["name"] == "boom" and g["count"] == 1


# ── 纪律 3：独立文件 ──

def test_setup后不向root传播(tmp_path):
    """propagate=True 会让性能行同时进 app.log 与内存日志缓冲 —— 两条都是回归。"""
    import logging
    root_before = len(logging.getLogger().handlers)

    perf.setup(tmp_path)
    lg = logging.getLogger("limo.perf")

    assert lg.propagate is False
    assert len(logging.getLogger().handlers) == root_before   # root 一个 handler 都没多


def test_setup幂等_不重复挂handler(tmp_path):
    """重复挂 handler 会让每行日志写两遍（文件里出现成对重复行）。"""
    import logging
    from logging.handlers import TimedRotatingFileHandler

    def _file_handlers() -> int:
        # ⚠️ 只数文件 handler：pytest 的 caplog 会往同一个 logger 上挂
        # LogCaptureHandler，数总数会随测试环境变化。
        return sum(1 for h in logging.getLogger("limo.perf").handlers
                   if isinstance(h, TimedRotatingFileHandler))

    perf.setup(tmp_path)
    perf.setup(tmp_path)
    assert _file_handlers() == 1


def test_落盘确实是JSONLines一行一条(tmp_path):
    import json
    perf.setup(tmp_path)
    perf.record("GET /api/x", 12.5, status=200)
    perf.record("store.save_index", 3.0, docs=145)
    logging_ = __import__("logging").getLogger("limo.perf")
    for h in logging_.handlers:
        h.flush()

    lines = [ln for ln in (tmp_path / "perf.log").read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 2
    rows = [json.loads(ln) for ln in lines]
    assert rows[0]["name"] == "GET /api/x" and rows[0]["ms"] == 12.5 and rows[0]["status"] == 200
    assert rows[1]["docs"] == 145
    assert all("ts" in r for r in rows)


def test_字段值为None时不落盘(tmp_path):
    """`_log_request` 传 `sse=True if is_sse else None` —— 用一个补位省掉分支，
    但 None 不该在留档里变成 `"sse": null` 这种噪音。"""
    import json
    perf.setup(tmp_path)
    perf.record("g", 1.0, status=200, sse=None)
    for h in __import__("logging").getLogger("limo.perf").handlers:
        h.flush()
    row = json.loads((tmp_path / "perf.log").read_text(encoding="utf-8").splitlines()[0])
    assert "sse" not in row


# ── HTTP 那一层：路由模板归一化 ──

def _fake_request(path: str, route_path: str | None):
    route = SimpleNamespace(path=route_path) if route_path else None
    return SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path=path),
        headers={},
        scope={"route": route} if route else {},
    )


def test_http打点用路由模板而不是原始路径(tmp_path):
    """否则一篇文档一个统计组（几百上千组），且内存无上界。"""
    import main

    perf.setup(tmp_path)
    # fixture 用**真实形态**：FastAPI 0.139 的 route.path 不含 include_router 的 prefix
    # （2026-10-27 实测 `GET /api/folders` → scope["route"].path == "/folders"）
    req = _fake_request("/api/transcripts/tr_abc123", "/transcripts/{tx_id}")
    main._log_request(req, 200, 42.0)

    names = [g["name"] for g in perf.snapshot()["groups"]]
    assert names == ["GET /transcripts/{tx_id}"]
    assert not any("tr_abc123" in n for n in names)


def test_路由模板若带api前缀也要剥掉(tmp_path):
    """口径免疫依赖升级：将来 FastAPI 若变回「展开后 route.path 含前缀」，
    统计键必须仍是 `/transcripts/{tx_id}` —— 否则 `/folders` 与 `/api/folders`
    会各算一组，历史分位数被一次升级劈成两半。"""
    import main

    perf.setup(tmp_path)
    main._log_request(_fake_request("/api/folders", "/api/folders"), 200, 2.0)
    assert perf.snapshot()["groups"][0]["name"] == "GET /folders"


def test_没有匹配到路由时退回原始路径(tmp_path):
    """404（未匹配任何路由）也要留一条，否则「打错的端点」在数据上不存在。
    风格与已匹配项一致（都剥掉 /api），否则同一张表里两种写法并存。"""
    import main

    perf.setup(tmp_path)
    main._log_request(_fake_request("/api/nope", None), 404, 3.0)
    assert perf.snapshot()["groups"][0]["name"] == "GET /nope"


def test_sse打点带ttfb后缀(tmp_path):
    """BaseHTTPMiddleware 量到的是建连时间，不是流总时长（实测 2–14ms vs 几十秒）——
    混进普通请求的分位数会把它整体拉低。"""
    import main

    perf.setup(tmp_path)
    req = _fake_request("/api/generate/tr_x/stream", "/generate/{tx_id}/stream")
    main._log_request(req, 200, 4.1,
                      response_headers={"content-type": "text/event-stream; charset=utf-8"})

    g = perf.snapshot()["groups"][0]
    assert g["name"] == "GET /generate/{tx_id}/stream [ttfb]"


def test_噪音路径不产生打点(tmp_path):
    """/health 探活 10 秒一次，记进去会把整张表淹掉（现状就是跳过的）。"""
    import main

    perf.setup(tmp_path)
    for p in ("/health", "/api/health", "/static/x.js", "/api/assets/a.png"):
        main._log_request(_fake_request(p, None), 200, 1.0)
    assert perf.snapshot()["groups"] == []
