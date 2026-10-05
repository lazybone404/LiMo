"""引擎能力位标签的完整性守卫（P2-15）。

运行：./.venv/Scripts/python.exe -m pytest tests/test_engine_capability_labels.py -q

**修复的是什么**：`GET /api/capabilities` 早就下发 `engineLabels`
（`{text: 文字, table: 表格, ...}`），前端 `api/core.ts` 也声明了该字段，
但**没有任何消费方** —— 于是 `EngineSelect` 把引擎的 `capabilities` 原样 join，
用户在引擎下拉里看到的是裸英文（`pypdfium2 · doc`）。

把它接上前端只是「用起来」；**真正会腐化的是这张表本身**：引擎类新增一个能力位
（比如某个引擎开始输出 `math`）时，没人会想起 `core/registry.py` 里还有一张
中文表 —— 症状是「那一个徽标是英文、其余是中文」，**零报错、零测试红**。

本文件守三件事：
  ① 全部引擎类声明的 `capabilities` 都在表里（含**动态**赋值的形态）
  ② 两个字面量点（常量定义 / 前端消费）不各写一份
  ③ 与 `modelCaps` 那套**模型**能力位不混用（两套词汇刻意分开）
"""

import ast
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.registry import ENGINE_CAPABILITY_LABELS

_BACKEND = Path(__file__).resolve().parent.parent
_APP = _BACKEND / "app"
_ENGINES = _APP / "engines"


# ═══════════════════════════════════════════════════════════════
# ① 表必须覆盖全部引擎声明的能力位
# ═══════════════════════════════════════════════════════════════

def _declared_capabilities() -> dict[str, list[str]]:
    """扫引擎类里的 `capabilities = [...]` / `self.capabilities = [...]`。

    ⚠️ **两种形态都要收**：类属性（`capabilities = ["asr"]`）与实例属性
    （`self.capabilities = [...]`，dashscope 系按配置动态算）。只扫前者会漏掉
    云端引擎 —— 而它们恰好是能力位最多的那几个。
    """
    found: dict[str, list[str]] = {}
    for py in sorted(_ENGINES.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "")
                       for t in node.targets]
            if "capabilities" not in targets:
                continue
            # 只看列表字面量；三元的另外两个分支也会各自是 Assign（条件表达式不是）
            for val in ast.walk(node.value):
                if isinstance(val, ast.List):
                    caps = [e.value for e in val.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    if caps:
                        found.setdefault(py.name, []).extend(caps)
    return found


def test_every_declared_capability_has_a_label():
    """全部引擎能力位都在表里 —— 漏一个就是「那一个徽标是英文」。"""
    declared = _declared_capabilities()
    assert declared, "没扫到任何引擎能力声明？守卫的扫描面可能坏了（模式 #38）"
    all_caps = {c for caps in declared.values() for c in caps}
    missing = sorted(all_caps - set(ENGINE_CAPABILITY_LABELS))
    assert not missing, (
        f"这些能力位没有中文标签，前端会显示裸英文：{missing}\n"
        f"（扫描到的声明：{ {k: sorted(set(v)) for k, v in declared.items()} }）"
    )


def test_control_scanner_finds_both_forms():
    """对照实验：扫描器对**类属性**与**实例属性**两种形态都有效（模式 #38）。"""
    class_form = ast.parse('class E:\n    capabilities = ["asr"]\n')
    inst_form = ast.parse('class E:\n    def __init__(self):\n        self.capabilities = ["ocr"]\n')

    def _caps(tree) -> set[str]:
        out: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "")
                         for t in node.targets]
                if "capabilities" in names and isinstance(node.value, ast.List):
                    out |= {e.value for e in node.value.elts
                            if isinstance(e, ast.Constant)}
        return out

    assert _caps(class_form) == {"asr"}
    assert _caps(inst_form) == {"ocr"}


def test_label_table_has_no_empty_value():
    """空标签比裸英文更糟 —— 界面上会多出个空徽标。"""
    empty = [k for k, v in ENGINE_CAPABILITY_LABELS.items() if not v.strip()]
    assert not empty, f"这些能力位的标签是空的：{empty}"


# ═══════════════════════════════════════════════════════════════
# ② 字面量只允许有一份
# ═══════════════════════════════════════════════════════════════

