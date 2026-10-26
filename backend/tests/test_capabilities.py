"""capabilities.missingKeys 检测逻辑（P2.1）— 纯函数单测，无需启动后端。

运行：uv run python tests/test_capabilities.py   （pytest tests/test_capabilities.py 亦可）
覆盖：llm 未配 / 已配 / 全局 key 同步 / 防错配（sk-ws/sk-ant 不同步）/ dashscope 引擎缺 key
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.routers.capabilities import _compute_missing_keys, _dimension_presets
from app.generation.synthesis import (
    CONTENT_TYPES, DEFAULT_DIMENSIONS, DIMENSION_PRESET_LABELS, DIMENSION_PRESETS,
    READER_INTENTS,
)


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


# ── dimensionPresets：与 synthesis 常量同源（前端不硬编码预设名）──
# 对照实验：在 capabilities 里**手写一份**预设列表（图快、或只改了文案）→
# 下面的逐值相等断言必红。这正是「引擎前端后端驱动」原则在维度上的落地。


def test_dimension_presets_from_synthesis():
    ps = _dimension_presets()
    assert [p["key"] for p in ps] == list(DIMENSION_PRESETS), "键与顺序必须来自 synthesis"
    for p in ps:
        assert p["label"] == DIMENSION_PRESET_LABELS[p["key"]], f'{p["key"]} 文案来源不对'
        assert p["dimensions"] == DIMENSION_PRESETS[p["key"]], f'{p["key"]} 维度值不同源'
    assert all(p["label"] and p["dimensions"] for p in ps), "不得有空白标签或空维度"
    # 默认套必须真在其中——生成侧回退用的就是它。且必须**排在第一位**：
    # 前端对话框取 `dimensionPresets[0]` 作默认选中项（`firstPreset`），
    # 插在后面 = 后端换了默认、前端仍静默预填 paper，零报错。
    assert ps[0]["key"] == "general", "默认套必须排在第一位（前端取 [0] 作默认）"
    assert ps[0]["dimensions"] == DEFAULT_DIMENSIONS
    # paper 仍在（历史文档回退标 / 用户手动选预设都要用它），且已与默认**分离**
    paper = [p for p in ps if p["key"] == "paper"][0]
    assert paper["dimensions"] == DIMENSION_PRESETS["paper"]
    assert paper["dimensions"] != DEFAULT_DIMENSIONS, \
        "默认套与 paper 必须是两套（换默认套时只改常量不改指向 → 此条红）"


# ── contentTypes / readerIntents：与 synthesis 常量同源 ──
# 同一精神（对照组：在 capabilities 里手写一份体裁列表 → 下面的断言必红）。


def test_content_types_from_synthesis():
    from app.routers.capabilities import _content_types
    ct = _content_types()
    assert [c["key"] for c in ct] == list(CONTENT_TYPES), "键与顺序必须来自 synthesis"
    assert all(c["label"] for c in ct), "不得有空白体裁标签"
    # 体裁与维度预设的键必须重叠（同一材料的两个侧面，见 synthesis 的注释）
    assert set(CONTENT_TYPES) <= set(DIMENSION_PRESETS)


def test_reader_intents_default_content_type_within_enum():
    """`defaultContentType` 允许为 None（「理解主题」这类不预设体裁），
    但**一旦有值就必须是合法体裁**。

    ⚠️ 断言写成单条件 `in CONTENT_TYPES` → understand/compare/custom 三条全红：
    与本设计「不重演静默预填 paper 那种错配」的意图直接冲突。这三条恰恰是
    最需要保持「无建议」的。
    """
    from app.routers.capabilities import _reader_intents
    for it in _reader_intents():
        dct = it["defaultContentType"]
        assert dct is None or dct in CONTENT_TYPES, \
            f'{it["key"]} 的建议体裁 {dct!r} 不在枚举内'
    assert all(it["label"] for it in _reader_intents()), "不得有空白意图标签"
    assert all(it["purpose"] or it["key"] == "custom" for it in _reader_intents()), \
        "除 custom（用户自填）外，每个意图都必须自带读者目的文案"


def test_release_policies_from_vector_worker():
    """三档释放策略的清单必须**逐档来自** `vector_worker.RELEASE_POLICIES`。

    ⚠️ 这是「后端拥有的枚举经 capabilities 下发」那条纪律的守卫：`maybe_release`
    按 key 分派，加一档只改后端 —— 若这里手抄一份（哪怕今天逐字相同），加档时
    设置页就永远看不到它，而**没有任何东西会报错**（同 `styleLabels` /
    `dimensionPresets` / `readerIntents`）。顺序也要跟：前端按下发顺序渲染。
    """
    from app.routers.capabilities import _release_policies
    from app.services.vector_worker import DEFAULT_RELEASE, RELEASE_POLICIES

    policies = _release_policies()
    assert [p["key"] for p in policies] == list(RELEASE_POLICIES), \
        "键与顺序必须来自 vector_worker.RELEASE_POLICIES（前端按此顺序渲染）"
    assert all(p["label"] for p in policies), "不得有空白档位标签"
    # 只写「省内存 / 不省内存」等于把选择压成一个没有信息的偏好 ——
    # 三档真正的差别是「哪一刻要等模型载入」，必须说清代价。
    assert all(len(p["hint"]) > 10 for p in policies), "每档都必须说清代价，不能是占位文案"

    # 默认档必须在下发清单里：前端把它当作 `vec.release` 缺失时的兜底高亮，
    # 漏了会渲染出一组「一个都没选中」的按钮。
    assert DEFAULT_RELEASE in {p["key"] for p in policies}


def test_dimension_presets_are_copies():
    """返回值不得与模块常量共享可变对象——否则调用方一次 append 污染全局预设"""
    ps = _dimension_presets()
    ps[0]["dimensions"].append("污染项XYZ")
    assert "污染项XYZ" not in DIMENSION_PRESETS[ps[0]["key"]]
    assert _dimension_presets()[0]["dimensions"] == DIMENSION_PRESETS[ps[0]["key"]]


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
