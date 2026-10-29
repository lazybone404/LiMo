"""MCP 工具层 —— 六个只读工具的定义与 handler。

**本模块只做参数搬运与形状整理**：不解析库的存储格式、不直接读 sqlite、不重算任何
判据。所有数据经 `limo_cli.sources` 的 `Source` 契约（`HttpSource` / `LocalSource`
同名同参同形状），与 `limo` CLI 完全同源 —— 于是「CLI 报 12 篇、MCP 报 11 篇」这类
静默分叉从结构上不可能发生。

⚠️ **与协议层分家**：`mcp_server.py` 管 JSON-RPC 帧、方法分派与错误码；本模块管
「一个工具该返回什么」。分开的理由是测试 —— 工具层能拿 fake Source 直接测，不必起
子进程、不必喂 stdio。

⚠️ **`app.*` 一律函数内 import**：`--http` 模式只该拉起 urllib，不该为了 `limo_list`
把 config / secrets / DPAPI 半棵依赖树搬进来（同 `sources.py` 的纪律）。

三个错误类别的分野见 `call_tool`：
- `McpToolError` / `SourceError` / `AppError` → `isError: true`（模型能读、能改、能重试）
- `McpUnknownTool` / `McpInvalidArguments` → 由协议层翻成 JSON-RPC `error`
- 其余 `Exception` → 协议层 `-32603`，只回类型名（堆栈可能含本地路径）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from limo_cli import commands
from limo_cli.sources import SourceError

# ── 工具侧默认值 ──
#
# `DEFAULT_LIST_LIMIT` 比 `QueryService.DEFAULT_PAGE_SIZE`（50）小：工具的读者是模型，
# 一次 50 条元数据就要占掉可观上下文，而它通常只需要「有没有、叫什麼」。要更多它
# 自己会传 limit。
DEFAULT_LIST_LIMIT = 20
DEFAULT_MAX_CHARS = 20000
MAX_MAX_CHARS = 200000
# `limo_get` 里替代整篇 rawText 的预览长度：足够让模型判断「要不要读正文」，
# 又不至于一次工具调用就灌满上下文。
PREVIEW_CHARS = 300

# `limo_read` 接受的 part。⚠️ **从 `commands` 派生 + 追加 `RAW_PART`**，不手抄那六个 ——
# 手抄的失效形态是「`ALL_PARTS` 加了第七种产物，而 MCP 悄悄不认它」，两边都不报错。
PARTS = commands.ALL_PARTS + (commands.RAW_PART,)


# ── 错误 ──

class McpToolError(Exception):
    """工具级错误 → `isError: true`（模型看得见原因并能自我纠正）。"""


class McpUnknownTool(Exception):
    """工具名不存在 → JSON-RPC `-32601`。"""


class McpInvalidArguments(Exception):
    """`arguments` 不是 JSON 对象 → JSON-RPC `-32602`。"""


# MCP 语境下**改写**的错误提示。键是后端/CLI 的 code。
#
# `CLI_NO_LOCAL_INDEX` 的原文后半句是「或改用不带 --local 的联网模式」—— 那是对 CLI
# 用户说的；MCP 的对应动作是 `--http`，而选了 `--http` 压根不会走到 `LocalSource`。
# 故在这里换文案，**不改 `sources.py`**：CLI 的那份对 CLI 是对的。
_HINT_OVERRIDES = {
    "CLI_NO_LOCAL_INDEX": (
        "先在 LiMo 应用里打开一次（应用启动时会建全文索引），"
        "或改用 `--http` 连正在运行的 LiMo。"
    ),
}


def _source_error_text(e: SourceError) -> str:
    """`SourceError` → 给模型看的一段话（**非空才拼**）。

    ⚠️ 别假设 `hint` 总有值：`LocalSource._run` 把 `QueryError` 翻成 `SourceError` 时
    只带 `message` + `detail`，`hint` 只有 `require_ident` 与 `_need_search` 两处会填。
    它的作用是「补充下一步动作」，空着不代表出错了。
    """
    lines = [e.message or "操作失败"]
    if e.detail:
        lines.append(e.detail)
    hint = _HINT_OVERRIDES.get(e.code) or e.hint
    if hint:
        lines.append(hint)
    return "\n".join(lines)


# ── 数据源持有者 ──
#
# 两种模式各一个极薄的类，都只暴露 `get()`：
#   `--http`      → `HttpSource` 没有任何本地缓存，抓着不放
#   直开本机库    → `Store` 把 `index.json` 读进内存，**必须随文件变化重建**


class StaticHolder:
    """`--http`：数据新鲜度由后端保证，本地零缓存。"""

    def __init__(self, source: Any) -> None:
        self._source = source

    def get(self) -> Any:
        return self._source


class LocalHolder:
    """直开本机库：`index.json` 的 `(mtime, size)` 变了就**重建整个 `LocalSource`**。

    `SearchEngine` 其实不需要重建（`search()` 每次新建连接，天然读到最新写入），但为了
    省它那几毫秒去拆 `LocalSource` 的内部结构，就等于长出一个「MCP 版 LocalSource」——
    第二份真相源。重建一次实测约 0.13s（152 篇的库）。

    ⚠️ **重建时传的是首次解析出的具体路径，不是 `None`** —— `LocalSource(None)` 会
    `load_config()`，而它每次都会 `copy2` 一份 `config.yaml.bak`。一个只读服务端不该
    因为「库里有新文档」就反复写配置文件。

    ⚠️ 不做「每次调用无条件重建」：`Store.__init__` 有条迁移自检（自愈重建 / 去重 /
    分类 sidecar / 遗留布局）。稳态下一条都不写盘，但没必要每次工具调用都跑一遍。
    """

    def __init__(self, make: Callable[[str | None], Any]) -> None:
        self._make = make
        self._source: Any = None
        self._data_dir: str = ""
        self._index_path: Path | None = None
        self._key: tuple[int, int] | None = None

    def get(self) -> Any:
        if self._source is None:
            return self._rebuild(None)
        if self._signature() != self._key:
            return self._rebuild(self._data_dir)
        return self._source

    def _rebuild(self, data_dir: str | None) -> Any:
        self._source = self._make(data_dir)
        self._data_dir = str(self._source.data_dir)
        self._index_path = Path(self._data_dir) / "index.json"
        self._key = self._signature()
        return self._source

    def _signature(self) -> tuple[int, int] | None:
        if self._index_path is None:
            return None
        try:
            st = self._index_path.stat()
        except OSError:
            # 库还没建索引：`None` 与「文件存在」是可区分的两个状态，故首次写入
            # 触发的那次重建不会因为「两次都是 None」被跳过
            return None
        return (st.st_mtime_ns, st.st_size)


# ── 参数取值（宽容：类型不对就退回默认，不因此让一次调用失败）──

def _opt_str(args: dict, key: str) -> str | None:
    v = args.get(key)
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def _opt_bool(args: dict, key: str, default: bool = False) -> bool:
    v = args.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _opt_int(args: dict, key: str, default: int, *,
             lo: int | None = None, hi: int | None = None) -> int:
    v = args.get(key)
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _require_str(args: dict, key: str, label: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise McpToolError(f"缺少必填参数 `{key}`（{label}）")
    return v.strip()


def _dumps(payload: dict) -> str:
    """紧凑序列化：工具结果是**进模型上下文**的，缩进空白纯属浪费。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ── handler ──
