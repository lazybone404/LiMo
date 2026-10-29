"""MCP 工具层（`limo_cli/mcp_tools.py`）—— 参数透传 / 错误映射 / raw 切窗 / 快照重建

⚠️ 断言都冲着一个具体失效形态去，不是「跑通就算」：

| 断言 | 写错会怎样 |
|---|---|
| `folder_id=""` 与不传可区分 | 想「只看未分类」的人拿到全部文档，而界面上看不出区别 |
| `limo_read` 的 `max_chars` 默认不是 `None` | `None` 在 `LocalSource` 里是「用后端默认 256K」，一次工具调用灌满上下文 |
| `limo_get` 里没有 `rawText` | 长视频转录几十万字符直接进模型上下文 |
| `limo_read(part="raw")` 走 `get_resource` | 走 `get_artifact` 会报「产物不存在」，而刚导入的文档只有 raw |
| raw 切窗字段与 `_build` 同形 | 同一套语义长成两个形状，模型得记住哪份是哪份 |
| `SourceError` → `isError: true` | 走 JSON-RPC error 模型读不到原因，只会反复重试 |
| `CLI_NO_LOCAL_INDEX` 的 hint 被改写 | 模型被告知去「改用 --local」，而它就是本地模式 |
| `PARTS` 从 `commands` 派生 | `ALL_PARTS` 加第七种产物后 MCP 悄悄不认 |
| `continuation` 字段名不在本模块 | 改回手搓，`_build` 演化时副本不跟着变（模式 #54） |
| `holder.get()` 失败 → `isError` | 冒成 `-32603` + 裸类名，模型不知道要换 `--http` |
| `LocalHolder` 按签名重建 | 用户在应用里新导入的文档，MCP 永远看不到 |
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from limo_cli import commands, mcp_tools  # noqa: E402
from limo_cli.sources import SourceError  # noqa: E402

RAW_TEXT = (
    '<!-- limo-meta {"v":1,"kind":"transcript"} -->\n'
    "# 标题\n\n"
    + "正文" * 100
)


class FakeSource:
    """`Source` 七方法的替身：记录调用参数，可注入错误。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error: Exception | None = None
        self.raw = RAW_TEXT
        self.artifacts = [{"kind": "notes", "version": 2, "storage": "markdown",
                           "createdAt": "2026-10-29T10:00:00", "style": "plain",
                           "engine": "", "edited": False}]

    def _rec(self, name: str, **kw) -> None:
        self.calls.append((name, kw))
        if self.error is not None:
            raise self.error

    def last(self) -> tuple[str, dict]:
        return self.calls[-1]

    # ── 契约 ──

    def status(self) -> dict:
        self._rec("status")
        return {"schema_version": "1", "docCount": 7, "bySourceType": {"video": 7},
                "folderCount": 1, "uncategorized": 0, "tagCount": 2,
                "oldest": "2026-01-01T00:00:00", "newest": "2026-10-29T00:00:00"}

    def list_resources(self, **kw) -> dict:
        self._rec("list_resources", **kw)
        return {"schema_version": "1", "items": [], "has_more": False,
                "next_cursor": None, "truncated": False, "continuation": None}

    def get_resource(self, doc_id: str) -> dict:
        self._rec("get_resource", doc_id=doc_id)
        return {"schema_version": "1", "id": doc_id, "title": "标题",
                "sourceType": "webpage", "tags": [], "folderId": "",
                "rawText": self.raw}

    def list_artifacts(self, doc_id: str) -> dict:
        self._rec("list_artifacts", doc_id=doc_id)
        return {"schema_version": "1", "docId": doc_id, "items": self.artifacts}

    def get_artifact(self, doc_id: str, part: str, **kw) -> dict:
        self._rec("get_artifact", doc_id=doc_id, part=part, **kw)
        text = f"{part} 的正文"
        return {"schema_version": "1", "id": f"{doc_id}:{part}", "docId": doc_id,
                "kind": part, "version": kw.get("version") or 2, "content": text,
                "meta": None, "metaCorrupt": False, "truncated": False,
                "totalChars": len(text), "offset": kw.get("offset") or 0,
                "continuation": None}

    def search(self, q: str, **kw) -> dict:
        self._rec("search", q=q, **kw)
        return {"schema_version": "1", "items": [], "has_more": False,
                "next_cursor": None, "truncated": False, "continuation": None}

    def folders(self) -> dict:
        self._rec("folders")
        return {"schema_version": "1", "items": [{"id": "f1", "name": "技术", "parentId": ""}]}


