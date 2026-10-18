"""index.json 损坏 / 缺失 / 旧格式时的**自愈**（`P0-03-index-corrupt-silent-empty`）。

**为什么值得一个独立文件**：`index.json` 是唯一的文档清单。它一坏，此前的行为是
`logger.warning` + **空索引** —— 磁盘上几十篇文档还在，库却空了，而**没有任何一层
会把这件事告诉用户**（前端只显示「还没有文档」）。同一个「读不出配置」的场景在
`core/config.py:338` 待遇完全不同：先 `copy2` 一份 `.bak`、再回落。这里补上同样的两半。

⚠️ 用例**双向**断言，缺一不可：
  · 损坏/缺失时文档必须回来（防「静默空库」）
  · 正常索引**原样不动**（防「无脑重建」——重建会重推 id，比不修更糟；
    分类本身自 2026-10-18 起由 sidecar 承接，见 `test_classification_survives_rebuild`）

运行：python -m pytest tests/test_store_index_recovery.py
"""
import hashlib
import json
import logging
import re

from app.core.types import RawMetadata, RawTranscript, SourceType
from app.storage import store as store_module
from app.storage.store import Store
from conftest import import_doc  # noqa: E402  (conftest 由 pytest 预先放进 sys.path)


_CORRUPT_RAW = '{"version": 3, "transcripts": [{"id": "tx_a"'


def _corrupt(root) -> str:
    """把 index.json 写成截断的 JSON，返回原文供备份比对。"""
    (root / "index.json").write_text(_CORRUPT_RAW, encoding="utf-8")
    return _CORRUPT_RAW


def _lib(tmp_path, n: int = 2) -> Store:
    """建一个含 n 篇文档的真实库。"""
    root = tmp_path / "lib"
    st = Store(str(root))
    for i in range(n):
        import_doc(st, f"tx_{i}", title=f"文档{i}")
    assert st.count == n, "装置没建成"
    return st


# ── 1. 损坏 → 备份 + 重建 ──

def test_corrupt_index_is_backed_up_and_rebuilt(tmp_path, caplog):
    st = _lib(tmp_path)
    raw = _corrupt(st.root)

    with caplog.at_level(logging.WARNING, logger="app.storage.store"):
        st2 = Store(str(st.root))

    # ① 损坏原文被留下来了 —— 重建一落盘就把它覆盖，这是**唯一**还能人工抢救
    #    分类 / 标签 / 创建时间的材料（对照 core/config.py 的 config.yaml.bak）
    bak = st.root / "index.json.bak"
    assert bak.exists(), "损坏的索引没有备份"
    assert bak.read_text(encoding="utf-8") == raw

    # ② 文档回来了（这条就是「静默空库」的反面）
    assert len(st2.list_transcripts()) == 2, "损坏后文档全没了"

    # ③ 变的是 id、不是分类：warning 要说清楚「哪些回不来、哪些从哪恢复」，
    #    用户才能判断要不要去翻备份（2026-10-18 起分类由 sidecar 承接，不再是「有损」）
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "sha256" in msg, f"warning 没说明 id 会变: {msg!r}"
    for field in ("文件夹归属", "标签", "meta.json", "folders.json"):
        assert field in msg, f"warning 没说明 {field} 的去向: {msg!r}"


def test_rebuild_is_what_recovers_the_docs(tmp_path, monkeypatch):
    """**对照实验**（模式 #38）：掐掉重建，同一个装置必须变红。

    证明上一条断言测的是「重建真的跑了」，而不是别的什么顺带让文档出现。
    """
    st = _lib(tmp_path)
    _corrupt(st.root)

    monkeypatch.setattr(Store, "_rebuild_index", lambda self, save=True: None)
    st2 = Store(str(st.root))

    assert st2.list_transcripts() == [], "对照没生效：掐掉重建后文档仍然回来了"


# ── 2. 缺失 / 旧格式 ──

