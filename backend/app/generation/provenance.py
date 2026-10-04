"""产物自证头（`type == "artifact"`）的**装配层** —— 把调用方手头的上下文翻译成四条。

与 `core/transcript_meta.py` 的分工：

| 层 | 管什么 | 依赖 |
|---|---|---|
| `core/transcript_meta.py` | **形状**：信封 + JSON + 字符串清洗 | 纯编解码，不认业务字段名 |
| 本模块 | **取值**：从哪拿到这四条 | `core/types`、prompts 包、调用方上下文 |

⚠️ 单独成模块的理由与 meta 编解码同源：**写方必须同源**。产物有多个落点
（generate 的 notes/polished、epub 的逐章产物、process 的图片处理），各拼一遍
JSON 的话字段名与取值口径必然漂移——而漂移的后果是产物离开 `.limo` 之后自证信息
残缺，正是本设计要修的缺陷（文档《产物复用与获取渠道约定》§2）。

⚠️ **`identity.version` 刻意不填**：真实版本号由 `store.save_markdown` 在加锁区内
算出（`top + 1`），写盘那一刻调用方**还不知道**。填预估值就是写假数据，而假数据
比没有更坏；读路径（`QueryService.get_artifact`）用 index 里的真版本补全。
"""

from datetime import datetime
from typing import Any

from app.core.transcript_meta import build_artifact_meta
from app.generation.prompts import PROMPT_VERSION


def compact_params(**kwargs) -> dict:
    """丢掉「没开 / 没填」的键再进 meta。

    meta 是给人（和其他程序）读的，一串 `"toc": false` 只是噪音——「不记」与
    「记 false」信息量完全相同。

    ⚠️ `False` 必须用 `is` 单独判，不能靠 `in (...)`：`0 == False` 成立，用相等
    判断会把数值参数里的 `0` 一起丢掉（`_EMPTY` 风格的元组匹配正是这么栽的）。
    """
    return {
        k: v for k, v in kwargs.items()
        if v is not None and v is not False and v != "" and v != [] and v != {}
    }


def artifact_meta(
    kind: str,
    *,
    doc_id: str,
    mode: str = "",
    source_type: str = "",
    source_url: str = "",
    model: str = "",
    engine: str = "",
    params: dict | None = None,
) -> dict:
    """装配一份产物的自证头。

    | 条 | 内容 | 来源 |
    |---|---|---|
    | ① 从哪来 | `source` | `doc_id`（= transcript id）+ 转录条目的 `sourceType` / `sourceUrl` |
    | ② 怎么来的 | `generated` | `model`（或 `engine`）+ `PROMPT_VERSION` + `params` + 时间 |
    | ③ 是什么 | `identity` | `mode`（version 见模块 docstring） |
    | ④ 可信度边界 | `trust` | **结构先留**：哪些是材料里说的、哪些是综合判断，需提示词侧配合标注 |

    `model` 与 `engine` 是两种不同的「生产者」，都为空时只写 `params`：生成产物记
    `model`（实际调用的 LLM），图片处理记 `engine`（OCR 引擎名，或 LLM 优先时的模型名）
    ——**两者不是同一件事**，混用一个字段会让读的人分不清「这是引擎确定性输出还是
    模型生成」。

    `kind` 是产物种类（`notes` / `polished` / `process`），与信封标识
    `type == "artifact"` 分开——前者答「这是什么产物」，后者答「这段 meta 由谁解析」。
    """
    source: dict = {"docId": doc_id}
    if source_type:
        source["sourceType"] = source_type
    if source_url:
        source["sourceUrl"] = source_url

    generated: dict = {
        "promptVersion": PROMPT_VERSION,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "params": dict(params or {}),
    }
    if model:
        generated["model"] = model
    if engine:
        generated["engine"] = engine

    return build_artifact_meta(
        kind,
        source=source,
        generated=generated,
        identity={"mode": mode},
        trust={},
    )


def markdown_meta(mv: Any, tx: dict, *,
                  formats: list[str] | None = None,
                  reasoning_effort: str = "",
                  extra_params: dict | None = None) -> dict:
    """从「一个 `MarkdownView` + 它的 transcript 条目」装配自证头。

    这是**取值口径的唯一落点**：`save_markdown` 在全仓有十余处调用（非流式双/单产物、
    流式双/单产物、chunk 超时、取消、epub 逐章），每处手拼一遍必然漂移——而自证信息
    的漂移是**静默**的：产物照样生成，只是离开 `.limo` 之后残缺（正是文档 §2 的现状）。

    `mv` 用鸭子类型标注（不 import `core.types`）：本函数只用到 `.mode` / `.style` /
    `.transcript_id` / `.generation_model` 四个属性，为一个注解引入类型依赖不划算。

    ⚠️ `generation_model` 由生成侧（`generator.py`）就地填——只有它和 `LLMService`
    知道实际用了哪个模型，落盘侧拿不到。
    """
    mode = mv.mode.value if hasattr(mv.mode, "value") else str(mv.mode)
    style = mv.style.value if hasattr(mv.style, "value") else str(mv.style)
    return artifact_meta(
        mode,
        doc_id=mv.transcript_id,
        mode=mode,
        source_type=tx.get("sourceType", "") or "",
        source_url=tx.get("sourceUrl", "") or "",
        model=getattr(mv, "generation_model", "") or "",
        params=compact_params(
            formats=list(formats or []),
            style=style,
            reasoningEffort=reasoning_effort,
            **(extra_params or {}),
        ),
    )
