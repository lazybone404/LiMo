"""模型能力自动检测 + 上下文窗口兜底（纯函数单测，无需启动后端）。

运行：uv run python tests/test_model_caps.py
覆盖：include/exclude 判据（含 "*" 分支必须仍走 exclude）/ deepseek 规则 / 视频精确匹配 /
      DEFAULT_CONTEXT 覆盖完整性 / resolve_context_window 不落兜底 / **scene 回退（第 0 层）**

⚠️ 每个用例都配了「对照实验」——先证明判据有区分力（把实现改回错误版本，用例确实会红），
   否则「测试通过」和「测试根本没在检查」无法区分（见 key-patterns #38/#39）。

⚠️ 必须直接调 _detect_capabilities，不能走 addModel 那条路——那里是
   `_lookup_preset_capabilities(...) or _detect_capabilities(...)`，预设层会替它通过，
   被测函数根本没执行。
"""
import ast
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.config import DEFAULT_CONFIG
from app.core.model_caps import has_capability
from app.core.model_resolver import resolve_context_window
from app.core.token import DEFAULT_CONTEXT
from app.generation.content_preparer import ContentPreparer
from app.llm.service import LLMService
from app.routers.model import _PROVIDER_IMAGE_RULES, _detect_capabilities


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
    未声明 context_window 的模型会静默落到第 4 层兜底，预算与实际窗口不符且无提示。
    """
    preset_providers = set(DEFAULT_CONFIG.get("llm_models", {}).keys())
    missing = preset_providers - set(DEFAULT_CONTEXT.keys())
    assert not missing, f"这些 provider 缺 DEFAULT_CONTEXT 兜底值：{sorted(missing)}"


def test_no_preset_model_silently_falls_back():
    """行为断言：每个预设模型都能解析到**它应有的**窗口。

    ⚠️ 判据必须按「模型 × 来源」**推导期望值**，不能比对第 4 层兜底常量的数值。
    本测试的初版正是后者（`win == 32000`），它在常量改为 128k 后**必然失效**：
    `moonshot` / `opencode` / `zhipu` 的 provider 兜底值恰好是 `131072`，与
    `128 * 1024` **数值完全相同** → 「返回值 == 常量」无法区分「走第 3 层」与
    「四层全落空」，且清单里会混进一批**根本没走兜底**的模型（模式 #50 的形态：
    值相等 ≠ 路径相同——用值反推路径，一旦两个来源同值，判据就失去区分力）。

    真正要守的两条性质：
      ① 每个 provider 都有兜底来源——否则该 provider 的未声明模型会用到
         「未知模型」的猜测值（这正是 #51「能力存在 ≠ 该路径在用它」的同族；
         表里**有 key** 与**这个模型真的走到它**是两件事，故本条仍需逐模型跑）
      ② 第 2 层查表能正确命中预设——预设值 ≠ provider 兜底值的模型（如
         `qwen-vl-ocr-latest` 38192 / `glm-4.7` 200000 / `kimi-k2.6` 262144）
         一旦静默落到兜底就会被本条抓住
    """
    problems = []
    for prov, models in DEFAULT_CONFIG.get("llm_models", {}).items():
        for m in models:
            mid = m["id"]
            win = resolve_context_window({"enabled_models": []},
                                         provider_id=prov, model_name=mid)
            expected = m.get("context_window") or DEFAULT_CONTEXT.get(prov)
            if expected is None:
                problems.append(f"{prov}/{mid}: 无任何兜底来源，落到第 4 层 {win}")
            elif win != expected:
                problems.append(f"{prov}/{mid}: 解析 {win} ≠ 期望 {expected}")
    assert not problems, "预设模型窗口解析异常：\n  " + "\n  ".join(problems)


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


# ── scene 回退（第 0 层）──
#
# ⚠️ 本轮修复的直接判据。调用方普遍传 `req.model_id`，而用户用「场景默认模型」时
#    它是 **None** —— `resolve_llm_config` 会回退到 `scene_models[scene]`（LLMService
#    因此用真模型），而本函数此前**没有这条回退** → 四层全落空 → 落到第 4 层兜底。
#    后果：同一请求里模型与预算各说各话（1M 窗口的模型按 128k 算），且**零报错**。

def test_scene_fallback_resolves_real_model():
    """入参 model_id 为 None（调用方常态）→ 应回退到 scene_models 绑定模型的窗口。

    对照实验：删掉 `resolve_context_window` 里第 0 层的回退 → 四层全落空 →
    返回 DEFAULT_CONTEXT_WINDOW(131072) → 本断言红。
    """
    cfg = {
        "scene_models": {"generate": 3628306387},
        "enabled_models": [
            {"id": 3628306387, "provider_id": "deepseek", "model_name": "deepseek-flash"},
        ],
    }
    win = resolve_context_window(cfg, None)   # ← 调用方的真实形态：model_id=None
    assert win == 1_000_000, \
        f"scene 回退应解析到 deepseek-flash 的 1M 窗口，实际 {win}（落回第 4 层兜底了？）"


def test_scene_fallback_respects_scene_arg():
    """scene 参数要真的被用上——不同场景绑不同模型时各取各的。

    刻意只用第 1 层（用户自定义值）做断言，不依赖任何厂商预设数值——
    否则厂商改一次预设，这条测试就跟着红，红得与它要守的性质无关。
    """
    cfg = {
        "scene_models": {"generate": 1, "chat": 2},
        "enabled_models": [
            {"id": 1, "context_window": 111111},
            {"id": 2, "context_window": 222222},
        ],
    }
    assert resolve_context_window(cfg, None, scene="generate") == 111111
    assert resolve_context_window(cfg, None, scene="chat") == 222222


def test_scene_fallback_not_used_when_provider_given():
    """对照：显式给了 provider/model 的调用方不得被 scene 回退覆盖。

    真实调用方 `reference_compressor` 按**实际在用的模型**查询（可能不是场景模型）。
    判据写「两个来源都为空」而非「model_id 为空」，正是为了保住它。

    对照实验：把判据改成 `if not model_id:` → model_id 回退成 1 → 第 1 层命中
    `context_window=12345` → 返回 12345 → 本断言红。
    """
    cfg = {
        "scene_models": {"generate": 1},
        "enabled_models": [
            {"id": 1, "provider_id": "deepseek",
             "model_name": "deepseek-flash", "context_window": 12345},
        ],
    }
    win = resolve_context_window(cfg, None, provider_id="anthropic", model_name="claude-opus-5")
    assert win != 12345, \
        f"显式 provider/model 被 scene 回退覆盖了（拿到 scene 模型的 12345），实际 {win}"


# ── 第 1 层的**第二条支路**：按 provider_id + model_name 命中（P1-09）──
#
# ⚠️ 调用方分两类，此前只有传 `model_id` 的那类被第 1 层覆盖：
#   · `generate` / `chat` / `synthesis` 传 `model_id`；
#   · `reference_compressor._resolve_budget`（:71-76）传 **provider_id + model_name**
#     —— 它按**实际在用的模型**查询，可能根本不是场景模型。
# 后者整条第 1 层被跳过 → 用户在设置页给这个模型填的 `context_window`
# 对它**完全不生效**，静默落到第 2/3/4 层（同一份配置下能差数倍，零报错）。

def test_user_custom_applies_to_provider_model_callers():
    """对照实验：删掉新支路（只剩 `model_id` 那条）→ 落回预设/兜底 → 本断言红。"""
    cfg = {"enabled_models": [
        {"id": 1, "provider_id": "dashscope", "model_name": "qwen-max",
         "context_window": 999888},
    ]}
    win = resolve_context_window(cfg, None, provider_id="dashscope", model_name="qwen-max")
    assert win == 999888, \
        f"按 provider/model 查询的调用方拿不到用户自定义窗口，实际 {win}（第 1 层被跳过了）"


def test_provider_model_match_requires_both_fields():
    """对照：只按 `model_name` 命中会**张冠李戴**——判据必须是 `and`。

    `qwen-max` 这类名字在各家兼容网关都可能有；只比名字的话，甲家设置的窗口会被
    安到乙家同名模型头上，而两家的真实窗口可能差数倍（且零报错）。
    对照实验：把 `and` 放宽成「名字相同即命中」→ 本断言红（拿到 999888）。
    """
    cfg = {"enabled_models": [
        {"id": 1, "provider_id": "dashscope", "model_name": "qwen-max",
         "context_window": 999888},
    ]}
    win = resolve_context_window(cfg, None, provider_id="opencode", model_name="qwen-max")
    assert win != 999888, "同名不同厂的模型串了窗口（判据漏了 provider_id）"


def test_model_id_branch_still_works():
    """零回归对照：按 `model_id` 的既有形态不受新支路影响。"""
    cfg = {"enabled_models": [
        {"id": 7, "provider_id": "x", "model_name": "y", "context_window": 54321},
    ]}
    assert resolve_context_window(cfg, 7) == 54321


def test_unknown_model_uses_128k_not_32k():
    """第 4 层兜底值断言——用户明确要求「不默认搞成 32k 那小的」。

    四层全部落空（连 provider 都未知，即用户自建中转模型）→ 128k。
    对照实验：常量写回 `32 * 1024` → 红。这条把该决策钉成不可回归的性质。
    """
    win = resolve_context_window({"enabled_models": []},
                                 provider_id="mystery-gateway", model_name="whatever")
    assert win == 128 * 1024, f"未知模型兜底应为 128k，实际 {win}"


# ── 能力位判据的单一真相源（P1-15 收敛）──
#
# ⚠️ 收敛前有 **6 处** 同构内联（`llm/service.py` ×3 + `generation/content_preparer.py` ×3），
#    其中 `supports_document` 与 `content_preparer._run_doc_direct` 的能力门是
#    **同一件事的两份判据**，而 `supports_document` 全仓零调用点 —— 两份里有一份是死的，
#    改哪一份都对不上另一份，且零报错（模式 #54「同源是搜索题不是记忆题」）。
#
#    本节三层断言：① 判据定义只有一处；② 全仓零内联形态（AST 守卫）；
#    ③ 两处消费方**同判**（行为断言）。②③ 缺一不可——只守 ② 会漏掉「调了但判错了」，
#    只守 ③ 会漏掉「第三份内联悄悄长出来」。

_BACKEND = Path(__file__).resolve().parent.parent
_APP = _BACKEND / "app"
_CAP_BITS = ("image", "document", "video", "reasoning")


def _rel(p: Path) -> str:
    return p.relative_to(_BACKEND).as_posix()


def _inline_capability_checks() -> list[str]:
    """全仓 `app/` 找**内联的能力位判据**：`<字面量能力位> in <表达式>`。

    ⚠️ 必须走 AST 而非 grep：`core/model_caps.py` 与本测试的文档里**刻意**引用了
    旧写法当反例，grep 会把「讲代码的文字」判成违规（模式 #54 的配套细节）。
    """
    hits: list[str] = []
    for py in sorted(_APP.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            if not isinstance(node.ops[0], ast.In):
                continue
            left = node.left
            if isinstance(left, ast.Constant) and left.value in _CAP_BITS:
                hits.append(f"{_rel(py)}:{node.lineno}")
    return hits


def _call_sites(rel: str) -> int:
    """文件里 `has_capability(...)` 的调用次数。"""
    tree = ast.parse((_BACKEND / rel).read_text(encoding="utf-8"))
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "has_capability")


def test_capability_judgement_defined_exactly_once():
    """判据实现**只有一处**（`core/model_caps.py`）。

    第二份定义（哪怕是复制粘贴的同名函数）会让守卫 `_call_sites` 的计数失去意义——
    它数的是**名字**，不是那份实现。
    """
    defs: list[str] = []
    for py in sorted(_APP.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "has_capability":
                defs.append(f"{_rel(py)}:{node.lineno}")
    assert len(defs) == 1 and defs[0].startswith("app/core/model_caps.py"), \
        f"能力位判据应只定义在 app/core/model_caps.py 一处，实际：{defs}"


def test_no_inline_capability_judgement():
    """全仓零内联能力位判据 —— 新增消费方必须调 `has_capability`。"""
    hits = _inline_capability_checks()
    assert not hits, (
        "发现内联的能力位判据（应改调 app/core/model_caps.has_capability）：\n  "
        + "\n  ".join(hits))


def test_control_inline_detector_has_discrimination():
    """对照实验：守卫**真能**抓到内联形态，且**不**误伤注释里的旧写法（模式 #38/#54）。

    前半证明判据有区分力（否则「零命中」与「扫描器根本没工作」无法区分）；
    后半证明它走的是 AST 而非 grep —— 讲代码的文字被判成违规是本项目栽过的形态
    （`url_utils.api_root` 的 docstring 里刻意引用了旧写法当反例）。
    """
    inline = ast.parse('x = any("image" in (c or "") for c in caps)')
    found = [n for n in ast.walk(inline)
             if isinstance(n, ast.Compare) and isinstance(n.ops[0], ast.In)
             and isinstance(n.left, ast.Constant) and n.left.value in _CAP_BITS]
    assert len(found) == 1, "守卫判据写错了：连手工造的内联样本都抓不到"

    commented = ast.parse('# any("image" in (c or "") for c in caps)\nx = 1')
    assert not [n for n in ast.walk(commented) if isinstance(n, ast.Compare)], \
        "注释被当成了代码——守卫必须走 AST 而非 grep"


def test_both_consumers_call_the_single_source():
    """两个消费方各自**接上**了单一真相源（模式 #52：漏一半 = 与完全没做一样）。"""
    assert _call_sites("app/llm/service.py") == 3, "三个 supports_* 属性都应走 has_capability"
    assert _call_sites("app/generation/content_preparer.py") == 3, \
        "三处（video/image/document）都应走 has_capability"


