"""MCP 服务端 —— 把 LiMo 库只读暴露给 Claude Code / Cursor 这类外部 agent。

    Claude Code ──stdio──▶ 本模块 ──▶ LocalSource ──▶ QueryService ──▶ Store / SearchEngine
                                      └─（--http）HttpSource ──▶ 127.0.0.1:7413

**不是一个新实现，只是一个新消费者**：`Source` 契约（七方法）已有两个实现 ——
`HttpSource`（走 HTTP 打到 7413）与 `LocalSource`（进程内直开本机库，`doctor --local` 用它）；
`limo` CLI 是第一个消费者，这里是第二个。故本模块**不碰 `QueryService` 一行**，
只做三件事：读帧、分派、把结果说出来。

⚠️ **与 `mcp_tools.py` 分家**：那边管「一个工具该返回什么」（拿 fake Source 就能测），
这边管 JSON-RPC 帧、方法分派、错误码（拿 StringIO 就能测）。混在一起的话，测一个工具
要先拼 JSON、再起子进程。

⚠️ **stdout 是协议通道**：stdio 模式下任何一行意外输出都会让客户端解析失败、连接直接断，
而本地看不出异常。所以 `main()` 第一件事就是把 `sys.stdout` 换成 stderr，协议写入只经
`_write()` —— 别处出现 `sys.stdout` 一律是 bug（`scripts/check-mcp` 有负向对照钉着）。

## 协议版本

新客户端（`2026-07-28`，stateless）与 legacy 客户端（`initialize` 握手）**都要支持**：
Claude Code 逐服务器协商，新协议失败时会自动回退 legacy。少一条栈，一半的客户端连不上。

`SUPPORTED_VERSIONS` **只列核实过的版本**。写一个自己没实现的历史版本号，是在承诺一套
没实现的行为：客户端协商到它之后按那套规范发请求，我们按这套回，**双方都不报错**，
只是语义全错。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, TextIO

from . import __version__, mcp_tools

PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_VERSIONS: tuple[str, ...] = (PROTOCOL_VERSION,)

# 单行上限：agent 偶尔会把整个文件塞进参数里。超了回 `-32600` 而不是把内存吃满。
MAX_LINE_BYTES = 8 * 1024 * 1024

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL = -32022

SERVER_INFO = {"name": "limo", "version": __version__}

INSTRUCTIONS = (
    "LiMo 是用户的本地知识库（网页 / 视频 / 文档的转录与笔记）。只读，不能写入。\n"
    "典型用法：`limo_search` 找内容 → `limo_get` 看某篇的元数据与产物清单 → "
    "`limo_read` 读其中一份的正文。\n"
    "检索是字面的：语义型提问可能搜不到，换关键词或用 `limo_list` 按主题 / 标签 / "
    "文件夹浏览。\n"
    "`limo_read` 的正文会被截断，看 `truncated` 与 `continuation.next_offset` 续读。"
)


# ── 帧读写 ──
#
# ⚠️ stdio 的帧格式是 **newline-delimited JSON**，没有 `Content-Length` 头
# （那是 LSP 的）。写一条 = 一行 JSON + `\n` + flush。


def _write(stdout: TextIO, obj: dict) -> None:
    """协议写入的**唯一出口**。"""
    stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    stdout.flush()


def _drain_line(stdin: TextIO) -> None:
    """把一个超长行的剩余部分吃掉，好让下一条能正常读。"""
    while True:
        chunk = stdin.readline(65536)
        if not chunk or chunk.endswith("\n"):
            return


# ── 响应封装 ──


class _RpcError(Exception):
    """要走 JSON-RPC `error` 对象的那一类（协议级，不是工具执行失败）。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": error}


# ── 方法分派 ──


def _requested_version(params: Any) -> str | None:
    """`server/discover` 把版本放在 `params._meta.protocolVersion`；legacy 在 `params.protocolVersion`。"""
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if isinstance(meta, dict):
        v = meta.get("protocolVersion")
        if isinstance(v, str) and v:
            return v
    v = params.get("protocolVersion")
    return v if isinstance(v, str) and v else None


