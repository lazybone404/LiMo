"""检索质量门禁 —— 字面层（`SearchEngine.search`）退化就红。

运行：`bash scripts/check-all search`（或 `pytest backend/tests/test_search_eval.py`）

⚠️ **它测的是哪一层**：只测字面检索（`SearchEngine.search`），**不含**向量与 RRF 融合
（`VectorSearcher.fuse` 的 depth / RRF k / 语义独有回补一个字都不会反映到分数上），
**也不含**对话侧。所以「这个门禁是绿的」只说明字面这条路没退，别当成「检索整体质量」。

⚠️ **绝对分数不可迁移**：这里的「一篇文档」是 md 的一个 H2 小节（几百到三千字），
真实库里一篇 transcript 是整段视频转录（上万字）。BM25 的长度归一化让检索单元变小
⇒ 命中显著更容易 —— 这正是本项目踩过的 40pp 落差形态。**这个分数只在同一份语料的前后
对比里有意义**，不要拿去跟 `eval_search.py` 的 29 题真实库分数比。

⚠️ **判据同源**：打分走 `eval_search._score`（与真实库测评是同一个函数），本文件只提供
数据与断言。各写一份的失效形态是「门禁说没退、真实库说退了」，而两边都觉得自己对。

⚠️ **更新基线**：`python backend/scripts/build_search_eval.py --update-baseline`，
**并在 docs/CHANGELOG.md 写一句为什么** —— 否则基线会变成橡皮图章。
语料指纹对不上时本文件直接红，那是「改了语料却没重跑基线」，不是「检索退化了」。

⚠️ **本文件也会被 `stage_pytest` 跑到**，故 `check-all all` 里它跑两次。这是刻意的：
`search` 层的价值在**失败时输出的那一屏**（哪题掉了 / 从第几掉到第几 / 更新基线的命令），
不在另起一套判据；多跑一次约 1–2 秒，不值得为它去改 pytest 的收集范围。
"""
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))            # backend/
sys.path.insert(0, str(_HERE.parent / "scripts"))  # backend/scripts

from build_search_eval import (                   # noqa: E402
    BASELINE_PATH, CORPUS_PATH, QUERIES_PATH, corpus_hash, score,
)

_UPDATE_HINT = (
    "若这次下降是**有意**的（例如换分词策略并接受代价），跑\n"
    "  python backend/scripts/build_search_eval.py --update-baseline\n"
    "更新基线，并在 docs/CHANGELOG.md 写一句理由 —— 否则基线会变成橡皮图章。"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _baseline() -> dict:
    return _load(BASELINE_PATH)


@lru_cache(maxsize=None)
def _corpus() -> dict:
    return _load(CORPUS_PATH)


@lru_cache(maxsize=None)
def _queries() -> list:
    return _load(QUERIES_PATH)["queries"]


@lru_cache(maxsize=None)
def _result() -> dict:
    """跑一次分，四个用例共用（`rebuild` + 30 次 search，纯 FTS5，约 1 秒）。"""
    return score(_corpus(), _queries(), _baseline()["topk"])


def _rank_text(rank: int) -> str:
    return f"第 {rank} 名" if rank else "未召回"


def _dropped(now: dict, kind: str | None = None) -> list[str]:
    """相对基线**从命中掉成未命中或名次变差**的题 —— 失败时第一眼要看的就是它。"""
    base, topk, out = _baseline(), _baseline()["topk"], []
    for q in _queries():
        if kind and q["kind"] != kind:
            continue
        was = base["ranks"].get(q["query"], 0)
        got = now["ranks"].get(q["query"], 0)
        if 0 < was <= topk and not (0 < got <= topk):
            out.append(f"  · [{q['kind']}] {q['query']}：{_rank_text(was)} → {_rank_text(got)}")
        elif 0 < was <= topk and got > was:
            out.append(f"  · [{q['kind']}] {q['query']}：{_rank_text(was)} → {_rank_text(got)}（仍在）")
    return out


def test_语料与基线同源():
    """改了语料却没重跑基线 ⇒ 拿旧基线给新语料打分，红绿都失去意义。"""
    assert _baseline()["corpusHash"] == corpus_hash(_corpus()), (
        "语料指纹与基线不符（改了语料 / 切分规则，却没重跑基线）。\n"
        "  python backend/scripts/build_search_eval.py --update-baseline\n"
        "⚠️ 这一步会**换掉对照的尺子**，先确认变化是想要的，再在 CHANGELOG 记一句。"
    )


def test_查询集指向的切片都存在():
    """题目指向已被改名或删掉的切片时，那题会静默变成「未召回」—— 那不是检索退化。"""
    ids = {s["id"] for s in _corpus()["slices"]}
    bad = [f"  · {q['query']} → {q['want']}" for q in _queries() if q["want"] not in ids]
    assert not bad, "查询集里有题指向不存在的切片：\n" + "\n".join(bad)


def test_整体命中不低于基线():
    now, base = _result(), _baseline()["overall"]
    assert now["hit"] >= base["hit"], (
        f"字面检索质量下降：合计 {now['hit']}/{now['total']}，基线 {base['hit']}/{base['total']}"
        f"（判据前 {_baseline()['topk']}）\n\n"
        + ("掉了：\n" + "\n".join(_dropped(now)) + "\n\n" if _dropped(now) else "")
        + _UPDATE_HINT
    )


def test_分类命中不低于基线():
    """分三类各自守 —— 只看总分会被「语义全崩、关键词全对」蒙混过去。"""
    now, base = _result()["per_kind"], _baseline()["perKind"]
    bad = []
    for kind, (b_hit, b_total) in base.items():
        n_hit, n_total = now.get(kind, [0, 0])
        if n_hit < b_hit:
            bad.append(f"  · {kind} {n_hit}/{n_total}，基线 {b_hit}/{b_total}")
    assert not bad, (
        "字面检索的某一类退化了：\n" + "\n".join(bad) + "\n\n"
        + ("掉了：\n" + "\n".join(l for l in _dropped(_result())) + "\n\n" if _dropped(_result()) else "")
        + _UPDATE_HINT
    )


def test_负向对照_目标整体错位后分数显著下降():
    """自证这个数字**有区分力**：把每题的目标换成相邻切片，分数必须明显掉下来。

    没有这一条，一个恒返回 77% 的假判据也能「守住」基线。实测错位后 4/30（基线 23/30）。
    """
    ids = [s["id"] for s in _corpus()["slices"]]
    pos = {s: i for i, s in enumerate(ids)}
    shifted = [{**q, "want": ids[(pos[q["want"]] + 1) % len(ids)]} for q in _queries()]
    got = score(_corpus(), shifted, _baseline()["topk"])
    base_hit = _baseline()["overall"]["hit"]
    assert got["hit"] <= base_hit - 10, (
        f"负向对照没生效：目标整体错位后仍有 {got['hit']}/{got['total']}"
        f"（基线 {base_hit}）—— 判据区分力不足，查查询集是不是全靠某几道题撑分。"
    )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✓ {name}")
