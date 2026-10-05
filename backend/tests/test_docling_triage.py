"""`engines/docling.py` 六处静默 except 的 triage（P1-16）。

运行：./.venv/Scripts/python.exe -m pytest tests/test_docling_triage.py -q

**修复的是什么**：这份文件是 PDF 的**默认引擎**（`engines/__init__.py` 里注册为
`pypdfium2`），里面有 6 处 `except Exception`。其中两处会让**内容凭空消失且零提示**：

  - `:88`  单张嵌入图提取失败 → 图从产物里消失（PDF 的嵌入图常是图表/公式）
  - `:182` 表格抽取失败 → 该页表格消失，而返回值 `None` 与「本来就没表格」**不可区分**

另外四处经 triage **刻意保留静默**（并补了注释说明判据）——本文件用**反向断言**
把它们钉住：那四处**不该**产生 warning，否则「triage 保留」就悄悄退化成了「到处报警」。

⚠️ 对照实验（每条断言都自证有区分力，模式 #38）：
  - 把 `:88` / `:182` 的 except 改回 `pass` → 前两条用例红
  - 把那四处保留项的 warning 补上 → 后四条用例红

⚠️ 为什么不给 `:88` 的产物插「图片提取失败」占位：正文里没有内部标记需要被消费，
   不是零残留场景；形态与 `doc_docx.py:218` 的图片提取失败**逐字同构**（warning + 跳过）。
   P1-07 的截图**必须**替换 marker，是因为 `Screenshot-[mm:ss]` 是内部标记，
   留着会被前端渲染成「📷 截图标」badge（看起来与成功一模一样）。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pypdfium2 as pdfium
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines.docling import PyPdfium2Engine, logger


@pytest.fixture()
def img_pdf(tmp_path) -> str:
    """一页、整页一张图的真 PDF（PIL 写的，pypdfium2 能读出 `PdfImage`）。"""
    p = tmp_path / "one_image.pdf"
    Image.new("RGB", (200, 200), "white").save(str(p), "PDF")
    return str(p)


def _warns(mock_warning) -> list[str]:
    return [c.args[0] for c in mock_warning.call_args_list]


# ═══════════════════════════════════════════════════════════════
# 修掉的两处：失败必须留痕
# ═══════════════════════════════════════════════════════════════

def test_image_extract_failure_warns(img_pdf, tmp_path):
    """`:88` 单图失败 → warning（含页码）+ 跳过该图 + 整体不抛。"""
    with patch.object(pdfium.PdfImage, "get_bitmap",
                      side_effect=RuntimeError("bad embedded image")):
        with patch.object(logger, "warning") as w:
            res = PyPdfium2Engine().execute(img_pdf, media_dir=str(tmp_path))

    hits = [c for c in w.call_args_list if "图片提取失败" in c.args[0]]
    assert len(hits) == 1, f"应恰好一条图片失败 warning，实际 {_warns(w)}"
    assert hits[0].args[1] == 1, "页码应从 1 起（不是内部索引 0）"
    assert "bad embedded image" in hits[0].args[2], "原因要带上，否则无从判断是坏图还是写盘失败"
    # 跳过而非崩：产物里不该出现半写成的图片引用，引擎照常返回
    assert "![img_" not in res.text
    assert res.engine == "pypdfium2"


class _BoomPages:
    """pdfplumber 文档替身：`pages` 一碰就炸。"""
    @property
    def pages(self):
        raise RuntimeError("table layer boom")


def test_table_extract_failure_warns():
    """`:182` 表格失败 → warning（含页码）+ 返回 None。"""
    with patch.object(logger, "warning") as w:
        out = PyPdfium2Engine._extract_tables(_BoomPages(), 7)

    assert out is None
    assert len(w.call_args_list) == 1, f"应恰好一条 warning，实际 {_warns(w)}"
    assert "表格提取失败" in w.call_args.args[0]
    assert w.call_args.args[1] == 8, "page_idx 是 0-based，日志里应是第 8 页"


# ═══════════════════════════════════════════════════════════════
# 保留的四处：不该留痕（反向断言，防止 triage 结论被改坏）
# ═══════════════════════════════════════════════════════════════

class _NoTables:
    class _P:
        def find_tables(self):
            return []
    pages = [_P()]


def test_no_table_is_silent():
    """`:182` 附近的**正常**路径：「这页没有表格」是常态，不能报警。

    ⚠️ 与上一条成对：一条证明「失败有声音」，一条证明「成功/常态没声音」。
    只留前者会把判据变成「只要没抽出表格就报警」——那份 PDF 的每一页都会刷一条。
    """
    with patch.object(logger, "warning") as w:
        assert PyPdfium2Engine._extract_tables(_NoTables(), 0) is None
    assert not w.called, f"无表格不是异常：{_warns(w)}"


class _NoToc:
    def get_toc(self):
        raise RuntimeError("no outline")


def test_toc_absent_is_silent():
    """`:133` 无目录 → 返回 None 且**不**记日志（绝大多数 PDF 没有目录）。"""
    with patch.object(logger, "warning") as w:
        assert PyPdfium2Engine._extract_toc(_NoToc()) is None
    assert not w.called, f"无目录是常态，不该刷日志：{_warns(w)}"


class _BoomTP:
    def count_rects(self):
        raise RuntimeError("textpage boom")


def test_paragraph_boundaries_silent():
    """`:160` 段落分界估算失败 → 返回 [] 且**不**记日志（排版退化，不丢内容）。"""
    with patch.object(logger, "warning") as w:
        assert PyPdfium2Engine._paragraph_boundaries(_BoomTP(), 3) == []
    assert not w.called, f"这是排版退化不是内容丢失：{_warns(w)}"


class _BoomClose:
    pages: list = []

    def close(self):
        raise RuntimeError("close boom")


def test_pdfplumber_close_failure_is_silent(img_pdf, tmp_path):
    """`:118` close 失败 → 无副作用、无日志（句柄随进程回收）。"""
    with patch("pdfplumber.open", return_value=_BoomClose()):
        with patch.object(logger, "warning") as w:
            res = PyPdfium2Engine().execute(img_pdf, media_dir=str(tmp_path))
    assert res.engine == "pypdfium2", "close 失败不该影响已经生成的产物"
    assert not [m for m in _warns(w) if "close" in m], f"close 失败不该留痕：{_warns(w)}"


# ═══════════════════════════════════════════════════════════════
# :44 pdfplumber 整体不可用 —— 能力级降级，留痕但继续
# ═══════════════════════════════════════════════════════════════

def test_pdfplumber_unavailable_warns_but_continues(img_pdf, tmp_path):
    """`:44` pdfplumber 打不开 → warning + **文字/图片路径照常走完**。"""
    with patch("pdfplumber.open", side_effect=RuntimeError("not installed")):
        with patch.object(logger, "warning") as w:
            res = PyPdfium2Engine().execute(img_pdf, media_dir=str(tmp_path))

    hits = [m for m in _warns(w) if "pdfplumber" in m]
    assert len(hits) == 1, f"表格能力整体失效必须留痕一次：{_warns(w)}"
    assert res.engine == "pypdfium2", "表格不可用不该中断整份转录"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
