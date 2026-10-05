"""`save_transcript_full` 的返回值必须被消费（P1-06）。

它返回 `bool`，`False` 的含义是「文档目录不存在，**什么都没写**」。三处调用方
（`ingest.py` PATCH / `ingest.py` retranscribe / `generate.py` `_auto_title`）原先一律丢弃
返回值，于是：

- PATCH：响应体回显内存里的新标题 → 用户看到「改成功了」，磁盘上没变，刷新即打脸；
- retranscribe：**已经花掉时间与额度**的转写结果没落盘，接口却回 ok；
- `_auto_title`：日志打进「自动标题: A → B」，而磁盘上还是 A（日志撒谎）。

⚠️ 三处的共同点是「**写盘失败被降级成一句日志/一个回显**」，而用户拿到的信号全是成功。
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store
from main import app

_ROUTERS = Path(__file__).resolve().parent.parent / "app" / "routers"


def _setup() -> tuple[TestClient, Store, str]:
    d = tempfile.mkdtemp(prefix="limo_savefull_")
    store = Store(d)
    app.state.store = store
    from app.core.search import SearchEngine
    app.state.search_engine = SearchEngine(d)
    store.save_transcript(RawTranscript(
        id="tr_save", title="原标题", source_type=SourceType.WEBPAGE,
        engine="trafilatura", raw_text="body", content_hash="h_save",
        metadata=RawMetadata(),
    ))
    return TestClient(app), store, d


def test_patch_reports_failure_instead_of_echoing_new_value(monkeypatch):
    """写盘失败 → 404，而不是 200 + 回显新值（回显不能骗人）。"""
    c, store, d = _setup()
    try:
        # 正常路径先跑一遍，确认接口本身是好的（否则下面的 404 说明不了任何事）
        ok = c.patch("/api/transcripts/tr_save", json={"title": "正常改标题"})
        assert ok.status_code == 200, ok.text
        assert store.get_transcript("tr_save")["title"] == "正常改标题"

        # 让写盘失败（目录不存在时的真实返回）：调用方必须把它翻成失败
        monkeypatch.setattr(store, "save_transcript_full", lambda tx: False)
        r = c.patch("/api/transcripts/tr_save", json={"title": "不该生效"})

        assert r.status_code == 404, (
            f"写盘失败却回了 {r.status_code}：{r.text}（旧行为是 200 + 回显「不该生效」）")
        assert r.json()["code"] == "INGEST_DOC_NOT_FOUND"
        # 内存里的索引也不能被改（get_transcript 会重读 index）
        monkeypatch.undo()
        assert store.get_transcript("tr_save")["title"] == "正常改标题", \
            "写盘失败的这次修改不得留在索引里"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_no_bare_calls_to_save_transcript_full():
    """源码守卫：router 里 `save_transcript_full(...)` 不得是**裸调用语句**。

    裸调用 = 返回值无处可去 = 与「没检查」等价。这条守卫比逐个调用点断言可靠：
    新增第四个调用点时会自动被它拦住。
    """
    offenders = []
    for py in sorted(_ROUTERS.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "attr", "") == "save_transcript_full"):
                offenders.append(f"{py.name}:{node.lineno}")
    assert not offenders, f"这些调用点丢弃了返回值（写盘失败会被静默吞掉）: {offenders}"


def test_guard_has_teeth():
    """自证：守卫真能抓到裸调用（否则「零命中」与「扫描器没工作」无法区分，模式 #38）。"""
    sample = ast.parse("def f(store, tx):\n    store.save_transcript_full(tx)\n")
    hit = [n for n in ast.walk(sample)
           if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
           and getattr(n.value.func, "attr", "") == "save_transcript_full"]
    assert len(hit) == 1, "守卫模式串写错了，抓不到裸调用"

    checked = ast.parse("def f(store, tx):\n    if not store.save_transcript_full(tx):\n        raise ValueError\n")
    hit2 = [n for n in ast.walk(checked)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "attr", "") == "save_transcript_full"]
    assert hit2 == [], "被检查的调用不该被误判为裸调用"