def test_missing_index_is_rebuilt_without_backup(tmp_path):
    """文件被删（不是损坏）→ 照样重建；但没有原文可备份，不该凭空造一个 `.bak`。"""
    st = _lib(tmp_path, n=1)
    (st.root / "index.json").unlink()

    st2 = Store(str(st.root))

    assert len(st2.list_transcripts()) == 1
    assert not (st.root / "index.json.bak").exists(), "没有原文却造了备份"


def test_old_format_version_is_rebuilt(tmp_path):
    """v2 索引结构不兼容 → 与损坏同一条路（备份 + 重建），不是静默丢弃。"""
    st = _lib(tmp_path, n=1)
    (st.root / "index.json").write_text(
        json.dumps({"version": 2, "transcripts": []}), encoding="utf-8")

    st2 = Store(str(st.root))

    assert len(st2.list_transcripts()) == 1, "旧格式被丢掉了"
    assert (st.root / "index.json.bak").exists(), "旧格式覆盖前没有备份"


# ── 3. 反面：不该动的时候一个字都不动 ──

def test_valid_index_is_left_alone(tmp_path):
    """正常索引二次打开必须**逐字节不变**。

    这条防的是「无脑重建」——重建把 id 换成正文 sha256 重推、folderId 抹成空串，
    每启动一次分类就烂一层。它比不修更糟，且完全无声。
    """
    st = _lib(tmp_path)
    before = (st.root / "index.json").read_text(encoding="utf-8")

    Store(str(st.root))

    assert (st.root / "index.json").read_text(encoding="utf-8") == before
    assert not (st.root / "index.json.bak").exists()


def test_fresh_store_does_not_create_index(tmp_path):
    """全新用户（空目录）：不该凭空多一个空 index.json。

    磁盘上既没有索引也没有文档 —— 这不是「坏了」，是「还没开始用」。
    """
    root = tmp_path / "lib"
    root.mkdir()

    Store(str(root))

    assert not (root / "index.json").exists()


# ── 4. 文档 id 必须唯一（重建的 id 是内容派生的，同内容必撞）──
#
# 真实故障现场：磁盘上有两份**内容完全相同**的文档目录（同 URL 并发导入 / 旧的导入
# 窗口 bug 的遗留），index.json 损坏后重建 —— 旧实现按 `transcript.md` 内容派生 id，
# 同内容推出同一个 id。后果不止前端那句 `Encountered two children with the same key`：
# `delete_transcript` 的 `find` 只命中第一条，**第二条永远删不掉**（点多少次「删除」
# 它都还在）。故 id 唯一是数据层的不变量，不是渲染层的整洁问题。

def _tx(tx_id: str, title: str, raw_text: str) -> RawTranscript:
    return RawTranscript(id=tx_id, title=title, source_type=SourceType.TEXT,
                         engine="text", raw_text=raw_text, content_hash="",
                         metadata=RawMetadata(), tags=[])


def test_unique_tx_id_is_legacy_when_free_and_salted_on_collision():
    """不冲突时**逐字等于**旧行为；冲突时换一个。

    前半条是「零回归」的支点：绝大多数库没有重复目录，此时重建结果必须与改造前
    一字不差（否则升级一次、全库 id 全体漂移，历史引用全断）。
    """
    seed = "同一段正文"
    legacy = f"tr_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    assert store_module._unique_tx_id(seed, set()) == legacy, "首次尝试必须与改造前逐字相同"

    second = store_module._unique_tx_id(seed, {legacy})
    third = store_module._unique_tx_id(seed, {legacy, second})
    assert len({legacy, second, third}) == 3, "已占用时必须换一个"
    for i in (second, third):
        assert re.fullmatch(r"tr_[0-9a-f]{12}", i), i