def test_has_capability_semantics():
    """判据语义**逐值等价**于收敛前的内联写法（不是重新设计）。"""
    assert has_capability(["text", "image"], "image") is True
    assert has_capability(["text"], "image") is False
    assert has_capability([], "image") is False
    # 子串包含而非相等：capabilities 是自由字符串，历史/未来都有 image_input 这类变体
    assert has_capability(["image_input"], "image") is True
    # 手编 YAML 会写出空项（`- `）；旧写法靠 `(c or "")` 兜住，收敛后必须仍不抛
    assert has_capability(["text", None, ""], "image") is False


def _svc(caps: list) -> LLMService:
    """真 LLMService（client 惰性构造，不发任何请求）。"""
    return LLMService({"llm": {"provider": "deepseek", "capabilities": caps}})


def test_supports_document_reads_the_capability_bit():
    """`supports_document` 按模型标注判，且**刻意不复用 image 位**。"""
    assert _svc(["text", "document"]).supports_document is True
    assert _svc(["text"]).supports_document is False
    # image 认 PDF ≠ OpenAI 兼容端点认 `file` 块（doc_understand.py 的逐家结论正是为此）
    assert _svc(["text", "image"]).supports_document is False


def test_two_consumers_agree_on_the_same_capabilities():
    """★ 两侧**同判**：同一份 capabilities 下，`supports_document` 与
    `ContentPreparer._run_doc_direct` 的能力门结论必须一致。

    这是 P1-15 收敛的直接判据。收敛前任一处改判（或另一处忘了跟），本条即红——
    而**只看代码看不出来**（两处表达式逐字同构，不放到一起跑就发现不了分叉）。
    """
    fake = {"filename": "x.pdf", "data_uri": "data:application/pdf;base64,AA=="}
    with patch("app.generation.content_preparer.build_pdf_document", return_value=fake):
        for caps, expected in ((["text", "document"], True),
                               (["text", "image"], False),
                               (["text"], False)):
            prep = ContentPreparer(llm_config={"capabilities": caps})
            got = prep._run_doc_direct("C:/nope/x.pdf") is not None
            assert got is expected, \
                f"caps={caps}: _run_doc_direct 能力门判成 {got}，期望 {expected}"
            assert _svc(caps).supports_document is expected, \
                f"caps={caps}: 两侧判据分叉（supports_document 与 content_preparer 是否同源？）"


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