def _holder():
    """(fake source, holder) —— holder 只是把 fake 原样递进去。"""
    src = FakeSource()
    return src, mcp_tools.StaticHolder(src)


# ── 工具集 ──

def test_六个工具且名字集合确定():
    names = [t["name"] for t in mcp_tools.list_tools()]
    assert names == ["limo_status", "limo_list", "limo_search",
                     "limo_get", "limo_read", "limo_folders"]
    assert set(mcp_tools._HANDLERS) == set(names)


def test_每个工具都有_description_与_inputSchema():
    for t in mcp_tools.list_tools():
        assert t["description"].strip(), t["name"]
        assert t["inputSchema"]["type"] == "object"


# ── part 枚举必须派生 ──

def test_part_枚举来自_commands_而不是手抄():
    assert mcp_tools.PARTS == commands.ALL_PARTS + (commands.RAW_PART,)
    schema = next(t for t in mcp_tools.list_tools() if t["name"] == "limo_read")
    assert schema["inputSchema"]["properties"]["part"]["enum"] == list(commands.ALL_PARTS + (
        commands.RAW_PART,))


def test_改了_ALL_PARTS_模块真的跟着变(monkeypatch):
    """⚠️ 对照实验：手抄一份写死的 tuple 时这条会红。"""
    monkeypatch.setattr(commands, "ALL_PARTS", ("aaa", "bbb"))
    importlib.reload(mcp_tools)
    try:
        assert mcp_tools.PARTS == ("aaa", "bbb", commands.RAW_PART)
        schema = next(t for t in mcp_tools.list_tools() if t["name"] == "limo_read")
        assert schema["inputSchema"]["properties"]["part"]["enum"] == ["aaa", "bbb", "raw"]
    finally:
        monkeypatch.undo()
        importlib.reload(mcp_tools)


# ── 参数透传 ──

def test_folder_id_空串与不传是两种语义():
    src, h = _holder()
    mcp_tools.call_tool("limo_list", {}, h)
    assert src.last()[1]["folder_id"] is None

    mcp_tools.call_tool("limo_list", {"folder_id": ""}, h)
    assert src.last()[1]["folder_id"] == ""

    mcp_tools.call_tool("limo_list", {"folder_id": "f1"}, h)
    assert src.last()[1]["folder_id"] == "f1"


def test_list_的其余参数逐个透传():
    src, h = _holder()
    mcp_tools.call_tool("limo_list", {
        "source_type": "video", "tag": "RAG", "query": "缓存",
        "include_descendants": True, "limit": 5, "cursor": "abc",
    }, h)
    kw = src.last()[1]
    assert kw["source_type"] == "video" and kw["tag"] == "RAG"
    assert kw["query"] == "缓存" and kw["include_descendants"] is True
    assert kw["limit"] == 5 and kw["cursor"] == "abc"


def test_list_的_limit_被夹紧():
    src, h = _holder()
    mcp_tools.call_tool("limo_list", {"limit": 9999}, h)
    assert src.last()[1]["limit"] == 200
    mcp_tools.call_tool("limo_list", {"limit": "不是数字"}, h)
    assert src.last()[1]["limit"] == mcp_tools.DEFAULT_LIST_LIMIT


def test_search_把_query_与_limit_交给_Source():
    src, h = _holder()
    mcp_tools.call_tool("limo_search", {"query": "  RAG  ", "limit": 3}, h)
    name, kw = src.last()
    assert name == "search"
    assert kw["q"] == "RAG"      # 两侧空白被剥掉
    assert kw["limit"] == 3


def test_search_缺必填_query_是工具级错误():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_search", {}, h)
    assert res["isError"] is True
    assert "query" in res["content"][0]["text"]
    assert src.calls == []       # 一个请求都没发


def test_folders_透传():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_folders", {}, h)
    assert src.last()[0] == "folders"
    assert res["structuredContent"]["items"][0]["name"] == "技术"


# ── limo_get ──

