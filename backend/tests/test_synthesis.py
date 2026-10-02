"""验证多篇文档合成 — Step 0 分叉 / 预算收敛 / 资产搬运 / 降级显式化

⚠️ **不发起任何真实计费调用**：LLM 用 `FakeLLM` 替身（记录调用次数与 prompt）。

⚠️ 本模块最该记住的一条：**「复制了但忘了改写引用」是本功能最容易白做的地方**——
文件躺在 `assets/d1_x.png`、正文却仍写 `assets/x.png`，结果与完全不搬运一模一样
（还是裂图），而日志里一切正常。故「正文引用与新文件名逐条对应」必须单独断言。
"""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.types import RawMetadata, RawTranscript, SourceType
from app.generation import synthesis as S
from app.generation.budget import input_budget_for, multi_capacity_ok
from app.storage.store import Store

_TMP: list[str] = []


def _new_store() -> Store:
    _d = tempfile.mkdtemp()
    _TMP.append(_d)
    return Store(_d)


def _cfg(cw: int = 1_000_000, model_id: int = 1) -> tuple[dict, int]:
    """返回 (config, model_id)。第 1 层（enabled_models.context_window）直接命中。"""
    return {"enabled_models": [{"id": model_id, "context_window": cw}]}, model_id


class FakeLLM:
    """替身：记录每次 prompt，返回固定回复。`fail=True` 时模拟抽取失败。"""

    def __init__(self, reply: str = "研究问题：测试\n方法：无", fail: bool = False,
                 dims: str = "无"):
        self.calls: list[str] = []
        self.reply = reply
        self.fail = fail
        self.dims = dims

    async def call(self, prompt: str, **kw) -> str:
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("fake llm failure")
        # 维度发现那次调用的 prompt 里含「默认维度」字样
        return self.dims if "默认维度" in prompt else self.reply


def _add_doc(store: Store, tx_id: str, title: str, raw: str) -> None:
    """写入一篇源文档。

    ⚠️ 正文末尾追加 `tx_id` 不是装饰——`save_transcript` 有**内容级 SHA256 去重**，
    而本文件多处刻意让两篇的正文一模一样（如两篇都带 `![图](assets/img_0001.png)`）。
    不加这个后缀，第二篇会被静默去重拒绝 → 合成时报「源文档已不存在」，
    而症状看起来像合成逻辑有 bug。
    """
    store.save_transcript(RawTranscript(
        id=tx_id, title=title, source_type=SourceType.PDF, engine="pypdfium2",
        raw_text=f"{raw}\n\n[源标识 {tx_id}]", content_hash="",
        metadata=RawMetadata(), tags=[]))


def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════
# 1. plan_synthesis —— Step 0 分叉（纯函数）
# ═══════════════════════════════════════════════════════════════

def _src(doc_id: str, chars: int) -> S.Source:
    """构造一篇约 chars 个字符的源。"""
    return S.Source(doc_id=doc_id, title=doc_id, raw_text="字" * chars)


# ── 窗口闸门必须在最前 ──
# ⚠️ 这道闸门防的**不是负数**（100k 窗口下 `cw−65536−2000` 仍是正的 32,464），
# 而是「额度算得出、但**装不下一篇论文**」的窗口：32,464 < MIN_INPUT(35,000)，
# 放行只会得到一份「每篇都被截断」的综述。判据是"够不够做这件事"，不是"数是否为负"。
assert not multi_capacity_ok(100_000)
assert multi_capacity_ok(102_536) and not multi_capacity_ok(102_535), \
    "门槛边界必须是 ≥（不是 >）"
_p = S.plan_synthesis([_src("a", 100), _src("b", 100)], 100_000)
assert _p.mode == "reject", _p
print(f"PASS: 窗口不足 → reject（{_p.reason}）")

# ⚠️ 这道闸门有**第二道防线**：即使 `plan_synthesis` 里的判断被删掉，
# `input_budget_for(…, "multi")` 自己也会抛 ValueError（见 budget.py），
# 不会静默取到小额度假装正常。但**不能因此省掉本处判断**——这里给的是
# **可展示的业务语义**（`reason` 文案 → SSE 提示用户换模型），
# ValueError 只会变成一个没有上下文的 500。两者层次不同，都要有。

CW = 131_072
BUDGET = input_budget_for(CW, "multi")
assert BUDGET == CW - 65536 - 2000, f"128k 下受输出预留限：{BUDGET}"

