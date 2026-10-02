"""验证统一预算函数 — single 零回归 / multi 闭合与取小 / 窗口闸门 / 现状自证

本模块存在的理由只有一条：**创建侧与生成侧必须用同一个预算函数**（设计 §4.4.5）。
若两处各算各的，小窗口模型上会在生成阶段二次截断、后几篇凭空消失。

两条核心不变量：
1. `single` 与既有 ContentPreparer 算法**逐值等价** —— 改了会让所有既有用户的
   单文档可用输入突然变化（零回归要求）
2. `multi` 满足 `input + output + margin ≤ cw` —— 不闭合就等于请求必然超窗
"""
import re
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.token import estimate_tokens as _estimate_tokens
from app.generation.budget import (
    GENERATION_MAX_TOKENS, MIN_MULTI_CONTEXT_WINDOW, MIN_MULTI_INPUT, RELIABILITY,
    SAFETY_MARGIN, SINGLE_OUTPUT_RESERVE,
    input_budget_for, multi_capacity_ok, multi_input_budget, single_input_budget,
    stuff_window,
)


def _overlong(budget: int, unit: str = "中文内容。") -> str:
    """构造一段**必然超出 `budget`** 的文本，长度按本环境实测的 token 密度反推。

    ⚠️ 不能假设「1 中文字符 = 1 token」：`estimate_tokens` 在未装 tiktoken 时退化为
    `len(text) // 2`（app/core/token.py），而 **tiktoken 不在依赖里** —— 降级分支才是
    打包版的常态。写死长度会让判据静默失效：150,000 字符在本机是 75,000 token，与
    86,473 的预算只差 14%，于是「必须被截断」的断言悄悄变成「恰好没被截断」。
    """
    per = max(_estimate_tokens(unit * 1000) / 1000, 1e-6)   # token / 重复单元
    return unit * int(budget * 2 / per)                     # 2× 预算 → 留足余量

# ═══════════════════════════════════════════════════════════════
# 0. 常量同源自证 —— 预算里的 output 必须等于请求真正发出的 max_tokens
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：把 generator.MAX_TOKENS 改成 32768 而 budget 仍按 65536 算 →
# 等式不闭合（实际输出更小，但不是我们算的那个数）→ 本断言红。
# 这是「成对常量必须同源」（项目模式 #39）在本模块的落点。

from app.generation import generator as _generator
from app.llm import service as _llm_service

assert GENERATION_MAX_TOKENS == _generator.MAX_TOKENS, (
    f"budget.GENERATION_MAX_TOKENS={GENERATION_MAX_TOKENS} 与 "
    f"generator.MAX_TOKENS={_generator.MAX_TOKENS} 不同源——预算等式失去意义"
)
assert GENERATION_MAX_TOKENS == _llm_service.MAX_TOKENS, (
    f"与 llm.service.MAX_TOKENS={_llm_service.MAX_TOKENS} 不同源"
)
# 闸门门槛必须由三个分量导出，不能是另一处写死的数
assert MIN_MULTI_CONTEXT_WINDOW == GENERATION_MAX_TOKENS + SAFETY_MARGIN + MIN_MULTI_INPUT
print(f"PASS: 输出上限三处同源（budget / generator / llm.service = {GENERATION_MAX_TOKENS}）")

# ═══════════════════════════════════════════════════════════════
# 1. single 逐值锁定（零回归）
# ═══════════════════════════════════════════════════════════════
#
# 期望值用**字面量**写（0.8 / 2000 / 16384），不是调函数算的——这样有人改了
# budget.py 里的常量或系数，此处立刻红。
# 对照实验：把 single 也「统一」成 multi 的公式 → 单文档截断阈值从 86,473 变成
# 63,536，既有用户突然看不到后半篇 → 红。

SINGLE_EXPECT = {
    32000:   int(32000 * 0.8) - 2000 - 16384,      # 7,216
    131072:  int(131072 * 0.8) - 2000 - 16384,     # 86,473
    200000:  int(200000 * 0.8) - 2000 - 16384,     # 141,616
    1000000: int(1000000 * 0.8) - 2000 - 16384,    # 781,616
}
for _cw, _expect in SINGLE_EXPECT.items():
    _got = single_input_budget(_cw)
    assert _got == _expect, f"single(cw={_cw}) = {_got}，应为 {_expect}"