def test_rebuild_gives_unique_ids_to_identical_dirs(tmp_path):
    """两份同内容目录 → 重建后 id 必须唯一（故障的直接复现）。"""
    st = Store(str(tmp_path / "lib"))
    body = "两份目录的正文一字不差。"
    # skip_dedup：绕过内容去重 —— 真实场景里正是内容去重**没能拦住**它们（不同 URL、
    # 或老版本还没有这道闸门），两份才都落了盘。测试装置必须复刻那个状态。
    assert st.save_transcript(_tx("tr_a", "标题甲", body), skip_dedup=True)
    assert st.save_transcript(_tx("tr_b", "标题乙", body), skip_dedup=True)
    assert st.count == 2

    (st.root / "index.json").unlink()          # 损坏/被删 → 走重建
    st2 = Store(str(st.root))

    ids = [e["id"] for e in st2.list_transcripts()]
    assert len(ids) == 2, f"两份目录都要回来：{ids}"
    assert len(set(ids)) == 2, f"同内容目录拿到了同一个 id（前端重复 key 的根因）：{ids}"


def test_rebuild_duplicate_ids_without_the_fix(tmp_path, monkeypatch):
    """**对照实验**（模式 #38）：把 id 派生换回纯内容派生，上一条必须变红。

    证明上一条测的是「唯一化真的跑了」，而不是别的什么顺带让两条 id 不同。

    ⚠️ 必须**同时**掐掉 `_dedupe_transcript_ids`（第二道网）—— 只掐第一道时它会把刚
    产生的重复当场修好，最终 id 仍然唯一，于是「对照实验」反而全绿，而结论是错的。
    两道网的存在是刻意的（重建管「将来」，dedupe 管「已有的脏索引」），但也正因此，
    验其中任何一道都必须把另一道按住。
    """
    st = Store(str(tmp_path / "lib"))
    body = "两份目录的正文一字不差。"
    st.save_transcript(_tx("tr_a", "标题甲", body), skip_dedup=True)
    st.save_transcript(_tx("tr_b", "标题乙", body), skip_dedup=True)
    (st.root / "index.json").unlink()

    monkeypatch.setattr(store_module, "_unique_tx_id",
                        lambda seed, taken: f"tr_{hashlib.sha256(seed.encode()).hexdigest()[:12]}")
    monkeypatch.setattr(Store, "_dedupe_transcript_ids", lambda self: None)
    ids = [e["id"] for e in Store(str(st.root)).list_transcripts()]

    assert len(ids) == 2 and len(set(ids)) == 1, f"对照没生效：{ids}"


def test_dedupe_repairs_already_dirty_index(tmp_path, caplog):
    """**已经落盘**的重复 id 在启动时被就地修好（改代码救不了旧数据）。"""
    st = _lib(tmp_path)
    idx_path = st.root / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    dup = idx["transcripts"][0]["id"]
    before_dirs = {e["dir_name"] for e in idx["transcripts"]}
    idx["transcripts"][1]["id"] = dup                # 手工制造重复
    idx_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.storage.store"):
        st2 = Store(str(st.root))

    rows = st2.list_transcripts()
    ids = [e["id"] for e in rows]
    assert len(ids) == 2 and len(set(ids)) == 2, f"重复 id 没修好：{ids}"
    assert dup in ids, "唯一的那条应保留原 id（只有重复的那条才重发）"
    # ⚠️ 只改 id 这一个字段：目录归属不动、条目数不变（不是重建）
    assert {e["dir_name"] for e in rows} == before_dirs, "自愈动了 dir_name"
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "重复" in msg, f"自愈没有留痕: {msg!r}"


def test_dedupe_is_idempotent_and_silent_when_clean(tmp_path, monkeypatch):
    """修一次就够：重复修完后再开不得再改；本来就干净时**一次盘都不写**。"""
    st = _lib(tmp_path)
    idx_path = st.root / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["transcripts"][1]["id"] = idx["transcripts"][0]["id"]
    idx_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")

    Store(str(st.root))                              # 第一次：修好并落盘
    fixed = idx_path.read_text(encoding="utf-8")

    writes: list[int] = []
    orig = Store._save_index
    monkeypatch.setattr(Store, "_save_index",
                        lambda self: (writes.append(1), orig(self))[1])

    Store(str(st.root))                              # 第二次：无可修

    assert idx_path.read_text(encoding="utf-8") == fixed, "第二次打开又改了索引"
    assert writes == [], f"干净的索引被重写了（每启动一次重写全库）：{writes}"


