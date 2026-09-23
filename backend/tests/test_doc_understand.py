"""验证文档直传（PDF file 块）— 闸门 / 编码 / 能力门 / 零影响回归

⚠️ **本文件不发任何真实请求**，全部是本地纯函数 + 一次 `prepare()` 调用。
plan 原方案里的「超时」用例在此**不适用**：`build_pdf_document` 只做「读文件 +
base64」（百毫秒级），不发起 LLM 调用，故没有超时可测。`DOC_DIRECT_TIMEOUT`
的真实语义是「直传模式下生成调用的**首个 chunk 静默超时**」（模型解析原件的
那段时间），落点在 effort.py::chunk_timeout_for —— 用例 6 测的是那条链。

⚠️ 闸门用例断言的是**真实数字**（14 MiB 边界的两侧），不是「函数被调用了」。
前者能抓住「闸门写成了 150MB（厂商宣传值）却照样通过」这类回归，后者抓不住。
"""
import sys, os, base64, asyncio, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.services.doc_understand as du
from app.generation.content_preparer import ContentPreparer


# ── 日志捕获：替换 doc_understand 模块上的 log 属性（不影响 app.core.logging） ──
class _FakeLog:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, msg, **kw):
        self.warnings.append((msg, kw))

    def info(self, msg, **kw):
        self.infos.append((msg, kw))

    def debug(self, msg, **kw):
        pass


_orig_log = du.log


def capture():
    fake = _FakeLog()
    du.log = fake
    return fake


def release():
    du.log = _orig_log


_TMP = tempfile.mkdtemp(prefix="limo_doc_direct_")


def write(path: str, data: bytes) -> str:
    with open(path, "wb") as f:
        f.write(data)
    return path


# ═══════════════════════════════════════════════════════════════
# 1. 成功路径：编码无损（base64 往返 == 原字节）
# ═══════════════════════════════════════════════════════════════

payload = b"%PDF-1.4\n" + bytes(range(256)) * 40 + b"\n%%EOF"
p_ok = write(os.path.join(_TMP, "论文.pdf"), payload)

doc = du.build_pdf_document(p_ok)
assert doc is not None, '正常 PDF 应可直传'
assert doc["data_uri"].startswith("data:application/pdf;base64,"), \
    f'MIME 必须是 application/pdf，实际 {doc["data_uri"][:40]}'
decoded = base64.b64decode(doc["data_uri"].split(",", 1)[1])
assert decoded == payload, 'base64 往返必须逐字节等于原文件（编码无损）'
assert doc["filename"] == "论文.pdf", f'文件名不对：{doc["filename"]}'
print('PASS: 成功路径（MIME 前缀 + base64 无损往返 + 文件名）')


# ═══════════════════════════════════════════════════════════════
# 2. 体积闸门 —— 断言 14 MiB 的真实边界，两侧都测
#    （闸门值取 14 而非厂商宣称的 150MB，也不是贴边的 15 MiB：
#    15 MiB 算出的 data-uri 恰好多 25 字节，照样被 dashscope 拒）
# ═══════════════════════════════════════════════════════════════

assert du.DOC_DIRECT_MAX_BYTES == 14 * 1024 * 1024, \
    f'闸门应为 14 MiB，实际 {du.DOC_DIRECT_MAX_BYTES}'

# 恰好等于上限 → 放行（闸门是「超过」才拒，不是「达到」）
p_edge = os.path.join(_TMP, "edge.pdf")
with open(p_edge, "wb") as f:          # 稀疏文件，不真占 14MB 磁盘
    f.seek(du.DOC_DIRECT_MAX_BYTES - 1)
    f.write(b"\0")
assert os.path.getsize(p_edge) == du.DOC_DIRECT_MAX_BYTES, '前置：稀疏文件尺寸应等于闸门'
assert du.build_pdf_document(p_edge) is not None, '恰好等于上限应放行'

# 超限 1 字节 → 拒，且日志带真实尺寸（「开了开关但没用上」要可见）
p_over = os.path.join(_TMP, "over.pdf")
with open(p_over, "wb") as f:
    f.seek(du.DOC_DIRECT_MAX_BYTES)
    f.write(b"\0")
assert os.path.getsize(p_over) == du.DOC_DIRECT_MAX_BYTES + 1, '前置：应超出 1 字节'
fake = capture()
r = du.build_pdf_document(p_over)
release()
assert r is None, '超限必须返回 None（调用方据此保持文本模式）'
assert len(fake.warnings) == 1, f'超限应产生 1 条 warning，实际 {len(fake.warnings)}'
_msg, _kw = fake.warnings[0]
assert _kw.get("limit_mb") == 14, f'日志应带闸门值，实际 {_kw}'
assert _kw.get("size_mb") == 14.0, f'日志应带实际尺寸，实际 {_kw}'
print('PASS: 体积闸门（14 MiB 边界两侧 + 日志带尺寸原因）')


