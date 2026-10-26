"""向量索引纯算子的契约测试（`app/core/vector_index.py`）。

运行：./.venv/Scripts/python.exe -m pytest tests/test_vector_index.py -q

**这一轮测的不是「检索准不准」**（那是 `scripts/eval_search.py` 的活，要真实库 +
真模型），而是三个算子在**搬家前后行为不变**，以及几条各自「看起来正常实际是错的」
失效形态：

  · `chunk_flat` 对超长段落**连续切**而不是截断 —— 截断的失效是静默的（块数正常、
    产物正常，只是后半段永远搜不到），见该函数里 T2Retrieval 的实测记录
  · 文档得分取各块**最大值**而不是平均 —— 取平均会稀释「只有一段相关」的长文档
  · RRF 只用名次，且 `weights` 缺省逐字节等价于改造前

最后一条是**同源守卫**：`scripts/embed_local.py` 不得再实现一份算子。它今天只是
转出（`from app.core.vector_index import ...`），抄回去的失效形态是「评测跑 A 套切块、
产品跑 B 套」，而两边都报成功。
"""

import ast
from pathlib import Path

import numpy as np
import pytest

from app.core.vector_index import (CHUNKERS, chunk_flat, chunk_h2, rrf,
                                   vector_rank, vector_rank_scored)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


# ═══════════════════════════════════════════════════════════════
# 切块
# ═══════════════════════════════════════════════════════════════

def test_短文本一块_空文本零块():
    assert chunk_flat("hi") == ["hi"]
    assert chunk_flat("") == []
    assert chunk_flat("\n\n\n") == [], "纯空白不该产出一个空块"


def test_段落不被合并切碎():
    """两段合计装得下 → 保持一段一块（`\\n\\n` 原样留着）。"""
    text = "a" * 50 + "\n\n" + "b" * 50
    assert chunk_flat(text, 200) == [text]


def test_装不下时按段边界分开_不预先切断段落():
    """合计 202 > 200 → 在两段之间分开，而不是从中间截断。"""
    a, b = "a" * 100, "b" * 100
    assert chunk_flat(a + "\n\n" + b, 200) == [a, b]


def test_超长单段连续切且零丢失():
    """⚠️ 这是 T2Retrieval 那次「118,605 篇全部只索引了开头 200 字」的守卫。

    判据不是「切了几块」（数字对了也可能内容错），而是**拼回去与原文逐字相等**。
    """
    text = "段" * 650                      # 无 `\\n\\n`，单段 650 字
    chunks = chunk_flat(text, 200)
    assert len(chunks) == 4                # 200 + 200 + 200 + 50
    assert "".join(chunks) == text
    assert all(len(c) <= 200 for c in chunks)


def test_chunk_h2_每块前置完整标题且不重复():
    """⚠️ 这条是 2026-10-26 修缺陷后的**新契约**。

    修之前 `H2` 正则是 `^##\\s+\\S`（`\\S` 只取一个字），前置的是 `"## 标"`；
    而 `body` 本身又**包含**标题行 —— 所以只改正则会让标题在块里印两遍。
    两条一起修，判据是**逐字相等**而不是「以标题开头」（后者对「印两遍」也成立）。
    """
    text = "前言\n\n## 标题一\n\n正文一\n\n## 标题二\n\n正文二"
    assert chunk_h2(text, 400) == [
        "前言",
        "## 标题一\n\n正文一",
        "## 标题二\n\n正文二",
    ]
    # 显式钉住「不重复」：每块只出现一次标题标记
    assert all(c.count("## ") == 1 for c in chunk_h2(text, 400)[1:])


def test_chunk_h2_只有标题的节不丢():
    """整节只有一行 `## 标题` 时，`chunk_flat("")` 返回空 —— 早先连标题一起丢掉。

    丢标题的失效形态很隐蔽：块数少一个、没报错，只是那一节彻底搜不到。
    """
    assert chunk_h2("## 只有标题", 400) == ["## 只有标题"]


def test_chunk_h2_无标题时退化固定切():
    text = "a" * 50 + "\n\n" + "b" * 50
    assert chunk_h2(text, 200) == chunk_flat(text, 200)


def test_CHUNKERS_键集与产品参数一致():
    """`eval_search.py --chunk` 的 choices 就是这两个键，改名会让两边静默对不上。"""
    assert set(CHUNKERS) == {"flat", "h2"}