def _discover(params: Any) -> dict:
    requested = _requested_version(params)
    if requested is not None and requested not in SUPPORTED_VERSIONS:
        raise _RpcError(
            UNSUPPORTED_PROTOCOL,
            f"不支持的协议版本 {requested}",
            {"supported": list(SUPPORTED_VERSIONS), "requested": requested},
        )
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_VERSIONS),
        "capabilities": {"tools": {}},
        "instructions": INSTRUCTIONS,
        # 工具集是静态的、内容随库变：让客户端每次都用缓存，但不要跨用户共享
        "ttlMs": 0,
        "cacheScope": "private",
        "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
    }


def _initialize(params: Any) -> dict:
    """legacy（`2025-*` 系）握手。

    `protocolVersion` **原样回显客户端请求的值**：老客户端按自己那套规范发请求，
    回一个它没听过的版本号会让它直接断开。

    ⚠️ 这与 `server/discover` 的 `supportedVersions` **是两件事**，别为了「统一」把它们
    并成一份：那里声明的是「我们实现了哪些协议版本」（必须只列核实过的，否则是在承诺
    一套没实现的行为），这里是「本次会话按哪个版本走」（由客户端自己宣告，而工具一族
    在 v1 各版本间形状未变，回显它才是诚实的）。
    """
    requested = _requested_version(params)
    return {
        "resultType": "complete",
        "protocolVersion": requested or PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": SERVER_INFO,
    }


def _tools_call(params: Any, holder: Any) -> dict:
    if not isinstance(params, dict):
        raise _RpcError(INVALID_PARAMS, "tools/call 的 params 必须是对象")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _RpcError(INVALID_PARAMS, "tools/call 缺少工具名 `name`")
    try:
        result = mcp_tools.call_tool(name, params.get("arguments"), holder)
    except mcp_tools.McpUnknownTool:
        # 工具名不存在是**协议级**错误：模型改不了，得让客户端知道工具表过期了
        raise _RpcError(METHOD_NOT_FOUND, f"未知工具 {name}") from None
    except mcp_tools.McpInvalidArguments as e:
        raise _RpcError(INVALID_PARAMS, str(e)) from None
    result["resultType"] = "complete"
    return result


def _handle(req: Any, holder: Any) -> dict | None:
    """一条请求 → 一条响应；返回 `None` 表示「不回」（通知）。"""
    if not isinstance(req, dict):
        return _err(None, INVALID_REQUEST, "请求必须是 JSON 对象")

    # ⚠️ 通知的判据是「没有 `id` 成员」，不是 `id is None` —— 后者在 2.0 里是
    # 「一个 id 为 null 的请求」，回了响应才是对的
    is_notification = "id" not in req
    rid = req.get("id")

    method = req.get("method")
    if not isinstance(method, str) or not method:
        if is_notification:
            return None
        return _err(rid, INVALID_REQUEST, "缺少 `method`")

    # 通知一律不回 —— 回了会破坏客户端的协议流
    if method.startswith("notifications/") or is_notification:
        return None

    try:
        if method == "server/discover":
            return _ok(rid, _discover(req.get("params")))
        if method == "initialize":
            return _ok(rid, _initialize(req.get("params")))
        if method == "tools/list":
            return _ok(rid, {"tools": mcp_tools.list_tools(), "resultType": "complete"})
        if method == "tools/call":
            return _ok(rid, _tools_call(req.get("params"), holder))
        raise _RpcError(METHOD_NOT_FOUND, f"未知方法 {method}")
    except _RpcError as e:
        return _err(rid, e.code, e.message, e.data)
    except Exception as e:  # noqa: BLE001
        # ⚠️ 只回类型名：异常文案可能夹带本地路径。全文留 stderr。
        import traceback

        traceback.print_exc(file=sys.stderr)
        return _err(rid, INTERNAL_ERROR, type(e).__name__)


# ── 主循环 ──


