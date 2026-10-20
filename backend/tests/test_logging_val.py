"""日志值格式化（`core/logging.py::_val`）—— **容器也必须截断**。

⚠️ 这条判据的失效形态是静默的：单看日志一切正常，只是每行里多了一段完整入参。
`GET /api/logs` 把最近 1000 条交给前端 LogPanel，用户排查问题时常常把日志贴给别人
—— 而工具入参里带着会话内容（搜索结果标题、文档片段、用户问题）。字符串一直截断、
容器一直不截断，纯粹是因为这条判据当时只写了 `str` 分支。
"""
import json

from app.core.logging import _MAX_CONTAINER, _MAX_STR, _val


def test_短字符串原样():
    assert _val("abc") == "abc"
    assert _val("") == ""


def test_长字符串按_80_截断():
    long = "x" * 200
    out = _val(long)
    assert len(out) == _MAX_STR
    assert out.endswith("...")


def test_容器超长也截断():
    """对照实验：去掉容器分支（退回 `return json.dumps(v, ...)`）→ 这条红，
    且红的原因是「长度 3000 > 300」，不是断言写错。"""
    big = {"query": "很长的搜索词" * 300, "hits": list(range(50))}
    raw = json.dumps(big, ensure_ascii=False)
    assert len(raw) > _MAX_CONTAINER          # 前提：这份输入确实超了
    out = _val(big)
    assert len(out) == _MAX_CONTAINER
    assert out.endswith("...")


def test_小容器原样保留_JSON():
    assert _val({"a": 1}) == '{"a": 1}'
    assert _val([1, 2]) == "[1, 2]"


def test_bool_不被当成数字():
    """`bool` 是 `int` 的子类 —— 分支顺序写反时它照样输出 `True`（str(True)），
    所以这条**不是为了行为**，是为了钉住「顺序是有意的」这件事：
    将来若把 bool 分支改成输出 `true`（JSON 风格），顺序就决定它会不会被数字分支吞掉。"""
    assert _val(True) == "True"
    assert _val(False) == "False"
