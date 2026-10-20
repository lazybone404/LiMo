"""测试共享装置 —— 全仓第一个 `conftest`。

存在的理由很具体：查询层的断言要跨**多个**文件（service 单元 / HTTP 契约 / 三期
CLI 门禁脚本），而每个文件各自拼一遍「建临时 Store + 塞几篇文档」的样板，正是本项目
反复栽的**同一判据多份副本**。副本在这里的失效方式格外隐蔽：**测试之间静默不一致**
——一处改了、另一处没改，于是某个文件卡在旧假设上、另一个已经在新假设上，两边都绿。

⚠️ 工厂写成**普通函数**而不是只写 pytest fixture：三期 CLI 的门禁脚本要 `import`
它，而那不是 pytest 进程（`conftest` 是 pytest 专属的加载约定，普通 import 取不到）。
下面的 pytest fixture 只是薄封装，供 `test_capabilities.py` 这类 pytest 风格文件用。

⚠️ 本文件同时负责把 `backend/` 放进 `sys.path` —— 全仓 30 多个测试文件此前各自
写了一行同样的 `sys.path.insert`。新文件**不要再写那一行**（pytest 会先加载本文件）。
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

#: `test_e2e_smoke.py` 是**脚本**不是 pytest 用例（自己起后端 + 全链路冒烟 + 退出码即结论），
#: 而文件名匹配 `test_*.py`。不排除的话 pytest 会把它当用例文件收集，两个后果：
#:   · **收集阶段 import 就执行模块顶层** —— 每跑一次 `pytest tests/` 就静默起一个后端、
#:     打几十个请求、写一次配置（`__pycache__/*-pytest-*.pyc` 就是它被收集过的证据）；
#:   · 单独跑时报「0 个用例」exit 5，看起来像失败。
#: 排掉之后两条路各归各位：`pytest tests/` 收集不到它，`python tests/test_e2e_smoke.py` 照跑。
collect_ignore = ["test_e2e_smoke.py"]

from app.core.types import RawMetadata, RawTranscript, SourceType  # noqa: E402
from app.storage.store import Store  # noqa: E402

_TMP: list[str] = []


#: 导出 HTML 的 CSP nonce，**两种形态都要归一**（顺序无关：两个字符集互不包含）：
#:   · CSP 指令里的 `'nonce-<token>'`（`<meta http-equiv="Content-Security-Policy">`）
#:   · 标签属性里的 `nonce="<token>"`（`<script nonce="…">`）
#: ⚠️ 只归一前者是**实测踩到的坑**：逐字节断言照样红，而 diff 会把 1.4MB 的 KaTeX
#: bundle 整段吐出来 —— 差异点正好落在 `<script nonce="…">` 之后，148 万字符「相同」。
_CSP_NONCE_ATTR_RE = re.compile(r'nonce="[A-Za-z0-9_-]+"')
_CSP_NONCE_DIRECTIVE_RE = re.compile(r"nonce-[A-Za-z0-9_-]+")


def normalize_export_nonce(html: str) -> str:
    """把导出 HTML 里的 CSP nonce 归一成固定占位符。

    导出 HTML 每次调用都生成**新的随机 nonce**（`secrets.token_urlsafe(16)`，见
    `routers/export.py::_csp_meta`）—— 那是「正文里的 `<script>` 一律不执行」的
    实现手段，不是不稳定。于是任何「两次导出逐字节相等」的断言都会被它打破，
    而那条断言真正想钉的是**样式/结构没有多一段**，与 nonce 无关。

    ⚠️ 归一化**只动 nonce 值**，meta 标签本身仍在串里 —— 于是「CSP 缺失」这类
    回归（比如某次重构把注入闸门写歪）依然会被逐字节断言抓到。
    ⚠️ 需要区分「两次 nonce 是否相同」的用例（nonce 复用 = 可预测 = 防护失效）
    不要用本函数，直接比对原文。
    """
    return _CSP_NONCE_DIRECTIVE_RE.sub(
        "nonce-{NONCE}", _CSP_NONCE_ATTR_RE.sub('nonce="{NONCE}"', html))


def make_store() -> Store:
    """新建一个**隔离**的临时 Store（每个调用独立目录，互不干扰）。"""
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return Store(d)


def cleanup_stores() -> None:
    """清掉本进程创建的全部临时 Store。测试脚本末尾调用。"""
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    _TMP.clear()


def import_doc(store: Store, tx_id: str, *, title: str = "", source_type=SourceType.WEBPAGE,
               created_at: str = "", raw_text: str = "", content_hash: str = "") -> None:
    """往 Store 里塞一篇文档。

    `created_at` 可显式指定——分页测试要**造并列时间戳**（同一秒多篇），而那是
    游标判据最容易出错的地方（只按 createdAt 排序时并列项的顺序不确定）。
    默认留空则由 Store 用当前时间填。
    """
    store.save_transcript(RawTranscript(
        id=tx_id,
        title=title or f"文档 {tx_id}",
        source_type=source_type,
        engine="webpage",
        raw_text=raw_text or f"{tx_id} 的正文内容。",
        content_hash=content_hash or f"hash_{tx_id}",
        metadata=RawMetadata(),
        tags=[],
        source_url="https://example.com/a",
        **({"created_at": created_at} if created_at else {}),
    ))


# ── pytest 薄封装（非 pytest 脚本直接用上面的函数）──

try:
    import pytest
except ImportError:  # pragma: no cover - pytest 必然存在，仅为 import 安全
    pytest = None

if pytest is not None:
    @pytest.fixture
    def tmp_store():
        """pytest fixture：一个隔离的临时 Store，测试结束自动清理。"""
        st = make_store()
        yield st
        shutil.rmtree(st.root, ignore_errors=True)
