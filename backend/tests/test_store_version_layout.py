"""产物**版本归档布局**（`{doc_dir}/{mode}/v{N}.md`）—— 一版一文件，删 index 可完整重建。

**为什么值得一个独立文件**（2026-10-10 重做的核心症状）：

| 旧行为（两版制） | 后果 |
|---|---|
| `save_markdown` 主动裁掉第 3 条往前的 index 条目 + `os.replace` 覆盖 | 第 3 次生成起，最早的版本**物理消失** |
| `_rebuild_index` **显式跳过 `.prev.md`**、且从不读文件内容 | 删一次 `index.json` → 版本序塌成一条、`createdAt` 恒为空串 |
| 编辑也走 `save_markdown` | 编辑两次，AI 生成的那一版被挤没了 |

而答案一直在文件里：产物首行的自证头有 `generated.generatedAt`（真实生成时间）与
`generated.params.style`。本轮的修复就是「**头是真相，index 只是缓存**」。

⚠️ 每条断言都配了它的反面（旧行为下会怎么红），写在注释里 —— 判据要有区分力，
不能只测「跑通了」。

运行：python -m pytest tests/test_store_version_layout.py
"""
from conftest import import_doc  # noqa: E402

from app.core.transcript_meta import dump_meta  # noqa: E402
from app.core.types import GenerateMode, MarkdownView, Style  # noqa: E402
from app.generation.provenance import artifact_meta  # noqa: E402
from app.storage.store import Store, markdown_version_path  # noqa: E402

TX = "tr_layout"


def _mv(content: str, mode: GenerateMode = GenerateMode.NOTES) -> MarkdownView:
    """⚠️ `id` 传的是**一次性**的值：`save_markdown` 刻意不采信它（id 由
    `(tx, mode, version)` 确定性派生）——这里传不同的值正好顺带钉住这一点。"""
    return MarkdownView(id=f"md_{content[:4]}", transcript_id=TX, content=content,
                        style=Style.DETAILED, mode=mode)


def _head(model: str = "test-model", style: str = "detailed") -> dict:
    return artifact_meta("notes", doc_id=TX, mode="notes", model=model,
                         params={"style": style})


def _write_versions(st: Store, n: int) -> list[dict]:
    """连写 n 版笔记，返回每一版落盘时用的自证头（供比对 `createdAt`）。"""
    heads = []
    for i in range(n):
        head = _head(f"model-{i}")
        heads.append(head)
        assert st.save_markdown(_mv(f"第 {i + 1} 版正文"), file_meta=head), \
            f"第 {i + 1} 版没存上"
    return heads


def _generated_at(head: dict) -> str:
    return head["generated"]["generatedAt"]


def _by_version(st: Store, tx: str = TX) -> dict[int, dict]:
    return {e["version"]: e for e in st.list_markdown_entries(tx)}


def _reopen_without_index(st: Store) -> tuple[Store, str]:
    """删掉 index.json 后重新打开，返回 `(新 store, 重建后的 transcript id)`。

    ⚠️ **必须用重建后的 id**：重建会按正文 sha256 重推文档 id（既有语义，
    `test_artifact_meta.py` 第 7 节踩过同一个坑）。拿旧 id 去查产物会得到空列表，
    于是「版本丢没丢」这条断言**以错误的理由**失败 —— 假红比不红更误导人。
    """
    (st.root / "index.json").unlink()
    st2 = Store(str(st.root))
    docs = st2.list_transcripts()
    assert len(docs) == 1, f"重建后文档数不对: {len(docs)}"
    return st2, docs[0]["id"]


# ── 1. 全部保留（不设上限、不裁到 2 版）──

