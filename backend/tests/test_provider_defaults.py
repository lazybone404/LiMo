"""内置厂商默认表 = 唯一真相源（P1-14）。

运行：uv run python tests/test_provider_defaults.py
     （或 ./.venv/Scripts/python.exe tests/test_provider_defaults.py）

**修复的是什么**：`routers/config.py::detect_models` 在「拿不到 base_url」时
**手写了第二份**默认表，只有 deepseek / openai 两家：

    defaults = {"deepseek": "https://api.deepseek.com",
                "openai":   "https://api.openai.com/v1"}

于是 `/llm/models` 对 dashscope / google / zhipu / moonshot / opencode / anthropic
**全部取不到地址** → 报「无法确定 API 地址」，而用户明明填了 provider、Key 也在。
默认表当时就在 `routers/model.py` 里躺着（只是短了一截）——**看代码看不出来**。

⚠️ 每段都配「对照实验」注释，说明把实现改回旧形态时**哪几条**会红（模式 #38：
断言必须自证有区分力；#54：同源是搜索题不是记忆题）。
"""
import ast
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.provider_defaults import PROVIDER_DEFAULTS, default_base_url
from app.routers.config import _resolve_detect_base_url

_APP = Path(__file__).resolve().parent.parent / "app"


# ── ① 表本身的形状 ──

def test_table_shape():
    assert PROVIDER_DEFAULTS, "内置厂商表不能为空"
    for pid, info in PROVIDER_DEFAULTS.items():
        assert set(info) >= {"name", "base_url", "type"}, f"{pid} 字段不全：{info}"
        parsed = urlparse(info["base_url"])
        assert parsed.scheme in ("http", "https"), f"{pid} base_url 协议不对"
        assert parsed.hostname, f"{pid} base_url 缺主机名"
    print(f"PASS: 内置厂商表 {len(PROVIDER_DEFAULTS)} 家，字段与地址形态正确")


# ── ② default_base_url 与表同源 ──

def test_default_base_url_matches_table():
    for pid, info in PROVIDER_DEFAULTS.items():
        assert default_base_url(pid) == info["base_url"], f"{pid} 取值与表不一致"
    assert default_base_url("not-a-provider") == ""
    assert default_base_url("") == ""
    print("PASS: default_base_url 与表同源；未知厂商返回空串")


# ── ③ ★ 关键：/llm/models 的兜底对**每一家**都给出地址 ──
#
# 对照实验：把 `_resolve_detect_base_url` 的第三档改回
#     `return {"deepseek": "https://api.deepseek.com",
#              "openai": "https://api.openai.com/v1"}.get(provider, "")`
# → 下面 dashscope / google / zhipu / moonshot / opencode / anthropic / anthropic
#    **每一条都会红**，而 deepseek / openai 仍绿 —— 那正是当年那份子集的形状。
#    所以这条用例的区分力**不在数量**，而在「红的恰好是子集之外那几家」。

def test_detect_fallback_covers_every_builtin():
    for pid, info in PROVIDER_DEFAULTS.items():
        got = _resolve_detect_base_url({}, pid, "")
        assert got == info["base_url"], (
            f"/llm/models 兜底对 {pid} 给出的地址是 {got!r}，应为 {info['base_url']!r}")
    print(f"PASS: /llm/models 兜底覆盖全部 {len(PROVIDER_DEFAULTS)} 家内置厂商")


# ── ④ 优先级顺序：显式 > 用户配置 > 内置默认 ──

def test_priority_explicit_then_cfg_then_default():
    cfg = {"llm": {"base_url": {"deepseek": "https://my-proxy.example/v1"}}}
    # 显式传入最高
    assert _resolve_detect_base_url(cfg, "deepseek", "https://explicit.example/v1") \
        == "https://explicit.example/v1"
    # 其次用户配置
    assert _resolve_detect_base_url(cfg, "deepseek", "") \
        == "https://my-proxy.example/v1"
    # 都没有才落到内置默认
    assert _resolve_detect_base_url(cfg, "zhipu", "") == PROVIDER_DEFAULTS["zhipu"]["base_url"]
    # cfg 结构缺失/为 None 都不能炸（前端可能不带 llm 段调这个端点）
    assert _resolve_detect_base_url({}, "zhipu", "") == PROVIDER_DEFAULTS["zhipu"]["base_url"]
    assert _resolve_detect_base_url({"llm": None}, "zhipu", "") == PROVIDER_DEFAULTS["zhipu"]["base_url"]
    print("PASS: 三档优先级 显式 > cfg > 内置默认；cfg 缺失/为 None 不炸")


# ── ⑤ 表只能有一处定义（AST 扫源码，不误伤注释）──
#
# 对照实验：在 `routers/model.py` 里加一行 `PROVIDER_DEFAULTS = {}` → 本段立刻红。
# ⚠️ 走 AST 而非 grep：本模块与 `provider_defaults.py` 的 docstring / 注释里
#    **刻意**引用了旧的错误写法当反例，grep 会把「讲代码的文字」判成违规（模式 #54）。

def _assign_targets(path: Path, name: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    ]


def test_table_defined_exactly_once():
    defs = []
    for py in _APP.rglob("*.py"):
        for lineno in _assign_targets(py, "PROVIDER_DEFAULTS"):
            defs.append(f"{py.relative_to(_APP)}:{lineno}")
    defs = [d.replace("\\", "/") for d in defs]
    assert len(defs) == 1, f"PROVIDER_DEFAULTS 有 {len(defs)} 处定义：{defs}（应只有一处）"
    assert defs[0].split(":")[0] == "core/provider_defaults.py", (
        f"表应定义在 core/provider_defaults.py，实际在 {defs[0]}")
    print(f"PASS: PROVIDER_DEFAULTS 全仓唯一定义 @ {defs[0]}")


if __name__ == "__main__":
    test_table_shape()
    test_default_base_url_matches_table()
    test_detect_fallback_covers_every_builtin()
    test_priority_explicit_then_cfg_then_default()
    test_table_defined_exactly_once()
    print("\n✅ test_provider_defaults 全部通过")
