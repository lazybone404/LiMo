"""导出资产收集 / 内联的路径穿越守卫（`P0-01-export-path-traversal`）。

**为什么值得一个独立文件**：这条通道把越界读到的文件 base64 **内嵌进对外分发物**
（自包含 HTML 单文件 / ZIP），是全项目唯一「越界读 = 直接把别人的文件交出去」的落点。
而它的三份兄弟副本（`content_preparer.py` 发给 LLM 的内联、`synthesis.py` 的资产搬运、
本轮抽出的 `core/path_guard.py`）都早有守卫——**判据只覆盖了一种写法、旁边那份漏了**，
正是本轮审查的主失效模式。

⚠️ 用例**双向**断言，缺一不可：
  · 越界引用被跳过（防漏）
  · 合法引用仍被收集，且条目名不含 `/` / `..`（防「一刀切全跳过」这类看起来更安全、
    实际把功能删掉的实现——它能让只断前者的用例全绿）

运行：python -m pytest tests/test_export_assets_guard.py
"""
import os
import tempfile
from pathlib import Path

from app.core.path_guard import resolve_within
from app.routers.export import (
    _collect_images, _find_image_refs, _inline_images,
)


# ── 装置 ──

def _make_store():
    """建一个临时 store_root，含：本文档目录（+ 合法图片）、越界的兄弟文档、越界的配置。"""
    tmp = Path(tempfile.mkdtemp(prefix="limo_guard_"))
    store_root = tmp / "store"
    doc_dir = store_root / "docs" / "本文档-aaa111"
    (doc_dir / "assets" / "sub").mkdir(parents=True)

    # 合法资产（两个，一个在子目录里）
    (doc_dir / "assets" / "img_0001.png").write_bytes(b"LEGIT-IMG-1")
    (doc_dir / "assets" / "sub" / "img_0002.png").write_bytes(b"LEGIT-IMG-2")

    # 越界目标：兄弟文档的正文、数据目录根下的配置
    sibling = store_root / "docs" / "别的文档-secret"
    sibling.mkdir(parents=True)
    (sibling / "transcript.md").write_text("OTHER-DOC-CONTENT", encoding="utf-8")
    (store_root / "config.yaml").write_text("api_key: sk-SECRET", encoding="utf-8")

    # files/ 与 screenshots/（另两个来源的落点）
    (store_root / "files" / "tx1").mkdir(parents=True)
    (store_root / "files" / "tx1" / "ok.png").write_bytes(b"LEGIT-FILE")
    (store_root / "screenshots").mkdir(parents=True)
    (store_root / "screenshots" / "ss.png").write_bytes(b"LEGIT-SS")

    # ⚠️ `files` / `screenshots` 两个分支的 base 是 **store_root**（不是 doc_dir），
    # 所以要造「穿出 store_root」的目标才有区分力——`../../config.yaml` 解析后仍在
    # store_root 内，守卫**应当放行**，拿它当越界用例是测试写错而非实现漏守。
    (tmp / "escaped.txt").write_text("OUTSIDE-STORE", encoding="utf-8")

    return tmp, str(store_root), str(doc_dir)


# ── 1. 守卫函数本身 ──

def test_resolve_within_allows_nested_and_dotdot_inside():
    """合法的多级子路径、以及**解析后仍在 base 内**的 `..` 都必须放行。

    ⚠️ 判据不能写成「路径里有没有 `..`」——那样 `assets/sub/../img.png` 会被误伤，
    而它是我们在乎的合法形态。
    """
    tmp, store_root, doc_dir = _make_store()
    assert resolve_within(doc_dir, "assets/img_0001.png") is not None
    assert resolve_within(doc_dir, "assets/sub/img_0002.png") is not None
    assert resolve_within(doc_dir, "assets/sub/../img_0001.png") is not None


def test_resolve_within_rejects_escapes():
    """越界一律 `None`：上跳、绝对路径、解析失败。

    `rel` 为绝对路径时 `Path(base) / rel` 会**丢弃 base**——不返回 `None` 的话
    `read_bytes()` 会直接读系统文件，这是最容易被忽略的一支。
    """
    tmp, store_root, doc_dir = _make_store()
    assert resolve_within(doc_dir, "assets/../../config.yaml") is None
    assert resolve_within(doc_dir, "assets/../../../etc/passwd") is None
    assert resolve_within(doc_dir, "/etc/passwd") is None
    assert resolve_within(store_root, "../config.yaml") is None


