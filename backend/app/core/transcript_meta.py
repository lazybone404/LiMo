"""
transcript 出处头（composite meta）的编解码。

合成文档（`sourceType="composite"`）的**来源身份**嵌在 `transcript.md` 的第一行——
因为它必须在 `index.json` 丢失重建后仍然存在：重建时 id 由内容 sha256 重推、
`sourceType` 被写死为空串，一切非内容派生的字段都保不住。

格式：

    <!-- limo-meta {"v":1,"sourceType":"composite",...} -->
    （空行）
    ## 来源文档
    1. 论文A ✓ 完整输入
    （空行）
    <body>

⚠️ **头不剥离**：`rawText == 模型输入 == 用户所见` 是本设计的三位一体前提，
把头搬到别处（索引、sidecar 文件）就等于制造第二个真相源。

⚠️ 写方（本模块的 `dump_meta`）与读方（`parse_meta`）必须成对——这正是把它单独
成模块的理由：两处各自手拼 JSON 必然漂移（项目铁律「成对逻辑必须同源」）。
"""

import json

from app.core.text_utils import sanitize_reference_name

# 头格式版本。将来改 schema 时靠它决定如何迁移；目前只有 1。
META_VERSION = 1

_META_PREFIX = "<!-- limo-meta "
_META_SUFFIX = " -->"

# 来源在正文列表里的标注（§4.4.3：降级必须**落进产物**，不能只靠 SSE 一闪而过）
_STATUS_LABEL = {
    "full": "✓ 完整输入",
    "summarized": "⚠ 已压缩为摘要",
    "truncated": "⚠ 已截断",
}


def _clean_title(title: str | None) -> str:
    """标题清洗：走通用的结构标记清洗，再额外抹掉 `-->`。

    额外那一层的理由很具体——出处头是 **HTML 注释**，标题里的 `-->` 会**提前闭合
    注释**：导出 HTML 时，注释之后的那截会变成可见文本，用户会看到 JSON 的尾巴
    （` b"]} -->` 这种）。JSON 序列化解决不了它（`-->` 不是 JSON 特殊字符）。
    换成 `→` 而不是直接删，保住「这里原本是个箭头」的信息。
    """
    return sanitize_reference_name(title).replace("-->", "→")


def dump_meta(meta: dict) -> str:
    """meta dict → 出处头，返回**单行**（不含换行）。

    `sourceTitles` 会逐项清洗后再序列化（`_clean_title` 幂等，重复调用无副作用）。
    """
    payload = dict(meta)
    payload["v"] = META_VERSION
    titles = payload.get("sourceTitles")
    if isinstance(titles, list):
        payload["sourceTitles"] = [_clean_title(t) for t in titles]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{_META_PREFIX}{body}{_META_SUFFIX}"


def parse_meta(raw_text: str) -> tuple[dict | None, bool]:
    """解析出处头，返回 `(meta, corrupt)`。

    | 输入 | 返回 |
    |---|---|
    | 无头（普通文档，绝大多数） | `(None, False)` |
    | 有头且合法 | `(meta, False)` |
    | 有头但坏 | `(None, True)` — 标记 `metadata_corrupt`，**不猜**来源 |

    ⚠️ 「无头」与「有头但坏」必须分开：把前者当坏，等于**把所有历史文档全标成损坏**。

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

    # schema 校验：只接受合成的出处头，形状不对一律按损坏处理
    if not isinstance(meta, dict) or meta.get("sourceType") != "composite":
        return None, True
    return meta, False


def strip_meta(raw_text: str) -> str:
    """去掉出处头（首行 + 紧随的空行），返回正文；无头时原样返回。

    用于需要「纯正文」的场景（如将来开放 transcript 编辑：meta 行属系统管理、
    不承诺可编辑，保存时要校验并保护首行）。
    """
    meta, corrupt = parse_meta(raw_text)
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
) -> str:
    """组装合成文档的完整 rawText：出处头 + 「## 来源文档」列表 + 正文。

    `sources`: `[{"title": str, "status": "full"|"summarized"|"truncated"}]`，
    `status` 决定列表里的标注（§4.4.3：降级必须持久化在产物里——SSE 提示是一次性的，
    用户当时没看见就永远不知道，而「以为读的是完整论文、其实是摘要」是知识系统里
    危害最大的误解）。

    标题在这里**统一清洗一次**，随后同时供 JSON 与列表使用——两处各清一次虽然
    结果相同（`_clean_title` 幂等），但形状上更不容易漂移。
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
    })

    lines = [head, "", "## 来源文档", ""]
    for i, s in enumerate(cleaned, 1):
        label = _STATUS_LABEL.get(s["status"], "")
        lines.append(" ".join(p for p in (f"{i}.", s["title"], label) if p))
    lines += ["", body]

    return "\n".join(lines)