# ── 5. 分类的真相源：`meta.json` / `folders.json`（2026-10-18）──
#
# index.json 的定位一直是「缓存」，但分类此前**只**存在它里面 —— 重建只能清空。
# 于是「删一次 index.json，整理工作全部归零」，而那条路径不需要用户做错任何事。
# 现在真相住在文档目录里（跟着文档走）和库根上；重建是**读它**，不是补救。

def _doc_metas(root):
    return sorted(root.glob("*/meta.json"))


def test_classification_survives_rebuild(tmp_path):
    """删 index.json 后：文件夹树 / 归属 / 标签 / 创建时间 / 来源全部回来。

    ⚠️ 恢复主键是 **`dir_name`** 而不是文档 id —— 重建把 id 由正文重推成新值，
    按 id 恢复等于什么都对不上。这条也是 sidecar 必须住在**目录**里的原因。
    """
    st = _lib(tmp_path, n=1)
    fid = st.create_folder("论文")["id"]
    st.set_transcripts_folder(["tx_0"], fid)
    st.update_transcripts_tags(["tx_0"], ["重要", "待读"])
    before = st.get_transcript("tx_0")
    assert before["createdAt"] and before["sourceUrl"], "装置没造出可断言的字段"

    (st.root / "index.json").unlink()
    st2 = Store(str(st.root))

    entries = st2.list_transcripts()
    assert len(entries) == 1
    e = entries[0]
    assert e["folderId"] == fid, "重建后文件夹归属丢了"
    assert e["tags"] == ["重要", "待读"], "重建后标签丢了"
    assert e["createdAt"] == before["createdAt"], "重建后创建时间丢了"
    # 来源字段同样从 meta 恢复：否则一篇网页文档会变成「无来源的文本」，
    # `find_by_url` 随之失效（同一 URL 再导入一次就会多出一篇）。
    assert e["sourceType"] == before["sourceType"], "重建后来源类型丢了"
    assert e["sourceUrl"] == before["sourceUrl"], "重建后来源 URL 丢了"
    assert [f["name"] for f in st2.list_folders()] == ["论文"], "重建后文件夹树丢了"


def test_title_survives_rebuild(tmp_path):
    """标题也住 sidecar：**目录名是有损的**，不能拿它当真相。

    `_compute_dir_name` 截到 30 字、剥掉 `<>:"/\\|?*`（它是磁盘目录名，必须合法）。
    没有这条恢复路径时，「用户起了 40 字标题」或「标题里带斜杠」→ 删一次
    `index.json`（或损坏一次）→ 标题永久变成截断/改写版，而界面上看不出是被
    重建改的、用户也从没编辑过它。
    """
    long_title = "标题" * 20                    # 40 字 → 目录名只留前 30
    slash_title = "前端/后端 对照笔记"          # `/` 会被剥成「前端后端」
    st = Store(str(tmp_path / "lib"))
    import_doc(st, "tx_a", title=long_title)
    import_doc(st, "tx_b", title=slash_title)

    # ⚠️ **前提断言**：先证明目录名确实是有损的，否则这条测试是空转 ——
    # 将来放宽 `_compute_dir_name` 时它会一直绿，而绿的原因已经和标题无关。
    dirs = [p.name for p in st.root.iterdir() if p.is_dir()]
    assert not any(long_title in d for d in dirs), "前提不成立：目录名没被截断"
    assert not any("/" in d for d in dirs), "前提不成立：目录名没被剥掉斜杠"

    (st.root / "index.json").unlink()
    titles = {e["title"] for e in Store(str(st.root)).list_transcripts()}
    assert titles == {long_title, slash_title}, f"重建后标题被目录名污染了：{titles}"


