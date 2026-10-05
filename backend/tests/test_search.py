"""全文搜索 — FTS5 索引与摘要正确性（纯函数单测，无需启动后端）。

运行：uv run python tests/test_search.py
覆盖：摘要列号（正文命中必须取 content 列）/ 标题命中（含**只在标题里**的中文词）/
CJK 逐字分词 / rebuild 守卫（正文为空但标题非空）/ 删除后不留幽灵命中

⚠️ 本文件的判据都刻意构造成「实现写错时必失败」——见每个用例的 ⚠️ 说明。
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.search import SearchEngine


def _engine():
    d = tempfile.mkdtemp(prefix="limo_search_test_")
    return SearchEngine(d), d


def test_snippet_comes_from_content():
    """⚠️ 摘要必须取自 content 列（snippet 列索引 2），不是 title（1）。

    构造：标题刻意**不含**关键词，只有正文含。
    - 列号 2（正确）→ 摘要里出现 needle
    - 列号 1（bug）→ 摘要返回标题原文 '完全无关的标题'，断言失败
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "完全无关的标题", "正文中间藏着 needle 这个词，前后都是别的内容。")
        hits = eng.search("needle", limit=10)
        assert len(hits) == 1, f"应命中 1 条，实际 {len(hits)}"
        snippet = hits[0]["snippet"]
        assert "needle" in snippet, (
            f"摘要里没有关键词——说明 snippet 取自 title 列而非 content 列。snippet={snippet!r}"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_title_hit_still_matches():
    """标题命中同样要能搜到（回归：改列号不能把标题搜索弄坏）"""
    eng, d = _engine()
    try:
        eng.index("tr_1", "needle 在标题里", "正文与关键词无关。")
        hits = eng.search("needle", limit=10)
        assert len(hits) == 1, f"应命中 1 条，实际 {len(hits)}"
        assert hits[0]["title"] == "needle 在标题里"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cjk_char_tokenize():
    """中文必须逐字可搜（unicode61 不切分 CJK，索引与查询两侧都插空格）"""
    eng, d = _engine()
    try:
        eng.index("tr_1", "机器学习入门", "本文介绍梯度下降与反向传播的基本原理。")
        hits = eng.search("梯度下降", limit=10)
        assert len(hits) == 1, f"中文查询应命中，实际 {len(hits)}"
        # 关键词在摘要里被 <mark> 逐字包住、且字间有分词空格（CJK 逐字分词），
        # 故先剥标记再去空格，断言的是「用户肉眼能读到的关键词」
        plain = re.sub(r'</?mark>', '', hits[0]["snippet"]).replace(" ", "")
        assert "梯度下降" in plain, f"摘要应含中文关键词，实际 {hits[0]['snippet']!r}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cjk_title_only_hit():
    """⚠️ 只在标题里、正文里**没有**的中文词必须能搜到。

    构造：正文刻意不含「使」「用」「说」「明」中的任何一个字，于是查询拆成
    `使 AND 用 AND 说 AND 明` 时，**只有标题参与匹配**才可能成立。

    区分力（本用例存在的理由）：`title` 列若不拆字，里面是整 token「使用说明」，
    四个拆开 token 一个都匹配不上 → 0 命中，用例必红。
    ⚠️ 这条**不能改用英文**——unicode61 天然切分英文，:44 的
    `test_title_hit_still_matches` 用 `needle` 测「标题命中」一直是绿的，
    而它掩盖的正是这个中文缺陷（断言存在 ≠ 断言有效）。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "使用说明", "这是正文，里面刻意不出现那四个字。")
        hits = eng.search("使用说明", limit=10)
        assert len(hits) == 1, f"只在标题里的中文词必须能搜到，实际 {len(hits)} 条"
        assert hits[0]["title"] == "使用说明", (
            f"title 列必须仍是原文（拆字版只该并入 content 列），实际 {hits[0]['title']!r}"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_title_only_doc_indexed_on_rebuild():
    """⚠️「正文为空、只有标题」的文档也必须进索引。

    `rebuild` 的守卫曾是 `if content:` —— 按它写，这类文档**整条不进索引**
    （连标题都搜不到）。判据应是「标题与正文**都**空」才跳过。
    """
    eng, d = _engine()
    try:
        eng.rebuild([{"id": "tr_t", "title": "只有标题的文档"}], lambda _id: "")
        hits = eng.search("只有标题", limit=10)
        assert len(hits) == 1, f"正文为空的文档应仍可按标题搜到，实际 {len(hits)} 条"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_delete_removes_hit():
    """删除索引后不得留幽灵命中（删除文档时若漏调 search_engine.delete 会挂）"""
    eng, d = _engine()
    try:
        eng.index("tr_1", "标题", "正文含 needle")
        assert len(eng.search("needle", limit=10)) == 1
        eng.delete("tr_1")
        assert eng.search("needle", limit=10) == [], "删除后仍能搜到 → 幽灵命中"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reindex_replaces_not_duplicates():
    """同 id 重复索引 = 覆盖，不是追加（FTS5 无 UPDATE，靠先删后插）"""
    eng, d = _engine()
    try:
        eng.index("tr_1", "旧标题", "旧正文含 needle")
        eng.index("tr_1", "新标题", "新正文不含那个词")
        hits = eng.search("needle", limit=10)
        assert hits == [], f"旧内容应被覆盖，实际仍命中 {hits}"
        assert len(eng.search("新正文", limit=10)) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_empty_query_returns_empty():
    eng, d = _engine()
    try:
        eng.index("tr_1", "标题", "正文")
        assert eng.search("") == []
        assert eng.search("   ") == []
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