# ═══════════════════════════════════════════════════════════════
# 3. 其余拒收路径：非 PDF / 空 / 不存在
#    ⚠️ 扩展名闸门是**纵深防御**：前端只在 isPDF 时显示开关，但 API 可直调，
#    而 MIME 被硬编码为 application/pdf —— 非 PDF 进来会以错误类型发出。
# ═══════════════════════════════════════════════════════════════

p_txt = write(os.path.join(_TMP, "notes.txt"), payload)
fake = capture()
r = du.build_pdf_document(p_txt)
release()
assert r is None, '非 .pdf 扩展名必须拒（哪怕内容恰好是 PDF 字节）'
assert len(fake.warnings) == 1 and "仅支持 PDF" in fake.warnings[0][0], \
    f'应带「仅支持 PDF」原因，实际 {fake.warnings}'

p_empty = write(os.path.join(_TMP, "empty.pdf"), b"")
fake = capture()
r = du.build_pdf_document(p_empty)
release()
assert r is None, '空文件必须拒（base64 为空串，发出去是无意义的空附件）'
assert "为空" in fake.warnings[0][0], f'应带「为空」原因，实际 {fake.warnings}'

fake = capture()
r = du.build_pdf_document(os.path.join(_TMP, "不存在.pdf"))
release()
assert r is None, '不存在的路径必须拒（tmp 被外部清理的典型形态）'
assert len(fake.warnings) == 1, '失败必须有日志留痕'
print('PASS: 非 PDF / 空文件 / 不存在 → 均拒并留痕')


# ═══════════════════════════════════════════════════════════════
# 4. 文件名清洗
# ═══════════════════════════════════════════════════════════════

_uuid = "a" * 32
assert du.clean_filename(f"/tmp/{_uuid}_报告.pdf") == "报告.pdf", \
    '落盘件的 uuid 前缀应剥掉（对模型是噪音）'
assert du.clean_filename("/tmp/报告.pdf") == "报告.pdf", '无前缀时不动'
assert du.clean_filename("") == "document.pdf", '空须回退，不能给出空 filename'
assert du.clean_filename("/tmp/") == "document.pdf", '路径结尾只有分隔符时同样回退'
_long = "题" * 200 + ".pdf"
_cleaned = du.clean_filename("/tmp/" + _long)
assert len(_cleaned) <= du._FILENAME_MAX, f'超长应截断，实际 {len(_cleaned)}'
assert _cleaned.endswith(".pdf"), '截断必须保留扩展名（否则 MIME 与名字对不上）'
print('PASS: 文件名清洗（uuid 前缀 / 回退 / 限长保扩展名）')


# ═══════════════════════════════════════════════════════════════
# 5. 能力门 + prepare() 零影响回归
#    ⚠️ 核心断言：doc_direct=False 时，prepare() 的输出必须与「这个参数
#    根本不存在」时**逐字节相同** —— 新参数是增量，不是重写。
# ═══════════════════════════════════════════════════════════════

_RAW = "# 标题\n\n这是一段正文。\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"


def run_prepare(caps=None, **kw):
    cfg = {"capabilities": caps} if caps is not None else {}
    p = ContentPreparer(store_root=_TMP, llm_config=cfg)
    return asyncio.run(p.prepare(_RAW, **kw))


base = run_prepare()
off = run_prepare(doc_direct=False)
off_with_path = run_prepare(doc_direct=False, doc_path=p_ok)
assert base == off == off_with_path, (
    '零影响回归失败：doc_direct=False 时的输出必须与未传该参数逐字节一致\n'
    f'  base={base!r}\n  off ={off!r}')

# 同上，但模型**标了 document 能力**（真实场景：能力在、开关关着）——
# 零影响必须与能力位无关，否则「换个模型就变了行为」是最难查的一类回归
_DOC_CAP = ["text", "document"]
base_cap = run_prepare(caps=_DOC_CAP)
assert base_cap == base, '标了 document 能力但未开直传时，输出也必须与原来逐字节一致'
assert run_prepare(caps=_DOC_CAP, doc_direct=False, doc_path=p_ok)[0] == base[0]

