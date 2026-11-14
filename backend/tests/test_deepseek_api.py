"""DeepSeek 端点 / Key / 错误翻译的唯一住处（`core/deepseek_api.py`）。

这个文件看守三件事，失效形态都是「不报错但行为错」：

1. **五个端点只写一份**。`agent/tools.py` 曾经自带 `api.deepseek.com` 字面量；
   新增 OCR / ASR / Files 三处后若各写一份，换域名时漏改一处就是静默 404。
   守卫用 AST 读源码（文本搜索会被注释里引用的域名误伤）。
2. **Key 四级回退顺序固定**。乱序不会报错，只会在某天取到另一把 Key 然后 401。
3. **状态码翻译**。`recoverable` 写反时，前端会把「余额不足」当成可重试，
   用户会一直点重试而账单提醒永远不来。
"""

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.core.deepseek_api import (
    DEEPSEEK_BASE_URL,
    FILES_URL,
    TOOL_ASR_URL,
    TOOL_FETCH_URL,
    TOOL_OCR_URL,
    TOOL_SEARCH_URL,
    check_response,
    deepseek_api_key,
)
from app.core.errors import AppError
from app.core.provider_defaults import PROVIDER_DEFAULTS


# ── 端点 ──

def test_endpoints_match_official_paths():
    """路径是官方文档给的字面量；域名取自 PROVIDER_DEFAULTS（不复制第二份）。"""
    base = PROVIDER_DEFAULTS["deepseek"]["base_url"].rstrip("/")
    assert DEEPSEEK_BASE_URL == base
    assert TOOL_SEARCH_URL == base + "/v1/tools/search"
    assert TOOL_FETCH_URL == base + "/v1/tools/fetch"
    assert TOOL_OCR_URL == base + "/v1/tools/ocr"
    assert TOOL_ASR_URL == base + "/v1/tools/asr"
    assert FILES_URL == base + "/files"


def test_agent_tools_has_no_own_endpoint_literal():
    """`agent/tools.py` 不得自带域名 —— 端点必须从本模块 import。

    ⚠️ 用 AST 而不是文本搜索：文件里的注释会引用域名（解释「为什么不从 base_url
    拼」），文本搜索会把注释算进去，守住的不是代码而是文档。
    """
    path = os.path.join(os.path.dirname(__file__), "..", "app", "agent", "tools.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    offenders = [s for s in literals if "api.deepseek.com" in s]
    assert not offenders, f"agent/tools.py 自带端点字面量：{offenders}"

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.deepseek_api":
            imported |= {alias.name for alias in node.names}
    assert {"TOOL_SEARCH_URL", "TOOL_FETCH_URL", "deepseek_api_key"} <= imported, \
        f"agent/tools.py 未从 deepseek_api 导入端点/Key：实际 {sorted(imported)}"


# ── Key 四级回退 ──

def test_key_explicit_wins_everything():
    cfg = {
        "agent_tools": {"search": {"deepseek_api_key": "at"}},
        "providers": [{"id": "deepseek", "api_key": "pv"}],
        "engine_providers": [{"id": "deepseek", "api_key": "ep"}],
    }
    assert deepseek_api_key(cfg, "explicit") == "explicit"


def test_key_falls_back_in_documented_order():
    cfg = {
        "agent_tools": {"search": {"deepseek_api_key": "at"}},
        "providers": [{"id": "deepseek", "api_key": "pv"}],
        "engine_providers": [{"id": "deepseek", "api_key": "ep"}],
    }
    assert deepseek_api_key(cfg) == "at"

    cfg["agent_tools"]["search"]["deepseek_api_key"] = ""
    assert deepseek_api_key(cfg) == "pv"

    cfg["providers"] = []
    assert deepseek_api_key(cfg) == "ep"

    cfg["engine_providers"] = []
    assert deepseek_api_key(cfg) == ""


def test_key_ignores_lookalike_providers():
    """只认 `id == "deepseek"`，不按名字或 base_url 猜 —— 猜错就是拿别人的 Key 发请求。"""
    cfg = {
        "providers": [{"id": "my-deepseek-proxy", "api_key": "other"},
                      {"id": "x", "name": "DeepSeek", "base_url": "https://api.deepseek.com"}],
    }
    assert deepseek_api_key(cfg) == ""


def test_key_survives_none_config():
    assert deepseek_api_key(None) == ""
    assert deepseek_api_key({}) == ""


# ── 错误翻译 ──

class _Resp:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_2xx_returns_json():
    assert check_response(_Resp(200, {"ok": 1}), "OCR 识别") == {"ok": 1}


def test_2xx_with_bad_json_raises():
    with pytest.raises(AppError) as ei:
        check_response(_Resp(200, None, "<html>gateway</html>"), "OCR 识别")
    assert ei.value.code.endswith("_BAD_JSON")
    assert "<html>" in ei.value.detail


@pytest.mark.parametrize("status,recoverable", [
    (400, False),
    (402, False),
    (408, True),
    (413, False),
    (429, True),
    (500, True),
    (503, True),
    (504, True),
])
def test_status_translation(status, recoverable):
    with pytest.raises(AppError) as ei:
        check_response(_Resp(status, None, "upstream body"), "语音转写")
    err = ei.value
    assert err.code == f"DEEPSEEK_HTTP_{status}"
    assert err.recoverable is recoverable
    assert "语音转写" in err.message, "错误消息必须带上「在做什么」，否则三个功能会互相冒认"
    assert "upstream body" in err.detail


def test_unknown_status_is_reported_verbatim():
    """未知状态码不硬猜原因 —— 猜错的提示比没有提示更糟。"""
    with pytest.raises(AppError) as ei:
        check_response(_Resp(418, None, "teapot"), "上传文档")
    assert ei.value.code == "DEEPSEEK_HTTP_418"
    assert "418" in ei.value.message
