"""遗留「两版制」布局 → 版本归档布局的**一次性迁移**（幂等、无损）。

```
{doc}/{mode}.md        （旧「最新」）  ┐
{doc}/{mode}.prev.md   （旧「上一版」）┘ → {mode}/v2.md + {mode}/v1.md
只有 {doc}/{mode}.md、没有 prev        → {mode}/v1.md
```

⚠️ 这是**唯一破坏性动作**（会改用户磁盘上已有文件的路径），所以这里的判据重点不是
「迁移成功」，而是三条**不出事**的保证：

| 保证 | 若破会怎样 | 本文件的用例 |
|---|---|---|
| 内容**逐字节**不变 | 「顺便整理内容」→ `generatedAt` 与正文的对应关系断掉，且再也无法验证无损 | 1 / 2 |
| **幂等**（跑两次 = 跑一次） | 每次启动都把最新版又往后接一号 → 版本列表无限膨胀 | 3 |
| 顺序对（prev 拿到**更小**的版本号） | 「上一版」变最新版，静默的历史错序 | 1 |

运行：python -m pytest tests/test_store_legacy_migration.py
"""
import json
import logging

from conftest import import_doc, make_store  # noqa: E402

from app.core.transcript_meta import dump_meta  # noqa: E402
from app.generation.provenance import artifact_meta  # noqa: E402
from app.storage.store import Store, markdown_version_path  # noqa: E402

TX = "tr_legacy"


def _artifact(text: str, when_model: str) -> str:
    head = artifact_meta("notes", doc_id=TX, mode="notes", model=when_model,
                         params={"style": "detailed"})
    return f"{dump_meta(head)}\n\n{text}"


LATEST = _artifact("最新一版正文", "model-latest")
PREV = _artifact("上一版正文", "model-prev")


def _legacy_lib(*, with_prev: bool = True):
    """建一个含遗留布局的库（index 已存在 —— 正常用户的现状）。"""
    st = make_store()
    import_doc(st, TX)
    d = st.get_doc_dir(TX)
    (d / "notes.md").write_text(LATEST, encoding="utf-8")
    if with_prev:
        (d / "notes.prev.md").write_text(PREV, encoding="utf-8")
    return st, d


def _mode_files(d, mode: str = "notes") -> dict[str, str]:
    """`{文件名: 内容}`，只列版本文件（不含其它杂项）。"""
    md = d / mode
    if not md.is_dir():
        return {}
    return {p.name: p.read_text(encoding="utf-8") for p in md.iterdir() if p.is_file()}


# ── 1. prev → v1、latest → v2，内容逐字节相等 ──

def test_prev_becomes_v1_latest_becomes_v2():
    st, d = _legacy_lib()

    st2 = Store(str(st.root))

    files = _mode_files(d)
    assert sorted(files) == ["v1.md", "v2.md"], f"迁移结果不对: {sorted(files)}"
    # ⚠️ 逐字节 —— 迁移只允许 `os.replace` 改名
    assert files["v1.md"] == PREV, "v1 的内容不是「上一版」"
    assert files["v2.md"] == LATEST, "v2 的内容不是「最新版」"
    # 顶层遗留文件已消失（留着的话下次启动会被当成「半途状态」再接一号）
    assert not (d / "notes.md").exists() and not (d / "notes.prev.md").exists()

    # 版本序不能反：v2 > v1，且内容各归各位
    entries = {e["version"]: e for e in st2.list_markdown_entries(
        st2.list_transcripts()[0]["id"])}
    assert sorted(entries) == [1, 2], sorted(entries)


def test_latest_only_becomes_v1():
    """**只有 `{mode}.md`、没有 prev** 是正常输入：旧实现在 `os.replace` 失败时
    静默吞异常，磁盘上就可能只剩最新那份。如实映射为 v1，**不伪造**一个更早的版本。

    反面：假设「必有 prev」而直接索引 `[1]` → 这里会 IndexError / 少一个文件。
    """
    st, d = _legacy_lib(with_prev=False)

    Store(str(st.root))

    files = _mode_files(d)
    assert sorted(files) == ["v1.md"], f"应只有 v1: {sorted(files)}"
    assert files["v1.md"] == LATEST


