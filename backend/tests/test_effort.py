"""思考强度决策（effort.py）—— 真相表 + 静默期分档 + 「按产物名传」的行为验证。

为什么值得一个独立测试文件：
1. `effort_for` 是四个调用点共享的唯一真相，且规则本身**反直觉**（整理版恒低档），
   散落的副本迟早漂移成「UI 说的是 A，实际跑的是 B」；
2. **`generate_both` 的 a/b 顺序与流式路径相反**（a=polished / b=notes），而本仓库
   有「曾解包反了导致两产物互换写进对方文件」的历史 —— 档位若按位置传，就是把
   笔记的档位配到整理版上，**不报错、无异常、单测全绿**，只有用户觉得「怎么变慢了」；
3. 静默期分档若写死，选最高档反而更容易被判超时 —— 而这一条只在 max 档真实触发。

判据自证（模式 #38「断言存在 ≠ 断言有效」）：文件末尾的对照实验可复现 ——
- 把 `chunk_timeout_for` 改成恒返回 150 → 分档用例红；
- 把 `generate_both` 的 effort_a/effort_b 互换 → 行为用例红；
- 往任一模块塞回 `reasoning_effort="low"` 字面量 → AST 用例红（今天的 :653/:799 正是两处写死）。
"""
import ast
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.generation.effort import (      # noqa: E402
    CHUNK_TIMEOUT_DEFAULT, CHUNK_TIMEOUT_MAX, EFFORT_TIERS,
    chunk_timeout_for, effort_for,
)
from app.generation.generator import Generator      # noqa: E402

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

TRUE_ = True
FALSE = False

# ═══════════════════════════════════════════════════════════════
# 1. 真相表逐格
# ═══════════════════════════════════════════════════════════════

# (product, user_tier, supports_reasoning) -> 期望下发值（None = 不传参数）
TABLE: list[tuple[str, str, bool, str | None]] = [
    # ── 支持 reasoning 的模型 ──
    # ""= 老客户端 / 未选择 → notes 不传（服务端默认 high），**零回归**
    ("notes",    "",     TRUE_, None),
    ("notes",    "low",  TRUE_, "low"),
    ("notes",    "high", TRUE_, "high"),
    ("notes",    "max",  TRUE_, "max"),
    # polished 恒低档：忠实整理任务，深度思考换不来质量却要付 90s+ 静默期
    ("polished", "",     TRUE_, "low"),
    ("polished", "low",  TRUE_, "low"),
    ("polished", "high", TRUE_, "low"),
    ("polished", "max",  TRUE_, "max"),      # 唯一例外：用户明确要「都用最高档」
    # ── 不支持 reasoning 的模型：一律不传，绝不硬塞 ──
    ("notes",    "",     FALSE, None),
    ("notes",    "low",  FALSE, None),
    ("notes",    "high", FALSE, None),
    ("notes",    "max",  FALSE, None),
    ("polished", "high", FALSE, None),
    ("polished", "max",  FALSE, None),
    # ── 非法/未知档位：按「未表达偏好」处理，不崩也不下发 ──
    ("notes",    "medium", TRUE_, None),
    ("notes",    "LOW",    TRUE_, None),     # 大小写敏感：拼错即视为未选择
    ("polished", "medium", TRUE_, "low"),    # 整理版仍守自己的规则
]

for product, tier, supports, want in TABLE:
    got = effort_for(product, tier, supports)
    assert got == want, (
        f'真相表不符：product={product!r} tier={tier!r} '
        f'supports_reasoning={supports} → 期望 {want!r}，实际 {got!r}')

assert len(TABLE) == 17
print(f'PASS: effort_for 真相表 {len(TABLE)} 格逐格相符')

# 返回 None 的语义是「不传该参数」，不是「传 low」—— 这两者下发给服务端的结果不同
assert effort_for("notes", "", TRUE_) is None, '未选择档位必须是 None（不传），而非 "low"'
print('PASS: 未指定档位 → None（交由服务端默认），不是静默改写为 low')

# EFFORT_TIERS 是判定合法档位的唯一来源，漏一个就会让该档静默失效
assert EFFORT_TIERS == ("low", "high", "max"), f'档位表被改动：{EFFORT_TIERS}'
print('PASS: 合法档位集合 = low/high/max')

# ═══════════════════════════════════════════════════════════════
# 2. 静默期分档
# ═══════════════════════════════════════════════════════════════