# ── 装得下 → stuff ──
_p = S.plan_synthesis([_src("a", 100), _src("b", 100)], CW)
assert _p.mode == "stuff" and _p.budget == BUDGET, _p
print("PASS: 总量远小于预算 → stuff")

# ── 恰好等于预算 → stuff；超出 1 token → map_reduce ──
# ⚠️ 用 input_budget_for 取值，**不写死 0.5×cw**：128k 下取小公式给出的是
# `cw - 65536 - 2000`（63,536），与 `int(cw*0.5)`（65,536）差 2,000。
# 写死 0.5×cw 的测试会在这 2,000 的区间里给出错误结论。
_est = S.estimate_tokens("字" * 10_000)          # 每个「字」约 1 token，先量出密度
_per_char = _est / 10_000
_exact = int(BUDGET / _per_char)                 # 恰好装满的字符数
_p_at = S.plan_synthesis([_src("a", _exact)], CW)
_p_over = S.plan_synthesis([_src("a", _exact + 200)], CW)
assert _p_at.total <= BUDGET and _p_at.mode == "stuff", (_p_at.total, BUDGET)
assert _p_over.total > BUDGET and _p_over.mode == "map_reduce", (_p_over.total, BUDGET)
print(f"PASS: 边界判定用真实预算（total={_p_over.total} vs budget={BUDGET}）")

# ── 配额总和必须收敛到预算内（★ 否则「装满的正文」生成时会被二次截断）──
# 对照实验：去掉 `if qsum > budget` 那段回缩 → 保底把总和顶超 → 下面必红
_big = [_src(f"d{i}", 200_000) for i in range(8)]
_p = S.plan_synthesis(_big, CW)
assert _p.mode == "map_reduce"
assert sum(_p.quotas) <= _p.budget, f"配额总和 {sum(_p.quotas)} 超预算 {_p.budget}"
# 保底场景：多篇极小 + 一篇极大 —— 保底会让总和顶超，回缩必须触发
_mixed = [_src("tiny", 100) for _ in range(20)] + [_src("huge", 900_000)]
_p2 = S.plan_synthesis(_mixed, CW)
assert _p2.mode == "map_reduce"
assert sum(_p2.quotas) <= _p2.budget, \
    f"保底把总和顶到 {sum(_p2.quotas)} 超预算 {_p2.budget}（缺回缩的实现会红）"
print(f"PASS: 配额收敛（8 篇 → {sum(_p.quotas)} ≤ {_p.budget}；保底场景 {sum(_p2.quotas)} ≤ {_p2.budget}）")

# ═══════════════════════════════════════════════════════════════
# 2. build_stuff_text —— 边界标记
# ═══════════════════════════════════════════════════════════════

_a, _b = _src("a", 20), _src("b", 20)
_a.title, _b.title, _a.text, _b.text = "论文A", "论文B", "正文甲", "正文乙"
_t = S.build_stuff_text([_a, _b])
assert "【文档 1】论文A" in _t and "【文档 2】论文B" in _t, _t
assert _t.index("【文档 1】") < _t.index("【文档 2】"), "顺序必须与选中序一致"
print("PASS: build_stuff_text 带【文档 i】边界标记且按序")

# ═══════════════════════════════════════════════════════════════
# 3. synthesize —— Stuff 路径（零 LLM 调用）
# ═══════════════════════════════════════════════════════════════

st = _new_store()
_add_doc(st, "tr_p1", "论文甲", "这是甲的正文，讲的是方法A。")
_add_doc(st, "tr_p2", "论文乙", "这是乙的正文，讲的是方法B。")
cfg, mid = _cfg(1_000_000)
llm = FakeLLM()
res = _run(S.synthesize(st, cfg, llm, mid, ["tr_p1", "tr_p2"], title="甲乙对比"))

assert res.mode == "stuff", res.mode
# ★ 这条同时防两种浪费：① 无脑跑「维度发现」（==1→红）
#   ② 任何「先探路」的调用（>0→红）
assert res.llm_call_count == 0, f"装得下时不该有任何 LLM 调用，实际 {res.llm_call_count}"
assert len(llm.calls) == 0, "FakeLLM 也不该被调到"
assert "【文档 1】" in res.raw_text and "【文档 2】" in res.raw_text
assert "<!-- limo-meta" in res.raw_text, "出处头必须在"
assert "## 来源文档" in res.raw_text, "来源列表必须在（降级透明度落进产物）"
assert "✓ 完整输入" in res.raw_text, "未降级的篇目标注完整输入"
print(f"PASS: stuff 路径零 LLM 调用 + 正文含出处头/来源列表（{res.transcript_id}）")

