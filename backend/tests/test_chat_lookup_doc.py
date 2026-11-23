"""`lookup_doc` —— 读哪一份、读哪一节（2026-11-24）。

改造前它只读 `Transcript.rawText` 的前 2000 字：对话里**看不到笔记和整理版**，
也**读不到后面**。而用户问的常常是笔记里的那句，或者原稿里靠后的一段。

三件要钉住的事，每件改了都不会有报错：

1. **`part` 真的换了正文** —— 不是「加了参数但还在读 rawText」。
2. **不带 `section` 时给目录** —— 截断后只给开头，模型会以为「后面没有了」。
3. **节名对不上要显式说** —— 静默返回空会让模型以为「这一节是空的」。

⚠️ 零回归：默认 `part=transcript`、不传 `section` 时，返回形状与改造前逐字相同
（`title` / `docId` / `content` / `totalLength`）。
"""

import json
from types import SimpleNamespace

from app.routers.chat import (
    DOC_HEAD_CHARS,
    DOC_SECTION_MAX_CHARS,
    _make_lookup_doc,
)


SHORT = "很短的一段话。"

LONG = """导语一句

## 一、起点

正文一

## 二、RAG 解决什么

正文二，这里有一段关于检索增强的说明。

## 三、结论

正文三
"""


class _Store:
    def __init__(self, raw: str, modes: list[str] | None = None):
        self.raw = raw
        self.modes = modes or []

    def get_transcript(self, did: str) -> dict:
        return {"id": did, "title": "测试文档", "rawText": self.raw}

    def get_artifact_modes(self, ids: list[str]) -> dict[str, list[str]]:
        return {ids[0]: list(self.modes)}


class _Svc:
    """只提供 `resolve_latest`。产物正文按 part 准备。"""

    def __init__(self, parts: dict[str, str]):
        self._parts = parts

    def resolve_latest(self, doc_id: str, mode: str):
        if mode not in self._parts:
            return None
        return SimpleNamespace(content=self._parts[mode])


def _call(store, svc=None, **kw) -> dict:
    return json.loads(_make_lookup_doc(store, svc)(**kw))


# ── 零回归 ──

def test_默认读逐字稿且形状与改造前一致():
    out = _call(_Store(SHORT), id="d1")

    assert out == {"title": "测试文档", "docId": "d1", "part": "transcript",
                   "content": SHORT, "totalLength": len(SHORT)}


def test_query_仍然按行匹配():
    out = _call(_Store(LONG), id="d1", query="检索增强")

    assert out["matches"] == ["正文二，这里有一段关于检索增强的说明。"]
    assert out["totalLength"] == len(LONG)


# ── part ──

def test_part_notes_读的是笔记不是逐字稿():
    svc = _Svc({"notes": "## 笔记一\n\n这是笔记正文"})
    out = _call(_Store(SHORT), svc, id="d1", part="notes")

    assert out["part"] == "notes"
    assert "这是笔记正文" in out["content"]
    assert SHORT not in out["content"]


def test_没有该产物时显式报错并列出可读的份():
    out = _call(_Store(SHORT, modes=["notes"]), _Svc({}), id="d1", part="polished")

    assert "没有「polished」" in out["error"]
    assert out["parts"] == ["transcript", "notes"]


def test_传了产物但_svc_缺席_也不崩():
    out = _call(_Store(SHORT), None, id="d1", part="notes")

    assert "产物读取不可用" in out["error"]


# ── 目录 ──

def test_长文档截断时给出目录():
    big = "填充。" * 700 + "\n\n" + LONG
    assert len(big) > DOC_HEAD_CHARS      # 前提：这段真的会被截
    out = _call(_Store(big), id="d1")

    assert out["truncated"] is True
    assert out["sections"] == ["一、起点", "二、RAG 解决什么", "三、结论"]
    assert out["hint"]


def test_短文档不带目录也不标截断():
    out = _call(_Store(SHORT), id="d1")
    assert "truncated" not in out and "sections" not in out


# ── 按节读 ──

def test_section_返回该节全文与位置():
    out = _call(_Store(LONG), id="d1", section="二、RAG 解决什么")

    assert out["section"] == "二、RAG 解决什么"
    assert out["content"].startswith("## 二、RAG 解决什么")
    assert "检索增强" in out["content"]
    assert "## 三、结论" not in out["content"], "节的边界到下一个同级标题为止"
    assert out["offset"] > 0


def test_section_可以只写标题的一部分():
    out = _call(_Store(LONG), id="d1", section="RAG")
    assert out["section"] == "二、RAG 解决什么"


def test_节名对不上时显式报错并给出目录():
    """⚠️ 静默返回空 = 模型以为「这一节是空的」，而真实原因是标题抄错了一个字。"""
    out = _call(_Store(LONG), id="d1", section="四、不存在的节")

    assert out["error"] == "没有这一节"
    assert "一、起点" in out["sections"]


def test_超长节只给开头并标_truncated():
    big = "## 长节\n\n" + "字" * (DOC_SECTION_MAX_CHARS + 100)
    out = _call(_Store(big), id="d1", section="长节")

    assert out["truncated"] is True
    assert len(out["content"]) == DOC_SECTION_MAX_CHARS + 3   # + "..."
