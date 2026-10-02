"""
模型上下文预算 — 创建侧与生成侧共用的唯一来源。

**为什么需要这个模块**：本项目此前有两套互不相同的预算算法——对话 / 参考压缩用
`× 0.7`，`ContentPreparer` 用 `int(cw×0.8) − 2000 − 16384`。多文档合成要求
「创建阶段按预算定尺寸」与「生成阶段截断」用的是**同一个**函数，否则小窗口模型上
会二次截断、后几篇凭空消失（设计文档 §4.4.5）。

⚠️ 两个 profile 的等式**刻意不同源**，理由各不相同：

  | profile | 等式 | 状态 |
  |---|---|---|
  | `single` | `int(cw×0.8) − 2000 − 16384`（`<= 0` 时退化） | 与既有实现**逐值等价**，含一处已知低估（见 `SINGLE_OUTPUT_RESERVE`） |
  | `multi`  | `min(int(cw×0.5), cw − 65536 − 2000)` | 新路径，等式**必须闭合** |

多文档侧**绝不照抄** single 的形态——这不是「两套预算」的退步，而是「旧的不动、
新的做对」；两者的 `cw` 来源已经统一（都由调用方从 `resolve_context_window()` 取，
见 `content_preparer` 的传参修正）。
"""

import math

# 生成侧真实发出的 max_tokens（generator.py:24 / llm/service.py:19 同值）。
# multi 的 output_budget 必须等于它，否则预算等式不闭合。
GENERATION_MAX_TOKENS = 65536

# prompt 模板 / system / 结构标记的开销，与 ContentPreparer 既有值同源
SAFETY_MARGIN = 2000

# `single` profile 的输出预留。
#
# ⚠️ **已知低估，且刻意保持**：真实 max_tokens 是 65536，此处只留 16384，相差 49152，
# 因此 single 的等式**不闭合**——128k 窗口下可用输入 86,473 + 实际可产出 65,536
# = 152,009 > 131,072。这是**既有缺陷，不是本模块引入的**。
#
# 为什么不顺手改对：改了会让所有既有用户的单文档可用输入从 86,473 掉到 37,321
# （**−57%**），是独立于本方案的行为变更，且「模型是否真会吐满 65536」尚无实测。
# 故保持现状，并由 `tests/test_budget.py` 用 `xfail(strict=True)` 把「不闭合」
# 钉成一条**有名字的测试**——哪天有人改成 65536，它会立刻 XPASS 失败（决策 D8）。
SINGLE_OUTPUT_RESERVE = 16384

# multi profile 的可靠度因子。
#
# ⚠️ **初始安全默认值，不是经验最优值**（设计 §5.2.2）。依据只有「有效窗口通常为
# 标称的 50~70%」这一区间观测，**没有任何研究能推出「LiMo 的最佳参数就是 0.5」**。
# 将来靠本项目自己的实测调整——不要把工程经验值包装成科学结论。
RELIABILITY = 0.5

# 一次多文档综述至少要能装下的输入量（≈ 一篇论文；实测语料 2~3.5 万 token/篇）。
# ⚠️ 这是从语料统计**推来**的，不是对「五段结构综述最少需要多少输入」的测量。
MIN_MULTI_INPUT = 35000

# 窗口闸门：`cw` 低于此值时直接拒绝合成，而不是让 min() 取到负值。
# = 65536 + 2000 + 35000 = 102,536 ⇒ 实务上即「需要 128k 窗口」。
MIN_MULTI_CONTEXT_WINDOW = GENERATION_MAX_TOKENS + SAFETY_MARGIN + MIN_MULTI_INPUT


def single_input_budget(context_window: int) -> int:
    """单文档可用输入 —— 与 `ContentPreparer._truncate_text` 的既有算法逐值等价。

    `int(cw×0.8)` 之后减去 prompt 开销与输出预留；算成非正数时退化为「先不乘 0.8」，
    这是既有实现就有的兜底分支，原样保留（改动它属于另一件事）。
    """
    available = int(context_window * 0.8) - SAFETY_MARGIN - SINGLE_OUTPUT_RESERVE
    if available <= 0:
        available = context_window - SAFETY_MARGIN - SINGLE_OUTPUT_RESERVE
    return available


def multi_capacity_ok(context_window: int) -> bool:
    """窗口闸门：这个窗口够不够做一次多文档综述。

    ⚠️ 必须在**算预算之前**调用。没有它，`multi_input_budget` 在小窗口下会算出
    负数，而 `min()` 会把这个负数取走——一个「看起来是数字、实际装不下任何东西」
    的额度，后续只会变成静默截断。
    """
    return context_window >= MIN_MULTI_CONTEXT_WINDOW


def multi_input_budget(context_window: int) -> int:
    """多文档可用输入 —— 两条上限**取更紧的一条**。

    推导（设计 §5.2.1），每一步都不是可选的：

    1. `output_budget` 必须等于请求真正带出去的 `max_tokens`（65536），等式才闭合。
    2. 但不能只写 `cw − 65536 − 2000`：200k 窗口下会得到 132,464 ≈ 0.66×cw，
       **反而冲破 `RELIABILITY = 0.5` 这条线**——大窗口下不该因为「输出预留是固定值」
       就让输入无上限地涨。
    3. 故两者取小：`RELIABILITY` 是一条上限，扣除输出预留是**另一条**上限。
    4. 小窗口下第二项为负 → 由 `multi_capacity_ok` 闸门拒绝，**不静默取负**。

    实测取值：128k → 63,536（受输出预留限）；200k → 100,000（受 0.5 限）；
    1M → 500,000（受 0.5 限）。

    `context_window` 不足时抛 `ValueError` 而不是返回负值——调用方应先过
    `multi_capacity_ok`；抛错是纵深防御（静默返回负额度比报错坏得多）。
    """
    if not multi_capacity_ok(context_window):
        raise ValueError(
            f"上下文窗口 {context_window} 不足以做多文档综述"
            f"（需 ≥ {MIN_MULTI_CONTEXT_WINDOW}）"
        )
    return min(
        int(context_window * RELIABILITY),
        context_window - GENERATION_MAX_TOKENS - SAFETY_MARGIN,
    )


def input_budget_for(context_window: int, profile: str = "single") -> int:
    """按 profile 分派可用输入预算。`profile ∈ {"single", "multi"}`。"""
    if profile == "multi":
        return multi_input_budget(context_window)
    return single_input_budget(context_window)


def stuff_window(total_tokens: int) -> int:
    """让这 N 篇**原样全部装下**（即走 Stuff 路径）所需的窗口。

    ⚠️ **它不是「能做这次合成的最小窗口」**——总量超了照样能做，只是改走压缩。
    命名必须精确：用户看到「需要 X 的窗口」时不该以为「窗口不够就不能合成」
    （设计 §17.4 的 `required_window` → `stuff_window` 改名）。

    由 `multi_input_budget` 的 `min()` 反解——两条上限都要满足：
        `cw × RELIABILITY ≥ total`  →  `cw ≥ total / RELIABILITY`
        `cw − 67536 ≥ total`        →  `cw ≥ total + GENERATION_MAX_TOKENS + SAFETY_MARGIN`
    取更紧的一条。
    """
    return max(
        int(math.ceil(total_tokens / RELIABILITY)),
        total_tokens + GENERATION_MAX_TOKENS + SAFETY_MARGIN,
    )
