"""对话侧检索的查询改写 —— 把口语问题换成库里可能出现的说法。

## 它解决的是什么问题

**词汇鸿沟，不是噪声。** 用户问「一边猜一边验证的那个技术」，库里写的是
「投机解码 草稿模型」—— 同一件事，一个词都不重叠。手册式的处理是「去掉口语词」，
而实测那件事**是负收益**：本系统的检索索引侧是二字组、查询侧是二字组 + OR，
删词等于直接砍 OR 的项（召回），而它想治的「噪声词权重过高」BM25 的 IDF 本来就在做。
数字与逐题对照见 [`_experiments/06-query-rewrite/`](../../../_experiments/06-query-rewrite/README.md)。

## 形态：原词 + 改写一起进 RRF

不是「用改写替掉原词」。44 题融合路实测：

| 臂 | H@1 | H@5 | MRR@10 |
|---|---|---|---|
| 原词 | 52.3% | 88.6% | 0.684 |
| 改写（3 条） | 77.3% | 95.5% | 0.846 |
| **原词 + 3 条改写** | 75.0% | **97.7%** | 0.844 |

H@5 最高的那一路留原词的理由不止分数：**改写失手时原词自动兜底**
（有一题原词第 3、改写第 7，合并后仍在 H@5 内）。而原词那次检索本来就要跑，
多一路几乎零成本（RRF 只用排名不用分数，`1/(k+rank)` 跨路相加）。

## 为什么只挂在对话侧

改写要花一次 LLM 调用（实测中位 **1.06 秒**）。对话里用户本来就在等一次工具调用，
这一秒藏在里面；文档库搜索框是**逐字联想**，每敲一次都多一秒直接毁掉输入体验。
开关是 `config["search"]["rewrite_queries"]`，**默认关**。

## 关思考是无条件的，不是「模型支持才关」

`LLMService.call(disable_thinking=True)` 无条件下发 `thinking: disabled`。实验第一版
写成 `disable_thinking=llm.supports_reasoning` —— 那个能力位标的是「认不认
`reasoning_effort`」，而 DeepSeek 服务端**思考默认开启**且能力位没标 ⇒ 没进关闭分支
⇒ 推理链把 300 token 预算全烧光 ⇒ `content` 空串，现象看起来像「解析器坏了」。
一并记下的还有延迟：不关思考中位 2596ms 且**随机失败**，关掉后 1059ms、0/44 退化。
"""

from __future__ import annotations

import asyncio

from app.core import logging as log
from app.core.text_utils import parse_json_object
from app.llm import CancelledError as LlmCancelledError
from app.llm import LLMService

#: 改写调用自己的超时（秒）。
#:
#: ⚠️ **独立一层，不共用 `LLMService` 内部的总超时**：这是 `search_docs` 的前置一步，
#: 用户正等在对话里，模型挂起时宁可退化用原词检索也不能让一次工具调用卡几分钟。
#: 实测中位 1.06s，60s 是「模型卡住」的兜底而非预期值（与追问侧同值，同类性质）。
REWRITE_TIMEOUT = 60.0

#: 最多取几条改写。3 是实验固定值 —— 更少/更多都没测过，别凭直觉调。
MAX_QUERIES = 3

#: 单条检索式长度上限。超长**截断**而非丢弃：截断后的前半段仍是一段可用的中文
#: 查询串，而丢弃等于静默少一路召回（用户与日志都看不出来）。
MAX_QUERY_CHARS = 40

#: 改写 prompt —— **与 `_experiments/06-query-rewrite/eval_rewrite.py` 逐字一致**。
#:
#: ⚠️ `keywords` 字段照旧要求但**实现里不使用**：关键词臂被实验否决（融合路 H@1
#: 68.2% vs 改写 77.3%，且在「Matt Pocock」这类专有名词题上更差 —— 抽取会把库里
#: 唯一的抓手当噪声弱化）。保留这个字段的唯一理由是**prompt 是被实验测过的那个**：
#: 改 prompt 就必须重跑实验，而多要一个不用字段只多约 20 token。
REWRITE_PROMPT = """你是检索系统的查询改写器。用户在一套个人知识库里找资料，库里是自己导入的网页、视频、文档转录，内容是中文。

用户的问题：
{q}

请输出一个 JSON 对象，只有两个字段：
- "queries": 2–3 条检索式。用资料里可能出现的中文术语重述这个问题，覆盖不同侧面；不要口语化，不要加书名号、引号、问号。
- "keywords": 一行关键词，用空格分隔。只保留实词（名词、术语），去掉「怎么」「为什么」「有哪些」「如何」这类疑问词和虚词。

只输出 JSON，不要解释，不要代码块围栏。"""


async def rewrite_queries(llm: LLMService, question: str, *,
                          cancel_event=None) -> list[str]:
    """把口语问题改写成最多 3 条检索式。拿不到就返回 `[]`。

    **失败一律返回空列表而不是抛错**：改写是锦上添花的一步，它失败时不该让
    「搜文档」这个动作失败 —— 调用方拿到 `[]` 就是按原词检索，那本来就是原设计。
    这不违反「失败显式化」：那条纪律管的是**用户能察觉的**降级（索引坏了要在界面上
    说，因为用户需要去修）；而这里用户看不出区别、也无事可做，报出来只是噪音。
    改写没产出会记一条 warning（**带模型原始输出**）—— 「模型没按格式答」与
    「我们没读对」是两种失败，必须能被分开看见。

    ⚠️ **绝不 `aclose()` 传进来的 `llm`**：连接池归调用方所有（同 `web_search.py`）。
    """
    q = (question or "").strip()
    if not q:
        return []
    # 用户已经按了停止：不再花钱发起一次新调用（在途的取消归 `LLMService` 管）。
    if cancel_event is not None and cancel_event.is_set():
        return []

    try:
        text = await asyncio.wait_for(
            llm.call(REWRITE_PROMPT.replace("{q}", q), max_tokens=400,
                     temperature=0.2, disable_thinking=True),
            timeout=REWRITE_TIMEOUT)
    except (asyncio.CancelledError, LlmCancelledError):
        raise                       # 用户取消不是「改写失败」，照常向上传播
    except Exception as e:          # noqa: BLE001 —— 任何失败都退化成原词检索
        log.warning("查询改写失败，按原词检索", err=f"{type(e).__name__}: {e}"[:200])
        return []

    obj = parse_json_object(text)
    raw = obj.get("queries") if isinstance(obj, dict) else None
    out: list[str] = []
    seen = {q}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        s = item.strip().strip("\"'「」《》").strip()[:MAX_QUERY_CHARS]
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= MAX_QUERIES:
            break

    if not out:
        # ⚠️ 把原始输出一并留下：见 docstring 末尾那两种失败的区分。
        log.warning("查询改写未产出可用检索式", raw=text[:200])
    return out