#
# 每个 handler 返回 `(给模型读的文本, 结构化载荷)`。五个 JSON 类工具的文本就是载荷的
# JSON；`limo_read` 的文本是**正文本身**，模型不用二次 parse。


def _tool_status(src: Any, args: dict) -> tuple[str, dict]:
    payload = src.status()
    return _dumps(payload), payload


def _tool_list(src: Any, args: dict) -> tuple[str, dict]:
    # ⚠️ `folder_id` 的三种取值是**三种语义**：不传 = 不过滤、`""` = 只看未分类、
    # 具体 id = 只看那个夹。故用 `in` 判存在，不能靠真值（`""` 会被吃掉）。
    folder_id = args.get("folder_id") if "folder_id" in args else None
    payload = src.list_resources(
        source_type=_opt_str(args, "source_type"),
        tag=_opt_str(args, "tag"),
        folder_id=None if folder_id is None else str(folder_id),
        query=_opt_str(args, "query"),
        include_descendants=_opt_bool(args, "include_descendants"),
        limit=_opt_int(args, "limit", DEFAULT_LIST_LIMIT, lo=1, hi=200),
        cursor=_opt_str(args, "cursor"),
    )
    return _dumps(payload), payload


def _tool_search(src: Any, args: dict) -> tuple[str, dict]:
    q = _require_str(args, "query", "要检索的词或短语")
    # ⚠️ `Source.search` 没有 cursor 参数（FTS5 的相关度序没有稳定键可做 keyset
    # 游标），「到头了」由 `truncated` + `continuation` 表达。别在这里臆造翻页。
    payload = src.search(q, limit=_opt_int(args, "limit", DEFAULT_LIST_LIMIT, lo=1, hi=200))
    return _dumps(payload), payload


