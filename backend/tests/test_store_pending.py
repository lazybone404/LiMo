"""占位条目与导入窗口（P1-3）。

缺陷：`list_transcripts` 只看「目录里有没有 `transcript.md`」，有则留、无则删。而
`_get_or_create_doc_dir` 是**先建目录 + 写占位条目**、正文直到 `save_transcript` 才落盘，
中间夹着封面下载（`timeout=15`）甚至整段视频的下载 + 转写。于是导入窗口内的任何一次
列表刷新都把这条判成「文档已删除」**永久删掉**；随后 `save_transcript` 走
`_get_or_create_doc_dir` 找不到条目 → 重建出 `{title}-{hex}_1` **第二份目录**，而 assets
早已写在旧目录 → 该文档图片**永久 404**，旧目录成孤儿（用户侧症状：导入完成后图片全裂）。

修法：条目带 `ready` 标记（`_get_or_create_doc_dir` 写 False、`save_transcript` 与
`_rebuild_index` 写 True），未就绪的**保留但不出现在列表里**，只对「非本次会话 + 超过
`PENDING_GRACE_SECONDS`」的清理。
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.storage.store import Store, PENDING_GRACE_SECONDS, _entry_age_seconds
from app.core.types import RawTranscript, SourceType, RawMetadata


def _tx(tx_id="tr_pend", title="待导入", text="正文内容", h="h_pend"):
    return RawTranscript(id=tx_id, title=title, source_type=SourceType.TEXT,
                         engine="text", raw_text=text, content_hash=h,
                         metadata=RawMetadata())


def _doc_dirs(root: Path) -> list[str]:
    """根目录下的文档目录名（排掉非文档目录）。"""
    skip = {"logs", "tmp", "files", "conversations", "cache"}
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and d.name not in skip)


def _age_entry(s: Store, tx_id: str, seconds: float):
    """把某条目的 createdAt 推到 `seconds` 秒之前（模拟「上个进程崩在导入中途」）。"""
    for e in s._index["transcripts"]:
        if e["id"] == tx_id:
            e["createdAt"] = (datetime.now() - timedelta(seconds=seconds)).isoformat()
    s._save_index()


# ══ 主判据：导入窗口内的列表刷新不得删掉占位条目 ══

def test_pending_entry_survives_list_refresh():
    root = Path(tempfile.mkdtemp())
    try:
        s = Store(str(root))
        doc_dir = s._get_or_create_doc_dir("tr_pend", "待导入")
        assert s._index["transcripts"][0]["ready"] is False, "占位条目应标为未就绪"

        # 导入窗口内的一次列表刷新（真实场景：用户随手点开文档库）
        assert s.list_transcripts() == [], "半成品条目不该出现在列表里"

        # ⭐ 但它必须还在索引里 —— 不在，save_transcript 就会重建第二份目录
        assert any(e["id"] == "tr_pend" for e in s._index["transcripts"]), \
            "占位条目被列表刷新删掉了（旧行为）→ save_transcript 将重建第二份目录"

        # 正文落盘 → 同一条目原地补全，仍指向**同一个**目录
        assert s.save_transcript(_tx())
        rows = s.list_transcripts()
        assert [e["id"] for e in rows] == ["tr_pend"], f"落盘后应出现在列表里: {rows}"
        assert rows[0]["dir_name"] == doc_dir.name
        assert rows[0]["ready"] is True
        assert _doc_dirs(root) == [doc_dir.name], \
            f"只应存在一份文档目录（出现第二份 = 图片会 404）: {_doc_dirs(root)}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_pending_entry_cleanup_needs_both_exemptions():
    """清理要**两条豁免都不过**：会话内活跃的、以及够新的，都不清。

    本用例刻意只用**同一条记录**翻两次判据 —— 环境完全相同，唯一变量是「在不在
    `_pending_ids` 里」，这样「保留」与「清理」的差异只可能来自那条豁免。
    """
    root = Path(tempfile.mkdtemp())
    try:
        s = Store(str(root))
        s._get_or_create_doc_dir("tr_pend", "待导入")
        _age_entry(s, "tr_pend", PENDING_GRACE_SECONDS + 60)   # 已经很旧

        # ① 会话内活跃 → 再旧也不清（视频下载 + 转写可以超过宽限期）
        assert s.list_transcripts() == []
        assert any(e["id"] == "tr_pend" for e in s._index["transcripts"]), \
            "本进程正在导入的占位条目被清理了（宽限期不该作用于会话内活跃项）"

        # ② 摘掉会话豁免 → 同样的记录、同样的年龄，这次必须被清理
        s._pending_ids.discard("tr_pend")
        assert s.list_transcripts() == []
        assert not any(e["id"] == "tr_pend" for e in s._index["transcripts"]), \
            "超过宽限期且非会话活跃的占位条目应被清理（否则索引里永远留着垃圾）"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_pending_entry_kept_across_processes():
    """另一进程刚写的占位条目（不在本进程 `_pending_ids`）按年龄宽限保留。"""
    root = Path(tempfile.mkdtemp())
    try:
        s = Store(str(root))
        s._get_or_create_doc_dir("tr_pend", "待导入")
        s._pending_ids.discard("tr_pend")          # 模拟「条目来自另一进程」
        assert s.list_transcripts() == []
        assert any(e["id"] == "tr_pend" for e in s._index["transcripts"]), \
            "刚创建的占位条目不该被清（另一进程可能正在导入）"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ══ 旧行为不能丢：真幽灵条目仍要清理 ══

def test_real_ghost_entry_still_cleaned():
    """就绪条目 + 正文被外力删除（真幽灵，无正文可等）→ 仍按原行为清理。"""
    root = Path(tempfile.mkdtemp())
    try:
        s = Store(str(root))
        assert s.save_transcript(_tx())
        doc_dir = s.get_doc_dir("tr_pend")
        shutil.rmtree(doc_dir)                     # 目录被外力删掉

        assert s.list_transcripts() == []
        assert not any(e["id"] == "tr_pend" for e in s._index["transcripts"]), \
            "真幽灵条目必须清理（不能因为新增了 pending 判据就整个不删）"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_legacy_entry_without_ready_key_is_ready():
    """缺 `ready` 键 = 就绪（旧 index.json 向后兼容）。

    判据写反（`entry.get("ready", False)`）的后果是**两向都坏**：历史条目全变成
    「永不清理的占位」（删了目录也不消失），且全都不出现在列表里 —— 一次升级让
    用户看不到任何老文档。
    """
    root = Path(tempfile.mkdtemp())
    try:
        s = Store(str(root))
        assert s.save_transcript(_tx())
        for e in s._index["transcripts"]:
            e.pop("ready")                          # 模拟旧版本写下的条目
        s._save_index()

        assert [e["id"] for e in s.list_transcripts()] == ["tr_pend"], \
            "缺 ready 键的旧条目必须照常出现在列表里"

        # 同一个旧条目，目录真没了 → 仍应清理
        shutil.rmtree(s.get_doc_dir("tr_pend"))
        assert s.list_transcripts() == []
        assert not s._index["transcripts"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ══ 年龄算子 ══

def test_entry_age_seconds():
    now = datetime.now()
    assert _entry_age_seconds({"createdAt": now.isoformat()}) < 5
    assert _entry_age_seconds(
        {"createdAt": (now - timedelta(hours=3)).isoformat()}) > 3600 * 2
    # 读不出来 → 视为「很久以前」（可清理）：写坏的占位记录没有自愈途径
    assert _entry_age_seconds({}) == float("inf")
    assert _entry_age_seconds({"createdAt": "not-a-date"}) == float("inf")