def test_get_摘掉完整原文只留长度与预览():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_get", {"id": "tr_1"}, h)
    sc = res["structuredContent"]
    assert "rawText" not in sc, "整篇原文不该进工具返回"
    assert sc["rawTextChars"] == len(RAW_TEXT)
    assert len(sc["rawTextPreview"]) <= mcp_tools.PREVIEW_CHARS
    # 预览是**剥过自证头**的：给模型看 `<!-- limo-meta … -->` 是噪音
    assert "limo-meta" not in sc["rawTextPreview"]
    # 「这篇有哪些产物」—— 没有它 agent 只能挨个试
    assert sc["artifacts"][0]["kind"] == "notes"
    assert [c[0] for c in src.calls] == ["get_resource", "list_artifacts"]


# ── limo_read ──

def test_read_默认_max_chars_不是_None():
    """⚠️ `max_chars=None` 在 LocalSource 里是「用后端默认 256K」，不是「不截断」。"""
    src, h = _holder()
    mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "notes"}, h)
    name, kw = src.last()
    assert name == "get_artifact"
    assert kw["max_chars"] == mcp_tools.DEFAULT_MAX_CHARS
    assert kw["offset"] == 0
    assert kw["version"] is None        # 不传版本 = 取最新版


def test_read_透传_offset_与_version():
    src, h = _holder()
    mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "notes",
                                      "offset": 40, "version": 3, "max_chars": 999}, h)
    _, kw = src.last()
    assert (kw["offset"], kw["version"], kw["max_chars"]) == (40, 3, 999)


def test_read_的_max_chars_有上限():
    src, h = _holder()
    mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "notes",
                                      "max_chars": 10_000_000}, h)
    assert src.last()[1]["max_chars"] == mcp_tools.MAX_MAX_CHARS


def test_read_的_part_默认是_notes():
    src, h = _holder()
    mcp_tools.call_tool("limo_read", {"id": "tr_1"}, h)
    assert src.last()[1]["part"] == commands.GENERATED_PARTS[0]


def test_read_返回正文文本且不重复塞进_structuredContent():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "notes"}, h)
    assert res["content"][0]["text"] == "notes 的正文"
    sc = res["structuredContent"]
    assert "content" not in sc, "正文已经给模型读了，structuredContent 再带一遍是双倍 token"
    assert sc["contentChars"] == len("notes 的正文")


def test_read_未知_part_是工具级错误():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "不存在的"}, h)
    assert res["isError"] is True
    assert "notes" in res["content"][0]["text"]   # 错误里给出合法值
    assert src.calls == []


# ── raw 切窗 ──

def test_raw_走_get_resource_而不是_get_artifact():
    src, h = _holder()
    mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "raw"}, h)
    names = [c[0] for c in src.calls]
    assert "get_resource" in names
    assert "get_artifact" not in names


def test_raw_切窗字段与_build_同形():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "raw",
                                            "offset": 5, "max_chars": 10}, h)
    sc = res["structuredContent"]
    text = res["content"][0]["text"]
    plain = mcp_tools._strip_meta(RAW_TEXT)

    assert text == plain[5:15]
    assert sc["totalChars"] == len(plain)
    assert sc["offset"] == 5
    assert sc["truncated"] is True
    assert set(sc["continuation"]) == {"reason", "limit", "next_offset",
                                       "remaining_chars", "hint"}
    assert sc["continuation"]["next_offset"] == 15
    assert sc["continuation"]["remaining_chars"] == len(plain) - 15
    assert sc["kind"] == "raw" and sc["version"] == 0


def test_raw_读完不给_continuation():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "raw",
                                            "max_chars": 100000}, h)
    sc = res["structuredContent"]
    assert sc["truncated"] is False
    assert sc["continuation"] is None


def test_raw_切窗与产物读取共用同一份实现():
    """⚠️ 守的是「只有一份实现」，不是「两份碰巧一样」。

    逐字段相等只是**当前快照**；真正的保证是那份字面量不在本模块里（见下一条）。
    """
    from app.core.text_window import window_text

    src, h = _holder()
    res = mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "raw",
                                            "offset": 3, "max_chars": 7}, h)
    plain = mcp_tools._strip_meta(RAW_TEXT)
    _, expected_text, expected_cont = window_text(plain, 3, 7)
    assert res["content"][0]["text"] == expected_text
    assert res["structuredContent"]["continuation"] == expected_cont