# 只有 max 放宽；"high"/None 与 "low" 同档（它们不改变服务端的思考时长上限）
for eff, want in [(None, CHUNK_TIMEOUT_DEFAULT), ("low", CHUNK_TIMEOUT_DEFAULT),
                  ("high", CHUNK_TIMEOUT_DEFAULT), ("max", CHUNK_TIMEOUT_MAX)]:
    got = chunk_timeout_for(eff)
    assert got == want, f'chunk_timeout_for({eff!r}) = {got}，期望 {want}'
assert CHUNK_TIMEOUT_MAX > CHUNK_TIMEOUT_DEFAULT, 'max 档必须比默认档更宽容'
print(f'PASS: 静默期分档 default={CHUNK_TIMEOUT_DEFAULT}s / max={CHUNK_TIMEOUT_MAX}s')

# ⚠️ 这两个值必须严格小于生成总超时（330 非图表档），否则单 chunk 超时永远轮不到
# 生效 —— 总超时先到，分档成了死代码（且「写到 400 才够」的错觉会让后来者调错方向）
from app.routers.generate import _GEN_TIMEOUT_BASE, _GEN_TIMEOUT_CHART   # noqa: E402
assert CHUNK_TIMEOUT_MAX < _GEN_TIMEOUT_BASE, (
    f'静默期上限 {CHUNK_TIMEOUT_MAX}s 不小于生成总超时基础档 {_GEN_TIMEOUT_BASE}s —— '
    f'分档永远不会生效，是死代码')
assert _GEN_TIMEOUT_BASE < _GEN_TIMEOUT_CHART, '图表档总超时必须更宽（多张语义 JSON）'
print(f'PASS: 分档值严格小于生成总超时（{CHUNK_TIMEOUT_MAX} < {_GEN_TIMEOUT_BASE}）')

# ═══════════════════════════════════════════════════════════════
# 3. 行为验证：generate_both 的档位「按产物名传，不按 a/b 位置传」
# ═══════════════════════════════════════════════════════════════

class _FakeLLM:
    """记录每次 call 的 (prompt, effort)。supports_reasoning 用 property 模拟真身。"""

    def __init__(self, supports: bool = True):
        self._supports = supports
        self.calls: list[tuple[str, str | None]] = []

    @property
    def supports_reasoning(self) -> bool:
        return self._supports

    async def call(self, prompt, **kw):
        self.calls.append((prompt, kw.get("reasoning_effort")))
        return f'OUT<{prompt}>'


class _FakePrep:
    doc_dir = ""

    async def prepare(self, text, *a, **kw):
        # 4 元组：`document` 是文档直传的原件块（见 services/doc_understand.py）。
        # ⚠️ 替身必须与真实签名同步 —— 少一个元素时 generator.py 会直接
        # ValueError("not enough values to unpack")，这是刻意的：位置返回让
        # 漏改调用点在第一次执行时就炸，而不是静默拿到一个空的 document。
        return text, [], None, None


class _FakePrompts:
    def build_both(self, *a, **kw):
        return "PROMPT_A", "PROMPT_B"       # a → polished，b → notes

    def build(self, *a, **kw):
        return "PROMPT_ONE"


def _run_both(tier: str, supports: bool = True):
    llm = _FakeLLM(supports)
    gen = Generator(llm, _FakePrep(), _FakePrompts(), {})
    polished, notes = asyncio.run(gen.generate_both(
        raw_text="原文", title="标题", source_type="text", reasoning_effort=tier))
    return llm, polished, notes


llm, polished, notes = _run_both("high")
by_prompt = dict(llm.calls)
# ⚠️ 判据：prompt_a 产 polished、prompt_b 产 notes。若把 effort_a/effort_b 互换
# （即「按 a/b 位置传」的写法），下面第一行会变成 "high"、第二行变 "low"。
assert by_prompt["PROMPT_A"] == "low", (
    f'prompt_a 产出 polished（恒低档），实际下发 {by_prompt["PROMPT_A"]!r} —— '
    f'档位很可能按 a/b 位置而非产物名传了')
assert by_prompt["PROMPT_B"] == "high", (
    f'prompt_b 产出 notes（跟随用户档位），实际下发 {by_prompt["PROMPT_B"]!r}')
assert polished.content == "OUT<PROMPT_A>" and notes.content == "OUT<PROMPT_B>", \
    '产物与 prompt 的对应关系变了 —— 先核对 generate_both 的返回值顺序'
print('PASS: generate_both 档位按产物名传（a=polished 低档 / b=notes 跟随用户档）')

