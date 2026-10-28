"""arXiv 适配器 —— id 解析、URL 改写、委托链、临时目录清理、注册顺序。

这个适配器**自己不解析 PDF**，它只做三件事：认出 arXiv 链接 / 拼出 PDF 直链 /
把活交给 `DocumentAdapter`。所以测试的重点不是「能不能读出 PDF 正文」（那是
`test_adapters.py` 的事），而是：

1. `accept()` 认得对、**不误伤**（拿别人的 `/abs/` 当 arXiv 是真会发生的）
2. 注册顺序对 —— 这条有真正的失败模式：`/pdf/xxx.pdf` 会被 `DocumentAdapter`
   按扩展名抢走，那一版**能跑**，所以顺序错了没有任何报错，只是 `/abs/` 与
   无扩展名的 `/pdf/` 一直走网页路径（拿到一屏「摘要 + 侧边栏」）。
3. 临时目录**成功和失败都清** —— 不清的话每导一篇留一个 200KB 目录，
   而目录名带随机后缀，用户在半年后才发现 temp 爆了。
"""
import asyncio
from pathlib import Path

import pytest

from app.adapters.arxiv import ArxivAdapter, parse_arxiv_id
from app.core.types import PipelineInput, InputType, AdapterOutput, RawMetadata


# ═══════════════════════════════════════════════════════════
# 1. id 解析 / accept()
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("url,expected", [
    # 摘要页（用户从浏览器复制到的最常见形态）
    ("https://arxiv.org/abs/2401.12345", "2401.12345"),
    ("http://arxiv.org/abs/2401.12345v2", "2401.12345v2"),
    ("https://www.arxiv.org/abs/2401.12345", "2401.12345"),
    # 从搜索结果里复制的链接常带查询串
    ("https://arxiv.org/abs/2401.12345?context=cs", "2401.12345"),
    # PDF 直链三种写法
    ("https://arxiv.org/pdf/2401.12345", "2401.12345"),
    ("https://arxiv.org/pdf/2401.12345v1.pdf", "2401.12345v1"),
    ("https://arxiv.org/pdf/2401.12345.pdf", "2401.12345"),
    # 老式编号（分类含连字符 / 带点）
    ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"),
    ("https://arxiv.org/abs/math.AG/0601001", "math.AG/0601001"),
])
def test_认得出各种_arxiv_链接形态(url, expected):
    assert parse_arxiv_id(url) == expected


@pytest.mark.parametrize("url", [
    "https://arxiv.org/list/cs.AI/recent",       # 列表页，不是论文
    "https://arxiv.org/abs/",                    # 没有 id
    "https://arxiv.org/",                        # 首页
    "https://arxiv.org/help/api",                # 文档页
    "https://example.com/abs/2401.12345",        # 别人的站点用了同样的路径
    "https://notarxiv.org/abs/2401.12345",       # 域名后缀陷阱（不是 arxiv.org）
    "https://arxiv.org/abs/240.12345",           # 编号位数不足
    "", None,
])
def test_不认识的链接一律返回空(url):
    assert parse_arxiv_id(url or "") == ""