def _tool_get(src: Any, args: dict) -> tuple[str, dict]:
    doc_id = _require_str(args, "id", "文档 ID")
    res = dict(src.get_resource(doc_id))

    # ⚠️ **必须摘掉 `rawText`**：`get_resource` 返回整篇原文，一篇长视频转录就是几十万
    # 字符 —— 直接透传等于一次「看元数据」的工具调用把上下文灌满。留长度 + 预览，
    # 让模型自己决定要不要 `limo_read(part="raw")`。
    raw = res.pop("rawText", "") or ""
    res["rawTextChars"] = len(raw)
    res["rawTextPreview"] = _strip_meta(raw)[:PREVIEW_CHARS]

    # 「这篇有哪些产物」——没有它 agent 只能挨个 `limo_read` 靠 `isError` 反推
    res["artifacts"] = src.list_artifacts(doc_id).get("items", [])

    return _dumps(res), res


def _tool_read(src: Any, args: dict) -> tuple[str, dict]:
    doc_id = _require_str(args, "id", "文档 ID")
    part = _opt_str(args, "part") or commands.GENERATED_PARTS[0]
    if part not in PARTS:
        raise McpToolError(
            f"未知的 part `{part}`，合法值：{', '.join(PARTS)}")
    offset = _opt_int(args, "offset", 0, lo=0)
    max_chars = _opt_int(args, "max_chars", DEFAULT_MAX_CHARS, lo=1, hi=MAX_MAX_CHARS)

    if part == commands.RAW_PART:
        payload = _read_raw(src, doc_id, offset, max_chars)
    else:
        payload = src.get_artifact(
            doc_id, part,
            version=_opt_int(args, "version", 0, lo=0) or None,
            offset=offset,
            # ⚠️ **必须显式传**：`max_chars=None` 在 `LocalSource` 里的语义是「用后端
            # 默认上限」（256K），不是「不截断」。传 None 等于把上下文一次性撑爆。
            max_chars=max_chars,
        )

    content = payload.get("content", "")
    # `content` 已经在返回值里给模型读了，`structuredContent` 再带一遍就是同一份正文
    # 双倍 token。换成长度 —— 与 `limo_get` 摘 `rawText` 同一条理由。
    structured = {k: v for k, v in payload.items() if k != "content"}
    structured["contentChars"] = len(content)
    return content, structured


def _tool_folders(src: Any, args: dict) -> tuple[str, dict]:
    payload = src.folders()
    return _dumps(payload), payload


_HANDLERS: dict[str, Callable[[Any, dict], tuple[str, dict]]] = {
    "limo_status": _tool_status,
    "limo_list": _tool_list,
    "limo_search": _tool_search,
    "limo_get": _tool_get,
    "limo_read": _tool_read,
    "limo_folders": _tool_folders,
}


def _strip_meta(text: str) -> str:
    """剥掉产物的 `<!-- limo-meta … -->` 自证头。

    函数内 import：`--http` 模式不该为了取一段预览就拉起 backend 依赖树。
    """
    from app.core.transcript_meta import strip_meta
    return strip_meta(text or "")


