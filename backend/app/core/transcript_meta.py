"""
出处头（limo-meta）的编解码——**两种头共用一套信封**。

所有自证信息都嵌在文件第一行的 HTML 注释里，因为它必须在 `index.json` 丢失重建后
仍然存在：重建时 id 由内容 sha256 重推、`sourceType` 被写死为空串，一切非内容派生的
字段都保不住。**文件自身是唯一能扛住重建的载体。**

| 种类 | 标识 | 写在哪 | 解析入口 |
|---|---|---|---|
| 合成出处头 | `sourceType == "composite"` | `transcript.md` 第一行 | `parse_meta` |
| 产物自证头 | `type == "artifact"` | `{mode}.md` 第一行 | `parse_artifact_meta` |

格式：

    <!-- limo-meta {"v":1,"sourceType":"composite",...} -->
    （空行）
    ## 来源文档
    1. 论文A ✓ 完整输入
    （空行）
    <body>

⚠️ **头不剥离**（合成文档）：`rawText == 模型输入 == 用户所见` 是本设计的三位一体
前提，把头搬到别处（索引、sidecar 文件）就等于制造第二个真相源。产物头同理——
`{mode}.md` 是导出/检索的真相源，导出物离开 `.limo` 后正是靠它自证。

⚠️ 写方（`dump_meta`）与读方（`parse_meta` / `parse_artifact_meta`）必须成对——这
正是把它单独成模块的理由：两处各自手拼 JSON 必然漂移（项目铁律「成对逻辑必须同源」）。

⚠️ **信封与内容分层**：`_parse_envelope` 只认「第一行是合法 limo-meta 注释」，各
种类在此之上做自己的 schema 校验。合并成一段的写法曾让**任何一种新头都被判成
「损坏」**——新增种类时请只加薄包装，不要动信封层。
"""

import json

from app.core.text_utils import sanitize_reference_name

# 头格式版本。将来改 schema 时靠它决定如何迁移；目前只有 1。
META_VERSION = 1

_META_PREFIX = "<!-- limo-meta "
_META_SUFFIX = " -->"

# 产物自证头的信封标识（与合成的 `sourceType="composite"` 并列，见模块 docstring）
_ARTIFACT_TYPE = "artifact"

# 来源在正文列表里的标注（§4.4.3：降级必须**落进产物**，不能只靠 SSE 一闪而过）
#
# ⚠️ **每种失败原因必须各自可辨**：`truncated` 曾是**垃圾桶**——超窗、超时、模型
# 抽风全标它。但用户能采取的行动完全不同（换大窗口模型 / 重试 / 检查 Key），
# 共用一个标签等于「知道出事了，但不知道该做什么」（设计 §17.3）。
#
# ⚠️ `truncated` 这一条**保留**：历史文档里写的就是它，删掉会让老产物在列表里
# 静默丢行（`status_label` 的回退链依赖它）。
_STATUS_LABEL = {
    # ── 正常/无损 ──
    "full": "✓ 完整输入",
    "summarized": "⚠ 已压缩为摘要",
    "chunked": "⚠ 已分块抽取（全文覆盖）",
    # ── 降级，但原因可辨 ──
    "chunked_partial": "⚠ 已分块（部分段落抽取失败）",
    "truncated_over_window": "⚠ 已截断（原文超出模型窗口）",
    "truncated_timeout": "⚠ 已截断（摘要超时）",
    "truncated_llm_error": "⚠ 已截断（模型调用失败）",
    # ── 历史文档的回退基础（勿删，见上）──
    "truncated": "⚠ 已截断",
}


def status_label(status: str) -> str:
    """status → 列表里的中文标注；未知 status 走**基础状态回退**。

    回退的意义：`truncated_timeout` 这类细分状态将来可能再增，而**历史产物**里
    存的是老的 `truncated`。直接 `_STATUS_LABEL[status]` 索引会让未知状态抛
    KeyError（整个列表渲染失败）或丢标注（用户看不到降级）——两者都比「退化成
    粗粒度标注」坏。故按 `_` 切出基础状态再查一次。

    历史文档（`truncated`）与将来未知状态（`foo_bar` → `foo` 查不到 → 空串）
    都不崩、不丢行。
    """
    if not status:
        return ""
    return _STATUS_LABEL.get(status) or _STATUS_LABEL.get(status.split("_", 1)[0], "")


def _clean_title(title: str | None) -> str:
    """标题清洗：走通用的结构标记清洗，再额外抹掉 `-->`。

    额外那一层的理由很具体——出处头是 **HTML 注释**，标题里的 `-->` 会**提前闭合
    注释**：导出 HTML 时，注释之后的那截会变成可见文本，用户会看到 JSON 的尾巴
    （` b"]} -->` 这种）。JSON 序列化解决不了它（`-->` 不是 JSON 特殊字符）。
    换成 `→` 而不是直接删，保住「这里原本是个箭头」的信息。
    """
    return sanitize_reference_name(title).replace("-->", "→")


