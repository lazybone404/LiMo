"""提示词指纹守卫 —— `PROMPT_FINGERPRINT` 与提示词源代码是否同源。

⚠️ 守的是 `PROMPT_VERSION` 的**手抄**问题：版本号由人 bump，已经漏过一次
（`1ef7e23` 加了关键词规范没 bump，"23" 同时对应两种 prompt）。本文件的第 1 条
把「声明的指纹」与「现算的指纹」钉在一起，第 2 条用对照样本证明这个比较**会红**
—— 只断言「两者相等」的话，一个恒返回空串的实现也能全绿。

⚠️ 改提示词后的正常流程：跑 `prompt_fingerprint()` 拿新值 → 更新
`prompts/__init__.py::PROMPT_FINGERPRINT` → 决定要不要 bump `PROMPT_VERSION`。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.generation.prompts import PROMPT_FINGERPRINT, PROMPT_VERSION  # noqa: E402
from app.generation.prompts.fingerprint import (  # noqa: E402
    fingerprint_of_sources,
    prompt_fingerprint,
    source_files,
)


def test_声明值与现算值一致():
    assert PROMPT_FINGERPRINT == prompt_fingerprint(), (
        '提示词文本变了而 PROMPT_FINGERPRINT 没更新。'
        '先确认这一版要不要 bump PROMPT_VERSION，再更新指纹常量。'
        f'（当前 PROMPT_VERSION={PROMPT_VERSION}）'
    )


def test_指纹是八位十六进制():
    assert len(PROMPT_FINGERPRINT) == 8
    assert all(c in '0123456789abcdef' for c in PROMPT_FINGERPRINT)


# ── 对照实验：证明这个比较有区分力 ──

def test_改一个字面量指纹就变():
    a = fingerprint_of_sources(['X = "强调这一句。"\n'])
    b = fingerprint_of_sources(['X = "着重这一句。"\n'])
    assert a != b


def test_docstring_不参与指纹():
    """注释性说明改了不该要求 bump —— 它不进入 prompt 组合。"""
    a = fingerprint_of_sources(['"""说明甲"""\nX = "同一句"\n'])
    b = fingerprint_of_sources(['"""说明乙"""\nX = "同一句"\n'])
    assert a == b


def test_常量搬家不改变指纹():
    """同一批字面量换个文件 / 换个顺序，指纹不变（否则每次重构都会红）。"""
    a = fingerprint_of_sources(['X = "甲"\nY = "乙"\n'])
    b = fingerprint_of_sources(['Y = "乙"\n', 'X = "甲"\n'])
    assert a == b


def test_真实文件都参与了计算():
    """往真实文件集合里加一句，指纹必须变 —— 证明 source_files 覆盖到了它们。"""
    real = [p.read_text(encoding='utf-8') for p in source_files()]
    assert real, 'source_files() 不能为空'
    assert fingerprint_of_sources(real + ['_SENTINEL = "哨兵"\n']) != prompt_fingerprint()