def _read_raw(src: Any, doc_id: str, offset: int, max_chars: int) -> dict:
    """读文档正文（`transcript.md`）并切窗。

    它**不是产物**（没有版本、没有自证头可解析），所以走 `get_resource()` 而不是
    `get_artifact()`，切窗也由本层自己做 —— 这是全模块**唯一**一处自己做的切片。

    没有这一条就是个真缺口：刚导入、还没生成过笔记的文档，六个产物 part 一个都不存在，
    agent 就只能从 `limo_get` 里看到「有这篇文档」却读不到任何内容。

    ⚠️ 切窗与 `continuation` 形状走 `app.core.text_window::window_text` ——
    与 `QueryService._build` **同一份**。此前这里逐字重写了一遍，`_build` 演化时
    这个副本不会跟着变，同一个「还剩多少字 / 下次从哪读」在两个入口形状分叉，
    而两边都不报错。
    """
    from app.core.text_window import window_text

    res = src.get_resource(doc_id)
    content = _strip_meta(res.get("rawText") or "")
    total = len(content)
    start, window, continuation = window_text(content, offset, max_chars)
    truncated = continuation is not None
    return {
        "schema_version": res.get("schema_version"),
        "id": res.get("id", doc_id),
        "docId": res.get("id", doc_id),
        "kind": commands.RAW_PART,
        "version": 0,
        "content": window,
        "meta": None,
        "metaCorrupt": False,
        "truncated": truncated,
        "totalChars": total,
        "offset": start,
        "continuation": continuation,
    }


# ── 对外入口 ──

def list_tools() -> list[dict]:
    """`tools/list` 的 `tools` 数组。"""
    return [dict(t) for t in _TOOL_DEFS]