def test_label_literal_defined_exactly_once():
    """含引擎独有键值对的字典只允许出现在 `core/registry.py`。

    ⚠️ **断言是子集（`<=`）而不是相等**，且 probe 只取**引擎独有**的两对：
    早先写成「表里 7 对全中」，于是本判据被绑上了「那张表必须完整」——
    registry 自己少写一个键时它也会红，而红的原因**不是「出现了副本」**。
    一条判据管一件事：完整性归 `test_every_declared_capability_has_a_label`，
    本条只管「有没有第二份」。
    """
    hits: list[str] = []
    # `office` / `asr` 是引擎侧独有的，模型能力位表里不可能有 → 拿它们当指纹
    probe = {"office": "文档", "asr": "语音"}
    for py in sorted(_APP.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pairs = {}
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    pairs[k.value] = v.value
            if all(pairs.get(k) == v for k, v in probe.items()):
                hits.append(f"{py.relative_to(_BACKEND).as_posix()}:{node.lineno}")
    where = {h.split(":")[0] for h in hits}
    assert where <= {"app/core/registry.py"}, \
        f"能力标签字面量只该在 core/registry.py，实际：{hits}"


def test_control_literal_detector_has_discrimination():
    """对照：整份副本会被抓，而与模型能力位相邻的字典不会（模式 #38）。"""
    def hits_of(src: str) -> list:
        out = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Dict):
                continue
            pairs = {k.value: v.value for k, v in zip(node.keys, node.values)
                     if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
            if all(pairs.get(k) == v for k, v in {"office": "文档", "asr": "语音"}.items()):
                out.append(node)
        return out

    assert hits_of('L = {"text":"文字","table":"表格","image":"图片","ocr":"OCR",'
                   '"pdf":"PDF","office":"文档","asr":"语音"}'), "整份副本没被抓到"
    assert hits_of('L = {"office":"文档","asr":"语音"}'), "精简副本没被抓到"
    # 模型能力位那张表（同含 text/image，但独有键是 video/reasoning/document）不该命中
    assert not hits_of('L = {"text":"文本","image":"图片","video":"视频",'
                       '"reasoning":"思考档位","document":"PDF 直传"}')


# ═══════════════════════════════════════════════════════════════
# ③ 与模型能力位那套不混用
# ═══════════════════════════════════════════════════════════════

_MODEL_CAPS_TS = _BACKEND.parent / "frontend" / "src" / "lib" / "modelCaps.ts"


def _model_capability_keys() -> set[str]:
    """从**前端真实源码**取模型能力位，而不是在这里手抄一份。

    ⚠️ 手抄的失效方式是它自己不会红：前端加了新能力位，这里的快照纹丝不动，
    而「两套词汇不混用」的结论**照样成立**（它只断言两侧互异），于是守卫看起来
    一直在工作。跨语言取真值 —— 同 `appShortcutKeys.test.ts` 读 Rust 源码的手法（模式 #65）。

    ⚠️ 文件找不到时**失败而不是跳过**：「永远跳过的门禁 ≡ 没有门禁」（模式 #61 配套陷阱）。
    """
    assert _MODEL_CAPS_TS.is_file(), f"找不到模型能力位表：{_MODEL_CAPS_TS}"
    src = _MODEL_CAPS_TS.read_text(encoding="utf-8")
    m = re.search(r"CAP_LABELS[^=]*=\s*\{(.*?)\}", src, re.S)
    assert m, "在 modelCaps.ts 里找不到 CAP_LABELS 对象字面量"
    body = re.sub(r"//.*$", "", m.group(1), flags=re.M)  # 行内注释可能含 `xx:`
    keys = set(re.findall(r"(?:^|[,{\s])([A-Za-z_][A-Za-z0-9_]*)\s*:", body))
    assert keys, "CAP_LABELS 里没解析出任何键（正则与那张表脱节了）"
    return keys


def test_distinct_from_model_capability_labels():
    """引擎能力位 ≠ 模型能力位 —— 有交集（text/image）但**含义不同**，不能合并。

    ⚠️ 合并的后果是静默的：`image` 在两侧都会被翻成「图片」，看不出问题；
    而 `document`（模型的 PDF 直传）压根不是引擎能力位、`office`（引擎的文档）
    也不是模型能力位 —— 两边各自多出来的那几个会互相污染。
    """
    model_caps = _model_capability_keys()
    engine_caps = set(ENGINE_CAPABILITY_LABELS)
    assert engine_caps != model_caps, "两套能力位被合并成一张表了"
    # 各自都有对方没有的（这才是「两套」的实证，而不是靠重叠与否猜）
    assert engine_caps - model_caps, "引擎侧没有独有能力位？那大概是拿错表了"
    assert model_caps - engine_caps, "模型侧没有独有能力位？那大概是拿错表了"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
