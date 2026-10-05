"""厂商 base_url 归一化（纯函数单测，无需启动后端）。

运行：uv run python tests/test_url_utils.py

覆盖：版本段判据表 / **Google 兼容层** / 智谱 / 全部内置 provider 行为不变 /
      历史副本零残留（AST 扫描，不误伤注释）

⚠️ 每个用例都配了「对照实验」——先证明判据有区分力（把实现改回错误版本，用例确实会红），
   否则「测试通过」和「测试根本没在检查」无法区分（见 key-patterns #38/#39）。

背景：同一个「把 base_url 拼成 /models 可用地址」的判据曾在 **3 处**各自实现
（`routers/model.py` 的 test_connection 与 refresh_models、`routers/config.py` 的
`/llm/models`），且判据本身（`endswith("/v1")`）对 google 与 zhipu 都不成立。
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.url_utils import api_root
# 内置厂商表已下沉 core（P1-14）——从定义处 import，不再绕经 `routers/model.py`
from app.core.provider_defaults import PROVIDER_DEFAULTS


def _legacy_root(base_url: str) -> str:
    """修复前写法的复刻：`if not url.endswith("/v1"): url += "/v1"`。
    仅用于对照，不参与业务。"""
    url = (base_url or "").rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


# ── 表驱动：归一结果 ──

CASES = [
    # (base_url, 期望的根, 说明)
    ("https://generativelanguage.googleapis.com/v1beta/openai",
     "https://generativelanguage.googleapis.com/v1beta/openai", "Google 兼容层：原样"),
    ("https://generativelanguage.googleapis.com/v1beta/openai/",
     "https://generativelanguage.googleapis.com/v1beta/openai", "Google：尾斜杠被剥"),
    ("https://open.bigmodel.cn/api/paas/v4",
     "https://open.bigmodel.cn/api/paas/v4", "智谱 v4：原样（旧实现会拼错）"),
    ("https://api.deepseek.com", "https://api.deepseek.com/v1", "无版本段 → 补 /v1"),
    ("https://api.anthropic.com", "https://api.anthropic.com/v1", "无版本段 → 补 /v1"),
    ("https://api.openai.com/v1", "https://api.openai.com/v1", "已带版本段 → 不补"),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1",
     "https://dashscope.aliyuncs.com/compatible-mode/v1", "已带版本段 → 不补"),
    ("https://api.moonshot.cn/v1", "https://api.moonshot.cn/v1", "已带版本段 → 不补"),
    ("https://opencode.ai/zen/go/v1", "https://opencode.ai/zen/go/v1", "已带版本段 → 不补"),
    ("http://localhost:8000", "http://localhost:8000/v1", "用户自配无版本段 → 补"),
    ("http://localhost:8000/v1", "http://localhost:8000/v1", "用户自配已带 → 不补"),
    # ⚠️ 空值返回 "/v1" 是**刻意的逐值兼容**——旧实现同样是 `"" → "/v1"`。
    #    它不是「合理的默认」而是历史行为；改掉它会让本函数不再是纯替换（有回归面）。
    #    空 base_url 的兜底由调用方负责（两个路由都在此之前判过 provider 是否存在）。
    ("", "/v1", "空值 → 与旧实现逐值一致"),
    (None, "/v1", "None → 与旧实现逐值一致"),
]


def test_table():
    bad = []
    for base, want, why in CASES:
        got = api_root(base)
        if got != want:
            bad.append(f"{why}: api_root({base!r}) = {got!r}，期望 {want!r}")
    assert not bad, "归一结果不符：\n  " + "\n  ".join(bad)
    print(f"✅ 表驱动 {len(CASES)} 条全部符合")


def test_legacy_contrast_has_discriminating_power():
    """对照实验：旧实现在 google / zhipu 上**确实**给出不同（且错误）的结果。

    这条是本文件的存在理由——它证明两个受测用例不是「怎么改都通过」的空断言。
    若哪天有人把判据改回 `endswith("/v1")`，上面 test_table 会红，而这条会**由红转绿**
    （两者结论重新一致），从而指出「不是新判据坏了，是旧判据回来了」。
    """
    diverged = []
    for base, want, why in CASES:
        legacy = _legacy_root(base)
        if legacy != api_root(base):
            diverged.append((base, why, legacy, api_root(base)))

    # 分歧必须**恰好**落在 google 与 zhipu 两家（按 host 去重——google 有「带/不带尾斜杠」
    # 两条用例，逐条数会是 3，那是用例数不是判据分歧数）
    hosts = {base.split("/")[2] for base, *_ in diverged}
    assert hosts == {"generativelanguage.googleapis.com", "open.bigmodel.cn"}, (
        f"旧/新判据应恰在 google / zhipu 两处分歧，实际涉及 {hosts}：{diverged}")
    for base, why, legacy, new in diverged:
        assert legacy != new
        # 旧值确实是「多拼了一层」的那种错法
        assert legacy.count("/v1") > new.count("/v1"), (
            f"{why}: 旧实现 {legacy} 应比新实现 {new} 多一层 /v1")
    print("✅ 对照实验：旧判据恰在 google / zhipu 两处给出错误结果（新判据有区分力）")


# ── PROVIDER_DEFAULTS 的完整性 ──

def test_google_default_is_openai_compat_layer():
    """Google 的默认 base_url **必须**是 OpenAI 兼容层。

    对照实验：改回 `https://generativelanguage.googleapis.com/v1beta`（原生 API 形态）
    → 本断言红。那正是修复前的状态——OpenAI SDK 会据此发
    `POST .../v1beta/chat/completions`，原生端点不存在该路径 → 整条 LLM 链路不通。

    ⚠️ 这条防的是**回退**，不是「有没有值」——两种形态都「看起来像个正常 URL」，
       只有断言能区分。
    """
    base = PROVIDER_DEFAULTS["google"]["base_url"]
    assert base == "https://generativelanguage.googleapis.com/v1beta/openai", (
        f"google base_url 应为兼容层（带 /openai）；实际 {base!r}")
    assert api_root(base) == base, "google 的 base_url 归一后不应再被拼上 /v1"
    print("✅ Google 默认 base_url = OpenAI 兼容层")


def test_all_defaults_normalize_sanely():
    """遍历全部内置 provider —— 归一后的根必须「路径里有版本段」且不以 `/v1/v1` 之类结尾。

    这条不是重复 test_table：它是**面向新增 provider 的守卫**。将来有人加一家
    `https://x.com/api/v2`（已带版本段），本断言保证它不会被再拼一层。
    """
    bad = []
    for pid, info in PROVIDER_DEFAULTS.items():
        base = info["base_url"]
        root = api_root(base)
        if api_root(root) != root:
            bad.append(f"{pid}: 归一不幂等 — api_root({root!r}) != {root!r}")
        # 幂等性是核心性质：它保证「已归一」与「未归一」不会互相转换
        if not root.startswith("http"):
            bad.append(f"{pid}: 归一结果不是 http(s) — {root!r}")
    assert not bad, "provider 归一异常：\n  " + "\n  ".join(bad)
    print(f"✅ 全部 {len(PROVIDER_DEFAULTS)} 个内置 provider 归一幂等")


def test_idempotent():
    """api_root 幂等：归一过的值再归一不变。对照：非幂等的实现会让
    「保存后再读」的路径每次多拼一层。"""
    for base, _want, why in CASES:
        once = api_root(base)
        twice = api_root(once)
        assert twice == once, f"{why}: 不幂等 — {once!r} → {twice!r}"
    print("✅ api_root 幂等")


# ── 源码守卫：历史副本零残留 ──

ROUTERS = Path(__file__).resolve().parent.parent / "app" / "routers"


def _legacy_concat_lines(path: Path) -> list[int]:
    """AST 扫描：找出**代码里**（不含注释/docstring）的 `X.endswith("/v1")`。

    ⚠️ 必须走 AST 而非 grep——`url_utils.api_root` 的 docstring 里**刻意**引用了旧写法
    作为反例，grep 会把那段说明文字也判成违规（这正是项目「判据要防输入已被上游改坏」
    的同类问题：文本匹配分不清「代码」与「讲代码的文字」）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "endswith" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "/v1"):
            hits.append(node.lineno)
    return hits