def _clean_meta_value(value):
    """递归清洗 meta 里的字符串：抹 `-->` + 归一换行。

    ⚠️ 出处头是 **HTML 注释**，任何字符串里的 `-->` 都会**提前闭合注释**——导出
    HTML 时注释之后的那截会变成可见文本，用户看到 JSON 的尾巴（` b"]} -->` 这种）。
    JSON 序列化解决不了它（`-->` 不是 JSON 特殊字符），转义也解决不了。

    ⚠️ 此前**只有 `sourceTitles` 走了清洗**，而 `dimensions` 同样进 meta——用户
    自定义一个叫 `A-->B` 的维度，导出 HTML 即泄漏。规则按**字段**写就会一直漏，
    按**递归遍历**写则新增字段自动受保护。

    ⚠️ 换行同样要归一：meta 必须**单行**（`parse_meta` 只认第一行），而
    `readerIntentText` 是用户自由文本，天然可能带换行。
    """
    if isinstance(value, str):
        return value.replace("-->", "→").replace("\r", " ").replace("\n", " ")
    if isinstance(value, list):
        return [_clean_meta_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean_meta_value(v) for k, v in value.items()}
    return value


def dump_meta(meta: dict) -> str:
    """meta dict → 出处头，返回**单行**（不含换行）。

    所有字符串值先过 `_clean_meta_value`（`-->` 与换行），`sourceTitles` 再**叠加**
    一层 `_clean_title`（结构标记清洗 + 限长）。两层都幂等，重复调用无副作用。
    """
    payload = {k: _clean_meta_value(v) for k, v in meta.items()}
    payload["v"] = META_VERSION
    titles = payload.get("sourceTitles")
    if isinstance(titles, list):
        payload["sourceTitles"] = [_clean_title(t) for t in titles]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{_META_PREFIX}{body}{_META_SUFFIX}"


def _parse_envelope(raw_text: str) -> tuple[dict | None, bool]:
    """解析出处头的**信封层**——只认「第一行是合法 limo-meta 注释 + 内容是 JSON 对象」，
    不做任何 schema 校验（那是各调用方的事）。

    | 输入 | 返回 |
    |---|---|
    | 无头（普通文档，绝大多数） | `(None, False)` |
    | 有头且信封合法 | `(payload, False)` |
    | 有头但坏（无收尾 / JSON 非法 / 非对象） | `(None, True)` |

    ⚠️ 拆出这一层的理由：`parse_meta` 曾把 schema 校验（`sourceType == "composite"`）
    写在同一段里，于是**任何一种新头都会被它判成「损坏」**。信封与内容必须分开——
    将来再加 meta 种类时，只需新增一个「信封 + 自己的 schema」的薄包装。

    ⚠️ 只认**第一行**。正文里偶然出现的 `<!-- limo-meta` 一律不解析——正文是不可信
    输入（导入内容），否则一段被导入的文本就能伪造出处头。
    """
    if not raw_text:
        return None, False

    first_line = raw_text.split("\n", 1)[0]
    if not first_line.startswith(_META_PREFIX):
        return None, False
    if not first_line.endswith(_META_SUFFIX):
        return None, True

    payload = first_line[len(_META_PREFIX):-len(_META_SUFFIX)]
    try:
        meta = json.loads(payload)
    except (ValueError, TypeError):
        return None, True
    if not isinstance(meta, dict):
        return None, True
    return meta, False


def parse_meta(raw_text: str) -> tuple[dict | None, bool]:
    """解析**合成**出处头，返回 `(meta, corrupt)`。

    | 输入 | 返回 |
    |---|---|
    | 无头（普通文档，绝大多数） | `(None, False)` |
    | 有头且是合成头 | `(meta, False)` |
    | 有头但坏，或信封合法却不是合成头 | `(None, True)` — 标记 `metadata_corrupt`，**不猜**来源 |

    ⚠️ 「无头」与「有头但坏」必须分开：把前者当坏，等于**把所有历史文档全标成损坏**。

    ⚠️ 签名与语义**保持不变**（读它的三处 `store.py` 路径依赖这个形状）。新增的
    产物头（`type == "artifact"`）走 `parse_artifact_meta`，不会出现在 transcript 上。
    """
    meta, corrupt = _parse_envelope(raw_text)
    if meta is None:
        return None, corrupt
    # schema 校验：只接受合成的出处头，形状不对一律按损坏处理
    if meta.get("sourceType") != "composite":
        return None, True
    return meta, False


def parse_artifact_meta(raw_text: str) -> tuple[dict | None, bool]:
    """解析**产物**出处头（`type == "artifact"`），返回 `(meta, corrupt)`。

    | 输入 | 返回 |
    |---|---|
    | 无头（历史产物，绝大多数） | `(None, False)` |
    | 有头且是产物头 | `(meta, False)` |
    | 有头但信封坏 | `(None, True)` |
    | 信封合法但是**别的种类**（如合成头） | `(None, False)` |

    ⚠️ 最后一行是刻意的：**「不是产物头」≠「损坏」**。一份文档的 notes 里若嵌了
    合成的头（例如被当原料转存），那是另一类东西，不该让读方报 corruption。
    """
    meta, corrupt = _parse_envelope(raw_text)
    if meta is None:
        return None, corrupt
    if meta.get("type") != _ARTIFACT_TYPE:
        return None, False
    return meta, False