on = run_prepare(caps=_DOC_CAP, doc_direct=True, doc_path=p_ok)
assert on[3] is not None, '直传开启且 PDF 有效时，第 4 个返回值应是文档块'
assert on[0] == ContentPreparer._DOC_DIRECT_PLACEHOLDER, \
    f'直传时正文应被**替换**为占位说明（追加 = token 双花），实际 {on[0]!r}'
assert _RAW not in on[0], '直传模式下原文不得出现在 prompt 里（只发原文件）'
assert on[1] is None, '直传时不应再带图片（prompt 里没有正文可嵌入的位置）'
print('PASS: prepare() 零影响回归 + 直传替换语义（正文不进 prompt / 图片不发）')

# 能力门：llm_config 未标 document → 保持文本模式（返回 4 元组但 document=None）
p_nocap = ContentPreparer(store_root=_TMP, llm_config={"capabilities": ["text", "image"]})
nocap = asyncio.run(p_nocap.prepare(_RAW, doc_direct=True, doc_path=p_ok))
assert nocap[3] is None, '未标 document 能力的模型不得直传'
assert nocap[0] == base[0], '能力门挡住时应完整回退到文本模式（正文原样）'

p_cap = ContentPreparer(store_root=_TMP, llm_config={"capabilities": ["text", "document"]})
assert asyncio.run(p_cap.prepare(_RAW, doc_direct=True, doc_path=p_ok))[3] is not None, \
    '标了 document 能力的模型应可直传'
# 开关开着但文档没有原件路径（如 tmp 被清理）→ 静默回退，不报错
assert asyncio.run(p_cap.prepare(_RAW, doc_direct=True, doc_path=""))[3] is None, \
    '无原件路径时应保持文本模式'
print('PASS: 能力门（未标 document → 文本模式；标了 → 直传；无路径 → 文本模式）')


# ═══════════════════════════════════════════════════════════════
# 6. 静默期超时链 —— DOC_DIRECT_TIMEOUT 的真实落点
# ═══════════════════════════════════════════════════════════════

from app.generation.effort import chunk_timeout_for, CHUNK_TIMEOUT_DEFAULT, CHUNK_TIMEOUT_MAX

assert chunk_timeout_for("low", doc_direct=True) == du.DOC_DIRECT_TIMEOUT, \
    '直传时静默期必须放宽到 DOC_DIRECT_TIMEOUT（实测首包最长 ~150s，默认 150s 会误杀）'
assert chunk_timeout_for("high", doc_direct=True) == du.DOC_DIRECT_TIMEOUT
assert chunk_timeout_for("max", doc_direct=True) == max(CHUNK_TIMEOUT_MAX, du.DOC_DIRECT_TIMEOUT)
# 未直传时保持原样（零回归）
assert chunk_timeout_for("high") == CHUNK_TIMEOUT_DEFAULT
assert chunk_timeout_for("max") == CHUNK_TIMEOUT_MAX
assert chunk_timeout_for(None) == CHUNK_TIMEOUT_DEFAULT
# 两个来源正交：不能因为 doc_direct 就丢掉档位的影响
assert chunk_timeout_for("max", doc_direct=True) >= chunk_timeout_for("high", doc_direct=True)

# max() 合并而非直接 return 的回归守卫：把 DOC_DIRECT_TIMEOUT 临时调到比档位还小，
# 直传不应反而更早被判挂起（那是个「看起来更安全」的隐蔽回归）
_orig_to = du.DOC_DIRECT_TIMEOUT
import importlib
import app.generation.effort as _eff
du.DOC_DIRECT_TIMEOUT = 1.0
try:
    importlib.reload(_eff)          # 常量是 import 期绑定的，重载才生效
    assert _eff.chunk_timeout_for("max", doc_direct=True) == _eff.CHUNK_TIMEOUT_MAX, \
        'DOC_DIRECT_TIMEOUT 被调小时，直传不应比普通档更早超时（必须是 max 合并）'
finally:
    du.DOC_DIRECT_TIMEOUT = _orig_to
    importlib.reload(_eff)
print('PASS: 静默期超时链（直传放宽 / 档位正交 / max 合并的回归守卫）')


# ═══════════════════════════════════════════════════════════════
# 7. rawText 过短的完成期提示 —— 双真相源唯一可观测的出口
#    （直传下模型读原件，而转录/导出/搜索读本地 rawText，扫描件上两者真分叉）
# ═══════════════════════════════════════════════════════════════

from app.routers.generate import _doc_direct_raw_warning