def test_accept只对URL类型为真():
    """FILE 类型（本地 `.pdf`）不该走这里 —— 那是 DocumentAdapter 的活。"""
    a = ArxivAdapter()
    assert a.accept(PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345"))
    assert not a.accept(PipelineInput(type=InputType.FILE, file_path="/tmp/2401.12345.pdf"))
    assert not a.accept(PipelineInput(type=InputType.TEXT, text="hi"))


# ═══════════════════════════════════════════════════════════
# 2. adapt()：URL 改写 + 委托 + 清理
# ═══════════════════════════════════════════════════════════

class _Resp:
    def __init__(self, content: bytes = b"%PDF-1.4 fake"):
        self.content = content
        self.calls = 0

    def raise_for_status(self):
        self.calls += 1


def _mock_download(monkeypatch, content: bytes = b"%PDF-1.4 fake") -> dict:
    """把 `safe_get` 换成返回固定字节的替身，记录请求的 URL / 头部。"""
    rec: dict = {}

    def _fake_get(url, **kw):
        rec["url"] = url
        rec["kwargs"] = kw
        return _Resp(content)

    # arxiv.py 里是**函数内** import，monkeypatch 模块属性对它有效
    monkeypatch.setattr("app.utils.url_security.safe_get", _fake_get)
    return rec


def _mock_document_adapter(monkeypatch, *, raise_exc: Exception | None = None) -> dict:
    """把委托目标换成替身，记录它收到的 `PipelineInput` 与当时磁盘上的文件。"""
    rec: dict = {}

    async def _fake_adapt(self, input, on_progress=None, engine_config=None):
        tmp_dir = Path(input.file_path or "").parent
        rec["file_path"] = input.file_path
        rec["input_type"] = input.type
        rec["engine_config"] = engine_config
        rec["tmp_dir"] = str(tmp_dir)
        rec["exists_during_call"] = Path(input.file_path or "").exists()
        rec["bytes_during_call"] = (Path(input.file_path or "").read_bytes()
                                    if rec["exists_during_call"] else b"")
        if raise_exc:
            raise raise_exc
        return AdapterOutput(
            raw_text="论文正文",
            metadata=RawMetadata(language="en", source_title="A Paper Title"),
            engine="pypdfium2",
        )

    monkeypatch.setattr("app.adapters.document.DocumentAdapter.adapt", _fake_adapt)
    return rec


def test_把_abs_链接改写成_pdf_直链(monkeypatch):
    """⚠️ 核心断言：送出去的是 `arxiv.org/pdf/<id>`，不是用户贴的那条。

    `/abs/2401.12345` 拿回来的是 HTML 摘要页；本适配器的全部价值就在这一次改写。
    """
    dl = _mock_download(monkeypatch)
    doc = _mock_document_adapter(monkeypatch)

    asyncio.run(ArxivAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345v3")))

    assert dl["url"] == "https://arxiv.org/pdf/2401.12345v3"
    assert doc["exists_during_call"], "委托时临时文件必须已落盘"
    assert doc["bytes_during_call"] == b"%PDF-1.4 fake"
    assert doc["input_type"] == InputType.FILE, "必须按文件类型委托，而不是把 URL 再转手"


def test_委托结果原样返回并沿用引擎名(monkeypatch):
    """本适配器不碰正文 —— `raw_text` / `engine` 都是 DocumentAdapter 给的。"""
    _mock_download(monkeypatch)
    _mock_document_adapter(monkeypatch)

    out = asyncio.run(ArxivAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345")))

    assert out.raw_text == "论文正文"
    assert out.engine == "pypdfium2"


def test_文档没有标题时补_arxiv_编号(monkeypatch):
    """PDF 元数据常常没有 Title（arXiv 的 LaTeX 模板不写 `\\title` 到 PDF info）。"""
    _mock_download(monkeypatch)

    async def _no_title(self, input, on_progress=None, engine_config=None):
        return AdapterOutput(raw_text="正文", metadata=RawMetadata(language="en"),
                             engine="pypdfium2")

    monkeypatch.setattr("app.adapters.document.DocumentAdapter.adapt", _no_title)

    out = asyncio.run(ArxivAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345")))

    assert out.metadata.source_title == "arXiv 2401.12345"


def test_文档有标题时不覆盖(monkeypatch):
    """对照：补标题只在**没有**时发生，别把 PDF 自带的标题盖掉。"""
    _mock_download(monkeypatch)
    _mock_document_adapter(monkeypatch)

    out = asyncio.run(ArxivAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345")))

    assert out.metadata.source_title == "A Paper Title"


def test_成功后临时目录被清理(monkeypatch):
    _mock_download(monkeypatch)
    doc = _mock_document_adapter(monkeypatch)

    asyncio.run(ArxivAdapter().adapt(
        PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345")))

    assert doc["exists_during_call"]
    assert not Path(doc["tmp_dir"]).exists(), "临时目录必须删掉"


def test_失败时临时目录同样被清理(monkeypatch):
    """⚠️ 清理在 `finally` 里 —— 只清成功路径的话，每失败一次留一个目录。"""
    _mock_download(monkeypatch)
    doc = _mock_document_adapter(monkeypatch, raise_exc=RuntimeError("引擎炸了"))

    with pytest.raises(RuntimeError, match="引擎炸了"):
        asyncio.run(ArxivAdapter().adapt(
            PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345")))

    assert doc["exists_during_call"]
    assert not Path(doc["tmp_dir"]).exists()


def test_下载失败报可读错误且不留目录(monkeypatch):
    """SSRF 校验失败与「网络不好」分开报（同 `document.py` 的处置）。"""
    def _boom(url, **kw):
        raise ValueError("内网地址")

    monkeypatch.setattr("app.utils.url_security.safe_get", _boom)
    _mock_document_adapter(monkeypatch)

    with pytest.raises(RuntimeError, match="安全校验失败"):
        asyncio.run(ArxivAdapter().adapt(
            PipelineInput(type=InputType.URL, url="https://arxiv.org/abs/2401.12345")))


def test_不可识别的链接显式报错():
    """`accept()` 已拦，但兜底分支不该静默去下一条错的 PDF。

    ⚠️ 断言具体是 `FatalError`：宽到 `Exception` 的话，「临时目录建失败」
    之类的无关异常也会让这条通过 —— 那时它测的就不再是「链接不认」了。
    """
    from app.core.errors import FatalError

    with pytest.raises(FatalError, match="不是可识别的 arXiv 链接"):
        asyncio.run(ArxivAdapter().adapt(
            PipelineInput(type=InputType.URL, url="https://arxiv.org/list/cs.AI")))


# ═══════════════════════════════════════════════════════════
# 3. 注册顺序 —— 这条有真实失败模式，必须钉
# ═══════════════════════════════════════════════════════════

def test_注册顺序_arxiv在document之前():
    """⚠️ 顺序错了**没有任何报错**，只是 `/abs/` 一直走网页路径。

    `/pdf/2401.12345v1.pdf` 这种带 `.pdf` 的形态会被 `DocumentAdapter` 的
    扩展名匹配抢走 —— 那种情况它能跑通（拿到的确实是 PDF），所以顺序错误
    在界面上看不出来；真正丢的是 `/abs/` 与无扩展名的 `/pdf/`。
    """
    from app.adapters import init_adapters
    from app.core.registry import AdapterRegistry

    backup = list(AdapterRegistry._adapters)
    try:
        AdapterRegistry._adapters.clear()
        init_adapters()
        names = [c.name for c in AdapterRegistry._adapters]
        assert names.index("arxiv") < names.index("document"), names

        route = lambda u: type(AdapterRegistry.route(
            PipelineInput(type=InputType.URL, url=u))).__name__

        assert route("https://arxiv.org/abs/2401.12345") == "ArxivAdapter"
        # ⚠️ 这三条是「顺序真的有效」的证据 —— 少了它们，把 arXiv 排到 document
        # 后面也能让第一条断言通过（`/abs/` 本来就只有 arXiv 认）。
        assert route("https://arxiv.org/pdf/2401.12345v1.pdf") == "ArxivAdapter"
        assert route("https://arxiv.org/pdf/2401.12345") == "ArxivAdapter"
        # 负向对照：普通 PDF URL 仍归 document，没被 arXiv 吞掉
        assert route("https://example.com/paper.pdf") == "DocumentAdapter"
    finally:
        AdapterRegistry._adapters[:] = backup


def test_sourceType_映射到pdf():
    """新增 SourceType（`arxiv`）会强制同步前端三处，漏一处预览区白屏 ⇒ 复用 `pdf`。"""
    from app.routers.ingest import _adapter_to_source_type

    assert _adapter_to_source_type("arxiv") == "pdf"
