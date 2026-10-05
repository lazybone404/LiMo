"""index.json 损坏 / 缺失 / 旧格式时的**自愈**（`P0-03-index-corrupt-silent-empty`）。

**为什么值得一个独立文件**：`index.json` 是唯一的文档清单。它一坏，此前的行为是
`logger.warning` + **空索引** —— 磁盘上几十篇文档还在，库却空了，而**没有任何一层
会把这件事告诉用户**（前端只显示「还没有文档」）。同一个「读不出配置」的场景在
`core/config.py:338` 待遇完全不同：先 `copy2` 一份 `.bak`、再回落。这里补上同样的两半。

⚠️ 用例**双向**断言，缺一不可：
  · 损坏/缺失时文档必须回来（防「静默空库」）
  · 正常索引**原样不动**（防「无脑重建」——重建会重推 id、丢分类，比不修更糟）

运行：python -m pytest tests/test_store_index_recovery.py
"""
import json
import logging

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

    # ③ 降级项写清楚了：用户知道「回来了，但丢了什么」，才能判断要不要去翻备份
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "有损" in msg, f"warning 没说明重建有损: {msg!r}"
    for field in ("文件夹归属", "标签", "sha256"):
        assert field in msg, f"warning 没列出降级字段 {field}: {msg!r}"


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
