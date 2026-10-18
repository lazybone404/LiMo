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

        res = s.delete_folder(fid)
        assert res["moved"] == 2, f"应迁走 2 个文档，实际 {res}"
        assert res["deletedFolders"] == 1
        assert res["deletedFolderIds"] == [fid]
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


# ── 嵌套：创建 / 移动 / 计数 ──

def _parent_of(s: Store, folder_id: str) -> str:
    """规范化后的父级（`folder_tree.normalize_parents` 的口径）。"""
    return next((f["parentId"] for f in s.list_folders() if f["id"] == folder_id), "")


def _tree(s: Store) -> dict:
    """建一棵 技术 ⊃ 前端 ⊃ React（+ 兄弟 技术 ⊃ 后端）"""
    tech = s.create_folder("技术")["id"]
    fe = s.create_folder("前端", tech)["id"]
    react = s.create_folder("React", fe)["id"]
    be = s.create_folder("后端", tech)["id"]
    return {"tech": tech, "fe": fe, "react": react, "be": be}


def test_create_with_parent_and_bad_parent():
    """parent 必须存在，否则 404；不传 parentId = 根级（老前端零回归）。"""
    s, d = _new_store()
    try:
        t = _tree(s)
        assert _parent_of(s, t["fe"]) == t["tech"]
        assert _parent_of(s, t["tech"]) == "", "不传 parentId 必须是根级"

        try:
            s.create_folder("孤儿", "fd_nope")
            assert False, "父级不存在竟被接受"
        except AppError as e:
            assert e.status_code == 404, f"应为 404，实为 {e.status_code}"
        assert len(s.list_folders()) == 4, "失败的那次不该留下任何条目"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_can_move_folder():
    s, d = _new_store()
    try:
        t = _tree(s)
        moved = s.rename_folder(t["be"], parent_id="")
        assert moved["parentId"] == "", "移到根级"
        assert _parent_of(s, t["be"]) == ""

        again = s.rename_folder(t["be"], parent_id=t["fe"])
        assert again["parentId"] == t["fe"], "挂到另一个父级下"
        assert _parent_of(s, t["be"]) == t["fe"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_rejects_cycle():
    """移到自己 / 自己的后代 → 400 FOLDER_CYCLE。

    ⚠️ 少了这条，`技术` 被移到 `React` 下之后整棵子树从所有视图里消失
    （树根没了），而数据看起来完全正常——是最典型的「静默变孤儿」。
    """
    s, d = _new_store()
    try:
        t = _tree(s)
        for target in (t["tech"], t["react"]):
            try:
                s.rename_folder(t["tech"], parent_id=target)
                assert False, f"把 技术 移到 {target} 下竟被接受"
            except AppError as e:
                assert e.status_code == 400 and e.code == "FOLDER_CYCLE", \
                    f"应为 400 FOLDER_CYCLE，实为 {e.status_code} {e.code}"
        # 失败后内存里的父级一点没变（校验全部先于写入）
        assert _parent_of(s, t["tech"]) == ""
        assert _parent_of(s, t["react"]) == t["fe"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_requires_at_least_one_field():
    s, d = _new_store()
    try:
        fid = s.create_folder("甲")["id"]
        try:
            s.rename_folder(fid)
            assert False, "两项都不给应被拒"
        except AppError as e:
            assert e.status_code == 400
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_unchanged_writes_nothing():
    """前端拖一下又拖回原处不该产生一次全量 index 写。"""
    s, d = _new_store()
    try:
        t = _tree(s)
        calls = _count_saves(s)
        s.rename_folder(t["fe"], "前端", t["tech"])
        assert len(calls) == 0, f"值没变却写了盘：{len(calls)} 次"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_legacy_folder_without_parentid_is_root_and_not_rewritten():
    """零迁移：老 index 的文件夹条目没有 parentId。

    ① 读回必须是根级（否则整库夹子同时消失）；
    ② **加载不得改写磁盘**——把「读坏了还能回滚」变成默认是把脏值洗白的开始。
    """
    s, d = _new_store()
    try:
        s.create_folder("老夹")
        p = os.path.join(d, "index.json")
        data = json.loads(open(p, encoding="utf-8").read())
        for f in data["folders"]:
            f.pop("parentId", None)      # 模拟上一版写出的条目
        open(p, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
        before = open(p, encoding="utf-8").read()

        s2 = Store(d)
        folders = s2.list_folders()
        assert folders[0]["parentId"] == "", "缺 parentId 必须归化为根级"
        assert open(p, encoding="utf-8").read() == before, "加载不该改写 index.json"

        # 触碰即补字段：读回后改一次名，磁盘上就该带上 parentId
        s2.rename_folder(folders[0]["id"], "新夹")
        data2 = json.loads(open(p, encoding="utf-8").read())
        assert data2["folders"][0]["parentId"] == "", "改过之后应显式写出 parentId 键"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_count_is_direct_total_is_subtree():
    """`count` 只算直接子级（历史口径），`totalCount` 含整棵子树。

    ⚠️ 两列并存是刻意的：把它们合并成一个会让「分组头显示几篇」与
    「文件夹 chip 显示几篇」只能取一个口径，而验收对账要用**直接数**
    （Σ 各夹直接 count + 未分类 = 文档总数；用 totalCount 求和会重复计数）。
    """
    s, d = _new_store()
    try:
        t = _tree(s)
        _add_doc(s, "tr_a", "A", "h_a")
        _add_doc(s, "tr_b", "B", "h_b")
        _add_doc(s, "tr_c", "C", "h_c")
        s.set_transcripts_folder(["tr_a"], t["tech"])
        s.set_transcripts_folder(["tr_b", "tr_c"], t["react"])

        by_id = {f["id"]: f for f in s.list_folders()}
        assert by_id[t["tech"]]["count"] == 1
        assert by_id[t["tech"]]["totalCount"] == 3, "技术 = 1 直接 + React 下 2 篇"
        assert by_id[t["fe"]]["count"] == 0 and by_id[t["fe"]]["totalCount"] == 2
        assert by_id[t["react"]]["count"] == 2 and by_id[t["react"]]["totalCount"] == 2

        # 对账硬断言：直接数求和 + 未分类 = 文档总数
        direct_sum = sum(f["count"] for f in by_id.values())
        assert direct_sum + len(s.list_transcripts({"folderId": ""})) == s.count
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 嵌套：级联删除 ──

def test_delete_subtree_moves_all_levels_one_write():
    """删「技术」= 整棵子树一起删，各层文档全部回落未分类，文档一篇都不少。

    ⚠️ 判据三件缺一不可：① 文档总数不变（防级联删文档）；② **每一层**的文档都回落
    （只处理直接子级会让孙级文档留在已不存在的夹里 → 悬空 → 只能靠「全部」找到）；
    ③ `deletedFolderIds` 精确等于 {自身} ∪ 后代（前端本地回落只认它）。
    """
    s, d = _new_store()
    try:
        t = _tree(s)
        生活 = s.create_folder("生活")["id"]       # 树外的夹，必须毫发无伤
        for i, fid in enumerate([t["tech"], t["fe"], t["react"], 生活]):
            _add_doc(s, f"tr_{i}", f"文档{i}", f"h_{i}")
            s.set_transcripts_folder([f"tr_{i}"], fid)

        calls = _count_saves(s)
        res = s.delete_folder(t["tech"])
        assert len(calls) == 1, f"级联删除必须只写盘 1 次，实际 {len(calls)} 次"

        assert res["moved"] == 3, f"三层各 1 篇，应迁走 3 篇，实际 {res['moved']}"
        assert res["deletedFolders"] == 4, "技术 ⊃ {前端 ⊃ React, 后端}，共 4 行"
        assert res["deletedFolderIds"] == sorted(
            [t["tech"], t["fe"], t["react"], t["be"]])

        assert s.count == 4, "文档数必须不变——删除文件夹绝不删文档"
        assert all(s.get_transcript(f"tr_{i}")["folderId"] == "" for i in range(3)), \
            "各层文档都必须回落未分类"
        remaining = [f["id"] for f in s.list_folders()]
        assert remaining == [生活], f"只剩树外的 生活，实际 {remaining}"
        assert s.get_transcript("tr_3")["folderId"] == 生活, "树外文档不该被动到"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 嵌套：folderIds 集合筛选 ──

def test_folder_ids_filter_includes_descendants():
    s, d = _new_store()
    try:
        t = _tree(s)
        for i, fid in enumerate([t["tech"], t["fe"], t["react"], t["be"]]):
            _add_doc(s, f"tr_{i}", f"文档{i}", f"h_{i}")
            s.set_transcripts_folder([f"tr_{i}"], fid)
        _add_doc(s, "tr_unc", "未分类", "h_unc")

        scope = s.folder_scope(t["tech"])
        assert scope == {t["tech"], t["fe"], t["react"], t["be"]}, f"scope 不对：{scope}"

        got = {e["id"] for e in s.list_transcripts({"folderIds": scope})}
        assert got == {"tr_0", "tr_1", "tr_2", "tr_3"}, "含子树筛选漏掉了某一层"
        assert "tr_unc" not in got

        # 单个子夹的 scope 不含叔伯
        assert {e["id"] for e in s.list_transcripts({"folderIds": s.folder_scope(t["fe"])})} \
            == {"tr_1", "tr_2"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_folder_ids_wins_over_folder_id():
    """两者同时给时 `folderIds` 胜 —— 且这个先后**不取决于书写顺序**（elif 钉住）。

    这是刻意选的语义：`folderIds` 是更宽的判据，调用方给了它就说明想要子树口径。
    若写成两条独立 `if`，结果会随书写顺序变化而不报错——那是一个静默分支。
    """
    s, d = _new_store()
    try:
        t = _tree(s)
        _add_doc(s, "tr_a", "A", "h_a")
        _add_doc(s, "tr_b", "B", "h_b")
        s.set_transcripts_folder(["tr_a"], t["tech"])
        s.set_transcripts_folder(["tr_b"], t["react"])

        got = s.list_transcripts({"folderIds": {t["react"]}, "folderId": t["tech"]})
        assert [e["id"] for e in got] == ["tr_b"], \
            f"folderIds 应优先（只命中 React 下那篇），实际 {[e['id'] for e in got]}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── ensure_folder_path ──

def test_ensure_path_creates_chain_and_is_idempotent():
    s, d = _new_store()
    try:
        r1 = s.ensure_folder_path("技术/前端/React")
        assert len(r1["created"]) == 3, f"应新建 3 层，实际 {len(r1['created'])}"
        assert r1["segments"] == ["技术", "前端", "React"]

        calls = _count_saves(s)
        r2 = s.ensure_folder_path("技术/前端/React")
        assert r2["id"] == r1["id"], "同一条路径必须复用同一个末端夹"
        assert r2["created"] == [], "重复调用不该新建任何夹（幂等）"
        assert len(calls) == 0, "幂等调用不该写盘"

        assert len(s.list_folders()) == 3
        by_id = {f["id"]: f for f in s.list_folders()}
        assert by_id[r1["id"]]["parentId"] != "", "末端夹必须有父级（否则链条没连上）"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_path_one_write_for_whole_chain():
    """整条路径一次写盘 —— 逐段 run 的时候中途失败会留下半条链。"""
    s, d = _new_store()
    try:
        calls = _count_saves(s)
        s.ensure_folder_path("a/b/c")
        assert len(calls) == 1, f"三层应只写盘 1 次，实际 {len(calls)} 次"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_path_reuses_sibling_case_insensitively():
    """段内匹配走 path_key（大小写 + 空白折叠），不是逐字相等。"""
    s, d = _new_store()
    try:
        first = s.ensure_folder_path("Tech Notes")
        again = s.ensure_folder_path("  tech   notes ")
        assert again["id"] == first["id"], "path_key 归一后应复用"
        assert again["created"] == []
        assert len(s.list_folders()) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_path_invalid_writes_nothing():
    """任何一段非法 → AppError，且**一个字节都不写**。

    ⚠️ 这是「全有或全无」的真正判据：只看异常类型的话，一个「先建了前两层再抛错」
    的实现照样能通过，而用户看到的是「分类失败，但库里多了两个空夹」。
    """
    s, d = _new_store()
    try:
        for bad, code in [
            ("", "FOLDER_PATH_INVALID"),
            ("技术//前端", "FOLDER_PATH_INVALID"),
            ("/技术", "FOLDER_PATH_INVALID"),
            ("a/b/c/d", "FOLDER_PATH_TOO_DEEP"),
        ]:
            calls = _count_saves(s)
            before = len(s.list_folders())
            try:
                s.ensure_folder_path(bad)
                assert False, f"{bad!r} 竟被接受"
            except AppError as e:
                assert e.code == code, f"{bad!r} 应为 {code}，实为 {e.code}"
            assert len(calls) == 0, f"{bad!r} 拒绝时不该写盘"
            assert len(s.list_folders()) == before

        try:
            s.ensure_folder_path("技术", "fd_nope")
            assert False, "父级不存在竟被接受"
        except AppError as e:
            assert e.status_code == 404
        assert s.list_folders() == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_path_ambiguous_is_explicit():
    """同名夹出现在多个父级下 → 明示歧义，**绝不按数组顺序取第一个**。

    ⚠️ 静默取第一个的失效形态：文档被塞进一个用户根本没打算用的夹，产物上
    看不出任何异常——而这正是「允许模型建议嵌套路径」必然会遇到的场景。
    """
    s, d = _new_store()
    try:
        a = s.create_folder("甲")["id"]
        b = s.create_folder("乙")["id"]
        s.create_folder("杂项", a)
        s.create_folder("杂项", b)

        try:
            s.ensure_folder_path("杂项")
            assert False, "歧义路径竟被静默接受"
        except AppError as e:
            assert e.code == "FOLDER_PATH_AMBIGUOUS"
            assert e.status_code == 400
        assert len(s.list_folders()) == 4, "歧义时不该新建任何东西"

        # 同一份输入在明确的父级下就没有歧义（parentId 走单段 ensure）
        got = s.ensure_folder_path("杂项", a)
        assert got["created"] == [], "甲 下已有 杂项，应复用"
        assert got["id"] != "" and _parent_of(s, got["id"]) == a
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_path_whole_match_protects_legacy_slash_names():
    """历史数据里本来就叫「前端/后端」的夹不能被拆成两层。

    ⚠️ 这条是「整串优先命中现有夹名」唯一的存在理由：老用户当年用一个带斜杠的
    名字表达层级，现在照原样再跑一次分类，必须还是那个夹。
    """
    s, d = _new_store()
    try:
        legacy = s.create_folder("前端/后端")["id"]
        got = s.ensure_folder_path("前端/后端")
        assert got["id"] == legacy
        assert got["created"] == []
        assert len(s.list_folders()) == 1, "不该拆成 前端 + 后端"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ensure_path_with_parent_keeps_slashed_name_as_one_segment():
    """`parent_id` 非空时，本名含 `/` 的夹名**不被拆开**。

    `parse_path` 的 `segments` 在两个分支里是**两种语义**：`existing` 是整串名的一项
    （`["前端/后端"]`），`path` 才是逐层切开。`ensure_folder_path` 的 `parent_id` 分支
    跳过 `existing` 短路、直接拿这份 segments 逐段 ensure —— 于是在指定父级下建出的
    仍是**单层**的「前端/后端」夹。

    若把 `existing` 的 segments 当逐层路径再切一次，这里会变成两层
    （`前端 › 后端`），而树长得完全正常、没有任何报错。
    """
    s, d = _new_store()
    try:
        legacy = s.create_folder("前端/后端")["id"]      # 根级、名字含斜杠的历史夹
        parent = s.create_folder("资料")["id"]

        got = s.ensure_folder_path("前端/后端", parent)
        assert got["segments"] == ["前端/后端"], (
            f"existing 分支的 segments 是整串名一项，实际 {got['segments']}")
        assert len(got["created"]) == 1, "指定父级下应新建**一层**"
        assert got["created"][0]["name"] == "前端/后端", "名字不该被拆开"
        assert got["created"][0]["parentId"] == parent
        assert got["id"] != legacy, "不该复用根级的同名夹（父级不同）"
        assert len(s.list_folders()) == 3, "总共三个单层夹，不该多出「前端」这一层"

        # 幂等：同一父级下再跑一次不新建
        again = s.ensure_folder_path("前端/后端", parent)
        assert again["id"] == got["id"] and again["created"] == []
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
    """删掉 index.json 后重建：**分类与文件夹树都回来**，条目 schema 仍完整。

    ⚠️ 2026-10-18 起：文件夹归属与标签的真相在 `{doc_dir}/meta.json`、文件夹树在
    `{root}/folders.json`（见 `storage/doc_meta.py`）。此前这里断言的正是
    「分类必然丢失」—— 而那条路径不需要用户做错任何事，一次断电就够。
    """
    s, d = _new_store()
    try:
        fid = s.create_folder("论文")["id"]
        _add_doc(s, "tr_a", "文档A", "h_a")
        s.set_transcripts_folder(["tr_a"], fid)
        s.update_transcripts_tags(["tr_a"], ["重要"], mode="add")

        os.remove(os.path.join(d, "index.json"))
        s2 = Store(d)
        s2._rebuild_index()

        entries = s2.list_transcripts()
        assert len(entries) == 1, f"应重建出 1 个条目，实际 {len(entries)}"
        assert "folderId" in entries[0], "重建条目缺 folderId 键（schema 不一致）"
        assert entries[0]["folderId"] == fid, "重建后文件夹归属丢了"
        assert entries[0]["tags"] == ["重要"], "重建后标签丢了"
        assert [f["name"] for f in s2.list_folders()] == ["论文"], "重建后文件夹树丢了"
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
