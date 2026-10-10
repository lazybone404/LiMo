"""对话导出的 `limo-block` 处理 —— **`.md` 剥、`.json` 保留**（刻意的非对称）。

协议与解析本身在 `test_artifact_blocks.py`（读共享 fixture）；产物侧的导出消费点在
`test_export_blocks.py`。**本文件只管对话这一条导出链**——它有一套自己的两个函数
（单条 / 批量），而「改了一处漏了另一处」正这类缺陷的默认形态：单条导出干净、批量导出
脏，两边都不报错，只有用户拿到文件才发现。

要防的失败（全是静默的）：

  A. 两个导出函数漏剥 → `.md` 里留着 `<!-- limo-block:v1 type="card-group" -->`
     （用户拍板「导出的文件应当是干净正文」）。
  B. 剥过头（比如顺手把整块内容也删了）→ 卡片文字**凭空消失**。故断言必须**双向**：
     标记不在 **且** 结构还在。
  C. 把 `.json` 也剥了 → 它**明确**是数据记录 / 可回灌格式，剥掉标记等于再导入时
     卡片结构消失。这条非对称是决策，不是疏忽，故用断言钉住，免得后人「顺手统一」。
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.dependencies import get_store
from app.core.errors import AppError
from app.core.types import Conversation
from app.routers import export as E
from app.storage.store import Store

_OPEN = '<!-- limo-block:v1 type="card-group" -->'
_CLOSE = "<!-- /limo-block -->"
CARD_GROUP = f"{_OPEN}\n### 方案 A\n直接整篇提交。\n\n### 方案 B\n先分块再处理。\n{_CLOSE}"
FOLLOW_UP = ('<!-- limo-block:v1 type="follow-up" -->\n'
             "- 分块会不会丢全局信息？\n- 有没有更小的模型能胜任？\n"
             "<!-- /limo-block -->")
ANSWER = f"两种方案各有取舍。\n\n{CARD_GROUP}\n\n{FOLLOW_UP}"

#: ⚠️ 用户消息**也**可能带标记：用户可以手工粘贴/编辑自己的消息，且 `editMessage`
#: 允许改写成任何文本。两个分支都走剥标记，不是只有 AI 那条。
USER_TEXT = f"我先前记过一段。\n\n<!-- limo-block:v1 type=\"follow-up\" -->\n- 还有别的吗？\n{_CLOSE}"

_TMP: list[str] = []


def _new_store() -> Store:
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


_STORE = _new_store()
_STORE.save_conversation(Conversation(
    id="conv_limo1", title="两方案对比",
    messages=[
        {"role": "user", "content": USER_TEXT, "timestamp": "2026-10-11T10:00:00"},
        {"role": "assistant", "content": ANSWER, "timestamp": "2026-10-11T10:00:05"},
    ],
))
_STORE.save_conversation(Conversation(
    id="conv_limo2", title="第二段对话",
    messages=[{"role": "assistant", "content": CARD_GROUP, "timestamp": "2026-10-11T11:00:00"}],
))

app = FastAPI()
app.include_router(E.router, prefix="/api")
app.dependency_overrides[get_store] = lambda: _STORE


@app.exception_handler(AppError)
async def _app_error_handler(request, exc: AppError):
    # ⚠️ 与 main.py **同规则**（模式 #36），与 `test_export_blocks.py` 逐字同源 ——
    # 自造一份更宽松的契约会让判据失去意义。
    http_status = exc.status_code if exc._explicit_status else (
        200 if exc.recoverable else exc.status_code)
    return JSONResponse(status_code=http_status, content=exc.to_dict())


client = TestClient(app)


def _md(conv_id: str = "conv_limo1") -> str:
    r = client.get(f"/api/export/conversation/{conv_id}", params={"format": "md"})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return r.text


def _json_conv(conv_id: str = "conv_limo1") -> dict:
    r = client.get(f"/api/export/conversation/{conv_id}", params={"format": "json"})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return json.loads(r.text)


# ═══════════════════════════════════════════════════════════════
# A. 单条导出
# ═══════════════════════════════════════════════════════════════

def test_夹具自证_未剥的话导出里就真的会有标记():
    """⚠️ **对照实验的机器形式**：先断言 store 里的原文确实含标记行。

    没有这一条的话，哪天夹具被改成不含标记，下面那组 `not in` 断言会**全部变成恒真**
    ——绿着失去意义，而报告上看不出来。
    """
    conv = _STORE.get_conversation("conv_limo1")
    for m in conv["messages"]:
        assert "<!-- limo-block" in m["content"], m["role"]


def test_md_导出剥掉标记行():
    """对照 A：漏剥的话 `.md` 里会有一行 `<!-- limo-block:v1 … -->`。"""
    out = _md()
    assert "limo-block" not in out
    assert "<!--" not in out


def test_md_导出保留卡内结构与全部文字():
    """对照 B：**双向断言**。只断「标记不在」的话，把整块内容一起删掉的实现照样绿 ——
    而那正是「卡片全没了」的静默版本。"""
    out = _md()
    for chunk in ("### 方案 A", "直接整篇提交。", "### 方案 B", "先分块再处理。",
                  "两种方案各有取舍。", "分块会不会丢全局信息？", "还有别的吗？"):
        assert chunk in out, chunk


def test_用户消息与_AI_消息走同一条剥标记路径():
    """两个分支各写一遍 `_strip_blocks` 是常态写法，漏一个的症状是「某一侧带着标记」。
    这里两侧都放了标记，任一漏剥即红。"""
    out = _md()
    assert out.count("limo-block") == 0


def test_json_导出刻意保留标记():
    """对照 C：`.json` 是**数据记录 / 可回灌格式**，剥掉标记等于再导入时卡片结构没了。
    与 `.md` 的非对称是决策（`export.py` 里那段注释就在说这件事），故钉住它 ——
    免得后人「顺手统一」。"""
    conv = _json_conv()
    body = json.dumps(conv, ensure_ascii=False)
    assert "<!-- limo-block:v1" in body
    assert conv["messages"][1]["content"] == ANSWER


# ═══════════════════════════════════════════════════════════════
# B. 批量导出（只改单条、漏批量是同类缺陷的默认形态）
# ═══════════════════════════════════════════════════════════════

def _batch() -> dict[str, str]:
    r = client.post("/api/export/conversations/batch",
                    json={"ids": ["conv_limo1", "conv_limo2"]})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".md")}


def _batch_json() -> dict[str, str]:
    r = client.post("/api/export/conversations/batch",
                    json={"ids": ["conv_limo1", "conv_limo2"]})
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".json")}


def test_批量_md_每一条都剥干净():
    # ⚠️ 不写死 zip 条目名（它过 `_safe_filename` + RFC 5987 编码）—— 断言的是
    # 「每一条都干净」，按值迭代即可，名字不是这条判据的一部分。
    mds = _batch()
    assert len(mds) == 2, mds.keys()
    for name, text in mds.items():
        assert "limo-block" not in text, name
        assert "<!--" not in text, name
        # 内容仍在 —— **逐条**自证，不是只看其中一条（漏剥与剥过头是两个方向）
        assert "### 方案 A" in text, name


def test_批量_json_同样保留标记():
    jsons = _batch_json()
    assert len(jsons) == 2, jsons.keys()
    for name, text in jsons.items():
        assert "limo-block:v1" in text, name


def teardown_module(module):
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