def test_all_versions_are_kept(tmp_store):
    """连写 3 版 → 磁盘上 v1/v2/v3 齐 + index 3 条。

    反面（旧实现）：`entry_list` 被裁到 2 条、`notes.prev.md` 被下一次 `os.replace`
    覆盖 → 这里会红在「v1.md 不存在」或「条目只有 2 条」。
    """
    import_doc(tmp_store, TX)
    heads = _write_versions(tmp_store, 3)
    doc_dir = tmp_store.get_doc_dir(TX)

    for n in (1, 2, 3):
        p = markdown_version_path(doc_dir, "notes", n)
        assert p.is_file(), f"v{n}.md 不存在 —— 版本被裁掉了"
        assert f"第 {n} 版正文" in p.read_text(encoding="utf-8")

    entries = _by_version(tmp_store)
    assert sorted(entries) == [1, 2, 3], f"index 条目版本: {sorted(entries)}"
    # 时间来自**写盘时那一份头**，不是「现在」、也不是彼此复制的同一个值
    for n, head in enumerate(heads, start=1):
        assert entries[n]["createdAt"] == _generated_at(head), \
            f"v{n} 的 createdAt 不是它自己那份头里的时间"

    # 新版本排在最前（`[0]` 即最新——下游四处都依赖这个约定）
    assert [e["version"] for e in tmp_store.list_markdown_entries(TX)] == [3, 2, 1]


# ── 2. 删掉 index.json → 从磁盘完整重建 ──

def test_rebuild_restores_versions_and_times(tmp_store):
    """**本轮要修的核心症状**：删一次 index.json，版本历史不能丢。

    反面（旧实现）：重建只认顶层 `{mode}.md`、跳过 `.prev.md`、且从不读文件头 →
    重建后只剩 1 条、`version` 恒为 1、`createdAt` 是空串。三条断言各钉一个面。
    """
    import_doc(tmp_store, TX)
    heads = _write_versions(tmp_store, 3)

    st2, tx2 = _reopen_without_index(tmp_store)

    entries = _by_version(st2, tx2)
    assert sorted(entries) == [1, 2, 3], \
        f"重建后版本序丢了，只剩 {sorted(entries)} —— 说明没扫磁盘上的 v*.md"
    for n in (1, 2, 3):
        assert entries[n]["createdAt"] == _generated_at(heads[n - 1]), \
            f"v{n} 的时间没从头里还原（旧实现这里是空串）"
        assert entries[n]["style"] == "detailed", \
            f"v{n} 的 style 没从 `generated.params` 还原"

    # 内容也要读得回来（条目在、文件打不开是另一种静默失败）
    got = st2.get_markdowns_by_transcript(tx2, mode="notes")
    assert [e["content"].strip() for e in got][0].endswith("第 3 版正文")


def test_rebuild_prefers_filename_over_header(tmp_store):
    """**版本号取文件名，不取 `identity.version`** —— 文件名才是布局的硬约束。

    构造一个头里写着 `version: 99` 的 `v5.md`：条目必须是 5。
    反面：若实现采信头里的值，条目会变成 99，而读路径按 99 拼路径 → 读不到文件。
    """
    import_doc(tmp_store, TX)
    mode_dir = tmp_store.get_doc_dir(TX) / "notes"
    mode_dir.mkdir(parents=True, exist_ok=True)
    head = _head()
    head["identity"]["version"] = 99          # 与文件名冲突
    (mode_dir / "v5.md").write_text(f"{dump_meta(head)}\n\n正文", encoding="utf-8")

    st2, tx2 = _reopen_without_index(tmp_store)

    assert sorted(_by_version(st2, tx2)) == [5], "版本号被头里的 99 带跑了"


def test_rebuild_marks_missing_header_honestly(tmp_store):
    """无自证头的历史产物：**保留**（不丢文档）+ 用占位值，而不是猜。

    这是「失败显式化」在重建路径上的落法——占位 `"detailed"` 的注释里写明了它
    是「无法还原时的占位」，不是默认档（模式 #44）。
    """
    import_doc(tmp_store, TX)
    mode_dir = tmp_store.get_doc_dir(TX) / "notes"
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "v1.md").write_text("没有自证头的老产物。", encoding="utf-8")

    st2, tx2 = _reopen_without_index(tmp_store)

    entries = _by_version(st2, tx2)
    assert sorted(entries) == [1], "无头产物被丢掉了"
    assert entries[1]["createdAt"] == "", "无头时不该凭空造时间"
    assert entries[1]["style"] == "detailed", "无头时的占位值变了"


