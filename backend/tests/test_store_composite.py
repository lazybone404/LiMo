"""验证 Store 对合成文档（composite）的支持 — 抗索引重建 / 四守卫 / 去重跳过 / 损坏标记

本模块只守一件事：**来源身份在 index.json 丢失重建后仍然存在**。
重建时 id 由内容 sha256 重推、sourceType 被写死为空、非内容派生字段全丢——
只有 `transcript.md` 第一行的出处头能还原它们。

⚠️ 观测面的选择（决定了这些测试有没有区分力）：
- 要验「index 条目里有没有该字段」→ 必须用 `list_transcripts()`（它直出 index 原始条目）
- 用 `get_transcript()` 会**掩盖**守卫失效——它有「出处头优先」逻辑，会从 rawText 里
  把字段重新解析回来，于是守卫没了也照样返回正确值。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.transcript_meta import build_composite_raw_text
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

BODY = "## 综合结论\n\n两篇的共同点是……"
SOURCES = [{"title": "论文A", "status": "full"}, {"title": "论文B", "status": "summarized"}]

_TMP: list[str] = []


def _new_store() -> Store:
    _d = tempfile.mkdtemp()
    _TMP.append(_d)
    return Store(_d)


def _composite_raw() -> str:
    return build_composite_raw_text(["tr_a", "tr_b"], ["hash_a", "hash_b"], SOURCES, BODY)


def _tx(tx_id: str, raw: str, title: str = "两篇论文对比") -> RawTranscript:
    return RawTranscript(id=tx_id, title=title, source_type=SourceType.COMPOSITE,
                         engine="synthesis", raw_text=raw, content_hash="",
                         metadata=RawMetadata(), tags=[])


d = tempfile.mkdtemp()
_TMP.append(d)
try:
    s = Store(d)

    # ═══════════════════════════════════════════════════════════
    # 1. 落库 → 读回：四个来源字段 + sourceType 全部就位
    # ═══════════════════════════════════════════════════════════

    assert s.save_transcript(_tx("tr_comp1", _composite_raw())), "写入应成功"
    got = s.get_transcript("tr_comp1")
    assert got is not None
    assert got["sourceType"] == "composite", got["sourceType"]
    assert got["sourceDocIds"] == ["tr_a", "tr_b"], got["sourceDocIds"]
    assert got["sourceTitles"] == ["论文A", "论文B"], got["sourceTitles"]
    assert got["sourceHashes"] == ["hash_a", "hash_b"], got["sourceHashes"]
    assert got["metadataCorrupt"] is False
    print("PASS: 落库 → 读回，四个来源字段与 sourceType 全部就位")

    # 结论 #2：list_transcripts 直出 index 条目（不走 get_transcript），
    # 字段若不写进 `_transcript_index_entry`，前端「N 合 1」徽章就永远拿不到数据。
    row = [e for e in s.list_transcripts() if e["id"] == "tr_comp1"][0]
    assert row["sourceDocIds"] == ["tr_a", "tr_b"], (
        f"列表接口必须带 sourceDocIds（只加在 get_transcript 里的实现会红）：{row.get('sourceDocIds')}"
    )
    assert row["sourceHashes"] == ["hash_a", "hash_b"]
    print("PASS: list_transcripts 直出 index，来源字段对前端可见（结论 #2）")

    # ═══════════════════════════════════════════════════════════
    # 2. 抗索引重建 —— 本模块存在的理由
    # ═══════════════════════════════════════════════════════════
    #
    # 对照实验：不解析出处头（保持 `sourceType: ""` 的字面量）→ 重建后该文档
    # 变成一篇「来源不明的普通文档」——**而这只在删掉 index.json 之后才出现**，
    # 平时怎么用都看不出来。故必须走真实的「删文件 → 重新构造 Store」路径。

    os.remove(os.path.join(d, "index.json"))
    s2 = Store(d)
    # ⚠️ 重建**不是** Store() 构造时自动跑的——`_rebuild_index` 是显式入口，
    # 当前全仓没有生产调用点（只有测试调它）。丢了 index.json 的实际后果是
    # 「索引初始化为空、文档从列表里消失」，而不是自动重扫。
    # 这里显式调它，是为了保住「将来接上重建入口时行为正确」这条不变量。
    s2._rebuild_index()
    comp_rows = [e for e in s2.list_transcripts() if e.get("sourceType") == "composite"]
    assert comp_rows, (
        "重建后 composite 身份丢失 —— 出处头没被解析。"
        f"全部条目：{[(e.get('title'), e.get('sourceType')) for e in s2.list_transcripts()]}"
    )
    rebuilt = comp_rows[0]
    assert rebuilt["sourceDocIds"] == ["tr_a", "tr_b"], rebuilt
    assert rebuilt["sourceTitles"] == ["论文A", "论文B"], rebuilt
    assert rebuilt["sourceHashes"] == ["hash_a", "hash_b"], rebuilt
    # 重建会重推 id（sha256(raw_text)），原 id 必然失效——这是既有语义，不是缺陷
    assert rebuilt["id"] != "tr_comp1", "重建重推 id 是既有语义（故此处的断言不能依赖旧 id）"
    print(f"PASS: 删 index.json → 重建后来源身份仍在（新 id={rebuilt['id']}）")

    # 经 get_transcript 读回（此时 index 与 header 是同一份数据，仍应一致）
    got2 = s2.get_transcript(rebuilt["id"])
    assert got2["sourceType"] == "composite" and got2["sourceDocIds"] == ["tr_a", "tr_b"]
    print("PASS: 重建后经 get_transcript 读回一致")

    # ═══════════════════════════════════════════════════════════
    # 3. skip_dedup —— 合成文档不参与内容去重
    # ═══════════════════════════════════════════════════════════
    #
    # 对照实验：去掉 skip_dedup 参数、一律走去重 → 同一批源第二次合并被静默拒绝
    # （save 返回 False、库里还是旧文档），用户以为新建了一份，实际拿到陈旧快照。

    s3 = _new_store()
    raw = _composite_raw()             # 同一份文本 = 同一个 content_hash
    assert s3.save_transcript(_tx("tr_d1", raw), skip_dedup=True), "首次写入"
    assert s3.save_transcript(_tx("tr_d2", raw), skip_dedup=True), \
        "skip_dedup=True 时第二份相同内容必须写入（走去重的实现会红）"
    assert s3.count == 2, f"应有两份文档，实际 {s3.count}"
    assert not s3.save_transcript(_tx("tr_d3", raw)), \
        "不传 skip_dedup 时仍应去重（普通导入路径的行为不能变）"
    assert s3.count == 2
    print("PASS: skip_dedup=True 跳过内容去重；缺省仍去重（普通导入零影响）")

    # ═══════════════════════════════════════════════════════════
    # 4. save_transcript_full 的四守卫 —— 静默数据损坏的唯一入口
    # ═══════════════════════════════════════════════════════════
    #
    # 该函数**整体替换** index 条目。PATCH /transcripts、retranscribe、_auto_title
    # 三个调用方都不会带这四个字段 —— 漏守卫 = 改一次标题就把来源清单抹成 []，
    # 且零报错。⚠️ 观测面必须是 list_transcripts（get_transcript 会从 header 补回来，
    # 掩盖守卫失效）。

    s4 = _new_store()
    s4.save_transcript(_tx("tr_k1", _composite_raw()))
    # 模拟 PATCH /transcripts：只带要改的字段（不带来源四字段）
    assert s4.save_transcript_full({"id": "tr_k1", "title": "改过的标题", "tags": ["x"]})
    row4 = [e for e in s4.list_transcripts() if e["id"] == "tr_k1"][0]
    assert row4["sourceDocIds"] == ["tr_a", "tr_b"], (
        f"save_transcript_full 缺守卫 → 改标题把来源抹成 {row4.get('sourceDocIds')}"
    )
    assert row4["sourceTitles"] == ["论文A", "论文B"], row4
    assert row4["sourceHashes"] == ["hash_a", "hash_b"], row4
    assert row4["metadataCorrupt"] is False
    print("PASS: save_transcript_full 四守卫 —— 不带来源字段的整替换不抹掉它们")

    # 显式传入时以传入值为准（守卫不能变成「永远忽略入参」）
    assert s4.save_transcript_full({
        "id": "tr_k1", "title": "改过的标题",
        "sourceDocIds": ["tr_z"], "sourceTitles": ["新来源"], "sourceHashes": ["h"],
        "metadataCorrupt": True,
    })
    row4b = [e for e in s4.list_transcripts() if e["id"] == "tr_k1"][0]
    assert row4b["sourceDocIds"] == ["tr_z"] and row4b["metadataCorrupt"] is True, row4b
    print("PASS: 显式传入时不被守卫覆盖（守卫只管「没传」）")

    # ═══════════════════════════════════════════════════════════
    # 5. 坏头 → metadataCorrupt，且**不猜**来源
    # ═══════════════════════════════════════════════════════════

    s5 = _new_store()
    bad_raw = '<!-- limo-meta {这不是合法 JSON} -->\n\n## 来源文档\n\n1. 某篇\n\n正文'
    s5.save_transcript(RawTranscript(
        id="tr_bad", title="坏头文档", source_type=SourceType.TEXT,
        engine="", raw_text=bad_raw, content_hash="", metadata=RawMetadata(), tags=[]))
    bad = s5.get_transcript("tr_bad")
    assert bad["metadataCorrupt"] is True, "坏头必须标损坏（当作无头处理的实现会红）"
    assert bad["sourceDocIds"] == [], "损坏时不许猜来源"
    assert bad["sourceType"] == "text", "sourceType 退回调用方给的值，不猜 composite"
    print("PASS: 出处头损坏 → metadataCorrupt=True 且不猜来源（§4.2.3）")

    # ═══════════════════════════════════════════════════════════
    # 6. 无头普通文档零影响
    # ═══════════════════════════════════════════════════════════

    s6 = _new_store()
    s6.save_transcript(RawTranscript(
        id="tr_plain", title="普通文档", source_type=SourceType.WEBPAGE,
        engine="trafilatura", raw_text="就是一段普通正文，没有出处头。",
        content_hash="", metadata=RawMetadata(), tags=[]))
    plain = s6.get_transcript("tr_plain")
    assert plain["sourceType"] == "webpage" and plain["metadataCorrupt"] is False
    assert plain["sourceDocIds"] == [] and plain["sourceTitles"] == []
    print("PASS: 无头普通文档零影响（不会被误标 metadata_corrupt）")

    # 普通文档同样能抗重建
    os.remove(os.path.join(s6.root, "index.json"))
    s6b = Store(str(s6.root))
    s6b._rebuild_index()
    p_rows = [e for e in s6b.list_transcripts() if e.get("metadataCorrupt")]
    assert not p_rows, f"普通文档在重建后不得被标损坏：{p_rows}"
    print("PASS: 无头文档重建后仍不标损坏")

finally:
    for _t in _TMP:
        shutil.rmtree(_t, ignore_errors=True)

print("\n全部通过：Store 合成文档支持（抗重建 / 守卫 / 去重 / 损坏标记）")