def test_rebuild_without_sidecar_degrades_to_empty(tmp_path):
    """**已知边界**：升级前索引就坏了的老库，那一部分分类确实回不来。

    这条记录的是「放弃在哪」，不是期望行为 —— 也钉住「别加一条假的兜底」：
    `index.json.bak` 存的是**损坏原文**（拷它是为了给人抢救），从里面捞不出东西。
    正常升级路径是「索引还好时打开一次」，那时 `_migrate_classification_sidecars`
    已把真相写成 sidecar（见上一条）。
    """
    st = _lib(tmp_path, n=1)
    fid = st.create_folder("论文")["id"]
    st.set_transcripts_folder(["tx_0"], fid)
    (st.root / "folders.json").unlink()             # 退化成「升级前的库」
    for p in _doc_metas(st.root):
        p.unlink()

    _corrupt(st.root)
    st2 = Store(str(st.root))

    assert len(st2.list_transcripts()) == 1, "文档本身必须还在"
    assert st2.list_transcripts()[0]["folderId"] == ""
    assert st2.list_folders() == []
    assert fid not in {e["folderId"] for e in st2.list_transcripts()}


def test_corrupt_sidecars_are_healed_on_startup(tmp_path):
    """sidecar 自身损坏：启动迁移按索引重写（判据是**读不出来**，不是「文件不在」）。"""
    st = _lib(tmp_path, n=1)
    fid = st.create_folder("论文")["id"]
    st.set_transcripts_folder(["tx_0"], fid)

    (st.root / "folders.json").write_text("{半截", encoding="utf-8")
    for p in _doc_metas(st.root):
        p.write_text("{半截", encoding="utf-8")

    st2 = Store(str(st.root))          # 索引是好的 → 不重建，只走迁移

    assert [f["name"] for f in st2.list_folders()] == ["论文"]
    assert st2.list_transcripts()[0]["folderId"] == fid
    assert json.loads((st.root / "folders.json").read_text(encoding="utf-8"))["schema"] == 1
    for p in _doc_metas(st.root):
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["schema"] == 1 and data["folderId"] == fid


def test_migration_writes_sidecars_without_touching_index(tmp_path):
    """启动迁移：补写 sidecar，**一个字节都不碰 index.json**，且幂等。"""
    st = _lib(tmp_path, n=2)
    fid = st.create_folder("论文")["id"]
    st.set_transcripts_folder(["tx_0"], fid)

    (st.root / "folders.json").unlink()
    for p in _doc_metas(st.root):
        p.unlink()
    raw = (st.root / "index.json").read_bytes()

    Store(str(st.root))

    assert (st.root / "index.json").read_bytes() == raw, "迁移改动了索引文件"
    assert (st.root / "folders.json").exists()
    assert len(_doc_metas(st.root)) == 2

    mtimes = {p: p.stat().st_mtime_ns for p in _doc_metas(st.root)}
    Store(str(st.root))                       # 第二次：没有可补的
    assert {p: p.stat().st_mtime_ns for p in _doc_metas(st.root)} == mtimes, \
        "幂等性破了：每次启动都重写全库的 meta.json"


def test_fresh_store_creates_no_sidecars(tmp_path):
    """全新用户不该凭空多出 folders.json / meta.json（同 index.json 的取舍）。"""
    root = tmp_path / "lib"
    root.mkdir()

    Store(str(root))

    assert not (root / "folders.json").exists()
    assert _doc_metas(root) == []


def test_folders_sidecar_follows_every_folder_write(tmp_path):
    """`folders.json` 与内存里的树同频（建 / 改 / 删三条路径）。"""
    st = _lib(tmp_path, n=1)
    fid = st.create_folder("论文")["id"]

    def rows():
        return json.loads((st.root / "folders.json").read_text(encoding="utf-8"))["folders"]

    assert [(f["id"], f["name"]) for f in rows()] == [(fid, "论文")]

    st.rename_folder(fid, name="论文（二）")
    assert rows()[0]["name"] == "论文（二）"

    st.delete_folder(fid)
    assert rows() == [], "删光所有夹后应落一个空表（不是把文件删掉）"
