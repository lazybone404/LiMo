"""「导入与转录分开」—— 收纳档位与 `transcribeState` 的端到端判据（2026-11-07）。

## 这里守的是什么

用户要的是「先囤着、晚点再转录」。于是导入多了一条**只收纳**的路径：不调
`adapt()`（不下载、不跑 ASR），只探测标题/封面，落一段占位正文，文档进库，
字段 `transcribeState = "pending"`。

默认值按成本分两档（媒体只收纳 / 文档网页一键），档位表住在后端 —— 前端拿到
一个 URL 时根本不知道它是视频还是文章。

## 为什么必须走 HTTP 而不是只测 store

`transcribeState` 有**九处落点**（写路径 4 / 重建与读路径 4 / 返回前端 1）。
store 层测试只碰得到中间那几处，而其中两处的失效形态完全静默：

- 只改 `RawTranscript` 字段、不改 `save_transcript` 拼 `data` 的那一行
  ⇒ `_transcript_index_entry` 永远读默认 `"done"`，收纳完的文档一进库就显示「已转录」；
- 八处全对、漏了 `_persist_ingest_result` 的**手写返回 dict**
  ⇒ 库里是 pending、前端拿到 `undefined`，导入那一刻（用户唯一盯着看的那一刻）
  不显示「待转录」，切去文档库重新拉列表反而正常。

第 1 组一次断言「返回 dict 与列表条目**都**带 pending」，把这两条链一起钉住。

## 对照实验（每条都真跑过，见各组注释）

    A. 去掉 `save_transcript_full` 的第六个守护 → 第 6 组红（改标题把 pending 抹成 done）
    B. 只把 `_run_adapter` 接到非流式端点 → 第 7 组红（SSE 路径照常下载转录）
    C. 注释掉 `run_pipeline` 里的 `_index_if_persisted` → 第 8 组红（SSE 导入搜不到）
    D. 删掉 `retranscribe` 里的 `tx["transcribeState"] = "done"` → 第 5 组红
    E. `doc_meta.DOC_FIELDS` 去掉 `transcribeState` → 第 6 组（重建那一支）红

⚠️ 本文件**不碰真实数据目录**：不进 TestClient 上下文（不跑 startup），手动注入
临时 Store / config / search_engine。
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.search import SearchEngine
from app.core.types import AdapterOutput, ProgressEvent, RawMetadata
from app.storage.store import Store

import app.routers.ingest as I

_TMP: list[str] = []


class _Guard:
    """`retranscribe` 只用到 `guard.local_semaphore`。"""

    def __init__(self) -> None:
        self.local_semaphore = asyncio.Semaphore(1)


def _new_store() -> Store:
    d = tempfile.mkdtemp(prefix="limo_pending_")
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()

app = FastAPI()
app.include_router(I.router, prefix="/api")
app.state.store = _STORE
app.state.config = {"data_dir": str(_STORE.root)}
app.state.guard = _Guard()
app.state.search_engine = SearchEngine(str(_STORE.root))


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    return JSONResponse(status_code=200 if exc.recoverable else exc.status_code,
                        content=exc.to_dict())


client = TestClient(app)


# ── 假适配器 ──────────────────────────────────────────────

class _FakeAdapter:
    """只为驱动路由。`name` 决定档位（见 `I._TIER_MEDIA` / `_TIER_DOC`）。"""

    def __init__(self, name: str, *, raw: str = "真正文", probe: dict | None = None):
        self.name = name
        self._raw = raw
        self._probe = probe
        self.called = False

    async def adapt(self, inp, on_progress=None, engine_config=None):
        self.called = True
        if on_progress:
            on_progress(ProgressEvent(phase="parse", status="running",
                                      percent=10, message="解析中"))
        return AdapterOutput(raw_text=self._raw, metadata=RawMetadata(), engine="fake")

    def probe(self, inp):
        # 只有显式给了 `probe` 的适配器才有这个钩子 —— 没有的照 `getattr` 惯例
        # 应该走 `_build_pending_output` 的空分支。
        return self._probe


class _NoProbeAdapter(_FakeAdapter):
    """没有 `probe` 属性的适配器（document / webpage / text / image 的真实形态）。"""


def _patch_route(monkeypatch, adapter) -> None:
    monkeypatch.setattr(I.AdapterRegistry, "route", staticmethod(lambda inp: adapter))


def _post_url(url: str, **form):
    return client.post("/api/ingest", data={"url": url, **form})


def _events(body: str) -> list[dict]:
    return [json.loads(ln[6:]) for ln in body.splitlines()
            if ln.startswith("data: ") and ln[6:].strip() != "null"]


def _list_entry(tx_id: str) -> dict:
    for e in _STORE.list_transcripts():
        if e["id"] == tx_id:
            return e
    raise AssertionError(f"列表里找不到 {tx_id}；现有 {[e['id'] for e in _STORE.list_transcripts()]}")


# ═══════════════════════════════════════════════════════════
# 1. 媒体档默认只收纳：adapt() 不被调用，两条链都带 pending
# ═══════════════════════════════════════════════════════════

def test_媒体默认只收纳_返回与列表双pending(monkeypatch):
    ad = _FakeAdapter("video", probe={"title": "静默测试词", "duration": 321,
                                      "cover_url": ""})
    _patch_route(monkeypatch, ad)

    r = _post_url("https://www.bilibili.com/video/BV1xx411c7mD")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("status") == "done", f"收纳也是成功导入；实际 {data}"

    # 第 9 处落点：手写返回 dict 里必须有这个字段
    assert data.get("transcribeState") == "pending", (
        "导入那一刻就拿到 pending —— 漏了 `_persist_ingest_result` 的返回 dict 时，"
        f"这里会是 {data.get('transcribeState')!r}（库里却是 pending，切文档库才对）")
    # 第 1-3 处落点：索引条目
    assert _list_entry(data["id"])["transcribeState"] == "pending", \
        "列表条目必须是 pending —— 漏了 save_transcript 拼 data 那一行时这里恒为 done"

    assert ad.called is False, "只收纳就不许调 adapt()（那会下载 + 跑 ASR）"
    assert "limp" not in data and data["title"] == "静默测试词", \
        f"标题取 probe 的真标题；实际 {data['title']!r}"

    tx = _STORE.get_transcript(data["id"])
    assert "limo-pending:" in tx["rawText"], "占位正文必须带 tx_id（保证 content_hash 唯一）"
    assert tx["engine"] == "pending"


def test_媒体档显式关闭只收纳_走转录(monkeypatch):
    """用户在设置里打开「导入视频后自动转录」→ 前端会发 media_probe_only=false。"""
    ad = _FakeAdapter("video")
    _patch_route(monkeypatch, ad)

    r = _post_url("https://www.bilibili.com/video/BV1yy411c7mE",
                  media_probe_only="false")
    data = r.json()
    assert data.get("transcribeState") == "done", data
    assert ad.called is True
    assert "limo-pending" not in _STORE.get_transcript(data["id"])["rawText"]


# ═══════════════════════════════════════════════════════════
# 2. 文档 / 网页档默认一键 = 改造前行为
# ═══════════════════════════════════════════════════════════

def test_网页默认一键_与改造前一致(monkeypatch):
    ad = _NoProbeAdapter("webpage", raw="网页正文")
    _patch_route(monkeypatch, ad)

    data = _post_url("https://example.com/blog/hello-world").json()
    assert data.get("transcribeState") == "done", data
    assert ad.called is True
    assert "limo-pending" not in _STORE.get_transcript(data["id"])["rawText"]


def test_文档档关掉自动_只收纳(monkeypatch):
    ad = _NoProbeAdapter("document")
    _patch_route(monkeypatch, ad)

    data = _post_url("https://example.com/paper.pdf", doc_probe_only="true").json()
    assert data.get("transcribeState") == "pending", data
    assert ad.called is False


def test_arxiv_跟随文档档(monkeypatch):
    """arXiv 委托 DocumentAdapter，归文档档 —— 两档不归同一格就说明这张表写歪了。"""
    # 正文必须独一无二：假适配器都返回同一段字就等于「内容与已有文档完全相同」，
    # 会走 duplicate_content 分支（那条刻意不带 transcribeState），与本条要测的东西无关
    ad = _NoProbeAdapter("arxiv", raw="arXiv 论文正文")
    _patch_route(monkeypatch, ad)

    a = _post_url("https://arxiv.org/abs/2401.12345").json()
    assert a.get("transcribeState") == "done", a

    b = _post_url("https://arxiv.org/abs/2401.12346", doc_probe_only="true").json()
    assert b.get("transcribeState") == "pending", b


# ═══════════════════════════════════════════════════════════
# 3. 档位表外的类型（text / image）两个参数都传 true 也不受影响
# ═══════════════════════════════════════════════════════════

def test_文本与图片不在档位表内(monkeypatch):
    for name in ("text", "image"):
        ad = _NoProbeAdapter(name, raw=f"{name} 的正文")
        _patch_route(monkeypatch, ad)
        r = client.post("/api/ingest", data={"text": f"{name} 的正文内容",
                                             "media_probe_only": "true",
                                             "doc_probe_only": "true"})
        data = r.json()
        assert data.get("transcribeState") == "done", f"{name}: {data}"
        assert ad.called is True, f"{name} 不该走收纳分支（它没有「晚点再转录」的形态）"


# ═══════════════════════════════════════════════════════════
# 4. 收纳模式下同一份文件导入两次 → 两篇
#    （把已知副作用钉住，防将来有人「顺手」用内容哈希去重把它修掉）
# ═══════════════════════════════════════════════════════════

def test_收纳模式同文件两次得两篇(monkeypatch):
    ad = _NoProbeAdapter("video")
    _patch_route(monkeypatch, ad)
    payload = b"same bytes"

    r1 = client.post("/api/ingest/stream",
                     files={"file": ("a.mp4", payload, "video/mp4")})
    r2 = client.post("/api/ingest/stream",
                     files={"file": ("a.mp4", payload, "video/mp4")})
    ids = {e["id"] for e in _events(r1.text) + _events(r2.text) if e.get("type") == "done"}
    assert len(ids) == 2, (
        "占位正文带 tx_id ⇒ 两份内容哈希不同 ⇒ 不该被判重复。"
        "若哪天被「优化」成按文件名/字节去重，这里会只剩一篇 —— 那等于收纳模式下"
        "用户真的导入了两份不同用途的材料却被静默吞掉一份。"
        f"实际 id={ids}")


# ═══════════════════════════════════════════════════════════
# 5. retranscribe → done（本步最容易漏的一行）
# ═══════════════════════════════════════════════════════════

def test_转录之后状态变done(monkeypatch):
    ad = _FakeAdapter("video", probe={"title": "待转录视频"})
    _patch_route(monkeypatch, ad)
    data = _post_url("https://www.bilibili.com/video/BV1zz411c7mF").json()
    tx_id = data["id"]
    assert data["transcribeState"] == "pending"

    # 换了适配器实例：这次真的要「转录」（adapt 返回真正文）
    _patch_route(monkeypatch, _FakeAdapter("video", raw="真正的转录正文"))
    r = client.post(f"/api/transcripts/{tx_id}/retranscribe")
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "ok", r.text

    tx = _STORE.get_transcript(tx_id)
    assert tx["transcribeState"] == "done", (
        "`retranscribe` 必须**显式**写 done —— save_transcript_full 有「没带这个字段"
        "就沿用旧值」的守护，不写等于正文换了、界面永远显示「待转录」，且零报错")
    assert "limo-pending" not in tx["rawText"]
    assert tx["rawText"] == "真正的转录正文"
    assert _list_entry(tx_id)["transcribeState"] == "done"


def test_转录失败保持pending(monkeypatch):
    """失败时后端不写盘 ⇒ 文档仍是 pending、用户可重试。**刻意没有 failed 态**。"""
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "会失败的视频"}))
    tx_id = _post_url("https://www.bilibili.com/video/BV1aa411c7mG").json()["id"]

    class _Boom(_FakeAdapter):
        async def adapt(self, inp, on_progress=None, engine_config=None):
            raise RuntimeError("下载失败")

    _patch_route(monkeypatch, _Boom("video"))
    r = client.post(f"/api/transcripts/{tx_id}/retranscribe")
    assert r.json().get("code") == "RETX_FAILED", r.text
    assert _STORE.get_transcript(tx_id)["transcribeState"] == "pending"


# ═══════════════════════════════════════════════════════════
# 6. 守护：改标题不抹状态；删 index.json 重建后仍是 pending
# ═══════════════════════════════════════════════════════════

def test_改标题不会把pending抹成done(monkeypatch):
    """端到端回归：改标题之后状态不变。

    ⚠️ **这条对「第六个守护」没有区分力**，做过对照实验：去掉守护它照样绿。
    原因是三个调用方（PATCH / `retranscribe` / `_auto_title`）的 tx 都来自
    `get_transcript()`，而第 7 处落点让它**本就带着** `transcribeState` ——
    守护拿到的是「有字段」的 tx，压根不触发。

    真正守住这条行为的是**第 7 处落点**（`get_transcript` 带出该字段），
    守护是它之上的**第二道**，针对「将来有人手工拼 tx 的调用方」。两者互为兜底，
    单独摘掉任何一处这条都还是绿的 —— 所以下面另有一条专门考守护本身的。
    """
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "改标题测试"}))
    data = _post_url("https://www.bilibili.com/video/BV1bb411c7mH").json()
    tx_id = data["id"]

    r = client.patch(f"/api/transcripts/{tx_id}", json={"title": "新标题"})
    assert r.status_code == 200, r.text
    assert _STORE.get_transcript(tx_id)["transcribeState"] == "pending", (
        "PATCH 三项（标题/标签/文件夹）都不带 transcribeState —— 抹成 done 的后果是"
        "用户以为已转录、预览区却还是占位文本，两处信息互相矛盾且零报错")


def test_守护_调用方没带字段时沿用旧值(monkeypatch):
    """**这条考的是守护本身**。对照实验 A：去掉 `store.py` 里那两行 → 这条红。

    模拟形态就是「将来有人新写一个调用方，手工拼 tx dict 而忘了这个字段」——
    `save_transcript_full` 是**整体替换** index 条目，没守护 = 那次写入顺手把
    pending 改成 done。
    """
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "守护测试"}))
    tx_id = _post_url("https://www.bilibili.com/video/BV1bb411c7mN").json()["id"]

    tx = _STORE.get_transcript(tx_id)
    tx.pop("transcribeState")          # 模拟「调用方没带这个字段」
    tx["title"] = "守护测试-改"
    assert _STORE.save_transcript_full(tx) is True

    entry = _list_entry(tx_id)
    assert entry["transcribeState"] == "pending", (
        "`save_transcript_full` 整体替换条目 ⇒ 缺字段时必须**沿用旧值**，"
        "否则任何一次手工拼 tx 的写入都会把 pending 静默抹成 done")


def test_读路径get_transcript带出状态(monkeypatch):
    """第 7 处落点。对照实验：删掉 `get_transcript` 里那一行 → 这条红。

    它是「改标题不抹状态」真正的第一道：三个调用方全都从它取 tx。
    """
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "读路径测试"}))
    tx_id = _post_url("https://www.bilibili.com/video/BV1bb411c7mP").json()["id"]

    assert _STORE.get_transcript(tx_id)["transcribeState"] == "pending"


def test_删index重建后仍是pending(monkeypatch):
    """对照实验 E：`doc_meta.DOC_FIELDS` 去掉 `transcribeState` → 这条红。"""
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "重建测试"}))
    tx_id = _post_url("https://www.bilibili.com/video/BV1cc411c7mJ").json()["id"]

    (_STORE.root / "index.json").unlink()
    _STORE._rebuild_index()

    # ⚠️ 重建会由正文 sha256 重推 id（必然变），只能按标题找
    entries = [e for e in _STORE.list_transcripts() if e["title"] == "重建测试"]
    assert len(entries) == 1, f"重建后应仍有一篇；实际 {[e['title'] for e in _STORE.list_transcripts()]}"
    assert entries[0]["transcribeState"] == "pending", (
        "重建后 `transcribeState` 的唯一来源是 `{doc_dir}/meta.json` 的 DOC_FIELDS —— "
        "少了它，删一次 index.json 就整库变「已转录」")


# ═══════════════════════════════════════════════════════════
# 7. SSE 端点同样生效（前端三条导入入口里两条打的是 /stream）
# ═══════════════════════════════════════════════════════════

def test_SSE_路径同样收纳(monkeypatch):
    """对照实验 B：只把 `_run_adapter` 接到非流式端点 → 这条红。

    「单测绿、实机不生效」最可能的形态就是这一条 —— 前端 `api/ingest.ts` 的主路径
    是 `/ingest/stream`。
    """
    ad = _FakeAdapter("video", probe={"title": "SSE 收纳"})
    _patch_route(monkeypatch, ad)

    r = client.post("/api/ingest/stream",
                    data={"url": "https://www.bilibili.com/video/BV1dd411c7mK"})
    assert r.status_code == 200, r.text
    done = [e for e in _events(r.text) if e.get("type") == "done"]
    assert done, f"必须推 done；实际 {r.text[:300]}"
    assert done[0].get("transcribeState") == "pending", done[0]
    assert ad.called is False
    assert _list_entry(done[0]["id"])["transcribeState"] == "pending"


# ═══════════════════════════════════════════════════════════
# 8. 收纳完的文档要能搜到（两条路径各一次）
# ═══════════════════════════════════════════════════════════

def _search_ids(q: str) -> set[str]:
    return {h["id"] for h in app.state.search_engine.search(q)}


def test_收纳模式下非流式导入进全文索引(monkeypatch):
    """防有人把收纳的返回 `status` 改成 `"stored"` —— `_index_if_persisted` 的判据
    是 `status == "done"`，改状态码等于让索引写入被静默跳过。

    ⚠️ 这里直接问 `SearchEngine` 而不打 `GET /api/search`：那条路还会经过
    `fused_search` 的向量/重排分支，与本条要钉的「索引有没有写进去」无关。
    """
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "非流式检索关键词"}))
    data = _post_url("https://www.bilibili.com/video/BV1ee411c7mL").json()
    assert data["status"] == "done"
    assert data["id"] in _search_ids("非流式检索关键词"), (
        "收纳完也是库里的文档，标题能搜到才对；搜不到与「库里真没有」逐字相同")


def test_收纳模式下SSE导入进全文索引(monkeypatch):
    """对照实验 C：注释掉 `run_pipeline` 里的 `_index_if_persisted` → 这条红。

    这是本轮顺带修掉的**既有缺口**：改造前 SSE 路径压根没调索引，经主路径导入的
    文档在下次后端重启前搜不到。
    """
    _patch_route(monkeypatch, _FakeAdapter("video", probe={"title": "SSE 检索关键词"}))
    r = client.post("/api/ingest/stream",
                    data={"url": "https://www.bilibili.com/video/BV1ff411c7mM"})
    done = [e for e in _events(r.text) if e.get("type") == "done"]
    assert done, r.text[:300]
    assert done[0]["id"] in _search_ids("SSE 检索关键词"), (
        "SSE 导入必须写全文索引 —— 少了这一步，主路径导入的文档在重启前搜不到")


# ── 收尾 ──────────────────────────────────────────────────

def teardown_module():
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    _TMP.clear()