_doc = {"filename": "扫描件.pdf", "data_uri": "x"}
assert _doc_direct_raw_warning(None, "") is None, '未直传 → 不该提示'
assert _doc_direct_raw_warning(None, "有内容" * 50) is None, '未直传 → 不该提示'
assert _doc_direct_raw_warning(_doc, "有内容" * 50) is None, '直传且抽取正常 → 不提示'
_msg = _doc_direct_raw_warning(_doc, "")
assert _msg and "转录" in _msg, f'直传 + 空 rawText 必须给出提示，实际 {_msg!r}'
# 判据是 100 字符而非「空串」：扫描件无 OCR 时 rawText 是**占位文本**（约 50 字符），
# 只判空串会漏掉这种「看起来有内容、实际没内容」的典型形态
_placeholder_raw = "![原图](assets/p1.png)\n\n_所有 OCR 引擎均失败_\n"
assert len(_placeholder_raw) < du.DOC_DIRECT_MIN_RAW_CHARS, '前置：占位文本应短于判据'
assert _doc_direct_raw_warning(_doc, _placeholder_raw) is not None, \
    'OCR 全失败留下的占位文本必须触发提示（只判空串的实现在此失败）'
assert _doc_direct_raw_warning(_doc, "字" * du.DOC_DIRECT_MIN_RAW_CHARS) is None, \
    '恰好达到判据 → 不提示（边界）'
print('PASS: rawText 过短提示（含「占位文本」形态 —— 判空串的实现在此失败）')

# ═══════════════════════════════════════════════════════════════
# 8. 调用点对称性（AST 全库扫描）—— 模式 #16 的防线
#
# ContentPreparer.prepare 新增了 doc_direct/doc_path 两个参数，而它在库里有多处
# 调用（流式 + 非流式 + 两个 task）。历史教训：这类「只给一条路径传新参数」的
# 漏改**不会报错**——那条路径静默走默认值，功能「上线了但没反应」。
# 故用结构断言把它钉死：凡是调 ContentPreparer.prepare 的地方，必须齐传。
#
# 识别条件用 `send_to_llm`（ContentPreparer.prepare 独有的 kwarg）而非变量名 ——
# 变量名（preparer / self._prep）是实现细节，改名就会让检查静默失效。
# ═══════════════════════════════════════════════════════════════

import ast, pathlib

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"
_MARKER = "send_to_llm"          # ContentPreparer.prepare 独有
_calls, _bad_args, _bad_unpack = [], [], []

for _py in sorted(_APP.rglob("*.py")):
    _tree = ast.parse(_py.read_text(encoding="utf-8"))
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Call):
            continue
        _fn = _node.func
        _name = _fn.attr if isinstance(_fn, ast.Attribute) else (
            _fn.id if isinstance(_fn, ast.Name) else "")
        if _name != "prepare":
            continue
        _kws = {k.arg for k in _node.keywords}
        if _MARKER not in _kws:
            continue                          # 不是 ContentPreparer.prepare
        _where = f"{_py.relative_to(_APP.parent)}:{_node.lineno}"
        _calls.append(_where)
        for _need in ("doc_direct", "doc_path"):
            if _need not in _kws:
                _bad_args.append(f"{_where} 缺 {_need}=")
        # 赋值解包必须是 4 个名字（漏改调用点会立刻 ValueError，此处提前拦）
        for _p in ast.walk(_tree):
            if (isinstance(_p, ast.Assign) and isinstance(_p.value, ast.Call)
                    and _p.value is _node and isinstance(_p.targets[0], ast.Tuple)
                    and len(_p.targets[0].elts) != 4):
                _bad_unpack.append(f"{_where} 解包 {len(_p.targets[0].elts)} 个名字（应为 4）")

assert _calls, '扫不到任何 ContentPreparer.prepare 调用点 —— 检查条件失效了（模式 #39）'
assert len(_calls) >= 3, f'调用点应 ≥3（流式 + 非流式 + 两个 task），实际 {_calls}'
assert not _bad_args, f'以下调用点漏传直传参数，会静默走文本模式：{_bad_args}'
assert not _bad_unpack, f'解包数量不对：{_bad_unpack}'
print(f'PASS: 调用点对称性（{len(_calls)} 处全传 doc_direct/doc_path + 4 元组解包）')
# ⚠️ 对照（已验证有区分力）：删掉 generate.py 流式 prepare 里的 `doc_direct=doc_direct,`
#    → 本条变红。

