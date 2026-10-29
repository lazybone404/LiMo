"""MCP 协议层（`limo_cli/mcp_server.py`）—— 帧、分派、错误码、双栈握手

直接拿两个 `StringIO` 喂 `serve()`，不起子进程：协议层的职责是「一行 JSON 进、
一行 JSON 出」，子进程只多一个「stdout 有没有被污染」的变量，那个归 `scripts/check-mcp`。

| 断言 | 写错会怎样 |
|---|---|
| 通知不回响应 | 客户端收到一条它没在等的响应，协议流直接错位 |
| 坏 JSON 回 `-32700` 且**继续读** | 一帧坏掉整条连接就死，agent 那边表现成「MCP 服务器崩了」 |
| 超长行回 `-32600` 且**吃掉剩余** | 不吃完的话下一行读进来的是**上一行的尾巴**，那半截会被当新请求 |
| 未知方法 `-32601` / 未知工具 `-32601` | 客户端不知道工具表过期了，只会反复重试同一个名字 |
| `discover` 版本不支持 → `-32022` + `data.supported` | 客户端只知道「失败」，不知道该退到哪个版本 |
| `initialize` 回显客户端版本 | 老客户端收到没听过的版本号直接断开 |
| **所有 result 带 `resultType`** | v2 客户端把它当必填字段，缺了整条响应作废 |

⚠️ **`supportedVersions` 只列核实过的版本**（当前 `2026-07-28`）：写一个自己没实现的
历史版本号 = 承诺一套没实现的行为，协商到它之后双方都不报错，只是语义全错。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from limo_cli import mcp_server, mcp_tools  # noqa: E402


class _MinimalSource:
    """协议层只关心「调通了没有」，故只补齐会被调到的几个方法。"""

    def status(self) -> dict:
        return {"schema_version": "1", "docCount": 3}

    def get_resource(self, doc_id: str) -> dict:
        return {"schema_version": "1", "id": doc_id, "rawText": "原文"}

    def list_artifacts(self, doc_id: str) -> dict:
        return {"schema_version": "1", "docId": doc_id, "items": []}


def _holder():
    return mcp_tools.StaticHolder(_MinimalSource())


def run(lines, holder=None, monkeypatch=None) -> list[dict]:
    """把若干行喂给 `serve()`，收回来的 JSON 响应按顺序返回。"""
    stdin = io.StringIO("\n".join(lines) + ("\n" if lines else ""))
    stdout = io.StringIO()
    code = mcp_server.serve(stdin, stdout, holder or _holder())
    assert code == 0, "EOF 之后应返回 0"
    return [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]


def req(rid, method, params=None) -> str:
    o = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        o["params"] = params
    return json.dumps(o, ensure_ascii=False)


# ── 帧 ──

def test_空行跳过():
    out = run(["", "   ", req(1, "tools/list"), ""])
    assert len(out) == 1 and out[0]["id"] == 1


def test_CRLF_能解析():
    out = run([req(1, "tools/list") + "\r"])
    assert out[0]["id"] == 1 and "tools" in out[0]["result"]


def test_坏JSON回负32700且继续读():
    out = run(["{不是 JSON", req(2, "tools/list")])
    assert out[0]["error"]["code"] == mcp_server.PARSE_ERROR
    assert out[0]["id"] is None
    assert out[1]["id"] == 2, "坏一帧不该让整条连接死掉"


def test_超长行回负32600且吃完剩余(monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_LINE_BYTES", 80)
    out = run(["x" * 200, req(9, "tools/list")])
    assert out[0]["error"]["code"] == mcp_server.INVALID_REQUEST
    # ⚠️ 第二行必须是**真的下一行**，不是超长行的尾巴
    assert out[1]["id"] == 9


def test_EOF干净退出():
    assert mcp_server.serve(io.StringIO(""), io.StringIO(), _holder()) == 0


# ── 通知 ──

def test_通知不回响应():
    out = run([
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}}',
        req(1, "tools/list"),
    ])
    assert len(out) == 1, "通知回了响应会破坏客户端的协议流"
    assert out[0]["id"] == 1


def test_不带id的方法调用也不回():
    """⚠️ 判据是「没有 `id` 成员」，不是 `id is None`。"""
    out = run(['{"jsonrpc":"2.0","method":"tools/list"}'])
    assert out == []


# ── 错误码 ──

def test_未知方法负32601():
    out = run([req(1, "resources/list")])
    assert out[0]["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_缺method负32600():
    out = run(['{"jsonrpc":"2.0","id":1}'])
    assert out[0]["error"]["code"] == mcp_server.INVALID_REQUEST


def test_method不是字符串负32600():
    out = run(['{"jsonrpc":"2.0","id":1,"method":42}'])
    assert out[0]["error"]["code"] == mcp_server.INVALID_REQUEST


def test_顶层不是对象负32600():
    for bad in ["[1,2]", '"字符串"', "42", "null"]:
        out = run([bad])
        assert out[0]["error"]["code"] == mcp_server.INVALID_REQUEST, bad


def test_未知工具负32601():
    out = run([req(1, "tools/call", {"name": "limo_nope", "arguments": {}})])
    assert out[0]["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_缺工具名与参数类型不对负32602():
    for params in ({}, {"name": ""}, {"name": 42},
                   {"name": "limo_status", "arguments": "不是对象"}):
        out = run([req(1, "tools/call", params)])
        assert out[0]["error"]["code"] == mcp_server.INVALID_PARAMS, params


def test_内部异常只回类型名(monkeypatch):
    """⚠️ 异常文案可能夹带本地路径，全文只进 stderr。"""
    class Boom:
        def status(self):
            raise RuntimeError("C:\\Users\\某人\\秘密路径出问题了")

    out = run([req(1, "tools/call", {"name": "limo_status", "arguments": {}})],
              holder=mcp_tools.StaticHolder(Boom()))
    assert out[0]["error"]["code"] == mcp_server.INTERNAL_ERROR
    assert out[0]["error"]["message"] == "RuntimeError"
    assert "秘密路径" not in json.dumps(out[0], ensure_ascii=False)


# ── 双栈握手 ──

def test_discover_支持当前版本():
    out = run([req(1, "server/discover",
                   {"_meta": {"protocolVersion": mcp_server.PROTOCOL_VERSION}})])
    result = out[0]["result"]
    assert result["resultType"] == "complete"
    assert mcp_server.PROTOCOL_VERSION in result["supportedVersions"]
    assert result["capabilities"] == {"tools": {}}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "limo"
    assert result["instructions"].strip()
    assert result["cacheScope"] == "private"


def test_discover_不传版本也认():
    out = run([req(1, "server/discover", {})])
    assert out[0]["result"]["supportedVersions"]


def test_discover_版本不支持负32022带_supported():
    out = run([req(1, "server/discover",
                   {"_meta": {"protocolVersion": "1999-01-01"}})])
    err = out[0]["error"]
    assert err["code"] == mcp_server.UNSUPPORTED_PROTOCOL
    assert err["data"]["requested"] == "1999-01-01"
    assert err["data"]["supported"] == list(mcp_server.SUPPORTED_VERSIONS)


def test_supportedVersions_只列核实过的版本():
    """⚠️ 这条是纪律守卫：加版本号之前先确认我们真按那套规范实现了。"""
    assert mcp_server.SUPPORTED_VERSIONS == ("2026-07-28",)


def test_initialize_回显客户端版本():
    """老客户端按自己那套规范发请求，回一个它没听过的版本号会让它断开。"""
    out = run([req(1, "initialize", {"protocolVersion": "2025-06-18"})])
    assert out[0]["result"]["protocolVersion"] == "2025-06-18"


def test_initialize_没传版本时给当前版本():
    out = run([req(1, "initialize", {})])
    assert out[0]["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION


def test_initialize_带能力与服务器信息():
    result = run([req(1, "initialize", {"protocolVersion": "2025-06-18"})])[0]["result"]
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "limo"


# ── 工具方法 ──

def test_tools_list_六个工具():
    result = run([req(1, "tools/list")])[0]["result"]
    assert len(result["tools"]) == 6
    assert {t["name"] for t in result["tools"]} == set(mcp_tools._HANDLERS)


def test_tools_call_返回内容与结构化载荷():
    result = run([req(1, "tools/call",
                      {"name": "limo_status", "arguments": {}})])[0]["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_工具级错误走_isError_不走协议错误():
    result = run([req(1, "tools/call",
                      {"name": "limo_get", "arguments": {}})])[0]["result"]
    assert result["isError"] is True
    assert "id" in result["content"][0]["text"]


# ── resultType 恒在场 ──

def test_所有_result_都带_resultType():
    out = run([
        req(1, "server/discover", {}),
        req(2, "initialize", {"protocolVersion": "2025-06-18"}),
        req(3, "tools/list"),
        req(4, "tools/call", {"name": "limo_status", "arguments": {}}),
        req(5, "tools/call", {"name": "limo_nope", "arguments": {}}),   # 这条是 error
    ])
    for resp in out:
        if "result" in resp:
            assert resp["result"]["resultType"] == "complete", resp["id"]
        else:
            assert "resultType" not in resp["error"], "error 对象不带 resultType"


# ── 串行与顺序 ──

def test_响应顺序与请求顺序一致():
    out = run([req(i, "tools/list") for i in range(5)])
    assert [r["id"] for r in out] == [0, 1, 2, 3, 4]