def test_window_text_夹紧负_offset_并把它交出去回显():
    """起点由 `window_text` 返回，调用方**不许自己再算** `max(0, …)`。

    两份夹紧的失效形态很轻但很典型：回显说 `offset: -5`、实际从 0 读，
    结构完整、值不对 —— 与 `continuation` 是同一类问题。
    """
    from app.core.text_window import window_text

    start, window, cont = window_text("abcdef", -5, 3)
    assert (start, window) == (0, "abc")
    assert cont["next_offset"] == 3


def test_continuation_的字段名只住在_core_text_window():
    """改回手搓必然要写 `next_offset` 这个键名 —— 字面量不在场就是判据。

    此前 `_read_raw` 与 `QueryService._build` 各拼了一遍，`_build` 演化时副本不会
    跟着变，同一个「还剩多少字 / 下次从哪读」在两个入口形状分叉，两边都不报错。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("limo_cli/mcp_tools.py", "app/services/query.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        hits = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and n.value == "next_offset"]
        assert not hits, f"{rel} 又自己拼 continuation 了（行 {hits}）"


def test_raw_剥掉了自证头():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_read", {"id": "tr_1", "part": "raw",
                                            "max_chars": 100000}, h)
    assert "limo-meta" not in res["content"][0]["text"]
    assert res["content"][0]["text"].startswith("# 标题")


# ── 错误映射 ──

def test_SourceError_变成_isError_而不是协议错误():
    src, h = _holder()
    src.error = SourceError(3, "文档不存在", code="QUERY_NOT_FOUND")
    res = mcp_tools.call_tool("limo_get", {"id": "tr_x"}, h)
    assert res["isError"] is True
    assert res["content"][0]["text"] == "文档不存在"


def test_hint_为空时不出现在文案里():
    """⚠️ 两个方向都测：`_run` 翻出来的 SourceError 本来就没有 hint。"""
    src, h = _holder()
    src.error = SourceError(3, "文档不存在", code="QUERY_NOT_FOUND", detail="tr_x")
    text = mcp_tools.call_tool("limo_get", {"id": "tr_x"}, h)["content"][0]["text"]
    assert text == "文档不存在\ntr_x"


def test_hint_有值时出现在文案末行():
    src, h = _holder()
    src.error = SourceError(5, "ID 不合法", code="QUERY_INVALID_ID",
                            detail="含 ../", hint="ID 只能包含字母数字与 _-")
    text = mcp_tools.call_tool("limo_get", {"id": "../x"}, h)["content"][0]["text"]
    assert text.splitlines()[-1] == "ID 只能包含字母数字与 _-"
    assert "含 ../" in text


def test_本地无索引的_hint_在_MCP_语境下被改写():
    """原文后半句是「或改用不带 --local 的联网模式」—— MCP 没有 --local 这个开关。"""
    e = SourceError(4, "本地全文索引尚未构建", code="CLI_NO_LOCAL_INDEX",
                    detail="/x/search.db",
                    hint="启动一次 LiMo 应用即可建立索引（或改用不带 --local 的联网模式）")
    text = mcp_tools._source_error_text(e)
    assert "--local" not in text
    assert "--http" in text


def test_AppError_也是工具级错误():
    from app.core.errors import AppError
    src, h = _holder()
    src.error = AppError("STORE_BROKEN", "存储坏了", detail="磁盘满")
    res = mcp_tools.call_tool("limo_status", {}, h)
    assert res["isError"] is True
    assert "存储坏了" in res["content"][0]["text"]
    assert "磁盘满" in res["content"][0]["text"]


class _BrokenHolder:
    """`holder.get()` 自己就失败 —— 打不开库，不是某个工具调不通。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get(self):
        raise self._exc


def test_打不开本机库是_isError_而不是协议错误():
    """`PermissionError` 此前冒到协议层 → `-32603` + 一个裸类名。

    模型拿到「PermissionError」既不知道能重试，也不知道还有 `--http` 这条路 ——
    而它是**预期内**的失败（目录没权限 / 路径不可达 / 磁盘满），不是内部缺陷。
    """
    res = mcp_tools.call_tool("limo_status", {},
                              _BrokenHolder(PermissionError(13, "拒绝访问")))
    assert res["isError"] is True
    text = res["content"][0]["text"]
    assert "PermissionError" in text
    assert "--http" in text