# 落库可见：新文档能读回，且来源身份正确
_got = st.get_transcript(res.transcript_id)
assert _got is not None, "必须真的落库"
assert _got["sourceType"] == "composite"
assert _got["sourceDocIds"] == ["tr_p1", "tr_p2"], _got["sourceDocIds"]
assert _got["metadataCorrupt"] is False
_row = [e for e in st.list_transcripts() if e["id"] == res.transcript_id][0]
assert _row["sourceDocIds"] == ["tr_p1", "tr_p2"], "列表接口（前端徽章）必须拿得到"
print("PASS: 落库 → 读回 + list_transcripts 直出（前端「N 合 1」徽章数据源）")

# ═══════════════════════════════════════════════════════════════
# 4. synthesize —— reject 走 AppError（不是 500）
# ═══════════════════════════════════════════════════════════════

_cfg_small, _mid_small = _cfg(100_000, model_id=2)
try:
    _run(S.synthesize(st, _cfg_small, FakeLLM(), _mid_small, ["tr_p1", "tr_p2"]))
    assert False, "窗口不足必须被拒绝（漏闸门的实现会走到装配并产出超窗请求）"
except Exception as e:
    assert type(e).__name__ == "SynthesisError", f"应是业务错误而非崩溃：{type(e).__name__}"
    assert e.code == "SYNTH_WINDOW_TOO_SMALL", e.code
    assert e.recoverable is True, "窗口不足是预期业务状态，不是不可恢复错误"
    assert not e._explicit_status, "不该显式指定 status_code（模式 #36）"
print("PASS: 窗口不足 → SynthesisError（recoverable，不显式 status）")

# 少于 2 篇
try:
    _run(S.synthesize(st, cfg, FakeLLM(), mid, ["tr_p1"]))
    assert False
except Exception as e:
    assert getattr(e, "code", "") == "SYNTH_TOO_FEW_SOURCES", e
# 源被删
try:
    _run(S.synthesize(st, cfg, FakeLLM(), mid, ["tr_p1", "tr_gone"]))
    assert False
except Exception as e:
    assert getattr(e, "code", "") == "SYNTH_SOURCE_MISSING", e
print("PASS: 篇数不足 / 源文档已删 → 各自明确错误码")

# ═══════════════════════════════════════════════════════════════
# 5. synthesize —— Map-Reduce 路径（含降级显式化 / 取消）
# ═══════════════════════════════════════════════════════════════

# ⚠️ 这一段必须用**会超预算**的窗口（128k → budget 63,536），而不是上面的 1M。
# 用 1M 的话两篇 6 万 token 装得下 → 走 stuff → **一次 LLM 调用都不会发生**，
# 于是「降级标注」「取消」这些只在 map_reduce 路径才存在的断言全部测不到东西。
_cfg_mr, _mid_mr = _cfg(131_072, model_id=3)

_mr = _new_store()
# ⚠️ 长度由**本环境的估算密度**反推（同 test_budget 的说明）：未装 tiktoken 时
# `estimate_tokens` = `len // 2`，写死 60,000 只会得到 30,000 token/篇 →
# 两篇合计 60,000 < budget 63,536 → 误判成 stuff，整段 map_reduce 断言全部失去意义。
_per_char = max(S.estimate_tokens("长" * 10_000) / 10_000, 1e-6)
_BIG = "长" * int(BUDGET * 1.5 / _per_char)   # 单篇 1.5× 预算 → 整体必超，且每篇都要走摘要
_add_doc(_mr, "tr_b1", "长文一", _BIG)
_add_doc(_mr, "tr_b2", "长文二", _BIG)
mr_llm = FakeLLM()
_mres = _run(S.synthesize(_mr, _cfg_mr, mr_llm, _mid_mr, ["tr_b1", "tr_b2"], title="长文对比"))
assert _mres.mode == "map_reduce", _mres.mode
# 1（发现维度）+ N（逐篇抽取）—— Reduce 由后续 notes 生成承担，不计在内
assert _mres.llm_call_count == 3, f"应为 1+2 次，实际 {_mres.llm_call_count}"
assert _mres.sources[0].status == "summarized", _mres.sources[0].status
assert "⚠ 已压缩为摘要" in _mres.raw_text, "降级必须落进产物（SSE 提示是一次性的）"
print(f"PASS: map_reduce 路径 1+N 次调用 + 降级标注落进正文")