# ── 3. 编辑就地覆盖（不产生版本）──

def test_replace_version_overwrites_in_place(tmp_store):
    """`replace_version=N` 覆盖第 N 版：版本数不变、id 不变、别的版本一字不动。

    反面（旧实现）：编辑走的是「新建一版 + 把旧版降 prev」→ 这里会红在
    「版本数变成 4」或「v1/v3 被动过」。
    """
    import_doc(tmp_store, TX)
    _write_versions(tmp_store, 3)
    doc_dir = tmp_store.get_doc_dir(TX)
    ids_before = {v: e["id"] for v, e in _by_version(tmp_store).items()}
    v1_before = markdown_version_path(doc_dir, "notes", 1).read_text(encoding="utf-8")
    v3_before = markdown_version_path(doc_dir, "notes", 3).read_text(encoding="utf-8")

    assert tmp_store.save_markdown(_mv("改过的第二版"), file_meta=_head("model-1"),
                                   replace_version=2)

    ids_after = {v: e["id"] for v, e in _by_version(tmp_store).items()}
    assert sorted(ids_after) == [1, 2, 3], "编辑不该新增版本"
    assert ids_after == ids_before, "覆盖同一版不该换 id（id 由 tx/mode/version 派生）"
    assert "改过的第二版" in markdown_version_path(doc_dir, "notes", 2).read_text(encoding="utf-8")
    assert markdown_version_path(doc_dir, "notes", 1).read_text(encoding="utf-8") == v1_before
    assert markdown_version_path(doc_dir, "notes", 3).read_text(encoding="utf-8") == v3_before


def test_replace_missing_version_fails_loud(tmp_store):
    """覆盖一个不存在的版本 → **返回 False 且绝不新建文件**。

    「用户以为在改 v1，库里凭空多了个 v1」是最坏的一种成功：正文落到了意料之外
    的版本上，而界面显示一切正常。
    """
    import_doc(tmp_store, TX)
    _write_versions(tmp_store, 2)
    doc_dir = tmp_store.get_doc_dir(TX)

    assert tmp_store.save_markdown(_mv("不该落盘"), file_meta=_head(),
                                   replace_version=9) is False
    assert not markdown_version_path(doc_dir, "notes", 9).is_file(), "失败时新建了文件"
    assert sorted(_by_version(tmp_store)) == [1, 2]


def test_edit_keeps_original_created_at(tmp_store):
    """编辑**不改**这一版的生成时间——「何时生成」不因编辑而变。

    反面：若 `created_at` 取 `mv.created_at`（= 现在），每次编辑这一版就在版本列表里
    跳到最前，用户会以为它被重新生成过。
    """
    import_doc(tmp_store, TX)
    heads = _write_versions(tmp_store, 2)
    original = _generated_at(heads[1])

    tmp_store.save_markdown(_mv("编辑过"), file_meta=_head("model-1"), replace_version=2)

    assert _by_version(tmp_store)[2]["createdAt"] == original


# ── 4. 并发：版本号计算与写盘同锁 ──

def test_concurrent_saves_do_not_collide(tmp_store):
    """并发写同一 mode → 版本号**互不重复**（每个 N 恰好一个文件）。

    版本号由磁盘 `max+1` 决定，而旧实现「文件写在锁外、index 写在锁内」——两个
    并发的生成会算出同一个 N 并互相覆盖（最后一个赢，版本凭空少一个）。
    """
    import threading

    import_doc(tmp_store, TX)
    doc_dir = tmp_store.get_doc_dir(TX)

    def worker(i: int):
        tmp_store.save_markdown(_mv(f"并发第 {i} 版"), file_meta=_head(f"m{i}"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    files = sorted(p.name for p in (doc_dir / "notes").iterdir())
    assert files == [f"v{i}.md" for i in range(1, 7)], f"版本号撞了: {files}"
    assert sorted(_by_version(tmp_store)) == [1, 2, 3, 4, 5, 6]
