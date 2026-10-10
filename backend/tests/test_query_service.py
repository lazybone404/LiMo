"""查询层（`app/services/query.py`）—— 契约成形：分页 / 版本 / 截断 / ID 校验 / 自证装配

⚠️ **本模块的断言都冲着一个具体的历史事故去**，不是「跑通就算」：

| 断言 | 若实现写错会怎样 |
|---|---|
| 翻页并集 == 全集 | 少几篇文档，**不报错**——用户以为库里就这些 |
| 版本选择 == store | 拿错版本，"我要第 1 版"给第 2 版 |
| 无头产物 `meta_corrupt is False` | 全部历史产物被标损坏 |
| `truncated` 与 `continuation` 同时存在 | 静默截断，调用方以为拿全了 |
| 读路径拒 `..` **且**写路径行为不变 | 硬合三个 ID 函数（双向的坏） |
| `search_engine=None` → 报错 | 搜索变成**静默空结果**（看起来完全正常） |

⚠️ **对照实验清单（每条判据配一个「看起来对、其实错」的实现，均已实测判红，
下方「实测」是该变体真实跑出来的失败信息，不是预期）**：

| 替代实现 | 判红处 | 实测失败信息 |
|---|---|---|
| 列表按 `createdAt` **单键**排序、游标仍按 `(createdAt, id)` 比较 | 第 1 节 | 并集 != 全集（少 `tr_p01/p02/p03`）: `[qb1, qb0, p04, p00]` |
| `build_from_entries` 取 `entries[-1]`（最旧） | 第 2 节 | 最新版判定必须与 store 一致 |
| `search_engine is None` 时返回 `Page(items=[])` | 第 8 节 | 应抛 QUERY_NO_SEARCH_ENGINE，但调用正常返回了 |
| 只截断、不给 `continuation` | 第 6 节 | truncated 必须同时给出 continuation |

⚠️ **第 1 节的对照实验做过两版**，第一版**完全没有区分力**：7 篇文档里同秒项的
写入顺序恰好等于 id 降序，于是「单键排序」的错误实现照样全绿——也就是说，当时
这句「已实测判红」是**假的**（铁律 #38 的教科书形态：断言跑了，但判据挑的样本
让它无法失败）。修法是让同秒项按**既非升序也非降序**的顺序入库，并额外断言
「全量顺序 == 按 `(createdAt, id)` 降序」——后者才直接钉住排序判据本身，
而不是钉住「翻页没漏」（一个排序稳定但判据不同的实现也能满足它）。
`_write_order` 里的顺序是**测试资产**，别为了「好看」改成升序。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import cleanup_stores, import_doc, make_store  # noqa: E402

from app.core.resource_id import is_valid_resource_id  # noqa: E402
from app.core.search import SearchEngine  # noqa: E402
from app.core.transcript_meta import strip_meta  # noqa: E402
from app.core.types import GenerateMode, MarkdownView, Style  # noqa: E402
from app.generation.provenance import artifact_meta  # noqa: E402
from app.services.query import QueryError, QueryService  # noqa: E402
from app.storage.store import Store  # noqa: E402

BODY = "# 标题\n\n正文第一段。\n"


def _mv(mv_id: str, tx_id: str, content: str = BODY,
        mode: GenerateMode = GenerateMode.NOTES) -> MarkdownView:
    return MarkdownView(id=mv_id, transcript_id=tx_id, content=content,
                        style=Style.DETAILED, mode=mode)


def _expect_code(code: str, fn, *a, **kw):
    """断言调用抛 `QueryError` 且 code 匹配；返回该异常（供进一步检查 detail）。"""
    try:
        fn(*a, **kw)
    except QueryError as e:
        assert e.code == code, f"应抛 {code}，实得 {e.code}"
        return e
    raise AssertionError(f"应抛 {code}，但调用正常返回了")


# ═══════════════════════════════════════════════════════════════
# 1. 分页：游标判据必须与排序判据同源
#
# ⚠️ **造同秒并列 + 让写入顺序与 id 顺序不一致**，两者缺一不可：
#   - 无并列 → 按 `createdAt` 单键排与按 `(createdAt, id)` 排结果相同，错误实现照样绿
#   - 有并列但写入顺序恰好等于 id 降序 → 同样看不出差别（本测试第一版就栽在这，
#     写完发现「单键排序」的变体全绿——一个**没有任何区分力**的对照实验）
# 故：同一秒的 5 篇按 `p04, p00, p02, p01, p03` 写库（既非升序也非降序），
# 再叠一组不同秒的（组内同样逆序写入），跨档边界一并覆盖。
# ═══════════════════════════════════════════════════════════════

s1 = make_store()
_TS_A, _TS_B = "2026-10-01T10:00:00", "2026-10-02T10:00:00"
_write_order = [
    (_TS_A, "tr_p04"), (_TS_A, "tr_p00"), (_TS_A, "tr_p02"),
    (_TS_A, "tr_p01"), (_TS_A, "tr_p03"),
    (_TS_B, "tr_qb1"), (_TS_B, "tr_qb0"),
]
for _ts, _id in _write_order:
    import_doc(s1, _id, created_at=_ts)

# 期望顺序 = 按 `(createdAt, id)` 降序（且**不等于**写入顺序——否则又没区分力）
_all_ids = {_id for _, _id in _write_order}
assert len(_all_ids) == 7

svc1 = QueryService(s1)
seen: list[str] = []
cursor = None
for _page_no in range(20):
    page = svc1.list_resources(limit=2, cursor=cursor)
    seen.extend(e["id"] for e in page.items)
    if not page.has_more:
        break
    assert page.next_cursor, "has_more=True 必须给出游标（否则调用方无法继续）"
    cursor = page.next_cursor
else:
    raise AssertionError("翻页未收敛")

assert len(seen) == len(set(seen)), f"翻页出现重复项: {seen}"
assert set(seen) == _all_ids, f"并集 != 全集（少 {sorted(_all_ids - set(seen))}）: {seen}"
# 顺序也要与单页全量一致（游标翻页不能改变可见顺序）
full = [e["id"] for e in svc1.list_resources(limit=None).items]
assert seen == full, f"翻页顺序与全量顺序不一致:\n分页={seen}\n全量={full}"
# ⚠️ 「全量顺序 == 按 (createdAt,id) 降序」单独断言：上面几条都可能在一个
# **排序稳定但与游标判据不同源**的实现下成立（比如只按 createdAt 排、恰好并列项
# 的 index 顺序也降序）——那样它仍然是个错误的判据，只是暂时没被抓到。
assert full == [i for _, i in sorted(_write_order, reverse=True)], \
    f"全量顺序必须按 (createdAt,id) 降序: {full}"

print("PASS: 分页并集 == 全集、无重复、顺序一致（含并列 createdAt）")

# 对照：limit=None 时不该给出游标（给全了就没有下一页）
assert svc1.list_resources(limit=None).has_more is False

# ═══════════════════════════════════════════════════════════════
# 2. 版本选择：只调 store，绝不本地重算
# ═══════════════════════════════════════════════════════════════

s2 = make_store()
import_doc(s2, "tr_ver")
s2.save_markdown(_mv("md_v1", "tr_ver", "第一版正文"))
s2.save_markdown(_mv("md_v2", "tr_ver", "第二版正文"))

store_entries = s2.get_markdowns_by_transcript("tr_ver", mode="notes")
assert len(store_entries) == 2, store_entries

svc2 = QueryService(s2)
art = svc2.resolve_latest("tr_ver", "notes")
assert art is not None
assert art.id == store_entries[0]["id"], "最新版判定必须与 store 一致"
assert art.version == store_entries[0]["version"], (art.version, store_entries[0])
assert "第二版正文" in art.content, art.content

# 显式取旧版：旧版内容此刻在 `notes/v1.md` 里（版本归档布局），由 store 定位
art_v1 = svc2.get_artifact("tr_ver", "notes", version=1)
assert art_v1.version == 1, art_v1.version
assert "第一版正文" in art_v1.content, art_v1.content

# ⚠️ 不存在的版本必须报错，**不静默回退最新**——「我要第 1 版」拿到第 2 版
# 是最坏的一种成功（调用方不会发现）
_expect_code("QUERY_ARTIFACT_NOT_FOUND", svc2.get_artifact, "tr_ver", "notes", version=99)

print("PASS: 版本选择与 store 同源 + 指定版本不静默回退")

# ═══════════════════════════════════════════════════════════════
# 3. 历史无 meta 产物：不判损坏、不报错、正文逐字节原样
# ═══════════════════════════════════════════════════════════════

s3 = make_store()
import_doc(s3, "tr_nometa")
s3.save_markdown(_mv("md_nm", "tr_nometa", "# 老产物\n\n没有自证头。"))

art3 = QueryService(s3).resolve_latest("tr_nometa", "notes")
assert art3.meta is None, art3.meta
# ⚠️ 断言**布尔值**而不是「没抛错」——「无头当损坏」的实现不抛错，只是把标志写反
assert art3.meta_corrupt is False, "无头 ≠ 损坏（否则全部历史产物被标脏）"
assert art3.content == "# 老产物\n\n没有自证头。", art3.content
assert art3.raw_content == art3.content, "无头时两个正文域应当相同"

print("PASS: 无头历史产物 metaCorrupt=False、正文零改动")

# ═══════════════════════════════════════════════════════════════
# 4. meta 往返：含 `-->` 与换行的字段不会撕开注释
# ═══════════════════════════════════════════════════════════════

s4 = make_store()
import_doc(s4, "tr_rt")
_meta4 = artifact_meta(
    "notes", doc_id="tr_rt", mode="notes", model="test-model",
    params={"title": "A-->B", "intent": "第一行\n第二行"},
)
s4.save_markdown(_mv("md_rt", "tr_rt"), file_meta=_meta4)

art4 = QueryService(s4).resolve_latest("tr_rt", "notes")
head_line = art4.raw_content.split("\n", 1)[0]
assert head_line.startswith("<!-- limo-meta ") and head_line.endswith(" -->"), head_line[:100]
p = art4.meta["generated"]["params"]
assert p["title"] == "A→B", p["title"]
assert "\n" not in p["intent"], f"meta 必须单行，实得: {p['intent']!r}"
assert art4.meta_corrupt is False

print("PASS: meta 往返（`-->` 转义、换行归一），注释不被撕开")

# ═══════════════════════════════════════════════════════════════
# 5. ID 判据：读路径**拒绝**、写路径**归一化**（对偶断言，证明不是硬合）
# ═══════════════════════════════════════════════════════════════

s5 = make_store()
import_doc(s5, "tr_id")
svc5 = QueryService(s5)

for _bad in ("..", "../../etc/passwd", "tr_x/y", "", "a" * 65, "tr x", None):
    _expect_code("QUERY_INVALID_ID", svc5.get_resource, _bad)

# 合法 ID 正常通行（字符集：字母数字下划线连字符）
assert is_valid_resource_id("tr_abc-123_X")
assert svc5.get_resource("tr_id")["id"] == "tr_id"

# ⚠️ 对偶：写路径的归一化器**永不失败**，行为一字未变。
# 这条断言存在的意义是证明「只统一读路径」是有意的选择——把三者硬合成一个的话，
# 不是这里红，就是上面的拒绝断言红。
assert Store._sanitize_id("../../etc/passwd") == "passwd"
assert Store._sanitize_id("..") == "_."
assert Store._sanitize_id("") == "unknown"

print("PASS: 读路径拒非法 ID；写路径归一化行为不变（对偶，非硬合）")

# ═══════════════════════════════════════════════════════════════
# 6. 截断：truncated 与 continuation **必须同时存在**，且 offset 能无缝续读
# ═══════════════════════════════════════════════════════════════

s6 = make_store()
import_doc(s6, "tr_big")
_big = "字" * 5000
s6.save_markdown(_mv("md_big", "tr_big", _big))
svc6 = QueryService(s6)

art6 = svc6.get_artifact("tr_big", "notes", max_chars=100)
assert art6.truncated is True, "超限必须显式标记"
assert art6.continuation is not None, "truncated 必须同时给出 continuation（否则是静默截断）"
assert len(art6.content) == 100, len(art6.content)
assert art6.total_chars == 5000, art6.total_chars
assert art6.continuation["next_offset"] == 100, art6.continuation

# ⚠️ 最强的一条：分段拼回来必须**逐字节等于原文**
# （只断言「有 continuation」的话，offset 写错的实现照样绿）
rest = svc6.get_artifact("tr_big", "notes", offset=100, max_chars=10_000)
assert art6.content + rest.content == _big, "分段续读必须无缝（不重不漏）"
assert rest.truncated is False and rest.continuation is None

# 不超限时不该出现截断标记
art6b = svc6.get_artifact("tr_big", "notes", max_chars=10_000)
assert art6b.truncated is False and art6b.continuation is None

# `max_chars=None` = 要全文（导出侧依赖它，漏传会静默导出半篇）
art6c = svc6.get_artifact("tr_big", "notes", max_chars=None)
assert art6c.truncated is False and art6c.content == _big

print("PASS: 截断标记 + continuation 同时存在；offset 续读无缝（不重不漏）")

# ═══════════════════════════════════════════════════════════════
# 7. 两个正文域：content 剥头、raw_content 带头，且恒等
# ═══════════════════════════════════════════════════════════════

s7 = make_store()
import_doc(s7, "tr_meta")
s7.save_markdown(_mv("md_meta", "tr_meta"),
                 file_meta=artifact_meta("notes", doc_id="tr_meta", mode="notes",
                                         model="qwen3.8-max"))
art7 = QueryService(s7).resolve_latest("tr_meta", "notes")

assert art7.raw_content.startswith("<!-- limo-meta "), art7.raw_content[:60]
assert "limo-meta" not in art7.content, "content 必须是纯正文"
assert art7.meta is not None and art7.meta_corrupt is False, "自己写的头不该判损坏"
# 恒等式：两个域必须成对（只给一个的实现会让导出丢自证、或让每个消费方自己剥头）
assert strip_meta(art7.raw_content) == art7.content

# 版本补全：写盘时 identity.version 留空，读路径用 index 的真版本补
assert art7.meta["identity"]["version"] == art7.version, art7.meta["identity"]

print("PASS: content/raw_content 成对（strip_meta 首次进生产）、版本由读路径补全")

# ═══════════════════════════════════════════════════════════════
# 8. 搜索引擎：缺失时**显式报错**（绝不静默空结果）
# ═══════════════════════════════════════════════════════════════

s8 = make_store()
import_doc(s8, "tr_se")
svc_no_engine = QueryService(s8)  # 不传 engine
_expect_code("QUERY_NO_SEARCH_ENGINE", svc_no_engine.search_resources, "任意词")

# 有引擎时能搜到，且结果上限被显式标记
eng = SearchEngine(str(s8.root))
eng.index("tr_se", "文档 tr_se", "机器学习 算法 模型")
svc8 = QueryService(s8, eng)
hit = svc8.search_resources("机器学习")
assert [h["id"] for h in hit.items] == ["tr_se"], hit.items
assert hit.truncated is False and hit.continuation is None

for _i in range(5):
    eng.index(f"tr_se_h{_i}", f"命中标题{_i}", "共同关键词 内容")
capped = svc8.search_resources("共同关键词", limit=3)
assert len(capped.items) == 3, len(capped.items)
assert capped.truncated is True and capped.continuation is not None, "到顶必须显式标记"

# 空查询不必碰引擎（也不该报「缺引擎」——那会把参数问题伪装成配置问题）
assert QueryService(s8).search_resources("   ").items == []

print("PASS: 缺引擎显式报错（非静默空结果）；命中/到顶标记正确")

# ═══════════════════════════════════════════════════════════════
# 9. 筛选语义：folderId=""（未分类）与「不传」（不过滤）是两回事
# ═══════════════════════════════════════════════════════════════

s9 = make_store()
import_doc(s9, "tr_f1")
import_doc(s9, "tr_f2")
_f = s9.create_folder("工作")
s9.set_transcripts_folder(["tr_f1"], _f["id"])
svc9 = QueryService(s9)

assert {e["id"] for e in svc9.list_resources(limit=None).items} == {"tr_f1", "tr_f2"}
assert {e["id"] for e in svc9.list_resources(folder_id=_f["id"], limit=None).items} == {"tr_f1"}
# ⚠️ 写成 `if folder_id:` 的实现会让空串退化成「不过滤」——这条就红
assert {e["id"] for e in svc9.list_resources(folder_id="", limit=None).items} == {"tr_f2"}
assert svc9.list_folders()[0]["count"] == 1

print("PASS: folderId 空串 = 未分类（与「不传」区分）")

# ═══════════════════════════════════════════════════════════════
# 10. 单资源契约：白名单字段 + 不存在时的错误码
# ═══════════════════════════════════════════════════════════════

res = svc9.get_resource("tr_f1")
assert res["id"] == "tr_f1" and res["folderId"] == _f["id"], res["folderId"]
assert "rawText" in res and res["sourceType"] == "webpage"
# ⚠️ rawText **不剥自证头**（合成文档的 rawText 是三位一体的一部分），且保持相对路径
_expect_code("QUERY_NOT_FOUND", svc9.get_resource, "tr_nope")

print("PASS: 单资源白名单契约 + 不存在 → QUERY_NOT_FOUND")

cleanup_stores()

print("\n全部通过：查询层（分页 / 版本 / 截断 / ID 校验 / 自证装配 / 引擎缺省）")