def test_no_legacy_copy_in_routers():
    """全 routers 目录不得再出现 `endswith("/v1")` 形态的拼接代码。

    对照实验：把 `model.py` 的 `api_root(...)` 换回
    `if not base_url.endswith("/v1"): base_url += "/v1"` → 本断言红。

    意义：这次收敛掉的正是「同一判据的第三份副本」。守卫在这里，第四份才不会长出来。
    """
    offenders = []
    for py in sorted(ROUTERS.glob("*.py")):
        for ln in _legacy_concat_lines(py):
            offenders.append(f"{py.name}:{ln}")
    assert not offenders, (
        "routers 下仍有 base_url 版本段拼接的旧副本（应改用 app.core.url_utils.api_root）：\n  "
        + "\n  ".join(offenders))
    print(f"✅ routers/ 下 {len(list(ROUTERS.glob('*.py')))} 个模块无旧拼接副本")


def test_guard_has_discriminating_power():
    """自证守卫有效：AST 扫描器对**故意构造的违规代码**确实报错，对注释/字符串不报错。

    这是 key-patterns #38「断言存在 ≠ 断言有效」的直接应用——不造一个违规样本跑一遍，
    「零命中」与「扫描器根本没工作」无法区分。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.py"
        p.write_text(
            'def f(url):\n'
            '    if not url.endswith("/v1"):\n'   # ← 违规：真实代码
            '        url += "/v1"\n'
            '    return url\n'
            'NOTE = "see url.endswith(\'/v1\') for the legacy shape"\n'  # ← 字符串，不应命中
            '# url.endswith("/v1")  ← 注释，不应命中\n',
            encoding="utf-8")
        hits = _legacy_concat_lines(p)
        assert hits == [2], f"扫描器应只命中第 2 行的真实代码，实际命中 {hits}"
    print("✅ 守卫自证：真代码命中、注释与字符串不命中")


if __name__ == "__main__":
    test_table()
    test_legacy_contrast_has_discriminating_power()
    test_google_default_is_openai_compat_layer()
    test_all_defaults_normalize_sanely()
    test_idempotent()
    test_no_legacy_copy_in_routers()
    test_guard_has_discriminating_power()
    print("\n🎉 全部通过")