# ── 抽取失败 → 显式标记，**不静默跳过** ──
_mr2 = _new_store()
_add_doc(_mr2, "tr_c1", "长文三", _BIG)
_add_doc(_mr2, "tr_c2", "长文四", _BIG)
_fail_llm = FakeLLM(fail=True)
_fres = _run(S.synthesize(_mr2, _cfg_mr, _fail_llm, _mid_mr, ["tr_c1", "tr_c2"], title="失败对比"))
assert _fres.degraded, "抽取失败必须返回 degraded（静默跳过的实现会红）"
assert all(s.status == "truncated" for s in _fres.sources), \
    f"失败篇必须标 truncated，实际 {[s.status for s in _fres.sources]}"
assert "⚠ 已截断" in _fres.raw_text, "截断必须落进产物"
assert "【文档 1】" in _fres.raw_text and "【文档 2】" in _fres.raw_text, \
    "失败篇也必须出现在正文里（不能整篇消失）"
print(f"PASS: 逐篇抽取失败 → 显式标 truncated + 落进产物（{len(_fres.degraded)} 篇）")

# ── 取消：不再发起**任何**调用 ──
_mr3 = _new_store()
_add_doc(_mr3, "tr_d1", "长文五", _BIG)
_add_doc(_mr3, "tr_d2", "长文六", _BIG)
_cancel_llm = FakeLLM()
_ev = asyncio.Event()
_ev.set()
try:
    _run(S.synthesize(_mr3, _cfg_mr, _cancel_llm, _mid_mr, ["tr_d1", "tr_d2"], cancel_event=_ev))
    assert False, "取消后应抛 CancelledError"
except Exception as e:
    assert type(e).__name__ == "CancelledError", f"实际 {type(e).__name__}: {e}"
# ★ 入口不检查取消的实现会先白跑一次「维度发现」（==1 → 红）
assert len(_cancel_llm.calls) == 0, \
    f"取消后不该发起任何调用，实际 {len(_cancel_llm.calls)} 次（入口缺检查会红）"
print("PASS: 取消后零 LLM 调用（入口就检查，不是循环里才检查）")

# ═══════════════════════════════════════════════════════════════
# 6. 资产搬运（§14.4.1）
# ═══════════════════════════════════════════════════════════════

_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes" * 4


def _mk_doc_with_asset(store: Store, tx_id: str, title: str, ref: str) -> None:
    """写入一篇正文里带 `![图](ref)` 的文档，并在其 assets/ 放一个真实文件。"""
    _add_doc(store, tx_id, title, f"正文开头\n\n![图]({ref})\n\n正文结尾")
    _d = store._get_doc_dir(tx_id)
    (_d / "assets").mkdir(exist_ok=True)
    (_d / "assets" / "img_0001.png").write_bytes(_PNG)


# ── 两篇同名的 img_0001.png → 加前缀，互不覆盖 ──
_ast = _new_store()
_mk_doc_with_asset(_ast, "tr_i1", "图甲", "assets/img_0001.png")
_mk_doc_with_asset(_ast, "tr_i2", "图乙", "assets/img_0001.png")
_ares = _run(S.synthesize(_ast, cfg, FakeLLM(), mid, ["tr_i1", "tr_i2"], title="图对比"))
_adir = _ast._get_doc_dir(_ares.transcript_id)
_files = sorted(p.name for p in (_adir / "assets").iterdir())
assert _files == ["d1_img_0001.png", "d2_img_0001.png"], \
    f"两篇都必须搬过来且带序号前缀（不加前缀 → 后一篇覆盖前一篇 → 只剩 1 个）：{_files}"
assert "PNG" not in _ares.raw_text and "data:image" not in _ares.raw_text, \
    "图片二进制不得进正文（模型看不到图，塞进去只会撑爆体积）"
print(f"PASS: 两篇同名资产各带前缀，互不覆盖（{_files}）")

