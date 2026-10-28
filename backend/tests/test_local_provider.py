"""本地推理厂商（Ollama）—— 不需要 API Key 的那一类。

**解决的是什么**：「本地优先的知识库，模型却必须上云」是最刺眼的闭环缺口。而接本地
模型的第一道坎不是协议（Ollama 有 OpenAI 兼容层），是**全仓 13 处「没 Key 就报错」**
的硬检查：逐处 `if provider == "ollama"` 就是模式 #54（同源判据散落），漏一处那个入口
就报「未配置 API Key」而界面完全正常。

所以本测试守两件事：
1. **判据只有一处**（`provider_requires_api_key`），且 13 处调用点都真的调了它；
2. **放行是精准的**——非 keyless 厂商空 Key 仍然报错（负向对照）。

⚠️ 全程 mock，不连真实 Ollama：门禁不该依赖用户机器上装没装某个服务。
"""
import ast
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.provider_defaults import (
    KEYLESS_API_KEY_PLACEHOLDER,
    KEYLESS_PROVIDERS,
    PROVIDER_DEFAULTS,
    provider_requires_api_key,
)

_APP = Path(__file__).resolve().parent.parent / "app"


# ── ① 表与判据本身 ──

def test_ollama_is_a_builtin_provider():
    info = PROVIDER_DEFAULTS["ollama"]
    assert info["base_url"].endswith("/v1"), "Ollama 的 OpenAI 兼容层挂在 /v1，缺了会 404"
    assert "localhost" in info["base_url"] or "127.0.0.1" in info["base_url"]


def test_keyless_set_is_minimal():
    """⚠️ 判据是「**这个集合里只有本地厂商**」，不是「里面有 ollama」。

    把它扩成 `{"ollama", "opencode"}` 之类的顺手写法，后果是那个厂商空 Key 时不再报错、
    而是带着占位串去打真实服务 —— 用户看到的是 401 而不是「请先填 Key」。
    """
    assert KEYLESS_PROVIDERS == frozenset({"ollama"})
    assert provider_requires_api_key("ollama") is False
    assert provider_requires_api_key("deepseek") is True
    assert provider_requires_api_key("") is True


def test_placeholder_is_not_empty():
    """`api_key=None` 会被 SDK 直接拒（OpenAIError）；空串能构造但服务端当非法。
    占位串让「不需要真 Key」在传输层也成立。"""
    assert KEYLESS_API_KEY_PLACEHOLDER
    assert not KEYLESS_API_KEY_PLACEHOLDER.startswith("sk-ws-")  # 别撞 DashScope 的格式告警
    assert not KEYLESS_API_KEY_PLACEHOLDER.startswith("sk-ant-")


# ── ② LLMService.client ──

def _config(**llm):
    return {"llm": {"provider": "ollama", "model": {"ollama": "qwen3:8b"}, **llm},
            "providers": [], "llm_models": {}}


def test_client_built_with_placeholder_for_keyless():
    from app.llm.service import LLMService

    svc = LLMService(_config(), model_id="")
    assert svc.provider == "ollama"
    with patch("app.llm.service.AsyncOpenAI") as fake:
        _ = svc.client
        kwargs = fake.call_args.kwargs
    assert kwargs["api_key"] == KEYLESS_API_KEY_PLACEHOLDER
    assert kwargs["api_key"], "空串会让服务端当非法凭据"


def test_client_still_rejects_empty_key_for_normal_provider():
    """⚠️ 负向对照 —— 这条证明放行是**精准的**，不是把检查拆了。

    把 `client` 里的 `provider_requires_api_key(self.provider)` 去掉（或写成恒 False），
    本用例立刻红：deepseek 空 Key 会带着占位串去打真实 API。
    """
    from app.llm.errors import LLMError
    from app.llm.service import LLMService

    cfg = {"llm": {"provider": "deepseek", "model": {"deepseek": "deepseek-flash"}},
           "providers": [], "llm_models": {}}
    svc = LLMService(cfg, model_id="")
    try:
        _ = svc.client
    except LLMError as e:
        assert "缺少 API Key" in str(e)
    else:
        raise AssertionError("非 keyless 厂商空 Key 必须报错")


# ── ③ test_connection 不发 Authorization ──

