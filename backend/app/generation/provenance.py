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

⚠️ **`identity.version` 本模块刻意不填**：真实版本号只有 `store.save_markdown` 在
加锁区内扫磁盘（`{mode}/v{N}.md` 的 N）才知道，装配这一头时调用方**还不知道**。
填预估值就是写假数据，而假数据比没有更坏。**由 store 在 dump 之前注入**
（2026-10-10 起，版本号就是文件名，写盘前已知）——此前那句「读路径用 index 里的
真版本补全」已随版本归档一起废弃：**头才是真相，index 只是缓存**，删了 index
必须还能从头上读出正确的版本号。
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


def source_refs_of(tx: dict) -> list[dict]:
    """合成文档的来源清单 → `[{"n": 1, "docId": …, "title": …}]`；非合成返回 `[]`。

    `[文档 N]` 是**位置**锚点：N 是合成材料里的篇目序号（1 起，与正文里
    `## 来源文档` 的编号、提示词里的 `【文档 i】` 同一个序）。产物离开 `.limo`
    之后，只有把「位置 → 稳定 id」的映射跟着带走，`[文档 2]` 才还答得出是哪篇——
    否则导出物里的锚点只是一串对谁都无意义的数字。

    ⚠️ 标题可能比 id 少（历史合成文档），按位置对齐、缺的就不写 `title` 键；
    **不拿占位文本凑位**（「文档 2」冒充标题会让读的人以为那真是标题）。
    """
    ids = list(tx.get("sourceDocIds") or [])
    if not ids:
        return []
    titles = list(tx.get("sourceTitles") or [])
    refs: list[dict] = []
    for i, doc_id in enumerate(ids, 1):
        ref: dict = {"n": i, "docId": doc_id}
        title = titles[i - 1] if i - 1 < len(titles) else ""
        if title:
            ref["title"] = title
        refs.append(ref)
    return refs


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
    usage: dict | None = None,
    source_refs: list[dict] | None = None,
) -> dict:
    """装配一份产物的自证头。

    | 条 | 内容 | 来源 |
    |---|---|---|
    | ① 从哪来 | `source` | `doc_id`（= transcript id）+ 转录条目的 `sourceType` / `sourceUrl`（+ 合成文档的 `sourceRefs`） |
    | ② 怎么来的 | `generated` | `model`（或 `engine`）+ `PROMPT_VERSION` + `params` + 时间 + `usage`（有才写）|
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
    # 合成产物才有：`[文档 N]` → 稳定 id 的映射。**空列表不写键** —— 非合成文档
    # （占绝大多数）的自证头因此逐字节不变。
    if source_refs:
        source["sourceRefs"] = list(source_refs)

    generated: dict = {
        "promptVersion": PROMPT_VERSION,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "params": dict(params or {}),
    }
    if model:
        generated["model"] = model
    if engine:
        generated["engine"] = engine
    # 这次生成的实际用量（`{"input", "output", "cost"?}`，见 `core/pricing.py`）。
    # **空 dict 不写键**：网关不回 usage / 历史产物就是这么来的，写 `{}` 会让读方
    # 以为「问了、答案是零」，而实际是「根本没问到」。
    if usage:
        generated["usage"] = dict(usage)

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
    `.transcript_id` / `.generation_model` / `.usage` 五个属性，为一个注解引入类型依赖不划算。

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
        # 合成文档：把「`[文档 N]` 是哪篇」一并写进头（见 `source_refs_of`）。
        source_refs=source_refs_of(tx),
        model=getattr(mv, "generation_model", "") or "",
        usage=getattr(mv, "usage", None) or None,
        params=compact_params(
            formats=list(formats or []),
            style=style,
            reasoningEffort=reasoning_effort,
            **(extra_params or {}),
        ),
    )