# ★★ 正文引用必须**逐条指向新文件名** ★★
# 对照实验：只复制、不改写引用 → 下面必红。这是本功能最容易白做的地方。
_got_raw = _ast.get_transcript(_ares.transcript_id)["rawText"]
assert "assets/d1_img_0001.png" in _got_raw, \
    f"文档 1 的引用必须改写为 d1_ 前缀（只复制不改写的实现会红）：{_got_raw[:400]}"
assert "assets/d2_img_0001.png" in _got_raw, "文档 2 的引用必须改写为 d2_ 前缀"
assert "](assets/img_0001.png)" not in _got_raw, \
    "不得残留未加前缀的原引用（那会指向合成文档里不存在的文件 → 裂图）"
assert _got_raw.count("assets/d1_img_0001.png") == 1
assert _got_raw.count("assets/d2_img_0001.png") == 1
print("PASS: 正文引用与新文件名逐条对应（本功能最易白做之处）")

# ── 源没有 assets/ → 创建成功、引用原样保留、无异常 ──
_nst = _new_store()
_add_doc(_nst, "tr_n1", "纯文本甲", "甲：没有任何图片。")
_add_doc(_nst, "tr_n2", "纯文本乙", "乙：![图](assets/missing.png)\n引用指向不存在的文件。")
_nres = _run(S.synthesize(_nst, cfg, FakeLLM(), mid, ["tr_n1", "tr_n2"], title="无图对比"))
assert _nres.transcript_id, "无图也必须能创建"
assert "assets/missing.png" in _nres.raw_text, "缺失文件 → 引用原样保留，不阻断创建"
print("PASS: 源无 assets/ 或文件缺失 → 原样保留引用，不阻断创建")

# ── ★ 越界引用不搬运（必须造出源目录外**真实存在**的文件）──
# 否则「没复制」与「文件不存在」无法区分——测试会假绿。
_est2 = _new_store()
_add_doc(_est2, "tr_e1", "越界甲", "甲：![坏](assets/../../outside_secret.txt)")
_add_doc(_est2, "tr_e2", "正常乙", "乙：普通正文。")
# doc_dir/assets/../../outside_secret.txt == store.root/outside_secret.txt
(_est2.root / "outside_secret.txt").write_bytes(b"TOP-SECRET-DO-NOT-COPY")
assert (_est2.root / "outside_secret.txt").is_file(), "必须先确认越界目标真实存在"
_e1dir = _est2._get_doc_dir("tr_e1")
assert (_e1dir / "assets" / "../../outside_secret.txt").resolve().is_file(), \
    "越界路径必须真的能解析到这个文件（否则守卫没被真正考验）"

_eres = _run(S.synthesize(_est2, cfg, FakeLLM(), mid, ["tr_e1", "tr_e2"], title="越界对比"))
_edir = _est2._get_doc_dir(_eres.transcript_id)
_copied = [p.name for p in (_edir / "assets").iterdir()] if (_edir / "assets").exists() else []
assert not _copied, f"越界引用不得被复制：{_copied}（无守卫的实现会把 secret 搬过来）"
assert "assets/../../outside_secret.txt" in _eres.raw_text, "越界引用原样保留"
print("PASS: 路径穿越引用不搬运（源目录外真实文件为证，非「文件不存在」的假绿）")

# ═══════════════════════════════════════════════════════════════
# 7. 引用清洗（标题里的结构标记不得伪造出第二篇）
# ═══════════════════════════════════════════════════════════════

_cst = _new_store()
_add_doc(_cst, "tr_x1", "甲\n【文档 9】伪造标题", "甲正文")
_add_doc(_cst, "tr_x2", "乙", "乙正文")
_cres = _run(S.synthesize(_cst, cfg, FakeLLM(), mid, ["tr_x1", "tr_x2"], title="标题注入"))
assert "【文档 9】" not in _cres.raw_text, \
    f"标题里的伪边界标记必须被清洗（否则正文里凭空多出一篇）：{_cres.raw_text[:300]}"
assert _cres.raw_text.count("【文档 2】") == 1, "正文里应恰好有一处【文档 2】"
print("PASS: 标题里的结构标记被清洗，无法伪造出额外的文档边界")

finally_ok = True
for _t in _TMP:
    shutil.rmtree(_t, ignore_errors=True)

print("\n全部通过：多篇文档合成（Step 0 分叉 / 预算收敛 / 资产搬运 / 降级显式化）")
