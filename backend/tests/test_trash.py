"""回收站：删除 → 恢复 → 清空。

判据集中在一句话上：**「删了」不能只是「从索引里摘掉」**。

- 删对了 → 目录在 `trash/{id}/doc/`、meta 在场、正文一个字节不少；
- 恢复对了 → **id 不变**、标签/文件夹/产物版本原样回来（这正是不能靠
  `_rebuild_index` 重建条目的原因：那里 id 由正文 sha256 重推，必然变）；
- 回收站坏了 → 删除**仍然成功**（回退到改造前的物理删除），但磁盘上不能留残骸。

⚠️ **文件名必须是 `test_trash.py`**：`backend/pyproject.toml` 没有
`[tool.pytest.ini_options]`，走默认 `python_files = test_*.py`。叫 `trash.py`
的话 `pytest tests/` 一次都不会执行到它 —— 写了测试但没人跑，比断言写错更贵。
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from app.core.types import (GenerateMode, MarkdownView, RawMetadata,
                            RawTranscript, SourceType)
from app.storage import store as store_mod
from app.storage.store import (TRASH_DIR_NAME, TRASH_DOC_SUBDIR,
                               TRASH_META_NAME, TRASH_RETENTION_DAYS, Store)


@pytest.fixture()
def store(tmp_path):
    # 建在 tmp_path 下而不是 tempfile.mkdtemp()：pytest 负责清理，
    # 失败时还能从 tmp 路径里翻现场。
    return Store(str(tmp_path / "lib"))


def _add(store: Store, tx_id: str, title: str, raw: str = "一段足够长的正文内容。") -> None:
    store.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=SourceType.WEBPAGE, engine="test",
        raw_text=raw, content_hash="", metadata=RawMetadata(), tags=[]),
        skip_dedup=True)


# ── 删除 ────────────────────────────────────────────────────────────────────

def test_删除把整个文档目录搬进回收站(store):
    _add(store, "tx1", "第一篇", "回收站要保住这段正文")
    doc_dir = store.get_doc_dir("tx1")
    assert doc_dir is not None and doc_dir.is_dir()

    assert store.delete_transcript("tx1") is True

    tdir = store._trash_dir("tx1")
    moved = tdir / TRASH_DOC_SUBDIR
    assert moved.is_dir(), "目录必须整体搬进 trash/{id}/doc/，不是被删掉"
    assert (moved / "transcript.md").exists()
    assert (tdir / TRASH_META_NAME).exists()
    # 正文一字不少（搬移不是重写）
    assert "回收站要保住这段正文" in (moved / "transcript.md").read_text(encoding="utf-8")
    # 原位置空了、索引里也没了
    assert not doc_dir.exists()
    assert store.get_transcript("tx1") is None


def test_meta_里存了原索引条目(store):
    _add(store, "tx1", "标题要能恢复")
    dir_name = store.get_doc_dir("tx1").name
    store.delete_transcript("tx1")

    meta = json.loads(
        (store._trash_dir("tx1") / TRASH_META_NAME).read_text(encoding="utf-8"))
    assert meta["id"] == "tx1"
    assert meta["dirName"] == dir_name
    assert meta["entry"]["title"] == "标题要能恢复"
    assert meta["deletedAt"]


def test_回收站列表按删除时间倒序且字段齐全(store):
    _add(store, "a", "先删的")
    _add(store, "b", "后删的")
    store.delete_transcript("a")
    # isoformat 只到秒，同一秒内两次删除会并列 → 手动拉开
    time.sleep(1.1)
    store.delete_transcript("b")

    items = store.list_trash()
    assert [i["id"] for i in items] == ["b", "a"]
    for it in items:
        assert it["title"] and it["deletedAt"] and it["expiresAt"]
        assert 0 <= it["daysLeft"] <= TRASH_RETENTION_DAYS


# ── 恢复 ────────────────────────────────────────────────────────────────────

def test_恢复保留_id_与标签(store):
    _add(store, "tx1", "带标签的")
    store.update_transcripts_tags(["tx1"], ["技术", "笔记"])
    store.delete_transcript("tx1")

    entry = store.restore_transcript("tx1")
    assert entry is not None and entry["id"] == "tx1", "id 变了 = 前端引用、文件夹归属全错位"

    got = store.get_transcript("tx1")
    assert got["title"] == "带标签的"
    assert got["tags"] == ["技术", "笔记"]
    assert store.get_doc_dir("tx1").is_dir()
    assert not store._trash_dir("tx1").exists(), "恢复后不留残壳"


def test_恢复把产物版本一起带回来(store):
    """`markdowns` 在 index 里是**另一批键**，删除时被 pop——不存进 meta 就恢复不回来
    （正文文件还在，但版本列表是空的，用户以为产物丢了）。"""
    _add(store, "tx1", "有产物的")
    store.save_markdown(MarkdownView(
        id="md1", transcript_id="tx1", content="# 笔记正文",
        mode=GenerateMode.NOTES))
    assert len(store.get_markdowns_by_transcript("tx1")) == 1

    store.delete_transcript("tx1")
    assert store.get_markdowns_by_transcript("tx1") == []

    assert store.restore_transcript("tx1") is not None
    versions = store.get_markdowns_by_transcript("tx1")
    assert len(versions) == 1
    assert "笔记正文" in versions[0]["content"]


def test_恢复时目录名冲突加后缀_绝不覆盖(store):
    _add(store, "tx1", "同名冲突")
    old_name = store.get_doc_dir("tx1").name
    store.delete_transcript("tx1")

    # 模拟「删掉之后又导入了一篇，正好占了同一个目录名」
    squatter = store.root / old_name
    squatter.mkdir()
    (squatter / "别人的文件.txt").write_text("别动我", encoding="utf-8")

    entry = store.restore_transcript("tx1")
    assert entry is not None
    assert entry["dir_name"] == f"{old_name}_1"
    assert (squatter / "别人的文件.txt").read_text(encoding="utf-8") == "别动我"
    assert (store.root / f"{old_name}_1" / "transcript.md").exists()


def test_恢复不存在的条目返回_None(store):
    assert store.restore_transcript("nope") is None


# ── 索引重建 ────────────────────────────────────────────────────────────────

def test_重建索引不会把回收站里的文档扫回来(store):
    """`trash` 必须在 `_rebuild_index` 的跳过名单里。

    一层扫描本来就碰不到 `trash/{id}/doc/`，这条是**防御**：哪天有人把扫描
    改成递归，已删文档会被当成正常文档重建回来，而且零报错。
    """
    _add(store, "tx1", "已删除")
    store.delete_transcript("tx1")

    store._rebuild_index()
    assert store.count == 0


def test_恢复后重建索引仍能扫到它(store):
    """⚠️ 重建会**重推 id**（`_unique_tx_id` 由正文 sha256 派生）——这是已知的、
    也是「恢复绝不能靠重建」的原因。这里只断言内容活下来了，不断言 id 不变。"""
    _add(store, "tx1", "恢复后重建", "恢复之后还要能被扫到")
    store.delete_transcript("tx1")
    store.restore_transcript("tx1")

    store._rebuild_index()
    titles = [e["title"] for e in store.list_transcripts()]
    assert titles == ["恢复后重建"]


# ── 过期清理 ────────────────────────────────────────────────────────────────

def test_过期清理只删超期项(store):
    _add(store, "old", "八天前删的")
    _add(store, "new", "刚删的")
    store.delete_transcript("old")
    store.delete_transcript("new")

    meta_path = store._trash_dir("old") / TRASH_META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_at = datetime.now() - timedelta(days=TRASH_RETENTION_DAYS + 1)
    meta["deletedAt"] = old_at.isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    store._purge_expired_trash()

    assert not store._trash_dir("old").exists()
    assert store._trash_dir("new").exists()


def test_无_meta_的目录按目录时间过期(store):
    """`_move_to_trash` 里 meta 写失败且回滚失败时，目录留在 trash 但没有 meta。

    只认 `deletedAt` 的话它会「永不过期、永不显示、永不清理」——静默占着磁盘。
    """
    orphan = store.root / TRASH_DIR_NAME / "orphan"
    orphan.mkdir(parents=True)
    old = time.time() - (TRASH_RETENTION_DAYS + 2) * 86400
    os.utime(orphan, (old, old))

    store._purge_expired_trash()
    assert not orphan.exists()


# ── 失败路径（回收站坏了不该让删除失败）────────────────────────────────────

def test_meta_写失败时回滚且不留残骸(store, monkeypatch):
    """meta 写失败 → 目录搬回原位 → 按改造前的行为物理删除 → 索引摘掉。

    ⚠️ 只断言「没报错」是不够的：真正的失效形态是「索引里没有了、磁盘上还在」，
    删除报告成功，而下一次 `_rebuild_index` 把它整篇复活。
    """
    _add(store, "tx1", "meta 写不进去")
    doc_dir = store.get_doc_dir("tx1")

    real = store_mod.atomic_write_text

    def selective(path, content, **kw):
        # 只让 trash-meta 失败；`_save_index` 走的是同一个模块级名字，
        # 全量 patch 会把索引写入也一起打掉（那样测的就不是这条路径了）。
        if TRASH_META_NAME in str(path):
            raise OSError("disk full")
        return real(path, content, **kw)

    monkeypatch.setattr(store_mod, "atomic_write_text", selective)

    assert store.delete_transcript("tx1") is True
    assert store.get_transcript("tx1") is None
    assert not store._trash_dir("tx1").exists(), "回滚成功就不该留下 trash 残壳"
    assert not doc_dir.exists(), "回滚后的目录必须被物理删掉，否则重建会复活它"


def test_移入回收站失败时仍完成删除(store, monkeypatch):
    _add(store, "tx1", "搬不动")
    doc_dir = store.get_doc_dir("tx1")

    def boom(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(store_mod.shutil, "move", boom)

    assert store.delete_transcript("tx1") is True, "回收站是便利设施，坏了不该让删除失败"
    assert store.get_transcript("tx1") is None
    assert not doc_dir.exists()
    assert not store._trash_dir("tx1").exists()


# ── 彻底删除 ────────────────────────────────────────────────────────────────

def test_彻底删除与清空(store):
    _add(store, "a", "甲")
    _add(store, "b", "乙")
    store.delete_transcript("a")
    store.delete_transcript("b")

    assert store.purge_trash("a") is True
    assert not store._trash_dir("a").exists()
    assert store.restore_transcript("a") is None, "彻底删除之后恢复不了，这是它的语义"
    assert store.purge_trash("a") is False, "重复清同一条返回 False，不抛"

    assert store.purge_all_trash() == 1
    assert store.list_trash() == []