assert single_input_budget(131072) == 86473, "128k 窗口的单文档可用输入必须是既有的 86,473"
print(f"PASS: single 逐值等于既有字面量（128k → {single_input_budget(131072)}）")

# 极小窗口走「先不乘 0.8」的兜底分支（既有实现就有的语义，原样保留）
# int(20000*0.8) - 2000 - 16384 = -2384 <= 0 → 退化
assert single_input_budget(20000) == 20000 - 2000 - 16384 == 1616, \
    "算成非正数时必须退化为 cw - margin - reserve（既有分支）"
print("PASS: 极小窗口走 <= 0 兜底分支")

# ═══════════════════════════════════════════════════════════════
# 2. 与 ContentPreparer 真同源（端到端，不是公式对公式）
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：content_preparer 保留自己的硬编码公式、只有 budget.py 变了 →
# 下面这条从截断标记里读出的实际额度会与 single_input_budget 不符 → 红。
# 只断言「都等于某个数」是不够的，必须证明**它真的调了同一个函数**。

from app.generation.content_preparer import ContentPreparer

_CW = 131072
_long = _overlong(single_input_budget(_CW))
_truncated, _was = ContentPreparer._truncate_text(_long, "dashscope", None, _CW)
assert _was, "超长文本必须被截断"
_m = re.search(r"约 (\d+) tokens", _truncated)
assert _m, f"截断标记里应有实际额度：{_truncated[-80:]!r}"
assert int(_m.group(1)) == single_input_budget(_CW), (
    f"_truncate_text 实际用的额度 {_m.group(1)} ≠ single_input_budget({_CW})"
    f"={single_input_budget(_CW)} —— 两处没有同源"
)
print(f"PASS: _truncate_text 实际额度 == single_input_budget（{single_input_budget(_CW)}）")

# ═══════════════════════════════════════════════════════════════
# 3. multi 闭合 + 两条上限取小
# ═══════════════════════════════════════════════════════════════

for _cw in (131072, 200000, 1000000):
    _b = multi_input_budget(_cw)
    assert _b + GENERATION_MAX_TOKENS + SAFETY_MARGIN <= _cw, (
        f"cw={_cw} 下 multi 预算不闭合：{_b} + {GENERATION_MAX_TOKENS} + {SAFETY_MARGIN}"
        f" = {_b + GENERATION_MAX_TOKENS + SAFETY_MARGIN} > {_cw}"
    )
    assert _b > 0
print("PASS: multi 在 128k / 200k / 1M 下全部闭合（input + output + margin ≤ cw）")

# 取小 —— 两个方向都要验，只验一边会漏掉另一种写法的错
assert multi_input_budget(131072) == 131072 - GENERATION_MAX_TOKENS - SAFETY_MARGIN == 63536, \
    "128k 下应受**输出预留**限（0.5×cw = 65,536 更宽）"
assert multi_input_budget(200000) == int(200000 * RELIABILITY) == 100000, \
    "200k 下应受 **0.5 可靠度**限（cw−67536 = 132,464 更宽）"
assert multi_input_budget(1000000) == 500000, "1M 下受 0.5 限"
print("PASS: multi 取两条上限中更紧的一条（128k 受输出预留限 / 200k 受 0.5 限）")

# 只写 int(cw×0.5) 的实现：128k 下会算 65,536 > 63,536（比真实可输入多 2000）
assert multi_input_budget(131072) < int(131072 * RELIABILITY), \
    "取小公式在 128k 下必须严格小于 0.5×cw，否则漏了输出预留这一项"
# 只写 cw−65536−2000 的实现：200k 下会算 132,464，冲破 0.5 线
assert multi_input_budget(200000) <= int(200000 * RELIABILITY), \
    "取小公式在 200k 下不得超过 0.5×cw，否则输出预留把输入顶到不可靠区间"
print("PASS: 两种单边写法都会被上面两条判红")