# ═══════════════════════════════════════════════════════════════
# 9. 超时常量的**官方下界** —— 实测证明不了上界
#
# 初版 DOC_DIRECT_TIMEOUT = 240 是「实测首包 149.3s × 1.6 余量」算出来的，看着很稳；
# 但官方《PDF 理解》页明写「首包超时时间最长为 300 秒」。240 < 300，于是落在
# 240~300s 区间的**正常请求会被我们自己的超时掐断**，然后静默降级回文本模式，
# 日志里只显示「超时」——用户和开发者都会以为是厂商没做好，而不是我们没等够。
#
# 模式 #39 的又一形态：**判据跑到了，但它算的是样本不是上界**。
# 实测只覆盖手上那几个 PDF，永远证不出「不可能更慢」；上界只写在官方文档里。
# ═══════════════════════════════════════════════════════════════

_VENDOR_FIRST_CHUNK_MAX = 300.0   # 官方承诺的首包最坏值（见上注）

assert du.DOC_DIRECT_TIMEOUT > _VENDOR_FIRST_CHUNK_MAX, (
    f'直传静默期 {du.DOC_DIRECT_TIMEOUT}s 必须 > 官方承诺的首包上限 {_VENDOR_FIRST_CHUNK_MAX}s：'
    '否则区间内的正常请求会被自己的超时掐断，且只表现为「静默降级」'
)

# 跨层关系：request_timeout 是 httpx 的 read timeout，必须包得住单 chunk 静默期。
# 反了的话 SDK 先于 wait_for 抛超时，异常被下游归到「连接中断」并按那条路重试——
# 真死因（等得不够久）被文案掩盖。这条用真实的两个函数断言，而非直接比常量。
from app.routers.generate import _gen_timeout   # noqa: E402

_direct_req_timeout = _gen_timeout([], doc_direct=True)
assert _direct_req_timeout >= chunk_timeout_for("low", doc_direct=True), \
    f'httpx read timeout {_direct_req_timeout}s < 静默超时 —— SDK 会先抛，归因错成「连接中断」'
assert _direct_req_timeout >= chunk_timeout_for("max", doc_direct=True)
# 总超时要容得下「首包静默 + 之后还要正常生成数千字」两段，等于静默期本身即首包一到就断
assert du.DOC_DIRECT_GEN_TIMEOUT >= du.DOC_DIRECT_TIMEOUT * 2, (
    f'总超时 {du.DOC_DIRECT_GEN_TIMEOUT}s 容不下「首包 {du.DOC_DIRECT_TIMEOUT}s + 生成预算」两段'
)
print(f'PASS: 超时常量（静默 {du.DOC_DIRECT_TIMEOUT}s > 官方上限 {_VENDOR_FIRST_CHUNK_MAX}s；'
      f'httpx {_direct_req_timeout}s 包得住静默；总 {du.DOC_DIRECT_GEN_TIMEOUT}s 容得下两段）')


# ── _gen_timeout 的调用点对称性（与第 8 节同法）──
#
# 它的默认 `doc_direct=False` 让「漏传」不会报错、只会静默少等——正是模式 #16 的
# 形状（今天 generate.py 流式端点构造 LLMService 时就漏了这一处，非流式那侧传了）。
# 故要求**每一处调用都显式传**，不给默认值留活口。
_to_calls, _to_bad = [], []
for _py2 in sorted(_APP.rglob("*.py")):
    _tree2 = ast.parse(_py2.read_text(encoding="utf-8"))
    for _n2 in ast.walk(_tree2):
        if not isinstance(_n2, ast.Call):
            continue
        _f2 = _n2.func
        if (getattr(_f2, "id", None) or getattr(_f2, "attr", None)) != "_gen_timeout":
            continue
        _w2 = f"{_py2.relative_to(_APP.parent)}:{_n2.lineno}"
        _to_calls.append(_w2)
        if "doc_direct" not in {k.arg for k in _n2.keywords}:
            _to_bad.append(_w2)

assert _to_calls, '扫不到 _gen_timeout 调用点 —— 检查条件失效（模式 #39）'
assert not _to_bad, f'以下 _gen_timeout 调用点漏传 doc_direct=，直传时 request_timeout 不放宽：{_to_bad}'
print(f'PASS: _gen_timeout 调用点对称性（{len(_to_calls)} 处全传 doc_direct=）')
# ⚠️ 对照（已验证有区分力）：把 generate.py 流式端点的
#    `_gen_timeout(format_list, doc_direct=doc_direct)` 改回 `_gen_timeout(format_list)`
#    → 本条变红并打印出该行号。

print('\n全部通过：文档直传（闸门 / 编码 / 能力门 / 零影响回归 / 超时链 / 提示 / 调用点 / 常量下界）')