def serve(stdin: TextIO, stdout: TextIO, holder: Any) -> int:
    """逐行读请求、逐行回响应，直到 EOF。**串行**：所有工具都是本地读，并发零收益。"""
    while True:
        raw = stdin.readline(MAX_LINE_BYTES)
        if not raw:
            return 0
        if not raw.endswith("\n") and len(raw) >= MAX_LINE_BYTES:
            _drain_line(stdin)
            _write(stdout, _err(None, INVALID_REQUEST,
                                f"请求行超过 {MAX_LINE_BYTES} 字节上限"))
            continue
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            # ⚠️ 坏一帧不退出：agent 那边可能只是某次拼接出了错，连接还能继续用
            _write(stdout, _err(None, PARSE_ERROR, f"JSON 解析失败：{e.msg}"))
            continue
        resp = _handle(req, holder)
        if resp is not None:
            _write(stdout, resp)


# ── 入口 ──


def make_holder(http: bool, base_url: str | None, timeout: float | None) -> Any:
    """`--http` 走 `StaticHolder`（后端保证新鲜），否则 `LocalHolder`（自己盯 `index.json`）。"""
    from .sources import HttpSource, LocalSource

    if http:
        return mcp_tools.StaticHolder(HttpSource(base_url=base_url, timeout=timeout))
    return mcp_tools.LocalHolder(lambda d: LocalSource(data_dir=d))


def _proto_stream() -> TextIO | None:
    """拿一条**独占**的 stdout 给协议用；拿不到返回 `None`。

    ⚠️ PyInstaller 的 `console=False` 打包会把 `sys.stdout` 置为 `None`，此时只能退回
    `sys.__stdout__` / fd 1。MCP 客户端总会给管道，故 fd 1 有效。

    ⚠️ 这也是为什么 `main.py` 的 `--mcp` 短路必须排在它自己的 stdout 归一化**之前**：
    归一化会把 `None` 换成 devnull，于是协议响应全写进黑洞、客户端只看到「连不上」，
    而我们这边一切正常（零报错）。
    """
    stream: TextIO | None = sys.stdout or getattr(sys, "__stdout__", None)
    if stream is None:
        try:
            stream = open(1, "w", encoding="utf-8", newline="\n", closefd=False)
        except OSError:
            return None
    try:
        # ⚠️ `newline="\n"`：Windows 默认会把 `\n` 翻成 `\r\n`，多出来的 `\r` 落在
        # JSON 之后就是客户端那侧的解析噪音
        stream.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    return stream


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # ⚠️ 从 `LiMoBackend.exe --mcp` 进来时 argv 里带着 `--mcp` 本身
    raw = [a for a in raw if a != "--mcp"]

    ap = argparse.ArgumentParser(
        prog="limo-mcp",
        description="LiMo MCP 服务端（stdio）——把本地知识库只读暴露给 AI 编程助手",
    )
    ap.add_argument("--http", action="store_true",
                    help="连正在运行的 LiMo（默认直开本机库，不需要先打开应用）")
    ap.add_argument("--base-url", default=None,
                    help="--http 时的后端地址（默认 http://127.0.0.1:7413）")
    ap.add_argument("--timeout", type=float, default=None, help="--http 时的请求超时（秒）")
    ap.add_argument("--version", action="version", version=f"limo-mcp {__version__}")
    args = ap.parse_args(raw)

    proto_out = _proto_stream()
    stdin = sys.stdin or getattr(sys, "__stdin__", None)
    if proto_out is None or stdin is None:
        # 只有「没有管道」这一种可能：客户端总会给 stdin/stdout
        return 2

    # ⚠️ **第一件事**：把 `sys.stdout` 换成 stderr。此后任何 print / 第三方库的
    # 输出都落在 stderr，协议通道只剩 `_write()` 在写。stderr 也没有就吞掉 ——
    # 宁可丢日志，也不能让一行日志把协议流冲掉。
    stderr = sys.stderr or getattr(sys, "__stderr__", None)
    if stderr is None:
        stderr = open(os.devnull, "w", encoding="utf-8", closefd=False)
    sys.stdout = stderr
    sys.stderr = stderr
    try:
        stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    try:
        return serve(stdin, proto_out, make_holder(args.http, args.base_url, args.timeout))
    finally:
        sys.stdout = proto_out


if __name__ == "__main__":
    raise SystemExit(main())
