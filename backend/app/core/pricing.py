"""模型单价查询 + 一次生成的用量折算（元）。

`price_in` / `price_out` 住在 `config["enabled_models"]` 的条目上（设置页可改，
单位是**每百万 token 的元**，见 `routers/model.py::UpdateModelData`）。本模块是
「按 provider + 模型名找单价」与「token 数 × 单价 = 金额」的**唯一实现**——
对话侧（`routers/chat.py`）与生成侧（`generation/generator.py`、
`routers/generate.py`）都要用，各写一份就是模式 #54。

⚠️ **两个单价都配了才算金额。** 只配输入价时把输出按 0 计，会把一次真实消费报成
一个偏小的数——「看起来有数」比「没有数」更坏。缺单价时 `cost` 键**根本不写**，
前端据此显示「只有 token、没有金额」，而不是「约 ¥0.00」。

⚠️ 金额是**单价快照**语义：写进产物自证头之后，用户后来改单价也不会回头改老产物。
那是「当时这次调用花了多少」的记录，不是「按今天的价重算」。
"""

from __future__ import annotations

#: `enabled_models` 条目里单价字段的单位：元 / 百万 token。
PRICE_UNIT = 1_000_000


def prices_for(config: dict | None, provider: str, model: str) -> tuple[float | None, float | None]:
    """按 provider + 模型名查 `(price_in, price_out)`；查不到返回 `(None, None)`。

    匹配的是**已启用模型条目**（`enabled_models`）而不是预设：单价是用户在中转 /
    自定义模型时自己填的，预设里没有。找不到条目不等于出错——用户可能用场景默认
    模型且从没填过价，那时就只是「不知道多少钱」。
    """
    if not config:
        return None, None
    for m in config.get("enabled_models", []) or []:
        if m.get("provider_id") == provider and m.get("model_name") == model:
            return m.get("price_in"), m.get("price_out")
    return None, None


def usage_record(
    prompt_tokens: int | None,
    completion_tokens: int | None,
    *,
    config: dict | None = None,
    provider: str = "",
    model: str = "",
) -> dict:
    """一次生成的用量 → 自证头的 `generated.usage`（`{}` = 拿不到用量，不写这个键）。

    形状：`{"input": 1234, "output": 5678, "cost": 0.0123}`。`input` / `output`
    是 token 数（与 OpenAI 的 `prompt_tokens` / `completion_tokens` 一一对应，这里
    换成人读得懂的名字）；`cost` 是元，**两个单价都配了才出现**（理由见模块头）。
    """
    p = int(prompt_tokens or 0)
    c = int(completion_tokens or 0)
    if not p and not c:
        return {}
    rec: dict = {"input": p, "output": c}
    price_in, price_out = prices_for(config, provider, model)
    if price_in is not None and price_out is not None:
        # 6 位小数：便宜模型（0.5 元/百万）一次几千 token 的金额在 0.001 量级，
        # 4 位会把它抹成 0。
        rec["cost"] = round((p * price_in + c * price_out) / PRICE_UNIT, 6)
    return rec