# ═══════════════════════════════════════════════════════════════
# 4. 窗口闸门 —— 不足时拒绝，不静默取负
# ═══════════════════════════════════════════════════════════════
#
# 对照实验：漏掉闸门 → cw=100000 时 min() 取到 100000−67536 = 32,464，
# 「看起来是个合理的数字」但连一篇论文（≈3.5 万 token）都装不下 → 后续静默截断。
# 断言点必须包含「抛错」，只断 `multi_capacity_ok is False` 的话，
# 「先算一次再拒绝」的实现照样通过。

assert not multi_capacity_ok(100000), "100k 窗口不足以做多文档综述"
assert not multi_capacity_ok(MIN_MULTI_CONTEXT_WINDOW - 1)
assert multi_capacity_ok(MIN_MULTI_CONTEXT_WINDOW), "门槛值本身应当通过（含等于）"
assert multi_capacity_ok(131072)
assert MIN_MULTI_CONTEXT_WINDOW == 102536, f"门槛应为 102,536，实际 {MIN_MULTI_CONTEXT_WINDOW}"

try:
    multi_input_budget(100000)
    raise AssertionError("窗口不足时应抛 ValueError，而不是返回一个（可能是负的）额度")
except ValueError as e:
    assert "不足" in str(e), f"错误信息应说明窗口不足：{e}"
print(f"PASS: 窗口闸门在 {MIN_MULTI_CONTEXT_WINDOW} 处生效（不足即抛错，不返回负额度）")

# ═══════════════════════════════════════════════════════════════
# 5. 分派
# ═══════════════════════════════════════════════════════════════

assert input_budget_for(131072, "single") == single_input_budget(131072)
assert input_budget_for(131072, "multi") == multi_input_budget(131072)
assert input_budget_for(131072) == single_input_budget(131072), "缺省 profile 必须是 single"
print("PASS: 分派正确（缺省 single）")

# ═══════════════════════════════════════════════════════════════
# 6. 现状自证 —— single 的等式**不闭合**（决策 D8）
# ═══════════════════════════════════════════════════════════════
#
# ⚠️ 这条断言的方向是**反的**，它刻意断言「当前不闭合」。
#
# 它的作用等价于 pytest 的 `xfail(strict=True)`——而且这里必须手写：本项目的后端
# 测试是脚本式（模块级 assert，`python tests/x.py`），pytest **不会收集**模块级
# 断言，`@pytest.mark.xfail` 挂上去也永不生效（正是「断言存在 ≠ 断言有效」）。
#
# 价值：把「single 低估了 49152」从一句注释变成一条**会失败**的测试。哪天有人把
# SINGLE_OUTPUT_RESERVE 改成 65536，它立刻红，并在信息里给出该做什么。

_cw = 131072
_single = single_input_budget(_cw)
assert _single + GENERATION_MAX_TOKENS + SAFETY_MARGIN > _cw, (
    "single 的等式竟然**闭合**了 —— 说明 SINGLE_OUTPUT_RESERVE 已被改成 "
    f"{_cw - _single - SAFETY_MARGIN}（此前是 {SINGLE_OUTPUT_RESERVE}）。\n"
    "这是好事，但请把本断言**反转**成「必须闭合」，并同步更新三处：\n"
    "  ① docs/CHANGELOG（这是一次行为变更：单文档可用输入会变）\n"
    "  ② content_preparer.py 的 MAX_TOKENS 注释与 design 文档 §14.10 D8\n"
    "  ③ 确认既有用户能接受单文档可用输入的下降，再发版"
)
print(f"PASS: single 现状自证 —— 已知不闭合（{_single} + 65536 + 2000 = "
      f"{_single + GENERATION_MAX_TOKENS + SAFETY_MARGIN} > {_cw}），且改动会被上面这条挡住")

