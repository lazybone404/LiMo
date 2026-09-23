"""capabilities.missingKeys 检测逻辑（P2.1）— 纯函数单测，无需启动后端。

运行：uv run python tests/test_capabilities.py   （pytest tests/test_capabilities.py 亦可）
覆盖：llm 未配 / 已配 / 全局 key 同步 / 防错配（sk-ws/sk-ant 不同步）/ dashscope 引擎缺 key
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.routers.capabilities import _compute_missing_keys


def _cfg(llm_api_key="", providers=None, llm_models=None, dashscope="", global_provider="deepseek"):
    return {
        "llm": {"api_key": llm_api_key, "provider": global_provider},
        "providers": providers or [],
        "llm_models": llm_models or {"deepseek": [{"id": "deepseek-flash"}]},
        "cloud": {"dashscope": dashscope},
    }


def _missing_llm_ids(mk):
    return {m["id"] for m in mk["llm"]}


def test_all_unconfigured():
    """场景①：llm key 全空 → 所有 llm_models 厂商判未配；dashscope 空 → engine 缺 dashscope"""
    mk = _compute_missing_keys(_cfg(llm_models={"deepseek": [{}], "openai": [{}]}))
    assert _missing_llm_ids(mk) == {"deepseek", "openai"}
    assert mk["engine"] == ["dashscope"]


def test_provider_configured():
    """场景②：某 provider 配 key → 该厂商已配，其余未配"""
    cfg = _cfg(
        providers=[{"id": "deepseek", "api_key": "sk-abc", "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com", "type": "built-in", "enabled": 1}],
        llm_models={"deepseek": [{}], "openai": [{}]},
    )
    mk = _compute_missing_keys(cfg)
    assert _missing_llm_ids(mk) == {"openai"}


def test_global_key_synced_to_default_provider():
    """场景③：仅全局 llm.api_key 非空 → 同步进默认 provider（deepseek）→ 判已配"""
    mk = _compute_missing_keys(_cfg(llm_api_key="sk-123456", llm_models={"deepseek": [{}], "openai": [{}]}))
    assert _missing_llm_ids(mk) == {"openai"}


def test_mismatch_key_not_synced():
    """场景③扩展：全局 key 是 DashScope 格式（sk-ws-）配 deepseek → 防错配不同步 → deepseek 仍未配"""
    mk = _compute_missing_keys(_cfg(llm_api_key="sk-ws-xxx", llm_models={"deepseek": [{}]}))
    assert _missing_llm_ids(mk) == {"deepseek"}


def test_anthropic_key_not_synced_to_other():
    """场景③扩展：全局 key 是 Anthropic 格式（sk-ant-）配 deepseek → 不同步 → deepseek 未配"""
    mk = _compute_missing_keys(_cfg(llm_api_key="sk-ant-xxx", llm_models={"deepseek": [{}]}))
    assert _missing_llm_ids(mk) == {"deepseek"}


def test_dashscope_configured_no_engine_missing():
    """dashscope 已配 → engine 不报缺"""
    mk = _compute_missing_keys(_cfg(dashscope="sk-ws-xxx"))
    assert mk["engine"] == []


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL: {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if passed == len(tests) else 1)
