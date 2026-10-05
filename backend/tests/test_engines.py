"""验证引擎注册（无引擎降级链）。

⚠️ 判据纪律（P1-01，2026-10-06）：本文件原先**零 assert** —— 引擎可用性用
`print(f'bcut-asr registered: {x is not None}')` 报告，而「失败即失败」那条更是
try / except **两个分支都** `print('PASS')`。后果是 pytest 收集 0 项、退出码 0：
`print` 出来的 `False` 不影响退出码，门禁全绿而这两件事其实**没人看守**。
现在：`print` 只留作诊断输出，一切判据一律 `assert`。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.core.registry import EngineRegistry
from app.engines import init_engines


@pytest.fixture(scope="module", autouse=True)
def _engines_ready():
    """模块内只初始化一次注册表（`init_engines` 幂等，但没必要每个用例跑一遍）。"""
    init_engines()


def test_engine_registration():
    """关键引擎按名字可查（注册表被改坏时变红）。"""
    asr = EngineRegistry.list_category("asr")
    doc = EngineRegistry.list_category("doc")
    print(f"asr: {asr}")   # 诊断输出，不参与判据
    print(f"doc: {doc}")

    assert "bcut-asr" in asr, f"bcut-asr 未注册，实际 {asr}"
    assert EngineRegistry.get("asr", "bcut-asr") is not None
    assert EngineRegistry.get("doc", "markitdown") is not None
    assert EngineRegistry.get("doc", "pypdfium2") is not None, "默认 PDF 引擎缺失"


def test_engine_no_fallback():
    """绑定 markitdown 时，产物自报的 engine 必须是 markitdown —— 「无降级链」的可观测判据。

    ⚠️ 这条**刻意不写成** `pytest.raises`：实测 markitdown 对垃圾 PDF 不抛异常，它把
    原始字节原样当文本返回（`DocResult(engine='markitdown', text='%PDF-1.4 garbage…')`）。
    无降级链要守的是「没有把活交给第二个引擎」，而这件事的可观测形态就是产物上的
    `engine` 字段 —— 降级链被加回来时它会变成另一个引擎名，这条即红。

    旧写法无论抛不抛都打印 PASS，所以这条判据从未存在过。
    """
    doc_md = EngineRegistry.get("doc", "markitdown")
    assert doc_md is not None, "markitdown 未注册"

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(b"%PDF-1.4 garbage not real pdf")
        path = tf.name
    try:
        result = doc_md.run(path)
    finally:
        os.unlink(path)

    assert result.engine == "markitdown", (
        f"产物自报引擎为 {result.engine!r}，说明本次调用被交给了别的引擎"
        f"（无降级链：绑定只取第一个）"
    )