def build_artifact_meta(
    kind: str,
    *,
    source: dict | None = None,
    generated: dict | None = None,
    identity: dict | None = None,
    trust: dict | None = None,
) -> dict:
    """组装一份产物的自证头（四条：从哪来 / 怎么来的 / 是什么 / 可信度边界）。

    | 条 | 字段 | 内容 |
    |---|---|---|
    | ① 从哪来 | `source` | `{docId, sourceType, sourceUrl?, sourceRefs?}` |
    | ② 怎么来的 | `generated` | `{model, promptVersion, params, generatedAt}` |
    | ③ 是什么 | `identity` | `{version, mode}` |
    | ④ 可信度边界 | `trust` | `{materialVsInference: [...]}`（**结构先留，值待提示词侧配合**） |

    `kind` 是产物种类（`notes` / `polished` / `process`），与信封标识 `type` 分开——
    前者回答「这是什么产物」，后者回答「这段 meta 由谁解析」。
    """
    return {
        "type": _ARTIFACT_TYPE,
        "kind": kind,
        "source": dict(source or {}),
        "generated": dict(generated or {}),
        "identity": dict(identity or {}),
        "trust": dict(trust or {}),
    }


def strip_meta(raw_text: str) -> str:
    """去掉出处头（首行 + 紧随的空行），返回正文；无头时原样返回。

    ⚠️ 判据走**信封层**而非 `parse_meta`：后者只认合成头，拿它当判据会让产物头
    （`type == "artifact"`）落进「有头但坏」分支——虽然结果同样是剥掉首行，但语义
    上把它当成了损坏数据。信封层只看「首行是不是合法 limo-meta 注释」，与种类无关。

    用于需要「纯正文」的场景：编辑保存时保护系统管理的首行、把笔记当合成原料时
    剥掉上一版的自证头（否则它会占 token 并进模型上下文）。
    """
    meta, corrupt = _parse_envelope(raw_text)
    if meta is None and not corrupt:
        return raw_text
    rest = raw_text.split("\n")[1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return "\n".join(rest)


def build_composite_raw_text(
    source_doc_ids: list[str],
    source_hashes: list[str],
    sources: list[dict],
    body: str,
    dimensions: list[str] | None = None,
    source_mode: str = "raw",
    content_type: list[str] | None = None,
    reader_intent: str | None = None,
    reader_intent_text: str = "",
) -> str:
    """组装合成文档的完整 rawText：出处头 + 「## 来源文档」列表 + 正文。

    `sources`: `[{"title": str, "status": "full"|"summarized"|…}]`，
    `status` 决定列表里的标注（§4.4.3：降级必须持久化在产物里——SSE 提示是一次性的，
    用户当时没看见就永远不知道，而「以为读的是完整论文、其实是摘要」是知识系统里
    危害最大的误解）。

    标题在这里**统一清洗一次**，随后同时供 JSON 与列表使用——两处各清一次虽然
    结果相同（`_clean_title` 幂等），但形状上更不容易漂移。

    `dimensions` / `source_mode` / `content_type` / `reader_intent*` **只进 meta，
    不进可见正文**（设计 §18.1 / §19.5 / §7.8）：
    - `dimensions` 是**给模型的指令**（生成侧 parse_meta 读回后注入 prompt）。
      混进可见正文会让用户在编辑正文时误删它——那是 rawText 里唯一被用户编辑的区。
    - `source_mode` 是**产物自解释**（三个月后看这份综述，应当知道它基于原文还是笔记
      ——这直接影响对信息完整度的判断）。生成侧不需要它（读的就是 rawText）。
    - `content_type` / `reader_intent` 同理走 meta：生成是**第二次独立请求**，届时
      手里只有 rawText，凡是生成侧要用的参数都只能落在这里。
    ⚠️ 与 `sourceTitles` 的处理**刻意不同**——后者在 meta 与列表**两处都有**，
    因为它需要人眼可读。
    """
    cleaned = [
        {"title": _clean_title(s.get("title")), "status": s.get("status", "full")}
        for s in sources
    ]

    head = dump_meta({
        "sourceType": "composite",
        "sourceDocIds": list(source_doc_ids),
        "sourceTitles": [s["title"] for s in cleaned],
        "sourceHashes": list(source_hashes),
        "dimensions": list(dimensions or []),
        "sourceMode": source_mode,
        "contentType": list(content_type or []),
        "readerIntent": reader_intent or "",
        "readerIntentText": reader_intent_text or "",
    })

    lines = [head, "", "## 来源文档", ""]
    for i, s in enumerate(cleaned, 1):
        label = status_label(s["status"])
        lines.append(" ".join(p for p in (f"{i}.", s["title"], label) if p))
    lines += ["", body]

    return "\n".join(lines)