def call_tool(name: str, arguments: Any, holder: Any) -> dict:
    """执行一个工具，返回 `tools/call` 的 result（**不含 `resultType`** —— 那是协议层的）。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise McpUnknownTool(name)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise McpInvalidArguments("arguments 必须是 JSON 对象")

    # `holder.get()` 单独一层：直开本机库时它要现造 `LocalSource`（连带 `Store`），
    # 打不开（数据目录没权限 / 路径不可达 / 磁盘满）是**预期内**的失败。此前它与
    # handler 共用下面那个 `except`，而 `except Exception` 支只放行 `AppError` ——
    # 于是 `PermissionError` 一路冒到协议层变成 `-32603` + 一个裸类名，模型拿到
    # 「PermissionError」既不知道能重试，也不知道还有 `--http` 这条路。
    try:
        src = holder.get()
    except SourceError as e:
        return _error_result(_source_error_text(e))
    except Exception as e:
        # ⚠️ **只回类型名，不回 `str(e)`**：`PermissionError` / `FileNotFoundError`
        # 的文案里带本地绝对路径（含用户名），而这段文字会进 agent 的上下文、还可能被
        # 用户贴进 issue。与 `mcp_server._handle` 的 `-32603` 分支同一条纪律 ——
        # 那里早就是这么写的，这里曾漏了。全文留 stderr（`main()` 已把 stdout 换成
        # stderr，故 `print_exc()` 的默认落点是安全的）。
        import traceback

        traceback.print_exc(file=sys.stderr)
        return _error_result(
            f"无法打开本机知识库（{type(e).__name__}）。\n"
            "可以用 `--http` 连正在运行的 LiMo 应用，"
            "或检查数据目录（~/.limo）的读写权限（详细堆栈见 stderr）。"
        )

    try:
        text, structured = handler(src, arguments)
    except McpToolError as e:
        return _error_result(str(e))
    except SourceError as e:
        return _error_result(_source_error_text(e))
    except Exception as e:  # 见下方：只认 AppError，其余继续上抛
        from app.core.errors import AppError
        if not isinstance(e, AppError):
            raise
        lines = [getattr(e, "message", "") or str(e)]
        detail = getattr(e, "detail", "")
        if detail:
            lines.append(detail)
        return _error_result("\n".join(lines))

    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


def _error_result(text: str) -> dict:
    """工具级失败 —— **不是** JSON-RPC error，模型要能读到原因。"""
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    }


# ── 工具定义（名字 / 描述 / JSON Schema）──
#
# 描述写得好不好，比实现更容易失败：agent 只凭这几句话决定「用哪个工具、传什么参数」。
# 故要写明**边界**（只读 / 字面检索的局限 / 截断怎么续），而不只是「列出文档」。

_TOOL_DEFS: list[dict] = [
    {
        "name": "limo_status",
        "description": (
            "查看用户本地 LiMo 知识库的概览：文档总数、按来源类型分布、文件夹数、"
            "未分类数、标签数、最早/最新的导入时间。回答『我库里都有什么』时先调这个。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "limo_list",
        "description": (
            "按条件浏览本地知识库里的文档（**不做全文检索**，只看元数据并按创建时间倒序）。"
            "适合『我最近导入了什么』『某个文件夹里有什么』『哪些是视频』这类按主题浏览的"
            "请求。要按内容找文档请用 limo_search。\n"
            "folder_id 有三种语义：不传 = 不过滤；传空串 \"\" = 只看未分类；传具体 id = "
            "只看那个文件夹（配合 include_descendants 连带子文件夹）。\n"
            "返回结果分页：has_more 为真时把 next_cursor 原样传回来取下一页。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "description": "来源类型过滤，如 video / webpage / pdf / docx / audio / image / text / composite。",
                },
                "tag": {"type": "string", "description": "只看带这个标签的文档。"},
                "folder_id": {
                    "type": "string",
                    "description": "文件夹 id。空串 = 只看未分类；不传 = 不过滤。",
                },
                "include_descendants": {
                    "type": "boolean",
                    "description": "为真时连带该文件夹的所有子孙文件夹（对空串无效）。",
                },
                "query": {
                    "type": "string",
                    "description": "按**标题**子串过滤（不是全文检索，不区分大小写）。",
                },
                "limit": {
                    "type": "integer",
                    "description": f"返回条数，默认 {DEFAULT_LIST_LIMIT}，上限 200。",
                },
                "cursor": {"type": "string", "description": "上一页返回的 next_cursor。"},
            },
        },
    },
    {
        "name": "limo_search",
        "description": (
            "在本地知识库里做**全文检索**（标题 + 正文 + 笔记，字面匹配，不区分大小写）。"
            "这是找文档内容的主工具。\n"
            "⚠️ 它是**字面检索**：语义型提问（『那篇讲缓存穿透的』）可能搜不到 —— 换更具体"
            "的词，或改用 limo_list 按主题浏览。\n"
            "结果按相关度排序，每条带 snippet 摘要（其中的 `<mark>` 是高亮标记，不是正文）。"
            "truncated 为真表示结果被 limit 截断"
            "（这种截断**不能翻页**，请细化关键词或调大 limit）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词或短语（必填）。"},
                "limit": {
                    "type": "integer",
                    "description": f"返回条数，默认 {DEFAULT_LIST_LIMIT}，上限 200。",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "limo_get",
        "description": (
            "看一篇文档的元数据：标题、来源、标签、文件夹、创建时间，以及它**有哪些产物**"
            "（artifacts，每项带 kind / version）。拿到 kind 后再用 limo_read 读正文。\n"
            "⚠️ 它**不返回全文**（只在 rawTextPreview 里给开头一小段）。读全文用 "
            "limo_read，文档正文用 part=\"raw\"。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "文档 ID（必填）。"}},
            "required": ["id"],
        },
    },
    {
        "name": "limo_read",
        "description": (
            "读一篇文档的正文。part 是产物种类——先用 limo_get 看这篇有哪些 artifacts。\n"
            f"part 合法值：{', '.join(PARTS)}。其中 \"{commands.RAW_PART}\" 是**文档原文**"
            "（转录/抓取的正文），刚导入还没生成过笔记的文档只有它可读。\n"
            "⚠️ 视频类文档的 raw 是带时间戳的字幕 JSON（正文在 `segments[].text` 里）。\n"
            "⚠️ 内容会按 max_chars 截断。看到 truncated=true 时，用 "
            "continuation.next_offset 作为下一次的 offset 继续读"
            "（字段名是下划线风格，不是 camelCase）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "文档 ID（必填）。"},
                "part": {
                    "type": "string",
                    "enum": list(PARTS),
                    "description": f"产物种类，默认 {commands.GENERATED_PARTS[0]}。",
                },
                "version": {
                    "type": "integer",
                    "description": "产物版本号；不传取最新版。对 raw 无效。",
                },
                "offset": {
                    "type": "integer",
                    "description": "从第几个字符开始读，默认 0。续读时填 continuation.next_offset。",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        f"本次最多返回多少个字符，默认 {DEFAULT_MAX_CHARS}，"
                        f"上限 {MAX_MAX_CHARS}。"
                    ),
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "limo_folders",
        "description": (
            "列出本地知识库的全部文件夹（树形，每项带 parentId 与文档数）。"
            "配合 limo_list 的 folder_id 使用。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]
