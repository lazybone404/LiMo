"""模型能力自动检测 + 上下文窗口兜底（纯函数单测，无需启动后端）。

运行：uv run python tests/test_model_caps.py
覆盖：include/exclude 判据（含 "*" 分支必须仍走 exclude）/ deepseek 规则 / 视频精确匹配 /
      DEFAULT_CONTEXT 覆盖完整性 / resolve_context_window 不落 32k 兜底

⚠️ 每个用例都配了「对照实验」——先证明判据有区分力（把实现改回错误版本，用例确实会红），
   否则「测试通过」和「测试根本没在检查」无法区分（见 key-patterns #38/#39）。

⚠️ 必须直接调 _detect_capabilities，不能走 addModel 那条路——那里是
   `_lookup_preset_capabilities(...) or _detect_capabilities(...)`，预设层会替它通过，
   被测函数根本没执行。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.config import DEFAULT_CONFIG
from app.core.model_resolver import resolve_context_window
from app.core.token import DEFAULT_CONTEXT
from app.routers.model import _PROVIDER_IMAGE_RULES, _detect_capabilities

_32K_FALLBACK = 32000


def _old_detect_image(include: list, exclude: list, name: str) -> bool:
    """修复前写法的复刻：`if "*" in include` 与 `elif 前缀匹配` 互斥，
    "*" 分支直接 append，**跳过 exclude 检查**。仅用于对照，不参与业务。"""
    if "*" in include:
        return True
    if any(p in name for p in include):
        return not exclude or not any(e in name for e in exclude)
    return False


# ── deepseek 规则 ──

def test_deepseek_flash_is_image():
    """真阳性：V4.1 Flash 原生多模态"""
    assert "image" in _detect_capabilities("deepseek", "deepseek-flash")


def test_deepseek_pro_is_not_image():
    """真阴性：V4 Pro 是纯文本（'pro' 在 exclude 里）"""
    assert "image" not in _detect_capabilities("deepseek", "deepseek-v4-pro")


def test_deepseek_exclude_works_under_star():
    """⚠️ "*" 分支必须仍然走 exclude —— 这是本次修复的直接判据。

    deepseek 规则是 {"include": ["*"], "exclude": [...]}，修复前 "*" 分支
    直接 append 且跳过 exclude，排除表整个是死代码。
    """
    for name in ("deepseek-v4-pro-preview", "deepseek-reasoner", "deepseek-coder-v2", "deepseek-embed"):
        assert "image" not in _detect_capabilities("deepseek", name), \
            f"{name} 应被 exclude 排除，却标成了多模态"


def test_control_old_impl_would_fail():
    """对照实验：证明上面那条用例有区分力。

    用修复前的写法复刻，同样的输入必须判成 image（即：错误实现骗不过测试）。
    """
    rules = _PROVIDER_IMAGE_RULES["deepseek"]
    assert _old_detect_image(rules["include"], rules["exclude"], "deepseek-v4-pro-preview") is True, \
        "旧写法本应误判为 image——若这里为 False，说明对照失效，判别用例没有区分力"


def test_control_deepseek_rule_is_not_noop():
    """对照实验②：证明「deepseek 能标出多模态」靠的是新规则，不是运气。

    把规则临时改回旧值（{"include": ["vl","vision"]}，对 deepseek-flash 是彻底 no-op），
    deepseek-flash 必须**不再**被标为多模态。不改回则说明新规则其实没起作用。
    """
    original = _PROVIDER_IMAGE_RULES["deepseek"]
    _PROVIDER_IMAGE_RULES["deepseek"] = {"include": ["vl", "vision"]}
    try:
        assert "image" not in _detect_capabilities("deepseek", "deepseek-flash"), \
            "旧规则下 deepseek-flash 竟被标为多模态——说明用例没测到规则表"
    finally:
        _PROVIDER_IMAGE_RULES["deepseek"] = original


# ── 其他厂商不受影响（回归） ──

def test_google_star_rule_still_works():
    """google 是 {"include": ["*"]} 且无 exclude → 仍全标多模态"""
    assert "image" in _detect_capabilities("google", "gemini-3.5-flash")


def test_dashscope_exclude_still_works():
    """dashscope 的排除表不能因本次改动失效"""
    assert "image" in _detect_capabilities("dashscope", "qwen-vl-max")
    assert "image" not in _detect_capabilities("dashscope", "qwen3-coder-plus")


def test_unknown_provider_only_text():
    """未知 provider 不猜测"""
    assert _detect_capabilities("mystery-gateway", "whatever-vl") == ["text"]


def test_video_capability_precise_match():
    """视频能力是精确名匹配（不能用前缀，否则会误标 qwen-vl-ocr-latest）"""
    assert "video" in _detect_capabilities("dashscope", "qwen3.8-max")
    assert "video" not in _detect_capabilities("dashscope", "qwen-vl-ocr-latest")


# ── 上下文窗口兜底 ──

def test_default_context_covers_all_providers():
    """不变式：llm_models 预设涉及的每个 provider 都必须在 DEFAULT_CONTEXT 里有兜底值。

    这道闸门抓的是「新增了 provider 预设却忘了补兜底表」——此时该 provider 的
    未声明 context_window 的模型会静默落到保守 32k，对话预算被腰斩且无任何提示。
    """
    preset_providers = set(DEFAULT_CONFIG.get("llm_models", {}).keys())
    missing = preset_providers - set(DEFAULT_CONTEXT.keys())
    assert not missing, f"这些 provider 缺 DEFAULT_CONTEXT 兜底值：{sorted(missing)}"


def test_no_preset_model_falls_back_to_32k():
    """行为断言：所有预设模型（含未声明 context_window 的）都不该落到 32k 兜底。

    比只断言「表里有 key」更强——它同时覆盖「provider 有兜底值」与
    「model_name 能正确解析到该 provider」两件事。
    """
    fallbacks = []
    for prov, models in DEFAULT_CONFIG.get("llm_models", {}).items():
        for m in models:
            win = resolve_context_window({"enabled_models": []},
                                         provider_id=prov, model_name=m["id"])
            if win == _32K_FALLBACK:
                fallbacks.append(f"{prov}/{m['id']}")
    assert not fallbacks, f"这些模型落到了 32k 保守兜底（缺预设上下文窗口或缺 provider 兜底）：{fallbacks}"


def test_context_window_value_matches_preset():
    """预设声明了 context_window 时，解析结果必须就是它（第 2 层优先于第 3 层）"""
    win = resolve_context_window({"enabled_models": []},
                                 provider_id="deepseek", model_name="deepseek-flash")
    assert win == 1_000_000, f"deepseek-flash 应为 1M，实际 {win}"


def test_user_custom_beats_preset():
    """第 1 层（用户自定义）高于第 2 层（预设）"""
    cfg = {"enabled_models": [{"id": 12345, "context_window": 65536}]}
    win = resolve_context_window(cfg, model_id=12345)
    assert win == 65536, f"用户自定义应生效，实际 {win}"


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