# ── 2. 幂等 ──

def test_migration_is_idempotent():
    """连开两次，文件集合与内容**一字不变**。

    反面（守卫失效）：第二次启动把 `v2.md` 又当成遗留文件接成 `v3.md`，或者每次
    启动版本号 +1 —— 用户的版本列表会凭空长出一串重复项。
    """
    st, d = _legacy_lib()

    Store(str(st.root))
    first = _mode_files(d)

    Store(str(st.root))
    second = _mode_files(d)

    assert first == second, f"第二次启动又动了一次: {sorted(first)} → {sorted(second)}"
    assert sorted(second) == ["v1.md", "v2.md"]


def test_no_migration_log_when_nothing_to_do(caplog):
    """没有遗留文件时**不该打迁移日志**（也不该重建索引）。

    这条防的是「无条件重建」：迁移代码若把「没得迁」也当成功，每次启动都会重扫
    全库并重写 index.json —— 分类/标签的丢失风险就此平白多了一倍频率。
    """
    st = make_store()
    import_doc(st, TX)

    with caplog.at_level(logging.WARNING, logger="app.storage.store"):
        Store(str(st.root))

    assert not [r for r in caplog.records if "布局已迁移" in r.getMessage()], \
        "没有遗留文件却报了迁移"


# ── 3. 半途状态自愈 ──