# ═══════════════════════════════════════════════════════════════
# 7. cw 来源 —— 预设窗口必须真的进得来（否则预算建立在错的窗口上）
# ═══════════════════════════════════════════════════════════════
#
# 【事实】`resolve_context_window` 是四层解析，但**第 2 层（llm_models 预设）只在
# 调用方真的调它时才生效**。以 dashscope/qwen-vl-ocr-latest 为例：预设 38,192 /
# provider 兜底表 1,000,000 —— **高估 26 倍**，按兜底算的预算必然超窗。
#
# 2026-10-02 的修法：把 `cw` 收口进 `resolve_llm_config`（它本来就已经做
# 「model_id 为空则回退 scene_models[scene]」），调用方不再各写一遍 ——
# 此前 generate.py 在调完 resolve_llm_config 之后**又**手工赋一次，
# 传的是**原始** req.model_id（用户用「场景默认模型」时是 None）→ 把正确值覆盖成兜底值。
#
# 对照实验：删掉 model_resolver.py 里 `llm_config["model_context_window"] = ...`
# 那一行 → 生成侧落到 provider 兜底表 → 本节红。

from app.core.model_resolver import resolve_context_window, resolve_llm_config
from app.core.token import DEFAULT_CONTEXT

_CFG = {
    "enabled_models": [{"id": 7, "provider_id": "dashscope",
                        "model_name": "qwen-vl-ocr-latest", "capabilities": ["text"]}],
    "providers": [{"id": "dashscope", "api_key": "sk-test", "base_url": ""}],
    "llm": {"provider": "dashscope", "api_key": "sk-test"},
}

_preset = resolve_context_window(_CFG, 7)
_fallback = DEFAULT_CONTEXT["dashscope"]
assert _preset == 38192, f"第 2 层预设应为 38,192，实际 {_preset}"
assert _fallback == 1000000, f"第 3 层兜底表应为 1,000,000，实际 {_fallback}"
assert _preset != _fallback, "前置：这个样本必须让两层取值不同，否则下面的断言没有区分力"

# 收口后：`resolve_llm_config` **必须**把完整四层解析的结果填进来。
# ⚠️ 断言是「等于 _preset」而非「字段存在」——只断言存在的话，填成兜底值也照样通过。
_llm_cfg = resolve_llm_config(_CFG, 7)
assert _llm_cfg.get("model_context_window") == _preset, (
    f"resolve_llm_config 应填四层解析的 cw（{_preset}），"
    f"实际 {_llm_cfg.get('model_context_window')} —— "
    "填错或没填会让生成侧落到 provider 兜底表（本样本下高估 26 倍）"
)

# 传进去的 cw 必须真的被用（而不是被兜底表覆盖）
assert single_input_budget(_preset) != single_input_budget(_fallback), \
    "38,192 与 1,000,000 的预算必须不同（否则本节的判别无效）"
_verylong = _overlong(single_input_budget(_preset))
_got_trunc, _ = ContentPreparer._truncate_text(_verylong, "dashscope", None, _preset)
_m2 = re.search(r"约 (\d+) tokens", _got_trunc)
assert _m2 and int(_m2.group(1)) == single_input_budget(_preset), (
    f"传入的 cw={_preset} 未被采用：实际额度 {_m2.group(1) if _m2 else '?'}"
    f" ≠ single_input_budget({_preset})={single_input_budget(_preset)}"
)
print(f"PASS: cw 来源 —— 预设 {_preset} 与兜底 {_fallback} 差 "
      f"{_fallback // _preset} 倍，传入值被真实采用")

# ═══════════════════════════════════════════════════════════════
# 8. 调用点对称性（AST 全库扫描）—— 模式 #16 在本轮的防线
# ═══════════════════════════════════════════════════════════════
#
# ≥2 个 ContentPreparer 构造点（非流式 / 流式），两条都必须：
#   ① 显式传 budget_profile=（漏传 → 合成文档按 single 截断，刚建好的综述被二次截断）
#   ② 所在函数体内出现 **cw 的来源**（漏了 → 预算落在 provider 兜底表上，见第 7 节）
#
# ⚠️ ② 接受**两种**合法来源（2026-10-02 收口后）：
#      · `resolve_llm_config` —— 非流式路径：cw 已由它在内部填好，构造点直接复用
#      · `resolve_context_window` —— 流式路径：prep_cfg 是手工构造的，必须自己填
#    判据写成「二者之一」而非写死某一个——收口把「哪个函数」变成了实现细节，
#    但「cw 必须来自四层解析」这条性质不变，这才是本节真正要守的东西。
# 「漏改一处不报错、静默走默认值」正是这条要拦的形态。

