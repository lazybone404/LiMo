"""全文搜索 — FTS5 索引与摘要正确性（纯函数单测，无需启动后端）。

运行：uv run python tests/test_search.py
覆盖：摘要可读性（原文形态，非分词/二字组串）/ 标题命中（含**只在标题里**的中文词）/
CJK 二字组检索 / 超长文档尾部可搜（去截断）/ 千分位数字 / 单字查询 /
rebuild 守卫（正文为空但标题非空）/ 删除后不留幽灵命中 / 结构迁移

⚠️ 本文件的判据都刻意构造成「实现写错时必失败」——见每个用例的 ⚠️ 说明。
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.search import SearchEngine, update_index
from app.generation.doc_text import search_index_text


class _FakeStore:
    """够 `search_index_text` 用的最小 store（duck typing，不碰真实磁盘）。"""

    def __init__(self, raw: str = "", notes: str | None = None):
        self._raw = raw
        self._notes = notes

    def get_transcript(self, _tid):
        return {"id": "tr_1", "title": "标题", "rawText": self._raw}

    def get_markdowns_by_transcript(self, _tid, mode=""):
        return [{"content": self._notes, "version": 1}] if self._notes else []


def _engine():
    d = tempfile.mkdtemp(prefix="limo_search_test_")
    return SearchEngine(d), d


def test_snippet_comes_from_body():
    """⚠️ 摘要必须取自正文（`body` 列，UNINDEXED），不是标题。

    构造：标题刻意**不含**关键词，只有正文含。
    - 正确 → 摘要里出现 needle
    - 取错列 → 摘要返回标题原文 '完全无关的标题'，断言失败
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "完全无关的标题", "正文中间藏着 needle 这个词，前后都是别的内容。")
        hits = eng.search("needle", limit=10)
        assert len(hits) == 1, f"应命中 1 条，实际 {len(hits)}"
        snippet = hits[0]["snippet"]
        assert "needle" in snippet, (
            f"摘要里没有关键词——说明摘要取自标题而非正文。snippet={snippet!r}"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_title_hit_still_matches():
    """标题命中同样要能搜到（回归：改列结构不能把标题搜索弄坏）"""
    eng, d = _engine()
    try:
        eng.index("tr_1", "needle 在标题里", "正文与关键词无关。")
        hits = eng.search("needle", limit=10)
        assert len(hits) == 1, f"应命中 1 条，实际 {len(hits)}"
        assert hits[0]["title"] == "needle 在标题里"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cjk_bigram_tokenize():
    """中文连续词必须可搜（CJK 二字组，2026-10-26 由逐字改来）。

    ⚠️ 这条**不能只断言「搜到了」**：逐字 AND（改造前）也能搜到，只是排在后面。
    真正的判据在 `test_snippet_is_readable_text`（摘要形态）和
    `test_long_doc_tail_searchable`（截断）——这里只守住「没把中文搜索弄坏」。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "机器学习入门", "本文介绍梯度下降与反向传播的基本原理。")
        hits = eng.search("梯度下降", limit=10)
        assert len(hits) == 1, f"中文查询应命中，实际 {len(hits)}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_snippet_is_readable_text():
    """⚠️ 摘要必须是**人类可读原文**——不含 CJK 分词空格、不含二字组重叠。

    改造前摘要取自逐字分词后的索引文本（`梯 度 下 降`）；若改成直接取二字组
    索引则更糟（`梯度 度下 下降`）。`body` 列（UNINDEXED，只存原文）存在的
    唯一理由就是这一条 —— 检索的 token 化不应泄漏到展示层。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "笔记", "本文介绍梯度下降与反向传播的基本原理。")
        snip = eng.search("梯度下降", limit=10)[0]["snippet"]
        plain = re.sub(r"</?mark>", "", snip)
        assert "梯度下降" in plain, f"摘要应含连续中文，实际 {snip!r}"
        # ⚠️ 判据是「汉字之间夹着空白」，不是「不含某个二字组」——后者会误伤，
        # 因为「梯度下降」本身就含子串「度下」（第一版断言正是这么写错的）。
        assert not re.search(r"[一-鿿]\s+[一-鿿]", plain), (
            f"摘要里汉字之间不该有空格（那是分词/二字组串的形态），实际 {snip!r}"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_long_doc_tail_searchable():
    """⚠️ 超过 5 万字符的文档，**尾部**内容也必须可搜。

    构造：前 60000 字符是填充，needle 只在末尾。
    - 有 `text[:50000]` 截断（改造前）→ 尾部从未进索引 → 0 命中
    - 无截断 → 命中

    区分力来自「needle 在截断线之后」——放在前面的话，截断版同样是绿的，
    这条用例就等于没测（实测踩过：真实库里 18% 的文档尾部根本不可搜）。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "长文档", "填" * 60000 + " 尾部有一个 needle 在这里。")
        hits = eng.search("needle", limit=10)
        assert len(hits) == 1, f"截断线之后的正文必须可搜，实际 {len(hits)} 条"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_thousand_separator_normalized():
    """⚠️ 正文 `1,048` 与查询 `1048` 必须能对上。

    unicode61 把 `1,048` 切成 `1` + `048`，用户搜 `1048` 命不中 ——
    这是字面与向量**两路都失手**的唯一一题。修法在清洗侧去千分位，
    与检索方式无关（所以不能靠改分词/加向量解决）。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "并发", "单台机器最多能塞下 1,048 个容器实例。")
        hits = eng.search("1048", limit=10)
        assert len(hits) == 1, f"千分位数字应可搜，实际 {len(hits)} 条"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_long_doc_middle_searchable():
    """⚠️ 长文档**中段**的专有名词必须可搜 —— 索引侧不许对 token 数量封顶。

    构造：中段塞一个英文专有名词，前后都是与它无关的中文填充。填充足够长，
    让 needle 落在第 64 个 token 之后。

    - 索引侧 `uniq[:64]`（改造前）→ 只索引开头几十个二字组 → 0 命中，必红
    - 正确 → 命中

    区分力来自「needle 在 64 个 token 之后」：短文本用例（本文件其他所有用例）
    在截断版下**全是绿的**，这正是它上线时没被任何测试拦住的原因。
    """
    eng, d = _engine()
    try:
        filler = "".join(f"填{i}充" for i in range(200))   # 约 600 字 → 数百个二字组
        eng.index("tr_1", "长文档", f"开头一段无关内容。{filler} 中段有 Matt Pocock 这个名字。{filler}")
        hits = eng.search("Matt Pocock", limit=10)
        assert len(hits) == 1, (
            f"长文档中段的专有名词必须可搜（索引侧不得对 token 封顶），实际 {len(hits)} 条"
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_index_text_includes_notes_and_strips_protocol():
    """`search_index_text` = rawText + **笔记正文**，且笔记里的协议层必须剥掉。

    ⚠️ 两件事各自都有失效形态，且都**不报错**：
    - 不含笔记 → 用户搜他在笔记里写下的那个词，搜不到（实测 66% vs 83%）
    - 不剥协议 → `limo-block` / `limo-meta` 变成可搜噪音，搜这几个词会命中所有
      带标记的文档（而它们本来一个都不是用户要找的）
    """
    raw = "这是视频转录的逐字稿。"
    notes = (
        '<!-- limo-meta {"v":1} -->\n'
        "正文里提到投机解码。\n"
        '<!-- limo-block:v1 type="key-takeaway" -->\n'
        "重点：解码可以并行。\n"
        "<!-- /limo-block -->"
    )
    text = search_index_text(_FakeStore(raw, notes), "tr_1")
    assert raw in text, "rawText 必须进索引"
    assert "投机解码" in text, "笔记正文必须进索引（这是 17pp 收益的来源）"
    assert "解码可以并行" in text, "块**内**的文字要保留（剥的是标记行，不是内容）"
    assert "limo-meta" not in text and "limo-block" not in text, (
        f"协议标记不该进索引（会变成可搜噪音），实际 {text!r}"
    )


def test_index_text_without_notes_is_raw():
    """没有笔记时逐字等于 rawText —— 摄入那一刻就是这个状态（笔记还没生成）。"""
    assert search_index_text(_FakeStore("只有原文", None), "tr_1") == "只有原文"


def test_update_index_writes_notes_content():
    """`update_index` 必须把笔记内容真的写进索引（端到端一条）。

    ⚠️ 这条钉的是「五个写点共用同一份判据」的**接线**部分：`update_index` 若退回
    只写 rawText（或漏了 `search_index_text` 直接用 tx["rawText"]），四个增量写点
    全部失效，而它们的调用方都只看到一个静默成功的函数调用。
    """
    eng, d = _engine()
    try:
        store = _FakeStore("原文里没有那个词", "笔记里写了 EnGram 这个东西")
        update_index(eng, store, "tr_1")
        hits = eng.search("EnGram", limit=10)
        assert [h["id"] for h in hits] == ["tr_1"], "笔记内容应可搜到"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_single_cjk_char_query():
    """单个汉字查询也必须能搜到。

    二字组对长度为 1 的中文串切不出 token —— 若不补单字分支，`_tokenize` 返回空表，
    `search()` 直接返回空，看起来像「库里没有这篇」（静默失效）。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "笔记", "光 是什么")
        assert len(eng.search("光", limit=10)) == 1, "单字查询不应返回空"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cjk_title_only_hit():
    """⚠️ 只在标题里、正文里**没有**的中文词必须能搜到。

    构造：正文刻意不含「使」「用」「说」「明」中的任何一个字，于是查询
    只能靠标题参与匹配才成立。

    区分力（本用例存在的理由）：标题若不参与 `toks`，里面是整 token「使用说明」，
    二字组 `使用/用说/说明` 一个都匹配不上 → 0 命中，用例必红。
    ⚠️ 这条**不能改用英文**——unicode61 天然切分英文，
    `test_title_hit_still_matches` 用 `needle` 测「标题命中」一直是绿的，
    而它掩盖的正是这个中文缺陷（断言存在 ≠ 断言有效）。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "使用说明", "这是正文，里面刻意不出现那四个字。")
        hits = eng.search("使用说明", limit=10)
        assert len(hits) == 1, f"只在标题里的中文词必须能搜到，实际 {len(hits)} 条"
        assert hits[0]["title"] == "使用说明", (
            f"title 列必须仍是原文（分词版只该进 toks），实际 {hits[0]['title']!r}"
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
        # 纯符号查询：_tokenize 返回空表 → 必须显式返回空，
        # 不能把空 MATCH 串送进 SQL（FTS5 会抛 syntax error 并走进 LIKE 回退）
        assert eng.search("!!!") == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_schema_migration_drops_old_table():
    """⚠️ 旧结构（单字时代 `id/title/content`）必须被识别并重建。

    判据是 `is_schema_current()` 读 `sqlite_master` 的 DDL 原文；若写成
    「表存在就算当前」，旧库会带着旧结构进 `index()`，首次 INSERT 才报
    `no column named toks` —— 崩在别处，且启动时看起来一切正常。

    ⚠️ 迁移**只能由应用启动路径调**（`main.py`）：`limo_cli --local` 也构造
    SearchEngine，DROP 完它手上没有 content getter，索引就再也补不回来。
    """
    d = tempfile.mkdtemp(prefix="limo_search_test_")
    try:
        conn = sqlite3.connect(str(Path(d) / "search.db"))
        conn.execute(
            "CREATE VIRTUAL TABLE transcripts_fts "
            "USING fts5(id, title, content, tokenize='unicode61')"
        )
        conn.execute("INSERT INTO transcripts_fts VALUES ('tr_old', '旧标题', '旧 正 文')")
        conn.commit()
        conn.close()

        eng = SearchEngine(d)
        assert not eng.is_schema_current(), "旧结构应被识别为需要迁移"
        assert eng.migrate_schema() is True, "应执行迁移"
        assert eng.is_schema_current(), "迁移后应是新结构"
        assert eng.migrate_schema() is False, "已是新结构时不应重复迁移（幂等）"
        assert eng.search("旧正文", limit=10) == [], "迁移即丢弃，数据由 rebuild 补回"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 健康状态 / 自愈 / 手动重建（2026-10-26） ──
#
# 这一组的共同前提：**坏掉的索引不该炸启动**。文档全在磁盘上，没有检索只是一个
# 能力缺失 —— 但「缺了」必须能被看见，否则用户看到的永远是「搜不到」。

def _freelist_pages(db_path) -> int:
    """`search.db` 里空闲页的数量（VACUUM 的**精确**判据）。

    ⚠️ 不用「文件大小变小」当判据：页大小、WAL 残留、平台差异都会影响字节数，
    断言阈值只能写成「大概」，而一个大概的断言在实现退化时也照样可能绿。
    `freelist_count` 是 SQLite 自己数的「可回收页数」，VACUUM 后必为 0。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        conn.close()


def test_status_reports_counts():
    """`status()` 给出篇数 / 体积 / 可用性，且**不抛错**（设置页直接渲染它）。"""
    eng, d = _engine()
    try:
        eng.index("tr_1", "标题一", "正文一")
        eng.index("tr_2", "标题二", "正文二")
        st = eng.status()
        assert st["available"] is True
        assert st["docCount"] == 2, f"篇数应 2，实际 {st['docCount']}"
        assert st["sizeBytes"] > 0, "体积应大于 0（文件已落盘）"
        assert st["lastError"] is None
        assert st["rebuiltAt"] is None, "还没 rebuild 过，时间应为空（不是编一个时间）"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_status_read_failure_does_not_poison_available():
    """`status()` 自己读失败 → 只写进快照，**不把引擎判成不可用**。

    缺陷形状（第四轮审查 P1-02）：`status()` 的 `except` 里写 `self._last_error`，
    而 `available` 的定义就是 `_last_error is None` —— 一次瞬时 I/O 失败
    （库被别的进程占着、磁盘抖动）会让全文检索**永久** 503：
    `QueryService.search_resources` 一进来就抛 `index_unavailable`。
    用户看到的只是「搜索坏了」，唯一出路是手动重建索引。

    对照实验：把那行改回 `self._last_error = ...` → 本条红（`eng.available` 变 False）。
    """
    eng, d = _engine()
    try:
        eng.index("tr_1", "标题一", "正文一")

        def _boom():
            raise sqlite3.OperationalError("disk I/O error")

        original = eng._conn
        eng._conn = _boom
        try:
            st = eng.status()
        finally:
            eng._conn = original

        assert st["docCount"] == 0, "读不到就给 0（状态里少个数，不是引擎坏了）"
        assert st["statusError"] and "disk I/O error" in st["statusError"], \
            f"失败原因要能带出去给诊断看：{st}"
        # ★ 核心断言：可用性不受影响
        assert st["available"] is True, f"一次读失败不该让索引变不可用：{st}"
        assert eng.available is True, f"实例状态也不能被毒化：last_error={eng.last_error!r}"
        assert eng.last_error is None

        # 恢复后一切照旧（证明这只是「这一次没读到」，不是持续状态）
        st2 = eng.status()
        assert st2["docCount"] == 1 and st2["statusError"] is None, st2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_corrupt_db_does_not_crash_construction():
    """⚠️ 索引文件损坏时构造**不抛**，而是标记不可用。

    改造前 `SearchEngine(...)` 在 `main.py` 的 try **之外**：一个坏掉的
    `search.db`（磁盘写坏、被别的程序占用、手工清了一半）会让整个 FastAPI
    startup 崩 —— 用户的知识库**打不开**，而所有文档其实好好躺在磁盘上。

    构造方式：往 `search.db` 写垃圾字节。`sqlite3.connect` 是惰性的，只有真正
    执行 SQL 才会报 `file is not a database` —— 所以这条也顺带钉住「构造期真的
    碰了库」（写成纯惰性构造的话，异常要到第一次搜索才炸，那时在请求里）。
    """
    d = tempfile.mkdtemp(prefix="limo_search_test_")
    try:
        Path(d, "search.db").write_bytes(b"this is definitely not a sqlite file" * 100)
        eng = SearchEngine(d)                      # ← 不许抛
        assert eng.available is False, "损坏的库应标记为不可用"
        assert eng.last_error and "索引库打不开" in eng.last_error, (
            f"应记录原因，实际 {eng.last_error!r}"
        )
        # 不可用时三个入口都短路，**不抛**（写入端抛错会让「导入文档」连带失败，
        # 那是把一个可修复的检索问题升级成数据写入问题）
        assert eng.search("任何词", limit=10) == [], "不可用时搜索返回空而不是抛错"
        eng.index("tr_1", "标题", "正文")            # 不抛即可
        eng.delete("tr_1")                          # 不抛即可
        st = eng.status()
        assert st["available"] is False and st["docCount"] == 0
        assert st["lastError"], "状态里必须带原因，否则界面上只有『不可用』三个字"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rebuild_recovers_from_corrupt_db():
    """重建能把坏掉的库修回来（删文件 → 重新建表 → 重灌）。

    ⚠️ 这是「用户在设置页点一下重建」的后端语义。**只有 rebuild 会自愈**：
    `index()` 走的是短路分支（它没有「从头灌一遍」的能力），所以「重建」按钮
    是唯一的出路 —— 这条正是把那个按钮接上的依据。
    """
    d = tempfile.mkdtemp(prefix="limo_search_test_")
    try:
        Path(d, "search.db").write_bytes(b"garbage" * 500)
        eng = SearchEngine(d)
        assert eng.available is False

        eng.rebuild([{"id": "tr_1", "title": "恢复后的标题"}],
                    lambda _id: "恢复后的正文有 needle 这个词")
        assert eng.available is True, f"重建后应恢复可用，实际 {eng.last_error!r}"
        assert len(eng.search("needle", limit=10)) == 1, "重建后的内容必须可搜"
        assert eng.status()["rebuiltAt"], "重建成功要记下时间（设置页显示『上次重建于』）"

        # 坏文件真的被换掉了（不是「在垃圾文件上又建了一层」）。
        # ⚠️ `-wal` / `-shm` 一并删掉才成立：只删主文件的话，SQLite 打开时会把
        # 旧 WAL 拿来回放，可能把刚重建的库再弄坏一次。
        head = Path(d, "search.db").read_bytes()[:16]
        assert head == b"SQLite format 3\x00", f"重建后应是真的 SQLite 文件，头部是 {head!r}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_vacuum_reclaims_pages_only_on_demand():
    """⚠️ `vacuum=True` 才回收空间，默认（启动路径）不回收。

    实测背景：一个 137 行 / 14.7MB 的 `search.db` 里 **23% 是空闲页** ——
    `auto_vacuum=0` 且从不 VACUUM，删掉的正文一直躺在文件里（索引里的正文是
    近全文副本，删一篇就留一大块）。所以「用户主动重建」必须带上回收。

    两段断言，缺一不可：
    - 不传 vacuum → freelist 仍 > 0（否则「只有主动重建才回收」这个区别不存在）
    - 传 vacuum    → freelist 归 0
    """
    eng, d = _engine()
    try:
        big = "填充内容" * 20000
        eng.index("tr_1", "大文档", big)
        db = Path(d, "search.db")
        assert _freelist_pages(db) == 0, "全新库不该有空闲页"

        eng.rebuild([], lambda _id: "")                    # 默认：不 VACUUM
        assert _freelist_pages(db) > 0, (
            "删空后应留下空闲页（这是 auto_vacuum=0 的既有行为）"
        )

        eng.rebuild([], lambda _id: "", vacuum=True)       # 主动重建：回收
        assert _freelist_pages(db) == 0, "vacuum=True 后空闲页应归零"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_query_service_raises_when_index_unavailable():
    """⚠️ 索引不可用时 `QueryService` 抛**显式错误**，不是返回空结果。

    这是整组的落点：「搜不到」与「索引坏了」必须能分开。此前这条路径上
    `search()` 短路返回 `[]`，于是 CLI 的 `limo search` 会 exit 0 + 空结果，
    前端显示「没有匹配」—— 三条路都指向「库里没有这个词」，全都是谎话。

    ⚠️ 错误码与「缺引擎」**必须不同**（`QUERY_INDEX_UNAVAILABLE` vs
    `QUERY_NO_SEARCH_ENGINE`）：前者用户点重建能修（recoverable），后者是
    代码构造时机错了。合并成一条等于让用户去查一个他并不拥有的初始化顺序。
    """
    from app.services.query import QueryError, QueryService

    d = tempfile.mkdtemp(prefix="limo_search_test_")
    try:
        Path(d, "search.db").write_bytes(b"garbage" * 500)
        eng = SearchEngine(d)
        svc = QueryService(None, eng)                      # store 在这条路径上用不到

        # 空查询仍然先返回：它根本不碰 FTS，把参数问题说成配置问题是误导
        assert svc.search_resources("").items == []

        try:
            svc.search_resources("任意词")
        except QueryError as e:
            assert e.code == "QUERY_INDEX_UNAVAILABLE", f"错误码不对：{e.code}"
            assert e.recoverable is True, "这条是用户能自助修的，必须标 recoverable"
            assert "重建" in (e.detail or ""), "detail 要给出出路（设置页在哪）"
        else:
            raise AssertionError("索引不可用时必须抛错，不能静默返回空结果")

        # 对照：引擎整个不在场时是**另一个**错误码
        try:
            QueryService(None, None).search_resources("任意词")
        except QueryError as e:
            assert e.code == "QUERY_NO_SEARCH_ENGINE", f"错误码不对：{e.code}"
        else:
            raise AssertionError("缺引擎时必须抛错")
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