# ── 2. `_collect_images`：越界跳过 + 合法保留（双向）──

def test_collect_skips_traversal_for_every_source():
    """**四个来源分支**都必须守。

    报告只点了 `assets` 那一支，但 `_IMG_RE` / `_SS_RE` / `_API_ASSET_RE` 的末段是
    `[^\\s)]*`，字符集同样吃得下 `../../config.yaml` —— 只修一支 = 三条通道仍在。
    """
    tmp, store_root, doc_dir = _make_store()
    # ⚠️ 越界目标必须**真实存在**：指向不存在的路径时，「文件缺失」那条分支会先跳过，
    # 于是一个恒返回 None 的守卫也能让用例全绿——断言就测不到任何东西（模式 #38）。
    cases = [
        # 前两条 base = doc_dir（上跳两级即越出）
        "![x](assets/../../别的文档-secret/transcript.md)",            # assets
        "![x](/api/assets/other/../../别的文档-secret/transcript.md)",  # api_assets
        # 后两条 base = store_root，须**穿出 store_root** 才算越界
        "![x](/api/files/tx1/../../../escaped.txt)",                    # files
        "![x](/static/screenshots/../../escaped.txt)",                  # screenshots
    ]
    for md in cases:
        refs = _find_image_refs(md + "\n")
        assert refs, f"用例没匹配到任何 ref（正则改了？）: {md}"
        data, name_map = _collect_images(refs, store_root, doc_dir)
        assert data == {}, f"越界引用被收集了: {md}"
        assert name_map == {}, f"越界引用进了 name_map: {md}"
        html = _inline_images(md + "\n", data, name_map, store_root, doc_dir)
        # ⚠️ 只能断 `base64`——内联会把内容编码成 data URI，按明文断「内容没泄漏」
        # 是恒真的假判据（实测：无守卫实现下 `"OTHER-DOC-CONTENT" not in html` 照样成立）
        assert "base64" not in html, f"越界文件被内联了: {md}"


def test_inline_does_not_read_outside():
    """内联侧与收集侧**同一守卫**——收集侧跳过、内联侧也不能读（少一半 = 没做）。"""
    tmp, store_root, doc_dir = _make_store()
    md = "![x](assets/../../别的文档-secret/transcript.md)\n"
    refs = _find_image_refs(md)
    data, name_map = _collect_images(refs, store_root, doc_dir)
    html = _inline_images(md, data, name_map, store_root, doc_dir)
    assert "base64" not in html, "越界文件被 base64 内嵌进导出了"


def test_collect_keeps_legitimate_refs_and_flattens_names():
    """**区分力对照**：合法引用必须仍被收集，且条目名已净化。

    条目名直接进 `zf.writestr(f"{doc}/images/{img_name}")`，含 `/` 或 `..` 就是 zip-slip。
    导出物的 `images/` 本就是扁平目录，取 basename 不丢信息。
    """
    tmp, store_root, doc_dir = _make_store()
    md = ("![a](assets/img_0001.png)\n"
          "![b](assets/sub/img_0002.png)\n"
          "![c](/api/files/tx1/ok.png)\n"
          "![d](/static/screenshots/ss.png)\n")
    refs = _find_image_refs(md)
    data, name_map = _collect_images(refs, store_root, doc_dir)

    # `img_0002.png` 来自 `assets/sub/img_0002.png` —— 子路径被拍平，正是净化生效处
    assert set(data) == {"img_0001.png", "img_0002.png", "ok.png", "ss.png"}, \
        f"合法资产收集不全或条目名未净化: {sorted(data)}"
    assert data["img_0001.png"] == b"LEGIT-IMG-1"
    assert data["img_0002.png"] == b"LEGIT-IMG-2"
    for name in data:
        assert ".." not in name and "/" not in name, f"条目名未净化: {name}"


def test_rewrite_and_inline_still_resolve_legitimate_images():
    """净化条目名后，正文改写与内联仍必须命中（否则「文件收到了、正文却裂图」）。"""
    tmp, store_root, doc_dir = _make_store()
    md = "![b](assets/sub/img_0002.png)\n"
    refs = _find_image_refs(md)
    data, name_map = _collect_images(refs, store_root, doc_dir)
    html = _inline_images(md, data, name_map, store_root, doc_dir)
    assert "base64," in html, "合法图片没被内联"
    assert "assets/sub/img_0002.png" not in html, "正文里的相对引用没被替换"