import ast, pathlib

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"
_ctors, _bad_profile, _bad_cw = [], [], []
_CW_SOURCES = {"resolve_llm_config", "resolve_context_window"}


def _callee_name(node: ast.Call) -> str:
    fn = node.func
    return getattr(fn, "id", None) or getattr(fn, "attr", None) or ""


for _py in sorted(_APP.rglob("*.py")):
    _tree = ast.parse(_py.read_text(encoding="utf-8"))
    for _fn in ast.walk(_tree):
        if not isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        _inner = list(ast.walk(_fn))
        _ctor_lines = []
        for _n in _inner:
            if isinstance(_n, ast.Call) and _callee_name(_n) == "ContentPreparer":
                _where = f"{_py.relative_to(_APP.parent)}:{_n.lineno}"
                _ctors.append(_where)
                _ctor_lines.append(_where)
                if "budget_profile" not in {k.arg for k in _n.keywords}:
                    _bad_profile.append(_where)
        if _ctor_lines and not any(
            isinstance(_n, ast.Call) and _callee_name(_n) in _CW_SOURCES
            for _n in _inner
        ):
            _bad_cw.append(f"{_py.relative_to(_APP.parent)}:{_fn.lineno} {_fn.name}")

assert _ctors, "扫不到任何 ContentPreparer 构造点 —— 检查条件失效了（模式 #39）"
assert len(_ctors) >= 2, f"构造点应 ≥2（流式 + 非流式），实际 {_ctors}"
assert not _bad_profile, f"以下构造点漏传 budget_profile=，合成文档会被二次截断：{_bad_profile}"
assert not _bad_cw, f"以下函数的构造点旁没有 cw 来源（resolve_llm_config / resolve_context_window），预算会落在兜底表上：{_bad_cw}"
print(f"PASS: ContentPreparer 构造点对称性（{_ctors}）")
# ⚠️ 对照（可验证有区分力）：删掉 generate.py 流式端点 prep_cfg 里的
#    "model_context_window" 行 → 本节不红（它扫的是函数体内是否有那个**调用**）；
#    删掉**整行 resolve_context_window(...)** → 节 8 红。两者的差别正是本节的边界：
#    它拦得住「整条链路没接」，拦不住「接了但没塞进传参的 dict」——后者由节 7 + 人工复核覆盖。
#    ⚠️ 非流式路径的同类风险在 2026-10-02 收口后**变小**了：cw 由 resolve_llm_config
#    直接写进 llm_config，而 llm_config 整个对象被传进构造点，没有「填了但没塞进去」这一步。

# ═══════════════════════════════════════════════════════════════
# 9. stuff_window —— 反解必须**恰好**（多一个 token 就装不下）
# ═══════════════════════════════════════════════════════════════
#
# 它由 `multi_input_budget` 的 `min()` 反解而来，语义是「让这 N 篇**原样全部装下**
# 所需的窗口」。判据不能只写「>= 某个数」（随便一个够大的数都满足）——必须**两侧
# 都钉**：这个窗口装得下，少 1 就装不下。这才是「反解」而不是「估个大的」。
#
# ⚠️ 对照实验（写作时最容易犯的两个错）：
#   ① 只写 `total / RELIABILITY` 一项 → 小总量下漏掉 `total + 67536` 那条上限 → 红
#   ② 只写 `total + 67536` → 大总量下漏掉可靠度上限 → 红
# （两条上限的**主导区间不同**，下面刻意取了两侧各一个样本。）

for _t, _expect in {
    0:       67536,      # 空集 → 输出预留那条主导（total/0.5 = 0）
    20000:   87536,      # 小总量 → 输出预留主导（40,000 vs 87,536）
    60000:   127536,     # 仍是输出预留主导（120,000 vs 127,536）—— 交界在 67,536
    67536:   135072,     # 两条相等的交界
    100000:  200000,     # 大总量 → 可靠度主导（200,000 vs 167,536）
    200000:  400000,
}.items():
    _got = stuff_window(_t)
    assert _got == _expect, f"stuff_window({_t}) = {_got}，应为 {_expect}"
