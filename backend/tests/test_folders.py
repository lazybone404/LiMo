"""单层文件夹 + 批量操作 — Store 层单测（无需启动后端）。

运行：uv run python tests/test_folders.py
覆盖：CRUD / 删除不删文档 / 悬空引用自愈 / 改标题不丢分类（整条 PATCH 链路）/
      批量一次写盘 / folderId 三态过滤 / 重建后 schema 完整

⚠️ 每条都配了「为什么这个判据能抓到 bug」的说明——只断言「函数跑通了」等于没验。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.errors import AppError
from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage.store import Store

_counter = [0]


def _new_store() -> tuple[Store, str]:
    d = tempfile.mkdtemp(prefix="limo_folders_test_")
    return Store(d), d


def _add_doc(s: Store, tx_id: str, title: str, hash_: str):
    tx = RawTranscript(
        id=tx_id, title=title, source_type=SourceType.WEBPAGE,
        engine="trafilatura", raw_text=f"content of {title}",
        content_hash=hash_, metadata=RawMetadata(),
    )
    assert s.save_transcript(tx), f"保存 {tx_id} 失败"


# ── CRUD ──

def test_folder_crud_roundtrip():
    s, d = _new_store()
    try:
        a = s.create_folder("论文")
        b = s.create_folder("论文")  # 允许重名：id 才是身份
        assert a["id"] != b["id"], "同名文件夹必须拿到不同 id"
        assert a["id"].startswith("fd_")

        folders = s.list_folders()
        assert len(folders) == 2
        assert all(f["count"] == 0 for f in folders)

        renamed = s.rename_folder(a["id"], "文献")
        assert renamed and renamed["name"] == "文献"
        assert any(f["name"] == "文献" for f in s.list_folders())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_empty_name_rejected():
    """空名/纯空白必须在 store 层就被拒（路由层再挡一道）"""
    s, d = _new_store()
    try:
        for bad in ("", "   ", None):
            try:
                s.create_folder(bad)
                assert False, f"空名 {bad!r} 竟被接受"
            except AppError as e:
                assert e.status_code == 400
        fid = s.create_folder("正常")["id"]
        try:
            s.rename_folder(fid, "  ")
            assert False, "重命名为空应被拒"
        except AppError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_missing_folder_returns_none():
    s, d = _new_store()
    try:
        assert s.rename_folder("fd_nope", "x") is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 删除文件夹 ──

def test_delete_folder_moves_docs_not_deletes():
    """⚠️ 判据是「文档还在且 folderId 变成空串」，不是「文件夹没了」。

    只断言文件夹消失的话，「级联删除文档」这种严重 bug 照样能通过。
    """
    s, d = _new_store()
    try:
        fid = s.create_folder("临时")["id"]
        _add_doc(s, "tr_a", "文档A", "h_a")
        _add_doc(s, "tr_b", "文档B", "h_b")
        _add_doc(s, "tr_c", "文档C", "h_c")
        s.set_transcripts_folder(["tr_a", "tr_b"], fid)

        assert s.list_folders()[0]["count"] == 2

        moved = s.delete_folder(fid)
        assert moved == 2, f"应迁走 2 个文档，实际 {moved}"
        assert s.list_folders() == [], "文件夹应已删除"
        assert s.count == 3, "文档数必须不变——删除文件夹绝不删文档"
        assert s.get_transcript("tr_a") is not None
        assert s.get_transcript("tr_b")["folderId"] == ""
        assert s.get_transcript("tr_c")["folderId"] == ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_delete_missing_folder_returns_none():
    s, d = _new_store()
    try:
        assert s.delete_folder("fd_nope") is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dangling_folder_id_falls_back_to_uncategorized():
    """悬空引用（手工改坏 index.json）→ 读路径**归化为未分类**，不抛错、不消失。

    ⚠️ 判据说的是「落回未分类」，不是「不抛错」。原实现直接拿原值比较，该文档
    既不属于任何文件夹、也不出现在「未分类」——从**所有筛选视图**中消失
    （只剩「全部」能看见），用户会以为文档丢了。对照：去掉 known 归化后本条必红。
    """
    s, d = _new_store()
    try:
        fid = s.create_folder("真文件夹")["id"]
        _add_doc(s, "tr_a", "文档A", "h_a")
        s.set_transcripts_folder(["tr_a"], fid)

        # 直接把 index.json 改成一个不存在的 folderId
        p = os.path.join(d, "index.json")
        data = json.loads(open(p, encoding="utf-8").read())
        data["transcripts"][0]["folderId"] = "fd_ghost"
        open(p, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))

        s2 = Store(d)
        assert s2.list_folders()[0]["count"] == 0, "悬空引用不该被算进任何文件夹"
        assert [e["id"] for e in s2.list_transcripts({"folderId": ""})] == ["tr_a"], \
            "悬空引用必须落回未分类——否则该文档从所有筛选视图中消失"
        assert len(s2.list_transcripts()) == 1, "「全部」不受影响"
        assert s2.list_transcripts({"folderId": "fd_ghost"}) == [], \
            "拿悬空 id 查不该命中（文档已归化为未分类）"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 改标题不丢分类（整条 PATCH 链路）──

def test_patch_title_keeps_folder():
    """⚠️ 这是最容易丢分类的路径：PATCH /transcripts 读 get_transcript() 的 dict，
    改完字段后 save_transcript_full 整体替换 index 条目。get_transcript 少返回
    一个字段，用户改个标题就等于把文档移出文件夹。"""
    s, d = _new_store()
    try:
        fid = s.create_folder("论文")["id"]
        _add_doc(s, "tr_a", "原标题", "h_a")
        s.set_transcripts_folder(["tr_a"], fid)

        tx = s.get_transcript("tr_a")
        assert tx["folderId"] == fid, "get_transcript 必须返回 folderId"
        tx["title"] = "新标题"
        assert s.save_transcript_full(tx)

        assert s.get_transcript("tr_a")["folderId"] == fid, "改标题把分类弄丢了"
        assert s.list_folders()[0]["count"] == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_full_save_without_folder_key_keeps_original():
    """兜底路径：调用方没传 folderId 时保持原值（不是重置为空）"""
    s, d = _new_store()
    try:
        fid = s.create_folder("论文")["id"]
        _add_doc(s, "tr_a", "原标题", "h_a")
        s.set_transcripts_folder(["tr_a"], fid)

        tx = s.get_transcript("tr_a")
        del tx["folderId"]          # 模拟别的调用方构造的 data
        tx["title"] = "改过的标题"
        assert s.save_transcript_full(tx)

        assert s.get_transcript("tr_a")["folderId"] == fid, "缺字段时应保持原值"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 批量操作 ──

def _count_saves(s: Store) -> list:
    """把 _save_index 包一层计数，返回可读的调用记录列表"""
    calls = []
    orig = s._save_index

    def wrapped():
        calls.append(1)
        return orig()

    s._save_index = wrapped
    return calls


def test_batch_move_writes_index_once():
    """⚠️ 核心收益判据：N 个文档只写盘 1 次。

    自证有区分力：下面先用「逐条调用」跑一遍，确认同一个计数器确实会计到 N 次——
    否则「计数 == 1」可能只是因为计数器根本没接上。
    """
    s, d = _new_store()
    try:
        fid = s.create_folder("归档")["id"]
        ids = [f"tr_{i}" for i in range(5)]
        for i, tx_id in enumerate(ids):
            _add_doc(s, tx_id, f"文档{i}", f"h_{i}")

        # ① 自证：逐条模式 → 计数必须是 N
        per_call = _count_saves(s)
        for tx_id in ids:
            s.set_transcripts_folder([tx_id], fid)
        assert len(per_call) == len(ids), \
            f"逐条模式应写盘 {len(ids)} 次，实际 {len(per_call)}——计数器没接上，本用例没有区分力"

        # ② 批量模式 → 计数必须是 1
        s.set_transcripts_folder([], "")  # 先清空，保证下面真的发生写入
        batch_call = _count_saves(s)
        n = s.set_transcripts_folder(ids, fid)
        assert n == len(ids), f"应命中 {len(ids)} 条，实际 {n}"
        assert len(batch_call) == 1, \
            f"批量移动必须只写盘 1 次，实际 {len(batch_call)} 次（等于退化成逐条）"
        assert all(s.get_transcript(t)["folderId"] == fid for t in ids)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_batch_move_no_hit_no_write():
    """全不命中时不该产生写盘（避免空操作刷盘）"""
    s, d = _new_store()
    try:
        _add_doc(s, "tr_a", "文档", "h_a")
        calls = _count_saves(s)
        assert s.set_transcripts_folder(["tr_不存在"], "") == 0
        assert len(calls) == 0, "零命中不该写盘"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_batch_tags_add_and_replace():
    s, d = _new_store()
    try:
        _add_doc(s, "tr_a", "文档A", "h_a")
        _add_doc(s, "tr_b", "文档B", "h_b")

        assert s.update_transcripts_tags(["tr_a", "tr_b"], ["重点"]) == 2
        assert s.get_transcript("tr_a")["tags"] == ["重点"]

        # add：并入且去重
        assert s.update_transcripts_tags(["tr_a"], ["重点", "待读"]) == 1
        assert s.get_transcript("tr_a")["tags"] == ["重点", "待读"]
        assert s.get_transcript("tr_b")["tags"] == ["重点"]

        # replace：整体替换
        assert s.update_transcripts_tags(["tr_a"], ["已归档"], mode="replace") == 1
        assert s.get_transcript("tr_a")["tags"] == ["已归档"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 过滤 ──

def test_folder_filter_three_states():
    """folderId 过滤的三态：不传 = 不过滤 / 空串 = 只看未分类 / id = 该文件夹"""
    s, d = _new_store()
    try:
        fid = s.create_folder("论文")["id"]
        _add_doc(s, "tr_a", "已分类", "h_a")
        _add_doc(s, "tr_b", "未分类", "h_b")
        s.set_transcripts_folder(["tr_a"], fid)

        assert len(s.list_transcripts()) == 2, "不传键 = 不过滤"
        uncategorized = s.list_transcripts({"folderId": ""})
        assert [e["id"] for e in uncategorized] == ["tr_b"], "空串 = 只看未分类"
        inside = s.list_transcripts({"folderId": fid})
        assert [e["id"] for e in inside] == ["tr_a"], "id = 该文件夹"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 索引重建 / schema 完整性 ──

def test_rebuild_keeps_schema_complete():
    """删掉 index.json 后重建：条目必须仍带 folderId 键。

    ⚠️ 分类本身必然丢失——文件夹归属只存在于 index.json，磁盘上没有任何痕迹
    （文件夹是纯逻辑分类，不落目录）。这里断言的是 **schema 完整**：
    下游 e.get("folderId") 拿到的永远是字符串，不会是缺失键带来的意外。
    """
    s, d = _new_store()
    try:
        fid = s.create_folder("论文")["id"]
        _add_doc(s, "tr_a", "文档A", "h_a")
        s.set_transcripts_folder(["tr_a"], fid)

        os.remove(os.path.join(d, "index.json"))
        s2 = Store(d)
        s2._rebuild_index()

        entries = s2.list_transcripts()
        assert len(entries) == 1, f"应重建出 1 个条目，实际 {len(entries)}"
        assert "folderId" in entries[0], "重建条目缺 folderId 键（schema 不一致）"
        assert entries[0]["folderId"] == ""
        assert s2.list_folders() == [], "文件夹列表随 index 丢失，但不应抛错"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_legacy_index_without_folders_key():
    """老 index.json（无 folders 键）→ 自动回填空列表，不抛错"""
    s, d = _new_store()
    try:
        _add_doc(s, "tr_a", "文档A", "h_a")
        p = os.path.join(d, "index.json")
        data = json.loads(open(p, encoding="utf-8").read())
        del data["folders"]
        open(p, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))

        s2 = Store(d)
        assert s2.list_folders() == []
        assert s2.create_folder("新文件夹")["name"] == "新文件夹"
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
