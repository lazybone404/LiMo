"""`limo_cli/sources.py::HttpSource` —— **发出去的查询串**才是判据

为什么单独一个文件：这类 bug 逐字读代码看不出来。

`_page_args` 产出的键叫 `query`（它同时喂着 `LocalSource` 那边
`QueryService.list_resources(query=…)` 的关键字参数），而后端
`routers/cli.py::cli_list_resources` 的形参叫 **`q`**。名字对不上时 FastAPI 对
**未知查询参数静默忽略** —— 请求合法、HTTP 200、契约字段齐全，只是关键词筛选凭空
消失（返回全库）。既有的 `cli` 门禁也照绿：它调 `--local`，根本不走 HTTP。

所以这里断言的是 **URL 里的 query string**，不是「代码里传了什么」。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from limo_cli.sources import HttpSource, _page_args  # noqa: E402

PAGE = {"schema_version": "1", "items": [], "has_more": False,
        "next_cursor": None, "truncated": False, "continuation": None}


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _query_of(monkeypatch, call) -> dict:
    """跑 `call(src)`，返回那次 HTTP 请求的 query string（空值保留）。"""
    seen: dict = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        return _Resp(PAGE)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    call(HttpSource("http://127.0.0.1:7011"))
    assert "url" in seen, "没有发出请求"
    qs = urllib.parse.urlparse(seen["url"]).query
    return urllib.parse.parse_qs(qs, keep_blank_values=True)


def test_list_resources_的_query_在_url_里必须叫_q(monkeypatch):
    """曾经是 `?query=…` —— 后端只认 `q`，于是关键词筛选静默失效、返回全库。"""
    qs = _query_of(monkeypatch,
                   lambda s: s.list_resources(query="检索重排", limit=20))
    assert qs["q"] == ["检索重排"]
    assert "query" not in qs, "后端没有这个形参；FastAPI 会静默忽略它"
    assert qs["limit"] == ["20"]


def test_folder_id_的空串必须保留(monkeypatch):
    """`""` = 只看未分类，是**有效筛选**；丢掉它就变成「不过滤」。"""
    qs = _query_of(monkeypatch, lambda s: s.list_resources(folder_id=""))
    assert qs["folder_id"] == [""]


def test_all_pages_带_all_true_且不传_limit(monkeypatch):
    """HTTP 侧「一次取全」是 `all=true` + 不传 limit（后端默认 50 ≠ 全部）。"""
    qs = _query_of(monkeypatch, lambda s: s.list_resources(all_pages=True))
    assert qs["all"] == ["true"]
    assert "limit" not in qs

    qs = _query_of(monkeypatch, lambda s: s.list_resources())
    assert "all" not in qs
    assert qs["limit"] == ["50"]


def test_page_args_仍然产出_query_键():
    """本地侧的键名动不得 —— 它直接当 `QueryService.list_resources` 的关键字参数。

    改这一处去迁就 HTTP，`LocalSource` 会当场 `TypeError`；所以两边各叫各的名字，
    映射只放在 `HttpSource.list_resources` 一个地方。
    """
    args = _page_args(None, None, None, "x", None)
    assert args["query"] == "x"
    assert "q" not in args
