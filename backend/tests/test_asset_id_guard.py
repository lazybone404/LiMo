"""P1-11 — 文件服务的 tx_id 校验必须与读路径**同源**（尤其**长度上限**）。

**修复前的形状**（`routers/ingest.py::_validate_tx_id`）：

    if not re.match(r'^[a-zA-Z0-9_\\-]+$', tx_id):     # ← 只有字符集，没有长度上限
        raise AppError("INGEST_INVALID_TX_ID", ...)

同一个 tx_id，`QueryService.resolve_doc_id`（`services/query.py:234`）与
`files._check_tx_id` 都走 `core/resource_id.is_valid_resource_id`（**有** 64 上限），
只有文件服务这一条放行无限长。**两侧都不报错**——只在某个超长 ID 上表现为
「一半端点 404、另一半能取到图」（模式 #54：同源是搜索题不是记忆题）。

⚠️ 判据要求：超长 ID 必须在**校验层**被拒（400 + `INGEST_INVALID_TX_ID`），
不是走到「文件不存在」的 404 —— 后者说明它已被当成合法 ID 放行了。

对照实验（均已实测，证明本文件有区分力）：
  A. 把 `_validate_tx_id` 改回裸正则（无长度上限）
     → `test_overlong_id_rejected` / `test_both_endpoints_reject_overlong` 红
     （得到 404 `INGEST_FILE_NOT_FOUND` 而非 400）
  B. 把上限从 64 放宽到 128 → 同上两条红
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient      # noqa: E402

from app.storage.store import Store            # noqa: E402
from main import app                           # noqa: E402

# 合法字符集、**超** 64 上限（`resource_id.MAX_ID_LEN`）
_OVERLONG = "a" * 65


def _setup() -> tuple[TestClient, str]:
    """临时 Store。⚠️ **不进 TestClient 上下文**（不跑 startup event）——与
    `test_delete_index.py` / `test_folders_api.py` 同一手法。"""
    d = tempfile.mkdtemp(prefix="limo_asset_id_")
    app.state.store = Store(d)
    return TestClient(app), d


def test_overlong_id_rejected():
    client, d = _setup()
    try:
        r = client.get(f"/api/assets/{_OVERLONG}/a.png")
        assert r.status_code == 400, (
            "超长 tx_id 必须在**校验层**被拒（400），而不是走到「文件不存在」的 404"
            f"——后者说明它被当成合法 ID 放行了：{r.status_code} {r.text[:200]}")
        assert r.json().get("code") == "INGEST_INVALID_TX_ID", r.text[:200]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_both_endpoints_reject_overlong():
    """两个媒体端点共用同一判据——修一处不能只修一半。"""
    client, d = _setup()
    try:
        for path in (f"/api/assets/{_OVERLONG}/a.png",
                     f"/api/files/{_OVERLONG}/a.png"):
            r = client.get(path)
            assert r.status_code == 400, f"{path} → {r.status_code}（应共用同一条判据）"
            assert r.json().get("code") == "INGEST_INVALID_TX_ID", path
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_valid_id_not_over_rejected():
    """**反向对照**：合法 ID 必须照常放行到「文件不存在」（修复不能把正常请求一起拦掉）。"""
    client, d = _setup()
    try:
        r = client.get("/api/assets/tr_abc123def456/a.png")
        code = r.json().get("code")
        # 走到查找阶段即算通过：文档不存在（无 doc_dir）或文件不存在，两者都说明
        # **校验放行了**；`INGEST_INVALID_TX_ID` 才是「被误拦」。
        assert code in ("INGEST_DOC_NOT_FOUND", "INGEST_FILE_NOT_FOUND"), (
            f"合法 ID 应走到查找阶段而非被校验拦下：{r.status_code} {r.text[:200]}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_chars_still_rejected():
    """字符集这一半不能被「换成公共判据」换没了（`is_valid_resource_id` 同样拒 `.`）。"""
    client, d = _setup()
    try:
        r = client.get("/api/assets/tr.bad/a.png")
        assert r.status_code == 400, f"{r.status_code} {r.text[:200]}"
        assert r.json().get("code") == "INGEST_INVALID_TX_ID", r.text[:200]
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
