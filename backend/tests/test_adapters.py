"""验证适配器注册 + 路由。

⚠️ 判据纪律（P1-01，2026-10-06）：原先把结果拼成
`status = "OK" if name == expected else f"FAIL (expected=…, got=…)"` 之后**只 print**。
失败被降级成一行日志，退出码仍是 0，pytest 收集 0 项 —— 路由表被改坏也全绿。
现在改成参数化用例，每条路由一个真断言。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.core.registry import AdapterRegistry
from app.core.types import InputType, PipelineInput
from app.adapters import init_adapters


@pytest.fixture(scope="module", autouse=True)
def _adapters_ready():
    init_adapters()


def test_adapters_registered():
    """注册表非空且覆盖各承载类型（`accept()` 是纯输入判断，此处只看名字集合）。"""
    names = {cls.name for cls in AdapterRegistry.all()}
    print(f"Adapters registered: {len(names)} → {sorted(names)}")
    assert {"text", "webpage", "document", "video"} <= names, f"缺适配器，实际 {names}"


@pytest.mark.parametrize(
    "inp, expected",
    [
        (PipelineInput(type=InputType.TEXT, text="hello"), "text"),
        (PipelineInput(type=InputType.URL, url="https://example.com"), "webpage"),
        (PipelineInput(type=InputType.FILE, file_path="test.pdf"), "document"),
        (PipelineInput(type=InputType.FILE, file_path="test.docx"), "document"),
        (PipelineInput(type=InputType.FILE, file_path="test.mp4"), "video"),
    ],
    ids=["text", "url", "pdf", "docx", "mp4"],
)
def test_adapter_routing(inp, expected):
    """注册顺序决定路由优先级：Video → Document → Image → Webpage → Text（兜底）。"""
    found = AdapterRegistry.route(inp)
    name = found.name if found else None
    print(f"  {'OK' if name == expected else 'FAIL'}: {inp.type} "
          f"{inp.url or inp.file_path or (inp.text or '')[:20]} → {name}")
    assert name == expected, f"路由错误：期望 {expected}，实际 {name}"