print("PASS: stuff_window 逐值正确（两条上限按总量大小分别主导）")

# ── 恰好性：装得下 ∧ 少 1 就装不下 ──
# 这是本节的核心判据。只验一侧的实现在另一侧必然露馅。
_checked = 0
for _t in (20000, 35000, 50000, 67536, 100000, 200000, 500000):
    _w = stuff_window(_t)
    if not multi_capacity_ok(_w):
        # 小总量下 stuff_window **低于闸门门槛**（见本节末「不能当闸门判据」）——
        # 预算函数此时拒绝求值，故「恰好性」只能在门槛之上那一段验证。
        assert _w < MIN_MULTI_CONTEXT_WINDOW
        continue
    _checked += 1
    _ok = multi_input_budget(_w)
    assert _ok >= _t, f"stuff_window({_t})={_w} 竟然装不下 {_t}（预算只有 {_ok}）"
    # 少 1 tok 的窗口：两个分支都必须够不着 total
    _w1 = _w - 1
    _ok1 = multi_input_budget(_w1) if multi_capacity_ok(_w1) else -1
    assert _ok1 < _t, (
        f"stuff_window({_t})={_w} 不是**最小**的：窗口 {_w1} 也装得下（预算 {_ok1}）"
        f"—— 反解多算了，用户会被推荐一个不必要的大窗口"
    )
assert _checked >= 5, f"恰好性样本太少（{_checked} 个），判据覆盖面不足"
print("PASS: 恰好性 —— w 装得下、w-1 装不下（反解不是「估个大的」）")

# ── `RELIABILITY` 必须真参与（否则「1M 窗口下 20 篇也全塞」）──
assert stuff_window(400000) == 800000, \
    f"大总量下应由 1/RELIABILITY=2 倍主导：{stuff_window(400000)}"
# 总量还不到「输出预留」的量级时，输出预留那条必须接管（而不是 2 倍就完事）
assert stuff_window(50000) == 50000 + GENERATION_MAX_TOKENS + SAFETY_MARGIN == 117536, \
    "总量 < 67,536 时输出预留必须成为更紧的那条（2×50,000 = 100,000 是不够的）"
print("PASS: 可靠度因子与输出预留都真在公式里（换了任一项此处立刻红）")

# ── ⚠️ `stuff_window` **不能**当闸门判据 ──
# 小总量下它小于 `MIN_MULTI_CONTEXT_WINDOW`——但那次合成**照样需要 128k 窗口**，
# 因为闸门约束的是「能不能做多文档综述」，与「这次有多少篇」无关。
# Web端的「换个窗口更大的模型吧」推荐若直接拿 stuff_window 当门槛，
# 会推荐出一个**窗口不足**的模型。故推荐值必须取两者较大者。
_small = stuff_window(1000)
assert _small < MIN_MULTI_CONTEXT_WINDOW, (
    f"前置失效：本断言要求小总量下 stuff_window({_small}) 小于门槛"
    f"（{MIN_MULTI_CONTEXT_WINDOW}）—— 若两者关系变了，请重新评估这条建议"
)
assert max(_small, MIN_MULTI_CONTEXT_WINDOW) == MIN_MULTI_CONTEXT_WINDOW, \
    "推荐模型时的窗口要求 = max(stuff_window, MIN_MULTI_CONTEXT_WINDOW)"
print(f"PASS: stuff_window({1000})={_small} < 门槛 {MIN_MULTI_CONTEXT_WINDOW} —— "
      f"推荐门槛必须取 max(两者)，否则会推荐窗口不足的模型")

# ── 单调性：多一篇进来，所需窗口不该变小 ──
_prevs = [stuff_window(t) for t in range(0, 200000, 25000)]
assert _prevs == sorted(_prevs), f"stuff_window 必须单调不减：{_prevs}"
print("PASS: 单调不减（总量增加 → 所需窗口不会变小）")

print("\n全部通过：budget 统一预算函数")