def test_test_connection_omits_auth_header_for_keyless():
    from app.routers import model as model_router

    prov = {"id": "ollama", "name": "Ollama（本地）",
            "base_url": "http://localhost:11434/v1", "api_key": "", "enabled": 1}
    fake_resp = MagicMock(status_code=200)
    with patch.object(model_router, "_get_cfg", return_value={}), \
         patch.object(model_router, "_get_providers", return_value=[prov]), \
         patch.object(model_router.requests, "get", return_value=fake_resp) as m:
        out = model_router.test_connection("ollama", MagicMock(), config={})
    assert out["ok"] is True
    headers = m.call_args.kwargs["headers"]
    assert "Authorization" not in headers, "本地服务不该收到一个假的 Bearer 串"


def test_test_connection_sends_auth_header_for_normal_provider():
    """⚠️ 负向对照：与上一条**方向相反**，证明不是把 Authorization 一律删了。"""
    from app.routers import model as model_router

    prov = {"id": "deepseek", "name": "DeepSeek",
            "base_url": "https://api.deepseek.com", "api_key": "sk-real", "enabled": 1}
    fake_resp = MagicMock(status_code=200)
    with patch.object(model_router, "_get_cfg", return_value={}), \
         patch.object(model_router, "_get_providers", return_value=[prov]), \
         patch.object(model_router.requests, "get", return_value=fake_resp) as m:
        model_router.test_connection("deepseek", MagicMock(), config={})
    assert m.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-real"


def test_test_connection_still_rejects_empty_key_for_normal_provider():
    from app.routers import model as model_router

    prov = {"id": "deepseek", "name": "DeepSeek",
            "base_url": "https://api.deepseek.com", "api_key": "", "enabled": 1}
    with patch.object(model_router, "_get_cfg", return_value={}), \
         patch.object(model_router, "_get_providers", return_value=[prov]):
        out = model_router.test_connection("deepseek", MagicMock(), config={})
    assert out["ok"] is False and "未配置 API Key" in out["error"]


# ── ④ 13 处调用点的守卫（AST，不看注释） ──

def _find_bare_api_key_checks(tree: ast.AST, src: str, path: Path) -> list[str]:
    """找出「`if not <xxx>.api_key`」形式且**没有** `provider_requires_api_key` 参与的分支。

    ⚠️ 判据只看**真代码**：注释里讨论这个属性是合法的（`routers/search.py` 那一课）。
    """
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        seg = ast.get_source_segment(src, node.test) or ""
        if not seg:
            continue
        # 只关心「单独的 `not xxx.api_key`」；组合条件里带收口判据的放行
        if "provider_requires_api_key" in seg:
            continue
        if seg.strip().startswith("not ") and seg.strip().endswith(".api_key"):
            # 引擎侧的 api_key 属于 `cloud.dashscope`，与 LLM provider 无关
            if "engine" in seg or "self.api_key" in seg:
                continue
            hits.append(f"{path.name}:{node.lineno}  {seg.strip()}")
    return hits


def test_no_bare_api_key_check_outside_the_gate():
    """⚠️ 这是本项最容易漏的那一半的守卫。

    漏改一处的失效形态：那个入口对本地模型报「未配置 API Key」，而**界面完全正常**——
    用户点了按钮才失败，且失败原因是错的。
    """
    allowed = {
        # keyless 分支自己就在判定，它**就是**判据的实现处
        "service.py",
        # 引擎侧（cloud.dashscope），与 LLM provider 无关
        "ocr.py", "asr_cloud.py", "embedding_dashscope.py",
        # config_io 的脱敏判断，不是「缺 Key」检查
        "config_io.py",
    }
    bad: list[str] = []
    for p in _APP.rglob("*.py"):
        if p.name in allowed:
            continue
        src = p.read_text(encoding="utf-8")
        bad.extend(_find_bare_api_key_checks(ast.parse(src), src, p))
    assert not bad, "裸空 Key 检查必须走 provider_requires_api_key：\n" + "\n".join(bad)


def test_gate_is_actually_used():
    """⚠️ 与上一条**成对**：上一条只说「没有裸检查」，一条都不调也能满足它。
    这条钉住「13 处真的调了收口判据」。"""
    users = [p for p in _APP.rglob("*.py")
             if "provider_requires_api_key" in p.read_text(encoding="utf-8")]
    names = {p.name for p in users}
    for expected in ("chat.py", "classify.py", "generate.py", "process.py",
                     "synthesis.py", "capabilities.py", "model_resolver.py",
                     "model.py", "config.py", "service.py"):
        assert expected in names, f"{expected} 没有调用 provider_requires_api_key"