def test_打不开本机库时不回显异常文案里的本地路径():
    """`str(PermissionError)` 带绝对路径（含**用户名**），而这段文字会进 agent 上下文，
    还可能被用户原样贴进 issue。

    `mcp_server._handle` 的 `-32603` 分支早就只回类型名（那里写着「异常文案可能夹带
    本地路径」），这条工具级文案曾把 `{e}` 拼进去 —— 同一个纪律漏了一处，等于没有。
    """
    exc = PermissionError(13, "拒绝访问", r"C:\Users\alice\.limo\index.json")
    text = mcp_tools.call_tool(
        "limo_status", {}, _BrokenHolder(exc))["content"][0]["text"]
    assert "PermissionError" in text          # 类型名是排障需要的那一位
    assert "alice" not in text                # 用户名不外泄
    assert "index.json" not in text           # 路径不外泄


def test_holder_抛_SourceError_走同一套错误文案():
    res = mcp_tools.call_tool("limo_status", {}, _BrokenHolder(
        SourceError(4, "找不到数据目录", code="CLI_NO_DATA_DIR", detail="~/.limo")))
    assert res["isError"] is True
    assert "找不到数据目录" in res["content"][0]["text"]


def test_非_AppError_异常继续上抛():
    """协议层要把它翻成 `-32603`，工具层不能吞成一句 isError 文案。"""
    src, h = _holder()
    src.error = RuntimeError("boom")
    try:
        mcp_tools.call_tool("limo_status", {}, h)
    except RuntimeError:
        pass
    else:
        raise AssertionError("RuntimeError 不该被工具层吞掉")


def test_未知工具名抛_McpUnknownTool():
    src, h = _holder()
    try:
        mcp_tools.call_tool("limo_nope", {}, h)
    except mcp_tools.McpUnknownTool:
        pass
    else:
        raise AssertionError("未知工具应抛 McpUnknownTool")


def test_arguments_不是对象抛_McpInvalidArguments():
    src, h = _holder()
    for bad in ("字符串", [1, 2], 42):
        try:
            mcp_tools.call_tool("limo_status", bad, h)
        except mcp_tools.McpInvalidArguments:
            continue
        raise AssertionError(f"arguments={bad!r} 应抛 McpInvalidArguments")


def test_arguments_为_None_等同空对象():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_status", None, h)
    assert res["isError"] is False
    assert res["structuredContent"]["docCount"] == 7


# ── JSON 文本与 structuredContent 同源 ──

def test_JSON_类工具的文本可被解析回同一份载荷():
    src, h = _holder()
    res = mcp_tools.call_tool("limo_status", {}, h)
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]


# ── 快照持有者 ──

def testLocalHolder_按_index_签名重建(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text("{}", encoding="utf-8")

    built: list = []

    class FakeLocal:
        def __init__(self, d):
            self.data_dir = str(tmp_path)
            built.append(d)

    holder = mcp_tools.LocalHolder(lambda d: FakeLocal(d))

    first = holder.get()
    assert len(built) == 1 and built[0] is None      # 首建允许它是 None（去读配置）

    assert holder.get() is first                     # 签名没变 → 复用
    assert len(built) == 1

    idx.write_text('{"transcripts":[]}', encoding="utf-8")   # size 变了
    second = holder.get()
    assert second is not first
    assert len(built) == 2
    # ⚠️ 重建时必须传**具体路径**：传 None 会再走一次 load_config()（它每次 copy2 一份
    # config.yaml.bak），只读服务端不该因为「库里有新文档」就重复写配置文件
    assert built[-1] == str(tmp_path)


def testLocalHolder_mtime_变化也重建(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text("{}", encoding="utf-8")
    built = []

    class FakeLocal:
        def __init__(self, d):
            self.data_dir = str(tmp_path)
            built.append(d)

    holder = mcp_tools.LocalHolder(lambda d: FakeLocal(d))
    first = holder.get()
    st = idx.stat()
    os.utime(idx, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert holder.get() is not first
    assert len(built) == 2


def testLocalHolder_索引尚不存在时首次写入会重建(tmp_path):
    """空库：`Store` 会在首次写盘时造出 index.json，此后必须能看见。"""
    idx = tmp_path / "index.json"
    built = []

    class FakeLocal:
        def __init__(self, d):
            self.data_dir = str(tmp_path)
            built.append(d)

    holder = mcp_tools.LocalHolder(lambda d: FakeLocal(d))
    first = holder.get()
    assert len(built) == 1
    idx.write_text("{}", encoding="utf-8")
    assert holder.get() is not first
    assert len(built) == 2