def test_partial_state_appends_after_existing_versions(caplog):
    """「目录里已有 `v*`、顶层却还留着 `{mode}.md`」= 上次崩在半途。

    接在**最大版本号之后**，并留一条 warning —— 迁移这种事不该悄悄发生。
    """
    st = make_store()
    import_doc(st, TX)
    d = st.get_doc_dir(TX)
    (d / "notes").mkdir(parents=True, exist_ok=True)
    (markdown_version_path(d, "notes", 1)).write_text(PREV, encoding="utf-8")
    (d / "notes.md").write_text(LATEST, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.storage.store"):
        Store(str(st.root))

    files = _mode_files(d)
    assert sorted(files) == ["v1.md", "v2.md"], f"半途状态没接上: {sorted(files)}"
    assert files["v1.md"] == PREV and files["v2.md"] == LATEST, "接号后内容错位"
    assert any("布局已迁移" in r.getMessage() for r in caplog.records), "没留迁移痕迹"


# ── 4. 两个 mode 各迁各的 ──

def test_both_modes_are_migrated_independently():
    """notes 与 polished 的版本序互不影响（同一个文件里两个 mode 各迁各的）。"""
    st = make_store()
    import_doc(st, TX)
    d = st.get_doc_dir(TX)
    (d / "notes.md").write_text(LATEST, encoding="utf-8")
    (d / "notes.prev.md").write_text(PREV, encoding="utf-8")
    (d / "polished.md").write_text("整理版正文", encoding="utf-8")

    Store(str(st.root))

    assert sorted(_mode_files(d, "notes")) == ["v1.md", "v2.md"]
    assert sorted(_mode_files(d, "polished")) == ["v1.md"]
    assert _mode_files(d, "polished")["v1.md"] == "整理版正文"


# ── 5. 元数据与索引的幸存（P0 回归守卫，2026-10-10 补）──
#
# ⚠️ 上面 1-4 条**全绿也拦不住 P0**：它们只看磁盘上的文件。而修复前的迁移结尾调的
# 是 `_rebuild_index(save=True)`（**全库有损重建**，docstring 自述会清空索引），
# 症状是「文件都在、文档还在列表里，只有分类和标签凭空没了」——完全静默。

def _doc_meta(st, tx_id: str) -> dict:
    """文档级元数据里**只活在 index.json** 的那几项（磁盘无从还原）。"""
    e = st.get_transcript(tx_id)
    return {k: e.get(k) for k in ("id", "tags", "folderId", "sourceUrl", "createdAt")}


def test_document_metadata_survives_migration():
    """迁移**只动文件路径**，不碰文档级元数据。

    反面：结尾换回 `self._rebuild_index(save=True)` → 这条红（folders 空、tags 空、
    folderId 空、id 被 `sha256(正文)[:12]` 重推）。升级用户的**首次启动**必现。
    """
    st, _d = _legacy_lib()
    assert st.update_transcripts_tags([TX], ["重要", "工作"]) == 1
    fid = st.create_folder("我的分类")["id"]
    assert st.set_transcripts_folder([TX], fid) == 1
    before_doc = _doc_meta(st, TX)
    before_folders = st.list_folders()
    # 自证：前提真的设上了（否则「不变」断言的是空值，毫无区分力）
    assert before_doc["tags"] == ["重要", "工作"] and before_doc["folderId"] == fid
    assert before_doc["sourceUrl"] and before_folders

    st2 = Store(str(st.root))

    assert st2.get_transcript(TX) is not None, "文档 id 变了（重建会从正文重推 id）"
    assert _doc_meta(st2, TX) == before_doc, "文档级元数据被迁移改动了"
    assert st2.list_folders() == before_folders, "文件夹表被迁移改动了"


def test_index_is_byte_identical_when_nothing_to_migrate():
    """没有遗留布局的库启动 → `index.json` **逐字节不变**。

    上面 `test_no_migration_log_when_nothing_to_do` 只查日志——一个「嘴上说没迁移、
    实际照样重写索引」的实现能照样通过。这条把承诺钉在**文件字节**上。
    反面：只要 `_save_index()` 被无条件调用 → 这条红。
    """
    st = make_store()
    import_doc(st, TX)
    idx = st.root / "index.json"
    before = idx.read_bytes()

    Store(str(st.root))

    assert idx.read_bytes() == before, "没有遗留文件，启动却改写了 index.json"


def test_resync_only_rewrites_the_affected_mode():
    """重扫**只覆盖受影响的那个 mode**，另一个 mode 的条目（含 index 独有字段）原样保留。

    ⚠️ 反面：整库 `_rebuild_index`、或「顺手把该文档两个 mode 一起重扫」→ 整理版那条
    被**磁盘视角**重写，`status` 这类只活在 index 里的字段静默丢回默认值。
    """
    st = make_store()
    import_doc(st, TX)
    d = st.get_doc_dir(TX)
    (d / "notes.md").write_text(LATEST, encoding="utf-8")        # 遗留布局（两版齐全）
    (d / "notes.prev.md").write_text(PREV, encoding="utf-8")
    pdir = d / "polished"
    pdir.mkdir()
    (pdir / "v1.md").write_text("整理版正文", encoding="utf-8")   # 已是新布局
    # 手工给整理版造一条「磁盘上无从还原」的条目：status=partial + 非派生 id
    idx_path = st.root / "index.json"
    raw = json.loads(idx_path.read_text(encoding="utf-8"))
    raw.setdefault("markdowns", {})[TX] = [{
        "id": "md_pol_keep", "transcriptId": TX, "mode": "polished", "version": 1,
        "style": "detailed", "createdAt": "2026-01-01T00:00:00", "status": "partial",
    }]
    idx_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    st2 = Store(str(st.root))

    entries = st2.list_markdown_entries(TX)
    pol = [e for e in entries if e.get("mode") == "polished"]
    assert len(pol) == 1, f"整理版条目数量变了: {entries}"
    assert pol[0]["id"] == "md_pol_keep" and pol[0]["status"] == "partial", \
        f"整理版条目被重扫覆盖了: {pol[0]}"
    # 而迁移过的那一 mode 确实被重算（否则本条测试会因「什么都没发生」而假绿）
    assert sorted(e["version"] for e in entries if e.get("mode") == "notes") == [1, 2], entries