# max 档：唯一让整理版也升档的情形（UI 文案必须与此一致）
llm_max, _, _ = _run_both("max")
assert dict(llm_max.calls) == {"PROMPT_A": "max", "PROMPT_B": "max"}, llm_max.calls
print('PASS: max 档下两产物都用最高档')

llm2 = _FakeLLM(True)
asyncio.run(Generator(llm2, _FakePrep(), _FakePrompts(), {}).generate(
    raw_text="原文", mode="polished", reasoning_effort="high"))
assert llm2.calls == [("PROMPT_ONE", "low")], llm2.calls
llm3 = _FakeLLM(True)
asyncio.run(Generator(llm3, _FakePrep(), _FakePrompts(), {}).generate(
    raw_text="原文", mode="notes", reasoning_effort="high"))
assert llm3.calls == [("PROMPT_ONE", "high")], llm3.calls
print('PASS: generate() 单产物按 mode 取档（polished→low / notes→high）')

# 未指定档位（老客户端）：polished 仍低档、notes 不传 —— 与流式路径同源
llm4, _, _ = _run_both("")
assert dict(llm4.calls) == {"PROMPT_A": "low", "PROMPT_B": None}, llm4.calls
print('PASS: 未指定档位时 polished 仍 "low"、notes 不传（零回归）')

# 不支持的模型：一律不传参数（不是传了被忽略）
llm5, _, _ = _run_both("max", supports=False)
assert dict(llm5.calls) == {"PROMPT_A": None, "PROMPT_B": None}, llm5.calls
print('PASS: 模型不支持 reasoning 时不传参数（含用户显式选档）')

# ═══════════════════════════════════════════════════════════════
# 4. 结构断言：档位只能在 effort.py 里决策（防「改一处漏一处」）
# ═══════════════════════════════════════════════════════════════

# 4a. 生成路径里任何 `reasoning_effort=<字符串字面量>` 的关键字实参都是硬编码 ——
# 一旦有人图省事写回 `reasoning_effort="low"`，就绕开了真相表（:653/:799 正是如此）。
# ⚠️ 只扫生成路径：辅助调用（reference_compressor 的摘要压缩）**有意**自带 "low"，
# 它不跟随用户档位——把它也纳入会得到一个「正确实现也报警」的判据。
GEN_PATH = {
    "app/routers/generate.py",
    "app/generation/generator.py",
}
BAD_KWARG: list[str] = []
# 4b. `effort_for("...")` 的第一个实参若是字面量，必须在合法产物名内（防拼写错误
# 如 "polish" —— 它会静默落进 notes 分支，把整理版当成笔记来配档）
BAD_PRODUCT: list[str] = []
VALID_PRODUCTS = {"notes", "polished"}
CALL_SITES = 0

for path in sorted(APP_DIR.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = str(path.relative_to(APP_DIR.parent))
    in_gen_path = rel.replace("\\", "/") in GEN_PATH
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # 4a
        if in_gen_path:
            for kw in node.keywords or []:
                if kw.arg == "reasoning_effort" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    BAD_KWARG.append(
                        f'{rel}:{kw.value.lineno} reasoning_effort="{kw.value.value}"')
        # 4b
        fn = node.func
        fname = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else "")
        if fname == "effort_for":
            CALL_SITES += 1
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and first.value not in VALID_PRODUCTS:
                BAD_PRODUCT.append(f'{rel}:{first.lineno} effort_for("{first.value}", ...)')

assert not BAD_KWARG, (
    '发现硬编码的 reasoning_effort 字面量 —— 档位必须经 effort.py::effort_for 决策，'
    '否则真相表会与实现漂移：\n  ' + '\n  '.join(BAD_KWARG))
assert not BAD_PRODUCT, (
    'effort_for 的产物名不在 {notes, polished} 内（拼错会静默按 notes 处理）：\n  '
    + '\n  '.join(BAD_PRODUCT))
# 7 处：generate.py 流式双产物 2 + 单产物 1；generator.py generate 1 + xlsx 1 + both 2
assert CALL_SITES >= 7, (
    f'effort_for 调用点只有 {CALL_SITES} 处，少于预期的 7 处 —— 某个路径的档位可能被'
    f'整体删掉或绕开了（模式 #16：漏传一处即静默失效）')
print(f'PASS: 全库无硬编码档位字面量；effort_for 调用点 {CALL_SITES} 处（≥7）')

print('\nAll effort tests PASS')