# ═══════════════════════════════════════════════════════════════
# 向量排名
# ═══════════════════════════════════════════════════════════════

def _mat(*rows):
    return np.array(rows, dtype=np.float32)


def test_文档得分取各块最大值不是平均():
    """A 有一块完美命中、一块完全无关；B 的两块都中等。

    取最大值 → A 第一（这是产品语义：文档的哪一段相关就够了）。
    取平均   → B 第一（0.8 > 0.5），长文档会被稀释 —— 那正是要防的。
    """
    mat = _mat([1.0, 0.0], [0.0, 1.0], [0.8, 0.0], [0.8, 0.0])
    owner = np.array(["a", "a", "b", "b"])
    q = _mat([1.0, 0.0])

    scored = vector_rank_scored(mat, owner, q, depth=2)[0]
    assert [tid for tid, _ in scored] == ["a", "b"]
    assert scored[0][1] == pytest.approx(1.0)
    assert scored[1][1] == pytest.approx(0.8)
    assert vector_rank(mat, owner, q, depth=2) == [["a", "b"]]


def test_depth_截断到文档数上限():
    mat = _mat([1.0, 0.0], [0.9, 0.0], [0.8, 0.0])
    owner = np.array(["a", "b", "c"])
    assert len(vector_rank(mat, owner, _mat([1.0, 0.0]), depth=100)[0]) == 3
    assert vector_rank(mat, owner, _mat([1.0, 0.0]), depth=2) == [["a", "b"]]


def test_空矩阵不崩():
    """库还没建向量时查询侧会走到这里 —— 返回空列表，不是 ZeroDivision 或 IndexError。"""
    out = vector_rank(np.zeros((0, 4), dtype=np.float32), np.array([], dtype=object),
                      _mat([1.0, 0.0, 0.0, 0.0]))
    assert out == [[]]


def test_同分时顺序由_id_排序决定():
    """`np.unique` 已排序 ⇒ 结果确定。否则同一份数据两次查询可能给不同顺序。"""
    mat = _mat([1.0, 0.0], [1.0, 0.0])
    owner = np.array(["b", "a"])
    assert vector_rank(mat, owner, _mat([1.0, 0.0]), depth=5) == [["a", "b"]]


# ═══════════════════════════════════════════════════════════════
# RRF
# ═══════════════════════════════════════════════════════════════

def test_两路都召回的排最前():
    """b 在字面第 2、向量第 1 → 合计分最高；a 只在字面第 1；c 只在向量第 2。"""
    assert rrf([["a", "b"], ["b", "c"]], k=10) == ["b", "a", "c"]


def test_weights_缺省等价于全一():
    lists = [["a", "b"], ["b", "c"]]
    assert rrf(lists, k=10) == rrf(lists, k=10, weights=[1.0, 1.0])


def test_weights_能压低低精度那一路():
    """等权时 a、b 并列；给第一路 10 倍权重后 a 明显领先。"""
    assert rrf([["a"], ["b"]], k=10) == ["a", "b"]        # 并列，靠插入序
    assert rrf([["a"], ["b"]], k=10, weights=[10.0, 1.0]) == ["a", "b"]
    assert rrf([["b"], ["a"]], k=10, weights=[10.0, 1.0]) == ["b", "a"]


def test_weights_长度不符显式报错():
    with pytest.raises(ValueError, match="weights 长度"):
        rrf([["a"], ["b"]], weights=[1.0])


# ═══════════════════════════════════════════════════════════════
# 同源守卫：评测侧不得再实现一份
# ═══════════════════════════════════════════════════════════════

def test_embed_local_只剩转出没有第二份实现():
    """`scripts/embed_local.py` 可以 import 这些算子，但不能再 `def` 一遍。

    抄一份的失效形态是「评测跑 A 套切块、产品跑 B 套」——`_experiments/` 里的
    93% / 83% 会悄悄变成对另一套实现的描述，且两边都不报错。
    """
    tree = ast.parse((SCRIPTS / "embed_local.py").read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    borrowed = {"chunk_flat", "chunk_h2", "rrf", "vector_rank", "vector_rank_scored"}
    assert not (defined & borrowed), (
        f"embed_local.py 又定义了 {sorted(defined & borrowed)} —— "
        "这些算子只该住在 app/core/vector_index.py")
