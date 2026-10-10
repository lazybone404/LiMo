"""对话引用去重（`_append_refs`）—— 防 React 重复 key。

背景：assistant 消息的引用 chips 用 `docId` 当 React **key**，重复项会让 chips 渲染
错乱并触发 React 重复 key 警告。而同一篇文档被多个工具轮次引用是**常态**
（`search_docs` 命中 → `lookup_doc` 打开），所以落盘的 `doc_refs` 里出现重复是必然，
不是异常。

⚠️ 症状之所以隐蔽：前端**流式路径**（`onRef`）顺手去重了，所以实时对话期间看不出来；
只有**重开会话**时从落盘数据复原（`openConversation`）才炸 —— 「刚才还好好的」。

⚠️ 本文件末尾有两条**成对**的非纯单测：一条对照实验（证明判据有区分力）+ 一条接线
守卫（证明调用点仍走这个函数）。缺了后者，`_append_refs` 测得再对也拦不住调用点被
改回 `refs.extend(...)` —— 那种情况下上面几条用例**照样全绿**。
"""
import re
from pathlib import Path

from app.routers.chat import _append_refs


def _run(events: list) -> list[dict]:
    """按调用点的用法驱动：**同一对 `refs` / `seen` 跨事件复用**（去重靠的就是这点）。"""
    refs: list[dict] = []
    seen: set[str] = set()
    for ev in events:
        _append_refs(refs, seen, ev)
    return refs


# ── 真行为 ──

def test_跨工具轮次重复的文档只留一次():
    """两个 tool_result 事件引用同一篇 → 只留首次那条。"""
    refs = _run([
        [{"docId": "tr_a", "title": "A"}, {"docId": "tr_b", "title": "B"}],
        [{"docId": "tr_a", "title": "A"}],          # 第二次是另一轮工具（如 lookup_doc）
    ])
    assert [r["docId"] for r in refs] == ["tr_a", "tr_b"]
    # 保留的是**首次**那条对象本身（不是后一条覆盖前一条）
    assert refs[0]["title"] == "A"


def test_保序而非集合序():
    """首见顺序决定结果顺序。

    ⚠️ 判据有区分力：若实现写成 `list(dict.fromkeys(...))` 之外的集合往返（或干脆
    先 set 再 list），顺序就不再由输入决定 —— 这条断言的第二个元素会漂。
    """
    refs = _run([
        [{"docId": "b", "title": "B"}, {"docId": "a", "title": "A"}],
        [{"docId": "c", "title": "C"}],
    ])
    assert [r["docId"] for r in refs] == ["b", "a", "c"]


def test_缺_docId_或空_docId_的项跳过():
    """没有 docId 的引用无法当 key，也认不出重复 —— 直接丢弃（不是塞个空串进去）。"""
    refs = _run([[
        {"title": "没有 id"},
        {"docId": "tr_a", "title": "A"},
        {"docId": "", "title": "空串 id"},
    ]])
    assert [r["docId"] for r in refs] == ["tr_a"]


def test_空列表与_None_不崩():
    """`event.get("refs")` 可能是缺失（None）或空 —— 两者都必须是合法输入。"""
    refs: list[dict] = []
    seen: set[str] = set()
    _append_refs(refs, seen, None)
    _append_refs(refs, seen, [])
    assert refs == []


def test_seen_是跨事件状态_每次调用原地更新():
    """`seen` 若在函数内新建，同一对 refs/seen 复用也拦不住第二轮重复。"""
    refs: list[dict] = []
    seen: set[str] = set()
    _append_refs(refs, seen, [{"docId": "tr_a", "title": "A"}])
    assert "tr_a" in seen
    _append_refs(refs, seen, [{"docId": "tr_a", "title": "A"}])
    assert len(refs) == 1


# ── 对照实验 ──

def test_对照_修复前的_extend_写法确实会留下重复():
    """把调用点换回 `refs.extend(...)`（修复前的行为）→ 重复出现。

    ⚠️ 这是「上面的断言有区分力」的证据：若拿这条写法跑，`test_跨工具轮次重复的文档
    只留一次` 的首个断言必红。没有它，「去重」可能只是场景本身不产生重复的巧合。
    """
    refs: list[dict] = []
    for ev in ([{"docId": "tr_a", "title": "A"}], [{"docId": "tr_a", "title": "A"}]):
        refs.extend(ev)
    assert len(refs) == 2, "旧写法应当是重复的 —— 否则本组的场景构造失效"


# ── 接线守卫 ──

def test_接线_sse_stream_仍调用_append_refs():
    """⚠️ 纯单测**拦不住调用点漂移**：`sse_stream` 是嵌套 async generator，测试够不到；
    若那里被改回 `refs.extend(...)`，上面所有用例照样全绿，而线上重复 key 复活。

    故直接读源码断言：① `_append_refs(` 出现在文件里；② 没有任何 `refs.extend(`
    （唯一的 refs 累加点必须是它）。
    """
    src = Path(__file__).resolve().parents[1] / "app" / "routers" / "chat.py"
    text = src.read_text(encoding="utf-8")
    body = re.sub(r"^\s*#.*$", "", text, flags=re.M)   # 剥注释：注释里提到 extend 不算

    assert "_append_refs(refs, seen_ref_ids, event.get(\"refs\"))" in body
    assert "refs.extend(" not in body
