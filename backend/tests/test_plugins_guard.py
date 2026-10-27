"""插件管理端点的来源守卫（`ensure_trusted_request`）。

**为什么需要这个文件**：`/api/plugins/*` 是本应用里**副作用最大**的一组端点 ——
`POST /plugins/install` 往 `~/.limo/plugins/` 解压任意 zip（重启后那份 `.py` 会被
`exec_module` 执行），`install-deps` 会在插件目录里建 venv 并 pip 安装，`DELETE`
直接删目录。它们此前**一个都没过来源校验**。

跨站触发这条链不需要 preflight：`POST .../enable` / `.../install-deps`（无 body）与
`multipart/form-data` 的上传都属于「简单请求」，浏览器**直接发出**、CORS 只拦读响应
（见 `app/core/origin.py` 的模块文档）。

对照实验（均实测）：
  A. 删掉任一写端点的 `ensure_trusted_request(request)` → 对应的参数化用例红
  B. 把 `ensure_trusted_request` 挪到 `_require_plugin(name)` **之后** →
     `test_守卫排在存在性校验之前` 红（会先报 PLUGIN_NOT_FOUND）
"""
import os
import shutil
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi.testclient import TestClient

from main import app

#: 陌生来源：浏览器发起跨站请求时必带 Origin，且不在白名单里
_EVIL = {"Origin": "https://evil.example"}

#: 前端 `apiFetch` 每个请求都带它（`frontend/src/api/core.ts::CLIENT_HEADERS`）
_CLIENT = {"X-LiMo-Client": "1"}


def _setup() -> TestClient:
    """只注入依赖解析需要的东西 —— **不进 `TestClient` 上下文**（不跑 startup）。

    `get_config` / `get_config_lock` 是 `Depends`，会在进入函数体**之前**求值，
    故 `app.state.config` 必须存在，否则拿到的是 500 而不是 403。
    """
    d = tempfile.mkdtemp(prefix="limo_plugins_guard_")
    app.state.config = {"data_dir": d, "plugin_states": {}, "plugin_configs": {}}
    app.state.config_lock = threading.Lock()
    return TestClient(app)


def _cleanup(c: TestClient) -> None:
    shutil.rmtree(app.state.config.get("data_dir", ""), ignore_errors=True)


#: (method, path, kwargs) —— 六个写端点逐个钉住
WRITE_ENDPOINTS = [
    ("post", "/api/plugins/install",
     {"files": {"file": ("x.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")}}),
    ("post", "/api/plugins/nope/enable", {}),
    ("post", "/api/plugins/nope/disable", {}),
    ("delete", "/api/plugins/nope", {}),
    ("post", "/api/plugins/nope/install-deps", {}),
    ("put", "/api/plugins/nope/config", {"json": {}}),
]


@pytest.mark.parametrize("method,path,kwargs", WRITE_ENDPOINTS,
                         ids=[f"{m.upper()} {p}" for m, p, _ in WRITE_ENDPOINTS])
def test_写端点拒绝陌生来源(method, path, kwargs):
    """陌生 Origin → 403 `UNTRUSTED_ORIGIN`，且**什么都没做**。"""
    c = _setup()
    try:
        r = getattr(c, method)(path, headers=_EVIL, **kwargs)
        assert r.status_code == 403, f"{method.upper()} {path} 应被拒，实际 {r.status_code}: {r.text}"
        assert r.json()["code"] == "UNTRUSTED_ORIGIN", r.text
    finally:
        _cleanup(c)


def test_守卫排在存在性校验之前():
    """不存在的插件 + 陌生来源 → 403，而不是 `PLUGIN_NOT_FOUND`。

    ⚠️ 这条钉的是**顺序**：先判来源再查存在性，攻击者拿不到「这个插件名存不存在」
    这个探测信号，也确保拒绝发生在任何业务动作之前。
    """
    c = _setup()
    try:
        r = c.post("/api/plugins/不存在/enable", headers=_EVIL)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "UNTRUSTED_ORIGIN", r.text
    finally:
        _cleanup(c)


def test_合法客户端不被拦():
    """带 `X-LiMo-Client` 的请求照常进入业务（这里用不存在的插件验证到不了 403）。

    ⚠️ 与上面的用例**成对**：只测拒绝的话，把守卫写成「一律 403」也会全绿。
    """
    c = _setup()
    try:
        r = c.post("/api/plugins/nope/enable", headers=_CLIENT)
        assert r.status_code != 403, r.text
        assert r.json()["code"] == "PLUGIN_NOT_FOUND", r.text
    finally:
        _cleanup(c)


def test_本机来源不被拦():
    """`Origin: http://localhost:5173`（Vite devUrl）在白名单里 —— 开发模式零回归。"""
    c = _setup()
    try:
        r = c.post("/api/plugins/nope/enable", headers={"Origin": "http://localhost:5173"})
        assert r.status_code != 403, r.text
        assert r.json()["code"] == "PLUGIN_NOT_FOUND", r.text
    finally:
        _cleanup(c)
