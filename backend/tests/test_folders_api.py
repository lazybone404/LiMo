"""文件夹 & 批量操作 — 路由层测试（无需启动后端，走 TestClient）。

运行：uv run python tests/test_folders_api.py

⚠️ 为什么不只测 store：这里一半的判据**只有走 HTTP 链路才暴露**——
query 参数的空串 vs 缺席、请求体字段解析、真 400/404 语义、返回体契约。
典型：`GET /transcripts?folderId=` 若在后端写成真值判断，store 层测试全绿而
「未分类」视图永远拿不到过滤条件（退化成看全部）。

⚠️ 不碰真实数据目录：不进入 TestClient 上下文（= 不跑 startup event），
手动注入临时 Store。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store
from main import app


def _setup(n_docs: int = 3) -> tuple[TestClient, Store, str]:
    d = tempfile.mkdtemp(prefix="limo_folders_api_")
    store = Store(d)
    app.state.store = store
    for i in range(n_docs):
        store.save_transcript(RawTranscript(
            id=f"tr_{i}", title=f"文档{i}", source_type=SourceType.WEBPAGE,
            engine="trafilatura", raw_text=f"body {i}", content_hash=f"h{i}",
            metadata=RawMetadata(),
        ))
    return TestClient(app), store, d


def test_folder_crud_over_http():
    c, _, d = _setup()
    try:
        assert c.get("/api/folders").json() == []

        r = c.post("/api/folders", json={"name": "论文"})
        assert r.status_code == 200 and r.json()["id"].startswith("fd_")
        fid = r.json()["id"]

        assert c.post("/api/folders", json={"name": "   "}).status_code == 400, \
            "空名必须 400（不能静默建出无名字的文件夹）"

        r = c.patch(f"/api/folders/{fid}", json={"name": "文献"})
        assert r.status_code == 200 and r.json()["name"] == "文献"

        assert c.patch("/api/folders/fd_nope", json={"name": "x"}).status_code == 404
        assert c.get("/api/folders").json()[0]["count"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_folder_id_query_param_three_states():
    """不传 = 全部 / 空串 = 仅未分类 / id = 该文件夹。

    ⚠️ 空串这一格是本用例的核心：写成 `if folderId:` 时它会静默退化成「全部」，
    而响应仍是 200，前端只会觉得筛选没生效。
    """
    c, _, d = _setup()
    try:
        fid = c.post("/api/folders", json={"name": "论文"}).json()["id"]
        assert c.patch("/api/transcripts/tr_0", json={"folderId": fid}).status_code == 200

        assert len(c.get("/api/transcripts").json()) == 3, "不传 = 不过滤"
        assert len(c.get("/api/transcripts", params={"folderId": ""}).json()) == 2, \
            "空串 = 只看未分类（应剩 2 条）"
        inside = c.get("/api/transcripts", params={"folderId": fid}).json()
        assert [e["id"] for e in inside] == ["tr_0"], "id = 该文件夹"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_patch_title_keeps_folder_over_http():
    """整条 PATCH 链路：get_transcript → 改字段 → save_transcript_full（整体替换）。"""
    c, _, d = _setup()
    try:
        fid = c.post("/api/folders", json={"name": "论文"}).json()["id"]
        c.patch("/api/transcripts/tr_0", json={"folderId": fid})

        r = c.patch("/api/transcripts/tr_0", json={"title": "改过的标题"})
        assert r.status_code == 200 and r.json()["folderId"] == fid, \
            f"改标题把分类弄丢了：{r.json()}"

        assert c.patch("/api/transcripts/tr_1", json={"folderId": "fd_ghost"}).status_code == 404, \
            "移到不存在的文件夹必须 404"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_batch_action_enum_validation():
    """按 action 的显式枚举校验——必须真 400，不能静默返回 affected:0"""
    c, _, d = _setup()
    try:
        assert c.post("/api/transcripts/batch",
                      json={"action": "boom", "ids": ["tr_1"]}).status_code == 400
        assert c.post("/api/transcripts/batch",
                      json={"action": "move", "ids": ["tr_1"]}).status_code == 400, \
            "move 缺 folderId 必须 400（空串是有效值，不能用默认空串兜）"
        assert c.post("/api/transcripts/batch",
                      json={"action": "tag", "ids": ["tr_1"]}).status_code == 400
        assert c.post("/api/transcripts/batch",
                      json={"action": "tag", "ids": ["tr_1"], "tags": ["x"],
                            "tagMode": "boom"}).status_code == 400
        assert c.post("/api/transcripts/batch",
                      json={"action": "move", "ids": []}).status_code == 400
        assert c.post("/api/transcripts/batch",
                      json={"action": "move", "ids": ["tr_1"],
                            "folderId": "fd_ghost"}).status_code == 404
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_batch_move_partial_failure_reports_detail():
    c, _, d = _setup()
    try:
        fid = c.post("/api/folders", json={"name": "归档"}).json()["id"]

        r = c.post("/api/transcripts/batch",
                   json={"action": "move", "ids": ["tr_0", "tr_1", "tr_不存在"],
                         "folderId": fid})
        body = r.json()
        assert r.status_code == 200 and body["affected"] == 2, r.text
        assert len(body["failed"]) == 1 and body["failed"][0]["id"] == "tr_不存在", \
            "失败明细必须逐条返回（前端据此提示「已处理 X 条，失败 Y 条」）"

        # 只有 2 条真的进了文件夹（不存在的 id 未产生任何副作用）
        assert len(c.get("/api/transcripts", params={"folderId": fid}).json()) == 2

        # 全失败 → 400，避免前端把「一条没成」当成功
        r = c.post("/api/transcripts/batch",
                   json={"action": "move", "ids": ["tr_不存在"], "folderId": ""})
        assert r.status_code == 400, r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_batch_tag_and_delete():
    c, _, d = _setup()
    try:
        r = c.post("/api/transcripts/batch",
                   json={"action": "tag", "ids": ["tr_0"], "tags": ["重点"]})
        assert r.status_code == 200 and r.json()["affected"] == 1
        tags = {e["id"]: e.get("tags") for e in c.get("/api/transcripts").json()}
        assert tags.get("tr_0") == ["重点"], str(tags)

        r = c.post("/api/transcripts/batch",
                   json={"action": "delete", "ids": ["tr_0", "tr_1"]})
        assert r.status_code == 200 and r.json()["affected"] == 2, r.text
        assert len(c.get("/api/transcripts").json()) == 1

        # 混入不存在的 id：其余仍成功，失败的进 failed
        r = c.post("/api/transcripts/batch",
                   json={"action": "delete", "ids": ["tr_2", "tr_3"]})
        assert r.json()["affected"] == 1 and len(r.json()["failed"]) == 1, r.text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_delete_folder_keeps_docs_over_http():
    c, _, d = _setup()
    try:
        fid = c.post("/api/folders", json={"name": "临时"}).json()["id"]
        c.post("/api/transcripts/batch",
               json={"action": "move", "ids": ["tr_0", "tr_1", "tr_2"], "folderId": fid})

        r = c.delete(f"/api/folders/{fid}")
        assert r.status_code == 200 and r.json()["moved"] == 3, r.text
        assert c.delete(f"/api/folders/{fid}").status_code == 404

        docs = c.get("/api/transcripts").json()
        assert len(docs) == 3, "删文件夹绝不删文档"
        assert all(e.get("folderId", "") == "" for e in docs), "应全部回落未分类"
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL: {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if passed == len(tests) else 1)
